## 直連 AirMini 拉量測資料：libairmini + Bluetooth Classic RFCOMM

狀態：**端到端已驗證（2026-11-06）**——配對（PIN <PAIRING_CODE>）→ 全史 GetLoggedData 拉取成功：
pull/logged_data.json ≈1MB（88 夜完整時序＋呼吸事件，自 2026-05-07 起），
masterPairKey 已存 pull/master_pair_key.json；實測重連用 `airmini_open_session(am, storedKey)`
成功（免 PIN、免重新配對）。**金鑰有效期＝機器當前配對**：任何一次全新 PIN 配對
（我方驅動或手機 app）都會輪換 masterPairKey——`[ok:pair]` 收到新 key 先回寫 key 檔
再測重連（舊 key 直連曾驗證成功、也有無回應案例，當作不保證項）。
**開會話規則（2026-11-07 讀 libairmini 原始碼定案，修正先前錯誤推論）**：
讀 `src/airmini.c` 確認——`airmini_pair()` **內部就完成整個握手並開好加密會話**
（`airmini_pair_feed` 回 PAIR_DONE → `open_session(am, sk)` → `am->state = SESSION_OPEN`，
回應 JSON 同時含 `masterPairKey` + `sessionKey`）。因此：

- **pair 之後不需要再呼 `airmini_open_session`**。再呼必然回 `rc=2`（AIRMINI_ERR_STATE），
  那是「狀態已不適用」＝**會話早已開好**，**不是拒絕**——把它讀成拒絕會誤導出一整套
  錯誤的 fallback 流程。`airmini_call()` 要求 `am->es` 存在，而 pair 路徑已建立 `am->es`，
  所以 pair 之後可直接送 GetDateTime / StartStream。
- **rc=2 的正確處置＝照常往下走**，不是重試或改走旁路。
- **免 PIN 重連才需要 `airmini_open_session(am, masterPairKey)`**：這條路徑回應只有
  `{"sessionKey":…}`（不含 masterPairKey），同樣最終進入 SESSION_OPEN。
- **分診順序**：先讀原始碼／原始碼可得的狀態語意，再下「裝置在限制我」的結論。
  裝置沉默的成因要在協定層找證據——把外部系統（如雲端/LLM token 配額）的限制誤套到
  機器端，會白等數小時。

狀態標記：**已驗證**＝RFCOMM ch5 連線成功、C 端配對驅動可編譯執行。

### 傳輸與協定（libairmini docs/PROTOCOL.md，已對真機驗證）

- 走 **Bluetooth Classic RFCOMM/SPP，channel 5**，不是 BLE（官方 app 反編譯證實：
  `listenUsingRfcommWithServiceRecord`，無任何 GATT 呼叫；iOS 走 MFi iAP2 `com.resmed.rpc`）。
- 配對 = SRP-6a，passkey 是**機身貼紙上印刷的固定配對碼**（本例 <PAIRING_CODE>），非動態 PIN。
  配對成功回 `masterPairKey`（64 hex，持久化）+ `sessionKey`；之後用
  `airmini_open_session(am, masterPairKey)` 免 PIN 重連。
- 資料通道：AES-256-CBC + JSON-RPC（`[u16 LE 長度][JSON]` 加密後走 vcid 0x0397）。

### 找到機器（不要只靠 inquiry）

- `hcitool scan` / `bluetoothctl scan on` 對這台機器**通常掃不到**（inquiry 靜默）。
- 正確入口：控制器**連線快取**——`bluetoothctl devices` 列出後逐台
  `bluetoothctl info <addr>`，認兩個特徵：**Serial Port UUID 00001101-…** 與
  ResMed vendor service `00000000-deca-fade-deca-deafdecacaff`；名稱形如 `ResMed <序號尾>`。
- 已驗證位址：`<BT_ADDR>`（"ResMed <MODEL>"）。MAC 不印在背面標籤上，別浪費時間找。

### 可連線窗口（連不上時的排查順序，2026-11-07 官方手冊＋實測修正）

- 機器上有一顆 **Bluetooth 按鈕**，指示燈狀態機（官方 user guide）：
  **白恆亮 = 飛航模式（BT 停用）**；**閃藍 = 配對模式**；**藍恆亮 = 已連線**。長按 10s 入飛航、短按 3s 再啟用。
- **【2026-11-07 後段實測修正】閃藍（配對模式）下 ch5 直連與完整 RPC 鏈均可用**：多次成功（含
  pair→StartStream 全鏈），且最有節奏的觸發就是「Chun Yi 按 Bluetooth 鍵→閃藍→數秒內咬住」。
  燈暗（一般待機）也開窗，但窗口約數分鐘一次；機器端一次僅一條連線，斷線後有 ~2 分冷卻期。
- errno 辨識：`112 EHOSTDOWN`＝BT 關閉/無 SPP 可接（含飛航模式）；`16 EBUSY`＝設備端
  仍掛著前一條未收尾的連線（連線中的行程被 kill 會留這狀態，device 端數十秒~數分鐘自清；**關開一次電源立即清**）；
  `114 EALREADY`＝本機殘留 socket 狀態（非機器問題），60 秒以上冷卻自消；
  `104 ECONNRESET`＝**對端有回應但拒絕＝視窗開著**——立刻 2 秒級連續重試，
  12 發內通常命中（這是可咬上的訊號，不是失敗）；
  `br-connection-page-timeout`（bluetoothctl）＝本機 ACL 層就建不上，與機器端無關。
  （`112`＝對端視窗沒開或 BT 停用——等待中、正常，不該因它加快節奏。）
- **BlueZ 卡死的復原（實測）**：連續 `Host is down` 但 `bluetoothctl info` 顯示
  `Connected: no`、RSSI 正常（-70dBm）＝**本機 BlueZ 的 stale connection 狀態**，不是機器端。
  `bluetoothctl disconnect <addr>` 清掉 → `bluetoothctl connect <addr>` 回
  `Connected: yes` → 之後 RFCOMM 直連立刻可通（不必等 60s 冷卻）。
  動手前**先殺掉自己的重試迴圈**：兩隻驅動搶唯一的 RFCOMM 槽會互相把對方擠成
  EHOSTDOWN，探測必須獨佔。
- **【沉默分診（勿另立理論）】**：機器沉默的已證原因只有三個：(a) **RPC 前沒做
  open_session**（見檔首開會話規則——最常見）；(b) **會話閒置**：無資料流動時機器
  1–2 分鐘單方關線，掛著 StartStream 資料流即可保活（實錄 13 分鐘不斷）；
  (c) **StartStream 參數形狀錯**（-32600/-32602，見錯誤碼分診）。警惕反例：曾把使用者
  提供的**雲端 token 配額訊息**（「N requests 90% used」）誤讀成機器端限制而白等三小時
  ——裝置行為要在協定層找證據，別把外部系統的配額套上機器。
