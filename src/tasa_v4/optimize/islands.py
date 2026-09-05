from __future__ import annotations

import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from pymoo.util.nds.non_dominated_sorting import NonDominatedSorting

from ..decision import DecisionCodec
from ..models import CandidatePlan, ProjectConfig
from ..progress import OptimizerCheckpoint, ProgressRecorder, config_fingerprint
from .nsga2 import IslandResult, run_nsga2_island


@dataclass
class IslandSearchResult:
    config: ProjectConfig
    island_results: list[IslandResult]

    def front_vectors(self) -> list[tuple[int, np.ndarray]]:
        return [
            (result.n_burns, vector.copy())
            for result in self.island_results
            for vector in result.front_x
        ]


def _migration_pool(
    results: list[IslandResult],
    n_burns: int,
    count: int,
) -> np.ndarray:
    relevant = [result for result in results if result.n_burns == n_burns]
    x = np.vstack([result.population_x for result in relevant])
    f = np.vstack([result.population_f for result in relevant])
    g = np.vstack([result.population_g for result in relevant])
    feasible = np.all(g <= 0.0, axis=1)
    if feasible.any():
        x_feasible, f_feasible = x[feasible], f[feasible]
        indices = NonDominatedSorting().do(f_feasible, only_non_dominated_front=True)
        selected = x_feasible[indices]
    else:
        violation = np.maximum(g, 0.0).sum(axis=1)
        selected = x[np.argsort(violation)]
    if len(selected) <= count:
        return selected
    positions = np.linspace(0, len(selected) - 1, count, dtype=int)
    return selected[positions]


