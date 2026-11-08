# 中樞型呼吸中止（CSA）——名詞、機制與治療選項速查

用途：使用者（CPAP 使用者，於睡眠專科門診追蹤中）會在資料專案中間問醫療名詞、
事件種類與治療方式。本檔只做名詞/衛教支持與資料描述，不做診斷；治療決策屬治療醫師。

## 名詞（對應 DB 的 kind 欄）

| 名詞 | 意義 | 對應欄/事件 |
|------|------|------------|
| Hypopnoea（低通氣） | 氣流下降 ≥30% 持續 ≥10 秒，通常伴 ≥3% 血氧降或醒覺——呼吸「變淺」非「停止」 | `Hypopnea` / `HypopneaEnd` → hypopnea 計數 |
| Central（中樞型） | 呼吸完全停止 ≥10 秒**且無呼吸用力**（腦幹未下指令）；氣道是通的，患者通常無感 | `Central Apnea` / `CentralApneaEnd` → central 計數 |
| Obstructive（阻塞型） | 氣道塌陷、身體持續用力但吸不到氣——一般典型 OSA，CPAP 的原理就是撐開氣道 | `Obstructive Apnea` / `ObstructiveApneaEnd` |
| Unknown（不明） | 呼吸中止但分類訊號不足 | `Apnea` / `ApneaEnd` → apnea_unspec |
| CSR（Cheyne-Stokes） | 漸強漸弱的週期呼吸波形（心衰竭相關） | STR `CSR` 通道；本機長期 ≈0 |

- AHI = (Hyp + CA + OA + UA) ÷ 小時；AI = 停止類三項；HI = 變淺類。
- **短段分母效應**：5 事件 ÷ 0.42h ≈ 12/小時——判讀高 AHI 前先看當夜時數。
- TECSA（治療中出現型 CSA）：CPAP 治療後出現/加重的中樞事件（約 1% CPAP 啟用者），
  機制與換氣過衝（loop gain：CO₂ 被沖低於觸發閾 → 腦幹關呼吸 → 堆回再開）相關。

## 本機資料圖像（夜別統一口徑後）

- 事件組成：AirSense 全史 中樞 1,432 / 阻塞 136 / 不明 166——**以中樞為主**。
- 中樞事件 90–100% 發生在換氣爆發（MinVent 衝峰）之後 1–4 分鐘，未見壓力峰相關；
  聚集於入睡後第一小時與每夜最後 1–1.5 小時（淺睡期）。CSR 波形長期 ≈0。
- 量級：整夜 AHI 多 2–6/小時；整夜使用時兩台 10 月最高僅 6.0 / 7.3。
- **缺口**：AirSense 無血氧模組（SpO2 全空）→ 事件當下缺氧負擔未知；
  建議加指夾式血氧機並與夜資料合併（現況最有價值的下一步量測）。

## 治療選項（2025 AASM 指引，皆為 conditional 推薦；選擇權在醫師）

| 方式 | 備註 |
|------|------|
| CPAP（現行） | 各成因 CSA 均可小幅改善；先確認裝置設定 |
| ASV | TECSA/原發性 CSA 降中樞事件最有效；**⚠ 心衰竭 LVEF≤45% 禁用（SERVE-HF 增加死亡率）**；本機兩台皆 AutoSet 無此模式，需要時＝另試機器 |
| BiPAP-BUR（雙壓+備用速率） | ASV 的替代 |
| 夜間低流量氧 | 心衰竭併 CSA 時 |
| Acetazolamide | 碳酸酐酶抑制劑，HF/高山型；台灣 off-label，耐受度有限 |
| TPNS（remedē 橫膈神經植入） | 其他治療失敗者的第二線；亞太地區實務罕得 |

來源：AASM 2025 CSA 臨床指引（aasm.org「A look at the new guideline for treating central
sleep apnea in adults」）、SERVE-HF / ADVENT-HF 試驗、apneaboard OSCAR leaks wiki。
門診帶圖用 `scripts/night_curve_check.py` 產生的夜圖＋`nightly_summary` 的逐夜 ai/cai 序列。
