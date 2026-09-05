from __future__ import annotations

import pytest

from tasa_v4.propagation import simulate_candidate


def test_legacy_candidate_first_entry_regression(competition_config, legacy_candidate):
    result = simulate_candidate(
        legacy_candidate,
        competition_config.scenario,
        competition_config.propagation,
    )
    assert result.success
    assert result.first_entry_time_s == pytest.approx(2582.097701537, abs=2e-6)
    assert result.minimum_distance_km == pytest.approx(1.0, abs=2e-6)
    assert result.minimum_distance_time_s == pytest.approx(2585.26251378827, abs=2e-6)
    assert result.total_dv_km_s == pytest.approx(0.818505565535, abs=1e-12)

