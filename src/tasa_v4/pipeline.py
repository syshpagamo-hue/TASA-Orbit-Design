from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from typing import Any

from .decision import DecisionCodec
from .finalization import compact_successful_plan
from .gmat.generator import generate_gmat_bundle
from .gmat.runner import verify_with_gmat
from .io import write_results
from .models import (
    CandidatePlan,
    GmatVerification,
    ProjectConfig,
    RobustMetrics,
    SimulationMetrics,
)
from .optimize.islands import IslandSearchResult, run_island_search
from .optimize.multiple_shooting import MultipleShootingRefiner
from .pareto import (
    EvaluatedCandidate,
    crowding_subset,
    nondominated,
    rank_candidates,
)
from .progress import ProgressRecorder, atomic_write_json, config_fingerprint
from .propagation import simulate_candidate
from .robustness import RobustEvaluator
from .scoring import competition_score
from .screening import CoarseMetrics, coarse_ranking_key, simulate_candidate_coarse
from .seeds import build_physical_seed_bank
from .submission import generate_submission_bundle


def _plan_signature(plan: CandidatePlan) -> str:
    values = [
        round(value, 11)
        for burn in plan.burns
        for value in (burn.time_s, *burn.dv_vnb_km_s)
    ]
    values.append(round(plan.evaluation_horizon_s, 11))
    return json.dumps(values, separators=(",", ":"))


def _deduplicate_plans(plans: list[CandidatePlan]) -> list[CandidatePlan]:
    unique: list[CandidatePlan] = []
    seen: set[str] = set()
    for plan in plans:
        signature = _plan_signature(plan)
        if signature not in seen:
            seen.add(signature)
            unique.append(plan)
    return unique


def _checkpoint_payload(
    config: ProjectConfig,
    plans: list[CandidatePlan],
    *,
    stage: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "format_version": 1,
        "stage": stage,
        "config_fingerprint": config_fingerprint(config.model_dump(mode="json")),
        "plans": [plan.model_dump(mode="json") for plan in plans],
        **(extra or {}),
    }


def _load_plan_checkpoint(
    path: Path,
    config: ProjectConfig,
) -> tuple[list[CandidatePlan], dict[str, Any]] | None:
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("format_version") != 1:
        return None
    expected = config_fingerprint(config.model_dump(mode="json"))
    if payload.get("config_fingerprint") != expected:
        return None
    return (
        [CandidatePlan.model_validate(item) for item in payload.get("plans", [])],
        payload,
    )


def _save_evaluated_checkpoint(
    path: Path,
    config: ProjectConfig,
    stage: str,
    candidates: list[EvaluatedCandidate],
) -> None:
    atomic_write_json(
        path,
        {
            "format_version": 1,
            "stage": stage,
            "config_fingerprint": config_fingerprint(
                config.model_dump(mode="json")
            ),
            "candidates": [
                {
                    "plan": item.plan.model_dump(mode="json"),
                    "nominal": item.nominal.model_dump(mode="json"),
                    "robust": (
                        item.robust.model_dump(mode="json")
                        if item.robust is not None
                        else None
                    ),
                    "objectives": list(item.objectives),
                    "constraint_violation": item.constraint_violation,
                    "score": item.score,
                }
                for item in candidates
            ],
        },
    )


def _load_evaluated_checkpoint(
    path: Path,
    config: ProjectConfig,
) -> list[EvaluatedCandidate] | None:
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    expected = config_fingerprint(config.model_dump(mode="json"))
    if (
        payload.get("format_version") != 1
        or payload.get("config_fingerprint") != expected
    ):
        return None
    return [
        EvaluatedCandidate(
            plan=CandidatePlan.model_validate(item["plan"]),
            nominal=SimulationMetrics.model_validate(item["nominal"]),
            robust=(
                RobustMetrics.model_validate(item["robust"])
                if item.get("robust") is not None
                else None
            ),
            objectives=tuple(float(value) for value in item["objectives"]),
            constraint_violation=float(item["constraint_violation"]),
            score=(float(item["score"]) if item.get("score") is not None else None),
        )
        for item in payload.get("candidates", [])
    ]


