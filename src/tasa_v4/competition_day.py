from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

from .gmat.parser import GmatEncounter, ParsedTrajectory, analyze_encounter
from .models import (
    CandidatePlan,
    KeplerianElements,
    ProjectConfig,
    ScoreConfig,
    SpacecraftConfig,
)


OFFICIAL_REPORT_COLUMNS = [
    "Sat.EarthMJ2000Eq.X",
    "Sat.EarthMJ2000Eq.Y",
    "Sat.EarthMJ2000Eq.Z",
    "Sat.EarthMJ2000Eq.VX",
    "Sat.EarthMJ2000Eq.VY",
    "Sat.EarthMJ2000Eq.VZ",
    "Sat.Earth.SMA",
    "Sat.Earth.ECC",
    "Sat.EarthMJ2000Eq.INC",
    "Sat.EarthMJ2000Eq.RAAN",
    "Sat.Earth.TA",
    "Sat.EarthMJ2000Eq.AOP",
    "Sat2.EarthMJ2000Eq.AOP",
    "Sat2.Earth.ECC",
    "Sat2.EarthMJ2000Eq.INC",
    "Sat2.EarthMJ2000Eq.RAAN",
    "Sat2.Earth.SMA",
    "Sat2.Earth.TA",
    "Sat2.EarthMJ2000Eq.VX",
    "Sat2.EarthMJ2000Eq.VY",
    "Sat2.EarthMJ2000Eq.VZ",
    "Sat2.EarthMJ2000Eq.X",
    "Sat2.EarthMJ2000Eq.Y",
    "Sat2.EarthMJ2000Eq.Z",
    "Sat.UTCGregorian",
    "Sat2.UTCGregorian",
]
NUMERIC_REPORT_COLUMNS = OFFICIAL_REPORT_COLUMNS[:24]


@dataclass(frozen=True)
class ModifiedKeplerianState:
    epoch_utc: str
    rad_per_km: float
    rad_apo_km: float
    inc_deg: float
    raan_deg: float
    aop_deg: float
    ta_deg: float
    dry_mass_kg: float = 850.0

    def to_spacecraft(self, name: str) -> SpacecraftConfig:
        denominator = self.rad_apo_km + self.rad_per_km
        if self.rad_per_km <= 0 or self.rad_apo_km < self.rad_per_km:
            raise ValueError("RadPer/RadApo 必須滿足 0 < RadPer <= RadApo")
        return SpacecraftConfig(
            name=name,
            keplerian=KeplerianElements(
                sma_km=0.5 * denominator,
                ecc=(self.rad_apo_km - self.rad_per_km) / denominator,
                inc_deg=self.inc_deg,
                raan_deg=self.raan_deg,
                aop_deg=self.aop_deg,
                ta_deg=self.ta_deg,
            ),
            dry_mass_kg=self.dry_mass_kg,
        )

    @classmethod
    def from_spacecraft(
        cls, spacecraft: SpacecraftConfig, epoch_utc: str
    ) -> "ModifiedKeplerianState":
        if spacecraft.keplerian is None:
            raise ValueError("競賽提交視窗需要 ModifiedKeplerian 軌道元素")
        elements = spacecraft.keplerian
        return cls(
            epoch_utc=epoch_utc,
            rad_per_km=elements.sma_km * (1.0 - elements.ecc),
            rad_apo_km=elements.sma_km * (1.0 + elements.ecc),
            inc_deg=elements.inc_deg,
            raan_deg=elements.raan_deg,
            aop_deg=elements.aop_deg,
            ta_deg=elements.ta_deg,
            dry_mass_kg=spacecraft.dry_mass_kg,
        )


def config_with_competition_inputs(
    base: ProjectConfig,
    sat: ModifiedKeplerianState,
    sat2: ModifiedKeplerianState,
    score: ScoreConfig,
) -> ProjectConfig:
    if parse_gmat_utc(sat.epoch_utc) != parse_gmat_utc(sat2.epoch_utc):
        raise ValueError("目前二體傳播器要求 Sat 與 Sat2 使用相同 Epoch")
    scenario = base.scenario.model_copy(
        update={
            "epoch_utc": sat.epoch_utc,
            "chaser": sat.to_spacecraft("Sat"),
            "target": sat2.to_spacecraft("Sat2"),
        }
    )
    return base.model_copy(update={"scenario": scenario, "score": score})


