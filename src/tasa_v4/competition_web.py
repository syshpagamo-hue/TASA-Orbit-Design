from __future__ import annotations

import json
import secrets
import shutil
import tempfile
import threading
import webbrowser
from dataclasses import asdict
from email import policy
from email.parser import BytesParser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

import yaml

from .competition_day import (
    ModifiedKeplerianState,
    OfficialReport,
    config_with_competition_inputs,
    parse_official_report,
    validate_generated_report,
)
from .config import load_candidate, load_project_config
from .finalization import compact_successful_plan
from .models import CandidatePlan, ProjectConfig, ScoreConfig
from .submission import generate_submission_bundle


MAX_UPLOAD_BYTES = 256 * 1024 * 1024


class _WebState:
    def __init__(self, config_path: str | Path):
        self.base_config = load_project_config(config_path)
        self.report: OfficialReport | None = None
        self.prepared_config: ProjectConfig | None = None
        self.prepared_plan: CandidatePlan | None = None
        self.token = secrets.token_urlsafe(24)
        self.temporary = tempfile.TemporaryDirectory(prefix="tasa-v4-web-")
        self.root = Path(self.temporary.name)
        self.downloads: dict[str, Path] = {}
        self.bundle_counter = 0

    def close(self) -> None:
        self.temporary.cleanup()


def _initial_payload(config: ProjectConfig) -> dict[str, object]:
    sat = ModifiedKeplerianState.from_spacecraft(
        config.scenario.chaser, config.scenario.epoch_utc
    )
    sat2 = ModifiedKeplerianState.from_spacecraft(
        config.scenario.target, config.scenario.epoch_utc
    )
    score = config.score
    return {
        "sat": asdict(sat),
        "sat2": asdict(sat2),
        "score": {
            "kt": score.kt if score else "",
            "ct_s": score.ct_s if score else "",
            "kv": score.kv if score else "",
            "cv_km_s": score.cv_km_s if score else "",
        },
    }