def evaluate_plan(
    config: ProjectConfig,
    plan: CandidatePlan,
    *,
    robust_scenarios: int = 0,
    robust_evaluator: RobustEvaluator | None = None,
    robust_metrics: RobustMetrics | None = None,
) -> EvaluatedCandidate:
    evaluated_plan = plan
    nominal = simulate_candidate(plan, config.scenario, config.propagation)
    # The decision horizon is an intended encounter time. If that attempt
    # misses, the rulebook still evaluates first entry and distance through
    # Tmax=4TA. Continue the final nominal coast so a short horizon cannot game
    # the failure score or hide a later natural intercept.
    if (
        not nominal.success
        and plan.evaluation_horizon_s < config.scenario.tmax_s() - 1e-9
    ):
        evaluated_plan = plan.model_copy(
            update={"evaluation_horizon_s": config.scenario.tmax_s()}
        )
        nominal = simulate_candidate(
            evaluated_plan, config.scenario, config.propagation
        )
    # A successful candidate only needs a short verification coast after the
    # exact first entry. NSGA-II's horizon gene can otherwise leave hours of
    # irrelevant propagation in the candidate and its GMAT Reports file.
    evaluated_plan, nominal = compact_successful_plan(
        config, evaluated_plan, nominal
    )
    robust = robust_metrics
    if robust is None and config.robustness.enabled and robust_scenarios > 0:
        evaluator = robust_evaluator or RobustEvaluator(
            config, len(evaluated_plan.burns), robust_scenarios
        )
        robust, _ = evaluator.evaluate(evaluated_plan)

    entry_time = (
        nominal.first_entry_time_s
        if nominal.first_entry_time_s is not None
        else config.scenario.tmax_s()
    )
    score = (
        competition_score(
            nominal, evaluated_plan, config.scenario, config.score
        )
        if config.score is not None
        else None
    )
    if score is not None and config.optimization.official_score_as_objective:
        objectives: tuple[float, ...] = (
            -float(score) / 100.0,
            float(entry_time / config.scenario.tmax_s()),
                nominal.total_dv_km_s
                / (
                max(1, len(evaluated_plan.burns))
                * config.scenario.max_burn_dv_km_s
            ),
        )
    elif robust is not None and config.optimization.robust_as_objective:
        objectives = (
            float(entry_time),
            nominal.total_dv_km_s,
            robust.p95_minimum_distance_km,
        )
    else:
        objectives = (float(entry_time), nominal.total_dv_km_s)

    violation = max(
        0.0,
        nominal.minimum_distance_km - config.scenario.intercept_radius_km,
    )
    if robust is not None:
        violation += max(
            0.0,
            config.robustness.required_success_rate - robust.success_rate,
        )
    return EvaluatedCandidate(
        plan=evaluated_plan,
        nominal=nominal,
        robust=robust,
        objectives=objectives,
        constraint_violation=violation,
        score=score,
    )


def evaluate_search_result(
    search: IslandSearchResult,
    *,
    progress: ProgressRecorder | None = None,
) -> list[EvaluatedCandidate]:
    config = search.config
    plans: list[CandidatePlan] = []
    seen: set[tuple[int, bytes]] = set()
    for n_burns, vector in search.front_vectors():
        key = (n_burns, vector.round(11).tobytes())
        if key in seen:
            continue
        seen.add(key)
        plans.append(DecisionCodec(n_burns, config.scenario).decode(vector))

    evaluated: list[EvaluatedCandidate] = []
    for index, plan in enumerate(plans, start=1):
        evaluated.append(evaluate_plan(config, plan))
        if progress is not None:
            progress.progress("exact_front_evaluation", index, len(plans))
    return crowding_subset(
        nondominated(evaluated), config.optimization.max_front_size
    )