@dataclass(frozen=True)
class OfficialReport:
    source: Path
    values: np.ndarray
    sat_utc: tuple[str, ...]
    sat2_utc: tuple[str, ...]
    elapsed_s: np.ndarray
    header_count: int
    warnings: tuple[str, ...]

    @property
    def row_count(self) -> int:
        return int(self.values.shape[0])

    @property
    def start_utc(self) -> str:
        return self.sat_utc[0]

    @property
    def end_utc(self) -> str:
        return self.sat_utc[-1]

    @property
    def duration_s(self) -> float:
        return float(self.elapsed_s[-1])

    @property
    def median_step_s(self) -> float:
        differences = np.diff(self.elapsed_s)
        differences = differences[differences > 0.0]
        return float(np.median(differences)) if len(differences) else 0.0

    @property
    def maximum_step_s(self) -> float:
        differences = np.diff(self.elapsed_s)
        differences = differences[differences > 0.0]
        return float(np.max(differences)) if len(differences) else 0.0

    @property
    def duplicate_time_rows(self) -> int:
        return int(np.count_nonzero(np.diff(self.elapsed_s) == 0.0))

    def initial_states(
        self,
    ) -> tuple[ModifiedKeplerianState, ModifiedKeplerianState]:
        first = self.values[0]
        sat_sma, sat_ecc = float(first[6]), float(first[7])
        sat2_sma, sat2_ecc = float(first[16]), float(first[13])
        sat = ModifiedKeplerianState(
            epoch_utc=self.sat_utc[0],
            rad_per_km=sat_sma * (1.0 - sat_ecc),
            rad_apo_km=sat_sma * (1.0 + sat_ecc),
            inc_deg=float(first[8]),
            raan_deg=float(first[9]),
            aop_deg=float(first[11]),
            ta_deg=float(first[10]),
        )
        sat2 = ModifiedKeplerianState(
            epoch_utc=self.sat2_utc[0],
            rad_per_km=sat2_sma * (1.0 - sat2_ecc),
            rad_apo_km=sat2_sma * (1.0 + sat2_ecc),
            inc_deg=float(first[14]),
            raan_deg=float(first[15]),
            aop_deg=float(first[12]),
            ta_deg=float(first[17]),
        )
        return sat, sat2

    def encounter(self, intercept_radius_km: float = 5.0) -> GmatEncounter:
        trajectory = ParsedTrajectory(
            elapsed_s=self.elapsed_s,
            chaser_position_km=self.values[:, 0:3],
            chaser_velocity_km_s=self.values[:, 3:6],
            target_position_km=self.values[:, 21:24],
            target_velocity_km_s=self.values[:, 18:21],
            duplicate_header_count=max(0, self.header_count - 1),
        )
        return analyze_encounter(trajectory, intercept_radius_km)

    def summary(self, intercept_radius_km: float = 5.0) -> dict[str, object]:
        encounter = self.encounter(intercept_radius_km)
        return {
            "source": str(self.source),
            "columns": OFFICIAL_REPORT_COLUMNS,
            "rows": self.row_count,
            "start_utc": self.start_utc,
            "end_utc": self.end_utc,
            "duration_s": self.duration_s,
            "median_step_s": self.median_step_s,
            "maximum_step_s": self.maximum_step_s,
            "duplicate_time_rows": self.duplicate_time_rows,
            "header_count": self.header_count,
            "first_entry_time_s": encounter.first_entry_time_s,
            "minimum_distance_km": encounter.minimum_distance_km,
            "minimum_distance_time_s": encounter.minimum_distance_time_s,
            "warnings": [*self.warnings, *encounter.diagnostics],
        }


def parse_gmat_utc(value: str) -> datetime:
    normalized = " ".join(value.strip().strip("'").split())
    for pattern in ("%d %b %Y %H:%M:%S.%f", "%d %b %Y %H:%M:%S"):
        try:
            return datetime.strptime(normalized, pattern)
        except ValueError:
            continue
    raise ValueError(f"無法解析 UTCGregorian 時間：{value!r}")