- 連線程序（真機順序）：**短按 Bluetooth 鈕一次退出配對模式 → 保持插電一般待機 → 驅動
  無限重試**；若燈恆亮白＝飛航，按 Bluetooth 鈕 3s 恢復後再短按退出閃藍。
- 狀態探測順序：`bluetoothctl info <addr>`（Paired/Trusted/UUID 含 SPP `00001101` 與
  vendor `00000000-deca-fade…`）→ `hcitool con`（ACL 已建立但 SPP 仍拒＝配對模式或
  設備端卡 session，前者退出配對、後者重啟電源）。BlueZ 端配對通常早已存在
  （`bluetoothctl pair` 回 `AlreadyExists`），不需重配。
- 歷史「開機窗口捕獲」仍是備案（重試迴圈 + 請使用者重開機），但**首要檢查燈色**：
  白＝飛航、閃藍＝退出配對、不閃＋插電＝正常可接。
- 使用者指定的重連優先序：**先試無聲 key 直連（免按鈕）**，無效才請他閃藍配對——
  金鑰路徑依賴「key 檔＝機器當前金鑰」此一前提（見檔首註記），真機完整驗證完成前
  閃藍仍是唯一已證路徑。

### 建置（CMakeLists 在庫內是壞的，直接 gcc）

- **庫的 CMakeLists 引用不存在的 `tools/airmini-cli/main.c`** → 別走 cmake，直接編所有 `src/*.c`。
- 本機系統沒有 OpenSSL 開發頭檔，用 conda 的：`/home/adrian/miniconda3`（include+lib）。
- **即時串流（2026-11-07 上線）**：`airmini_start_stream` 接受型別 MaskPressure-100hz /
  InspiratoryPressure-50hz / Leak-TwoSecond；機器以 `StreamData` 通知回傳。**取樣率由
  `sampleIntervalMs` 決定**（實測 10＝真 100Hz、40＝25Hz、1000＝1Hz；映射見下方
  「sampleIntervalMs 就是實際取樣間隔」條），報告批節奏＝`reportIntervalMs`。
- **連線穩定法**：(a) 閃藍（配對模式）時 SPP 可被直接 connect，多次驗證；(b) 暗燈待機
  自然開窗機率極低——要確定性就請使用者按鍵；(c) 掃描（inquiry）後立即 connect
  反而失敗。無按鍵/按鍵的重試節奏規則見「可連線窗口」errno 表與「重試節奏分場景」
  條；pair/open_session 見檔首；StartStream 見 -32600 分診——不再重複列。
```sh
gcc -O2 -Wall -I include -I /home/adrian/miniconda3/include -DAIRMINI_WITH_OPENSSL \
  src/*.c driver_pull.c -o airmini_pull \
  -L /home/adrian/miniconda3/lib -lssl -lcrypto -Wl,-rpath,/home/adrian/miniconda3/lib
timeout 290 ./airmini_pull <BT_ADDR> <PAIRING_CODE>   # addr passkey
```
- 驅動 `scripts/airmini_pull.c`：RFCOMM ch5（自定 `sockaddr_rc`，免 BlueZ 頭）→
  `airmini_pair(PIN)` → 回呼鏈 `open_session` → `version/datetime/metrics/settings/history`
  → `GetLoggedData`（≤6 訊號/請求，結果以 `[notify] LoggedData` 進 `pull/*.json`）。
- 驅動引用 `airmini/crypto.h` 才有 `airmini_crypto_openssl()`；host 提供 send 回呼+feed 迴圈。

### 資料面（signal 名與用途；protocol.h 常數，勿自寫字串）

| signal | 解析度 | 用途 |
|---|---|---|
| `TherapyEvents-RespiratoryEvent` | 事件 | **AHI 原料**：item['events']＝[{event:'…End', time, durationSeconds?}]（Apnea 帶秒數、Hypopnea 不帶）；型別 CentralApneaEnd/HypopneaEnd/ApneaEnd/ObstructiveApneaEnd/ReraEnd/CsrStart|CsrEnd（一次實測 3050 事件分布：Central 2191、Hypo 339、Apnea 242、RERA 190、OA 86、CSR 各1）；與 EVE 對應＝Central Apnea↔CentralApneaEnd、Hypopnea↔HypopneaEnd、Obstructive Apnea↔ObstructiveApneaEnd、Apnea(不明)↔ApneaEnd；**HI/AI/OAI/CAI 拆項無來源，RERA 只有 AirMini 有** |

- `pull/logged_data.json` 是 **JSONL**（每行一個 JSON 物件，`{jsonrpc, method:'LoggedData', params:{logStreamId, data:[…]}}`）；`json.load()` 整檔會 `Extra data` 報錯——逐行 `for line in f: json.loads(line)` 並跳空行。
| `UsageEvents-TherapyStatusEvent` | 事件 | therapy 起訖（夜次歸屬） |
| `TherapyOneMinutePeriodic-Leak` | 1 分 | 漏氣時序（可算 median/95th，同 AirView 定義） |
| `TherapyOneMinutePeriodic-InspiratoryPressure` | 1 分 | 壓力時序 |
| `Diagnostic25HzPeriodic-BlowerFlow/-BlowerPressure` | 25 Hz | 波形（大；先拉一分鐘級，波形按需求） |
| `SystemActivityEvents-*`、`SystemExceptionEvents-*` | 事件 | 開機/ therapy 狀態、錯誤 |

- `AIRMINI_TIME_EPOCH = "2008-01-01T00:00:00.000Z"` = 拉全史；`AIRMINI_MAX_SIGNALS_PER_REQUEST = 6`。
- GetLoggedData 的資料以**notify** 送回（不進 response），記得掛 `airmini_set_notify`。
- AHI 計算：RespiratoryEvent 事件數 ×60 / 該夜使用分鐘；顯示截尾 1 位——完整規則同
  `references/airview-aggregation.md`。

### 判讀機器時鐘（已定案）

**AirMini 時鐘＝UTC（比真實慢約 13m25s）**，與資料來源無關——activity log 與 GetLoggedData 同一時鐘。
- 實測（2026-11-06）：pull 當下 GetDateTime=16:45:53Z、host=16:59:18Z → **慢 13m25s**。
- 實測（2026-11-07 複測）：GetDateTime=06:59:43Z、host=07:13:10Z → **慢 808s＝13m28s**，
  與前次一致 → offset 穩定，非持續漂移。
- **台北時間 = 記錄時間 + 7h46m35s**；所有 AirMini 時戳先過此校正再畫圖/入庫，
  保留原始欄（am_events.ts_raw）以便回查。
- 早期「裝置時鐘≈當地時間」「各自 +8h 解讀」的結論是正午規則/午夜座標混用的假象，別再用；
  判準只有一個：校正後雙機全部配對不得出現 ≥15 分的佩戴重疊。
