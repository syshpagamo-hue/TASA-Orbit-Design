from __future__ import annotations

import math

from scipy.special import expit

from .models import CandidatePlan, ScenarioConfig, ScoreConfig, SimulationMetrics


def competition_score(
    metrics: SimulationMetrics,
    plan: CandidatePlan,
    scenario: ScenarioConfig,
    coefficients: ScoreConfig,
) -> float:
    if metrics.success:
        team_time = float(metrics.first_entry_time_s)
        distance = scenario.intercept_radius_km
    else:
        team_time = scenario.tmax_s()
        distance = max(metrics.distance_at_horizon_km, scenario.intercept_radius_km)
    distance_points = 50.0 * math.exp(
        -(distance - scenario.intercept_radius_km) / 100.0
    )
    time_points = 25.0 * float(expit(-coefficients.kt * (team_time - coefficients.ct_s)))
    dv_points = 25.0 * float(
        expit(-coefficients.kv * (metrics.total_dv_km_s - coefficients.cv_km_s))
    )
    illegal_burns = sum(
        burn.magnitude_km_s() > scenario.max_burn_dv_km_s + 1e-12
        for burn in plan.burns
    )
    return max(0.0, distance_points + time_points + dv_points - 10.0 * illegal_burns)

