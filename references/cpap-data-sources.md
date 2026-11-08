# 兩台 CPAP 主機資料源對照與彙整 Schema

## 逐項對照（資料源 → 欄位）

| 欄位 | AirSense 10（醫材主機）| AirMini（旅行機）|
|------|------------------------|------------------|
| usage | STR.edf `MaskOn/MaskOff/Duration`（分鐘，自資料日正午起算）| `UsageEvents-TherapyStatusEvent`：MaskOn↔MaskOff 區間秒數加總 |
| leak | STR `Leak.50/.70/.95/.Max`（夜統計）或 PLD.edf `Leak.2s`（逐 session 波形）——EDF 檔頭宣告單位 **L/s**，物理範圍 0–2.0（×60 = L/min；2.0 L/s = 120 L/min 通道封頂）| `TherapyOneMinutePeriodic-Leak` 逐分鐘值，**已是 L/min** |
| pressure | STR `MaskPress.50/.95/.Max`（另有 `TgtIPAP/EPAP.50/.95/.Max`）或 PLD `MaskPress.2s` | `TherapyOneMinutePeriodic-InspiratoryPressure` 逐分鐘值 |
| 呼吸事件 | EVE.edf 註記逐筆（onset/duration/text）| `TherapyEvents-RespiratoryEvent` events[] 逐筆（time/event/durationSeconds）|
| AHI | STR 直接有 `AHI/HI/AI/OAI/CAI/UAI/CSR` | 自算：事件計數 ÷ 用時小時 |
| SpO2/脈搏 | SAD.edf `SpO2.1s/Pulse.1s` | 無 |
| 呼吸生理 | BRP.edf `Flow.40ms/Press.40ms`；STR `MinVent/RespRate/TidVol` | 無（僅逐分鐘壓力/漏氣）|

## 事件語彙對照

| 意義 | AirMini `event` 值 | AirSense EVE 註記文字 |
|------|--------------------|-----------------------|
| Hypopnea | `HypopneaEnd` | `Hypopnea` |
| Central Apnea | `CentralApneaEnd` | `Central Apnea` |
| Obstructive Apnea | `ObstructiveApneaEnd` | `Obstructive Apnea` |
| 未分類 Apnea | `ApneaEnd` | `Apnea` |
| RERA | `ReraEnd` | （EVE 註記未見獨立文字）|
| Cheyne-Stokes | `CsrStart` / `CsrEnd` | STR `CSR` 通道；CSL.edf 偶見 `CSR Start/End` |

AirMini 事件部分帶 `durationSeconds`（apnea 類才有），Hypopnea/RERA 無。

## DATALOG EDF 通道實測（本台）

| 檔案 | 通道 | 節奏 |
|------|------|------|
| EVE.edf | `EDF Annotations`（事件文字）+ `Crc16` | 每夜一檔；註記 = 事件 onset/dur/text |
| PLD.edf | `MaskPress.2s Press.2s EprPress.2s Leak.2s RespRate.2s TidVol.2s MinVent.2s Snore.2s FlowLim.2s` | 60s/record，每 channel 360 int16 |
| BRP.edf | `Flow.40ms Press.40ms` | 60s/record，400ms 取樣 |
| SAD.edf | `Pulse.1s SpO2.1s` | 60s/record |
| CSL.edf | 註記 | **實測幾乎只有 `Recording starts`**（偶見 CSR Start/End）→ 此台無可用睡眠分期，別設計依賴分期的指標 |

## 時鐘 offset 量測流程

1. 藍牙重抓完成後**立刻**讀 `pull/datetime.json` 的 `dateTime`（UTC 帶 Z）。
2. `drift = host_utc − machine_utc`（機器快為正）。上批驗證值：drift 13m25s。
3. `applied_offset = +8h − drift`（台北 = 機器時間 + applied_offset）。上批值：+7h46m35s。
4. 寫入 `sync_meta`：machine_ts、host_ts、drift_sec、applied_offset——offset 與該批資料綁定。
5. **陷阱**：`datetime.json` 是抓取當下快照；拿它跟「現在時間」比會算出幾十小時假漂移。
   要新 offset 必須重抓藍牙。漂移會累積，每次抓取 offset 都可能不同。

## 夜別彙整 SQLite schema（實測後定案版；建庫前仍先出 DataFrame 預覽）

夜別規則（兩台統一）：**夜 = (session 開始時間 − 12h) 的日期**（「睡覺那晚」）。
單位分層：`s10_*` 原始表保留通道原單位（漏氣 L/s）；彙整層（nightly_summary/am_*）
統一 L/min、cmH2O。兩台 leak 語意相同＝扣除刻意漏氣後的 excessive leak。

```sql
-- ★ 此表已上線（不是草案）；欄位順序以 PRAGMA table_info 為準，勿手寫表頭配 SELECT *
CREATE TABLE nightly_summary (
    ts          TEXT NOT NULL,      -- 台北日期 YYYY-MM-DD（= (session 開始 −12h).date()）
    tool        TEXT NOT NULL,      -- 'AirSense10' | 'AirMini'
    usage_hr    REAL,
    ahi         REAL,               -- AirSense=STR 機器值；AirMini=五類事件÷小時
    hypopnea    INTEGER,            -- 事件計數：AirSense=EVE 逐晚；AirMini=am_nights
    central     INTEGER,
    obstructive INTEGER,
    apnea_unspec INTEGER,
    rera        INTEGER,
    leak_med    REAL,               -- 兩台均為 L/min（刻意漏氣扣除後）
    leak_95     REAL,
    leak_max    REAL,
    press_med   REAL,
    press_95    REAL,
    press_max   REAL,
    hi  REAL, ai  REAL,             -- AirSense=s10_daily 機器日值；AirMini=事件÷時數
    cai REAL, oai REAL, uai REAL,
    PRIMARY KEY (ts, tool)
);
-- am_sessions(start, end 台北時間, duration_min, night, raw_start)、
-- am_nights(date PK, minutes, sessions, ahi…, events_json 在第 11 欄)、
-- am_events（ts=台北、ts_raw=機器原始）、sync_meta/am_meta（key-value 文字註記）。
-- 報告快照例外：機器覆寫日（如午睡+隔日凌晨合併）在 nightly_summary 放匯出時快照值，
-- s10_daily 保留機器現值；理由寫 sync_meta.snapshot_override_<date>。

## AirMini logged_data.json 解析要點

- JSONL：逐行 `json.loads(line)`；結構 `{jsonrpc, method:'LoggedData', params:{logStreamId, data:[...]}}`。
- `data[].dataId` 分流；periodic 流用 `startTime` + `interval`（秒）enumerate `values[]`，
  空值（null）跳過。
- Leak 值 ×60 轉 L/min 的慣例來自 `build_db.py`，與單位對照一起記。
- 預覽慣例：AirMini 87 夜、AirSense 91 夜量級（會隨時間增長）；同夜 inner-join 後
  ahi/usage 先目檢再建庫。

## 跨表 join 陷阱：同一晚的 session 名稱會差幾秒

EVE.edf 與 PLD.edf 的檔名時間戳**不會相同**（例如 `20261101_134247` vs
`20261101_134253`，同一物理 session）。因此：

- s10_events.session 與 s10_pld_2s.session **不能字串相等 join**——直接等值
  join 會靜默得到 0 列（事件線全部消失、統計全空），且無報錯。
- 正確做法：同 night 下解析 session 名為時間，容差 ±90 秒內視為同一 session，
  事件時間 = 事件自身 session 起點 + onset_s。
- 已固化的實作：`scripts/night_curve_check.py`（晝夜圖＋列印過度換氣先行比例）。
