from __future__ import annotations

from dataclasses import dataclass

import casadi as ca
import numpy as np

from ..astrodynamics import apply_vnb_burn, propagate_universal, spacecraft_state
from ..models import Burn, CandidatePlan, ProjectConfig, SimulationMetrics
from ..propagation import simulate_candidate
from ..robustness import Perturbation, make_perturbations, perturb_plan


@dataclass
class RefinementResult:
    plan: CandidatePlan
    metrics: SimulationMetrics
    converged: bool
    solver_status: str
    objective: float | None
    variable_count: int
    constraint_count: int
    jacobian_nonzeros: int


def _two_body_rhs(state: ca.MX, mu: float | ca.MX) -> ca.MX:
    r = state[0:3]
    v = state[3:6]
    radius = ca.sqrt(ca.dot(r, r) + 1e-24)
    return ca.vertcat(v, -mu * r / radius**3)


def _combined_rhs(state: ca.MX, mu: float | ca.MX) -> ca.MX:
    return ca.vertcat(
        _two_body_rhs(state[0:6], mu),
        _two_body_rhs(state[6:12], mu),
    )


def _rk4(state: ca.MX, duration_s: ca.MX, mu: float | ca.MX) -> ca.MX:
    k1 = _combined_rhs(state, mu)
    k2 = _combined_rhs(state + 0.5 * duration_s * k1, mu)
    k3 = _combined_rhs(state + 0.5 * duration_s * k2, mu)
    k4 = _combined_rhs(state + duration_s * k3, mu)
    return state + duration_s / 6.0 * (k1 + 2.0 * k2 + 2.0 * k3 + k4)


def _integrate_rk4(
    state: ca.MX,
    duration_s: ca.MX,
    mu: float | ca.MX,
    substeps: int,
) -> ca.MX:
    step = duration_s / substeps
    propagated = state
    for _ in range(substeps):
        propagated = _rk4(propagated, step, mu)
    return propagated


def _apply_symbolic_vnb_burn(combined_state: ca.MX, dv_vnb: ca.MX) -> ca.MX:
    chaser = combined_state[0:6]
    target = combined_state[6:12]
    r = chaser[0:3]
    v = chaser[3:6]
    v_hat = v / ca.sqrt(ca.dot(v, v) + 1e-24)
    normal = ca.cross(r, v)
    n_hat = normal / ca.sqrt(ca.dot(normal, normal) + 1e-24)
    b_axis = ca.cross(v_hat, n_hat)
    b_hat = b_axis / ca.sqrt(ca.dot(b_axis, b_axis) + 1e-24)
    delta_inertial = ca.horzcat(v_hat, n_hat, b_hat) @ dv_vnb
    return ca.vertcat(r, v + delta_inertial, target)


def _nominal_gaps(plan: CandidatePlan) -> np.ndarray:
    times = np.asarray([burn.time_s for burn in plan.burns])
    gaps = [float(times[0])]
    gaps.extend(float(times[index] - times[index - 1]) for index in range(1, len(times)))
    gaps.append(float(plan.evaluation_horizon_s - times[-1]))
    return np.asarray(gaps)


def _effective_symbolic_gaps(
    gaps: ca.MX,
    perturbation: Perturbation | None,
    n_burns: int,
) -> list[ca.MX]:
    if perturbation is None:
        return [gaps[index] for index in range(n_burns + 1)]
    timing = perturbation.timing_errors_s
    values: list[ca.MX] = [gaps[0] + float(timing[0])]
    for index in range(1, n_burns):
        values.append(gaps[index] + float(timing[index] - timing[index - 1]))
    values.append(gaps[n_burns] - float(timing[-1]))
    return values


