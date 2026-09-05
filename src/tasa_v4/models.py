from __future__ import annotations

import math
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


Vector3 = tuple[float, float, float]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class KeplerianElements(StrictModel):
    sma_km: float = Field(gt=0)
    ecc: float = Field(ge=0, lt=1)
    inc_deg: float
    raan_deg: float
    aop_deg: float
    ta_deg: float


class CartesianState(StrictModel):
    position_km: Vector3
    velocity_km_s: Vector3


class SpacecraftConfig(StrictModel):
    name: str
    keplerian: KeplerianElements | None = None
    cartesian: CartesianState | None = None
    dry_mass_kg: float = Field(default=850.0, gt=0)

    @model_validator(mode="after")
    def exactly_one_state(self) -> "SpacecraftConfig":
        if (self.keplerian is None) == (self.cartesian is None):
            raise ValueError("exactly one of keplerian or cartesian must be set")
        return self


class ScenarioConfig(StrictModel):
    epoch_utc: str
    mu_km3_s2: float = Field(default=398600.4415, gt=0)
    chaser: SpacecraftConfig
    target: SpacecraftConfig
    intercept_radius_km: float = Field(default=5.0, gt=0)
    max_burn_dv_km_s: float = Field(default=1.5, gt=0)
    min_burn_spacing_s: float = Field(default=100.0, ge=0)
    tmax_override_s: float | None = Field(default=None, gt=0)

    def target_period_s(self) -> float:
        if self.target.keplerian is None:
            r = self.target.cartesian.position_km
            v = self.target.cartesian.velocity_km_s
            radius = math.sqrt(sum(x * x for x in r))
            speed2 = sum(x * x for x in v)
            specific_energy = 0.5 * speed2 - self.mu_km3_s2 / radius
            if specific_energy >= 0:
                raise ValueError("target state is not a bound orbit; period is undefined")
            sma_km = -self.mu_km3_s2 / (2.0 * specific_energy)
        else:
            sma_km = self.target.keplerian.sma_km
        return 2.0 * math.pi * math.sqrt(sma_km**3 / self.mu_km3_s2)

    def tmax_s(self) -> float:
        return self.tmax_override_s or 4.0 * self.target_period_s()


class PropagationConfig(StrictModel):
    event_scan_step_s: float = Field(default=20.0, gt=0)
    root_tolerance_s: float = Field(default=1e-7, gt=0)
    universal_tolerance: float = Field(default=1e-11, gt=0)
    universal_max_iterations: int = Field(default=50, ge=5)


class ScreeningConfig(StrictModel):
    """Cheap nominal propagation used before the exact event solver."""

    enabled: bool = True
    step_s: float = Field(default=60.0, gt=0)
    early_stop_enabled: bool = True
    early_stop_after_fraction: float = Field(default=0.35, gt=0, le=1)
    early_stop_distance_km: float = Field(default=3000.0, gt=0)
    increasing_window: int = Field(default=5, ge=3)
    physical_seed_keep_fraction: float = Field(default=0.10, gt=0, le=1)
    physical_seed_minimum_keep: int = Field(default=12, ge=1)