def _header_cells(line: str) -> list[str]:
    if "," in line:
        return [cell.strip() for cell in next(csv.reader([line]))]
    return line.split()


def _parse_data_line(line: str) -> tuple[list[float], str, str]:
    if "," in line:
        cells = [cell.strip() for cell in next(csv.reader([line]))]
        if len(cells) == 26:
            return [float(value) for value in cells[:24]], cells[24], cells[25]
    cells = line.split()
    if len(cells) != 32:
        raise ValueError("空白分隔資料列必須包含 24 個數值與兩組四欄 UTC 時間")
    numeric = [float(value) for value in cells[:24]]
    return numeric, " ".join(cells[24:28]), " ".join(cells[28:32])


def parse_official_report(path: str | Path) -> OfficialReport:
    source = Path(path).resolve()
    numeric_rows: list[list[float]] = []
    sat_utc: list[str] = []
    sat2_utc: list[str] = []
    sat_times: list[datetime] = []
    sat2_times: list[datetime] = []
    header_count = 0
    malformed: list[str] = []

    with source.open("r", encoding="utf-8-sig", errors="replace") as stream:
        for line_number, raw_line in enumerate(stream, start=1):
            line = raw_line.strip()
            if not line or line.startswith(("#", "%")):
                continue
            cells = _header_cells(line)
            if cells and cells[0] == OFFICIAL_REPORT_COLUMNS[0]:
                if cells != OFFICIAL_REPORT_COLUMNS:
                    raise ValueError(
                        f"Reports 第 {line_number} 行欄位或順序不符官方 26 欄規格"
                    )
                header_count += 1
                continue
            try:
                values, sat_time_text, sat2_time_text = _parse_data_line(line)
                first_time = parse_gmat_utc(sat_time_text)
                second_time = parse_gmat_utc(sat2_time_text)
            except ValueError as error:
                malformed.append(f"第 {line_number} 行：{error}")
                continue
            numeric_rows.append(values)
            sat_utc.append(" ".join(sat_time_text.split()))
            sat2_utc.append(" ".join(sat2_time_text.split()))
            sat_times.append(first_time)
            sat2_times.append(second_time)

    if malformed:
        details = "; ".join(malformed[:5])
        more = f"；另有 {len(malformed) - 5} 行" if len(malformed) > 5 else ""
        raise ValueError(f"Reports 含無法解析的資料列：{details}{more}")
    if header_count == 0:
        raise ValueError("Reports 缺少官方 26 欄標題列")
    if len(numeric_rows) < 2:
        raise ValueError("完整連續 Reports 至少需要兩筆資料")
    if any(first != second for first, second in zip(sat_times, sat2_times)):
        raise ValueError("Sat 與 Sat2 的 UTCGregorian 未逐列對齊")

    elapsed = np.asarray(
        [(value - sat_times[0]).total_seconds() for value in sat_times], dtype=float
    )
    differences = np.diff(elapsed)
    if np.any(differences < 0.0):
        raise ValueError("Reports 時間不可倒退；可能混入舊資料或多次執行結果")
    warnings: list[str] = []
    positive_differences = differences[differences > 0.0]
    median_step = (
        float(np.median(positive_differences)) if len(positive_differences) else 0.0
    )
    maximum_step = (
        float(np.max(positive_differences)) if len(positive_differences) else 0.0
    )
    if median_step > 0.0 and maximum_step > 5.0 * median_step:
        warnings.append(
            "最大時間間隔超過中位間隔五倍；請確認 GMAT 傳播設定與任務段是否完整"
        )
    duplicate_time_rows = int(np.count_nonzero(differences == 0.0))
    if duplicate_time_rows:
        warnings.append(
            f"Reports 含 {duplicate_time_rows} 筆同時刻資料；通常是 Maneuver 前後狀態，已保留"
        )
    if header_count > 1:
        warnings.append("Reports 含重複標題列；請確認只執行本次 submission.script 一次")

    return OfficialReport(
        source=source,
        values=np.asarray(numeric_rows, dtype=float),
        sat_utc=tuple(sat_utc),
        sat2_utc=tuple(sat2_utc),
        elapsed_s=elapsed,
        header_count=header_count,
        warnings=tuple(warnings),
    )