- 夜別＝session 開始 −12h 的日期（≡ 結束 −18h；勿用 +6h——過午夜入睡會被打成隔天日期）。
- **無 RTC 的時鐘行為（2026-11-07 跳躍分析定案）**：分鐘資料 38,655 列實測 88 個 >2h
  跳躍、全部正向（無一倒退）、連續使用中間隔精確 60s ⇒ 無 RTC（斷電歸零）但有
  運轉中穩定走時；長時間斷電後首夜時戳仍合理＝開機後被外部對時（手機 app 接上時，
  機理未證）。`am_events` 同批可見 2012-01-01 假基準而分鐘表全為 2026 合理值
  ⇒ **兩張表時鐘基準不同，跨表拉時間軸不要互推**。
  事件層也夾帶假基準列（時間落在該機資料起點之前的 PowerOff 殘留）——那是時鐘
  未設定的殘留，不是機器故障；核對 AHI/事件時先排除「早於首次治療日」的列，
  過濾條件寫在查詢裡（如 `ts >= 日期起點`），不要逐筆追查。
- **機上無對時方法可尋**：RPC manifest 無 SetTime/SyncTime，GetDateTime 只讀
  ——基準錯了只能在我方校正層修（offset/夜別錨點），不要在協定裡找對時指令。
  **offset 不是常數（2026-11-27 推翻先前的常數結論）**：跨 20 天漂 11~15 秒、
  **同一條連線內**也量到 ~33 秒變動——先前「兩次隔日幾乎相同」是抽樣巧合。
  校正必須**每個 streamId 現量中位偏移**（`ingest_stream.py` 已內建 per-streamId
  offset + `ts_corrected` 欄），「落後 ~13.5 分鐘」只當粗先驗，不做全域固定 offset。

### 即時串流（**2026-11-07 真機驗證成功**，現行驅動 `airmini_capture2.py`）

- 實測鏈：python 直連 ch5（閃藍時最穩）→ ctypes `pair(PIN)`（回應含 masterPairKey+sessionKey）→ `open_session(key)`（**rc=2 ERR_STATE
  ＝會話已開＝成功語意，此步不可略**）→ GetDateTime（回 dateTime ✓）→
  StartStream（[ok:stream] 回 dateTime＋dataIds 三型 valid:true ✓）→ StreamData 通知。
- **實作檔**：`/home/adrian/airmini/s10/airmini_capture2.py`（**現行正解**）／
  `airmini_server.py`（舊版，StartStream 手寫 params 有 bug，勿再用）／`airmini_live.py`
  （舊版，開會話順序有誤）。
  ＋ `libairmini.so`（`gcc -fPIC -shared src/*.c -lssl -lcrypto`，含 rpath）。
- **正解流程（`airmini_capture2.py`）**：
  1. 有 `pull/master_pair_key.json` → `airmini_open_session(am, mpk)`（**免 PIN、免按機器按鈕**）
  2. 無 key → `airmini_pair(am, PIN)`（回應含 masterPairKey＋sessionKey，**立即回寫 key 檔**）
  3. 不論哪條路，會話都已開 → 直接 `airmini_get_datetime` 驗證 → `airmini_start_stream`
  4. 斷線自動重連；`--sample/--report` 控 interval，`--minutes` 控時長
  5. 資料落 `pull/stream_data.jsonl`（append，每行帶 recv 毫時戳）
- **ctypes 陷阱**：`airmini_crypto_openssl().restype = c_void_p` 必設——未設則
  airmini_pair_start/ad_rand 段錯誤；`airmini_create().restype = c_void_p`；callback 的
  result=bytes（re.search 前 decode）；argtypes 類別定義後再設。
- **sampleIntervalMs 就是實際取樣間隔（回應的 `intervalMs` 即此值）**：通道名後綴
  （`-100hz`/`-50hz`）是**來源訊號的頻率標示，不是你會拿到的解析度**——要真 100Hz
  就傳 `sampleIntervalMs=10`。實測：`10` → 回 `intervalMs=10`、每報 5 樣、連續 799 筆、
  `dataIds` 三型全 `valid:true`（**真 100Hz 已打通**）；`40` → `intervalMs=40`（＝25Hz，
  不是 100Hz）；`1000` → 1 樣/秒/型（7.5 分鐘 445 列；治療中面罩壓 3.7–4.9 cmH2O 波動
  可辨呼吸、漏氣 0.0）。`reportIntervalMs`＝回報節奏，與取樣率無關（每報含
  reportInterval/sampleInterval 個樣本）。先前「sampleIntervalMs=10 被 -32600 拒」的
  紀錄是**連線層失敗被誤記成參數拒絕**（errno 112 Host is down ≠ JSON-RPC 錯誤碼），
  已由 probe 推翻——分診時先分清是 errno 還是 JSON-RPC code 再歸因。資料：
  `pull/stream_data.jsonl` 每行
  `{"recv":unix_ms,"method":"StreamData","data":{params:{data:[{型別:[值]}],intervalMs,startTime}}}`。
- API：`airmini_start_stream(am, stream_types[], n, sampleIntervalMs, reportIntervalMs, cb, user)`
；資料以 **StreamData** notify 即時推回（`airmini_set_notify` 分流 jsonl）。
- 串流型別（protocol.h）：`MaskPressure-100hz`、`InspiratoryPressure-50hz`、
  `Leak-TwoSecond`、`RemainingRampTime`。
- **行程管理鐵則**：只允許**一隻** airmini_live 存在——兩隻並存會互相搶設備唯一
  RFCOMM 槽（對方顯示 EBUSY）。啟動新獵人前 `ps aux | grep airmini_live` 並按
  **精確 PID** kill（勿 `pkill -f`——除會誤殺已連線中的行程外，pattern 寫在
  heredoc/複合命令裡會吻合自身命令列而自殺 exit -15）。
- **被 SIGTERM 的行程是無聲死亡**：不會印任何錯誤，只在正常收尾行出現
  「— 即時抓取結束：StreamData 0 筆」且提前退出；看到這特徵先想是不是自己 kill 的，
  不是斷線。後台完成通知是**延遲批次到達**——收到 exit 通知時連線可能早已斷了數分鐘，
  判斷斷線要以 `hcitool con`/stream_data.jsonl 增長為準，不要以通知時間為準。
- 輸出：`pull/stream_data.jsonl`，每行 `{"recv":<unix_ms>,"method":"StreamData","data":<原JSON>}`。
- 真機條件：插電、治療進行中（待機只有平線）、**手機 App 關閉**（機器一次僅一條
  RFCOMM）、距離 ~10m 內。
- **JSON-RPC 錯誤碼分診（真機實測）**：
  - `-32600 Invalid Request`＝**StartStream 的 params 形狀錯**。**不要手寫 params JSON**——
    用 `airmini_start_stream(am, types, n, sample_ms, report_ms, cb, user)`，
    參數交給 `airmini_params_start_stream()` 產生，形狀固定為
    `{"dataIds":[…],"sampleIntervalMs":N,"reportIntervalMs":M}`。
    先前 `airmini_server.py` 用 raw `airmini_call()` 手寫
    `{"dataIds":[…]}`（**漏掉兩個 interval 欄**）→ 裝置回 -32600。
    同理 `-32602 Invalid Params`＝interval 值不在裝置接受範圍。**已實測可用：10、40、1000**
    （10＝真 100Hz）；方法存在性以 RPC manifest 為準。
  - `-32602` 也可能是 dataId 名錯——型別字串一律取 `protocol.h` 的 `AIRMINI_STREAM_*` 常數，
    勿自寫。
