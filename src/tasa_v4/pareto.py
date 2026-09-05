from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .models import CandidatePlan, RobustMetrics, SimulationMetrics


@dataclass
class EvaluatedCandidate:
    plan: CandidatePlan
    nominal: SimulationMetrics
    robust: RobustMetrics | None
    objectives: tuple[float, ...]
    constraint_violation: float
    score: float | None = None

    @property
    def feasible(self) -> bool:
        return self.constraint_violation <= 1e-12


def dominates(left: EvaluatedCandidate, right: EvaluatedCandidate) -> bool:
    if left.feasible and not right.feasible:
        return True
    if not left.feasible:
        if right.feasible:
            return False
        return left.constraint_violation < right.constraint_violation
    return all(a <= b for a, b in zip(left.objectives, right.objectives)) and any(
        a < b for a, b in zip(left.objectives, right.objectives)
    )


def nondominated(candidates: list[EvaluatedCandidate]) -> list[EvaluatedCandidate]:
    unique: dict[tuple[float, ...], EvaluatedCandidate] = {}
    for item in candidates:
        key = tuple(
            round(value, 10)
            for burn in item.plan.burns
            for value in (burn.time_s, *burn.dv_vnb_km_s)
        )
        key += (round(item.plan.evaluation_horizon_s, 10),)
        existing = unique.get(key)
        if existing is None or item.constraint_violation < existing.constraint_violation:
            unique[key] = item
    values = list(unique.values())
    front = [
        candidate
        for index, candidate in enumerate(values)
        if not any(
            dominates(other, candidate)
            for other_index, other in enumerate(values)
            if other_index != index
        )
    ]
    return sorted(front, key=lambda item: item.objectives)


def crowding_subset(
    candidates: list[EvaluatedCandidate], maximum: int
) -> list[EvaluatedCandidate]:
    if len(candidates) <= maximum:
        return candidates
    objectives = np.asarray([item.objectives for item in candidates], dtype=float)
    count, dimensions = objectives.shape
    crowding = np.zeros(count)
    for column in range(dimensions):
        order = np.argsort(objectives[:, column])
        crowding[order[0]] = np.inf
        crowding[order[-1]] = np.inf
        span = objectives[order[-1], column] - objectives[order[0], column]
        if span <= 1e-15:
            continue
        for position in range(1, count - 1):
            crowding[order[position]] += (
                objectives[order[position + 1], column]
                - objectives[order[position - 1], column]
            ) / span
    selected = np.argsort(-crowding)[:maximum]
    return [candidates[index] for index in selected]


def rank_candidates(
    candidates: list[EvaluatedCandidate],
    *,
    official_score_enabled: bool,
) -> list[EvaluatedCandidate]:
    """Official score, then the rulebook tie-break order."""

    if not official_score_enabled:
        return sorted(candidates, key=lambda item: item.objectives)

    def key(item: EvaluatedCandidate) -> tuple[float, ...]:
        score = item.score if item.score is not None else -float("inf")
        team_time = (
            item.nominal.first_entry_time_s
            if item.nominal.first_entry_time_s is not None
            else item.nominal.horizon_s
        )
        return (
            0.0 if item.feasible else 1.0,
            -float(score),
            item.nominal.minimum_distance_km,
            item.nominal.total_dv_km_s,
            float(team_time),
        )

    return sorted(candidates, key=key)
