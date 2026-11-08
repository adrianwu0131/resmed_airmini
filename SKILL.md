---
name: resmed-cpap-sdcard
description: "Parse ResMed AirSense SD cards and AirMini Bluetooth data."
version: 1.2.0
author: Chun Yi Wu (adrianwu0131), Hermes Agent
license: MIT
platforms: [linux, macos]
metadata:
  hermes:
    tags: [resmed, cpap, airmini, airview, edf, bluetooth, sleep-therapy]
---

# ResMed CPAP SD Card 解析與 AirView 報告重現

## When to Use

- 使用者提供 ResMed SD 卡 dump：含 `STR.edf`、`DATALOG/<yyyymmdd>/<ts>_{CSL,EVE,BRP,PLD,SAD}.edf`、`Identification.tgt` 的資料夾。
- 要用 Python 重現 AirView（合規 / 期間治療 / 每日詳細）報告、核對報告數字，或萃取每晚壓力/漏氣/AHI 數據時。

## 流程

1. **先收集目標值再解析**：若使用者有 AirView PDF（合規/治療/詳細報告），先用 read_file 直接讀
   文字層；圖表頁用 pdf skill 的 `pdf_page_image.py --dpi 200` 轉 PNG + vision_analyze 讀。
   把每個印出數字記成驗證清單——所有解析值都要對上它。
2. **盤點 SD**：`Identification.tgt` 給 SRN/型號；`STR.edf` = 每日總摘要（173 記錄 × 1/天）；
   `DATALOG/<資料日>/` 每晚一組（PLD/SAD = 依 session 分檔，CSL/EVE = 該日一份）。
3. **解析**：`from resmed_s10 import EDF, parse_str, decode_annotations`（scripts/ 下，stdlib only）。
   `parse_str(STR)` 回傳每天 dict（Duration、MaskPress/Leak/TgtIPAP 百分位、AHI 族、SpO2…）。
4. **每日值一律取 STR 通道**，不要用 PLD 2s 樣本自行池化百分位來對報告——機器內部演算法與
   池化結果差 0.1~0.3（機制的量化步階不同）。PLD 只用在：per-session 明細、機器日值已被覆寫的
   例外日（見 airview-aggregation.md）。
5. **事件（AHI 族）由 EVE 註記還原**：文字 `Central Apnea` / `Obstructive Apnea` / `Apnea`(=不明)
   / `Hypopnea`；rate = 事件數 × 60 / **OnDuration** 分鐘；顯示 = 截尾到 0.1（4.58→4.5）。
   注意分母是 OnDuration（通電分鐘）不是 Duration——30/10 那天 367 分鐘用 368 才得 1.7。
6. **依 references/airview-aggregation.md 的規則做期間聚合**，逐值對照 PDF。
7. **匯出 CSV**（腳本會寫到 CWD 的 `daily_report.csv`）。
8. **重產 AirView 版式報告的鐵則**：副本＝原版數值——表內主機數值用原版已驗證值，
   第二台（AirMini）以補充段落/小表加入；逐日圖表雙機同軸（夜 = session 開始 −12h 的
日期，即「睡覺那晚」），夜圖逐夜單機畫（絕大多數夜只有一台），3 個真雙機夜才雙機同頁；
   y 軸 24 小時制＋主睡眠帶 00:00–08:00 淡藍標示；版位用 pypdf font probe 實測原版，
   不要目測。細節與實測幾何見 `references/airview-pdf-report.md`。

## 鐵則（違反必錯）

- **註記通道字元是每 int16 裝 2 個（little-endian 原始位元組）**：用
  `b''.join(struct.pack('<h', v) for v in edf.rawsamples[r][chan])` 重組。
  用 `bytes(v & 0xFF …)` 只會拿到隔字元的亂碼、事件文字全空。
- **EDF header 數值欄是 ASCII**，不是二進位 int16；欄位「逐欄排列」（全部 labels →
  全部 transducer → 全部 units…），不是每訊號 256B 連續。驗證：hdr_bytes == 256 + ns×256。
- **縮放**：phys = pmin + (raw−dmin)×(pmax−pmin)/(dmax−dmin)。壓力步階 0.02 cmH2O、
  漏氣步階 0.02 L/s（=1.2 L/min），round 比較時注意。
