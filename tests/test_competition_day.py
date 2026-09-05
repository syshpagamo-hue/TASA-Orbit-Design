from __future__ import annotations

import csv

import pytest

from tasa_v4.competition_day import OFFICIAL_REPORT_COLUMNS, parse_official_report


def _row(elapsed: int) -> list[object]:
    sat_time = f"26 Jun 2026 02:00:{elapsed:02d}.000"
    return [
        7000.0,
        float(elapsed),
        0.0,
        0.0,
        7.5,
        0.0,
        7000.0,
        0.0,
        97.7,
        250.86,
        95.57,
        0.0,
        0.0,
        0.0,
        97.7,
        250.86,
        10000.0,
        135.0,
        0.0,
        6.3,
        0.0,
        10000.0,
        float(elapsed),
        0.0,
        sat_time,
        sat_time,
    ]


def test_official_report_parses_26_columns_and_derives_initial_states(tmp_path):
    path = tmp_path / "Reports.csv"
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(OFFICIAL_REPORT_COLUMNS)
        writer.writerow(_row(0))
        writer.writerow(_row(10))
    report = parse_official_report(path)
    sat, sat2 = report.initial_states()
    assert report.row_count == 2
    assert report.duration_s == pytest.approx(10.0)
    assert report.median_step_s == pytest.approx(10.0)
    assert sat.rad_per_km == pytest.approx(7000.0)
    assert sat.ta_deg == pytest.approx(95.57)
    assert sat2.rad_apo_km == pytest.approx(10000.0)
    assert sat2.ta_deg == pytest.approx(135.0)


def test_official_report_rejects_time_reset(tmp_path):
    path = tmp_path / "Reports.csv"
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(OFFICIAL_REPORT_COLUMNS)
        writer.writerow(_row(10))
        writer.writerow(_row(0))
    with pytest.raises(ValueError, match="不可倒退"):
        parse_official_report(path)


def test_official_report_accepts_native_space_delimited_gmat_rows(tmp_path):
    path = tmp_path / "Reports.txt"
    lines = [" ".join(OFFICIAL_REPORT_COLUMNS)]
    lines.extend(" ".join(str(value) for value in _row(second)) for second in (0, 10))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    report = parse_official_report(path)
    assert report.row_count == 2
    assert report.start_utc == "26 Jun 2026 02:00:00.000"


def test_official_report_preserves_duplicate_maneuver_timestamp(tmp_path):
    path = tmp_path / "Reports.csv"
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(OFFICIAL_REPORT_COLUMNS)
        writer.writerow(_row(0))
        writer.writerow(_row(0))
        writer.writerow(_row(10))
    report = parse_official_report(path)
    assert report.row_count == 3
    assert report.duplicate_time_rows == 1
    assert report.median_step_s == pytest.approx(10.0)