class MultipleShootingRefiner:
    """Sparse, automatically differentiated multiple-shooting NLP."""

    def __init__(self, config: ProjectConfig):
        self.config = config

    def _numeric_node_guesses(
        self,
        plan: CandidatePlan,
        perturbation: Perturbation | None,
        mesh: int,
    ) -> list[np.ndarray]:
        scenario = self.config.scenario
        mu = scenario.mu_km3_s2
        chaser = spacecraft_state(scenario.chaser, mu)
        target = spacecraft_state(scenario.target, mu)
        rollout = plan
        if perturbation is not None:
            chaser = chaser + perturbation.chaser_state_error
            target = target + perturbation.target_state_error
            mu *= 1.0 + perturbation.mu_relative_error
            rollout = perturb_plan(plan, perturbation)
        gaps = _nominal_gaps(rollout)
        guesses: list[np.ndarray] = []
        for coast_index, coast_duration in enumerate(gaps):
            dt = coast_duration / mesh
            for _ in range(mesh):
                chaser = propagate_universal(chaser, dt, mu)
                target = propagate_universal(target, dt, mu)
                guesses.append(np.concatenate((chaser, target)))
            if coast_index < len(rollout.burns):
                chaser = apply_vnb_burn(
                    chaser, rollout.burns[coast_index].dv_vnb_km_s
                )
        return guesses

    def refine(self, initial_plan: CandidatePlan, *, time_weight: float | None = None) -> RefinementResult:
        scenario = self.config.scenario
        settings = self.config.multiple_shooting
        n_burns = len(initial_plan.burns)
        if n_burns < 1:
            raise ValueError("multiple shooting requires at least one burn")
        weight = settings.time_weight if time_weight is None else time_weight
        if not 0.0 <= weight <= 1.0:
            raise ValueError("time_weight must be in [0, 1]")

        opti = ca.Opti()
        gaps = opti.variable(n_burns + 1)
        dvs = opti.variable(3, n_burns)
        lower_gaps = np.zeros(n_burns + 1)
        if n_burns > 1:
            lower_gaps[1:n_burns] = scenario.min_burn_spacing_s
        opti.subject_to(gaps >= ca.DM(lower_gaps))
        opti.subject_to(ca.sum1(gaps) <= scenario.tmax_s())
        for index in range(n_burns):
            opti.subject_to(ca.sumsqr(dvs[:, index]) <= scenario.max_burn_dv_km_s**2)

        nominal_gaps = _nominal_gaps(initial_plan)
        nominal_dvs = np.column_stack(
            [np.asarray(burn.dv_vnb_km_s) for burn in initial_plan.burns]
        )
        opti.set_initial(gaps, nominal_gaps)
        opti.set_initial(dvs, nominal_dvs)

        robust_count = (
            settings.robust_scenarios if self.config.robustness.enabled else 0
        )
        perturbations = make_perturbations(self.config, n_burns, robust_count)
        scenario_set: list[Perturbation | None] = [None, *perturbations]
        mesh = settings.mesh_intervals_per_coast
        substeps = settings.rk4_substeps_per_interval

        for scenario_index, perturbation in enumerate(scenario_set):
            mu = scenario.mu_km3_s2
            chaser_initial = spacecraft_state(scenario.chaser, mu)
            target_initial = spacecraft_state(scenario.target, mu)
            if perturbation is not None:
                chaser_initial += perturbation.chaser_state_error
                target_initial += perturbation.target_state_error
                mu *= 1.0 + perturbation.mu_relative_error
            state: ca.MX = ca.DM(np.concatenate((chaser_initial, target_initial)))
            effective_gaps = _effective_symbolic_gaps(gaps, perturbation, n_burns)
            for effective_gap in effective_gaps:
                opti.subject_to(effective_gap >= 0.0)
            node_guesses = self._numeric_node_guesses(initial_plan, perturbation, mesh)
            node_cursor = 0
            for coast_index in range(n_burns + 1):
                step = effective_gaps[coast_index] / mesh
                for _ in range(mesh):
                    next_state = opti.variable(12)
                    opti.subject_to(
                        next_state == _integrate_rk4(state, step, mu, substeps)
                    )
                    opti.set_initial(next_state, node_guesses[node_cursor])
                    node_cursor += 1
                    state = next_state
                if coast_index < n_burns:
                    scenario_dv = dvs[:, coast_index]
                    if perturbation is not None:
                        scenario_dv = (
                            scenario_dv
                            * (1.0 + float(perturbation.burn_relative_errors[coast_index]))
                            + ca.DM(perturbation.burn_component_errors[coast_index])
                        )
                    state = _apply_symbolic_vnb_burn(state, scenario_dv)

            relative_position = state[0:3] - state[6:9]
            if scenario_index == 0:
                terminal_radius = max(
                    0.05,
                    scenario.intercept_radius_km
                    - self.config.robustness.safety_margin_km,
                )
            else:
                terminal_radius = scenario.intercept_radius_km
            opti.subject_to(ca.sumsqr(relative_position) <= terminal_radius**2)

        smooth_total_dv = sum(
            ca.sqrt(ca.sumsqr(dvs[:, index]) + 1e-16)
            for index in range(n_burns)
        )
        normalized_time = ca.sum1(gaps) / scenario.tmax_s()
        normalized_dv = smooth_total_dv / (
            n_burns * scenario.max_burn_dv_km_s
        )
        objective = weight * normalized_time + (1.0 - weight) * normalized_dv
        opti.minimize(objective)

        jacobian = ca.jacobian(opti.g, opti.x)
        variable_count = int(opti.x.numel())
        constraint_count = int(opti.g.numel())
        jacobian_nonzeros = int(jacobian.sparsity().nnz())
        opti.solver(
            "ipopt",
            {"expand": True, "print_time": False},
            {
                "max_iter": settings.max_iterations,
                "tol": settings.tolerance,
                "constr_viol_tol": settings.tolerance,
                "acceptable_tol": max(settings.tolerance * 10.0, 1e-8),
                "print_level": 0,
                "sb": "yes",
            },
        )

        converged = False
        solver_status = "not_started"
        objective_value: float | None = None
        refined = initial_plan
        try:
            solution = opti.solve()
            solver_status = str(opti.stats().get("return_status", "unknown"))
            converged = bool(opti.stats().get("success", False))
            solved_gaps = np.asarray(solution.value(gaps), dtype=float).reshape(-1)
            solved_dvs = np.asarray(solution.value(dvs), dtype=float).reshape(3, n_burns)
            objective_value = float(solution.value(objective))
            burns: list[Burn] = []
            current_time = float(solved_gaps[0])
            for index in range(n_burns):
                dv = solved_dvs[:, index]
                magnitude = float(np.linalg.norm(dv))
                if magnitude > scenario.max_burn_dv_km_s:
                    dv *= scenario.max_burn_dv_km_s / magnitude
                burns.append(
                    Burn(
                        time_s=current_time,
                        dv_vnb_km_s=tuple(float(value) for value in dv),
                    )
                )
                if index + 1 < n_burns:
                    current_time += float(solved_gaps[index + 1])
            refined = CandidatePlan(
                name=f"{initial_plan.name}-ipopt",
                burns=burns,
                evaluation_horizon_s=float(solved_gaps.sum()),
                source="casadi-ipopt-multiple-shooting",
                metadata={
                    **initial_plan.metadata,
                    "time_weight": float(weight),
                    "ipopt_status": solver_status,
                },
            )
        except RuntimeError as exc:
            stats = opti.stats()
            solver_status = str(stats.get("return_status", type(exc).__name__))

        metrics = simulate_candidate(
            refined,
            scenario,
            self.config.propagation,
        )
        if converged and not metrics.success:
            converged = False
            solver_status = f"{solver_status}; exact-propagation post-check failed"
        return RefinementResult(
            plan=refined,
            metrics=metrics,
            converged=converged,
            solver_status=solver_status,
            objective=objective_value,
            variable_count=variable_count,
            constraint_count=constraint_count,
            jacobian_nonzeros=jacobian_nonzeros,
        )
