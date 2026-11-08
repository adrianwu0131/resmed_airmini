# ResMed AirSense 10 SD 卡格式速查

## 目錄結構

```
SD├─ Identification.tgt    # #SRN 機器序號、#PNA 型號（AirSense_10_AutoSet…）
├─ Identification.crc
├─ Journal.dat            # 未解析
├─ settings/ *.tgt        # 設定組（未解析；設定值在 STR 的 S.* 通道就有）
├─ STR.edf                # 每日總摘要：1 記錄/天，record_dur=86400s
└─ DATALOG/<yyyymmdd>/    # 目錄名 = 資料日（歸日規則見 aggregation reference）
   └─ <yyyymmdd_HHMMSS>_{CSL,EVE,BRP,PLD,SAD}.edf + .crc
```

檔名時間戳 = session 實際起始（機器時鐘）。一個資料日可有多組（mask off 超過閾值就換組；
CSL/EVE 只屬於第一組、但 EVE 事件涵蓋全日各 session，onset 以 EVE 起始為 0 秒）。

## EDF header（欄位皆 ASCII、逐欄排列）

root：version(8) patient(80) recording(80) startdate(8,dd-mm-yy) starttime(8,hh-mm-ss)
hdr_bytes(8) reserved(44) n_records(8) record_dur(8) ns(4)；之後每訊號一欄
（label 16、transducer 80、units 8、phys_min 8、phys_max 8、dig_min 8、dig_max 8、
prefilter 80、samples_per_record 8、reserved 32；合計 256B/訊號），hdr_bytes = 256+ns×256。

物理值 = phys_min + (raw − dig_min) × (phys_max−phys_min)/(dig_max−dig_min)。

## STR.edf 通道（ns=80；MaskOn/MaskOff 各 10 個樣本/記錄，其餘 1）

| 通道 | 意義 | 單位/縮放 |
|---|---|---|
| Date |  Unix 天數（1970 起算） | 1 天 |
| MaskOn / MaskOff | 各至多 10 個 mask 起訖（−1=未用）| 分鐘，12h 位差：機器=(v−720) mod 1440 |
| MaskEvents | mask 事件數 | 1 |
| Duration / OnDuration | mask-on / 通電分鐘 | 分鐘 |
| PatientHours | 機器錶時數（會 reset） | 小時，≈ΣDuration（±0.4%） |
| Mode、S.*、S.AS.MinPress/MaxPress | 設定（AutoSet 4-20 等） | 列舉/0.02 cmH2O |
| MaskPress.50/.95/.Max | 面罩壓力百分位 | phys 30/dig 1500 → 0.02 cmH2O |
| TgtIPAP.50/.95/.Max、TgtEPAP.… | 目標壓力（單壓=面罩近似） | 同上 |
| Leak.50/.95/.70/.Max | 漏氣百分位（含 mask-off 尖峰） | phys 2/dig 100 → 0.02 L/s（×60=L/min） |
| MinVent.-. / RespRate.-. / TidVol.-. | 通氣/呼吸率/潮氣量百分位 | L/min、bpm、L |
| SpO2.50/.95/.Max、Pulse（SAD） | 無血氧模組時全 0 | %、bpm |
| AHI/HI/AI/OAI/CAI/UAI | 事件指標 | phys 240/dig 2400 → 0.1 /hr |
| CSR | CSR 時數 | 分鐘 |
| Fault.* / Crc16 | 故障碼/CRC | — |

## PLD.edf（60s/記錄；各通道 30 樣本 + Crc16 1）

MaskPress.2s(40/2000)、Press.2s(30/1500)、EprPress.2s(30/1500)、Leak.2s(2/100 L/s)、
RespRate.2s(50/250)、TidVol.2s(4/200)、MinVent.2s(30/240)、Snore.2s(5/250)、FlowLim.2s(1/100)。
sample 數 = record_dur×(解析度)，2s → 30/記錄。

## SAD.edf（60s/記錄）

Pulse.1s(18/300 bpm)、SpO2.1s(100/100)——無血氧模組時兩者全 0；Crc16 1。

## EVE/CSL（EDF+ 註記）

- 訊號：`EDF Annotations`(19 樣本/記錄) + `Crc16`(1)。機器非標：**每 int16 裝 2 字元**。
- 每記錄：`+0`(timekeep) → 成對 token：`+onset[\x15dur]` + 描述文字。
- 事件文字：『Recording starts』『Central Apnea』『Obstructive Apnea』『Apnea』（=不明型）『Hypopnea』。
- Apnea 的 `\x15` 後 = 實際秒數（10~30s）；**Hypopnea 的 `\x15` 後是子型（0/1），不是秒數**。
- CSL 每資料日 1 記錄，多為空 token（session/mask 事件不在內）。onset 單位=秒，自 EVE 起始。

## BRP.edf

75Hz 高解析流量波形（每晚 ~2.5MB）。重現 AirView 報告用不到，先不解。

## 其他已知非標準點

- 未上傳完成的 session：機器稍後把 STR 日值**覆寫**成合併值——重現報告當時的頁面要用 PLD 重算。
- 事件率分母：AirView 用 OnDuration（通電分鐘），顯示截尾 0.1。

## 用獨立版 edfparse（~/airmini/s10/edfparse.py）解析 EVE/CSL

此複本與 scripts/resmed_s10.py 的 EDF 不同：`EDF` 物件**沒有 idx/data/rawsamples**，整個檔案是 `.raw`（bytes）；資料區自 `.header_bytes` 起。
- EVE/CSL 註記檔：2 通道（Annotations+Crc16）、每記錄 720 B = 每通道 360 int16、header 768 B。
- 踩坑：`e.raw[0]` 是 int（整檔第一個 byte），不是分通道陣列；照 scripts 版寫 `rawsamples[r][chan]` 直接炸 AttributeError。

```python
import struct
from edfparse import EDF
e = EDF(path)
chan = e.labels.index('EDF Annotations')
for r in range(e.n_records):
    off = e.header_bytes + r*720
    raw = e.raw[off+chan*720 : off+(chan+1)*720]
    buf = b''.join(struct.pack('<h', v) for v in struct.unpack('<360h', raw))
    txt = buf.decode('latin-1')   # 之後 \x14 切段、去 timekeep，同 SD 卡鐵則
```