class PhysicalSeedConfig(StrictModel):
    enabled: bool = True
    lambert_enabled: bool = True
    lambert_tof_fractions: list[float] = Field(
        default_factory=lambda: [
            0.10,
            0.16,
            0.25,
            0.35,
            0.50,
            0.70,
            1.00,
            1.35,
            1.75,
            2.25,
            3.00,
            4.00,
        ]
    )
    lambert_revolutions: list[int] = Field(default_factory=lambda: [0, 1, 2, 3])
    lambert_include_retrograde: bool = False
    lambert_max_solutions: int = Field(default=48, ge=1)
    phasing_enabled: bool = True
    phasing_closure_periods: list[float] = Field(
        default_factory=lambda: [0.5, 1.0, 1.5, 2.0, 3.0]
    )
    hohmann_enabled: bool = True
    node_plane_change_enabled: bool = True
    high_apogee_enabled: bool = True
    high_apogee_raise_burns_km_s: list[float] = Field(
        default_factory=lambda: [0.15, 0.30, 0.50, 0.75]
    )
    population_fraction: float = Field(default=0.70, ge=0, le=1)
    jitter_per_seed: int = Field(default=3, ge=0)
    exploration: Literal["sobol", "uniform"] = "sobol"

    @model_validator(mode="after")
    def valid_seed_grids(self) -> "PhysicalSeedConfig":
        if any(value <= 0 for value in self.lambert_tof_fractions):
            raise ValueError("lambert_tof_fractions must be positive")
        if any(value < 0 for value in self.lambert_revolutions):
            raise ValueError("lambert_revolutions must be non-negative")
        if any(value <= 0 for value in self.phasing_closure_periods):
            raise ValueError("phasing_closure_periods must be positive")
        if any(value <= 0 for value in self.high_apogee_raise_burns_km_s):
            raise ValueError("high_apogee_raise_burns_km_s must be positive")
        return self


class CheckpointConfig(StrictModel):
    enabled: bool = True
    resume: bool = True
    directory_name: str = "checkpoints"


class RobustnessConfig(StrictModel):
    enabled: bool = True
    distribution: Literal["normal", "uniform"] = "normal"
    screening_scenarios: int = Field(default=0, ge=0)
    certification_scenarios: int = Field(default=128, ge=0)
    required_success_rate: float = Field(default=0.95, ge=0, le=1)
    position_sigma_km: float = Field(default=0.02, ge=0)
    velocity_sigma_km_s: float = Field(default=2e-5, ge=0)
    burn_timing_sigma_s: float = Field(default=0.02, ge=0)
    burn_relative_sigma: float = Field(default=2e-4, ge=0)
    burn_component_sigma_km_s: float = Field(default=2e-5, ge=0)
    mu_relative_sigma: float = Field(default=1e-8, ge=0)
    sample_seed: int = 20260814
    safety_margin_km: float = Field(default=0.25, ge=0)


class OptimizationConfig(StrictModel):
    burn_counts: list[int] = Field(default_factory=lambda: [1, 2, 3])
    population_size: int = Field(default=160, ge=8)
    generations_per_epoch: int = Field(default=60, ge=1)
    island_epochs: int = Field(default=4, ge=1)
    seeds: list[int] = Field(default_factory=lambda: [11, 29, 47, 83])
    workers: int = Field(default=4, ge=1)
    migration_fraction: float = Field(default=0.15, gt=0, le=0.5)
    robust_as_objective: bool = False
    max_front_size: int = Field(default=250, ge=1)
    official_score_as_objective: bool = True
    final_robustness_candidates: int = Field(default=20, ge=1)

    @model_validator(mode="after")
    def valid_burn_counts(self) -> "OptimizationConfig":
        if not self.burn_counts or any(n < 1 for n in self.burn_counts):
            raise ValueError("burn_counts must contain positive integers")
        if not self.seeds:
            raise ValueError("at least one seed is required")
        return self


class MultipleShootingConfig(StrictModel):
    enabled: bool = True
    mesh_intervals_per_coast: int = Field(default=12, ge=2)
    rk4_substeps_per_interval: int = Field(default=8, ge=1)
    robust_scenarios: int = Field(default=0, ge=0)
    max_iterations: int = Field(default=1200, ge=10)
    tolerance: float = Field(default=1e-9, gt=0)
    time_weight: float = Field(default=0.5, ge=0, le=1)
    refine_front_candidates: int = Field(default=12, ge=0)


