from __future__ import annotations

import math
from collections.abc import Iterable

import numpy as np
from scipy.optimize import least_squares

from .astrodynamics import (
    apply_vnb_burn,
    propagate_universal,
    spacecraft_state,
    specific_energy,
    vnb_basis,
)
from .models import Burn, CandidatePlan, ProjectConfig


def _unit(vector: np.ndarray) -> np.ndarray:
    magnitude = float(np.linalg.norm(vector))
    if magnitude <= 1e-14:
        raise ValueError("cannot normalize a zero vector")
    return np.asarray(vector, dtype=float) / magnitude


def _cap(vector: np.ndarray, maximum: float) -> tuple[np.ndarray, bool]:
    value = np.asarray(vector, dtype=float)
    magnitude = float(np.linalg.norm(value))
    if magnitude <= maximum:
        return value, False
    return value * (maximum / magnitude), True


def _plane_normal(state: np.ndarray) -> np.ndarray:
    return _unit(np.cross(state[:3], state[3:]))


def _plane_angle_rad(first: np.ndarray, second: np.ndarray) -> float:
    cosine = float(
        np.clip(np.dot(_plane_normal(first), _plane_normal(second)), -1.0, 1.0)
    )
    return math.acos(cosine)


def _forward_angle(
    origin: np.ndarray,
    destination: np.ndarray,
    normal: np.ndarray,
) -> float:
    first = _unit(origin)
    second = _unit(destination)
    sine = float(np.dot(_unit(normal), np.cross(first, second)))
    cosine = float(np.clip(np.dot(first, second), -1.0, 1.0))
    return math.atan2(sine, cosine) % (2.0 * math.pi)


def _orbital_period_s(state: np.ndarray, mu_km3_s2: float) -> float:
    energy = specific_energy(state, mu_km3_s2)
    if energy >= 0.0:
        raise ValueError("state is not on a bound orbit")
    semi_major_km = -mu_km3_s2 / (2.0 * energy)
    return 2.0 * math.pi * math.sqrt(semi_major_km**3 / mu_km3_s2)


def _eci_burns_to_plan(
    config: ProjectConfig,
    *,
    name: str,
    source: str,
    eci_burns: list[tuple[float, np.ndarray]],
    horizon_s: float,
    metadata: dict[str, str | int | float | bool | None],
) -> CandidatePlan:
    scenario = config.scenario
    mu = scenario.mu_km3_s2
    state = spacecraft_state(scenario.chaser, mu)
    current_time = 0.0
    burns: list[Burn] = []
    for time_s, dv_eci in sorted(eci_burns, key=lambda item: item[0]):
        if time_s > current_time:
            state = propagate_universal(
                state,
                time_s - current_time,
                mu,
                tolerance=config.propagation.universal_tolerance,
                max_iterations=config.propagation.universal_max_iterations,
            )
            current_time = float(time_s)
        dv_vnb = vnb_basis(state).T @ np.asarray(dv_eci, dtype=float)
        burn = Burn(
            time_s=float(time_s),
            dv_vnb_km_s=tuple(float(value) for value in dv_vnb),
        )
        burns.append(burn)
        state = apply_vnb_burn(state, burn.dv_vnb_km_s)
    return CandidatePlan(
        name=name,
        burns=burns,
        evaluation_horizon_s=float(horizon_s),
        source=source,
        metadata=metadata,
    )


def _estimate_completed_revolutions(
    state: np.ndarray,
    tof_s: float,
    config: ProjectConfig,
    requested_revolutions: int,
) -> int:
    normal = _plane_normal(state)
    radial0 = _unit(state[:3])
    transverse0 = _unit(np.cross(normal, radial0))
    samples = max(48, 64 * (requested_revolutions + 1))
    angles: list[float] = []
    for time_s in np.linspace(0.0, tof_s, samples):
        current = propagate_universal(
            state,
            float(time_s),
            config.scenario.mu_km3_s2,
            tolerance=config.propagation.universal_tolerance,
            max_iterations=config.propagation.universal_max_iterations,
        )
        radial = _unit(current[:3])
        angles.append(
            math.atan2(
                float(np.dot(radial, transverse0)),
                float(np.dot(radial, radial0)),
            )
        )
    unwrapped = np.unwrap(np.asarray(angles))
    return max(
        0,
        int(
            math.floor(
                (abs(float(unwrapped[-1])) + 1e-8) / (2.0 * math.pi)
            )
        ),
    )


