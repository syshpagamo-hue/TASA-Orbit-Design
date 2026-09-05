# V4.2 設計與規則對照

|競賽要求|程式中的硬約束／驗證|
|---|---|
|每次 `||ΔVk|| <= 1.5 km/s`|方向＋大小基因保證；IPOPT 另加二次約束；GMAT 前再驗證|
|相鄰機動至少 100 s|NSGA-II schedule codec 以 mandatory gaps 建構；IPOPT 使用 gap 下界|
|`Tmax = 4TA`|由目標半長軸與 GMAT Earth `mu=398600.4415` 推導|
|首次 `RelDist <= 5 km`|Python 對連續二體軌跡找第一個 inward root；GMAT 報告以 Hermite 區間重建|
|只限制相對位置|不額外加入接近速度限制|
|結果必須可重現|設定 snapshot、候選 manifest、每候選獨立輸出目錄、禁止 append|
|主辦方不執行 script|產生固定 12 位小數且可執行的 `submission.script`；參賽者本機 Run 後連同 Reports 提交|
|Reports 為完整連續記錄|Script 內建官方 26 欄 ReportFile；驗證雙星逐列同時、時間不倒退與完整任務區間；原檔保存|

## 搜尋層

正式 NSGA-II 前先建立依當題幾何重算的物理種子：0–3 圈 Lambert shooting、升／降軌相位、Hohmann 高度轉移、節點變軌面與高遠點變軌面。物理種子先用 60 秒名目傳播篩選；快速擦身區間另用 cubic-Hermite 檢查，不能把兩端都大於 5 km 誤判成沒有穿越。明顯遠離且所有 burn 已完成者可提前終止。粗篩只負責漏斗，正式可行性仍由精確 root solver 判定。

固定脈衝數分開編碼。時間基因先決定任務 horizon，再把可用 coast 時間分配到第一脈衝前、各 mandatory gap 以外的自由部分，以及最後 coast。ΔV 使用 magnitude + sphere direction，因此不會產生超限向量。

公告四係數後，NSGA-II 的 objectives 是：

1. 正式競賽分數的負值（最小化形式）
2. 首次進入時間
3. 已執行總 ΔV

四係數尚未公告時仍使用時間－ΔV Pareto。名目相交使用 inequality constraint；robustness 不在每一個 NSGA-II 候選內執行，避免原本的 8 倍傳播成本。

每個 island epoch 完成後會原子寫入 population、objectives、constraints、front 與下一輪 sampling。相同設定和輸出目錄可續跑；設定指紋不同時不載入舊狀態。

## Multiple shooting 層

每個 coast 分成固定 mesh，所有 mesh 終點都是 NLP state variables。每段 RK4 continuity、脈衝 VNB velocity jump、終端距離、時間與 ΔV 約束一起形成稀疏 NLP。CasADi 對整張 expression graph 產生 derivatives，再交給 IPOPT。

名目終端半徑使用 `5 km - safety_margin`。V4.2 預設 multiple shooting 的 robust copies 為 0；完整 128+ 情境只對正式排序前 20 組執行 certification，避免拖垮全域搜尋。若特定測試需要，可明確提高 `multiple_shooting.robust_scenarios`，但它仍只作用於少量 IPOPT candidates。

## 精確事件與正式計分

粗篩後的候選全部重新使用 universal-variable 傳播；每段先掃描，再對局部極小值和 hidden crossing bracket 求根，`first_entry_time_s` 永遠是第一次進入 5 km 的 inward root。若候選的自訂 horizon 前沒有成功，正式評估會繼續 coast 到 `Tmax=4TA`，避免用短 horizon 的距離冒充規則所需的 `Δr(Tmax)`，也不會漏掉稍晚發生的自然攔截。

正式總分直接進入搜尋與最後排序；同分依規則使用最小相對距離、總 ΔV、任務完成時間。

## 正式提交層

主辦方直接解析 `.script`，但參賽者仍須用同一份 Script 產生 Reports。`submission.script` 因此同時是「機器可解析的正式參數檔」與「可執行的 GMAT 任務」。它固定使用 `ModifiedKeplerian` 航天器狀態、`Local VNB` 脈衝、逐段 `Propagate` duration，以及 12 位小數。檔頭另列出每次脈衝的絕對秒數、三軸 ΔV 與四個評分係數。

Script 內的 `ReportFile Reports` 使用主辦方指定的 26 欄、16 位精度、空白分隔、`WriteReport=true` 與 `AppendToExistingFile=false`。參賽者 Run 後，工具讀取但不改寫 `Reports.txt`；Sat 與 Sat2 時間不一致、時間倒退、起訖區間或初始軌道不符即拒絕。同時刻的 Maneuver 前／後狀態是合法完整紀錄，會保留並提示。

## GMAT 內部驗證層

每一段 `Propagate` 寫的是該段 duration，不把絕對 `t_int` 誤當第二段 duration。軌跡檔只使用一套 `.Add` 欄位，最終摘要用另一個 `ReportFile`，兩者皆 `AppendToExistingFile = false`。

因一般 GMAT `Variable` 不會在 propagation integrator step 自動重算，本版不在 GMAT 內維護 `RelDist`。GMAT 輸出原生位置與速度；Python 對相鄰狀態做 cubic-Hermite interpolation，即使一次快速穿越發生在兩筆取樣都大於 5 km 的區間內，也會檢查區間內最小值與首次根。

額外的 `gmat-verify` 狀態 `passed` 是隊內交叉驗證證據；但用正式 `submission.script` 產生並繳交 Reports 是必要提交步驟。
