# V4.2 驗證紀錄（更新至 2026-08-15）

## 規格來源

已核對使用者提供的：

- `初賽規則_20260605...pdf`：1.5 km/s、100 s、`Tmax=4TA`、首次 5 km、評分式與同分順序
- `成功1.script`：目前 7000／10000 km 場景、二體力場、RK89、VNB burn
- `GMAT_Workshop_ManuExample.script`：兩次 Hohmann burn 與 ReportFile 寫法
- `GMAT_example.m`：Hohmann ΔV／TOF 基準
- `formulae.pdf`：軌道元素與 Cartesian 轉換公式
- GMAT 教學講義：ImpulsiveBurn、Propagate、ReportFile 操作

## 數值回歸

目前設定推導：

```text
Target period = 9952.014054236299 s
Tmax          = 39808.056216945195 s
```

舊候選經 V4 重新計算：

```text
Burn time       = 44.326687911605 s
DV VNB          = [0.816188057413, 0, -0.061550107621] km/s
Total DV        = 0.818505565534996 km/s
First 5 km entry= 2582.097701416983 s
Minimum range   = 1.000000244598765 km
Min-range time  = 2585.262513788270 s
```

首次進入時間與上一版 Python 候選欄位 `2582.097701537 s` 相差約 `1.2e-7 s`；這項測試已固定成 regression test。

## 穩健性 certification

使用 `current_competition.yaml` 中明確列出的假設 sigma 與 128 個 scrambled Sobol scenarios：

```text
Success rate          = 100%
Worst minimum range   = 3.767230216 km
p95 minimum range     = 2.171516236 km
p95 first-entry time  = 2583.098450781 s
```

這些 sigma 是工程假設，不是主辦單位公告值；正式比賽前必須依模型誤差與執行精度更新。

V4.2 已移除 NSGA-II 內的全候選 8 情境評估；上述 Sobol evaluator 保留，但 pipeline 只將正式排序前 `optimization.final_robustness_candidates` 組送入 certification。

## CasADi／IPOPT 實跑

V4.1.4 的歷史回歸曾以 4 個 robust scenario shooting copies、每 coast 12 個 shooting intervals、每 interval 8 個 RK4 substeps 精修：

```text
IPOPT status       = Solve_Succeeded
Variables          = 1445
Constraints        = 1459
Jacobian nonzeros  = 11297
Exact post-check   = success
```

IPOPT 結果一律再用獨立 universal-variable propagator 驗證。若 CasADi 離散模型宣稱可行、精確後驗卻未進 5 km，程式會將 refinement 標為失敗。

V4.2 正式設定預設 `multiple_shooting.robust_scenarios: 0`，把魯棒計算延後到 finalists；上面的 4-copy 測試仍保留為可選回歸能力。

## V4.2 端到端煙霧測試

使用 `--quick --no-refine` 等價設定、範例係數 `kt=0.01, Ct=2500 s, kv=10, Cv=1 km/s`：

```text
物理種子（縮小 quick 網格） = 14
最終 candidates              = 2
最佳範例分數                 = 83.3437638481
首次進入 5 km                = 2484.470151580 s
總 ΔV                        = 0.864464470171 km/s
```

第二次以相同設定和輸出目錄執行時，物理種子與 NSGA-II epoch 均從 checkpoint 載入，最佳分數逐位一致。這是接線與續跑煙霧測試，不是正式搜尋的效能或最高分保證。

## 自動測試

```text
27 passed
```

涵蓋軌道閉合與守恆量、GMAT VNB 軸、schedule codec、首次進入 regression、Sobol 重現性、hidden crossing 報告解析、時間倒退偵測、Maneuver 同時刻資料保留、NSGA-II smoke test、CasADi sparse NLP、exact post-check、官方 26 欄 Reports、可執行 submission，以及 V4.2 多圈 Lambert 圈數、物理策略種子、粗篩／精算分權、正式失敗計分、全候選 robustness 禁止、正式分數 objective、Tmax 延伸與 epoch checkpoint 續跑。

## V4.2 安裝修復回歸

另建立隔離虛擬環境，先從內附 wheel 成功執行 `validate-config`，再刻意移走 `site-packages/tasa_v4`、保留原 `tasa-v4` 啟動檔，重現：

```text
ModuleNotFoundError: No module named 'tasa_v4'
```

已重新建立 `tasa_orbit_v4-4.2.0-py3-none-any.whl`，並在全新隔離虛擬環境由 wheel 安裝全部相依套件；`tasa-v4 --version`、`validate-config` 與舊候選 `simulate` 均通過。兩個 `.command` 檔也已通過 Bash 語法檢查。修復流程只重建虛擬環境套件，不會改動 `configs`、`runs` 或 Reports。

## 尚待本機 GMAT 完成的驗證

目前執行環境沒有 `GmatConsole`，所以已完成的是：

- GMAT 腳本產生器
- 每段相對 duration 檢查
- 雙報告檔與 `AppendToExistingFile=false`
- 命令列 runner
- CSV parser、區間內 hidden crossing 檢測
- Python／GMAT time tolerance 判定

但尚未在此環境真正啟動 GMAT。安裝 GMAT 後執行：

```bash
export GMAT_EXECUTABLE="/path/to/GmatConsole"
tasa-v4 gmat-verify configs/current_competition.yaml \
  examples/legacy_candidate_unverified.yaml \
  --output runs/gmat-check
```

只有輸出狀態 `passed` 才代表額外的 Python–GMAT 閉環完成。除此之外，正式流程仍必須在本機 GMAT 執行 `submission.script`，產生完整 `Reports.txt`，再與 Script 一起提交。