def lambert_shooting_seeds(config: ProjectConfig) -> list[CandidatePlan]:
    """Zero- and multi-revolution Lambert-like shooting seeds."""

    scenario = config.scenario
    settings = config.physical_seeds
    mu = scenario.mu_km3_s2
    departure = spacecraft_state(scenario.chaser, mu)
    target = spacecraft_state(scenario.target, mu)
    target_period = scenario.target_period_s()
    times_of_flight = [
        fraction * target_period for fraction in settings.lambert_tof_fractions
    ]
    r0 = departure[:3]
    v0 = departure[3:]
    radius0 = float(np.linalg.norm(r0))
    normal = _plane_normal(departure)
    tangent = _unit(np.cross(normal, _unit(r0)))
    if np.dot(tangent, v0) < 0.0:
        tangent *= -1.0

    plans: list[CandidatePlan] = []
    seen: set[tuple[int, int, int, int]] = set()
    orientations = (1.0, -1.0) if settings.lambert_include_retrograde else (1.0,)

    for tof_s in times_of_flight:
        if tof_s < scenario.min_burn_spacing_s or tof_s > scenario.tmax_s():
            continue
        target_at_arrival = propagate_universal(
            target,
            tof_s,
            mu,
            tolerance=config.propagation.universal_tolerance,
            max_iterations=config.propagation.universal_max_iterations,
        )
        direct_guess = (target_at_arrival[:3] - r0) / tof_s

        for revolution in settings.lambert_revolutions:
            branch_period = tof_s / max(revolution + 0.5, 0.5)
            semi_major_guess = (
                mu * (branch_period / (2.0 * math.pi)) ** 2
            ) ** (1.0 / 3.0)
            speed_squared = mu * (2.0 / radius0 - 1.0 / semi_major_guess)
            branch_speed = math.sqrt(max(speed_squared, 0.01))
            radial_guess = float(
                np.dot(target_at_arrival[:3] - r0, _unit(r0)) / tof_s
            )

            for orientation in orientations:
                guesses = [
                    orientation * branch_speed * tangent
                    + radial_guess * _unit(r0),
                    0.75 * orientation * branch_speed * tangent
                    + 0.25 * direct_guess,
                ]
                if revolution == 0:
                    guesses.append(direct_guess)

                best_velocity: np.ndarray | None = None
                best_error_km = math.inf
                for guess in guesses:

                    def residual(departure_velocity: np.ndarray) -> np.ndarray:
                        try:
                            trial = np.concatenate((r0, departure_velocity))
                            arrival = propagate_universal(
                                trial,
                                tof_s,
                                mu,
                                tolerance=config.propagation.universal_tolerance,
                                max_iterations=config.propagation.universal_max_iterations,
                            )
                            return (arrival[:3] - target_at_arrival[:3]) / 1000.0
                        except (
                            ArithmeticError,
                            FloatingPointError,
                            OverflowError,
                            RuntimeError,
                            ValueError,
                        ):
                            return np.full(3, 1e6)

                    solution = least_squares(
                        residual,
                        guess,
                        method="trf",
                        bounds=(-20.0, 20.0),
                        xtol=1e-9,
                        ftol=1e-9,
                        gtol=1e-9,
                        max_nfev=80,
                    )
                    error_km = float(np.linalg.norm(residual(solution.x)) * 1000.0)
                    if solution.success and error_km < best_error_km:
                        best_velocity = np.asarray(solution.x, dtype=float)
                        best_error_km = error_km

                if best_velocity is None or best_error_km > 25.0:
                    continue
                transfer_state = np.concatenate((r0, best_velocity))
                estimated_revolutions = _estimate_completed_revolutions(
                    transfer_state,
                    tof_s,
                    config,
                    revolution,
                )
                if estimated_revolutions != revolution:
                    continue
                exact_dv = best_velocity - v0
                legal_dv, clipped = _cap(exact_dv, scenario.max_burn_dv_km_s)
                signature = tuple(
                    np.round(np.concatenate(([tof_s], legal_dv)) * 1000).astype(int)
                )
                if signature in seen:
                    continue
                seen.add(signature)
                plans.append(
                    _eci_burns_to_plan(
                        config,
                        name=f"lambert-{len(plans) + 1:04d}",
                        source="physical:lambert_shooting",
                        eci_burns=[(0.0, legal_dv)],
                        horizon_s=tof_s,
                        metadata={
                            "tof_s": float(tof_s),
                            "requested_revolutions": int(revolution),
                            "estimated_revolutions": int(estimated_revolutions),
                            "orientation": "prograde"
                            if orientation > 0
                            else "retrograde",
                            "shooting_position_error_km": best_error_km,
                            "exact_departure_dv_km_s": float(np.linalg.norm(exact_dv)),
                            "clipped_to_burn_limit": clipped,
                        },
                    )
                )
                if len(plans) >= settings.lambert_max_solutions:
                    return plans
    return plans


