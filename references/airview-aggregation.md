# AirView 統計重現規則（2026-11-02 報告實測逐值驗證）

## 日期歸屬與切點

- **Session→資料日**：起始 ≥12:00 → 當日；<12:00 → 前一天。DATALOG 目錄名 = 資料日，
  STR 每日列同規則（該日全部 session 合併）。
- **切點 CUT**：報告產生時（讀卡/上傳時點，多在印表日早晨）尚未結束的 session 全部排除：
  合規/治療統計不計、詳細報告頁缺頁或只剩白天已完成的部分。
  判斷法：找出結束時刻 > 讀卡時點的最後一個 session。
- **讀錶時點 = 印表日早晨**：Total used hours、AHI 類與壓力全史平均都切到印表日前一天深夜。

## 合規報告（Compliance）

- window = 標頭日期區間（例：26/07–01/11 = 99 天；頁首寫 "90 day period"，分母仍 99）。
- 使用天數 = Σ Duration > 0；≥4h = Duration ≥ 240 分；<4h 其餘；未使用 = 99−使用。
- 總時數 = Σ Duration；使用日平均 = 總時數/使用天數。
- 近 30 天 = window 最後 30 天中 ≥240 分的天數/30。
- Total used hours = 全史 Σ Duration（至印表日前一天）。

## 每日詳細報告頁

- 使用 = STR Duration（h:mm 格式）。壓力 median = MaskPress.50、95th = **TgtIPAP.95**
  （round 到 1 位）。漏氣 med/95 = Leak.50/.95 ×60（L/min）。
- AHI/AI/HI/CAI = trunc(事件數 ×60 / **OnDuration**)；事件在 EVE 的 [0, Duration×60) 秒窗內
  按文字分類計數。
- 同日多 session 全部合併成一頁；只有當「部分 session 未上傳完成」時才只剩已完成的頁
  （此頁是機器快照：SD 日值後來被覆寫，重算時用該 session 的 PLD，結果標註為近似）。

## 期間治療統計（Therapy Report）

| 統計 | 公式 | 顯示 |
|---|---|---|
| AHI/AI/CAI/OAI/UAI | 全史（STR 首日→印表日前一天）各日值、**未加權平均** | trunc 或 round 混用（逐值驗證） |
| AI | CAI+OAI+UAI 之原始和 | trunc（3.087→3.0） |
| HI | **顯示鏈 = 顯示 AHI − 顯示 AI**（4.1−3.0=1.1） | — |
| 壓力 median/95th/Max | 同 AHI 族（全史各日 MaskPress.50/.95/Max 平均） | trunc（7.175→7.1） |
| 漏氣 med/95/Max | **報告窗各日值平均**（含最後日部分 session）×60 | trunc（31.886→31.8） |
| CSR % | Σ(csr)/Σ(使用分鐘)×100 | round（0.072→0.1） |
| Total used hours | 全史 Σ Duration 至印表日前一天 | 530h 例 |

- 不要用 PLD 樣本池化重算期間百分位——機器內部演算法與 2s 池化差 0.1~0.3。
- 加權平均（按時數）是錯的方向：AHI 加權會得 3.9 而非 4.1，未加權才命中。
- 殘差 ≤0.1 的已知原因：SD STR 不含首個 STR 日之前的雲端上傳資料
  （UAI 差 ≈3 事件、壓力 95th 差 ≈0.08）——如實標註 Δ，不要硬湊。

## 驗證閘門

每個輸出值對照 AirView PDF；對不上時依序查：日期歸屬 → 切點 → 分母
（OnDuration vs Duration）→ round/trunc 顯示 → 全史 vs window 平均 → 最後才懷疑公式。
