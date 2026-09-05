# TASA Orbit V4.2 完整使用手冊

版本 4.2.2｜更新日期 2026-08-16｜macOS / Linux

## 一、重要規則

V4.2 依兩艘飛船初始軌道與四個評分係數，搜尋 1 至 3 次脈衝攔截方案，精確計算首次進入 5 km 的時間，並產生 GMAT `submission.script`。

主辦方會直接解析 Script 參數，不會替參賽者執行 Script；但參賽者仍須在自己的 GMAT 執行相同 Script，取得完整連續 `Reports.txt`。最後提交同一候選的 Script 與 Reports。

V4.2 新增物理種子、60 秒粗篩、提前終止、官方分數導向、精確首次進入根搜尋、finalists-only robustness、進度／耗時與 checkpoint 續跑。

## 二、安裝與啟動

解壓後，在專案根目錄執行：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
tasa-v4 --version
```

應顯示 `4.2.2`。不要用系統層級 pip，以免出現 Homebrew `externally-managed-environment`。

macOS 可直接雙擊 `launch_competition_day.command`。若被阻擋：

```bash
chmod +x launch_competition_day.command repair_install.command
./launch_competition_day.command
```

安裝損壞或出現 `No module named tasa_v4` 時執行：

```bash
./repair_install.command
```

若 Python 缺少 `_tkinter`，GUI 會自動改開本機瀏覽器版；也可手動執行：

```bash
tasa-v4 web-ui configs/current_competition.yaml
```

## 三、比賽日輸入

在 GUI / Web UI 輸入 Sat、Sat2 的 Epoch、RadPer、RadApo、INC、RAAN、AOP、TA、DryMass，以及 `kt`、`Ct (s)`、`kv`、`Cv (km/s)`。

內部換算：

```text
SMA = (RadApo + RadPer) / 2
ECC = (RadApo - RadPer) / (RadApo + RadPer)
```

Sat 與 Sat2 的 Epoch 必須一致。距離用 km、速度與 Delta-V 用 km/s、時間用秒、角度用度。

若直接編輯 YAML：

```yaml
score:
  kt: VALUE_FROM_ORGANIZER
  ct_s: VALUE_FROM_ORGANIZER
  kv: VALUE_FROM_ORGANIZER
  cv_km_s: VALUE_FROM_ORGANIZER
```

未填 `score` 仍可建立 Pareto 前緣，但不能視為正式總分排名。

## 四、先做快速測試

```bash
source .venv/bin/activate
tasa-v4 validate-config configs/competition_day.yaml
tasa-v4 optimize configs/competition_day.yaml \
  --output runs/quick --quick --no-refine
```

`--quick` 只確認安裝、設定、搜尋與輸出流程，不代表最佳成績。分行命令的每個未結束行尾都必須有反斜線；`--output` 不是獨立命令。

驗證既有候選：

```bash
tasa-v4 simulate configs/competition_day.yaml candidate.yaml
tasa-v4 simulate configs/competition_day.yaml candidate.yaml \
  --robust --scenarios 128
```

## 五、正式搜尋與續跑

```bash
tasa-v4 optimize configs/competition_day.yaml \
  --output runs/competition
```

加入一個或多個人工種子：

```bash
tasa-v4 optimize configs/competition_day.yaml \
  --output runs/competition \
  --seed-candidate candidates/good_1.yaml \
  --seed-candidate candidates/good_2.yaml
```

流程依序為：物理種子生成與粗篩、NSGA-II 名目搜尋、精確前緣重算、IPOPT 精修、finalists robustness、正式分數排序及輸出。

若中斷，重跑相同命令與同一 `--output`，會從相容 checkpoint 繼續。若要刻意忽略 checkpoint：

```bash
tasa-v4 optimize configs/competition_day.yaml \
  --output runs/competition --no-resume
```

不要在同一 output 目錄混用不同場景或分數係數。

### V4.2.1 邊界修正

最初的 4.2.0 可能在一燃燒 island 遇到 `evaluation_horizon_s = 0.0` 而中止。更新包已將最小有效 horizon 固定為正值並加入回歸測試。若舊執行已顯示「已儲存 NSGA-II epoch 1 checkpoint」，安裝修正版後以同一命令重跑，即可由 checkpoint 接續，不必刪除 `runs/competition`。

### V4.2.2 成功後自動截短 horizon

精確評估首次進入 5 km 後，輸出候選的 `evaluation_horizon_s` 會自動設為 `first_entry_time_s + 10 秒`，並移除首次進入後才會執行的燃燒。因此 GMAT 的 `Reports.txt` 不會再因 NSGA-II 原始 horizon 而多跑幾萬秒。

用舊 checkpoint 續跑也會自動重算少量精確前緣候選並套用此規則，不需刪除 NSGA-II checkpoint。對舊的 `Pxxx.yaml` 直接執行 `submission-generate`、`gmat-generate`或完整 `report-validate` 時，也會先做相同截短。

## 六、進度與輸出

終端機顯示各階段百分比及耗時。長跑時可查看：

- `progress.jsonl`：逐階段事件與耗時。
- `run_summary.json`：候選數、robustness 政策與最佳結果。
- `checkpoints/`：可續跑的物理種子、epoch、精確前緣、IPOPT 與 robustness 狀態。
- `pareto.csv` / `pareto.json`：Pareto 候選。
- `physical_seed_screening.csv`：物理種子粗篩結果。
- `candidates/Pxxx.yaml`：後續 GMAT 與提交用候選。

成功候選的 YAML 內會保留 `original_evaluation_horizon_s`、`first_entry_time_s`、`success_horizon_buffer_s: 10.0` 與 `horizon_compacted: true` 等 metadata，可用來確認已經套用截短。

四個半小時不等於必然當機；以 `progress.jsonl` 和 checkpoint 是否更新判斷。程序已停止時，修正原因後用同一命令續跑。

選候選時先確認：精確進入 5 km、正式分數高、robustness 達標、GMAT 驗證通過。不要只看最小距離。

## 七、GMAT 執行與 Reports

先產生 GMAT 執行包：

```bash
tasa-v4 submission-generate configs/competition_day.yaml \
  runs/competition/candidates/P001.yaml \
  --output runs/gmat_run_package
