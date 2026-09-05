from __future__ import annotations

import csv
import io
import json
import threading
from datetime import datetime, timedelta
from urllib.request import Request, urlopen
import zipfile

from tasa_v4.competition_day import OFFICIAL_REPORT_COLUMNS
from tasa_v4.competition_web import _initial_payload, _make_server

from conftest import ROOT


def _multipart(
    fields: dict[str, str], files: dict[str, tuple[str, bytes]]
) -> tuple[bytes, str]:
    boundary = "----TasaV4Boundary"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                value.encode(),
                b"\r\n",
            ]
        )
    for name, (filename, content) in files.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                (
                    f'Content-Disposition: form-data; name="{name}"; '
                    f'filename="{filename}"\r\n'
                ).encode(),
                b"Content-Type: application/octet-stream\r\n\r\n",
                content,
                b"\r\n",
            ]
        )
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def _report_bytes(horizon_s: float) -> bytes:
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(OFFICIAL_REPORT_COLUMNS)
    numeric = [
        7000,
        0,
        0,
        0,
        7.5,
        0,
        7000,
        0,
        97.7,
        250.86,
        95.57,
        0,
        0,
        0,
        97.7,
        250.86,
        10000,
        135,
        0,
        6.3,
        0,
        10000,
        0,
        0,
    ]
    start = datetime(2026, 6, 26, 2, 0, 0)
    for second in (0.0, horizon_s):
        timestamp = (start + timedelta(seconds=second)).strftime(
            "%d %b %Y %H:%M:%S.%f"
        )[:-3]
        writer.writerow([*numeric, timestamp, timestamp])
    return stream.getvalue().encode()


def _post(url: str, token: str, body: bytes, content_type: str) -> dict[str, object]:
    request = Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": content_type, "X-TASA-Token": token},
    )
    with urlopen(request, timeout=10) as response:
        return json.loads(response.read())


def test_browser_fallback_generates_run_bundle_then_finalizes_own_report():
    server, state, url = _make_server(ROOT / "configs" / "current_competition.yaml")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(url, timeout=10) as response:
            page = response.read().decode()
        assert "不需要 Tkinter" in page

        payload = _initial_payload(state.base_config)
        payload["score"] = {"kt": 0.01, "ct_s": 2500, "kv": 10, "cv_km_s": 1}
        body, content_type = _multipart(
            {"payload": json.dumps(payload)},
            {
                "candidate": (
                    "P001.yaml",
                    (ROOT / "examples" / "legacy_candidate_unverified.yaml").read_bytes(),
                )
            },
        )
        generated = _post(
            url + "api/generate", state.token, body, content_type
        )
        with urlopen(url + generated["url"], timeout=10) as response:
            archive = io.BytesIO(response.read())
        with zipfile.ZipFile(archive) as bundle:
            assert "submission.script" in bundle.namelist()
            assert "Reports.txt" not in bundle.namelist()
            script = bundle.read("submission.script").decode()
            assert "Reports.Filename = 'Reports.txt';" in script

        assert state.prepared_plan is not None
        horizon_s = state.prepared_plan.evaluation_horizon_s
        body, content_type = _multipart(
            {}, {"report": ("Reports.txt", _report_bytes(horizon_s))}
        )
        report_result = _post(url + "api/report", state.token, body, content_type)
        assert report_result["summary"]["rows"] == 2
        assert report_result["summary"]["validation"] == "passed"

        final = _post(
            url + "api/finalize", state.token, b"", "application/octet-stream"
        )
        with urlopen(url + final["url"], timeout=10) as response:
            archive = io.BytesIO(response.read())
        with zipfile.ZipFile(archive) as bundle:
            assert "submission.script" in bundle.namelist()
            assert "Reports.txt" in bundle.namelist()
    finally:
        server.shutdown()
        server.server_close()
        state.close()
        thread.join(timeout=5)