- **串流/實驗全程不可脫面罩**：脫面罩觸發 SmartStop 治療停 → 串流即斷。刻意呼吸
  實驗（正常呼吸 30s → 快速深呼吸 30s → 閉氣 15–20s，作為清醒版中樞事件模型）
  必須在治療停止前完成——實錄過一次「先停治療才做實驗」＝實驗段全沒錄到，只留
  清醒呼吸基線（~445 筆，面罩壓 3–6 cmH2O 波動可辨）。
- **驅動陷阱**：(a) 多個 sequential RPC 不可共用一個可變 tag——前一站的回應會掛到
  後送的 tag 下（GetDateTime 的 dateTime 被誤標成 stream 回應），每請求配獨立
  tag/id；pair/時間共用同一 callback 時以**回應 JSON 內容分流**（`'masterPairKey'`
  in txt→pair、`'dateTime'` in txt→時間），勿只信 tag；(b) RFCOMM socket `settimeout(0.5)` 放在 `connect()` 前＝每次連線必然
  逾時（BT connect 需要完整窗口，短逾時只給連上後的 recv）；(c) ctypes
  `CDLL('libairmini.so')` 裸名載入失敗（dlopen 不搜 cwd）——用絕對路徑或
  LD_LIBRARY_PATH。
- **【2026-11-07 19:00 重大修正】ch5 是資料通道，但 SPP 視窗是間歇性的；ch6 是 iAP 通道**：
  - 用 `sdptool browse <BT_ADDR>` 探索會看到**兩個** RFCOMM 通道：
    **ch6 = "Wireless iAP"**（UUID `00000000-deca-fade-deca-deafdecacaff`，Apple iAP 用）、
    **ch5 = Serial Port**（UUID `00001101-…`，**這才是 libairmini 的資料通道**，
    歷史所有成功串流都在 ch5）。**不要看到 SDP 列出 ch6 就去用 ch6**——
    ch6 能連上但**不講我們的協定**（實測握手送 `RequestSession` 後 30 秒無任何回應）。
  - **SPP 視窗間歇性開關**：待機時 ch5 偶爾開窗（早期小樣本 40 輪 2 次；後續
    更大樣本 486 輪 0 中——**待機自然開窗應視為接近零，要視窗請使用者按鍵**）。
    通的瞬間常先看到 errno 104（視窗開著但裝置拒絕，立搶即中）。
    成功連上後對端會 **idle**（數秒內既不送資料也不關閉）＝可握手的狀態。
  - **通了之後絕對不要立刻 close()**：探測腳本「一通就關」會把寶貴的視窗浪費掉，
    而且**不關的話才 handshake 得成**。正確流程＝通了就**直接交棒給 libairmini 握手**。
  - **connect timeout 長短無關**：err112 一律在 ~5s 內回，設 3s 或 15s 都一樣；
    真正要靠的是**密集輪詢**（間隔 2~4s）＋「通了就握到底」。
  - **「整包重試 60~90s」與「純 connect 輪詢 2~4s」是兩種不同的策略**：
    前者適用於完整握手失敗後的冷卻，後者適用於等待 SPP 視窗開啟。
    搞混會讓等待變成幾十分鐘而不是幾秒。**獵手式抓取見
    `scripts/capture_hunt.py`**（ch5 輪詢＋通了直接握手）。
  - **【2026-11-07 22:57 定案】`reportIntervalMs` 必須 = `sampleIntervalMs` × 5**：
    這是「每報 5 樣」的限制，**比例不對裝置直接回 -32600 Invalid Request**。
    實測對照：
    | sample | report | 每報樣本 | 結果 |
    |---|---|---|---|
    | 10 | **50** | 5 | ✅ 5,985 筆連續零中斷（22:57 實跑） |
    | 10 | 500 | 50 | ❌ -32600（19:31 實跑，浪費 3 分鐘除錯） |
    | 40 | **200** | 5 | ✅ 586 筆（17:58 實跑） |
    | 1000 | 1000 | 1 | ✅ 445 筆 |
    **傳錯比例會被誤讀成「StartStream 參數形狀錯」**，但 shape 明明是對的
    （`airmini_start_stream()` 產生的 JSON 格式無誤）——真正的限制是**每報樣本數 ≤ 5**。
    坑在於：同一個 -32600 碼，shape 錯與比例錯共用。
    **除錯時先看「每報樣本數 = report/sample」是否 ≤ 5**，再看 JSON 形狀。
  - **【2026-11-07 22:57 完整跑通】真 100Hz 串流已驗證**：
    `open_session` → `{"sessionKey":…}` → StartStream rc=0 → dataIds 三型
    `valid:true` → 5,985 筆／5 分鐘連續零中斷（20 報/秒 × 5 樣 = 100Hz×3 通道），
    全程金鑰直連（**免 PIN、免按鈕**）。回應 `intervalMs=10`、每報 5 樣。
  - **機器時鐘 offset 隨批重測（2026-11-27 已推翻常數結論，見判讀時鐘節）**：
    本批（11-07）量得 −810.2s、跨 3 小時僅漂 ~3s，當時誤判為常數——
    後續長期量測發現跨日與同連線內都會變動，校正必須 per-streamId 現量。
  - **待機時串流值全 0**：機器沒在治療時 MaskPressure/InspiratoryPressure/Leak
    全是 0.0（不是沒資料）。要抓「呼吸波形」必須**機器在治療中**。
  - **機器「待機」時開窗機率遠低於「閃藍配對模式」**：要確定性就請使用者
    按一下 Bluetooth 鈕（閃藍）——實測 22:57 一次就咬上、5 分鐘零中斷。
- **金鑰直連的邊界（2026-11-07 18:29 實測定案）：金鑰只解「認證層」，不解「transport 層」**。
  分成兩步看：
  - **步驟1（transport）**：`socket.connect(bt_addr, 5)` 開 RFCOMM/SPP 槽位——**這步不需要金鑰也
    不需要 PIN，但需要裝置端主動開 SPP 視窗**。視窗沒開時回 `Errno 112 Host is down`，
    這時無論金鑰多新、有沒有 PIN 都一樣失敗。**「有金鑰就能連」這句話只在步驟2成立**。
  - **步驟2（認證）**：`airmini_open_session(masterPairKey)` 開加密會話——**這步免 PIN、免再配對**，
    舊版要重打 PIN 配對才能重連的問題由此解決。
  - 實測：機器不按鍵、只給金鑰 → 步驟1 就卡 `Host is down`（`bluetoothctl info` 顯示
    `Connected: no`、RSSI −70 正常）；按鍵開窗後 → 步驟1 通、步驟2 回 `{"sessionKey":…}` 全通。
  - **`bluetoothctl connect` 回 `Connected: yes` ≠ RFCOMM 可用**：那是 ACL 層連上了，
    裝置的 SPP 槽位仍可能關著 → Python `connect()` 照樣 `Host is down`。
    判斷要分兩層看：`bluetoothctl info` 的 Connected 是 ACL，RFCOMM 能不能連才是 transport。
  - 實際用法（pi5 整夜錄製）：**金鑰讓重連不需要人**（省掉按鍵＋重打 PIN），
    但**機器端 SPP 視窗仍要它在開機/待機時自然開窗**；視窗沒開就重試迴圈等，
    別誤判成金鑰失效。
