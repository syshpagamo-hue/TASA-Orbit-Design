from __future__ import annotations

import csv
import json
from pathlib import Path

import yaml

from .models import GmatVerification, ProjectConfig
from .pareto import EvaluatedCandidate


def _candidate_payload(index: int, item: EvaluatedCandidate) -> dict[str, object]:
    return {
        "candidate_id": f"P{index:03d}",
        "plan": item.plan.model_dump(mode="json"),
        "nominal": item.nominal.model_dump(mode="json"),
        "robust": item.robust.model_dump(mode="json") if item.robust else None,
        "objectives": list(item.objectives),
        "constraint_violation": item.constraint_violation,
        "feasible": item.feasible,
        "score": item.score,
    }


def write_results(
    output_directory: str | Path,
    config: ProjectConfig,
    candidates: list[EvaluatedCandidate],
    verifications: dict[str, GmatVerification] | None = None,
) -> dict[str, Path]:
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    config_path = output / "config_snapshot.yaml"
    json_path = output / "pareto.json"
    csv_path = output / "pareto.csv"
    verification_path = output / "gmat_verification.json"
    candidates_directory = output / "candidates"
    candidates_directory.mkdir(parents=True, exist_ok=True)

    config_path.write_text(
        yaml.safe_dump(
            config.model_dump(mode="json", exclude_none=True),
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    payload = [_candidate_payload(index, item) for index, item in enumerate(candidates, 1)]
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    for index, item in enumerate(candidates, 1):
        candidate_path = candidates_directory / f"P{index:03d}.yaml"
        candidate_path.write_text(
            yaml.safe_dump(
                item.plan.model_dump(mode="json", exclude_none=True),
                allow_unicode=True,
                sort_keys=False,
            ),
            encoding="utf-8",
        )

    fieldnames = [
        "candidate_id",
        "official_rank",
        "source",
        "burn_count",
        "first_entry_time_s",
        "total_dv_km_s",
        "minimum_distance_km",
        "robust_success_rate",
        "robust_p95_distance_km",
        "score",
        "feasible",
        "burn_times_s",
        "dv_vnb_km_s",
    ]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        for index, item in enumerate(candidates, 1):
            writer.writerow(
                {
                    "candidate_id": f"P{index:03d}",
                    "official_rank": index if config.score is not None else None,
                    "source": item.plan.source,
                    "burn_count": len(item.plan.burns),
                    "first_entry_time_s": item.nominal.first_entry_time_s,
                    "total_dv_km_s": item.nominal.total_dv_km_s,
                    "minimum_distance_km": item.nominal.minimum_distance_km,
                    "robust_success_rate": (
                        item.robust.success_rate if item.robust else None
                    ),
                    "robust_p95_distance_km": (
                        item.robust.p95_minimum_distance_km if item.robust else None
                    ),
                    "score": item.score,
                    "feasible": item.feasible,
                    "burn_times_s": json.dumps(
                        [burn.time_s for burn in item.plan.burns]
                    ),
                    "dv_vnb_km_s": json.dumps(
                        [burn.dv_vnb_km_s for burn in item.plan.burns]
                    ),
                }
            )
    if verifications is not None:
        verification_path.write_text(
            json.dumps(
                {
                    key: value.model_dump(mode="json")
                    for key, value in verifications.items()
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    return {
        "config": config_path,
        "json": json_path,
        "csv": csv_path,
        "candidates": candidates_directory,
        **({"verification": verification_path} if verifications is not None else {}),
    }
