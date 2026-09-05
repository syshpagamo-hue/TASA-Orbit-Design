from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from ..models import CandidatePlan, GmatVerification, ProjectConfig
from ..propagation import simulate_candidate
from .generator import generate_gmat_bundle
from .parser import analyze_encounter, parse_trajectory_report


def resolve_gmat_executable(configured: str | None = None) -> Path | None:
    candidates = [configured, os.environ.get("GMAT_EXECUTABLE")]
    candidates.extend(shutil.which(name) for name in ("GmatConsole", "GMAT", "GMAT.exe"))
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate).expanduser()
        if path.is_file():
            return path.resolve()
        located = shutil.which(candidate)
        if located:
            return Path(located).resolve()
    return None


def run_gmat(
    executable: Path,
    script: Path,
    log: Path,
    *,
    timeout_s: float,
    startup_file: str | None = None,
) -> subprocess.CompletedProcess[str]:
    command = [str(executable), "--run", str(script), "--exit", "--logfile", str(log)]
    if startup_file:
        command.extend(("--startup_file", str(Path(startup_file).expanduser().resolve())))
    return subprocess.run(
        command,
        cwd=script.parent,
        text=True,
        capture_output=True,
        timeout=timeout_s,
        check=False,
    )


def verify_with_gmat(
    config: ProjectConfig,
    plan: CandidatePlan,
    run_directory: str | Path,
    *,
    executable: str | Path | None = None,
) -> GmatVerification:
    bundle = generate_gmat_bundle(config, plan, run_directory)
    gmat = resolve_gmat_executable(str(executable) if executable else config.gmat.executable)
    if gmat is None:
        return GmatVerification(
            status="not_run",
            diagnostics=[
                "GMAT executable not found; set gmat.executable or GMAT_EXECUTABLE"
            ],
            run_directory=bundle["run_directory"],
        )
    for key in ("trajectory", "summary", "log"):
        path = bundle[key]
        if path.exists():
            path.unlink()
    completed = run_gmat(
        gmat,
        bundle["script"],
        bundle["log"],
        timeout_s=config.gmat.timeout_s,
        startup_file=config.gmat.startup_file,
    )
    diagnostics: list[str] = []
    if completed.returncode != 0:
        diagnostics.append(f"GMAT returned exit code {completed.returncode}")
        if completed.stderr.strip():
            diagnostics.append(completed.stderr.strip()[-1000:])
        return GmatVerification(
            status="failed",
            diagnostics=diagnostics,
            run_directory=bundle["run_directory"],
        )
    if not bundle["trajectory"].exists():
        return GmatVerification(
            status="failed",
            diagnostics=["GMAT completed but TrajectoryReport.csv was not created"],
            run_directory=bundle["run_directory"],
        )
    parsed = parse_trajectory_report(bundle["trajectory"])
    encounter = analyze_encounter(parsed, config.scenario.intercept_radius_km)
    diagnostics.extend(encounter.diagnostics)
    verification_horizon = min(
        config.scenario.tmax_s(),
        plan.evaluation_horizon_s + config.gmat.post_entry_buffer_s,
    )
    python_plan = plan.model_copy(update={"evaluation_horizon_s": verification_horizon})
    python_metrics = simulate_candidate(
        python_plan,
        config.scenario,
        config.propagation,
    )
    if python_metrics.first_entry_time_s is None or encounter.first_entry_time_s is None:
        diagnostics.append("Python or GMAT did not find a 5 km entry")
        return GmatVerification(
            status="failed",
            python_first_entry_time_s=python_metrics.first_entry_time_s,
            gmat_first_entry_time_s=encounter.first_entry_time_s,
            gmat_minimum_distance_km=encounter.minimum_distance_km,
            diagnostics=diagnostics,
            run_directory=bundle["run_directory"],
        )
    difference = abs(python_metrics.first_entry_time_s - encounter.first_entry_time_s)
    if difference > config.gmat.max_time_difference_s:
        diagnostics.append(
            f"first-entry times differ by {difference:.6g} s, above tolerance"
        )
    passed = difference <= config.gmat.max_time_difference_s
    return GmatVerification(
        status="passed" if passed else "failed",
        python_first_entry_time_s=python_metrics.first_entry_time_s,
        gmat_first_entry_time_s=encounter.first_entry_time_s,
        time_difference_s=difference,
        gmat_minimum_distance_km=encounter.minimum_distance_km,
        diagnostics=diagnostics,
        run_directory=bundle["run_directory"],
    )