- **Date 通道 = Unix 天數**；−1（0xFFFF）= 缺值/未使用。
- **STR 的 mask_on/mask_off ＝自該資料日正午起算的分鐘**：機器時刻 = 資料日 12:00 + 值
  （值＋720 會差 12 小時，兩套解讀混用是長條整體位移 12h 的根源）。值 0 ＝正午整開始的
  短佩戴（DATALOG 檔名 12:00:xx 證實），勿當空值丟棄；on ≥ off 的對＝跨正午（off＋1440），
  不是空值——只有 -1/缺失才丟。
- **Session 歸日**：起始 ≥12:00 → 當日；<12:00 → 前一天（半夜 02:43 才睡的那晚歸前一晚）。
  DATALOG 目錄名即資料日。
- **報告切點**：AirView 產報告時尚未結束/上傳的 session 全部不計。找出結束時間 > 讀卡時間的
  那個 session 排除；該日的詳細報告頁也會缺它（或只剩白天已完成的部分）。
- **機器會覆寫 STR**：多日合併後日值會被覆寫（例：午睡 129 分與隔日晨間 session 合併成 528
  分的日值）。重現「報告當時」要用該 session 的 PLD 重算，且**頁面與彙整庫一致**：
  `nightly_summary` 放報告快照值（事件計數＝頁面 rate × 時數反推）、`s10_daily`
  保留機器現值、`sync_meta` 註明 override——DB 與報告任一不一致，使用者核對時
  就會報「數值沒有修正」。快照↔現值清單見各 `snapshot_override_*` 註解。
- 比對不通過時，排查順序：日期歸屬 → 切點 → 分母（OnDuration vs Duration）→
  round/trunc 顯示 → 最後才懷疑公式。
- **裝置行為的歸因先在協議/原始碼層找證據**：使用者看到的配額、額度、用量提示
  （如雲端/LLM token 額度）屬於**外部系統**，不能拿來解釋藍牙裝置為何不應答——
  誤套會白等數小時。讀第三方驅動原始碼確認狀態機語意（錯誤碼＝拒絕？＝已就緒？）
  比猜裝置意圖可靠。

## AirMini（行動機）的資料是 app/裝置 log，不是 SD 卡

- 檔案：`airmini_activity_log_*.txt`（JSON lines 裝置事件，含全時間軸）、
  `airmini_log_*.txt`（app↔BLE RPC 日誌，只有心跳/簽章，無量測值）、
  `airmini_cloudupload_*.txt`（雲端嘗試紀錄，只看 REG 是否 401）。
- **量測資料（AHI/漏氣/壓力）不存在這些檔裡**：REG Failed 401 代表從未上傳成功；
  app 的 Scoring 行都是 0 record。能還原的只有 therapy 時段（PressureStart→PressureStop）。
- 檔案時間帶 `Z` ＝ **機器時鐘≈UTC（實測快 13m25s；真台北時間 = 記錄 +7h46m35s）**：
  先校正再歸夜，校正後與 AirSense session 交叉比對必須零重疊＋空檔續接——校正錯時會出現
  雙機同時佩戴數小時的矛盾。
- session 歸夜（校正後）：**夜別 = (session 開始 − 12h) 的日期**（＝「睡覺那晚」的
  晚上日期，與 AirSense 資料日/STR 行同口徑）。舊規「結束+6h」會把過午夜的入睡
  （校正後普遍 00:0x–02:2x 才睡）打成隔天日期，兩台夜別錯位一天，nightly_summary
  出現大量假「雙機夜」（實測 35→3，單人雙機互斥幾乎全成立）。
- **寫 CSV 禁忌：SELECT * 配手寫表頭＝欄位錯位**。events_json 佔了 schema 第 11 欄，
  表頭少列它會讓漏氣/壓力整排右移一格（壓力 med 顯示成漏氣 max）。表頭從
  PRAGMA table_info 生成或明確 SELECT 欄名。