class GmatConfig(StrictModel):
    executable: str | None = None
    startup_file: str | None = None
    timeout_s: float = Field(default=600.0, gt=0)
    initial_step_s: float = Field(default=0.25, gt=0)
    min_step_s: float = Field(default=1e-4, gt=0)
    max_step_s: float = Field(default=0.25, gt=0)
    accuracy: float = Field(default=1e-12, gt=0)
    post_entry_buffer_s: float = Field(default=30.0, ge=0)
    max_time_difference_s: float = Field(default=0.5, gt=0)
    max_position_difference_km: float = Field(default=0.25, gt=0)


class ScoreConfig(StrictModel):
    kt: float
    ct_s: float
    kv: float
    cv_km_s: float


class ProjectConfig(StrictModel):
    scenario: ScenarioConfig
    propagation: PropagationConfig = Field(default_factory=PropagationConfig)
    screening: ScreeningConfig = Field(default_factory=ScreeningConfig)
    physical_seeds: PhysicalSeedConfig = Field(default_factory=PhysicalSeedConfig)
    checkpoint: CheckpointConfig = Field(default_factory=CheckpointConfig)
    robustness: RobustnessConfig = Field(default_factory=RobustnessConfig)
    optimization: OptimizationConfig = Field(default_factory=OptimizationConfig)
    multiple_shooting: MultipleShootingConfig = Field(default_factory=MultipleShootingConfig)
    gmat: GmatConfig = Field(default_factory=GmatConfig)
    score: ScoreConfig | None = None


class Burn(StrictModel):
    time_s: float = Field(ge=0)
    dv_vnb_km_s: Vector3

    def magnitude_km_s(self) -> float:
        return math.sqrt(sum(value * value for value in self.dv_vnb_km_s))


class CandidatePlan(StrictModel):
    name: str = "candidate"
    burns: list[Burn]
    evaluation_horizon_s: float = Field(gt=0)
    source: str = "manual"
    metadata: dict[str, str | int | float | bool | None] = Field(default_factory=dict)

    @model_validator(mode="after")
    def ordered_burns(self) -> "CandidatePlan":
        times = [burn.time_s for burn in self.burns]
        if times != sorted(times):
            raise ValueError("burn times must be sorted")
        if times and times[-1] > self.evaluation_horizon_s:
            raise ValueError("evaluation_horizon_s must not precede the last burn")
        return self

    def total_dv_km_s(self, until_s: float | None = None) -> float:
        return sum(
            burn.magnitude_km_s()
            for burn in self.burns
            if until_s is None or burn.time_s <= until_s + 1e-9
        )

    def violations(self, scenario: ScenarioConfig) -> list[str]:
        problems: list[str] = []
        for index, burn in enumerate(self.burns, start=1):
            if burn.magnitude_km_s() > scenario.max_burn_dv_km_s + 1e-12:
                problems.append(f"burn {index} exceeds the per-burn delta-v limit")
        for first, second in zip(self.burns, self.burns[1:]):
            if second.time_s - first.time_s < scenario.min_burn_spacing_s - 1e-9:
                problems.append("adjacent burns violate the minimum spacing")
        if self.evaluation_horizon_s > scenario.tmax_s() + 1e-9:
            problems.append("evaluation horizon exceeds Tmax")
        return problems


class SimulationMetrics(StrictModel):
    success: bool
    first_entry_time_s: float | None
    minimum_distance_km: float
    minimum_distance_time_s: float
    distance_at_horizon_km: float
    total_dv_km_s: float
    executed_burns: int
    horizon_s: float


class RobustMetrics(StrictModel):
    scenario_count: int
    success_rate: float
    worst_minimum_distance_km: float
    p95_minimum_distance_km: float
    p95_entry_time_s: float | None
    p05_score: float | None = None


class GmatVerification(StrictModel):
    status: Literal["passed", "failed", "not_run"]
    python_first_entry_time_s: float | None = None
    gmat_first_entry_time_s: float | None = None
    time_difference_s: float | None = None
    gmat_minimum_distance_km: float | None = None
    diagnostics: list[str] = Field(default_factory=list)
    run_directory: Path | None = None
