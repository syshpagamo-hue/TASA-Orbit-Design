# 競賽日輸入與正式提交規格

## 評分資料流

1. 將主辦方公布的 Sat、Sat2 設定填入視窗。
2. 將 `kt`、`Ct`、`kv`、`Cv` 四係數填入視窗。
3. 執行最佳化並選定候選 YAML。
4. 產生 GMAT 執行包；其中的 `submission.script` 已建立官方 26 欄 `ReportFile`。
5. 參賽者在本機 GMAT 開啟並執行 `submission.script` 至少一次，產生完整連續 `Reports.txt`。
6. 將這份 Reports 上傳回工具驗證。工具會核對它確實對應本次 Epoch、初始軌道及完整候選任務區間。
7. 顯示「通過：N 筆完整資料」後，建立最終包，將 `submission.script` 與 `Reports.txt` 一起上傳主辦方。

主辦方會直接解析 Script 參數，因此主辦方不會替參賽者執行 Script；這不代表參賽者可以省略 GMAT Run。Reports 是參賽者執行 Script 後必須繳交的成果。

macOS 的 Homebrew Python 若缺少 `_tkinter`，`tasa-v4 gui` 會自動啟動本機瀏覽器版。瀏覽器版與桌面版使用同一套資料驗證及提交產生器；差別只是輸出會直接下載成 ZIP。

## Reports 的 26 欄順序

Reports 標題必須完全依下列順序：

```text
Sat.EarthMJ2000Eq.X Sat.EarthMJ2000Eq.Y Sat.EarthMJ2000Eq.Z
Sat.EarthMJ2000Eq.VX Sat.EarthMJ2000Eq.VY Sat.EarthMJ2000Eq.VZ
Sat.Earth.SMA Sat.Earth.ECC Sat.EarthMJ2000Eq.INC
Sat.EarthMJ2000Eq.RAAN Sat.Earth.TA Sat.EarthMJ2000Eq.AOP
Sat2.EarthMJ2000Eq.AOP Sat2.Earth.ECC Sat2.EarthMJ2000Eq.INC
Sat2.EarthMJ2000Eq.RAAN Sat2.Earth.SMA Sat2.Earth.TA
Sat2.EarthMJ2000Eq.VX Sat2.EarthMJ2000Eq.VY Sat2.EarthMJ2000Eq.VZ
Sat2.EarthMJ2000Eq.X Sat2.EarthMJ2000Eq.Y Sat2.EarthMJ2000Eq.Z
Sat.UTCGregorian Sat2.UTCGregorian
```

可接受逗號分隔（兩個 UTC 各為一格），或 GMAT 空白分隔格式（每個 UTC 為 `26 Jun 2026 02:00:00.000` 四個 token）。檢查項目：

- 至少兩筆資料；
- 26 欄名稱與順序完全相符；
- Sat 與 Sat2 的時間逐列相同；
- 時間不可倒退；Maneuver 邊界可能出現同時刻的燃燒前／後兩筆，兩筆都保留並顯示提醒；
- 最大時間間隔若超過中位間隔五倍，顯示可能缺資料警告；
- 第一筆必須對應 Script 的 Epoch 與兩艘飛船初始軌道；
- 最後一筆必須到達候選的完整 `evaluation_horizon_s`；
- 位置與速度用 cubic-Hermite 區間重建，避免兩筆取樣之間短暫進入 5 km 卻被漏掉。

## 軌道欄位轉換

視窗使用主辦方的 ModifiedKeplerian 欄位。內部傳播器使用：

```text
SMA = (RadApo + RadPer) / 2
ECC = (RadApo - RadPer) / (RadApo + RadPer)
```

Sat 與 Sat2 的 Epoch 必須一致；若主辦方給出不同時間，程式會停止而不自行猜測。

Reports 不會反向覆蓋設定。這樣可以避免把別的候選、舊場景或錯誤 Run 的第一列誤當成競賽輸入。

## 正式提交包

- `submission.script`：固定 12 位小數的可解析參數，且可直接在 GMAT 執行並輸出 Reports；
- `candidate.json`：完整候選與脈衝資訊；
- `competition_config.yaml`：兩艘飛船、規則與四係數快照；
- `submission_manifest.json`：明確標記「主辦方不執行，但參賽者必須在 GMAT 執行」；
- `Reports.txt`：本機 GMAT 產生的原始完整檔案，工具逐位元保留；
- `Reports_summary.json`：欄位、完整時間範圍、初始狀態與相遇檢查摘要。

`verify.script` 與 `gmat_verification.json` 是額外的 Python–GMAT 交叉驗證；它們不能取代正式 `submission.script` 所產生的 `Reports.txt`。
