from __future__ import annotations

import numpy as np

from tasa_v4.decision import DecisionCodec


def test_codec_constructs_only_legal_schedules(competition_config):
    codec = DecisionCodec(3, competition_config.scenario)
    vector = np.linspace(0.01, 0.99, codec.n_var)
    vector = codec.lower + vector * (codec.upper - codec.lower)
    plan = codec.decode(vector)
    assert plan.violations(competition_config.scenario) == []
    assert plan.burns[1].time_s - plan.burns[0].time_s >= 100
    assert plan.burns[2].time_s - plan.burns[1].time_s >= 100
    assert all(burn.magnitude_km_s() <= 1.5 for burn in plan.burns)


def test_encode_decode_preserves_candidate(competition_config, legacy_candidate):
    codec = DecisionCodec(1, competition_config.scenario)
    rebuilt = codec.decode(codec.encode(legacy_candidate))
    assert abs(rebuilt.burns[0].time_s - legacy_candidate.burns[0].time_s) < 1e-7
    assert np.allclose(
        rebuilt.burns[0].dv_vnb_km_s,
        legacy_candidate.burns[0].dv_vnb_km_s,
        atol=1e-12,
    )
    assert abs(rebuilt.evaluation_horizon_s - legacy_candidate.evaluation_horizon_s) < 1e-7


def test_one_burn_lower_bound_has_positive_horizon(competition_config):
    codec = DecisionCodec(1, competition_config.scenario)
    plan = codec.decode(codec.lower.copy())

    assert plan.evaluation_horizon_s > 0.0
    assert plan.evaluation_horizon_s == codec.minimum_horizon_s