- **重試節奏分場景（2026-11-27 歸因修正）**：**無人值守等待**（沒人會去按鍵）用
  60/75/90s 輪替——但這只是不浪費、不代表能咬上；**要咬上＝武裝 `ready_capture.py`
  後請使用者按一下鍵**，3s burst 在按鍵落下 3~10 秒內連上（6/6 實測）。
  無按鍵狂試的成因是「視窗不會開」，不是節奏傷介面。**改抓取節奏前先讀本條
  與「視窗一次性」條**。
- **BlueZ 復原已內建進抓取器**：`airmini_capture2.py` 的 `bluez_recover()` 在
  遇到 errno 112/114 時自動執行 `bluetoothctl disconnect → 4s → connect`，
  再重試一次 RFCOMM。仍失敗就等下一輪。
- **怎麼分「機器沒開機」vs「BlueZ 卡死」**：
  - `bluetoothctl info` 的 **RSSI 是快取值**（實測連續三次量都是 −70 不變，
    不反映即時訊號強度），**不能**拿它判斷機器是否開機或距離。
  - 判別順序：(1) `bluetoothctl show` 看控制器 `Powered: yes`（本機正常）；
    (2) `hcitool con` 看有沒有 ACL 連線；(3) 對方 bonding/trusted 狀態
    （`Paired/Bonded/Trusted` 都是 yes）；(4) 以上都正常但 RFCOMM 持續
    `Host is down` ＝**對端 SPP 視窗沒開**（多為未開機/休眠），不是本機問題——
    這時只能等，不要把時間花在改本機設定上。
  - 本機 BlueZ 的 SPP 視窗不可見是正常的：這台機器的 inquiry 掃不到
    （見「找到機器」節），`bluetoothctl scan` 也掃不到——只能靠連線快取。
- **整夜 100Hz 的容量與吞吐（2026-11-07 實測，非估算）**：
  模擬 8 小時 × 3 通道 × 100Hz = **8,640,000 列**（67MB jsonl）→
  `ingest_stream.py` 全部入庫**耗時 94 秒**（WAL + synchronous=NORMAL），
  結果 **3.17 GB**。換算：**每整夜約 3.2 GB、每小時 400MB**。
  - 磁碟不是問題（本機 1.2T 可用），但**一個月的 100Hz 原始波形 ≈ 100GB**——
    要長期累積就必須定期把舊波形降取樣或轉檔，別無腦堆 `am_stream_wave`。
  - 查詢效能：1 分鐘波形取回 6,000 點 **0.01s**；全通道 COUNT 0.18s；
    帶 `LIKE '...T05:%'` 的聚合 0.4s。**索引 `(ts)` 與 `(channel, ts)` 夠用**。
  - **ts 格式要統一帶小數秒**（`2026-11-07T05:24:47.395000`）：實測若整秒時
    省略小數秒，會出現同一分鐘內部分列有小數秒、部分沒有，字串比較與
    `LIKE` 查詢都會漏列。真實資料（由 `datetime.isoformat()` 產生）已一致。
- **【2026-11-27 新發現】金鑰會因機器端配對狀態改變而失效，錯誤碼是 `-11005 VerificationFailure`**：
  - 症狀：`open_session` 送出 rc=0（已送出），但**沒有** `[ok:session]` 回應；
    隨後的 StartStream 回 **rc=2**（ERR_STATE＝session 沒開成），
    裝置端 JSON-RPC 錯誤是 `{"code":-11005,"message":"VerificationFailure"}`，
    然後連線被 `Errno 104 Connection reset by peer` 收掉。
    **不要把 rc=2 當成根因**——那只是下游症狀；真正的錯在 open_session 被拒。
  - **實例**：11/7 配對的金鑰（`145AE0EF02B5…`）在 11/27 被拒；
    重新用 PIN 配對得到新金鑰（`08E325873F3E…`）後，**同一支程式立刻恢復正常**
    （免 PIN 直連 + 100Hz 串流 1,800 筆零中斷）。
  - **成因**：masterPairKey 只在「機器當前配對」有效。任何改變配對狀態的動作
    （出廠重置、App 重新配對、換新機）都會輪換它。**隔了幾天就要重新配對**，
    不要假設舊金鑰還能用。
  - **分診順序**：看到 rc=2 / -32600 / 沉默時，先確認 **open_session 有沒有真的
    換到 sessionKey**。沒有 → 是金鑰問題（重新配對），不是參數問題。
  - **重新配對的動作**：連線後 `airmini_pair(am, PIN, cb, user)` → 回應含新的
    masterPairKey → **立刻回寫 key 檔** → 同步給其他機器（pi5）→ 再驗證直連。
  - **重新配對後舊金鑰要一併清掉**：pi5 等其他機器上的 key 檔也要換，
    否則那些機器會繼續拿舊金鑰去撞 -11005。
  - **`SubscribeEvent` 的參數名仍未驗證（2026-11-27 更正）**：App 的 BLE 日誌
    **只記訂閱的數量**（3 或 4，視時機），不記事件名；唯一可證的是 App 實際
    **收到**過的 NOTIFICATION 共 4 種：`FGState`（30 次）／`RecoverableError`（13 次）／
    `SystemError`（13 次）／`Heartbeat`（4,615 次，每 60 秒一筆）。
    先前程式裡湊的 `TherapyState` 在 App 日誌出現 **0 次**——明顯是錯的，已移除。
    現在只送 **3 個確實存在**的名（Heartbeat 由裝置主動送，不需訂閱）。
    **仍是 UNVERIFIED**：真實清單要靠實機送不同組合、看裝置回什麼才知。
- **【2026-11-27 視窗行為確認】SPP 視窗是「用完即收」**：一次連線內做完
  GetLoggedData + StartStream 後，只要連線一斷（即使是我方主動關），
  機器就把 SPP 槽位收回 → 立刻再連會得到 `Host is down`。
  **要把多件事塞進同一個 session**：拉全史與開串流要**同一條連線**一起送
  （`scripts/pull_history.py` 與 `session_all` 流程），不要分兩次連。
  順帶：`airmini_pull.c` 走的是「每次重新配對」的路線（會輪換金鑰），
  改用金鑰直連的 `scripts/pull_history.py` 才不會動到配對狀態。