def _prepare_seed_plans(
    config: ProjectConfig,
    output: Path,
    supplied: list[CandidatePlan],
    progress: ProgressRecorder,
) -> list[CandidatePlan]:
    checkpoint_directory = output / config.checkpoint.directory_name
    bank_path = checkpoint_directory / "physical_seed_bank.json"
    screened_path = checkpoint_directory / "physical_seed_screened.json"
    screening_csv = output / "physical_seed_screening.csv"

    saved_screened = (
        _load_plan_checkpoint(screened_path, config)
        if config.checkpoint.enabled and config.checkpoint.resume
        else None
    )
    if saved_screened is not None:
        plans, _payload = saved_screened
        progress.emit(
            "checkpoint_loaded",
            stage="physical_seed_screen",
            message=f"已從物理種子 checkpoint 繼續：保留 {len(plans)} 組",
        )
        return _deduplicate_plans([*supplied, *plans])

    saved_bank = (
        _load_plan_checkpoint(bank_path, config)
        if config.checkpoint.enabled and config.checkpoint.resume
        else None
    )
    if saved_bank is not None:
        physical, _payload = saved_bank
        progress.emit(
            "checkpoint_loaded",
            stage="physical_seed_generation",
            message=f"已載入 {len(physical)} 組物理種子",
        )
    else:
        with progress.stage("physical_seed_generation"):
            physical = build_physical_seed_bank(config)
        if config.checkpoint.enabled:
            atomic_write_json(
                bank_path,
                _checkpoint_payload(
                    config,
                    physical,
                    stage="physical_seed_generation",
                ),
            )

    if not config.screening.enabled or not physical:
        return _deduplicate_plans([*supplied, *physical])

    evaluated: list[tuple[CandidatePlan, CoarseMetrics]] = []
    with progress.stage("physical_seed_coarse_screen"):
        for index, plan in enumerate(physical, start=1):
            try:
                metrics = simulate_candidate_coarse(plan, config)
                evaluated.append((plan, metrics))
            except (
                ArithmeticError,
                FloatingPointError,
                OverflowError,
                RuntimeError,
                ValueError,
            ) as error:
                progress.emit(
                    "physical_seed_rejected",
                    candidate=plan.name,
                    reason=type(error).__name__,
                    message=f"物理種子 {plan.name} 粗篩失敗",
                )
            progress.progress("physical_seed_coarse_screen", index, len(physical))

    keep_count = min(
        len(evaluated),
        max(
            config.screening.physical_seed_minimum_keep,
            int(
                math.ceil(
                    config.screening.physical_seed_keep_fraction
                    * len(evaluated)
                )
            ),
        ),
    )
    ranked = sorted(evaluated, key=coarse_ranking_key, reverse=True)
    selected: list[CandidatePlan] = []
    selected_signatures: set[str] = set()

    def add(plan: CandidatePlan) -> None:
        signature = _plan_signature(plan)
        if signature not in selected_signatures and len(selected) < keep_count:
            selected_signatures.add(signature)
            selected.append(plan)

    best_per_strategy: dict[tuple[str, int], CandidatePlan] = {}
    for plan, _metrics in ranked:
        best_per_strategy.setdefault((plan.source, len(plan.burns)), plan)
    for plan in best_per_strategy.values():
        add(plan)
    for plan, _metrics in ranked:
        add(plan)

    screening_csv.parent.mkdir(parents=True, exist_ok=True)
    with screening_csv.open("w", encoding="utf-8-sig", newline="") as stream:
        fields = [
            "candidate",
            "source",
            "burn_count",
            "selected",
            "success_coarse",
            "approximate_score",
            "sampled_minimum_distance_km",
            "total_dv_km_s",
            "propagated_to_s",
            "stopped_early",
        ]
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for plan, metrics in ranked:
            writer.writerow(
                {
                    "candidate": plan.name,
                    "source": plan.source,
                    "burn_count": len(plan.burns),
                    "selected": _plan_signature(plan) in selected_signatures,
                    "success_coarse": metrics.success,
                    "approximate_score": metrics.approximate_score,
                    "sampled_minimum_distance_km": metrics.sampled_minimum_distance_km,
                    "total_dv_km_s": metrics.total_dv_km_s,
                    "propagated_to_s": metrics.propagated_to_s,
                    "stopped_early": metrics.stopped_early,
                }
            )

    if config.checkpoint.enabled:
        atomic_write_json(
            screened_path,
            _checkpoint_payload(
                config,
                selected,
                stage="physical_seed_coarse_screen",
                extra={
                    "generated_count": len(physical),
                    "evaluated_count": len(evaluated),
                    "selected_count": len(selected),
                },
            ),
        )
    return _deduplicate_plans([*supplied, *selected])