- **要 AirMini 的 AHI/時序資料：log 檔沒有，走藍牙直連**（RFCOMM ch5 + libairmini，
  PIN = 機身貼紙印的配對碼）——完整流程見 `references/airmini-access.md`；
  驅動在 `scripts/airmini_pull.c`。
  **「有金鑰就能直接連」只對一半**（2026-11-07 實測）：金鑰取代的是**認證層**
  （免 PIN、免重新配對地開加密會話）；**transport 層仍需裝置端開 SPP 視窗**，
  視窗沒開時連線直接 `Errno 112 Host is down`，金鑰無效。`bluetoothctl connect` 回
  `Connected: yes` 只是 ACL 通了，**不代表 RFCOMM 可用**——兩層要分開判。
  **協議層結論先讀原始碼再下判斷**：`airmini_pair()` 內部就完成握手並開好加密會話，
  pair 之後**不必**再呼 `airmini_open_session`（再呼回 rc=2 = 會話已開，非拒絕）；
  免 PIN 重連才需要 `airmini_open_session(masterPairKey)`。
  StartStream 一律用 `airmini_start_stream()`，不要 raw `airmini_call()` 手寫 params
  （漏 `sampleIntervalMs`/`reportIntervalMs` → 裝置回 -32600 Invalid Request）。
  現行抓取器：`scripts/airmini_capture2.py`（有金鑰免 PIN 免按鈕直連＋自動重連）。
  藍牙抓取寫到 `scripts/pull/`（`logged_data.json` 971KB + settings/pair 等），
  `logged_data.json` 是 **JSONL**（每行一個 JSON-RPC 物件）：逐行 `json.loads`，
  整檔 `json.load` 會 Extra data 直接崩。
- **即時串流抓取（2026-11-07 真機驗證；現行主驅動＝`scripts/airmini_capture2.py`，
  python ctypes 直連 ch5；`airmini_live.c` 為 C 變體）**：`airmini_start_stream`
  推送 MaskPressure-100hz／InspiratoryPressure-50hz／Leak-TwoSecond，資料以
  StreamData notify 落 `pull/stream_data.jsonl`（每行帶接收毫時戳）。條件：機器插電
  +治療進行中+手機 App 關閉（一次僅一條連線）；串流/實驗全程不可脫面罩
  （SmartStop 斷流）。**取樣率＝`sampleIntervalMs`（通道名後綴不是解析度）**：
  傳 10 得真 100Hz、傳 40 只有 25Hz。金鑰輪換、JSON-RPC 錯誤碼分診、pi5 夜間部署見
  `references/airmini-access.md`。
- **分診先分清 transport 層與 protocol 層**：errno（EHOSTDOWN/EBUSY/EALREADY）是連線層，
  JSON-RPC code（-32600/-32602）是參數層。把「連線失敗」記成「參數被拒」會讓錯的結論
  寫進筆記並浪費數小時重試。同一裝置一次僅一條 RFCOMM 槽——自己的重試迴圈會把探測
  擠成 EHOSTDOWN，探測前先停掉；BlueZ 卡死用 `bluetoothctl disconnect`→`connect` 復原。
- **同路人（2026-11 網路現況）**：libairmini 生態（psychoticbeef：`libairmini`／
  `AirMiniKit`／`airsupply`——HA 外掛）；MiniBridge（nitebridge.net，Android 藍牙→
  原生 ResMed EDF→OSCAR/SleepHQ）；ApneaBoard「AirMini detailed data over
  Bluetooth on Windows」＝同流程（藍牙→SQLite→事件/逐分鐘壓力漏氣→夜圖，
  已可匯 ResMed 格式 EDF 餖 OSCAR）；SD 端解析對照：hms-cpapdash-parser（C++）。OSCAR 官方不支援 AirMini——橋接方案即價值。

## 兩台合併的 SQLite 彙整

- **先預覽再建庫**：schema 定案前先產出夜別 DataFrame（tail 10–15 夜 + 雙機同夜
  inner-join 對照）給使用者過目。兩台 AHI 同夜常差 0.5–2（演算法差），先讓使用者
  看懂數字再動 SQL，不要直接落 schema。
- **協作單寫者**：cpap_data.db 另有輔助實例會同時改寫（親歷：連線中被換成另一組
  修正狀態）——重建/ALTER 前先查檔案 mtime 與 sync_meta keys 變化，腳本一律冪等；
  分工記錄在 sync_meta.coordination_roles：主=Nova（重建/抓取/報告），輔助=pi5
  （備份+唯讀稽核，勿直接 ALTER）＋本機每日 04:00 cron 備份推送 <backup host>:<backup dir>/。
- **AHI 高值先看分母**：佩戴 <1h 的短段（午睡/測試）AHI 被小分母放大（5 事件
  ÷0.42h ≈ 12/小時）。核對異常 AHI 前先看當夜 `usage_hr` 與事件總數；整夜
  使用（>5h）的雙台全月最高僅 6–7/小時，10–20 只出現在短段。
- **實作陷阱：sqlite 同 cursor 邊 SELECT 邊 INSERT 只跑一行**——游標的結果集
  被 INSERT 打斷後 iteration 靜默結束（不報錯、只是少 178 列）。來源 SELECT
  一律先 `fetchall()` 再另外迴圈 INSERT。