def phasing_seeds(config: ProjectConfig) -> list[CandidatePlan]:
    scenario = config.scenario
    mu = scenario.mu_km3_s2
    interceptor = spacecraft_state(scenario.chaser, mu)
    target = spacecraft_state(scenario.target, mu)
    interceptor_normal = _plane_normal(interceptor)
    target_projected = target[:3] - np.dot(
        target[:3], interceptor_normal
    ) * interceptor_normal
    phase_ahead = _forward_angle(
        interceptor[:3], target_projected, interceptor_normal
    )
    if phase_ahead > math.pi:
        phase_ahead -= 2.0 * math.pi

    radius = float(np.linalg.norm(interceptor[:3]))
    tangent = _unit(np.cross(interceptor_normal, _unit(interceptor[:3])))
    if np.dot(tangent, interceptor[3:]) < 0.0:
        tangent *= -1.0
    current_tangent_speed = float(np.dot(interceptor[3:], tangent))
    target_period = scenario.target_period_s()
    target_mean_motion = 2.0 * math.pi / target_period
    plans: list[CandidatePlan] = []

    for periods in config.physical_seeds.phasing_closure_periods:
        closure_time = float(periods) * target_period
        if closure_time <= 0.0 or closure_time > scenario.tmax_s():
            continue
        desired_mean_motion = target_mean_motion + phase_ahead / closure_time
        if desired_mean_motion <= 0.0:
            continue
        desired_a = (mu / desired_mean_motion**2) ** (1.0 / 3.0)
        desired_speed_squared = mu * (2.0 / radius - 1.0 / desired_a)
        if desired_speed_squared <= 0.0:
            continue
        desired_speed = math.sqrt(desired_speed_squared)
        exact_dv = (desired_speed - current_tangent_speed) * tangent
        dv1, clipped1 = _cap(exact_dv, scenario.max_burn_dv_km_s)
        strategy = (
            "lower_and_run_faster"
            if float(np.dot(exact_dv, tangent)) < 0.0
            else "raise_and_wait"
        )
        common_metadata: dict[str, str | int | float | bool | None] = {
            "closure_periods": float(periods),
            "closure_time_s": closure_time,
            "phase_ahead_rad": phase_ahead,
            "desired_semi_major_km": desired_a,
            "strategy": strategy,
            "clipped_to_burn_limit": clipped1,
        }
        plans.append(
            _eci_burns_to_plan(
                config,
                name=f"phasing-1burn-{len(plans) + 1:04d}",
                source="physical:phasing",
                eci_burns=[(0.0, dv1)],
                horizon_s=closure_time,
                metadata={**common_metadata, "return_burn": False},
            )
        )

        if closure_time >= scenario.min_burn_spacing_s:
            phased = np.concatenate((interceptor[:3], interceptor[3:] + dv1))
            phased_at_closure = propagate_universal(
                phased,
                closure_time,
                mu,
                tolerance=config.propagation.universal_tolerance,
                max_iterations=config.propagation.universal_max_iterations,
            )
            target_at_closure = propagate_universal(
                target,
                closure_time,
                mu,
                tolerance=config.propagation.universal_tolerance,
                max_iterations=config.propagation.universal_max_iterations,
            )
            dv2, clipped2 = _cap(
                target_at_closure[3:] - phased_at_closure[3:],
                scenario.max_burn_dv_km_s,
            )
            plans.append(
                _eci_burns_to_plan(
                    config,
                    name=f"phasing-2burn-{len(plans) + 1:04d}",
                    source="physical:phasing",
                    eci_burns=[(0.0, dv1), (closure_time, dv2)],
                    horizon_s=closure_time,
                    metadata={
                        **common_metadata,
                        "return_burn": True,
                        "return_burn_clipped": clipped2,
                    },
                )
            )
    return plans


