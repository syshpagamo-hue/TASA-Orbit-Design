from __future__ import annotations

import json
import shutil
from pathlib import Path

import yaml

from .competition_day import (
    OFFICIAL_REPORT_COLUMNS,
    ModifiedKeplerianState,
    OfficialReport,
    validate_generated_report,
)
from .models import CandidatePlan, ProjectConfig


def _number(value: float) -> str:
    return f"{float(value):.12f}"


def _spacecraft_block(name: str, state: ModifiedKeplerianState) -> str:
    return f"""Create Spacecraft {name};
{name}.DateFormat = UTCGregorian;
{name}.Epoch = '{state.epoch_utc}';
{name}.CoordinateSystem = EarthMJ2000Eq;
{name}.DisplayStateType = ModifiedKeplerian;
{name}.RadPer = {_number(state.rad_per_km)};
{name}.RadApo = {_number(state.rad_apo_km)};
{name}.INC = {_number(state.inc_deg)};
{name}.RAAN = {_number(state.raan_deg)};
{name}.AOP = {_number(state.aop_deg)};
{name}.TA = {_number(state.ta_deg)};
{name}.DryMass = {_number(state.dry_mass_kg)};
"""


def _burn_block(index: int, values: tuple[float, float, float]) -> str:
    name = f"Burn{index}"
    return f"""Create ImpulsiveBurn {name};
{name}.CoordinateSystem = Local;
{name}.Origin = Earth;
{name}.Axes = VNB;
{name}.Element1 = {_number(values[0])};
{name}.Element2 = {_number(values[1])};
{name}.Element3 = {_number(values[2])};
{name}.DecrementMass = false;
"""


def render_submission_script(config: ProjectConfig, plan: CandidatePlan) -> str:
    if config.score is None:
        raise ValueError("正式提交前必須填入 kt、Ct、kv、Cv 四個評分係數")
    violations = plan.violations(config.scenario)
    if violations:
        raise ValueError("候選不符合硬限制：" + "；".join(violations))

    sat = ModifiedKeplerianState.from_spacecraft(
        config.scenario.chaser, config.scenario.epoch_utc
    )
    sat2 = ModifiedKeplerianState.from_spacecraft(
        config.scenario.target, config.scenario.epoch_utc
    )
    score = config.score
    comments = [
        "% TASA Orbit V4 executable formal submission",
        "% ORGANIZER_EVALUATION = PARSE_SCRIPT_PARAMETERS",
        "% CONTESTANT_MUST_RUN_IN_GMAT = TRUE",
        "% GMAT_OUTPUT_REPORT = Reports.txt",
        f"% CANDIDATE_NAME = {plan.name}",
        f"% SCORE_KT = {_number(score.kt)}",
        f"% SCORE_CT_S = {_number(score.ct_s)}",
        f"% SCORE_KV = {_number(score.kv)}",
        f"% SCORE_CV_KM_S = {_number(score.cv_km_s)}",
        f"% EVALUATION_HORIZON_S = {_number(plan.evaluation_horizon_s)}",
        f"% BURN_COUNT = {len(plan.burns)}",
    ]
    for index, burn in enumerate(plan.burns, start=1):
        comments.extend(
            [
                f"% BURN_{index}_TIME_S = {_number(burn.time_s)}",
                "% BURN_{}_DV_VNB_KM_S = {}, {}, {}".format(
                    index, *(_number(value) for value in burn.dv_vnb_km_s)
                ),
            ]
        )

    resources = [
        "\n".join(comments),
        _spacecraft_block("Sat", sat),
        _spacecraft_block("Sat2", sat2),
        """Create ForceModel TwoBodyFM;
TwoBodyFM.CentralBody = Earth;
TwoBodyFM.PrimaryBodies = {Earth};
TwoBodyFM.Drag = None;
TwoBodyFM.SRP = Off;
TwoBodyFM.RelativisticCorrection = Off;
TwoBodyFM.GravityField.Earth.Degree = 0;
TwoBodyFM.GravityField.Earth.Order = 0;

Create Propagator Prop;
Prop.FM = TwoBodyFM;
Prop.Type = RungeKutta89;
"""
        + f"""Prop.InitialStepSize = {_number(config.gmat.initial_step_s)};
Prop.Accuracy = {_number(config.gmat.accuracy)};
Prop.MinStep = {_number(config.gmat.min_step_s)};
Prop.MaxStep = {_number(config.gmat.max_step_s)};
Prop.MaxStepAttempts = 100;
Prop.StopIfAccuracyIsViolated = true;
""",
    ]
    resources.extend(
        _burn_block(index, burn.dv_vnb_km_s)
        for index, burn in enumerate(plan.burns, start=1)
    )
    # GMAT's script parser requires ReportFile.Add to stay on one line.
    report_columns = ", ".join(OFFICIAL_REPORT_COLUMNS)
    resources.append(
        f"""Create ReportFile Reports;
Reports.Filename = 'Reports.txt';
Reports.Precision = 16;
Reports.Add = {{{report_columns}}};
Reports.WriteHeaders = true;
Reports.LeftJustify = On;
Reports.ZeroFill = Off;
Reports.FixedWidth = true;
Reports.Delimiter = ' ';
Reports.ColumnWidth = 32;
Reports.WriteReport = true;
Reports.AppendToExistingFile = false;
"""
    )

    mission = ["BeginMissionSequence;"]
    current_time = 0.0
    for index, burn in enumerate(plan.burns, start=1):
        duration = burn.time_s - current_time
        if duration > 1e-12:
            mission.append(
                "Propagate Prop(Sat, Sat2) "
                f"{{Sat.ElapsedSecs = {_number(duration)}}};"
            )
        mission.append(f"Maneuver Burn{index}(Sat);")
        current_time = burn.time_s
    final_duration = plan.evaluation_horizon_s - current_time
    if final_duration > 1e-12:
        mission.append(
            "Propagate Prop(Sat, Sat2) "
            f"{{Sat.ElapsedSecs = {_number(final_duration)}}};"
        )
    return "\n\n".join(resources) + "\n\n" + "\n".join(mission) + "\n"


