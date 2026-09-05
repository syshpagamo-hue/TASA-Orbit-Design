from __future__ import annotations

from tasa_v4.optimize.nsga2 import run_nsga2_island


def test_nsga2_smoke(competition_config):
    config = competition_config.model_copy(
        update={
            "robustness": competition_config.robustness.model_copy(
                update={"enabled": False, "screening_scenarios": 0}
            ),
            "optimization": competition_config.optimization.model_copy(
                update={"population_size": 8}
            ),
        }
    )
    result = run_nsga2_island(config.model_dump(mode="json"), 1, 7, 2)
    assert result.population_x.shape[0] == 8
    assert result.population_f.shape[1] == 2
    assert result.population_g.shape[1] == 1