def _angular_difference_deg(first: float, second: float) -> float:
    return abs((first - second + 180.0) % 360.0 - 180.0)


def validate_generated_report(
    report: OfficialReport,
    config: ProjectConfig,
    plan: CandidatePlan,
) -> dict[str, object]:
    """Validate a Report generated locally by executing this candidate in GMAT."""

    expected_start = parse_gmat_utc(config.scenario.epoch_utc)
    actual_start = parse_gmat_utc(report.start_utc)
    actual_end = parse_gmat_utc(report.end_utc)
    tolerance_s = max(1.0, 2.0 * config.gmat.max_step_s)
    start_error_s = abs((actual_start - expected_start).total_seconds())
    end_elapsed_s = (actual_end - expected_start).total_seconds()
    end_error_s = abs(end_elapsed_s - plan.evaluation_horizon_s)
    if start_error_s > tolerance_s:
        raise ValueError(
            "Reports 起始時間與 submission.script 的 Epoch 不符："
            f"相差 {start_error_s:.6f} s"
        )
    if end_error_s > tolerance_s:
        raise ValueError(
            "Reports 未涵蓋完整任務區間："
            f"預期結束於 {plan.evaluation_horizon_s:.6f} s，"
            f"實際為 {end_elapsed_s:.6f} s"
        )

    # When the initial epoch row exists, verify that this is the Report belonging
    # to the spacecraft settings used to create submission.script.
    state_checks: dict[str, float] = {}
    if start_error_s <= 1.0e-6:
        sat = config.scenario.chaser.keplerian
        sat2 = config.scenario.target.keplerian
        if sat is None or sat2 is None:
            raise ValueError("正式提交驗證需要兩艘飛船的 ModifiedKeplerian 設定")
        first = report.values[0]
        checks = {
            "Sat.SMA": abs(float(first[6]) - sat.sma_km),
            "Sat.ECC": abs(float(first[7]) - sat.ecc),
            "Sat.INC": _angular_difference_deg(float(first[8]), sat.inc_deg),
            "Sat.RAAN": _angular_difference_deg(float(first[9]), sat.raan_deg),
            "Sat.TA": _angular_difference_deg(float(first[10]), sat.ta_deg),
            "Sat.AOP": _angular_difference_deg(float(first[11]), sat.aop_deg),
            "Sat2.AOP": _angular_difference_deg(float(first[12]), sat2.aop_deg),
            "Sat2.ECC": abs(float(first[13]) - sat2.ecc),
            "Sat2.INC": _angular_difference_deg(float(first[14]), sat2.inc_deg),
            "Sat2.RAAN": _angular_difference_deg(float(first[15]), sat2.raan_deg),
            "Sat2.SMA": abs(float(first[16]) - sat2.sma_km),
            "Sat2.TA": _angular_difference_deg(float(first[17]), sat2.ta_deg),
        }
        linear_tolerance = 1.0e-5
        angular_tolerance = 1.0e-5
        eccentricity_tolerance = 1.0e-9
        for name, error in checks.items():
            state_checks[name] = error
            limit = (
                eccentricity_tolerance
                if name.endswith("ECC")
                else linear_tolerance
                if name.endswith("SMA")
                else angular_tolerance
            )
            if error > limit:
                raise ValueError(
                    f"Reports 首筆 {name} 與 submission.script 不符：誤差 {error:.12g}"
                )

    summary = report.summary(config.scenario.intercept_radius_km)
    summary.update(
        {
            "validation": "passed",
            "candidate_name": plan.name,
            "expected_epoch_utc": config.scenario.epoch_utc,
            "expected_horizon_s": plan.evaluation_horizon_s,
            "start_time_error_s": start_error_s,
            "end_time_error_s": end_error_s,
            "initial_state_errors": state_checks,
        }
    )
    return summary
