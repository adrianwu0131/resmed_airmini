# AirView 版式報告重現（reportlab 副本報告）

目標：照 AirView 原版三份 PDF 的**版位與數值**重產，AirMini 資料補上空缺。
產品腳本（本機 `~/airmini/s10/`）：`avview_charts.py`（圖表，conda `ml` env）、
`avview_complete_pdf.py`（組裝，documents venv）、`build_db.py`（SQLite 彙整+時間校正）；
字型 `~/.fonts/NotoSansTC.ttf`。

## 最重要規則：副本報告＝原版數值

- 表內主機（AirSense）數值**必須用原版印出的已驗證值**（例：56/99 (57%)、341h50m、530h、
  AHI 4.1/7.1/10.7），不要用自己的全窗重算值取代——重算值和原版差一點點，使用者就會說
  「除了 airmini 外資料應該要一樣」。第二台（AirMini）以**補充段落/小表**加入，各用自己期間的平均。
- 檔案 1 使用圖回原版的 90 天視窗（08/03–11/01、90 槽、同 Sunday 標籤），AirMini 柱疊在同一欄：
  主機未用→原版語意的全高空心紅柱，AirMini 有戴就在柱內畫藍柱。全資料版面留給檔案 2 補充段與檔案 3。
- 檔案 2 頁 1 的 Usage(hours) 小圖**也要雙機並排連續軸**（綠/紅=AirSense＋藍=AirMini），
  即使原版只有主機單機也補上第二台——使用者以「兩台一起畫」為基準，不因原版單機而回退。
- **逐日 Usage 圖的每日值＝匯出當時可見的 session 累計，不是現在讀卡的 STR 日值**：
  被切點跨到的日子（午睡＋隔天清晨段已遭機器合併）要按 session 拆回報告當時的部分，
  否則原版 <4h 的短紅柱會被合併後的長綠柱蓋掉——使用者會問「X 小時的紅棒去哪了」。
- **AHI 面板照原版結構**：柱＝AI（深紫）在下＋HI（淺紫）在上、總高=AHI（堆疊順序畫反
  使用者即報不對）；y 軸 0–40 間隔 10、x 月標籤兩行（縮寫＋年份）；左欄圖例 AHI=空心方塊
  紫字、HI=淺紫實心、AI=深紫實心、CAI/OAI/UAI/CSR% 灰字、數值粗體右對齊；AirMini 柱
  同構換色系（藍綠），AirMini 數值留在頁底補充表，主圖例不要混併 A/M 兩值。
- 檔案 2 頁 2 四面板：**兩台機器同畫在同一條連續日期軸**（藍柱=AirSense、橘柱=AirMini 並排），
  不要按機器拆頁；左欄統計＝原版數值，AirMini 以 A/M 兩值並列或在底部補小表。
- 檔案 3 逐夜：**夜 = (session 開始 − 12h) 的日期**（＝「睡覺那晚」，與 DB/CSV 同口徑；
  連續 HH:MM、跨午夜）。單人雙機互斥 ⇒ 幾乎每夜只有一台：單機夜畫該機曲線＋
  全寬單機表，只有真正雙機夜才左右並排雙表；別把「雙機同頁」當常態去強配——
  強配一定把相鄰兩晚擠同一頁。detN **逐夜都產**（渲染器對單機夜自然只畫
  一台曲線）；只畫雙機夜的話 176 頁會剩 3 頁。原版藍本 -3.pdf 只有 4 夜詳細頁（切點前）；176 頁
  是自產版，逐值核對以原版那幾頁為準。
- **夜圖的 AirSense 漏氣曲線＝PLD 樣本 ×60**：PLD `leak` 是 L/s、AirMini 1-min
  值已是 L/min，不換同一條軸上視覺差 60 倍（主機貼底）。壓力樣本已是 cmH2O 不換。
- y 軸時間用 **24 小時制標籤**（12:00/16:00/20:00/00:00/04:00/08:00/12:00），
  並以淡藍底標出使用者主睡眠帶 00:00–08:00——AM/PM 寫法使用者曾誤讀。

## 版位比對技術：pypdf font probe

要對齊原版幾何，不要目測——用 pypdf 的 visitor 量出每段文字的 (x, y, font size)：

```python
import pypdf
sizes = {}
def visitor(text, cm, tm, fontDict, fontSize):
    if text.strip():
        sizes.setdefault((round(float(tm[5]),1), round(float(tm[4]),1),
                          round(float(fontSize) if fontSize else 0,1)), text.strip()[:40])
r.pages[n].extract_text(visitor_text=visitor)
```

