from __future__ import annotations

import json
from pathlib import Path
from tkinter import BOTH, END, LEFT, X, filedialog, messagebox
import tkinter as tk
from tkinter import scrolledtext, ttk

from .competition_day import (
    ModifiedKeplerianState,
    OfficialReport,
    config_with_competition_inputs,
    parse_official_report,
    validate_generated_report,
)
from .config import load_candidate, load_project_config, save_model_yaml
from .finalization import compact_successful_plan
from .models import CandidatePlan, ProjectConfig, ScoreConfig
from .submission import generate_submission_bundle


SPACECRAFT_FIELDS = [
    ("epoch_utc", "Epoch (UTCGregorian)"),
    ("rad_per_km", "RadPer (km)"),
    ("rad_apo_km", "RadApo (km)"),
    ("inc_deg", "INC (deg)"),
    ("raan_deg", "RAAN (deg)"),
    ("aop_deg", "AOP (deg)"),
    ("ta_deg", "TA (deg)"),
    ("dry_mass_kg", "DryMass (kg)"),
]


class CompetitionDayApp:
    def __init__(self, root: tk.Tk, config_path: str | Path):
        self.root = root
        self.config_path = Path(config_path).resolve()
        self.base_config = load_project_config(self.config_path)
        self.report: OfficialReport | None = None
        self.prepared_config: ProjectConfig | None = None
        self.prepared_plan: CandidatePlan | None = None
        self.variables: dict[str, tk.StringVar] = {}

        root.title("TASA Orbit V4｜競賽日設定與提交")
        root.geometry("1080x780")
        root.minsize(920, 680)
        self._build()
        self._load_config_to_form(self.base_config)

    def _build(self) -> None:
        header = ttk.Frame(self.root, padding=(14, 12, 14, 8))
        header.pack(fill=X)
        ttk.Label(
            header,
            text="競賽日設定與正式提交",
            font=("TkDefaultFont", 17, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            header,
            text=(
                "主辦方解析 Script；你仍須先在本機 GMAT 執行 Script，產生完整 Reports 後一併上傳。"
            ),
        ).pack(anchor="w", pady=(4, 0))

        notebook = ttk.Notebook(self.root)
        notebook.pack(fill=BOTH, expand=True, padx=14, pady=(0, 14))
        state_tab = ttk.Frame(notebook, padding=12)
        score_tab = ttk.Frame(notebook, padding=12)
        report_tab = ttk.Frame(notebook, padding=12)
        output_tab = ttk.Frame(notebook, padding=12)
        notebook.add(state_tab, text="1 兩艘飛船")
        notebook.add(score_tab, text="2 評分四係數")
        notebook.add(output_tab, text="3 產生 GMAT 執行包")
        notebook.add(report_tab, text="4 驗證 Reports／完成提交")
        self._build_spacecraft_tab(state_tab)
        self._build_score_tab(score_tab)
        self._build_output_tab(output_tab)
        self._build_report_tab(report_tab)

    def _build_spacecraft_tab(self, parent: ttk.Frame) -> None:
        ttk.Label(
            parent,
            text="以 ModifiedKeplerian 輸入；RadPer、RadApo 會轉成內部 SMA、ECC。",
        ).pack(anchor="w", pady=(0, 10))
        body = ttk.Frame(parent)
        body.pack(fill=BOTH, expand=True)
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)
        self._spacecraft_panel(body, "sat", "Sat（攔截者）", 0)
        self._spacecraft_panel(body, "sat2", "Sat2（目標）", 1)

    def _spacecraft_panel(
        self, parent: ttk.Frame, prefix: str, title: str, column: int
    ) -> None:
        frame = ttk.LabelFrame(parent, text=title, padding=12)
        frame.grid(row=0, column=column, sticky="nsew", padx=(0, 7) if column == 0 else (7, 0))
        frame.columnconfigure(1, weight=1)
        for row, (key, label) in enumerate(SPACECRAFT_FIELDS):
            ttk.Label(frame, text=label).grid(
                row=row, column=0, sticky="w", padx=(0, 12), pady=5
            )
            variable = tk.StringVar()
            self.variables[f"{prefix}_{key}"] = variable
            ttk.Entry(frame, textvariable=variable, width=30).grid(
                row=row, column=1, sticky="ew", pady=5
            )

    def _build_score_tab(self, parent: ttk.Frame) -> None:
        ttk.Label(
            parent,
            text="四個係數皆為正式輸出前必填；括號內是本程式使用的單位。",
        ).pack(anchor="w", pady=(0, 12))
        frame = ttk.LabelFrame(parent, text="官方評分係數", padding=16)
        frame.pack(fill=X)
        frame.columnconfigure(1, weight=1)
        fields = [
            ("score_kt", "kt", "時間項權重"),
            ("score_ct", "Ct (s)", "時間正規化常數"),
            ("score_kv", "kv", "ΔV 項權重"),
            ("score_cv", "Cv (km/s)", "ΔV 正規化常數"),
        ]
        for row, (key, label, description) in enumerate(fields):
            ttk.Label(frame, text=label, width=18).grid(row=row, column=0, sticky="w", pady=7)
            variable = tk.StringVar()
            self.variables[key] = variable
            ttk.Entry(frame, textvariable=variable, width=28).grid(
                row=row, column=1, sticky="ew", pady=7
            )
            ttk.Label(frame, text=description).grid(
                row=row, column=2, sticky="w", padx=(14, 0), pady=7
            )

    def _build_report_tab(self, parent: ttk.Frame) -> None:
        ttk.Label(
            parent,
            text=(
                "先在步驟 3 產生 submission.script，並在 GMAT 至少執行一次。"
                "再載入該次執行產生的 Reports.txt；程式會核對官方 26 欄、"
                "逐列雙星時間與完整任務區間。"
            ),
            wraplength=960,
        ).pack(anchor="w", pady=(0, 10))
        chooser = ttk.Frame(parent)
        chooser.pack(fill=X)
        self.report_path = tk.StringVar()
        ttk.Entry(chooser, textvariable=self.report_path).pack(
            side=LEFT, fill=X, expand=True
        )
        ttk.Button(chooser, text="選擇 Reports", command=self._choose_report).pack(
            side=LEFT, padx=(8, 0)
        )
        ttk.Button(chooser, text="載入並驗證", command=self._load_report).pack(
            side=LEFT, padx=(8, 0)
        )
        ttk.Button(
            parent,
            text="產生最終 Script＋Reports 提交包",
            command=self._finalize,
        ).pack(anchor="w", pady=10)
        self.report_summary = scrolledtext.ScrolledText(
            parent, height=22, wrap="word", state="disabled"
        )
        self.report_summary.pack(fill=BOTH, expand=True)

    def _build_output_tab(self, parent: ttk.Frame) -> None:
        form = ttk.Frame(parent)
        form.pack(fill=X)
        form.columnconfigure(1, weight=1)

        ttk.Label(form, text="候選 YAML").grid(row=0, column=0, sticky="w", pady=6)
        self.candidate_path = tk.StringVar()
        ttk.Entry(form, textvariable=self.candidate_path).grid(
            row=0, column=1, sticky="ew", padx=8, pady=6
        )
        ttk.Button(form, text="選擇", command=self._choose_candidate).grid(
            row=0, column=2, pady=6
        )

        ttk.Label(form, text="輸出資料夾").grid(row=1, column=0, sticky="w", pady=6)
        self.output_path = tk.StringVar(
            value=str(self.config_path.parent.parent / "runs" / "formal_submission")
        )
        ttk.Entry(form, textvariable=self.output_path).grid(
            row=1, column=1, sticky="ew", padx=8, pady=6
        )
        ttk.Button(form, text="選擇", command=self._choose_output).grid(
            row=1, column=2, pady=6
        )

        buttons = ttk.Frame(parent)
        buttons.pack(fill=X, pady=(12, 8))
        ttk.Button(
            buttons, text="儲存競賽設定 YAML", command=self._save_config
        ).pack(side=LEFT)
        ttk.Button(
            buttons, text="產生可執行的 submission.script", command=self._generate
        ).pack(side=LEFT, padx=(10, 0))

        ttk.Label(
            parent,
            text=(
                "先產生 gmat_run_package，在 GMAT 開啟 submission.script 並 Run；"
                "GMAT 會在執行目錄產生 Reports.txt。之後到步驟 4 驗證並完成提交。"
            ),
            wraplength=960,
        ).pack(anchor="w", pady=(0, 8))
        self.log = scrolledtext.ScrolledText(parent, height=20, wrap="word")
        self.log.pack(fill=BOTH, expand=True)

    def _load_config_to_form(self, config: ProjectConfig) -> None:
        sat = ModifiedKeplerianState.from_spacecraft(
            config.scenario.chaser, config.scenario.epoch_utc
        )
        sat2 = ModifiedKeplerianState.from_spacecraft(
            config.scenario.target, config.scenario.epoch_utc
        )
        self._state_to_form("sat", sat)
        self._state_to_form("sat2", sat2)
        score_values = (
            (config.score.kt, config.score.ct_s, config.score.kv, config.score.cv_km_s)
            if config.score is not None
            else ("", "", "", "")
        )
        for key, value in zip(
            ("score_kt", "score_ct", "score_kv", "score_cv"), score_values
        ):
            self.variables[key].set(str(value))

    def _state_to_form(self, prefix: str, state: ModifiedKeplerianState) -> None:
        for key, _ in SPACECRAFT_FIELDS:
            value = getattr(state, key)
            self.variables[f"{prefix}_{key}"].set(str(value))

    def _form_state(self, prefix: str) -> ModifiedKeplerianState:
        values = self.variables
        return ModifiedKeplerianState(
            epoch_utc=values[f"{prefix}_epoch_utc"].get().strip(),
            rad_per_km=float(values[f"{prefix}_rad_per_km"].get()),
            rad_apo_km=float(values[f"{prefix}_rad_apo_km"].get()),
            inc_deg=float(values[f"{prefix}_inc_deg"].get()),
            raan_deg=float(values[f"{prefix}_raan_deg"].get()),
            aop_deg=float(values[f"{prefix}_aop_deg"].get()),
            ta_deg=float(values[f"{prefix}_ta_deg"].get()),
            dry_mass_kg=float(values[f"{prefix}_dry_mass_kg"].get()),
        )

    def _form_config(self) -> ProjectConfig:
        score = ScoreConfig(
            kt=float(self.variables["score_kt"].get()),
            ct_s=float(self.variables["score_ct"].get()),
            kv=float(self.variables["score_kv"].get()),
            cv_km_s=float(self.variables["score_cv"].get()),
        )
        return config_with_competition_inputs(
            self.base_config, self._form_state("sat"), self._form_state("sat2"), score
        )

    def _choose_report(self) -> None:
        selected = filedialog.askopenfilename(
            title="選擇本次 GMAT 產生的 Reports",
            filetypes=[("Reports", "*.csv *.txt *.report"), ("所有檔案", "*")],
        )
        if selected:
            self.report_path.set(selected)

    def _load_report(self) -> None:
        if self.prepared_config is None or self.prepared_plan is None:
            messagebox.showwarning("順序錯誤", "請先在步驟 3 產生並執行 submission.script")
            return
        try:
            self.report = parse_official_report(self.report_path.get())
            summary = validate_generated_report(
                self.report, self.prepared_config, self.prepared_plan
            )
        except Exception as error:
            messagebox.showerror("Reports 驗證失敗", str(error))
            return
        self.report_summary.configure(state="normal")
        self.report_summary.delete("1.0", END)
        self.report_summary.insert("1.0", json.dumps(summary, ensure_ascii=False, indent=2))
        self.report_summary.configure(state="disabled")
        messagebox.showinfo("Reports 驗證完成", f"通過：{self.report.row_count} 筆完整資料")

    def _choose_candidate(self) -> None:
        selected = filedialog.askopenfilename(
            title="選擇候選 YAML", filetypes=[("YAML", "*.yaml *.yml"), ("所有檔案", "*")]
        )
        if selected:
            self.candidate_path.set(selected)

    def _choose_output(self) -> None:
        selected = filedialog.askdirectory(title="選擇正式提交輸出資料夾")
        if selected:
            self.output_path.set(selected)

    def _save_config(self) -> None:
        try:
            config = self._form_config()
        except Exception as error:
            messagebox.showerror("設定不完整", str(error))
            return
        selected = filedialog.asksaveasfilename(
            title="儲存競賽設定",
            initialfile="competition_day.yaml",
            defaultextension=".yaml",
            filetypes=[("YAML", "*.yaml *.yml")],
        )
        if not selected:
            return
        save_model_yaml(config, selected)
        self.base_config = config
        self._append_log(f"已儲存設定：{Path(selected).resolve()}")

    def _generate(self) -> None:
        try:
            config = self._form_config()
            plan = load_candidate(self.candidate_path.get())
            plan, _ = compact_successful_plan(config, plan)
            output = Path(self.output_path.get()) / "gmat_run_package"
            outputs = generate_submission_bundle(
                config,
                plan,
                output,
            )
        except Exception as error:
            messagebox.showerror("產生失敗", str(error))
            return
        self.base_config = config
        self.prepared_config = config
        self.prepared_plan = plan
        self.report = None
        self._append_log("GMAT 執行包已產生：")
        for key, path in outputs.items():
            self._append_log(f"  {key}: {path}")
        messagebox.showinfo(
            "下一步：執行 GMAT",
            "請在 GMAT 開啟 gmat_run_package/submission.script 並至少 Run 一次，"
            "產生 Reports.txt 後到步驟 4。",
        )

    def _finalize(self) -> None:
        if self.prepared_config is None or self.prepared_plan is None:
            messagebox.showwarning("尚未產生 Script", "請先完成步驟 3")
            return
        if self.report is None:
            messagebox.showwarning("尚未通過 Reports", "請先載入並驗證本次 Reports")
            return
        try:
            output = Path(self.output_path.get()) / "formal_submission"
            outputs = generate_submission_bundle(
                self.prepared_config,
                self.prepared_plan,
                output,
                official_report=self.report,
            )
        except Exception as error:
            messagebox.showerror("產生失敗", str(error))
            return
        self._append_log("最終提交包已產生（上傳 submission.script 與 Reports.txt）：")
        for key, path in outputs.items():
            self._append_log(f"  {key}: {path}")
        messagebox.showinfo("完成", "請將 submission.script 與 Reports.txt 一併上傳主辦方")

    def _append_log(self, message: str) -> None:
        self.log.insert(END, message + "\n")
        self.log.see(END)


def launch_gui(config_path: str | Path) -> None:
    root = tk.Tk()
    CompetitionDayApp(root, config_path)
    root.mainloop()
