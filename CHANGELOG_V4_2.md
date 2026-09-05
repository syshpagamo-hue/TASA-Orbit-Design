# TASA Orbit V4.2 變更紀錄

## 4.2.2

- 成功候選在精確找到首次進入 5 km 後，自動將 `evaluation_horizon_s`
  改為 `first_entry_time_s + 10 秒`。
- 自動移除首次進入後才會執行的後續燃燒，使正式候選、GMAT script
  與實際計分燃燒序列一致。
- 舊 checkpoint 續跑時會重算少量精確前緣候選，同樣套用截短規則。
- 修正 GMAT `Reports.Add` 必須保持單行的產生格式。

日期：2026-08-16

## 4.2.1

- 修正一燃燒 NSGA-II 在 horizon 基因等於下界時產生 `evaluation_horizon_s = 0.0`，導致 Pydantic ValidationError 的問題。
- 新增完整 V4.2 中文使用手冊，並移除誤附的 V4.1.2 舊版 PDF。
- 保留 4.2.0 checkpoint 相容資料格式；更新安裝後可用原命令接續。

日期：2026-08-15

## 已完成

1. 執行進度、耗時與 checkpoint
   - `progress.jsonl` 即時記錄階段、百分比與耗時。
   - 每個 NSGA-II epoch 保存 population、objectives、constraints、front 與下一輪 sampling。
   - 物理種子、粗篩、精確前緣、IPOPT 與 robustness 都有可續跑資料。
   - 相同設定與 `--output` 自動續跑；`--no-resume` 可忽略舊 checkpoint。

2. 關閉全候選 robustness
   - `InterceptionProblem` 不再建立 `RobustEvaluator`。
   - 正式設定 `robustness.screening_scenarios: 0`。
   - 只對正式排序前 20 組執行 certification。

3. 快速粗篩與提前終止
   - 60 秒名目 universal-variable 粗篩。
   - cubic-Hermite hidden crossing 檢查，避免高速穿越漏判。
   - 所有 burn 已完成、距離很遠且持續增加時可提前終止。
   - 粗篩只負責漏斗，正式結果全部由精確傳播重算。

4. 多圈 Lambert 種子
   - 掃描 0–3 圈、不同飛行時間及可選順／逆行。
   - shooting 後驗驗證實際完成圈數，不能只相信要求圈數。
   - 超過 1.5 km/s 時保留方向並截限，交由 NSGA-II 修復。

5. 升／降軌相位種子
   - 依初始相位差自動判斷 `lower_and_run_faster` 或 `raise_and_wait`。
   - 同時產生 1-burn 及含回復 burn 的 2-burn 起點。

6. 正式計分與排序
   - 公告四係數後，`-Score` 直接成為 NSGA-II objective。
   - 失敗候選延伸 coast 到 `Tmax=4TA`，使用 `Δr(Tmax)`，不能以短 horizon 冒充分數。
   - 最後依 Score 排序；同分依最小距離、總 ΔV、完成時間。

7. 精確第一次進入 5 km
   - 保留並強化 V4.1.4 的 scan、局部極小值、Brent root 與 hidden-crossing 流程。
   - 粗篩結果不會被當成正式 `t_int`。

8. Hohmann、節點與高遠點種子
   - Hohmann 兩脈衝高度轉移。
   - 軌道平面交線節點變軌面。
   - 抬高遠拱點後在低速處變軌面。
   - 共面題型會自然略過無意義的節點種子。

## 相容性

- CLI 名稱仍為 `tasa-v4`。
- 原有 config 欄位、候選 YAML、GMAT generator、Reports 驗證與競賽日 GUI／web UI 保留。
- 新增的 `screening`、`physical_seeds`、`checkpoint` 有完整預設值，舊設定檔仍可載入。
- 套件版本更新為 `4.2.0`。

## 驗證

- 27 個自動測試通過。
- quick 端到端流程成功產生正式分數排序、GMAT scripts、submission scripts、進度與 checkpoints。
- 相同設定第二次執行成功跳過物理種子及 NSGA-II epoch，最佳結果一致。
- 尚需在使用者 Mac 的 GMAT 中執行最終 script／Reports 閉環；本容器沒有 `GmatConsole`。
