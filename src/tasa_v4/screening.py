from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np
from scipy.optimize import brentq, minimize_scalar

from .astrodynamics import apply_vnb_burn, propagate_universal, spacecraft_state
from .models import CandidatePlan, ProjectConfig, SimulationMetrics
from .scoring import competition_score


@dataclass(frozen=True)
class CoarseMetrics:
    success: bool
    first_entry_time_s: float | None
    sampled_minimum_distance_km: float
    distance_at_stop_km: float
    total_dv_km_s: float
    executed_burns: int
    propagated_to_s: float
    stopped_early: bool
    approximate_score: float | None


def simulate_candidate_coarse(
    plan: CandidatePlan,
    config: ProjectConfig,
) -> CoarseMetrics:
    """Fast nominal screen with coarse samples and configurable early exit.

    This result is never used as the final intercept proof. Survivors are always
    rerun by ``simulate_candidate``, whose root solver determines first entry.
    """

    scenario = config.scenario
    settings = config.screening
    violations = plan.violations(scenario)
    if violations:
        raise ValueError("; ".join(violations))

    mu = scenario.mu_km3_s2
    chaser = spacecraft_state(scenario.chaser, mu)
    target = spacecraft_state(scenario.target, mu)
    burns = list(plan.burns)
    evaluation_end_s = scenario.tmax_s()
    burn_index = 0
    current_time = 0.0
    executed_burns = 0

    while burn_index < len(burns) and burns[burn_index].time_s <= 1e-12:
        chaser = apply_vnb_burn(chaser, burns[burn_index].dv_vnb_km_s)
        burn_index += 1
        executed_burns += 1

    distance = float(np.linalg.norm(chaser[:3] - target[:3]))
    previous_distance = distance
    sampled_minimum = distance
    first_entry = 0.0 if distance <= scenario.intercept_radius_km else None
    recent: deque[float] = deque(
        [distance], maxlen=max(3, settings.increasing_window)
    )
    stopped_early = False

    while current_time < evaluation_end_s - 1e-12 and first_entry is None:
        next_burn_time = (
            burns[burn_index].time_s
            if burn_index < len(burns)
            else evaluation_end_s
        )
        next_time = min(
            current_time + settings.step_s,
            next_burn_time,
            evaluation_end_s,
        )
        duration = next_time - current_time
        if duration > 0.0:
            chaser_start = chaser.copy()
            target_start = target.copy()
            chaser = propagate_universal(
                chaser,
                duration,
                mu,
                tolerance=config.propagation.universal_tolerance,
                max_iterations=config.propagation.universal_max_iterations,
            )
            target = propagate_universal(
                target,
                duration,
                mu,
                tolerance=config.propagation.universal_tolerance,
                max_iterations=config.propagation.universal_max_iterations,
            )
            current_time = next_time
            previous_distance, distance = distance, float(
                np.linalg.norm(chaser[:3] - target[:3])
            )
            sampled_minimum = min(sampled_minimum, distance)
            recent.append(distance)
            if distance <= scenario.intercept_radius_km:
                denominator = max(previous_distance - distance, 1e-12)
                fraction = np.clip(
                    (previous_distance - scenario.intercept_radius_km) / denominator,
                    0.0,
                    1.0,
                )
                first_entry = current_time - duration + float(fraction) * duration
                break

            # A fast encounter can enter and leave the 5 km sphere between
            # two 60 s samples. A cubic-Hermite midpoint is inexpensive; only
            # promising intervals receive a one-dimensional refinement.
            relative_p0 = chaser_start[:3] - target_start[:3]
            relative_p1 = chaser[:3] - target[:3]
            relative_v0 = chaser_start[3:] - target_start[3:]
            relative_v1 = chaser[3:] - target[3:]
            midpoint_position = (
                0.5 * relative_p0
                + 0.125 * duration * relative_v0
                + 0.5 * relative_p1
                - 0.125 * duration * relative_v1
            )
            midpoint_distance = float(np.linalg.norm(midpoint_position))
            if midpoint_distance < min(previous_distance, distance):

                def hermite_distance(fraction_value: float) -> float:
                    u = float(fraction_value)
                    h00 = 2.0 * u**3 - 3.0 * u**2 + 1.0
                    h10 = u**3 - 2.0 * u**2 + u
                    h01 = -2.0 * u**3 + 3.0 * u**2
                    h11 = u**3 - u**2
                    position = (
                        h00 * relative_p0
                        + h10 * duration * relative_v0
                        + h01 * relative_p1
                        + h11 * duration * relative_v1
                    )
                    return float(np.linalg.norm(position))

                optimum = minimize_scalar(
                    hermite_distance,
                    bounds=(0.0, 1.0),
                    method="bounded",
                    options={"xatol": 1e-7},
                )
                local_minimum = float(optimum.fun)
                sampled_minimum = min(sampled_minimum, local_minimum)
                if local_minimum <= scenario.intercept_radius_km:
                    root_fraction = float(
                        brentq(
                            lambda value: hermite_distance(value)
                            - scenario.intercept_radius_km,
                            0.0,
                            float(optimum.x),
                            xtol=1e-9,
                        )
                    )
                    first_entry = current_time - duration + root_fraction * duration
                    break

        while (
            burn_index < len(burns)
            and abs(burns[burn_index].time_s - current_time) <= 1e-8
        ):
            chaser = apply_vnb_burn(chaser, burns[burn_index].dv_vnb_km_s)
            burn_index += 1
            executed_burns += 1

        increasing = len(recent) == recent.maxlen and all(
            later > earlier for earlier, later in zip(recent, list(recent)[1:])
        )
        if (
            settings.early_stop_enabled
            and current_time
            >= settings.early_stop_after_fraction * evaluation_end_s
            and sampled_minimum > settings.early_stop_distance_km
            and distance > 1.25 * sampled_minimum
            and increasing
            and burn_index >= len(burns)
        ):
            stopped_early = True
            break

    total_dv = sum(
        burn.magnitude_km_s() for burn in burns[:executed_burns]
    )
    approximate_score: float | None = None
    if config.score is not None:
        fake = SimulationMetrics(
            success=first_entry is not None,
            first_entry_time_s=first_entry,
            minimum_distance_km=sampled_minimum,
            minimum_distance_time_s=current_time,
            distance_at_horizon_km=max(distance, sampled_minimum)
            if stopped_early
            else distance,
            total_dv_km_s=total_dv,
            executed_burns=executed_burns,
            horizon_s=evaluation_end_s,
        )
        approximate_score = competition_score(
            fake, plan, scenario, config.score
        )
    return CoarseMetrics(
        success=first_entry is not None,
        first_entry_time_s=first_entry,
        sampled_minimum_distance_km=sampled_minimum,
        distance_at_stop_km=distance,
        total_dv_km_s=total_dv,
        executed_burns=executed_burns,
        propagated_to_s=current_time,
        stopped_early=stopped_early,
        approximate_score=approximate_score,
    )


def coarse_ranking_key(item: tuple[CandidatePlan, CoarseMetrics]) -> tuple[float, ...]:
    plan, metrics = item
    score = (
        metrics.approximate_score
        if metrics.approximate_score is not None
        else 0.0
    )
    entry_time = (
        metrics.first_entry_time_s
        if metrics.first_entry_time_s is not None
        else plan.evaluation_horizon_s
    )
    return (
        1.0 if metrics.success else 0.0,
        float(score),
        -metrics.sampled_minimum_distance_km,
        -metrics.total_dv_km_s,
        -float(entry_time),
    )