- **【2026-11-07 23:30 根因定案：穩定連線要抄手機 App 的三件事】**
  對照 `~/airmini/small/airmini_log_*.txt`（App 的 BLE RPC 日誌）與我們自己的失敗記錄：
  | | 我們的紀錄 | App 的紀錄 |
  |---|---|---|
  | 單次連線維持 | **0～299 秒**（streamId 1~6） | **4～10 小時**（最長 36,650s） |
  | 開 session 後 | 直接 StartStream | GetVersion → **GetSessionKey** → **SubscribeEvent** → Get → StartStream |
  | 連線中 | **只收不送** | **每 60 秒主動送一輪**：GetLoggedData → GetHistory → Get |
  - **Heartbeat 是裝置→主機方向**（App 日誌每 60 秒一筆 NOTIFICATION Heartbeat），
    **主機端不需要送心跳**；App 保持連線靠的是**自己每 60 秒送一輪 RPC**。
    這就是「只收不送 → 閒置被視為無效 → 被裝置收線」的機制。
  - **App 連線後第一個 RPC 故意是 StartStream 且必然 TIMEOUT**（session 還沒開），
    然後才 GetSessionKey 開 session。**這不是錯誤，是 App 的探測行為**——
    日誌裡每次 INBOUND_CONNECTION 後都是 `OUT 1 StartStream → TIMEOUT`。
    我們不必模仿這個多餘步驟，但要知道 **StartStream timeout ≠ 握手失敗**。
  - **`SubscribeEvent` 一定要送**：App 在 GetSessionKey 之後立刻送（App 日誌顯示
    訂閱數 3 或 4，視時機）。libairmini **沒有** `airmini_subscribe_event()`
    公開 API 與 params builder，要用
    `airmini_call(am, "SubscribeEvent", params_json, cb, user)` 自己組。
  - **App 的連線幾乎都發生在晚上 23:00～隔天 09:30**（戴機睡覺時段），
    而且多數是 `INBOUND_CONNECTION`＝**機器主動撥入 App**。
    這解釋了「待機時 SPP 視窗難開」：**機器在治療中才會主動維持藍牙連線**。
  - **實作：`scripts/stable_capture.py`**（依上述序列重建握手＋55 秒保活輪詢）。
- **【2026-11-07 23:45 新線索】`EnterTherapy` / `EnterMaskFit` / `EnterStandby` 存在但未試過**：
  `protocol.h` 有這三個 method 常數（`AIRMINI_M_ENTER_THERAPY` 等），libairmini
  **沒有對應的公開 API 與 params builder**，要用 raw
  `airmini_call(am, "EnterTherapy", params_json, cb, user)` 自行組參數。
  假設：主動送 `EnterTherapy` 會讓機器進入治療狀態並**持續維持 SPP 視窗開啟**，
  這是「待機時開窗機率極低（486 次輪詢 0 命中）」的可能解法。
  **UNVERIFIED——尚未在真機測試過**，下次連線時優先實驗：
  先送 EnterTherapy → 再 StartStream → 觀察 180 秒後視窗是否仍在。
  （注意：這會改變機器狀態，屬治療控制面，測試前要和使用者確認，
  且不應在無面罩佩戴時長時間停留於治療狀態。）
- **咬上後把整個抓取做進同一條連線，且不主動關線**：視窗「用完即收」——連線一斷
  （連我方主動 close 算在內）機器即收走 SPP 槽位，立刻再連必得 112。所以全史＋
  串流＋保活要同一 session 一次做完（`session_all2.py`／`ready_capture.py`：
  咬上後串流到設定時長、55s 保活、不關線）。
- **分「對端沒回應」與「本機 BlueZ 卡死」**：用**另一台機器**（pi5，獨立藍牙硬體、
  不同位置）試連同一台 AirMini——兩台都失敗＝對端沒回應（沒開機/沒廣播）；
  只有本機失敗＝BlueZ 問題（disconnect→connect 復原）。偶發 `16 EBUSY`＝
  裝置端當時有東西在跑（可能它主動撥入），重試即可。
- **【視窗＝一次性門票；一次成功配方】**按鍵開的視窗約 1~2 分鐘，且**任何「連上
  立刻 close」的測試會提前收走它**（單發試連成功後 21 秒即 112）——所以**每個
  connect 都要打算做完整握手，絕不做「只試一下」的連線**。想抓＝先武裝
  `ready_capture.py`（3s×25 burst → 15s×20 → 60/90 遞增），武裝好使用者才按
  一下鈕，3~10 秒內咬上；武裝中撞到 errno 104 不必改輪次，2 秒級連搶直接咬。
  **使用者分心晚按也無妨**：等待會遞增到 60/90s，幾分鐘後才按仍會咬上
  （實測 8 分鐘）；不必重新武裝、不必催。
- **【穩定性定稿（2026-11-27，四次實驗合計）】****11/11 按鍵全咬上**（每次一按
  就中，最快 1 秒）；涓流偵測上線後連續 5 輪全量（3,599~3,600 筆/3min）零異常。
  **已穩**：人在場按一下鍵 → 每次抓都順利。**未驗證**：整夜免按鍵自動錄
  （需 `EnterTherapy` 實測）與單一連線 >1 小時存活。
- **【多輪實測】**`capture_rounds.py`（N 輪 × M 分鐘）：咬上率 100%，但串流筆數呈
  「0/全量」交替——**裝置端串流殘留 TTL（約 180s）**：上一條連線的串流剛結束時，
  新連線的 StartStream 收不到資料（與 App 日誌的 180 秒痕跡一致）。對策：
  `capture_rounds.py --cooldown 120`＋ready_capture 的成長檢查（見涓流條）。
  **GetLoggedData 是增量的**：重複拉取只送少量新資料，同一時段重拉約 788 筆
  固定塊——重複抓不會重複搬全史。**重複抓是安全的**：jsonl 全 append、
  入庫 INSERT OR IGNORE 冪等；資料庫規模持續增長，回報前先 SELECT COUNT(*).
- **【串流涓流停擺】**串流可能「有資料後停住」——重送邏輯若只查「總數=0」會漏抓。
  正解：**25 秒檢查一次串流筆數有無成長，沒成長就重送 StartStream（≤3 次）**；
  重送時回呼 tag 要重設回 'stream'，並記錄回應（[stream-resp]）供診斷。
  **驗證準則**：cooldown>0 的 run 每輪 stream_msgs 全 >0＝殘留對策足夠（看
  `pull/capture_rounds.jsonl`）；再現 0 → 冷卻拉到 ≥200s，或等 180s 殘留過期再試。
- **未測/待驗證**：長連線持續性（HeartBeat 被庫過濻、120s 無 StreamData 即判斷斷流）、
  串型別組合上限、**`EnterTherapy` 能否免按鍵維持 SPP 視窗**、保活輪詢間隔的最佳值
  （App 用 60s；我們用 55s 避開與 Heartbeat 同相位，實際門檻未量）。
