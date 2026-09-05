from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from pymoo.algorithms.moo.nsga2 import NSGA2
from pymoo.core.problem import ElementwiseProblem
from pymoo.optimize import minimize
from scipy.stats import qmc

from ..decision import DecisionCodec
from ..models import ProjectConfig
from ..propagation import simulate_candidate
from ..screening import simulate_candidate_coarse


class InterceptionProblem(ElementwiseProblem):
    def __init__(self, config: ProjectConfig, n_burns: int):
        self.config = config
        self.codec = DecisionCodec(n_burns, config.scenario)
        score_enabled = (
            config.score is not None
            and config.optimization.official_score_as_objective
        )
        n_obj = 3 if score_enabled else 2
        super().__init__(
            n_var=self.codec.n_var,
            n_obj=n_obj,
            n_ieq_constr=1,
            xl=self.codec.lower,
            xu=self.codec.upper,
        )

    def _evaluate(self, x: np.ndarray, out: dict[str, Any], *args: Any, **kwargs: Any) -> None:
        plan = self.codec.decode(x)
        try:
            if self.config.screening.enabled:
                coarse = simulate_candidate_coarse(plan, self.config)
                t_value = (
                    coarse.first_entry_time_s
                    if coarse.first_entry_time_s is not None
                    else self.config.scenario.tmax_s()
                )
                total_dv = coarse.total_dv_km_s
                minimum_distance = coarse.sampled_minimum_distance_km
                approximate_score = coarse.approximate_score
            else:
                nominal = simulate_candidate(
                    plan,
                    self.config.scenario,
                    self.config.propagation,
                )
                t_value = (
                    nominal.first_entry_time_s
                    if nominal.first_entry_time_s is not None
                    else plan.evaluation_horizon_s
                )
                total_dv = nominal.total_dv_km_s
                minimum_distance = nominal.minimum_distance_km
                approximate_score = None
                if self.config.score is not None:
                    from ..scoring import competition_score

                    approximate_score = competition_score(
                        nominal,
                        plan,
                        self.config.scenario,
                        self.config.score,
                    )
        except (ArithmeticError, FloatingPointError, OverflowError, RuntimeError, ValueError):
            out["F"] = np.full(self.n_obj, 1e6)
            out["G"] = np.full(self.n_ieq_constr, 1e6)
            return
        time_and_dv = [
            float(t_value / self.config.scenario.tmax_s()),
            float(
                total_dv
                / (len(plan.burns) * self.config.scenario.max_burn_dv_km_s)
            ),
        ]
        objectives: list[float]
        if self.n_obj == 3:
            score = 0.0 if approximate_score is None else approximate_score
            objectives = [-float(score) / 100.0, *time_and_dv]
        else:
            objectives = time_and_dv
        constraints = [
            minimum_distance - self.config.scenario.intercept_radius_km
        ]
        # Never pass NaN/inf into pymoo selection or crowding distance.
        # Invalid candidates must simply be dominated by finite feasible ones.
        out["F"] = np.nan_to_num(
            np.asarray(objectives), nan=1e6, posinf=1e6, neginf=0.0
        )
        out["G"] = np.nan_to_num(
            np.asarray(constraints), nan=1e6, posinf=1e6, neginf=1e6
        )


@dataclass
class IslandResult:
    n_burns: int
    seed: int
    population_x: np.ndarray
    population_f: np.ndarray
    population_g: np.ndarray
    front_x: np.ndarray
    front_f: np.ndarray
    front_g: np.ndarray


def _exploration_sampling(
    config: ProjectConfig,
    problem: InterceptionProblem,
    seed: int,
) -> np.ndarray:
    population_size = config.optimization.population_size
    if config.physical_seeds.exploration == "sobol":
        exponent = int(np.ceil(np.log2(population_size)))
        unit = qmc.Sobol(
            d=problem.n_var,
            scramble=True,
            seed=seed,
        ).random_base2(exponent)[:population_size]
        return qmc.scale(unit, problem.xl, problem.xu)
    return np.random.default_rng(seed).uniform(
        problem.xl,
        problem.xu,
        size=(population_size, problem.n_var),
    )


def run_nsga2_island(
    config_payload: dict[str, Any],
    n_burns: int,
    seed: int,
    generations: int,
    sampling: np.ndarray | None = None,
) -> IslandResult:
    config = ProjectConfig.model_validate(config_payload)
    problem = InterceptionProblem(config, n_burns)
    algorithm = NSGA2(
        pop_size=config.optimization.population_size,
        sampling=(
            sampling
            if sampling is not None
            else _exploration_sampling(config, problem, seed)
        ),
        eliminate_duplicates=True,
    )
    result = minimize(
        problem,
        algorithm,
        termination=("n_gen", generations),
        seed=seed,
        verbose=False,
        save_history=False,
        copy_algorithm=False,
    )
    population = result.pop
    pop_x = np.asarray(population.get("X"), dtype=float)
    pop_f = np.asarray(population.get("F"), dtype=float)
    pop_g = np.asarray(population.get("G"), dtype=float)
    if result.X is None:
        violation = np.maximum(pop_g, 0.0).sum(axis=1)
        best = np.argsort(violation)[: min(8, len(pop_x))]
        front_x, front_f, front_g = pop_x[best], pop_f[best], pop_g[best]
    else:
        front_x = np.atleast_2d(np.asarray(result.X, dtype=float))
        front_f = np.atleast_2d(np.asarray(result.F, dtype=float))
        front_g = np.atleast_2d(np.asarray(result.G, dtype=float))
    return IslandResult(
        n_burns=n_burns,
        seed=seed,
        population_x=pop_x,
        population_f=pop_f,
        population_g=pop_g,
        front_x=front_x,
        front_f=front_f,
        front_g=front_g,
    )
