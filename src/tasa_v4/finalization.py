from __future__ import annotations

from .models import CandidatePlan, ProjectConfig, SimulationMetrics
from .propagation import simulate_candidate


SUCCESS_HORIZON_BUFFER_S = 10.0


def compact_successful_plan(
    config: ProjectConfig,
    plan: CandidatePlan,
    nominal: SimulationMetrics | None = None,
) -> tuple[CandidatePlan, SimulationMetrics]:
    """Return the scored maneuver sequence with a short post-entry coast."""

    metrics = nominal or simulate_candidate(
        plan, config.scenario, config.propagation
    )
    if not metrics.success or metrics.first_entry_time_s is None:
        return plan, metrics

    first_entry = float(metrics.first_entry_time_s)
    retained_burns = [
        burn for burn in plan.burns if burn.time_s <= first_entry + 1e-9
    ]
    compact_horizon = min(
        config.scenario.tmax_s(),
        first_entry + SUCCESS_HORIZON_BUFFER_S,
    )
    metadata = dict(plan.metadata)
    metadata.setdefault(
        "original_evaluation_horizon_s",
        float(plan.evaluation_horizon_s),
    )
    metadata.update(
        {
            "first_entry_time_s": first_entry,
            "success_horizon_buffer_s": SUCCESS_HORIZON_BUFFER_S,
            "horizon_compacted": True,
        }
    )
    compacted = plan.model_copy(
        update={
            "burns": retained_burns,
            "evaluation_horizon_s": compact_horizon,
            "metadata": metadata,
        }
    )
    if compacted == plan:
        return plan, metrics
    return compacted, simulate_candidate(
        compacted, config.scenario, config.propagation
    )
