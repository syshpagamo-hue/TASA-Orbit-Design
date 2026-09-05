from __future__ import annotations

import csv

import pytest

from tasa_v4.gmat.generator import TRAJECTORY_COLUMNS, generate_gmat_bundle
from tasa_v4.gmat.parser import analyze_encounter, parse_trajectory_report


def test_generator_uses_segment_duration_and_separate_reports(
    tmp_path, competition_config, legacy_candidate
):
    bundle = generate_gmat_bundle(competition_config, legacy_candidate, tmp_path)
    script = bundle["script"].read_text(encoding="utf-8")
    first = legacy_candidate.burns[0].time_s
    final_duration = (
        legacy_candidate.evaluation_horizon_s
        + competition_config.gmat.post_entry_buffer_s
        - first
    )
    assert f"Sat.ElapsedSecs = {first:.16g}" in script
    assert f"Sat.ElapsedSecs = {final_duration:.16g}" in script
    assert "TrajectoryReport.AppendToExistingFile = false" in script
    assert "FinalSummary.AppendToExistingFile = false" in script
    assert "TrajectoryReport.Delimiter = Comma" in script
    assert script.count("Create ReportFile TrajectoryReport") == 1
    assert script.count("Create ReportFile FinalSummary") == 1


def test_parser_detects_hidden_crossing_between_samples(tmp_path):
    path = tmp_path / "trajectory.csv"
    rows = [
        [0, 10, 0, 0, -6, 0, 0, 0, 0, 0, 0, 0, 0],
        [5, 10, 0, 0, 6, 0, 0, 0, 0, 0, 0, 0, 0],
    ]
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(TRAJECTORY_COLUMNS)
        writer.writerows(rows)
    parsed = parse_trajectory_report(path)
    encounter = analyze_encounter(parsed, 5.0)
    assert encounter.first_entry_time_s is not None
    assert 0 < encounter.first_entry_time_s < 2.5
    assert encounter.minimum_distance_km == pytest.approx(2.5, abs=1e-7)
    assert encounter.minimum_distance_time_s == pytest.approx(2.5, abs=1e-7)


def test_parser_rejects_appended_time_reset(tmp_path):
    path = tmp_path / "trajectory.csv"
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(TRAJECTORY_COLUMNS)
        writer.writerow([5, *([0] * 12)])
        writer.writerow(TRAJECTORY_COLUMNS)
        writer.writerow([0, *([0] * 12)])
    with pytest.raises(ValueError, match="not monotonic"):
        parse_trajectory_report(path)