- **單人雙機驗證（夜別統一後重驗）**：校正後 217 段 session 零重疊；
  同「夜」雙機只剩 3 天（09/12、09/25、10/25，都是午睡/傍晚短戴過渡）。之後若
  出現長時間雙機同戴即錯。
- **夜別口徑已統一**：am_sessions.night / am_nights / nightly_summary
  全部 = (開始 −12h) 的晚上日期；使用者的逐夜換機模式（例如週日→10、
  週一/三→mini、週四→10）是機器歸屬的驗證基準，吻合率 ~85%（例外夜＝真實作息）。
  `nightly_summary` 重建前先備份整表到 nightly_summary_backup.json；重建腳本
  （重聚合 am_ 表 → 重寫 am_nights/CSV/nightly_summary）要回寫 am_meta 的
  night_convention 說明。
- **總表 schema**：`<aggregate db>` 的 `nightly_summary`（每機每夜一列，
  PK (ts,tool)；欄位 usage_hr/ahi/hypopnea/central/obstructive/apnea_unspec/rera/
  leak ×3/press ×3/**hi/ai**）+ `sync_meta`（offset/夜別規則/單位分層/快照
  override 註記）。HI/AI 來源：AirSense＝機器日值（`s10_daily.ai/hi`）、
  AirMini＝事件數÷時數（`am_nights`，app 同公式）。事件計數：AirMini＝
  `am_nights.events_json`（鍵：CA事件/OA/UApnea/Hypopnea）、AirSense＝
  `s10_events` 按 kind 群計（EVE 無 RERA 類，恒 0）。
- **全時間軸統一表 `nightly_timeline`（已建，103 萬列／181 夜）**：每時間點一列，index
  與 `nightly_summary` 同口徑——`night`（同一套 −12h 規則）+ `t_local`（本地 UTC+8）+
  `source`（A/M）+ `minutes`（粒度 2s/1min、0＝事件點）。數值欄 leak/pressure/tid_vol/
  min_vent/snore/flow_lim + 事件欄 ev_c/ev_o/ev_u/ev_kind；PK (night,t_local,source,
  minutes,ev_kind)＋night 索引。`WHERE night='<date>' ORDER BY t_local` ＝整晚全解析度。
  AirSense 2s 漏氣入表時 ×60（L/s→L/min，原始表保留原單位）。SQLite 陷阱：
  `julianday('… HHMMSS')` 回 NULL（數字時間不是可解析時間）——session 字串的時間段
  要用 `||':'||` 重組後再餵 julianday/strftime；INSERT 回 rowcount=0 先查這個。
- **回報表規模/粒度前先 COUNT 各表、按表分類**：`am_events`＝機器生命週期事件
  （PowerOn/MaskOn/MaskOff，~371 列）、`am_respiratory_events`＝呼吸事件（per-event
  帶 time/durationSeconds，實測 ~3 千筆）、分鐘表（數萬列）——三種粒度三張表。
  把 lifecycle 表當成事件證據會把「mini 有逐事件時間戳」講反；GetLoggedData 的
  `TherapyEvents-RespiratoryEvent` 本身就帶逐事件 time（見 airmini-access 資料面表）。
  回答「兩台哪個有逐事件」前先抽表看 schema，不要憑表名推斷。
- **DB 單位分層固定不動**：`s10_daily`/`s10_pld_2s` 原始解析表保留通道原單位
  （漏氣 L/s、2s 樣本同）；**只在彙整層**（`nightly_summary`/`am_*`）存換算後的
  L/min，並把分層寫進 `sync_meta`。原始表被換過單位再改回＝徒增一輪往返，
  使用者核對 DB 的入口是 nightly_summary，不是原始表。
- **時鐘 offset 每次抓取重測**：抓完馬上用機器 `GetDateTime`（`pull/datetime.json`）
  對 host UTC 量漂移，offset 存進 DB 的 `sync_meta` 表並與該批資料綁定；寫死的
  +7h46m35s 只對當批有效（機器石英鐘持續漂移）。絕不拿舊 `datetime.json` 快照跟
  「現在時間」比——快照是抓取當下的值，會算出幾十小時的假漂移；要新 offset 先重抓。
- **Leak 定義已三重驗證（2026-11-06）**：(a) EDF 檔頭宣告 `Leak.50/95/70/Max = 'L/s'，物理範圍 0–2.0`
  （STR 與 PLD 的 `Leak.2s` 同）→ ×60 = L/min，2.0 L/s 上限 = **120 L/min 封頂的來源**；
  (b) OSCAR 官方 wiki（apneaboard.com/wiki/OSCAR_leaks）：「ResMed 機器只記錄 Excessive
  Leak（刻意漏氣扣除後），原始 Total Leak 不會寫入 SD/機器」，與我們按壓力分層下界=0
  的實測一致；(c) 報告中 24 L/min 虛線 = ResMed 慣用的 unintentional-leak 參考值。兩台
  （AirSense 與 AirMini）的 leak 語意相同、可直接互比（先前「AirMini 未扣刻意漏氣」為誤）；彙整層實測兩台相近（各夜中位 AirSense 3.6 vs AirMini 4.8、95% 15.6 vs 21.6）。
- **同路人參考（2026-11-06 查證）**：SD 解析＝**hms-cpapdash-parser**（C++，OSCAR 相容，
  src/EDFParser_STR.cpp 寫死「EDF stores L/s, convert to L/min」×60，AHI/壓力不縮放——
  獨立實作與我們完全一致，可直接當第二實作對帳）；cpap-py（PyPI 搜尋結果有頁面但
  實際 404，為假套件，勿信）；sleepyCPAP（repo 已移除）；hms-cpap/dash 產品；
  AirMini 藍牙＝libairmini（BT Classic SPP UUID 00001101、NCP 番外 0xCAFEBABE、
  CRC32 0xEDB88320 無終局 XOR、SRP-6a+PIN→masterPairKey、AES-256-CBC 皆與我們
  實作一致）＋ airsupply（HA 外掛）＋ AirMiniKit（Swift）＋ MiniBridge（Android，
  AirMini→原生 ResMed EDF）；ApneaBoard 有 Windows+SQLite 同類專案。
  AIRVIEW 版式 PDF 重產無人做。
- **夜圖雙機曲線單位陷阱**：AirSense PLD 2s 樣本 `leak` 是 **L/s**（實測中位 0.0、
  95% 0.1、max 0.38），AirMini 逐分鐘值是 **L/min**——同一條 L/min 軸直接並畫，
  主機曲線會視覺上小 60 倍、貪底。畫進雙機同軸圖前 AirSense 樣本一律 ×60。
- **漏氣「最大值」有三個語意，混用必被抓錯**：各夜最大 = STR `leak_max` ×60
  （AirSense 漏氣通道在 2.0 L/s = 120 L/min 飄頂，多晚卡片同一值；判讀高值先
  查飽和）；全史最大 = 前者取最大（AirSense 120、AirMini 66）；AirView 報告
  表的 "Maximum" 其實是**各夜最大值的平均**（窗口平均 ≈47.9）。回報任何「最」
  值前先確認取的是哪一種。
- **AirMini RespiratoryEvent 事件語彙**：CentralApneaEnd / HypopneaEnd / ApneaEnd
  （未明分類）/ ReraEnd / ObstructiveApneaEnd / CsrStart / CsrEnd（部分帶
  durationSeconds）；AHI = 五類計數總和 ÷ 用時小時。EVE 註記（AirSense）對照：
  "Central Apnea" / "Hypopnea" / "Obstructive Apnea" / "Apnea"。逐項對照與
  schema 草案見 `references/cpap-data-sources.md`。
- **repo 的 `scripts/edfparse.py` 缺 `idx`/`rawsamples`**，跑 skill 的
  `scripts/resmed_s10.py` 才有 `chan()/decode_annotations()`。若只能用 repo 版，
  註記通道手算：EVE 每 record 720 bytes、每通道 360 int16 little-endian
  成對成字符。STR 彙總 repo 版 `parse_str()` 可用（實測過）。

## 檔案

- `scripts/resmed_s10.py` — EDF/STR/註記解析模組（stdlib only）
- `scripts/s10_report.py` — 主報告：合規對照 + 期間統計 + 每日 4 頁對照 + 每日 CSV；
  內含一份已驗證資料的 fixture 常數（CUT/午睡檔名/AirView 目標值），換新報告或新機器時調整。
- `scripts/night_curve_check.py` — 逐夜換氣/壓力（及漏氣）曲線＋事件對位檢查；
  列印「中樞事件前 1-4 分過度換氣」比例。EVE/PLD session 名秒差容錯內建。
- `scripts/airmini_capture2.py` — AirMini 即時串流擷取（正規握手路線；有金鑰免 PIN
  免按鈕直連、断線自動重連、StartStream interval 由 libairmini 組 params）；
  工作副本見 `scripts/airmini_capture2.py`
- `scripts/ingest_stream.py` — 串流 jsonl → `am_stream_wave` 表（一報多樣本要按
  `startTime + i×interval_ms` 展開，否則七成資料被 UNIQUE 當重複丟棄；INSERT OR IGNORE 冪等）
- `scripts/plot_stream.py` — 串流三通道波形圖（跨時段插 NaN 斷線、中文字型用系統 .ttc）
- `scripts/probe_intervals.py` — 探測裝置實際接受的 sampleIntervalMs（一次連線內依序試
  多組，回報裝置回的 intervalMs 與每報樣本數；探測前先停掉自己的重試迴圈）
- `scripts/breath_period.py` — 從串流 jsonl 用自相關估呼吸週期與局部週期變動
  （1Hz 實測段最佳 lag=9s；只看非零段，待機平線會得出假長週期）
- `scripts/capture_hunt.py` — 獵手式串流抓取：等待 AirMini **間歇性 SPP 視窗**
  （SDP 列出 ch5=資料通道／ch6=iAP 通道；ch5 待機時隨機開窗，實測 40 選 2）；
  密集輪詢 2~4s，**通了直接握手不放手**（探測腳本「一通就 close」會浪費視窗）。
  `--minutes/--sample/--report` 控時長與取樣率
- `scripts/stable_capture.py` — **穩定版**（2026-11-07 23:30 依手機 App 日誌重建）：
  握手順序 GetVersion → GetSessionKey → **SubscribeEvent** → Get → StartStream，
  加上 **55 秒保活輪詢**（App 靠每 60 秒主動送一輪 RPC 保活；只收不送會被閒置收線）。
  實測單次連線時長：舊版 0～299 秒 → App 4～10 小時
- `references/clinical-notes.md` — 中樞型呼吸中止的名詞/機制/2025 AASM 治療選項摘要
  （醫療問答與門診匯談支持，非診斷）
- `references/file-format.md` — 通道表、header 版位、各檔型格式
- `references/airview-aggregation.md` — 日期歸屬/切點/聚合公式/顯示規則
- `references/airmini-access.md` — AirMini 藍牙直連（RFCOMM ch5、連線窗口、
  conda OpenSSL 建置、signal/事件清單）；驅動 `scripts/airmini_pull.c`
- `references/airview-pdf-report.md` — AirView 版式 PDF 報告生成
  （reportlab + TrueType Noto TC；缺字 \x00 驗證迴圈）；產品腳本
  `scripts/build_db.py`（SQLite 彙整＋時間校正）、`avview_charts.py`（圖表）、
  `avview_complete_pdf.py`（三份 PDF 組裝）
- `references/cpap-data-sources.md` — 兩台機器資料源逐項對照（dataId/EDF 通道
  ↔ usage/leak/pressure/AHI）、DATALOG EDF 通道實測表、offset 量測流程、
  夜別彙整 SQLite schema（nightly_summary + sync_meta）

本機已有驗證過的工作副本與資料：`scripts/`、SD dump 在 `<SD dump dir>/`。


## Repo 安裝與環境變數

把本目錄複製到 skills 目錄後即可用（skill 名 `resmed-cpap-sdcard`）。
AirMini 相關腳本靠環境變數定位資源，**配對碼是憑證，不要寫進版控**：

```bash
export AIRMINI_LIB=/path/to/libairmini.so      # libairmini 共享庫
export AIRMINI_ADDR=<BT_ADDR>                 # AirMini 藍牙位址
export AIRMINI_PIN=<PAIRING_CODE>              # 機身貼紙配對碼（僅首次配對需要）
export AIRMINI_KEYFILE=pull/master_pair_key.json
export AIRMINI_OUT=pull/stream_data.jsonl
export AIRMINI_CHANNELS=5                      # 資料通道（ch6 是 iAP，勿用）

# AirSense 側（SD 卡解析與彙整庫）
export CPAP_DB=/path/to/cpap_data.db          # 兩台機器的彙整庫
export CPAP_SD_DUMP=/path/to/sd_dump          # SD 卡 dump 根目錄
export CPAP_FONT=/path/to/NotoSansTC.ttf      # 中文字型（圖表用）
```

不設 `AIRMINI_PIN` 時腳本不做 PIN 配對，只走金鑰直連——金鑰已存在即可。
