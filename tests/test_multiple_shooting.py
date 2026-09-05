from __future__ import annotations

from tasa_v4.optimize.multiple_shooting import MultipleShootingRefiner


def test_multiple_shooting_builds_sparse_ad_nlp_and_passes_exact_postcheck(
    competition_config, legacy_candidate
):
    config = competition_config.model_copy(
        update={
            "robustness": competition_config.robustness.model_copy(
                update={"enabled": False}
            ),
            "multiple_shooting": competition_config.multiple_shooting.model_copy(
                update={
                    "robust_scenarios": 0,
                    "max_iterations": 500,
                    "tolerance": 1e-8,
                }
            ),
        }
    )
    result = MultipleShootingRefiner(config).refine(legacy_candidate, time_weight=0.5)
    assert result.converged, result.solver_status
    assert result.metrics.success
    assert result.jacobian_nonzeros > 0
    assert result.variable_count > 0
    assert result.constraint_count > 0

