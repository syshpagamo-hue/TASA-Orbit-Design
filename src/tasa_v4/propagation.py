from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.optimize import brentq, minimize_scalar

from .astrodynamics import apply_vnb_burn, propagate_universal, spacecraft_state
from .models import CandidatePlan, PropagationConfig, ScenarioConfig, SimulationMetrics


@dataclass(frozen=True)
class _SegmentResult:
    first_entry_time_s: float | None
    minimum_distance_km: float
    minimum_distance_time_s: float
    final_chaser: np.ndarray
    final_target: np.ndarray


def _scan_coast(
    chaser_start: np.ndarray,
    target_start: np.ndarray,
    start_s: float,
    end_s: float,
    mu_km3_s2: float,
    radius_km: float,
    settings: PropagationConfig,
) -> _SegmentResult:
    duration = end_s - start_s
    if duration < -1e-10:
        raise ValueError("coast segment has negative duration")
    if duration <= 1e-12:
        distance = float(np.linalg.norm(chaser_start[:3] - target_start[:3]))
        return _SegmentResult(
            first_entry_time_s=start_s if distance <= radius_km else None,
            minimum_distance_km=distance,
            minimum_distance_time_s=start_s,
            final_chaser=chaser_start.copy(),
            final_target=target_start.copy(),
        )

    state_cache: dict[float, tuple[np.ndarray, np.ndarray, float]] = {}

    def state_and_distance(absolute_time_s: float) -> tuple[np.ndarray, np.ndarray, float]:
        key = round(float(absolute_time_s), 12)
        cached = state_cache.get(key)
        if cached is not None:
            return cached
        dt = float(absolute_time_s - start_s)
        chaser = propagate_universal(
            chaser_start,
            dt,
            mu_km3_s2,
            tolerance=settings.universal_tolerance,
            max_iterations=settings.universal_max_iterations,
        )
        target = propagate_universal(
            target_start,
            dt,
            mu_km3_s2,
            tolerance=settings.universal_tolerance,
            max_iterations=settings.universal_max_iterations,
        )
        value = (chaser, target, float(np.linalg.norm(chaser[:3] - target[:3])))
        state_cache[key] = value
        return value

    count = max(1, int(math.ceil(duration / settings.event_scan_step_s)))
    grid = np.linspace(start_s, end_s, count + 1)
    sampled_states = [state_and_distance(float(t)) for t in grid]
    distances = np.asarray([sample[2] for sample in sampled_states])
    min_index = int(np.argmin(distances))
    minimum_distance = float(distances[min_index])
    minimum_time = float(grid[min_index])
    first_entry: float | None = None

    if distances[0] <= radius_km:
        first_entry = float(start_s)

    def signed_distance(time_s: float) -> float:
        return state_and_distance(float(time_s))[2] - radius_km

    for index in range(1, len(grid)):
        left = float(grid[index - 1])
        right = float(grid[index])
        if distances[index - 1] > radius_km and distances[index] <= radius_km:
            root = float(
                brentq(
                    signed_distance,
                    left,
                    right,
                    xtol=settings.root_tolerance_s,
                )
            )
            first_entry = root if first_entry is None else min(first_entry, root)
            break

    # A fast encounter can enter and leave the sphere between two grid points.
    # Refine every sampled local minimum so such a hidden crossing is detected.
    refinement_brackets: set[tuple[float, float]] = {
        (float(grid[index - 1]), float(grid[index + 1]))
        for index in range(1, len(grid) - 1)
        if distances[index] <= distances[index - 1]
        and distances[index] <= distances[index + 1]
    }
    if len(grid) == 2:
        refinement_brackets.add((float(grid[0]), float(grid[1])))
    elif len(grid) > 2:
        if distances[0] <= distances[1]:
            refinement_brackets.add((float(grid[0]), float(grid[1])))
        if distances[-1] <= distances[-2]:
            refinement_brackets.add((float(grid[-2]), float(grid[-1])))

    # A midpoint Hermite check is cheap and catches an enter-and-exit event
    # hidden entirely between adjacent scan samples.
    for index in range(len(grid) - 1):
        left, right = float(grid[index]), float(grid[index + 1])
        dt = right - left
        chaser0, target0, _ = sampled_states[index]
        chaser1, target1, _ = sampled_states[index + 1]
        p0 = chaser0[:3] - target0[:3]
        p1 = chaser1[:3] - target1[:3]
        v0 = chaser0[3:] - target0[3:]
        v1 = chaser1[3:] - target1[3:]
        midpoint_position = 0.5 * p0 + 0.125 * dt * v0 + 0.5 * p1 - 0.125 * dt * v1
        midpoint_distance = float(np.linalg.norm(midpoint_position))
        if midpoint_distance + 1e-8 < min(distances[index], distances[index + 1]):
            refinement_brackets.add((left, right))

    for left, right in sorted(refinement_brackets):
        optimum = minimize_scalar(
            lambda time_s: state_and_distance(float(time_s))[2],
            bounds=(left, right),
            method="bounded",
            options={"xatol": settings.root_tolerance_s},
        )
        local_time = float(optimum.x)
        local_distance = float(optimum.fun)
        if local_distance < minimum_distance:
            minimum_distance = local_distance
            minimum_time = local_time
        if local_distance <= radius_km and (first_entry is None or left < first_entry):
            if signed_distance(left) <= 0:
                root = left
            else:
                root = float(
                    brentq(
                        signed_distance,
                        left,
                        local_time,
                        xtol=settings.root_tolerance_s,
                    )
                )
            first_entry = root if first_entry is None else min(first_entry, root)

    final_chaser, final_target, _ = state_and_distance(float(end_s))
    return _SegmentResult(
        first_entry_time_s=first_entry,
        minimum_distance_km=minimum_distance,
        minimum_distance_time_s=minimum_time,
        final_chaser=final_chaser,
        final_target=final_target,
    )


