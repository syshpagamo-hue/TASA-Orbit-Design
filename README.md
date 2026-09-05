# TASA Orbit V4.2 競賽級攔截最佳化器

完整中文操作說明：`docs/TASA_Orbit_V4_2_完整使用手冊.pdf`。整合包不再附帶舊版 V4.1.2 手冊，以免誤用舊流程。

這個專案把競賽日資料輸入、全域 Pareto 搜尋、局部軌跡最佳化、穩健性驗證、固定格式正式提交與 GMAT 內部重現整合成一條可追溯流程。所有時間使用秒、距離使用 km、速度與 ΔV 使用 km/s。

主辦方的正式評分模式是**直接解析參賽者提交的 `.script` 參數，不替參賽者執行腳本**。但是參賽者必須先在本機 GMAT 執行同一份 `submission.script` 至少一次，產生完整連續 `Reports.txt`，最後將 **Script 與 Reports 一起提交**。

## 已實作

- CasADi + IPOPT 稀疏 multiple shooting
- CasADi 自動微分 Jacobian／Hessian
- 多圈 Lambert、升／降軌相位、Hohmann、節點與高遠點物理種子
- 60 秒名目粗篩、hidden-crossing Hermite 檢查與可設定提前終止
- NSGA-II 正式分數－時間－ΔV Pareto 搜尋
- 多核心、多 seed、分 epoch migration 的 island 搜尋
- Sobol 全域初始探索與物理種子鄰域擾動
- 每個 epoch 原子 checkpoint、各階段耗時與 `progress.jsonl`
- Sobol common-random-number 擾動；只對最後 finalists 執行 certification
- 二體 universal-variable 快速傳播與首次 `RelDist <= 5 km` 根搜尋
- GMAT VNB 脈衝腳本產生、命令列執行、軌跡解析與 Python 交叉驗證
- `TrajectoryReport.csv`／`FinalSummary.csv` 分離，禁止舊資料追加
- 競賽日圖形視窗：兩艘飛船 ModifiedKeplerian 設定與四個評分係數
- 本機 GMAT 所產生之官方 26 欄 Reports：完整區間、時間連續性與雙星同步檢查
- 穩定小數格式的 `submission.script`、manifest 與完整提交包
- 成功後自動將候選 horizon 截為首次進入 5 km 後 10 秒，避免產生數萬秒的 Reports

## 安裝（macOS／Linux）

請使用虛擬環境，這也會避開 Homebrew 的 `externally-managed-environment` 錯誤：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
tasa-v4 --version
```

## 競賽日視窗（建議）

```bash
tasa-v4 gui configs/current_competition.yaml
```

macOS 也可直接雙擊專案根目錄的 `launch_competition_day.command`；第一次會建立 `.venv` 並安裝套件，之後直接開啟視窗。

V4.2 起，啟動器每次都會檢查 `tasa_v4`、CasADi 與 pymoo 是否能載入。即使 `.venv` 資料夾仍存在，但專案曾搬移、改名或安裝連結失效，也會用 `dist` 內附的 wheel 自動修復，不再只檢查 `.venv/bin/python` 是否存在。

若 Homebrew Python 沒有 `_tkinter`（Python 3.14 常見），V4.1.3 會自動改開瀏覽器版競賽日視窗，功能和輸出相同，不需要另外安裝 Tcl/Tk。也可以明確啟動：

```bash
tasa-v4 web-ui configs/current_competition.yaml
```

瀏覽器版只監聽本機 `127.0.0.1`，每次啟動使用隨機網址；正式提交包會以 ZIP 下載。終端機需保持開啟，完成後可在網頁按「停止競賽日視窗」。

依頁籤順序操作：

1. 輸入 Sat、Sat2 的 Epoch、RadPer、RadApo、INC、RAAN、AOP、TA、DryMass。
2. 輸入 `kt`、`Ct (s)`、`kv`、`Cv (km/s)`。
3. 選擇最佳化候選 YAML，產生並下載 GMAT 執行包。
4. 解壓執行包，在 GMAT 開啟 `submission.script` 並按 Run 至少一次；確認產生 `Reports.txt`。
5. 回到工具，上傳這份由你本機 GMAT 跑出的 `Reports.txt`。看到「通過：N 筆完整資料」後，產生最終提交包。
6. 向主辦方上傳最終包內的 `submission.script` 與 `Reports.txt`。

命令列也可直接驗證 Reports：

```bash
tasa-v4 report-validate /path/to/Reports.txt \
  --config configs/current_competition.yaml \
  --candidate runs/competition/candidates/P001.yaml
