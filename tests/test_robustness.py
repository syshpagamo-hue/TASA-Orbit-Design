from __future__ import annotations

from tasa_v4.robustness import RobustEvaluator, make_perturbations


def test_sobol_perturbations_are_reproducible(competition_config):
    left = make_perturbations(competition_config, 1, 4)
    right = make_perturbations(competition_config, 1, 4)
    assert len(left) == len(right) == 4
    for a, b in zip(left, right):
        assert (a.chaser_state_error == b.chaser_state_error).all()
        assert (a.burn_component_errors == b.burn_component_errors).all()


def test_robust_evaluator_reports_requested_count(competition_config, legacy_candidate):
    metrics, results = RobustEvaluator(competition_config, 1, 3).evaluate(
        legacy_candidate
    )
    assert metrics.scenario_count == len(results) == 3
    assert 0 <= metrics.success_rate <= 1


def test_failed_propagation_uses_finite_penalty(monkeypatch, competition_config, legacy_candidate):
    def fail(*args, **kwargs):
        raise FloatingPointError("synthetic numerical failure")

    monkeypatch.setattr("tasa_v4.robustness.simulate_candidate", fail)
    metrics, results = RobustEvaluator(competition_config, 1, 3).evaluate(legacy_candidate)
    assert metrics.success_rate == 0.0
    assert metrics.p95_minimum_distance_km == 1_000_000.0
    assert all(item.minimum_distance_km == 1_000_000.0 for item in results)
