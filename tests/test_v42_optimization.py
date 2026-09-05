from __future__ import annotations

import math

import pytest

from tasa_v4.models import Burn, CandidatePlan, ScoreConfig, SimulationMetrics
from tasa_v4.finalization import SUCCESS_HORIZON_BUFFER_S
from tasa_v4.optimize.islands import run_island_search
from tasa_v4.optimize.nsga2 import run_nsga2_island
from tasa_v4.pipeline import evaluate_plan
from tasa_v4.propagation import simulate_candidate
from tasa_v4.scoring import competition_score
from tasa_v4.screening import simulate_candidate_coarse
from tasa_v4.seeds import build_physical_seed_bank


def test_physical_seed_bank_contains_multi_rev_and_strategy_families(
    competition_config,
):
    plans = build_physical_seed_bank(competition_config)
    sources = {plan.source for plan in plans}
    assert "physical:lambert_shooting" in sources
    assert "physical:phasing" in sources
    assert "physical:hohmann" in sources
    assert "physical:high_apogee_plane_change" in sources
    assert any(
        plan.metadata.get("requested_revolutions", 0) >= 1
        and plan.metadata.get("estimated_revolutions")
        == plan.metadata.get("requested_revolutions")
        for plan in plans
    )
    assert all(plan.violations(competition_config.scenario) == [] for plan in plans)


def test_coarse_screen_is_only_a_funnel_and_exact_solver_keeps_authority(
    competition_config, legacy_candidate
):
    coarse = simulate_candidate_coarse(legacy_candidate, competition_config)
    exact = simulate_candidate(
        legacy_candidate,
        competition_config.scenario,
        competition_config.propagation,
    )
    assert coarse.success
    assert exact.success
    assert abs(coarse.first_entry_time_s - exact.first_entry_time_s) < 60.0
    assert exact.first_entry_time_s == pytest.approx(2582.097701537, abs=2e-6)


def test_official_score_uses_horizon_distance_on_failure(
    competition_config, legacy_candidate
):
    coefficients = ScoreConfig(kt=0.01, ct_s=2500, kv=10, cv_km_s=1.0)
    failed = SimulationMetrics(
        success=False,
        first_entry_time_s=None,
        minimum_distance_km=1.0,
        minimum_distance_time_s=100.0,
        distance_at_horizon_km=105.0,
        total_dv_km_s=0.5,
        executed_burns=1,
        horizon_s=competition_config.scenario.tmax_s(),
    )
    value = competition_score(
        failed,
        legacy_candidate,
        competition_config.scenario,
        coefficients,
    )
    expected_distance = 50.0 * math.exp(-(105.0 - 5.0) / 100.0)
    expected_time = 25.0 / (
        1.0
        + math.exp(
            coefficients.kt
            * (competition_config.scenario.tmax_s() - coefficients.ct_s)
        )
    )
    expected_dv = 25.0 / (
        1.0 + math.exp(coefficients.kv * (0.5 - coefficients.cv_km_s))
    )
    assert value == pytest.approx(expected_distance + expected_time + expected_dv)


def test_final_evaluation_continues_failed_short_horizon_to_tmax(
    competition_config,
):
    config = competition_config.model_copy(
        update={
            "score": ScoreConfig(kt=0.01, ct_s=2500, kv=10, cv_km_s=1.0),
            "robustness": competition_config.robustness.model_copy(
                update={"enabled": False}
            ),
        }
    )
    short = CandidatePlan(
        name="short-failure",
        burns=[Burn(time_s=0.0, dv_vnb_km_s=(0.0, 0.0, 0.0))],
        evaluation_horizon_s=100.0,
    )
    evaluated = evaluate_plan(config, short)
    assert evaluated.plan.evaluation_horizon_s == pytest.approx(
        config.scenario.tmax_s()
    )
    assert evaluated.nominal.horizon_s == pytest.approx(config.scenario.tmax_s())


def test_successful_candidate_horizon_is_compacted_and_late_burns_removed(
    competition_config,
    legacy_candidate,
):
    late_burn = Burn(time_s=3500.0, dv_vnb_km_s=(0.01, 0.0, 0.0))
    long_plan = legacy_candidate.model_copy(
        update={
            "burns": [*legacy_candidate.burns, late_burn],
            "evaluation_horizon_s": 4000.0,
        }
    )

    evaluated = evaluate_plan(competition_config, long_plan)

    assert evaluated.nominal.success
    assert evaluated.nominal.first_entry_time_s is not None
    assert evaluated.plan.evaluation_horizon_s == pytest.approx(
        evaluated.nominal.first_entry_time_s
        + SUCCESS_HORIZON_BUFFER_S,
        abs=2e-6,
    )
    assert evaluated.nominal.horizon_s == pytest.approx(
        evaluated.plan.evaluation_horizon_s
    )
    assert len(evaluated.plan.burns) == 1
    assert all(
        burn.time_s <= evaluated.nominal.first_entry_time_s + 1e-9
        for burn in evaluated.plan.burns
    )
    assert evaluated.plan.metadata["original_evaluation_horizon_s"] == 4000.0


def test_nsga_never_runs_robustness_for_every_candidate(
    monkeypatch, competition_config
):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("robustness must not run inside NSGA-II")

    monkeypatch.setattr("tasa_v4.robustness.RobustEvaluator.evaluate", forbidden)
    config = competition_config.model_copy(
        update={
            "robustness": competition_config.robustness.model_copy(
                update={"enabled": True, "screening_scenarios": 8}
            ),
            "optimization": competition_config.optimization.model_copy(
                update={"population_size": 8}
            ),
        }
    )
    result = run_nsga2_island(config.model_dump(mode="json"), 1, 7, 1)
    assert result.population_x.shape[0] == 8
    assert result.population_g.shape[1] == 1


def test_official_score_is_an_nsga_objective(competition_config):
    config = competition_config.model_copy(
        update={
            "score": ScoreConfig(kt=0.01, ct_s=2500, kv=10, cv_km_s=1.0),
            "optimization": competition_config.optimization.model_copy(
                update={"population_size": 8}
            ),
        }
    )
    result = run_nsga2_island(config.model_dump(mode="json"), 1, 7, 1)
    assert result.population_f.shape[1] == 3


def test_completed_island_checkpoint_resumes_without_recomputing(
    tmp_path, competition_config
):
    config = competition_config.model_copy(
        update={
            "physical_seeds": competition_config.physical_seeds.model_copy(
                update={"enabled": False}
            ),
            "robustness": competition_config.robustness.model_copy(
                update={"enabled": False}
            ),
            "optimization": competition_config.optimization.model_copy(
                update={
                    "burn_counts": [1],
                    "population_size": 8,
                    "generations_per_epoch": 1,
                    "island_epochs": 2,
                    "seeds": [7],
                    "workers": 1,
                }
            ),
        }
    )
    first = run_island_search(config, output_directory=tmp_path)
    second = run_island_search(config, output_directory=tmp_path)
    assert len(first.island_results) == len(second.island_results) == 2
    assert (tmp_path / "checkpoints" / "nsga2" / "optimizer_checkpoint.json").exists()
    for left, right in zip(first.island_results, second.island_results):
        assert left.seed == right.seed
        assert left.n_burns == right.n_burns
        assert (left.population_x == right.population_x).all()