def generate_submission_bundle(
    config: ProjectConfig,
    plan: CandidatePlan,
    output_directory: str | Path,
    *,
    official_report: OfficialReport | None = None,
) -> dict[str, Path]:
    output = Path(output_directory).resolve()
    output.mkdir(parents=True, exist_ok=True)
    script_path = output / "submission.script"
    candidate_path = output / "candidate.json"
    config_path = output / "competition_config.yaml"
    manifest_path = output / "submission_manifest.json"

    script_path.write_text(render_submission_script(config, plan), encoding="utf-8")
    candidate_path.write_text(
        json.dumps(plan.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    config_path.write_text(
        yaml.safe_dump(
            config.model_dump(mode="json", exclude_none=True),
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    outputs: dict[str, Path] = {
        "directory": output,
        "script": script_path,
        "candidate": candidate_path,
        "config": config_path,
        "manifest": manifest_path,
    }
    report_summary = None
    for stale_name in ("Reports.txt", "Reports_summary.json"):
        stale_path = output / stale_name
        is_uploaded_source = (
            official_report is not None
            and stale_name == "Reports.txt"
            and official_report.source.resolve() == stale_path.resolve()
        )
        if stale_path.exists() and not is_uploaded_source:
            stale_path.unlink()
    if official_report is not None:
        report_path = output / "Reports.txt"
        report_summary_path = output / "Reports_summary.json"
        report_summary = validate_generated_report(official_report, config, plan)
        if official_report.source.resolve() != report_path.resolve():
            shutil.copy2(official_report.source, report_path)
        report_summary_path.write_text(
            json.dumps(report_summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        outputs["report"] = report_path
        outputs["report_summary"] = report_summary_path

    manifest = {
        "format_version": 2,
        "workflow": (
            "contestant runs submission.script in GMAT, then submits "
            "submission.script and the generated Reports.txt"
        ),
        "formal_evaluation": "organizer parses submission.script parameters",
        "organizer_executes_script": False,
        "gmat_execution_required_by_contestant": True,
        "spacecraft_state_type": "ModifiedKeplerian",
        "burn_coordinate_axes": "Local VNB",
        "report_columns": OFFICIAL_REPORT_COLUMNS,
        "report_source": (
            "generated by contestant executing submission.script in GMAT"
            if official_report is not None
            else "not generated yet; run submission.script in GMAT"
        ),
        "report_included": official_report is not None,
        "report_summary": report_summary,
        "files": {key: value.name for key, value in outputs.items() if key != "directory"},
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return outputs
