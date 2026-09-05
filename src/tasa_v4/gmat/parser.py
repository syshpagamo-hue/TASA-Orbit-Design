from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy.optimize import brentq, minimize_scalar

from .generator import TRAJECTORY_COLUMNS


@dataclass
class ParsedTrajectory:
    elapsed_s: np.ndarray
    chaser_position_km: np.ndarray
    chaser_velocity_km_s: np.ndarray
    target_position_km: np.ndarray
    target_velocity_km_s: np.ndarray
    duplicate_header_count: int


@dataclass
class GmatEncounter:
    first_entry_time_s: float | None
    minimum_distance_km: float
    minimum_distance_time_s: float
    rows: int
    diagnostics: list[str]


def parse_trajectory_report(path: str | Path) -> ParsedTrajectory:
    source = Path(path)
    rows: list[list[float]] = []
    header_count = 0
    with source.open("r", encoding="utf-8-sig", errors="replace", newline="") as stream:
        for raw in csv.reader(stream):
            cells = [cell.strip() for cell in raw if cell.strip()]
            if not cells:
                continue
            if cells[0] == TRAJECTORY_COLUMNS[0] or "ElapsedSecs" in cells[0]:
                header_count += 1
                continue
            if len(cells) != len(TRAJECTORY_COLUMNS):
                # Older fixed-width/space-delimited reports remain readable.
                cells = " ".join(raw).split()
            if len(cells) != len(TRAJECTORY_COLUMNS):
                continue
            try:
                rows.append([float(value) for value in cells])
            except ValueError:
                continue
    if not rows:
        raise ValueError(f"no numeric GMAT trajectory rows found in {source}")
    data = np.asarray(rows, dtype=float)
    if np.any(np.diff(data[:, 0]) < -1e-9):
        raise ValueError("GMAT elapsed time is not monotonic; output may contain appended runs")
    return ParsedTrajectory(
        elapsed_s=data[:, 0],
        chaser_position_km=data[:, 1:4],
        chaser_velocity_km_s=data[:, 4:7],
        target_position_km=data[:, 7:10],
        target_velocity_km_s=data[:, 10:13],
        duplicate_header_count=max(0, header_count - 1),
    )


def _hermite_relative_position(data: ParsedTrajectory, index: int, fraction: float) -> np.ndarray:
    dt = data.elapsed_s[index + 1] - data.elapsed_s[index]
    p0 = data.chaser_position_km[index] - data.target_position_km[index]
    p1 = data.chaser_position_km[index + 1] - data.target_position_km[index + 1]
    v0 = data.chaser_velocity_km_s[index] - data.target_velocity_km_s[index]
    v1 = data.chaser_velocity_km_s[index + 1] - data.target_velocity_km_s[index + 1]
    u = float(fraction)
    h00 = 2 * u**3 - 3 * u**2 + 1
    h10 = u**3 - 2 * u**2 + u
    h01 = -2 * u**3 + 3 * u**2
    h11 = u**3 - u**2
    return h00 * p0 + h10 * dt * v0 + h01 * p1 + h11 * dt * v1


def analyze_encounter(
    data: ParsedTrajectory,
    intercept_radius_km: float = 5.0,
) -> GmatEncounter:
    diagnostics: list[str] = []
    if data.duplicate_header_count:
        diagnostics.append(
            f"report contains {data.duplicate_header_count} duplicate header block(s)"
        )
    relative = data.chaser_position_km - data.target_position_km
    sampled_distances = np.linalg.norm(relative, axis=1)
    minimum_distance = float(sampled_distances[0])
    minimum_time = float(data.elapsed_s[0])
    first_entry: float | None = (
        float(data.elapsed_s[0]) if sampled_distances[0] <= intercept_radius_km else None
    )

    for index in range(len(data.elapsed_s) - 1):
        t0, t1 = float(data.elapsed_s[index]), float(data.elapsed_s[index + 1])
        dt = t1 - t0
        if dt <= 1e-12:
            continue

        # A conservative displacement bound for the cubic-Hermite segment.
        # It lets long, dense official Reports skip intervals that provably
        # cannot improve the current minimum or cross the intercept radius.
        p0 = relative[index]
        p1 = relative[index + 1]
        v0 = data.chaser_velocity_km_s[index] - data.target_velocity_km_s[index]
        v1 = (
            data.chaser_velocity_km_s[index + 1]
            - data.target_velocity_km_s[index + 1]
        )
        displacement_bound = float(
            np.linalg.norm(p1 - p0)
            + (4.0 / 27.0) * dt * (np.linalg.norm(v0) + np.linalg.norm(v1))
        )
        lower_bound = float(sampled_distances[index]) - displacement_bound
        could_improve_minimum = lower_bound <= minimum_distance
        could_enter = first_entry is None and lower_bound <= intercept_radius_km
        if not could_improve_minimum and not could_enter:
            continue

        def distance_at(fraction: float) -> float:
            return float(np.linalg.norm(_hermite_relative_position(data, index, fraction)))

        optimum = minimize_scalar(
            distance_at,
            bounds=(0.0, 1.0),
            method="bounded",
            options={"xatol": 1e-12},
        )
        local_fraction = float(optimum.x)
        local_distance = float(optimum.fun)
        if local_distance < minimum_distance:
            minimum_distance = local_distance
            minimum_time = t0 + local_fraction * dt
        if first_entry is not None:
            continue
        d0 = float(sampled_distances[index])
        d1 = float(sampled_distances[index + 1])
        if d0 <= intercept_radius_km:
            first_entry = t0
        elif d1 <= intercept_radius_km:
            root = brentq(
                lambda fraction: distance_at(fraction) - intercept_radius_km,
                0.0,
                1.0,
                xtol=1e-12,
            )
            first_entry = t0 + float(root) * dt
        elif local_distance <= intercept_radius_km and local_fraction > 0.0:
            root = brentq(
                lambda fraction: distance_at(fraction) - intercept_radius_km,
                0.0,
                local_fraction,
                xtol=1e-12,
            )
            first_entry = t0 + float(root) * dt

    return GmatEncounter(
        first_entry_time_s=first_entry,
        minimum_distance_km=minimum_distance,
        minimum_distance_time_s=minimum_time,
        rows=len(data.elapsed_s),
        diagnostics=diagnostics,
    )
