from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta

from tasa_v4.competition_day import OFFICIAL_REPORT_COLUMNS, parse_official_report
from tasa_v4.models import ScoreConfig
from tasa_v4.submission import generate_submission_bundle, render_submission_script


def _report_for_candidate(path, horizon_s: float) -> bytes:
    start = datetime(2026, 6, 26, 2, 0, 0)
    rows = []
    numeric = [
        7000, 0, 0, 0, 7.5, 0, 7000, 0, 97.7, 250.86, 95.57, 0,
        0, 0, 97.7, 250.86, 10000, 135, 0, 6.3, 0, 10000, 0, 0,
    ]
    for elapsed in (0.0, horizon_s):
        stamp = (start + timedelta(seconds=elapsed)).strftime(
            "%d %b %Y %H:%M:%S.%f"
        )[:-3]
        rows.append([*numeric, stamp, stamp])
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(OFFICIAL_REPORT_COLUMNS)
        writer.writerows(rows)
    return path.read_bytes()


def test_submission_is_executable_and_writes_official_report(
    tmp_path, competition_config, legacy_candidate
):
    config = competition_config.model_copy(
        update={"score": ScoreConfig(kt=0.01, ct_s=2500, kv=10, cv_km_s=1.0)}
    )
    script = render_submission_script(config, legacy_candidate)
    assert "% ORGANIZER_EVALUATION = PARSE_SCRIPT_PARAMETERS" in script
    assert "% CONTESTANT_MUST_RUN_IN_GMAT = TRUE" in script
    assert "Sat.DisplayStateType = ModifiedKeplerian;" in script
    assert "Sat.RadPer = 7000.000000000000;" in script
    assert "Sat2.RadApo = 10000.000000000000;" in script
    assert "% SCORE_KT = 0.010000000000" in script
    assert "% BURN_1_TIME_S = 44.326687911605" in script
    assert "Burn1.Axes = VNB;" in script
    assert "Create ReportFile Reports;" in script
    assert "Reports.Filename = 'Reports.txt';" in script
    assert "Reports.AppendToExistingFile = false;" in script
    assert all(column in script for column in OFFICIAL_REPORT_COLUMNS)
    assert (
        "Reports.Add = {" + ", ".join(OFFICIAL_REPORT_COLUMNS) + "};"
        in script
    )

    outputs = generate_submission_bundle(config, legacy_candidate, tmp_path)
    manifest = json.loads(outputs["manifest"].read_text(encoding="utf-8"))
    assert manifest["gmat_execution_required_by_contestant"] is True
    assert manifest["organizer_executes_script"] is False
    assert manifest["report_included"] is False
    assert manifest["spacecraft_state_type"] == "ModifiedKeplerian"

    generated = tmp_path / "GMAT_Reports.txt"
    original = _report_for_candidate(generated, legacy_candidate.evaluation_horizon_s)
    final = tmp_path / "final"
    final_outputs = generate_submission_bundle(
        config,
        legacy_candidate,
        final,
        official_report=parse_official_report(generated),
    )
    assert final_outputs["report"].name == "Reports.txt"
    assert final_outputs["report"].read_bytes() == original
    final_manifest = json.loads(final_outputs["manifest"].read_text(encoding="utf-8"))
    assert final_manifest["report_included"] is True
    assert final_manifest["report_summary"]["validation"] == "passed"
