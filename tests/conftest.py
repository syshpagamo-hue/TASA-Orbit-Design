from __future__ import annotations

from pathlib import Path

import pytest

from tasa_v4.config import load_candidate, load_project_config


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def competition_config():
    return load_project_config(ROOT / "configs" / "current_competition.yaml")


@pytest.fixture
def legacy_candidate():
    return load_candidate(ROOT / "examples" / "legacy_candidate_unverified.yaml")