```

## 先驗證目前場景與舊候選

```bash
tasa-v4 validate-config configs/current_competition.yaml
tasa-v4 simulate configs/current_competition.yaml examples/legacy_candidate_unverified.yaml
tasa-v4 simulate configs/current_competition.yaml examples/legacy_candidate_unverified.yaml --robust --scenarios 128
```

`legacy_candidate_unverified.yaml` 只是把上一版候選帶進 V4，名稱刻意標示 `unverified`；它不會因為以前某一終點距離為 1 km 就被視為已證明首次進入時間。

## 快速煙霧測試

```bash
tasa-v4 optimize configs/current_competition.yaml \
  --output runs/quick --quick --no-refine \
  --seed-candidate examples/legacy_candidate_unverified.yaml
```

命令可以寫成一行；若分成多行，每一個尚未結束的行尾都必須有反斜線 `\`。`--output` 不是獨立指令，不能單獨貼到終端機。

若看到以下錯誤：

```text
ModuleNotFoundError: No module named 'tasa_v4'
```

可直接雙擊 `repair_install.command`。這會自動選取 `dist` 中最新版 wheel，無須手填版本號。

```bash
source .venv/bin/activate
./repair_install.command
```

這項修復只重建虛擬環境內的套件，不會刪除 `configs`、`runs`、候選 YAML 或 Reports。

## 正式搜尋

```bash
tasa-v4 optimize configs/current_competition.yaml \
  --output runs/competition \
  --seed-candidate examples/legacy_candidate_unverified.yaml
```

V4.2 會先依當天兩船狀態自動建立物理種子，再以便宜模型粗篩。預設初代最多 70% 來自物理／人工種子及其鄰域，至少 30% 保留給 scrambled Sobol 全域探索。

`--seed-candidate` 可重複使用；人工 seed 會保留精確原解並優先注入對應脈衝數的 islands。它與自動物理種子並存，不會互相覆蓋。

搜尋順序：

1. 多圈 Lambert、升／降軌相位、Hohmann、節點、高遠點種子。
2. 60 秒粗篩與提前終止，保留各策略代表與前段候選。
3. NSGA-II 只跑名目粗模型；不對每一個候選跑 8 個 robustness 情境。
4. Pareto 前緣用精確 universal-variable 傳播與 root finding 重算首次 5 km。
5. 成功候選的 horizon 自動改為 `first_entry_time_s + 10 秒`，移除進入後燃燒。
6. IPOPT 精修少量候選。
7. 只對正式分數最高的最後 20 組執行 128 情境 certification。
8. 依正式總分排序；同分依最小距離、總 ΔV、完成時間排序。

若搜尋中斷，直接重跑同一條指令與同一個 `--output`，會自動從相容 checkpoint 繼續。若要刻意忽略舊 checkpoint 重新搜尋：

```bash
tasa-v4 optimize configs/current_competition.yaml \
  --output runs/competition --no-resume
