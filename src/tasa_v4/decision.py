from __future__ import annotations

import math

import numpy as np

from .models import Burn, CandidatePlan, ScenarioConfig


class DecisionCodec:
    """Feasibility-preserving genotype for a fixed number of burns.

    The schedule is represented by an intercept horizon plus non-negative coast
    allocations. Mandatory 100 s inter-burn gaps are added by construction.
    Each burn is represented by magnitude, direction cosine, and azimuth, so
    its norm can never exceed the configured limit.
    """

    def __init__(self, n_burns: int, scenario: ScenarioConfig):
        if n_burns < 1:
            raise ValueError("n_burns must be positive")
        self.n_burns = n_burns
        self.scenario = scenario
        self.n_split = n_burns + 1
        self.n_var = 1 + self.n_split + 3 * n_burns
        self.lower = np.concatenate(
            (
                np.array([0.0]),
                np.zeros(self.n_split),
                np.tile(np.array([0.0, -1.0, -math.pi]), n_burns),
            )
        )
        self.upper = np.concatenate(
            (
                np.array([1.0]),
                np.ones(self.n_split),
                np.tile(
                    np.array([scenario.max_burn_dv_km_s, 1.0, math.pi]),
                    n_burns,
                ),
            )
        )

    @property
    def minimum_horizon_s(self) -> float:
        """Smallest valid horizon, including the one-burn zero-boundary case."""
        return max(self.mandatory_horizon_s, 1.0e-6)

    @property
    def mandatory_horizon_s(self) -> float:
        return self.scenario.min_burn_spacing_s * (self.n_burns - 1)

    def decode(self, vector: np.ndarray, *, name: str = "nsga2") -> CandidatePlan:
        x = np.asarray(vector, dtype=float)
        if x.shape != (self.n_var,):
            raise ValueError(f"expected decision vector of length {self.n_var}")
        minimum = self.minimum_horizon_s
        horizon = minimum + x[0] * (self.scenario.tmax_s() - minimum)
        raw_weights = np.maximum(x[1 : 1 + self.n_split], 0.0) + 1e-12
        weights = raw_weights / raw_weights.sum()
        free_time = max(0.0, horizon - self.mandatory_horizon_s)
        free_gaps = free_time * weights
        coast_gaps = free_gaps.copy()
        if self.n_burns > 1:
            coast_gaps[1 : self.n_burns] += self.scenario.min_burn_spacing_s

        burns: list[Burn] = []
        current_time = float(coast_gaps[0])
        offset = 1 + self.n_split
        for index in range(self.n_burns):
            magnitude, z_axis, azimuth = x[offset + 3 * index : offset + 3 * index + 3]
            radial = math.sqrt(max(0.0, 1.0 - z_axis * z_axis))
            direction = np.array(
                [radial * math.cos(azimuth), radial * math.sin(azimuth), z_axis]
            )
            burns.append(
                Burn(
                    time_s=current_time,
                    dv_vnb_km_s=tuple(float(value) for value in magnitude * direction),
                )
            )
            if index + 1 < self.n_burns:
                current_time += float(coast_gaps[index + 1])
        return CandidatePlan(
            name=name,
            burns=burns,
            evaluation_horizon_s=float(horizon),
            source="nsga2",
        )

    def encode(self, plan: CandidatePlan) -> np.ndarray:
        if len(plan.burns) != self.n_burns:
            raise ValueError("candidate burn count does not match codec")
        minimum = self.minimum_horizon_s
        horizon_fraction = (plan.evaluation_horizon_s - minimum) / (
            self.scenario.tmax_s() - minimum
        )
        times = [burn.time_s for burn in plan.burns]
        coast_gaps = [times[0]]
        coast_gaps.extend(
            times[index] - times[index - 1] - self.scenario.min_burn_spacing_s
            for index in range(1, len(times))
        )
        coast_gaps.append(plan.evaluation_horizon_s - times[-1])
        free_total = max(
            plan.evaluation_horizon_s - self.mandatory_horizon_s, 1e-12
        )
        weights = np.maximum(np.asarray(coast_gaps) / free_total, 0.0)
        if weights.sum() <= 0:
            weights = np.ones(self.n_split) / self.n_split
        else:
            weights /= weights.sum()
        values: list[float] = [float(np.clip(horizon_fraction, 0.0, 1.0)), *weights]
        for burn in plan.burns:
            dv = np.asarray(burn.dv_vnb_km_s, dtype=float)
            magnitude = float(np.linalg.norm(dv))
            if magnitude <= 1e-15:
                z_axis, azimuth = 0.0, 0.0
            else:
                direction = dv / magnitude
                z_axis = float(np.clip(direction[2], -1.0, 1.0))
                azimuth = math.atan2(float(direction[1]), float(direction[0]))
            values.extend((magnitude, z_axis, azimuth))
        return np.clip(np.asarray(values), self.lower, self.upper)
