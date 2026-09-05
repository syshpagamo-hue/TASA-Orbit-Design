# TASA Orbit V4.2 跨 ChatGPT 帳號交接紀錄

## 2026-08-16 手冊與邊界修正

- 新增 `docs/TASA_Orbit_V4_2_完整使用手冊.pdf` 與 Markdown 來源，移除誤附的 V4.1.2 舊手冊。
- 修正一燃燒 NSGA-II 基因下界可產生 `evaluation_horizon_s = 0.0` 的錯誤。
- 已新增一燃燒 horizon 下界回歸測試；舊執行若已有 epoch checkpoint，安裝修正版後可用相同命令接續。

更新日期：2026-08-16（Asia/Taipei）

## 1. 專案狀態

本版是由使用者提供的完整原始碼：

```text
TASA_Orbit_V4_1_4_Robustness_Fix(1).zip
```

直接整合而成，不是外掛式示範模組。CLI、GUI／web UI、候選 YAML、GMAT generator、Reports 驗證與 submission 流程都保留；套件版本更新為：

```text
tasa-orbit-v4 4.2.0
```

原始 V4.1.4 基準測試為 `20 passed`。整合完成後為 `27 passed`。

## 2. 本次要求的八項功能

### 2.1 進度、耗時與 checkpoint

- 終端機會顯示 `[TASA V4.2]` 階段、進度百分比與耗時。
- `progress.jsonl` 保存機器可讀事件。
- `run_summary.json` 保存各階段耗時、候選數、robustness 政策與最佳結果。
- `checkpoints/` 保存：
  - 物理種子 bank。
  - 粗篩 survivors。
  - 每個 NSGA-II epoch 的 population、F、G、front 與下一輪 sampling。
  - 精確前緣。
  - IPOPT 已處理項目。
  - finalists robustness 結果。
- NSGA-II checkpoint 使用 generation-specific NPZ，最後才原子切換 JSON 指標；中途斷電不會讓舊 metadata 指到半寫入的新陣列。
- 相同 config 和 `--output` 直接重跑會續跑。

### 2.2 關閉全候選 8 情境 robustness

- `InterceptionProblem` 內完全不建立或呼叫 `RobustEvaluator`。
- 正式設定：

```yaml
robustness:
  screening_scenarios: 0
```

- 只對正式排序前 `optimization.final_robustness_candidates: 20` 組執行 128 情境 certification。
- 自動測試會把 `RobustEvaluator.evaluate` 改成一呼叫就報錯，證明 NSGA-II 仍能完成。

### 2.3 快速粗篩與提前終止

- NSGA-II 與物理種子漏斗使用 60 秒名目 universal-variable 傳播。
- 相鄰樣本之間使用 cubic-Hermite 檢查快速 enter-and-exit crossing。
- 所有 burn 已完成、距離仍很遠、最近數筆持續增加時可提前停止。
- 粗篩 `t_int` 只作排序提示；正式候選全部重新交給精確事件求根。

### 2.4 多圈 Lambert 種子

- 預設飛行時間網格為目標週期的 `0.10–4.00` 倍。
- 枚舉 0、1、2、3 圈。
- 可選 retrograde；預設關閉，避免不合理的近 180° 反向代價。
- least-squares shooting 解出出發速度後，再實際傳播並計算完成圈數；要求圈數與實際圈數不符者淘汰。
- 精確 departure ΔV 超過 1.5 km/s 時，只保留方向並截限，交由 NSGA-II 修復。

### 2.5 升／降軌相位種子

- 依初始相位差自動產生：
  - `lower_and_run_faster`
  - `raise_and_wait`
- 每個 closure period 同時建立：
  - 1-burn 相位候選。
  - 2-burn 含回復速度候選。

### 2.6 官方 Score 接入搜尋與排序

已依使用者提供的 `初賽規則_20260605` PDF 視覺核對：

```text
Score = 50 exp(-(Δrmin-5)/100)
      + 25/(1+exp(kt(Tteam-Ct)))
      + 25/(1+exp(kv(ΔVteam-Cv)))
      - ΣPn
```

- 四係數存在時，`-Score/100` 是 NSGA-II 第一個 objective。
- 無四係數時仍建立時間－ΔV Pareto。
- 失敗候選若自訂 horizon 小於 `Tmax`，正式評估會繼續 coast 到 `Tmax=4TA`；使用 `Δr(Tmax)`，不能用短 horizon 或歷史最小距離冒充分數。
- 最後依 Score 排序；同分順序為最小距離、總 ΔV、任務完成時間。

### 2.7 精確第一次進入 5 km

- V4.1.4 原本已有可用的 `_scan_coast`：scan、local minimum、Hermite hidden crossing、Brent root。
- 本版保留這套精確 authority，並新增測試確保粗篩不能取代它。
- 舊候選 regression：

```text
first_entry_time_s = 2582.0977014169825
minimum_distance_km = 1.0000002445987648
minimum_distance_time_s = 2585.26251378827
```

### 2.8 Hohmann、節點與高遠點種子

- Hohmann：兩次高度轉移 burn。
- Node plane change：在兩軌道平面交線附近變軌面。
- High apogee plane change：先抬高遠拱點，再於低速處改平面。
- 若兩軌道共面，node seed 會自然略過，不製造數值上無意義的節點。

