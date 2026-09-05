from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import __version__
from .competition_day import parse_official_report, validate_generated_report
from .config import load_candidate, load_project_config, save_model_yaml
from .finalization import compact_successful_plan
from .gmat.generator import generate_gmat_bundle
from .gmat.runner import verify_with_gmat
from .propagation import simulate_candidate
from .robustness import RobustEvaluator
from .submission import generate_submission_bundle


def _json(value: object) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, indent=2)


def _quick_config(config):
    return config.model_copy(
        update={
            "screening": config.screening.model_copy(
                update={"physical_seed_minimum_keep": 6}
            ),
            "physical_seeds": config.physical_seeds.model_copy(
                update={
                    "lambert_tof_fractions": [0.25, 0.5, 1.0, 2.0],
                    "lambert_revolutions": [0, 1],
                    "lambert_max_solutions": 12,
                    "phasing_closure_periods": [0.5, 1.0],
                }
            ),
            "optimization": config.optimization.model_copy(
                update={
                    "population_size": 16,
                    "generations_per_epoch": 2,
                    "island_epochs": 1,
                    "seeds": [11],
                    "workers": 1,
                    "max_front_size": 20,
                    "final_robustness_candidates": 6,
                }
            ),
            "robustness": config.robustness.model_copy(
                update={
                    "screening_scenarios": 0,
                    "certification_scenarios": 0,
                }
            ),
            "multiple_shooting": config.multiple_shooting.model_copy(
                update={"enabled": False, "refine_front_candidates": 0}
            ),
        }
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tasa-v4")
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate-config")
    validate.add_argument("config")

    simulate = subparsers.add_parser("simulate")
    simulate.add_argument("config")
    simulate.add_argument("candidate")
    simulate.add_argument("--robust", action="store_true")
    simulate.add_argument("--scenarios", type=int)

    refine = subparsers.add_parser("refine")
    refine.add_argument("config")
    refine.add_argument("candidate")
    refine.add_argument("--output", required=True)
    refine.add_argument("--time-weight", type=float)

    generate = subparsers.add_parser("gmat-generate")
    generate.add_argument("config")
    generate.add_argument("candidate")
    generate.add_argument("--output", required=True)

    verify = subparsers.add_parser("gmat-verify")
    verify.add_argument("config")
    verify.add_argument("candidate")
    verify.add_argument("--output", required=True)
    verify.add_argument("--executable")

    report = subparsers.add_parser("report-validate")
    report.add_argument("report")
    report.add_argument("--radius-km", type=float, default=5.0)
    report.add_argument("--config")
    report.add_argument("--candidate")

    submit = subparsers.add_parser("submission-generate")
    submit.add_argument("config")
    submit.add_argument("candidate")
    submit.add_argument("--output", required=True)
    submit.add_argument("--report")

    gui = subparsers.add_parser("gui")
    gui.add_argument("config", nargs="?", default="configs/current_competition.yaml")

    web_ui = subparsers.add_parser("web-ui")
    web_ui.add_argument(
        "config", nargs="?", default="configs/current_competition.yaml"
    )

    optimize = subparsers.add_parser("optimize")
    optimize.add_argument("config")
    optimize.add_argument("--output", required=True)
    optimize.add_argument("--quick", action="store_true")
    optimize.add_argument("--no-refine", action="store_true")
    optimize.add_argument("--run-gmat", action="store_true")
    optimize.add_argument(
        "--no-resume",
        action="store_true",
        help="ignore compatible checkpoints and start a fresh search",
    )
    optimize.add_argument(
        "--seed-candidate",
        action="append",
        default=[],
        help="candidate YAML to inject into every matching-burn island (repeatable)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "gui":
        try:
            from .competition_gui import launch_gui
        except ModuleNotFoundError as error:
            if error.name not in {"tkinter", "_tkinter"}:
                raise
            from .competition_web import launch_web_ui

            launch_web_ui(args.config)
            return 0
        launch_gui(args.config)
        return 0

    if args.command == "web-ui":
        from .competition_web import launch_web_ui

        launch_web_ui(args.config)
        return 0

    if args.command == "report-validate":
        report = parse_official_report(args.report)
        if bool(args.config) != bool(args.candidate):
            raise SystemExit("--config 與 --candidate 必須一起提供")
        if args.config:
            report_config = load_project_config(args.config)
            report_plan, _ = compact_successful_plan(
                report_config, load_candidate(args.candidate)
            )
            result = validate_generated_report(
                report, report_config, report_plan
            )
        else:
            result = report.summary(args.radius_km)
        print(_json(result))
        return 0

    config = load_project_config(args.config)
    if args.command == "validate-config":
        print(
            _json(
                {
                    "valid": True,
                    "target_period_s": config.scenario.target_period_s(),
                    "tmax_s": config.scenario.tmax_s(),
                    "burn_counts": config.optimization.burn_counts,
                    "physical_seeds": config.physical_seeds.enabled,
                    "coarse_screening": config.screening.enabled,
                    "resume": config.checkpoint.resume,
                    "robustness_inside_nsga2": False,
                }
            )
        )
        return 0

    if args.command == "simulate":
        plan = load_candidate(args.candidate)
        metrics = simulate_candidate(plan, config.scenario, config.propagation)
        payload: dict[str, object] = {"nominal": metrics.model_dump(mode="json")}
        if args.robust:
            count = args.scenarios or config.robustness.certification_scenarios
            robust, _ = RobustEvaluator(config, len(plan.burns), count).evaluate(plan)
            payload["robust"] = robust.model_dump(mode="json")
        print(_json(payload))
        return 0

    if args.command == "refine":
        from .optimize.multiple_shooting import MultipleShootingRefiner

        plan = load_candidate(args.candidate)
        result = MultipleShootingRefiner(config).refine(
            plan, time_weight=args.time_weight
        )
        save_model_yaml(result.plan, args.output)
        print(
            _json(
                {
                    "output": str(Path(args.output).resolve()),
                    "converged": result.converged,
                    "status": result.solver_status,
                    "metrics": result.metrics.model_dump(mode="json"),
                    "automatic_differentiation": {
                        "variables": result.variable_count,
                        "constraints": result.constraint_count,
                        "jacobian_nonzeros": result.jacobian_nonzeros,
                    },
                }
            )
        )
        return 0 if result.converged else 2

    if args.command == "gmat-generate":
        plan, _ = compact_successful_plan(
            config, load_candidate(args.candidate)
        )
        bundle = generate_gmat_bundle(
            config, plan, args.output
        )
        print(_json({key: str(value) for key, value in bundle.items()}))
        return 0

    if args.command == "gmat-verify":
        plan, _ = compact_successful_plan(
            config, load_candidate(args.candidate)
        )
        verification = verify_with_gmat(
            config,
            plan,
            args.output,
            executable=args.executable,
        )
        print(_json(verification))
        return 0 if verification.status == "passed" else 2

    if args.command == "submission-generate":
        report = parse_official_report(args.report) if args.report else None
        plan, _ = compact_successful_plan(
            config, load_candidate(args.candidate)
        )
        outputs = generate_submission_bundle(
            config,
            plan,
            args.output,
            official_report=report,
        )
        print(_json({key: str(value) for key, value in outputs.items()}))
        return 0

    if args.command == "optimize":
        from .pipeline import run_competition_pipeline

        active = _quick_config(config) if args.quick else config
        if args.no_resume:
            active = active.model_copy(
                update={
                    "checkpoint": active.checkpoint.model_copy(
                        update={"resume": False}
                    )
                }
            )
        seed_plans = [load_candidate(path) for path in args.seed_candidate]
        front, verifications = run_competition_pipeline(
            active,
            args.output,
            refine=not args.no_refine,
            run_gmat_verification=args.run_gmat,
            seed_plans=seed_plans,
        )
        print(
            _json(
                {
                    "pareto_candidates": len(front),
                    "output": str(Path(args.output).resolve()),
                    "best_score": front[0].score if front else None,
                    "best_first_entry_time_s": (
                        front[0].nominal.first_entry_time_s if front else None
                    ),
                    "gmat_passed": sum(
                        value.status == "passed" for value in verifications.values()
                    ),
                }
            )
        )
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