def _refine_front(
    config: ProjectConfig,
    output: Path,
    front: list[EvaluatedCandidate],
    progress: ProgressRecorder,
) -> list[CandidatePlan]:
    checkpoint_path = (
        output / config.checkpoint.directory_name / "refinement_checkpoint.json"
    )
    saved: dict[str, dict[str, Any] | None] = {}
    if config.checkpoint.enabled and config.checkpoint.resume and checkpoint_path.exists():
        payload = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        expected = config_fingerprint(config.model_dump(mode="json"))
        if payload.get("config_fingerprint") == expected:
            saved = dict(payload.get("results", {}))
            progress.emit(
                "checkpoint_loaded",
                stage="multiple_shooting",
                message=f"已載入 {len(saved)} 筆精修 checkpoint",
            )

    seeds = crowding_subset(front, config.multiple_shooting.refine_front_candidates)
    refiner = MultipleShootingRefiner(config)
    refined: list[CandidatePlan] = []
    for index, item in enumerate(seeds):
        signature = _plan_signature(item.plan)
        if signature in saved:
            stored = saved[signature]
            if stored is not None:
                refined.append(CandidatePlan.model_validate(stored))
        else:
            weight = (index + 1) / (len(seeds) + 1) if seeds else 0.5
            result = refiner.refine(item.plan, time_weight=weight)
            stored_plan = (
                result.plan.model_dump(mode="json") if result.converged else None
            )
            saved[signature] = stored_plan
            if result.converged:
                refined.append(result.plan)
            if config.checkpoint.enabled:
                atomic_write_json(
                    checkpoint_path,
                    {
                        "format_version": 1,
                        "config_fingerprint": config_fingerprint(
                            config.model_dump(mode="json")
                        ),
                        "results": saved,
                    },
                )
        progress.progress("multiple_shooting", index + 1, len(seeds))
    return refined


def _certify_finalists(
    config: ProjectConfig,
    output: Path,
    front: list[EvaluatedCandidate],
    progress: ProgressRecorder,
) -> list[EvaluatedCandidate]:
    ranked = rank_candidates(front, official_score_enabled=config.score is not None)
    finalists = ranked[: config.optimization.final_robustness_candidates]
    if not config.robustness.enabled or config.robustness.certification_scenarios <= 0:
        return finalists

    checkpoint_path = (
        output / config.checkpoint.directory_name / "robustness_checkpoint.json"
    )
    saved: dict[str, dict[str, Any]] = {}
    if config.checkpoint.enabled and config.checkpoint.resume and checkpoint_path.exists():
        payload = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        expected = config_fingerprint(config.model_dump(mode="json"))
        if payload.get("config_fingerprint") == expected:
            saved = dict(payload.get("results", {}))
            progress.emit(
                "checkpoint_loaded",
                stage="robustness_finalists",
                message=f"已載入 {len(saved)} 筆 robustness checkpoint",
            )

    evaluators = {
        burn_count: RobustEvaluator(
            config,
            burn_count,
            config.robustness.certification_scenarios,
        )
        for burn_count in {len(item.plan.burns) for item in finalists}
    }
    certified: list[EvaluatedCandidate] = []
    for index, item in enumerate(finalists, start=1):
        signature = _plan_signature(item.plan)
        if signature in saved:
            robust = RobustMetrics.model_validate(saved[signature])
        else:
            robust, _ = evaluators[len(item.plan.burns)].evaluate(item.plan)
            saved[signature] = robust.model_dump(mode="json")
            if config.checkpoint.enabled:
                atomic_write_json(
                    checkpoint_path,
                    {
                        "format_version": 1,
                        "config_fingerprint": config_fingerprint(
                            config.model_dump(mode="json")
                        ),
                        "scenario_count": config.robustness.certification_scenarios,
                        "results": saved,
                    },
                )
        certified.append(evaluate_plan(config, item.plan, robust_metrics=robust))
        progress.progress("robustness_finalists", index, len(finalists))
    return rank_candidates(certified, official_score_enabled=config.score is not None)