## 3. 主要程式位置

```text
src/tasa_v4/models.py             新增 screening、physical_seeds、checkpoint 設定
src/tasa_v4/seeds.py              五類物理種子與 ECI→VNB 轉換
src/tasa_v4/screening.py          粗篩、Hermite crossing、提前終止
src/tasa_v4/progress.py           進度、耗時、原子 checkpoint
src/tasa_v4/optimize/nsga2.py     粗模型、正式 Score objective、禁止全候選 robustness
src/tasa_v4/optimize/islands.py   70/30 初代、epoch checkpoint 與 resume
src/tasa_v4/pipeline.py           全漏斗接線、精確重算、IPOPT、finalists robustness
src/tasa_v4/pareto.py             正式排序及同分順序
configs/current_competition.yaml  V4.2 正式設定
tests/test_v42_optimization.py    V4.2 新增回歸測試
```

## 4. 驗證結果

### 自動測試

```text
27 passed
```

### quick 端到端範例

係數只用來驗證接線，不代表比賽日正式係數：

```text
kt=0.01, Ct=2500 s, kv=10, Cv=1 km/s
physical seeds in reduced quick grid = 14
final exact candidates = 2
best example score = 83.34376384805837
first 5 km entry = 2484.470151580251 s
total ΔV = 0.8644644701709862 km/s
```

第二次相同設定、相同輸出目錄：物理種子、NSGA-II epoch 與精確前緣皆從 checkpoint 載入；最佳結果一致。

### wheel

```text
dist/tasa_orbit_v4-4.2.0-py3-none-any.whl
SHA-256: 41d47b00b6e4abf6cfaa99d6dd82a0eeeb3d7bbedf819cd16b59f3de255592ff
```

已在全新 Python 3.12 虛擬環境由 wheel 安裝全部相依套件，並通過：

- `tasa-v4 --version`
- `tasa-v4 validate-config configs/current_competition.yaml`
- 舊候選 `simulate`
- 兩個 macOS `.command` 的 Bash 語法檢查

## 5. 在 M5 Mac 上使用

解壓後進入專案根目錄：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
tasa-v4 --version
```

先跑完整測試：

```bash
python -m pytest
```

先做 quick smoke：

```bash
tasa-v4 optimize configs/current_competition.yaml \
  --output runs/quick \
  --quick \
  --no-refine \
  --seed-candidate examples/legacy_candidate_unverified.yaml
```

正式搜尋：

```bash
tasa-v4 optimize configs/current_competition.yaml \
  --output runs/competition \
  --seed-candidate examples/legacy_candidate_unverified.yaml
```

中斷後直接重跑同一指令即可續跑。刻意重新開始：

```bash
tasa-v4 optimize configs/current_competition.yaml \
  --output runs/competition \
  --no-resume
```

`--no-resume` 不刪除檔案；它只在本次執行忽略舊 checkpoint，之後會寫入新的相容 checkpoint。

## 6. 比賽日必做

1. 把兩艘飛船當天狀態填入 `scenario`。
2. 填入主辦方公告的 `kt, Ct, kv, Cv`。
3. 確認 `Tmax` 與 spacecraft A／目標週期的競賽定義一致。
4. 先跑 quick，確認 config 與輸出權限。
5. 正式 optimize。
6. 對首選與備案執行 GMAT。
7. 驗證完整連續 `Reports.txt`，必須顯示「通過：N 筆完整資料」。
8. 提交同一候選的 `submission.script` 與 `Reports.txt`，不能混用。

## 7. 已知邊界

- 本容器沒有 `GmatConsole`，因此尚未在此環境真正執行 GMAT；GMAT generator、parser 與既有測試均通過，但正式閉環必須在使用者 Mac 完成。
- 物理種子與粗篩使用二體模型；最後仍需 GMAT 驗證。
- 提前終止是粗篩 heuristic，不是正式淘汰證明；精確 solver 才是最終 authority。
- 目前正式搜尋仍以 1–3 burns 為主。尚未啟用 4–6 burns 高維模式。
- `lambert_include_retrograde` 預設 false；只有題目明確需要且 ΔV 可行時才應開啟。
- robustness sigma 是工程假設，不是主辦方保證值。
- 任一全域最佳化都不能保證找到數學上的絕對最高分；應保留最高分、最快、最省油與最穩健備案。

## 8. 給新 ChatGPT 帳號的接續提示

將完整 V4.2 ZIP 與本文件上傳後，可直接貼：

```text
這是 TASA 軌道設計競賽的 TASA Orbit V4.2。請先閱讀
docs/CROSS_ACCOUNT_HANDOFF_V4_2.md、README.md、CHANGELOG_V4_2.md、
docs/VALIDATION.md 與 configs/current_competition.yaml。

目前已完成：物理種子、粗篩、正式 Score objective、精確首次 5 km、
全候選 robustness 關閉、finalists certification、進度與 checkpoint。
請勿退回全候選 8 情境，也不要以 coarse t_int 當正式結果。

下一步先在新的比賽案例跑 quick，再分析 progress.jsonl、
physical_seed_screening.csv、pareto.csv 與 run_summary.json；
最後在本機 GMAT 完成 Script + Reports 閉環。
```