```

在 GMAT 開啟 `runs/gmat_run_package/submission.script`，按 Run 至少一次，等待 Mission Complete，確認新產生的 `Reports.txt` 有標題及多筆連續資料。Script 使用 `AppendToExistingFile = false`，但仍應核對檔案修改時間以免拿到舊檔。

完整驗證：

```bash
tasa-v4 report-validate /path/to/Reports.txt \
  --config configs/competition_day.yaml \
  --candidate runs/competition/candidates/P001.yaml
```

必須通過 26 欄順序、雙星逐列同步、時間不倒退、初始 Epoch／狀態吻合、最後一筆抵達完整 horizon、資料可重建首次進入 5 km 等檢查。

只執行 `tasa-v4 report-validate Reports.txt` 只是格式與距離摘要，不足以證明 Reports 對應本次 config 與 candidate。

## 八、建立最終提交包

```bash
tasa-v4 submission-generate configs/competition_day.yaml \
  runs/competition/candidates/P001.yaml \
  --report /path/to/Reports.txt \
  --output runs/formal_submission
```

最終包包含 `submission.script`、原始 `Reports.txt`、`candidate.json`、`competition_config.yaml`、`submission_manifest.json` 與 `Reports_summary.json`。

只有 GUI 顯示「通過：N 筆完整資料」或命令列完整驗證通過後才提交。不可混用 P001 的 Script 與 P002 的 Reports。

## 九、額外功能

單獨 IPOPT 精修：

```bash
tasa-v4 refine configs/competition_day.yaml candidate.yaml \
  --time-weight 0.5 --output runs/refined.yaml
```

Python-GMAT 交叉驗證：

```bash
export GMAT_EXECUTABLE="/path/to/GmatConsole"
tasa-v4 gmat-verify configs/competition_day.yaml candidate.yaml \
  --output runs/gmat-check
```

此驗證不能取代正式 Script 所產生的 Reports。

## 十、重要設定

- `physical_seeds.population_fraction: 0.70`：物理／人工種子及其鄰域占比上限，仍保留 Sobol 探索。
- `screening.step_s: 60`：只供粗篩；正式首次進入時間另用精確根搜尋。
- `robustness.screening_scenarios: 0`：保持為 0，禁止全候選 robustness。
- `optimization.final_robustness_candidates: 20`：只驗證 finalists。
- 成功後的輸出 horizon 固定保留首次進入後 10 秒，無需在 YAML 另外設定。
- `checkpoint.resume: true`：允許相容續跑。
- `optimization.workers`：建議不高於效能核心數。

正式搜尋太久時，可先降低 population、generations 或 epochs；不要先打開全候選 robustness，也不要立即增加到 4 至 6 次燃燒。

## 十一、常見問題

- `externally-managed-environment`：建立並啟動 `.venv`，不要使用 `--break-system-packages`。
- `No module named tasa_v4`：執行 `./repair_install.command`。
- GUI 未出現：改執行 `tasa-v4 web-ui ...`，終端機需保持開啟。
- Reports 前段相對距離為 0：不要用舊式 GMAT Variable 的 dX/dY/dZ/RelDist；V4.2 從雙星原生 Cartesian state 重建相對距離。
- Reports 不完整：通常是舊檔、未跑到完整 horizon、欄位不同步或候選不匹配；重新以本次 Script 執行，勿手工拼接。

## 十二、正式提交前清單

- [ ] `tasa-v4 --version` 顯示修正版。
- [ ] 當天 Sat、Sat2 資料與 Epoch 完全正確。
- [ ] 四個係數使用主辦方公告值。
- [ ] quick 流程通過。
- [ ] 正式搜尋完成或由 checkpoint 正常續跑完成。
- [ ] 候選精確進入 5 km，分數與 robustness 已檢查。
- [ ] 已用該候選生成並在 GMAT 執行新的 Script。
- [ ] Reports 完整驗證通過。
- [ ] Script 與 Reports 屬於同一候選。
- [ ] 保留 config、candidate、checkpoint 與最終包備份。

## 十三、命令速查

```text
tasa-v4 --version
tasa-v4 validate-config CONFIG
tasa-v4 simulate CONFIG CANDIDATE [--robust] [--scenarios N]
tasa-v4 optimize CONFIG --output DIR [--quick] [--no-refine]
                 [--run-gmat] [--no-resume]
                 [--seed-candidate YAML ...]
tasa-v4 refine CONFIG CANDIDATE --output YAML [--time-weight X]
tasa-v4 gmat-generate CONFIG CANDIDATE --output DIR
tasa-v4 gmat-verify CONFIG CANDIDATE --output DIR [--executable PATH]
tasa-v4 report-validate REPORT [--radius-km R]
                 [--config CONFIG --candidate CANDIDATE]
tasa-v4 submission-generate CONFIG CANDIDATE --output DIR [--report REPORT]
tasa-v4 gui [CONFIG]
tasa-v4 web-ui [CONFIG]
```

本手冊只適用更新後的 V4.2。舊版 `TASA_Orbit_V4_1_2_完整使用手冊.pdf` 不適用本流程。