```

輸出包含：

- `pareto.csv`：可直接檢視的前緣
- `pareto.json`：完整候選、名目與穩健指標
- `physical_seed_screening.csv`：物理種子的粗篩結果與是否保留
- `progress.jsonl`：各階段開始、完成、百分比與耗時事件
- `run_summary.json`：耗時、候選數、robustness 政策與最佳結果
- `checkpoints/`：物理種子、NSGA-II epoch、精確前緣、IPOPT 與 robustness 續跑資料
- `candidates/Pxxx.yaml`：可直接在競賽日視窗選取的個別候選
- `config_snapshot.yaml`：可重現設定
- `gmat/Pxxx/verify.script`：每個候選的獨立 GMAT 驗證腳本
- `gmat/Pxxx/candidate.json`：候選與輸出欄位 manifest
- `submission/Pxxx/submission.script`：若四係數已填，為主辦方可直接抓參數的正式腳本

## 命令列產生 GMAT 執行包與最終提交包

第一階段先產生可執行 Script（此時尚無 Reports）：

```bash
tasa-v4 submission-generate configs/current_competition.yaml \
  runs/competition/candidates/P001.yaml \
  --output runs/gmat_run_package
```

在 GMAT 開啟 `runs/gmat_run_package/submission.script` 並執行。Script 的 `ReportFile` 會以官方 26 欄連續寫出 `Reports.txt`，且 `AppendToExistingFile = false`，每次 Run 會重建本次結果。

成功候選在這一步也會再檢查一次 horizon；即使輸入舊版 `Pxxx.yaml`，產生的正式 Script 也只會跑到首次進入後 10 秒。

第二階段，用剛才跑出的 Reports 建立最終包：

```bash
tasa-v4 submission-generate configs/current_competition.yaml \
  runs/competition/candidates/P001.yaml \
  --report /path/to/Reports.txt \
  --output runs/formal_submission
```

`submission.script` 固定使用 `ModifiedKeplerian`、`Local VNB` 與 12 位小數；脈衝的絕對時間、三軸 ΔV 及四個評分係數也會寫入明確的機器可讀註解。最終包會逐位元保留原始 `Reports.txt`，不改寫、不正規化，也不只擷取首尾或最接近時刻。

## 額外的 Python–GMAT 閉環驗證

在設定檔填入 `gmat.executable`，或設定環境變數：

```bash
export GMAT_EXECUTABLE="/path/to/GmatConsole"
tasa-v4 gmat-verify configs/current_competition.yaml \
  examples/legacy_candidate_unverified.yaml --output runs/gmat-check
```

GMAT 報告只記錄兩顆衛星的原生 Cartesian state。`dX、dY、dZ、RelDist` 由 Python 對每個時間區間做 cubic-Hermite 重建並找首次進入根，因此不再依賴傳播期間不會自動更新的 GMAT `Variable`。

## CasADi 局部精修

```bash
tasa-v4 refine configs/current_competition.yaml \
  examples/legacy_candidate_unverified.yaml \
  --time-weight 0.5 \
  --output runs/refined.yaml
```

輸出會顯示 NLP 變數數、約束數與 Jacobian nonzeros，作為自動微分與稀疏 multiple-shooting 已實際建立的檢查。每個 shooting interval 內另用多個 RK4 substeps，IPOPT 成功後仍會以獨立 universal-variable propagator 重算；後驗檢查未進入 5 km 的解不會被接受。

## 正式比賽前必改

1. 將主辦方最新初始軌道覆寫到 `scenario`。
2. 公告四係數後填入：

```yaml
score:
  kt: ...
  ct_s: ...
  kv: ...
  cv_km_s: ...
```

3. 依硬體與時間調整 population、epochs、seeds；先用 `--quick --no-refine` 確認整條流程。
4. 保持 `robustness.screening_scenarios: 0`；依實際執行誤差更新 certification sigma，不要把範例值當成主辦單位保證值。
5. 產生 `submission.script`，在本機 GMAT 至少 Run 一次並取得完整 `Reports.txt`。
6. 回到工具驗證 Reports，只有顯示「通過：N 筆完整資料」才建立最終提交包。
7. 上傳同一候選的 `submission.script` 與 `Reports.txt`；不要混用不同候選或不同次執行的檔案。

技術基準：CasADi 官方文件 <https://web.casadi.org/>、pymoo NSGA-II <https://pymoo.org/algorithms/moo/nsga2.html>、GMAT ReportFile <https://documentation.help/GMAT/ReportFile.html>、GMAT 命令列 <https://documentation.help/GMAT/CommandLine.html>。
