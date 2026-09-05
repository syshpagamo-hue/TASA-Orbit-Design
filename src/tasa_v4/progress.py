from __future__ import annotations

import hashlib
import json
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

import numpy as np


def config_fingerprint(payload: dict[str, Any]) -> str:
    normalized = json.loads(json.dumps(payload, ensure_ascii=False))
    # Checkpoint routing/resume policy changes execution, not the mathematical
    # search problem. Ignoring it lets --no-resume create checkpoints that a
    # later normal invocation can safely continue.
    normalized.pop("checkpoint", None)
    encoded = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def atomic_write_json(path: str | Path, payload: Any) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary, destination)
    return destination


@dataclass
class ProgressRecorder:
    output_directory: Path
    timings_s: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.output_directory = Path(self.output_directory)
        self.output_directory.mkdir(parents=True, exist_ok=True)
        self.events_path = self.output_directory / "progress.jsonl"

    def emit(self, event: str, **payload: Any) -> None:
        record = {"unix_s": time.time(), "event": event, **payload}
        with self.events_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        print(f"[TASA V4.2] {payload.get('message', event)}", flush=True)

    def progress(self, stage: str, completed: int, total: int) -> None:
        percent = 100.0 if total == 0 else 100.0 * completed / total
        self.emit(
            "progress",
            stage=stage,
            completed=int(completed),
            total=int(total),
            percent=round(percent, 1),
            message=f"{stage}: {completed}/{total} ({percent:.1f}%)",
        )

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        started = time.perf_counter()
        self.emit("stage_started", stage=name, message=f"開始 {name}")
        try:
            yield
        finally:
            elapsed = time.perf_counter() - started
            self.timings_s[name] = self.timings_s.get(name, 0.0) + elapsed
            self.emit(
                "stage_finished",
                stage=name,
                elapsed_s=elapsed,
                message=f"完成 {name}，耗時 {elapsed:.2f} 秒",
            )


@dataclass
class OptimizerCheckpoint:
    """Atomic JSON + NPZ checkpoint used after every completed island epoch."""

    directory: Path

    def __post_init__(self) -> None:
        self.directory = Path(self.directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    @property
    def metadata_path(self) -> Path:
        return self.directory / "optimizer_checkpoint.json"

    @property
    def arrays_path(self) -> Path:
        return self.directory / "optimizer_checkpoint.npz"

    def save(self, metadata: dict[str, Any], arrays: dict[str, np.ndarray]) -> None:
        cleaned: dict[str, np.ndarray] = {}
        for key, value in arrays.items():
            array = np.asarray(value)
            if np.issubdtype(array.dtype, np.number) and not np.all(np.isfinite(array)):
                raise ValueError(f"checkpoint array {key} contains non-finite values")
            cleaned[key] = array

        generation_path = self.directory / (
            f"optimizer_checkpoint_{time.time_ns()}.npz"
        )
        temporary_arrays = generation_path.with_suffix(".npz.tmp")
        with temporary_arrays.open("wb") as stream:
            np.savez_compressed(stream, **cleaned)
        os.replace(temporary_arrays, generation_path)
        atomic_write_json(
            self.metadata_path,
            {
                "format_version": 2,
                "arrays_file": generation_path.name,
                **metadata,
            },
        )

    def load(self) -> tuple[dict[str, Any], dict[str, np.ndarray]] | None:
        if not self.metadata_path.exists():
            return None
        metadata = json.loads(self.metadata_path.read_text(encoding="utf-8"))
        if metadata.get("format_version") != 2:
            raise ValueError("unsupported optimizer checkpoint format")
        arrays_name = metadata.get("arrays_file")
        if not isinstance(arrays_name, str):
            return None
        arrays_path = self.directory / arrays_name
        if not arrays_path.exists():
            return None
        with np.load(arrays_path, allow_pickle=False) as archive:
            arrays = {key: np.asarray(archive[key]) for key in archive.files}
        return metadata, arrays