- **金鑰直連已在真機完整驗證（2026-11-07 17:58）**：`airmini_open_session(mpk)` 回
  rc=0 → `[ok:session] {"sessionKey":…}` → StartStream rc=0 → dataIds 三型 valid:true
  → 586 筆 StreamData 連續流入。**金鑰直連＝免 PIN、免按機器按鈕的已證路徑**
  （仍受「key 檔＝機器當前金鑰」前提約束：任何新配對都會輪換金鑰）。
- **探測取樣率用專用腳本，不要手改主程式**：`scripts/probe_intervals.py` 一次連線內
  依序試多組 sampleIntervalMs 並回報裝置實際回的 intervalMs 與每報樣本數。
  實測 10 → intervalMs=10（真 100Hz）／40 → 40（25Hz）／1000 → 1Hz。
  探測前**必須先停掉自己的重試迴圈**（兩隻驅動搶唯一 RFCOMM 槽會互相擠成 EHOSTDOWN）。
- **量呼吸週期用自相關**：`scripts/breath_period.py` 從串流 jsonl 的 MaskPressure 通道
  估週期——1Hz 實測段（非零 403 點、面罩壓 2.8–5.8 cmH2O）自相關最佳 lag=**9 秒**
  r=0.36–0.44，局部窗週期 2–14s 變動（過度換氣時週期縮短）。**只有非零段有意義**：
  待機是平線 0，不切非零段會得到假的長週期。
- **BlueZ stale connection 的另一種徵狀（2026-11-07 18:08 實測）**：連線回
  `Errno 104 Connection reset by peer` 或 `114 EALREADY Operation already in progress`
  ＝本機 BlueZ 還掛著前一次嘗試。`bluetoothctl disconnect <addr>` → 等數秒 →
  `bluetoothctl connect <addr>` 回 `Connected: yes` → RFCOMM 直連立刻可通。
  **必須等 `bluetoothctl connect` 回 Connected: yes 之後**，Python RFCOMM 直連才會通；
  `bluetoothctl info` 顯示 `Connected: no` ＋ RSSI −70dBm 正常＝stale，不是機器端問題。
- **歷史 25Hz 可能補得回**：資料面表列的 `Diagnostic25HzPeriodic-BlowerFlow/-BlowerPressure`
  是 GetLoggedData 可請求的 dataId（先前未實拉）——下次連線先試
  `GetLoggedData([BlowerFlow 25Hz], fromTime=近數日)` 確認裝置保存深淺；若成功，
  歷史波形可直接拉，不需即時錄。
- 收後流程：jsonl → `am_stream_wave` 表 → 與事件對位繪圖（skill `scripts/ingest_stream.py`
  ＋ `scripts/plot_stream.py`）：
  - **一報多樣本的時間展開**：每次 StreamData 報含 N 個樣本但只有一個 `startTime`，
    樣點 i 的真實時刻＝`startTime + i×interval_ms`。直接拿 `startTime` 當每個樣點的 ts
    會讓同一報內的樣本全撞同一 timestamp——UNIQUE 約束下被當重複丟棄（實測 10,125 筆
    只剩 3,093 筆，七成資料靜默消失）。
  - **表設計**：`am_stream_wave(ts, channel, value, interval_ms, stream_id, ts_corrected,
    offset_s)`，UNIQUE(ts, channel, interval_ms, stream_id)；匯入用 `INSERT OR IGNORE`
    （冪等、可重跑、不覆寫既有列），開 `busy_timeout` 應付 WAL 下的多寫入者。
  - **offset 由資料本身量、按 streamId 各量各的**：成對比較 `startTime`（機器時鐘）
    與 jsonl 的 `recv`（本機 UTC）取中位，先驗約落後 ~810 秒——但跨日、甚至同
    連線內都會變動（不是常數，見判讀機器時鐘節），**不要全域套一個數**；
    `ingest_stream.py` 的 per-streamId 中位偏移 + `ts_corrected` 欄是定案做法，
    原始 ts 一併保留。
  - **畫圖跨時段要斷線**：不同 streamId 或時間跳 >5s 的兩段若直接畫會出現假的斜坡
    長條——在斷點插 `NaN` 再畫。
  - matplotlib 中文字型：`font_manager.addfont('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc')`
    後設 `rcParams['font.family']`（系統 .ttc 可用於 matplotlib；reportlab 不吃 .ttc）。

### 夜間自動錄製部署到 pi5（2026-11-07 已部署，2026-11-09 更新為穩定版）

- **目標機器**：pi5（192.168.100.6，aarch64 Debian 13、Python 3.13.5）。
  環境檢查（部署前必查）：`uname -m`＝aarch64、`python3 -c 'import socket;
  socket.AF_BLUETOOTH'` 存在、`/usr/include/openssl/ssl.h` 存在、
  `lsmod | grep bluetooth` 有模組、`rfcomm`/`bluetoothctl` 可用。
  **pi5 系統自帶 openssl 開發檔，用 native gcc 即可，不需 conda**（主機才需要 conda）。
- **必須傳過去的檔案**：
  | 檔案 | 來源 | 備註 |
  |---|---|---|
  | `stable_capture.py` | 本 repo `scripts/` | 現行正解（App 握手段序＋55s 保活） |
  | `capture_hunt.py` | 本 repo `scripts/` | 備援：等間歇性 SPP 視窗 |
  | `ingest_stream.py` | 本 repo `scripts/` | 入庫（`INSERT OR IGNORE` 冪等） |
  | `libairmini.so` | `gcc -fPIC -shared src/*.c -lssl -lcrypto` | pi5 端自建 |
  | `pull/master_pair_key.json` | **憑證** | scp 過去後 `chmod 600`；**不同時也不經 stdout** |
  | `pull/pin.txt` | **憑證** | 僅首次配對用；`chmod 600` |
- **憑證傳輸紀律**：PIN 與金鑰都是憑證——用 `scp` 傳檔、不 `echo` 到終端輸出、
  落地 `chmod 600`、**不進 git**。
- **環境變數**（寫在啟動腳本裡，不寫進碼）：
  ```
  AIRMINI_LIB=/home/adrian/airmini_capture/libairmini.so
  AIRMINI_ADDR=<BT_ADDR>
  AIRMINI_KEYFILE=/home/adrian/airmini_capture/pull/master_pair_key.json
  AIRMINI_OUT=/home/adrian/airmini_capture/pull/stream_data.jsonl
  AIRMINI_CHANNEL=5
  AIRMINI_KEEPALIVE=55
  ```
- **cron（pi5）**：`0 22 * * *` 跑 `start_night.sh 480`（錄 8 小時）、
  `30 7 * * *` 跑 `ingest_night.sh`（入庫）。**先停掉舊的 `capture_night.sh`**
  （它跑的是有 raw `airmini_call` bug 的 `airmini_live.py`）。
- **入庫的 offset 要現算**：pi5 端 `ingest_night.sh` 從 jsonl 的
  `startTime`（機器時鐘）與 `recv`（本機 UTC）配對量出偏移，
  **不要寫死 807**——那是本機某一批的數值。