def hohmann_seeds(config: ProjectConfig) -> list[CandidatePlan]:
    scenario = config.scenario
    mu = scenario.mu_km3_s2
    interceptor = spacecraft_state(scenario.chaser, mu)
    target = spacecraft_state(scenario.target, mu)
    radius1 = float(np.linalg.norm(interceptor[:3]))
    radius2 = float(np.linalg.norm(target[:3]))
    if abs(radius2 - radius1) < 1e-6:
        return []
    transfer_a = (radius1 + radius2) / 2.0
    transfer_time = math.pi * math.sqrt(transfer_a**3 / mu)
    if (
        transfer_time < scenario.min_burn_spacing_s
        or transfer_time > scenario.tmax_s()
    ):
        return []
    tangent = _unit(np.cross(_plane_normal(interceptor), _unit(interceptor[:3])))
    if np.dot(tangent, interceptor[3:]) < 0.0:
        tangent *= -1.0
    current_tangent_speed = float(np.dot(interceptor[3:], tangent))
    transfer_departure = math.sqrt(mu * (2.0 / radius1 - 1.0 / transfer_a))
    dv1, clipped1 = _cap(
        (transfer_departure - current_tangent_speed) * tangent,
        scenario.max_burn_dv_km_s,
    )
    transfer_start = np.concatenate((interceptor[:3], interceptor[3:] + dv1))
    transfer_arrival = propagate_universal(
        transfer_start,
        transfer_time,
        mu,
        tolerance=config.propagation.universal_tolerance,
        max_iterations=config.propagation.universal_max_iterations,
    )
    desired_arrival_speed = math.sqrt(mu / radius2)
    dv2_raw = (
        desired_arrival_speed - np.linalg.norm(transfer_arrival[3:])
    ) * _unit(transfer_arrival[3:])
    dv2, clipped2 = _cap(dv2_raw, scenario.max_burn_dv_km_s)
    return [
        _eci_burns_to_plan(
            config,
            name="hohmann-0001",
            source="physical:hohmann",
            eci_burns=[(0.0, dv1), (transfer_time, dv2)],
            horizon_s=transfer_time,
            metadata={
                "radius1_km": radius1,
                "radius2_km": radius2,
                "transfer_time_s": transfer_time,
                "phase_match_not_guaranteed": True,
                "clipped_to_burn_limit": clipped1 or clipped2,
            },
        )
    ]


def node_plane_change_seeds(config: ProjectConfig) -> list[CandidatePlan]:
    scenario = config.scenario
    mu = scenario.mu_km3_s2
    interceptor = spacecraft_state(scenario.chaser, mu)
    target = spacecraft_state(scenario.target, mu)
    normal_i = _plane_normal(interceptor)
    normal_t = _plane_normal(target)
    node = np.cross(normal_i, normal_t)
    if np.linalg.norm(node) < 1e-10:
        return []
    period = _orbital_period_s(interceptor, mu)
    options: list[tuple[float, np.ndarray]] = []
    for direction in (_unit(node), -_unit(node)):
        angle = _forward_angle(interceptor[:3], direction, normal_i)
        options.append((angle / (2.0 * math.pi) * period, direction))
    time_to_node, _ = min(options, key=lambda item: item[0])
    if time_to_node > scenario.tmax_s():
        return []
    at_node = propagate_universal(
        interceptor,
        time_to_node,
        mu,
        tolerance=config.propagation.universal_tolerance,
        max_iterations=config.propagation.universal_max_iterations,
    )
    desired_direction = _unit(np.cross(normal_t, _unit(at_node[:3])))
    if np.dot(desired_direction, at_node[3:]) < 0.0:
        desired_direction *= -1.0
    exact_dv = np.linalg.norm(at_node[3:]) * desired_direction - at_node[3:]
    dv, clipped = _cap(exact_dv, scenario.max_burn_dv_km_s)
    horizon = min(
        scenario.tmax_s(),
        max(time_to_node, time_to_node + 0.5 * scenario.target_period_s()),
    )
    return [
        _eci_burns_to_plan(
            config,
            name="node-plane-change-0001",
            source="physical:node_plane_change",
            eci_burns=[(time_to_node, dv)],
            horizon_s=horizon,
            metadata={
                "plane_angle_deg": math.degrees(
                    _plane_angle_rad(interceptor, target)
                ),
                "time_to_node_s": time_to_node,
                "exact_plane_change_dv_km_s": float(np.linalg.norm(exact_dv)),
                "clipped_to_burn_limit": clipped,
            },
        )
    ]


