from __future__ import annotations

from pathlib import Path
from typing import Any, TypeVar

import yaml
from pydantic import BaseModel

from .models import CandidatePlan, ProjectConfig


T = TypeVar("T", bound=BaseModel)


def _load_yaml(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    with source.open("r", encoding="utf-8") as stream:
        payload = yaml.safe_load(stream)
    if not isinstance(payload, dict):
        raise ValueError(f"{source} must contain a YAML mapping")
    return payload


def load_project_config(path: str | Path) -> ProjectConfig:
    return ProjectConfig.model_validate(_load_yaml(path))


def load_candidate(path: str | Path) -> CandidatePlan:
    return CandidatePlan.model_validate(_load_yaml(path))


def save_model_yaml(model: BaseModel, path: str | Path) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8") as stream:
        yaml.safe_dump(
            model.model_dump(mode="json", exclude_none=True),
            stream,
            allow_unicode=True,
            sort_keys=False,
        )
    return destination