- **DB 位置**：pi5 沒有完整彙整庫，首次執行時 `scp` 主機的副本過來；
  之後 pi5 是獨立寫入者，**主機與 pi5 的寫入要分工**（見下「協作單寫者」）。
- **限制（未解）**：機器待機時 SPP 視窗開啟機率極低（實測 486 次輪詢 0 命中），
  所以 **pi5 的整夜錄製在「機器沒有被戴著睡覺」時大概率開不了**。
  App 日誌顯示機器的藍牙連線集中在 23:00–09:30（戴機時段）且多為
  `INBOUND_CONNECTION`（機器主動撥入）。**這是 pi5 整夜自動錄的真正瓶頸**，
  不是程式問題。可能的解是 `EnterTherapy`（UNVERIFIED，見上）。

### 退路（直連不成）

手機 AirMini app 的本地資料庫（Android adb 備份 / iOS 解備份）；myAir 雲端整合
（`prestomation/resmed_myair_sensors`，台灣未開放則無效）。這兩條未在本機驗證，僅當備案。

### AirView 版式報告重產（2026-11-06 v2 已對齊原版）

- **SQLite 彙整庫**：`~/airmini/s10/build_db.py`（documents venv）→
  `/home/adrian/airmini/cpap_data.db`（138MB）：`s10_daily/sessions/session_files/pld_2s（99.4 万列）/events/csl_events`
  ＋ `am_events/respiratory_events/leak_1min/pressure_1min/sessions/nights/meta`。【2026-11-07 補】
  am_events 實際僅 **371 筆＝生命週期事件**（PowerOn/MaskOn 等），勿再誤記為 3.8 萬——分鐘資料筆數（38,655）才是大件；逐事件時刻**只有 AirSense 有**（Central 1432/Hypopnea 486/Apnea 166/Obstructive 136），AirMini 藍牙只給逐夜次數（CAI/OAI/HI 總別）。
  另有 **`nightly_timeline`**（103 萬列逐點時間軸：night −12h／t_local 本地時／source A·M／minutes 粒度 2·1·0（0＝事件點）；AirSense leak L/s×60 統一 L/min、兩台 mask_press 同 cmH2O；查時段平均**按 minutes 加權**避免 AirSense 取樣密被過度代表）。
  **時間校正（2026-11-06 實測）**：GetDateTime=16:45:53Z、host 真實=16:59:18Z → AirMini 時鐘=UTC（快 13m25s），
  am_* 全部 +7h46m35s 校正為台北時間（am_events.ts_raw 保留原始）；
  **夜別 = (session 開始 −12h) 的晚上日期**（≡ 結束 −18h；勿用 +6h——過午夜入睡會被打成隔天日期，
  兩台夜別錯位。官方手冊實測：指示燈白=飛航藍牙停用，閃藍=配對模式只收配對不收 SPP，
  資料 SPP 在一般待機，詳見 airmini-access「可連線窗口」節）；建置：直接重跑 build_db.py。
  校正後雙機 121×110 全配對重疊 = 0、AirMini 夜 ≈23–01 睡、07:45–08:15 醒（符合作息）。
  EDF 結構重點：每 record 含**多樣本**（PLD 30 樣×2s＝60s/record），樣本時間＝(record_idx×ns＋si)×2；
  EVE onset/dur 單位＝**秒**；SAD（SpO2/脈搏）無模組＝空白表。重建：直接重跑 build_db.py。
- 圖表：`~/airmini/s10/avview_charts.py`（conda `ml` env）→
  `~/.hermes/cache/scratch/avview/`：`f1_usage.png`（檔案1，語意見下條）、
  `detN_YYYYMMDD.png`（檔案3 每夜三面板：Leak 虛線 24、Pressure 虛線 20/4、Events
  雙色刻記 AirSense=藍/AirMini=橘、HH:MM 連續軸）、
  `b2_usage/leak/press/ahi/mini.png`（檔案2 雙機並排柱）、`night_stats.json`（夜統計）。
  舊 `det_*_{A,M}`/`f2_mini`/`p2_*` 為棄用產物。
- PDF 組裝：`~/airmini/s10/avview_complete_pdf.py`（documents venv）→
  `~/airmini/報告/20261106 吳俊毅 202607-202611-{1,2,3}.pdf`。
  **完整資料版（睡眠夜制）**：檔案1＝**照原版 90 天視窗鏡射**（原版 "90 day period ending
  01/11/2026"；槽位/星期標籤/紅空心全高＝**主機當日未用**皆照原版語意；AirMini 只以藍柱
  疊入空心或併排補充——空心語意不因第二裝置改寫）；y 軸 **24 小時制**＋ 00:00–08:00
  主睡眠帶淡藍底（AirView 的 12PM/8PM 標示會被誤讀）；
  檔案2 共 2 頁（合規＝雙機合計數值＋Therapy 四面
  板雙機並排柱 `b2_*.png`、日期軸連續）；
  檔案3 睡眠夜頁（夜＝session 結束 +6h 的日期；雙機曲線同頁同軸、連續 HH:MM
  橫軸、藍=AirSense / 橘=AirMini；夜統計由波形+事件直接計算 →
  `avview/night_stats.json`；`avview_charts.build_nights()`）。
- **座標陷阱（曾造成長條整體位移 12h）**：STR `mask_on/mask_off` 已是自正午起算的分鐘，
  換小時直接 `/60`，不要再減 720；跨正午的 session 在 Noon-錨定軸要畫兩段（h0→24＋0→h1）。
- `mask_off=1439`＝MaskOff 缺失哨兵 → 原版畫到底軸（不是錯誤資料）。
- **夜統計分母**：每段佩戴的分鐘＝Σ(PLD 樣本數)×2/60——把樣本數當分鐘會把 AHI 壓小 30 倍。
- **重製圖被說「不對」**：先 300dpi 渲染原版＋自己版，逐槽像素量測（顏色＋起訖）比對——
  真正的差異常是視窗/語意而非資料；語意確認一致後，視窗照原版鏡射，第二裝置只當補充。
- **reportlab 雙欄左欄**：標籤 ≤6 字（中文混排會撞右對齊值欄），值欄右緣與圖表 y 標籤留 ≥12pt。
- 資料欄位關鍵：STR `mask_on/mask_off`＝**自正午（12:00 機器本地）起算的分鐘**（值 0＝正午整
  開始的短佩戴，勿當空值丟棄）；`mask_off=1439`＝MaskOff 缺失哨兵（圖畫到底軸）。
  AirMini 原始時戳＝UTC-快 13m25s（見上校正）。
- reportlab 陷阱：`drawImage` y＝底邊座標，白底 PNG 會蓋掉下方文字；缺字 ▾／✓ 用 drawPath 畫。
- 字型：`~/.fonts/NotoSansTC.ttf`（TrueType；系統 CFF .ttc reportlab 不吃）＋ matplotlib
  `font_manager.addfont` 同一家族。