def simulate_candidate(
    plan: CandidatePlan,
    scenario: ScenarioConfig,
    settings: PropagationConfig,
    *,
    chaser_initial: np.ndarray | None = None,
    target_initial: np.ndarray | None = None,
    mu_override_km3_s2: float | None = None,
) -> SimulationMetrics:
    violations = plan.violations(scenario)
    if violations:
        raise ValueError("; ".join(violations))
    mu = float(mu_override_km3_s2 or scenario.mu_km3_s2)
    chaser = (
        np.asarray(chaser_initial, dtype=float).copy()
        if chaser_initial is not None
        else spacecraft_state(scenario.chaser, mu)
    )
    target = (
        np.asarray(target_initial, dtype=float).copy()
        if target_initial is not None
        else spacecraft_state(scenario.target, mu)
    )
    current_time = 0.0
    first_entry: float | None = None
    minimum_distance = math.inf
    minimum_time = 0.0

    for burn in plan.burns:
        coast = _scan_coast(
            chaser,
            target,
            current_time,
            burn.time_s,
            mu,
            scenario.intercept_radius_km,
            settings,
        )
        if coast.first_entry_time_s is not None:
            first_entry = (
                coast.first_entry_time_s
                if first_entry is None
                else min(first_entry, coast.first_entry_time_s)
            )
        if coast.minimum_distance_km < minimum_distance:
            minimum_distance = coast.minimum_distance_km
            minimum_time = coast.minimum_distance_time_s
        chaser, target = coast.final_chaser, coast.final_target
        current_time = burn.time_s
        chaser = apply_vnb_burn(chaser, burn.dv_vnb_km_s)

    coast = _scan_coast(
        chaser,
        target,
        current_time,
        plan.evaluation_horizon_s,
        mu,
        scenario.intercept_radius_km,
        settings,
    )
    if coast.first_entry_time_s is not None:
        first_entry = (
            coast.first_entry_time_s
            if first_entry is None
            else min(first_entry, coast.first_entry_time_s)
        )
    if coast.minimum_distance_km < minimum_distance:
        minimum_distance = coast.minimum_distance_km
        minimum_time = coast.minimum_distance_time_s
    distance_at_horizon = float(
        np.linalg.norm(coast.final_chaser[:3] - coast.final_target[:3])
    )
    cutoff = first_entry if first_entry is not None else plan.evaluation_horizon_s
    executed = [burn for burn in plan.burns if burn.time_s <= cutoff + 1e-9]
    return SimulationMetrics(
        success=first_entry is not None,
        first_entry_time_s=first_entry,
        minimum_distance_km=float(minimum_distance),
        minimum_distance_time_s=float(minimum_time),
        distance_at_horizon_km=distance_at_horizon,
        total_dv_km_s=sum(burn.magnitude_km_s() for burn in executed),
        executed_burns=len(executed),
        horizon_s=plan.evaluation_horizon_s,
    )