def run_competition_pipeline(
    config: ProjectConfig,
    output_directory: str | Path,
    *,
    refine: bool = True,
    run_gmat_verification: bool = False,
    seed_plans: list[CandidatePlan] | None = None,
) -> tuple[list[EvaluatedCandidate], dict[str, GmatVerification]]:
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    progress = ProgressRecorder(output)

    with progress.stage("physical_seed_funnel"):
        active_seeds = _prepare_seed_plans(
            config, output, seed_plans or [], progress
        )

    with progress.stage("nsga2_island_search"):
        search = run_island_search(
            config,
            seed_plans=active_seeds,
            output_directory=output,
            progress=progress,
        )

    exact_checkpoint_path = (
        output / config.checkpoint.directory_name / "exact_front_checkpoint.json"
    )
    front = (
        _load_evaluated_checkpoint(exact_checkpoint_path, config)
        if config.checkpoint.enabled and config.checkpoint.resume
        else None
    )
    if front is not None:
        progress.emit(
            "checkpoint_loaded",
            stage="exact_first_entry_and_official_score",
            message=f"已載入 {len(front)} 組精確前緣候選",
        )
        # Older checkpoints may still contain the original NSGA-II horizon.
        # Re-evaluate the small exact front so resumed runs also export compact
        # successful candidates.
        front = [evaluate_plan(config, item.plan) for item in front]
    else:
        with progress.stage("exact_first_entry_and_official_score"):
            front = evaluate_search_result(search, progress=progress)
        if config.checkpoint.enabled:
            _save_evaluated_checkpoint(
                exact_checkpoint_path,
                config,
                "exact_first_entry_and_official_score",
                front,
            )

    refined_plans: list[CandidatePlan] = []
    if refine and config.multiple_shooting.enabled:
        with progress.stage("multiple_shooting"):
            refined_plans = _refine_front(config, output, front, progress)
        for plan in refined_plans:
            front.append(evaluate_plan(config, plan))
        front = crowding_subset(
            nondominated(front), config.optimization.max_front_size
        )

    with progress.stage("robustness_finalists"):
        front = _certify_finalists(config, output, front, progress)
    front = rank_candidates(front, official_score_enabled=config.score is not None)

    verifications: dict[str, GmatVerification] = {}
    with progress.stage("gmat_and_submission_outputs"):
        for index, item in enumerate(front, 1):
            candidate_id = f"P{index:03d}"
            run_directory = output / "gmat" / candidate_id
            if not item.feasible:
                verifications[candidate_id] = GmatVerification(
                    status="not_run",
                    diagnostics=[
                        "candidate is infeasible; GMAT script was not generated"
                    ],
                )
                continue

            if config.score is not None:
                generate_submission_bundle(
                    config,
                    item.plan,
                    output / "submission" / candidate_id,
                )

            if run_gmat_verification:
                verifications[candidate_id] = verify_with_gmat(
                    config, item.plan, run_directory
                )
            else:
                generate_gmat_bundle(config, item.plan, run_directory)
                verifications[candidate_id] = GmatVerification(
                    status="not_run",
                    diagnostics=[
                        "GMAT script generated; execution was not requested"
                    ],
                    run_directory=run_directory,
                )
            progress.progress("gmat_and_submission_outputs", index, len(front))

    write_results(output, config, front, verifications)
    atomic_write_json(
        output / "run_summary.json",
        {
            "format_version": 1,
            "timings_s": progress.timings_s,
            "counts": {
                "physical_and_manual_seeds_injected": len(active_seeds),
                "nsga2_front_vectors": len(search.front_vectors()),
                "refined_plans": len(refined_plans),
                "finalists": len(front),
            },
            "robustness_policy": {
                "evaluated_inside_nsga2": False,
                "finalist_limit": config.optimization.final_robustness_candidates,
                "certification_scenarios": (
                    config.robustness.certification_scenarios
                    if config.robustness.enabled
                    else 0
                ),
            },
            "coarse_screening": config.screening.model_dump(mode="json"),
            "checkpoint": config.checkpoint.model_dump(mode="json"),
            "official_score_enabled": config.score is not None,
            "best_candidate": (
                {
                    "score": front[0].score,
                    "first_entry_time_s": front[0].nominal.first_entry_time_s,
                    "minimum_distance_km": front[0].nominal.minimum_distance_km,
                    "total_dv_km_s": front[0].nominal.total_dv_km_s,
                }
                if front
                else None
            ),
        },
    )
    progress.emit(
        "pipeline_complete",
        finalists=len(front),
        message=f"最佳化完成：輸出 {len(front)} 組決賽候選",
    )
    return front, verifications