def _config_from_payload(base: ProjectConfig, payload: dict[str, object]) -> ProjectConfig:
    try:
        sat = ModifiedKeplerianState(**payload["sat"])
        sat2 = ModifiedKeplerianState(**payload["sat2"])
        score_payload = payload["score"]
        score = ScoreConfig(
            kt=float(score_payload["kt"]),
            ct_s=float(score_payload["ct_s"]),
            kv=float(score_payload["kv"]),
            cv_km_s=float(score_payload["cv_km_s"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"競賽設定欄位不完整：{error}") from error
    return config_with_competition_inputs(base, sat, sat2, score)


def _page(initial: dict[str, object], token: str) -> str:
    initial_json = json.dumps(initial, ensure_ascii=False).replace("</", "<\\/")
    token_json = json.dumps(token)
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>TASA Orbit V4｜競賽日設定</title>
<style>
:root{{--ink:#12263a;--muted:#60758a;--blue:#1f6feb;--line:#d9e3ed;--bg:#f3f7fb;--ok:#177245;--bad:#b42318}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
header{{background:#102b46;color:white;padding:24px max(24px,calc((100% - 1120px)/2))}} h1{{margin:0;font-size:25px}} header p{{margin:5px 0 0;color:#c9d9e8}}
.badge{{display:inline-block;margin-top:10px;padding:4px 9px;border-radius:99px;background:#dff6ea;color:#155d3a;font-size:12px;font-weight:700}}
main{{max-width:1120px;margin:22px auto;padding:0 18px 48px}} .card{{background:white;border:1px solid var(--line);border-radius:14px;padding:20px;margin:14px 0;box-shadow:0 2px 10px #2342}}
h2{{margin:0 0 12px;font-size:19px}} h3{{margin:0 0 10px;font-size:16px}} .hint{{color:var(--muted);margin:-5px 0 15px}}
.grid2{{display:grid;grid-template-columns:1fr 1fr;gap:16px}} .sat{{border:1px solid var(--line);border-radius:11px;padding:15px}}
.field{{display:grid;grid-template-columns:145px 1fr;gap:8px;align-items:center;margin:8px 0}} label{{font-weight:600}}
input{{width:100%;padding:9px 10px;border:1px solid #b9c7d4;border-radius:7px;font:inherit}} input:focus{{outline:2px solid #b7d3ff;border-color:var(--blue)}}
button,.download{{display:inline-block;border:0;border-radius:8px;padding:10px 14px;background:var(--blue);color:white;font:600 14px inherit;text-decoration:none;cursor:pointer;margin:4px 6px 4px 0}}
button.secondary{{background:#e8eef5;color:var(--ink)}} button.good{{background:var(--ok)}} button.danger{{background:#8c2f24}}
pre{{white-space:pre-wrap;background:#f7f9fc;border:1px solid var(--line);border-radius:9px;padding:13px;max-height:270px;overflow:auto}}
.status{{min-height:24px;margin-top:8px;font-weight:600}} .status.ok{{color:var(--ok)}} .status.bad{{color:var(--bad)}}
.score-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px}} .score-grid label{{display:block;margin-bottom:4px}}
@media(max-width:760px){{.grid2,.score-grid{{grid-template-columns:1fr}} .field{{grid-template-columns:1fr}}}}
</style>
</head>
<body>
<header><h1>TASA Orbit V4｜競賽日設定與正式提交</h1><p>主辦方會解析 Script，但你仍須先在本機 GMAT 執行 Script，產生完整 Reports 後一併上傳。</p><span class="badge">瀏覽器版・不需要 Tkinter</span></header>
<main>
<section class="card"><h2>1　兩艘飛船</h2><p class="hint">輸入 ModifiedKeplerian；RadPer、RadApo 會轉為內部 SMA、ECC。</p><div id="sat-grid" class="grid2"></div></section>
<section class="card"><h2>2　官方評分四係數</h2><div class="score-grid">
<div><label for="kt">kt</label><input id="kt" type="number" step="any"></div>
<div><label for="ct_s">Ct (s)</label><input id="ct_s" type="number" step="any"></div>
<div><label for="kv">kv</label><input id="kv" type="number" step="any"></div>
<div><label for="cv_km_s">Cv (km/s)</label><input id="cv_km_s" type="number" step="any"></div>
</div></section>
<section class="card"><h2>3　產生 GMAT 執行包</h2><p class="hint">選擇最佳化輸出的 candidates/Pxxx.yaml，下載後解壓縮，並在 GMAT 開啟及執行 submission.script。它會輸出 Reports.txt。</p>
<input id="candidate-file" type="file" accept=".yaml,.yml"><br>
<button class="secondary" onclick="saveConfig()">下載競賽設定 YAML</button><button class="good" onclick="generateBundle()">產生 GMAT 執行包</button>
<div id="output-status" class="status"></div><div id="downloads"></div></section>
<section class="card"><h2>4　驗證你跑出的 Reports 並完成提交</h2><p class="hint">先完成步驟 3 並在 GMAT 至少執行一次。再上傳該 Script 產生的 Reports.txt；工具會檢查官方 26 欄、完整時間區間與雙星逐列對齊。</p>
<input id="report-file" type="file" accept=".csv,.txt,.report"><br>
<button onclick="uploadReport()">驗證 GMAT 產生的 Reports</button><button class="good" onclick="finalizeBundle()">產生最終 Script＋Reports 提交包</button>
<div id="report-status" class="status"></div><pre id="report-summary">尚未驗證本次 GMAT Reports</pre><div id="final-downloads"></div></section>
<section class="card"><h2>結束</h2><p class="hint">下載完成後可關閉本頁，再按下方按鈕停止背景伺服器；終端機也可按 Control-C。</p><button class="danger" onclick="shutdown()">停止競賽日視窗</button></section>
</main>
<script>
const initial={initial_json}; const TOKEN={token_json};
const fields=[['epoch_utc','Epoch (UTCGregorian)','text'],['rad_per_km','RadPer (km)','number'],['rad_apo_km','RadApo (km)','number'],['inc_deg','INC (deg)','number'],['raan_deg','RAAN (deg)','number'],['aop_deg','AOP (deg)','number'],['ta_deg','TA (deg)','number'],['dry_mass_kg','DryMass (kg)','number']];
function satCard(prefix,title,data){{let html=`<div class="sat"><h3>${{title}}</h3>`;for(const [key,label,type] of fields){{html+=`<div class="field"><label for="${{prefix}}_${{key}}">${{label}}</label><input id="${{prefix}}_${{key}}" type="${{type}}" ${{type==='number'?'step="any"':''}} value="${{data[key]}}"></div>`}}return html+'</div>'}}
document.getElementById('sat-grid').innerHTML=satCard('sat','Sat（攔截者）',initial.sat)+satCard('sat2','Sat2（目標）',initial.sat2);
for(const key of ['kt','ct_s','kv','cv_km_s'])document.getElementById(key).value=initial.score[key];
function state(prefix){{const out={{}};for(const [key,,type] of fields){{const value=document.getElementById(`${{prefix}}_${{key}}`).value;out[key]=type==='number'?Number(value):value}}return out}}
function payload(){{return {{sat:state('sat'),sat2:state('sat2'),score:{{kt:document.getElementById('kt').value,ct_s:document.getElementById('ct_s').value,kv:document.getElementById('kv').value,cv_km_s:document.getElementById('cv_km_s').value}}}}}}
function status(id,text,ok){{const node=document.getElementById(id);node.textContent=text;node.className='status '+(ok?'ok':'bad')}}
async function api(path,options={{}}){{options.headers=Object.assign({{}},options.headers||{{}},{{'X-TASA-Token':TOKEN}});const response=await fetch(path,options);const data=await response.json();if(!response.ok)throw new Error(data.error||'操作失敗');return data}}
async function uploadReport(){{try{{const file=document.getElementById('report-file').files[0];if(!file)throw new Error('請先選擇由本次 submission.script 產生的 Reports.txt');const form=new FormData();form.append('report',file);status('report-status','正在驗證本機 GMAT 輸出…',true);const data=await api('api/report',{{method:'POST',body:form}});document.getElementById('report-summary').textContent=JSON.stringify(data.summary,null,2);status('report-status',`通過：${{data.summary.rows}} 筆完整資料`,true)}}catch(e){{status('report-status',e.message,false)}}}}
async function saveConfig(){{try{{const data=await api('api/config',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(payload())}});addDownload('downloads',data.url,data.filename);status('output-status','競賽設定已產生',true)}}catch(e){{status('output-status',e.message,false)}}}}
async function generateBundle(){{try{{const file=document.getElementById('candidate-file').files[0];if(!file)throw new Error('請先選擇候選 YAML');const form=new FormData();form.append('payload',JSON.stringify(payload()));form.append('candidate',file);status('output-status','正在產生可執行 submission.script…',true);const data=await api('api/generate',{{method:'POST',body:form}});addDownload('downloads',data.url,data.filename);document.getElementById('report-summary').textContent='尚未驗證本次 GMAT Reports';status('report-status','請解壓並在 GMAT 執行 submission.script，之後再上傳 Reports.txt',true);status('output-status','GMAT 執行包已完成；下一步必須執行 Script',true)}}catch(e){{status('output-status',e.message,false)}}}}
async function finalizeBundle(){{try{{status('report-status','正在建立最終提交包…',true);const data=await api('api/finalize',{{method:'POST'}});addDownload('final-downloads',data.url,data.filename);status('report-status',`完成：${{data.rows}} 筆完整資料，請上傳 submission.script 與 Reports.txt`,true)}}catch(e){{status('report-status',e.message,false)}}}}
function addDownload(target,url,name){{const a=document.createElement('a');a.className='download';a.href=url;a.textContent='下載 '+name;document.getElementById(target).appendChild(a)}}
async function shutdown(){{try{{await api('api/shutdown',{{method:'POST'}});document.body.innerHTML='<main><section class="card"><h2>競賽日視窗已停止</h2><p>可以關閉此頁。</p></section></main>'}}catch(e){{alert(e.message)}}}}
</script>
</body></html>"""


class _Handler(BaseHTTPRequestHandler):
    state: _WebState

    def log_message(self, format: str, *args: object) -> None:
        return

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: dict[str, object]) -> None:
        self._send(
            status,
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8",
        )

    def _authorized(self) -> bool:
        return secrets.compare_digest(
            self.headers.get("X-TASA-Token", ""), self.state.token
        )

    def _body(self) -> bytes:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise ValueError("Content-Length 無效") from error
        if length <= 0 or length > MAX_UPLOAD_BYTES:
            raise ValueError("上傳資料為空或超過 256 MiB")
        return self.rfile.read(length)

    def _multipart(self) -> tuple[dict[str, str], dict[str, tuple[str, bytes]]]:
        content_type = self.headers.get("Content-Type", "")
        if not content_type.startswith("multipart/form-data"):
            raise ValueError("需要 multipart/form-data")
        message = BytesParser(policy=policy.default).parsebytes(
            b"Content-Type: "
            + content_type.encode("ascii")
            + b"\r\nMIME-Version: 1.0\r\n\r\n"
            + self._body()
        )
        fields: dict[str, str] = {}
        files: dict[str, tuple[str, bytes]] = {}
        for part in message.iter_parts():
            name = part.get_param("name", header="content-disposition")
            if not name:
                continue
            content = part.get_payload(decode=True) or b""
            filename = part.get_filename()
            if filename:
                files[name] = (Path(filename).name, content)
            else:
                fields[name] = content.decode("utf-8")
        return fields, files

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        root = f"/{self.state.token}/"
        if path == root:
            body = _page(
                _initial_payload(self.state.base_config), self.state.token
            ).encode("utf-8")
            self._send(HTTPStatus.OK, body, "text/html; charset=utf-8")
            return
        prefix = root + "download/"
        if path.startswith(prefix):
            name = unquote(path[len(prefix) :])
            source = self.state.downloads.get(name)
            if source is None or not source.is_file():
                self._json(HTTPStatus.NOT_FOUND, {"error": "下載檔不存在"})
                return
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Disposition", f'attachment; filename="{name}"')
            self.send_header("Content-Length", str(source.stat().st_size))
            self.end_headers()
            with source.open("rb") as stream:
                shutil.copyfileobj(stream, self.wfile)
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": "頁面不存在"})

    def do_POST(self) -> None:
        root = f"/{self.state.token}/api/"
        path = urlparse(self.path).path
        if not path.startswith(root) or not self._authorized():
            self._json(HTTPStatus.FORBIDDEN, {"error": "未授權的本機請求"})
            return
        action = path[len(root) :]
        try:
            if action == "report":
                self._post_report()
            elif action == "config":
                self._post_config()
            elif action == "generate":
                self._post_generate()
            elif action == "finalize":
                self._post_finalize()
            elif action == "shutdown":
                self._json(HTTPStatus.OK, {"ok": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
            else:
                self._json(HTTPStatus.NOT_FOUND, {"error": "功能不存在"})
        except Exception as error:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(error)})

    def _post_report(self) -> None:
        if self.state.prepared_config is None or self.state.prepared_plan is None:
            raise ValueError("請先完成步驟 3，產生並執行本次 submission.script")
        _, files = self._multipart()
        if "report" not in files:
            raise ValueError("未收到 Reports 檔案")
        filename, content = files["report"]
        destination = self.state.root / f"uploaded_{filename}"
        destination.write_bytes(content)
        report = parse_official_report(destination)
        summary = validate_generated_report(
            report, self.state.prepared_config, self.state.prepared_plan
        )
        self.state.report = report
        self._json(
            HTTPStatus.OK,
            {"summary": summary},
        )

    def _post_config(self) -> None:
        content_type = self.headers.get("Content-Type", "")
        if not content_type.startswith("application/json"):
            raise ValueError("設定資料格式錯誤")
        payload = json.loads(self._body().decode("utf-8"))
        config = _config_from_payload(self.state.base_config, payload)
        name = "competition_day.yaml"
        destination = self.state.root / name
        destination.write_text(
            yaml.safe_dump(
                config.model_dump(mode="json", exclude_none=True),
                allow_unicode=True,
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        self.state.downloads[name] = destination
        self._json(
            HTTPStatus.OK,
            {"filename": name, "url": f"download/{name}"},
        )

    def _post_generate(self) -> None:
        fields, files = self._multipart()
        if "payload" not in fields or "candidate" not in files:
            raise ValueError("缺少競賽設定或候選 YAML")
        config = _config_from_payload(
            self.state.base_config, json.loads(fields["payload"])
        )
        candidate_name, candidate_content = files["candidate"]
        candidate_path = self.state.root / f"uploaded_{candidate_name}"
        candidate_path.write_bytes(candidate_content)
        candidate = load_candidate(candidate_path)
        candidate, _ = compact_successful_plan(config, candidate)
        self.state.prepared_config = config
        self.state.prepared_plan = candidate
        self.state.report = None
        self.state.bundle_counter += 1
        bundle_name = f"gmat_run_package_{self.state.bundle_counter:02d}"
        bundle_directory = self.state.root / bundle_name
        generate_submission_bundle(
            config,
            candidate,
            bundle_directory,
        )
        archive = Path(
            shutil.make_archive(
                str(self.state.root / bundle_name), "zip", bundle_directory
            )
        )
        self.state.downloads[archive.name] = archive
        self._json(
            HTTPStatus.OK,
            {"filename": archive.name, "url": f"download/{archive.name}"},
        )

    def _post_finalize(self) -> None:
        if self.state.prepared_config is None or self.state.prepared_plan is None:
            raise ValueError("尚未產生本次 GMAT 執行包")
        if self.state.report is None:
            raise ValueError("尚未上傳並通過本次 GMAT 產生的 Reports")
        bundle_name = f"formal_submission_{self.state.bundle_counter:02d}"
        bundle_directory = self.state.root / bundle_name
        generate_submission_bundle(
            self.state.prepared_config,
            self.state.prepared_plan,
            bundle_directory,
            official_report=self.state.report,
        )
        archive = Path(
            shutil.make_archive(
                str(self.state.root / bundle_name), "zip", bundle_directory
            )
        )
        self.state.downloads[archive.name] = archive
        self._json(
            HTTPStatus.OK,
            {
                "filename": archive.name,
                "url": f"download/{archive.name}",
                "rows": self.state.report.row_count,
            },
        )


def _make_server(
    config_path: str | Path,
) -> tuple[ThreadingHTTPServer, _WebState, str]:
    state = _WebState(config_path)
    handler = type("TasaCompetitionHandler", (_Handler,), {"state": state})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    host, port = server.server_address
    url = f"http://{host}:{port}/{state.token}/"
    return server, state, url


def launch_web_ui(config_path: str | Path) -> None:
    server, state, url = _make_server(config_path)
    print("已啟動不需要 Tkinter 的瀏覽器版競賽日視窗。")
    print(f"若瀏覽器沒有自動開啟，請前往：{url}")
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        state.close()
