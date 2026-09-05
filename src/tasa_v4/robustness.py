from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.stats import norm, qmc

from .astrodynamics import spacecraft_state
from .models import (
    Burn,
    CandidatePlan,
    ProjectConfig,
    RobustMetrics,
    SimulationMetrics,
)
from .propagation import simulate_candidate
from .scoring import competition_score


@dataclass(frozen=True)
class Perturbation:
    chaser_state_error: np.ndarray
    target_state_error: np.ndarray
    mu_relative_error: float
    timing_errors_s: np.ndarray
    burn_relative_errors: np.ndarray
    burn_component_errors: np.ndarray


def _standard_samples(count: int, dimension: int, seed: int, distribution: str) -> np.ndarray:
    if count <= 0:
        return np.empty((0, dimension))
    sampler = qmc.Sobol(d=dimension, scramble=True, seed=seed)
    exponent = int(math.ceil(math.log2(count)))
    unit = sampler.random_base2(exponent)[:count]
    if distribution == "normal":
        return norm.ppf(np.clip(unit, 1e-9, 1.0 - 1e-9))
    return (2.0 * unit - 1.0) * np.sqrt(3.0)


def make_perturbations(config: ProjectConfig, n_burns: int, count: int) -> list[Perturbation]:
    robust = config.robustness
    dimension = 13 + 5 * n_burns
    raw = _standard_samples(
        count,
        dimension,
        robust.sample_seed + 1009 * n_burns + count,
        robust.distribution,
    )
    scenarios: list[Perturbation] = []
    for row in raw:
        cursor = 0
        chaser_error = np.concatenate(
            (
                robust.position_sigma_km * row[cursor : cursor + 3],
                robust.velocity_sigma_km_s * row[cursor + 3 : cursor + 6],
            )
        )
        cursor += 6
        target_error = np.concatenate(
            (
                robust.position_sigma_km * row[cursor : cursor + 3],
                robust.velocity_sigma_km_s * row[cursor + 3 : cursor + 6],
            )
        )
        cursor += 6
        mu_error = robust.mu_relative_sigma * row[cursor]
        cursor += 1
        timing = robust.burn_timing_sigma_s * row[cursor : cursor + n_burns]
        cursor += n_burns
        relative = robust.burn_relative_sigma * row[cursor : cursor + n_burns]
        cursor += n_burns
        component = robust.burn_component_sigma_km_s * row[
            cursor : cursor + 3 * n_burns
        ].reshape(n_burns, 3)
        scenarios.append(
            Perturbation(
                chaser_state_error=chaser_error,
                target_state_error=target_error,
                mu_relative_error=float(mu_error),
                timing_errors_s=timing,
                burn_relative_errors=relative,
                burn_component_errors=component,
            )
        )
    return scenarios


def perturb_plan(plan: CandidatePlan, perturbation: Perturbation) -> CandidatePlan:
    burns: list[Burn] = []
    previous_time = -1e-9
    for index, burn in enumerate(plan.burns):
        time_s = max(0.0, burn.time_s + float(perturbation.timing_errors_s[index]))
        time_s = max(time_s, previous_time + 1e-8)
        nominal = np.asarray(burn.dv_vnb_km_s)
        delta_v = (
            nominal * (1.0 + perturbation.burn_relative_errors[index])
            + perturbation.burn_component_errors[index]
        )
        burns.append(Burn(time_s=time_s, dv_vnb_km_s=tuple(delta_v)))
        previous_time = time_s
    horizon = max(plan.evaluation_horizon_s, previous_time)
    return CandidatePlan(
        name=plan.name,
        burns=burns,
        evaluation_horizon_s=horizon,
        source=plan.source,
        metadata=plan.metadata,
    )


class RobustEvaluator:
    def __init__(self, config: ProjectConfig, n_burns: int, count: int):
        self.config = config
        self.perturbations = make_perturbations(config, n_burns, count)
        self.nominal_chaser = spacecraft_state(
            config.scenario.chaser, config.scenario.mu_km3_s2
        )
        self.nominal_target = spacecraft_state(
            config.scenario.target, config.scenario.mu_km3_s2
        )

    def evaluate(self, plan: CandidatePlan) -> tuple[RobustMetrics, list[SimulationMetrics]]:
        results: list[SimulationMetrics] = []
        scores: list[float] = []
        for perturbation in self.perturbations:
            perturbed = perturb_plan(plan, perturbation)
            scenario = self.config.scenario
            mu = scenario.mu_km3_s2 * (1.0 + perturbation.mu_relative_error)
            try:
                result = simulate_candidate(
                    perturbed,
                    scenario,
                    self.config.propagation,
                    chaser_initial=self.nominal_chaser + perturbation.chaser_state_error,
                    target_initial=self.nominal_target + perturbation.target_state_error,
                    mu_override_km3_s2=mu,
                )
            except (ArithmeticError, FloatingPointError, OverflowError, RuntimeError, ValueError):
                # Failed numerical propagations are unsuccessful scenarios, not
                # mathematical infinities.  Keeping ``inf`` here later reaches
                # NumPy's quantile interpolation (inf - inf) and contaminates
                # NSGA-II with NaNs.  A large finite distance is conservative:
                # it strongly penalizes the candidate while preserving sorting.
                failed_distance_km = 1_000_000.0
                result = SimulationMetrics(
                    success=False,
                    first_entry_time_s=None,
                    minimum_distance_km=failed_distance_km,
                    minimum_distance_time_s=0.0,
                    distance_at_horizon_km=failed_distance_km,
                    total_dv_km_s=perturbed.total_dv_km_s(),
                    executed_burns=len(perturbed.burns),
                    horizon_s=perturbed.evaluation_horizon_s,
                )
            results.append(result)
            if self.config.score is not None and np.isfinite(result.distance_at_horizon_km):
                scores.append(
                    competition_score(result, perturbed, scenario, self.config.score)
                )

        if not results:
            metrics = RobustMetrics(
                scenario_count=0,
                success_rate=1.0,
                worst_minimum_distance_km=0.0,
                p95_minimum_distance_km=0.0,
                p95_entry_time_s=None,
            )
            return metrics, results
        # A final guard also covers any non-finite metric returned by a lower
        # level numerical routine without raising an exception.
        minimum_distances = np.asarray([item.minimum_distance_km for item in results])
        minimum_distances = np.nan_to_num(
            minimum_distances, nan=1_000_000.0, posinf=1_000_000.0, neginf=0.0
        )
        entry_times = np.asarray(
            [
                item.first_entry_time_s
                if item.first_entry_time_s is not None
                else self.config.scenario.tmax_s()
                for item in results
            ]
        )
        metrics = RobustMetrics(
            scenario_count=len(results),
            success_rate=sum(item.success for item in results) / len(results),
            worst_minimum_distance_km=float(np.max(minimum_distances)),
            p95_minimum_distance_km=float(np.quantile(minimum_distances, 0.95)),
            p95_entry_time_s=float(np.quantile(entry_times, 0.95)),
            p05_score=float(np.quantile(scores, 0.05)) if scores else None,
        )
        return metrics, results