def high_apogee_plane_change_seeds(config: ProjectConfig) -> list[CandidatePlan]:
    scenario = config.scenario
    mu = scenario.mu_km3_s2
    interceptor = spacecraft_state(scenario.chaser, mu)
    target = spacecraft_state(scenario.target, mu)
    target_normal = _plane_normal(target)
    tangent = _unit(interceptor[3:])
    plans: list[CandidatePlan] = []
    radius = float(np.linalg.norm(interceptor[:3]))

    for raise_dv in config.physical_seeds.high_apogee_raise_burns_km_s:
        dv1, raise_clipped = _cap(
            float(raise_dv) * tangent,
            scenario.max_burn_dv_km_s,
        )
        raised = np.concatenate((interceptor[:3], interceptor[3:] + dv1))
        energy = specific_energy(raised, mu)
        if energy >= 0.0:
            continue
        semi_major = -mu / (2.0 * energy)
        time_to_apogee = math.pi * math.sqrt(semi_major**3 / mu)
        if (
            time_to_apogee < scenario.min_burn_spacing_s
            or time_to_apogee > scenario.tmax_s()
        ):
            continue
        at_apogee = propagate_universal(
            raised,
            time_to_apogee,
            mu,
            tolerance=config.propagation.universal_tolerance,
            max_iterations=config.propagation.universal_max_iterations,
        )
        desired_direction = _unit(np.cross(target_normal, _unit(at_apogee[:3])))
        if np.dot(desired_direction, at_apogee[3:]) < 0.0:
            desired_direction *= -1.0
        exact_plane_dv = (
            np.linalg.norm(at_apogee[3:]) * desired_direction - at_apogee[3:]
        )
        plane_dv, clipped = _cap(
            exact_plane_dv, scenario.max_burn_dv_km_s
        )
        horizon = min(
            scenario.tmax_s(),
            time_to_apogee + 0.5 * scenario.target_period_s(),
        )
        plans.append(
            _eci_burns_to_plan(
                config,
                name=f"high-apogee-{len(plans) + 1:04d}",
                source="physical:high_apogee_plane_change",
                eci_burns=[(0.0, dv1), (time_to_apogee, plane_dv)],
                horizon_s=horizon,
                metadata={
                    "raise_burn_km_s": float(raise_dv),
                    "raise_burn_clipped": raise_clipped,
                    "time_to_apogee_s": time_to_apogee,
                    "plane_angle_deg": math.degrees(
                        _plane_angle_rad(interceptor, target)
                    ),
                    "exact_plane_change_dv_km_s": float(
                        np.linalg.norm(exact_plane_dv)
                    ),
                    "clipped_to_burn_limit": clipped,
                },
            )
        )
    return plans


def build_physical_seed_bank(config: ProjectConfig) -> list[CandidatePlan]:
    settings = config.physical_seeds
    if not settings.enabled:
        return []
    plans: list[CandidatePlan] = []
    if settings.lambert_enabled:
        plans.extend(lambert_shooting_seeds(config))
    if settings.phasing_enabled:
        plans.extend(phasing_seeds(config))
    if settings.hohmann_enabled:
        plans.extend(hohmann_seeds(config))
    if settings.node_plane_change_enabled:
        plans.extend(node_plane_change_seeds(config))
    if settings.high_apogee_enabled:
        plans.extend(high_apogee_plane_change_seeds(config))
    return plans