def _next_sampling(
    result: IslandResult,
    migrants: np.ndarray,
    codec: DecisionCodec,
    population_size: int,
    seed: int,
) -> np.ndarray:
    rng = np.random.default_rng(seed)
    own = result.population_x
    rows: list[np.ndarray] = [row.copy() for row in migrants]
    own_order = rng.permutation(len(own))
    rows.extend(own[index].copy() for index in own_order)
    unique: list[np.ndarray] = []
    seen: set[bytes] = set()
    for row in rows:
        key = np.round(row, 12).tobytes()
        if key not in seen:
            seen.add(key)
            unique.append(row)
        if len(unique) == population_size:
            break
    while len(unique) < population_size:
        unique.append(rng.uniform(codec.lower, codec.upper))
    sampling = np.vstack(unique)
    jitter_count = min(len(migrants), population_size // 4)
    if jitter_count:
        scales = 0.015 * (codec.upper - codec.lower)
        jittered = migrants[:jitter_count] + rng.normal(size=(jitter_count, codec.n_var)) * scales
        sampling[-jitter_count:] = np.clip(jittered, codec.lower, codec.upper)
    return sampling


def _initial_sampling(
    config: ProjectConfig,
    n_burns: int,
    seed: int,
    seed_plans: list[CandidatePlan],
) -> np.ndarray | None:
    relevant = [plan for plan in seed_plans if len(plan.burns) == n_burns]
    if not relevant:
        return None
    codec = DecisionCodec(n_burns, config.scenario)
    rng = np.random.default_rng(seed)
    manual = [plan for plan in relevant if not plan.source.startswith("physical:")]
    physical = [plan for plan in relevant if plan.source.startswith("physical:")]
    ordered = [*manual, *physical]
    population_size = config.optimization.population_size
    seed_budget = min(
        population_size,
        max(
            len(manual),
            int(round(population_size * config.physical_seeds.population_fraction)),
        ),
    )
    rows = [codec.encode(plan) for plan in ordered[:seed_budget]]
    # Add small neighborhoods around physical/manual seeds while reserving the
    # remaining population for global Sobol/uniform exploration.
    scale = 0.01 * (codec.upper - codec.lower)
    exact_rows = list(rows)
    for base in exact_rows:
        for _ in range(config.physical_seeds.jitter_per_seed):
            if len(rows) >= seed_budget:
                break
            rows.append(
                np.clip(
                    base + rng.normal(size=codec.n_var) * scale,
                    codec.lower,
                    codec.upper,
                )
            )
    while len(rows) < population_size:
        rows.append(rng.uniform(codec.lower, codec.upper))
    return np.vstack(rows[:population_size])


def _checkpoint_payload(
    config: ProjectConfig,
    history: list[IslandResult],
    samplings: dict[tuple[int, int], np.ndarray | None],
    next_epoch: int,
) -> tuple[dict[str, Any], dict[str, np.ndarray]]:
    arrays: dict[str, np.ndarray] = {}
    records: list[dict[str, int]] = []
    for index, result in enumerate(history):
        prefix = f"history_{index:04d}"
        arrays[f"{prefix}_population_x"] = result.population_x
        arrays[f"{prefix}_population_f"] = result.population_f
        arrays[f"{prefix}_population_g"] = result.population_g
        arrays[f"{prefix}_front_x"] = result.front_x
        arrays[f"{prefix}_front_f"] = result.front_f
        arrays[f"{prefix}_front_g"] = result.front_g
        records.append(
            {
                "index": index,
                "n_burns": result.n_burns,
                "seed": result.seed,
            }
        )
    sampling_records: list[dict[str, int | str]] = []
    for index, ((burns, seed), sampling) in enumerate(sorted(samplings.items())):
        if sampling is None:
            continue
        key = f"sampling_{index:04d}"
        arrays[key] = sampling
        sampling_records.append(
            {"key": key, "n_burns": burns, "seed": seed}
        )
    metadata = {
        "config_fingerprint": config_fingerprint(config.model_dump(mode="json")),
        "next_epoch": int(next_epoch),
        "history": records,
        "samplings": sampling_records,
        "rng_policy": "deterministic island seed plus completed epoch",
    }
    return metadata, arrays


def _restore_checkpoint(
    config: ProjectConfig,
    checkpoint: OptimizerCheckpoint,
) -> tuple[int, list[IslandResult], dict[tuple[int, int], np.ndarray | None]] | None:
    loaded = checkpoint.load()
    if loaded is None:
        return None
    metadata, arrays = loaded
    expected = config_fingerprint(config.model_dump(mode="json"))
    if metadata.get("config_fingerprint") != expected:
        return None
    history: list[IslandResult] = []
    for record in metadata.get("history", []):
        prefix = f"history_{int(record['index']):04d}"
        history.append(
            IslandResult(
                n_burns=int(record["n_burns"]),
                seed=int(record["seed"]),
                population_x=arrays[f"{prefix}_population_x"],
                population_f=arrays[f"{prefix}_population_f"],
                population_g=arrays[f"{prefix}_population_g"],
                front_x=arrays[f"{prefix}_front_x"],
                front_f=arrays[f"{prefix}_front_f"],
                front_g=arrays[f"{prefix}_front_g"],
            )
        )
    samplings: dict[tuple[int, int], np.ndarray | None] = {
        (burns, seed): None
        for burns in config.optimization.burn_counts
        for seed in config.optimization.seeds
    }
    for record in metadata.get("samplings", []):
        samplings[(int(record["n_burns"]), int(record["seed"]))] = arrays[
            str(record["key"])
        ]
    return int(metadata.get("next_epoch", 0)), history, samplings


def run_island_search(
    config: ProjectConfig,
    seed_plans: list[CandidatePlan] | None = None,
    *,
    output_directory: str | Path | None = None,
    progress: ProgressRecorder | None = None,
) -> IslandSearchResult:
    workers = min(config.optimization.workers, os.cpu_count() or 1)
    payload = config.model_dump(mode="json")
    latest: dict[tuple[int, int], IslandResult] = {}
    history: list[IslandResult] = []
    seeds_to_use = seed_plans or []
    samplings: dict[tuple[int, int], np.ndarray | None] = {
        (burns, seed): _initial_sampling(
            config, burns, seed, seeds_to_use
        )
        for burns in config.optimization.burn_counts
        for seed in config.optimization.seeds
    }

    checkpoint: OptimizerCheckpoint | None = None
    start_epoch = 0
    if config.checkpoint.enabled and output_directory is not None:
        checkpoint = OptimizerCheckpoint(
            Path(output_directory)
            / config.checkpoint.directory_name
            / "nsga2"
        )
        restored = (
            _restore_checkpoint(config, checkpoint)
            if config.checkpoint.resume
            else None
        )
        if restored is not None:
            start_epoch, history, samplings = restored
            for result in history:
                original_seed = next(
                    (
                        seed
                        for seed in config.optimization.seeds
                        if result.seed >= seed
                        and (result.seed - seed) % 1000003 == 0
                    ),
                    config.optimization.seeds[0],
                )
                latest[(result.n_burns, original_seed)] = result
            if progress is not None:
                progress.emit(
                    "checkpoint_loaded",
                    stage="nsga2",
                    next_epoch=start_epoch,
                    message=f"已從 NSGA-II checkpoint 繼續：epoch {start_epoch}",
                )

    for epoch in range(start_epoch, config.optimization.island_epochs):
        jobs = [
            (burns, seed, samplings[(burns, seed)])
            for burns in config.optimization.burn_counts
            for seed in config.optimization.seeds
        ]
        epoch_results: list[IslandResult] = []
        completed_jobs = 0
        if workers == 1:
            for burns, seed, sampling in jobs:
                epoch_results.append(
                    run_nsga2_island(
                        payload,
                        burns,
                        seed + 1000003 * epoch,
                        config.optimization.generations_per_epoch,
                        sampling,
                    )
                )
                completed_jobs += 1
                if progress is not None:
                    progress.progress(
                        f"nsga2_epoch_{epoch + 1}", completed_jobs, len(jobs)
                    )
        else:
            with ProcessPoolExecutor(max_workers=workers) as executor:
                futures = {
                    executor.submit(
                        run_nsga2_island,
                        payload,
                        burns,
                        seed + 1000003 * epoch,
                        config.optimization.generations_per_epoch,
                        sampling,
                    ): (burns, seed)
                    for burns, seed, sampling in jobs
                }
                for future in as_completed(futures):
                    epoch_results.append(future.result())
                    completed_jobs += 1
                    if progress is not None:
                        progress.progress(
                            f"nsga2_epoch_{epoch + 1}", completed_jobs, len(jobs)
                        )

        for result in epoch_results:
            original_seed = config.optimization.seeds[
                min(
                    range(len(config.optimization.seeds)),
                    key=lambda index: abs(
                        config.optimization.seeds[index]
                        - (result.seed - 1000003 * epoch)
                    ),
                )
            ]
            latest[(result.n_burns, original_seed)] = result
        history.extend(epoch_results)

        if epoch + 1 < config.optimization.island_epochs:
            for burns in config.optimization.burn_counts:
                same_burn_results = [
                    latest[(burns, seed)] for seed in config.optimization.seeds
                ]
                migrant_count = max(
                    1,
                    int(
                        round(
                            config.optimization.population_size
                            * config.optimization.migration_fraction
                        )
                    ),
                )
                migrants = _migration_pool(same_burn_results, burns, migrant_count)
                codec = DecisionCodec(burns, config.scenario)
                for seed in config.optimization.seeds:
                    samplings[(burns, seed)] = _next_sampling(
                        latest[(burns, seed)],
                        migrants,
                        codec,
                        config.optimization.population_size,
                        seed + 7919 * (epoch + 1),
                    )

        if checkpoint is not None:
            metadata, arrays = _checkpoint_payload(
                config,
                history,
                samplings,
                epoch + 1,
            )
            checkpoint.save(metadata, arrays)
            if progress is not None:
                progress.emit(
                    "checkpoint_saved",
                    stage="nsga2",
                    completed_epoch=epoch + 1,
                    message=f"已儲存 NSGA-II epoch {epoch + 1} checkpoint",
                )

    return IslandSearchResult(config=config, island_results=history)