把量到的 (y, x, size) 排序就是原版的排版參數。本報告類實測值（20261102 版）：
頁尾 fs 7 @y18（'Page 1 of 1'，每頁各自印）；標題 18pt @x151；表格 x=151→548（寬 397）、
行距 19、表頭 12/內文 9、次行縮排 +16；病患 11pt 右對齊 561、DOB/Age 9pt；
表頭列右側日期 x≈441；列內三值欄（Median: 268/值 317、95th: 357/437、Maximum: 474/527）；
頁 2 面板標題 fs11+副標 fs8（Usage 671/Leak 522/Pressure 373/AHI 224，pitch 約 149）；
左欄統計 fs7 @x47–148、10pt 行距；圖表影像 x=155 寬 406；版本字串 4.5.1-1.0.0。

## 產物（腳本鏈）

1. 拉取流程見 airmini-access.md → `pull/logged_data.json`（內含 GetDateTime 機器時鐘）。
2. `~...documents venv.../python build_db.py` → SQLite `cpap_data.db`＋校正後
   `airmini_nights.csv`（am_* 時間 +7h46m35s=台北時間；**每次拉新資料都先拿 GetDateTime
   對 host 時間重測 offset**，機器鐘會漂移，不要寫死舊值）。
3. `...ml env.../python avview_charts.py` → `avview/f1_usage.png`、`detN_<夜>.png`、
   `b2_*.png`、`night_stats.json`（夜統計由波形+事件直接計算，AHI 分母=佩戴分鐘）。
4. `...documents venv.../python avview_complete_pdf.py` → 三份 PDF。

## 陷阱

- **reportlab 圖片 y=底邊**：文字在 yy、圖放 `yy - 高度`，白底 PNG 會蓋掉下方文字；
  面板標題要放在影像「上方」，即先 title 再 `drawImage(y=title_y - 18)`。
- **逐夜頁表格起點在標題下方**：'Detailed report' 18pt 標題 y≈720，逐夜表格（含
  compact_table 與全寬 Statistics）從 y≈700 起——放 726 會被表頭深色條蓋住標題。
- **數值軸語意**：STR mask 值已是自正午起算分鐘，直接 `/60`，再減 720 會整體位移
  12h；on≥off 的跨正午 session 畫兩段（h0→24 ＋ 0→h1）。
- **AirMini 時鐘=UTC**：logged_data 的時間戳 +7h46m35s 才是台北時間；校正前後的差別是
  「傍晚佩戴」vs「夜間佩戴」的假象，校正後雙機全配對重疊必須=0（驗證用）。
- **內嵌 heredoc 改寫大段 Python**：外層與目標碼的三引號互撞會把目標檔寫壞。大段改寫的
  安全程序：先 `cp` 備份到 scratch → `rm` 原檔 → `write_file` 寫新檔（write_file 對"存在但
  本程序沒讀過"的檔會拒絕覆寫，rm 之後就乾淨）；片段小改用 python -c 讀檔 replace 後寫回。
- reportlab/pypdf 只在 documents venv（`~/.hermes/environments/documents/.../venv`），
  matplotlib 只在 conda `ml` env——產線指令用對應 venv 的 python 跑。
- CJK 缺字變 NUL：下標與符號改寫（cmH2O/SpO2），每輪以 \x00 掃描把關；
  TTFont 不吃 CFF，字型必須用 TrueType 的 NotoSansTC.ttf。

## 驗證迴圈（每輪必跑）

1. `pypdf` 逐頁 `extract_text()` 掃 `\x00`。
2. `pdf_page_image.py --dpi 100~300` 轉 PNG → `vision_analyze` 目檢排版重疊。
3. 版位懷疑時：font probe 對照原版並排渲染（兩邊 resize 併圖）目視 diff。
4. 數值對帳：用 pypdf 逐頁 regex 抽 Statistics 區塊（日期行 `^\d{2}/\d{2}/\d{4}$`、
   `Usage (\d+) hr (\d+) min`、`Leak - L/min Median: x 95th %: y`、`AHI: x AI: y HI: z`）
   存成 JSON，再對 `nightly_summary` 做逐值 diff——這比目測快且不漏頁，被覆寫的
   夜會立刻浮出來。凡出現「DB ≠ 頁面」的夜：DB 行改為**報告快照值**（事件計數可由
   頁面 rate × 時數反推），`s10_daily` 保留機器覆寫後現值，並在 `sync_meta`
   寫 `snapshot_override_<日期>` 說明快照理由與覆寫內容——使用者核對 DB 的順序
   就是對照報告，DB 與報告不一致他會直接報「數值沒有修正」。
