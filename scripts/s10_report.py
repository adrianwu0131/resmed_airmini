#!/usr/bin/env python3
"""ResMed SD 卡 → AirView 報告重現（合規 / 期間治療 / 每日詳細 + 每日 CSV）

用法：python3 s10_report.py [SD卡根目錄]

規則依據 references/airview-aggregation.md。腳本內 Fixture 區塊（CUT、W_START/W_END、
NAP_MIN、Pages 目標值、午睡檔名）對應一份已驗證的 2026-11-02 AirView 報告——
換新報告/新機器時改這些常數，跑完對照 ✓/Δ 表。
"""
import csv
import datetime as dt
import math
import sys
from pathlib import Path
from statistics import mean

sys.path.insert(0, str(Path(__file__).resolve().parent))
from resmed_s10 import EDF, decode_annotations, parse_str

# ---------------- Fixture（對應 2026-11-02 報告；其他報告請調整）----------------
SD = Path(sys.argv[1] if len(sys.argv) > 1 else '/path/to/sd_dump')
CUT = dt.datetime(2026, 11, 2, 9, 0)      # 報告產生時刻；此後結束的 session 不計
W_START, W_END = dt.date(2026, 7, 26), dt.date(2026, 11, 1)   # 合規窗（99 天）
NAP_MIN = 130                             # 11/01 進報告的午細 session 分鐘數

rows = {r['date']: r for r in parse_str(str(SD / 'STR.edf'))}
window = [rows.get(W_START + dt.timedelta(days=i)) for i in range((W_END - W_START).days + 1)]
window = [r for r in window if r]


def pct_(xs, p):
    xs = sorted(xs)
    n = len(xs)
    k = (n - 1) * p / 100
    f = math.floor(k)
    c = min(f + 1, n - 1)
    return xs[f] + (xs[c] - xs[f]) * (k - f)


def day_sessions(d):
    """資料日 d 已完成（結束 ≤ CUT）的 [(prefix, ts, minutes, PLD)]"""
    ddir = SD / 'DATALOG' / d.strftime('%Y%m%d')
    out = []
    if ddir.exists():
        for f in sorted(ddir.glob('*_PLD.edf')):
            prefix = f.stem.rsplit('_', 1)[0]
            ts = dt.datetime.strptime(prefix, '%Y%m%d_%H%M%S')
            e = EDF(f)
            if ts + dt.timedelta(minutes=e.n_records) <= CUT:
                out.append((prefix, ts, e.n_records, e))
    return out


def events(eve_edf, t0, t1):
    """EVE 註記中 [t0,t1) 秒的事件分類計數"""
    counts = dict(ca=0, oa=0, ua=0, hy=0)
    for _, on, dur, text in decode_annotations(eve_edf):
        o = float(on) if on else None
        if o is None or not (t0 <= o < t1):
            continue
        if text == 'Central Apnea':
            counts['ca'] += 1
        elif text == 'Obstructive Apnea':
            counts['oa'] += 1
        elif text == 'Apnea':
            counts['ua'] += 1
        elif text == 'Hypopnea':
            counts['hy'] += 1
    return counts


# ---------------- 1. 合規報告 ----------------
print('=' * 72)
print('1) 合規報告 → AirView Compliance Report')
print('=' * 72)
strmins = {r['date']: int(round(r['duration'] or 0)) if r['date'] != W_END else NAP_MIN
           for r in window}
used_s = [r for r in window if strmins[r['date']] > 0]
ge4 = [r for r in used_s if strmins[r['date']] >= 240]
lt4 = [r for r in used_s if strmins[r['date']] < 240]
tot = int(round(sum(strmins.values())))
last30 = window[-30:]
ge4_30 = [r for r in last30 if strmins[r['date']] >= 240]
full_used_hours = sum(r['duration'] for d, r in rows.items()
                      if r['on_duration'] not in (None, 0) and d < W_END) / 60
print(f'使用天數      : {len(used_s)}/{len(window)} ({len(used_s)/len(window)*100:.0f}%)')
print(f'>=4 小時      : {len(ge4)} ({len(ge4)/len(window)*100:.0f}%)')
print(f'<4 小時       : {len(lt4)} ({len(lt4)/len(window)*100:.0f}%)')
print(f'未使用        : {len(window)-len(used_s)}')
print(f'總使用時數    : {tot//60}h{tot%60:02d}m')
print(f'使用日平均    : {tot/len(used_s)/60:.1f}h')
print(f'近 30 天合規  : {len(ge4_30)}/{len(last30)} = {len(ge4_30)/len(last30)*100:.0f}%')
print(f'Total used    : {round(full_used_hours)}h（印表日早晨讀值）')
print(f'(fixture 目標 : 56/99、50、6、43、341h50m、6.1h、47%、530h)')

# ---------------- 2. 期間治療統計 ----------------
print()
print('=' * 72)
print('2) 期間治療統計 → AirView Therapy Report')
print('=' * 72)

sub_leak = []
sub_idx = {k: [] for k in ['ahi', 'ai', 'hi', 'cai', 'oai', 'uai', 'hi_ev']}
for r in used_s:
    if r['date'] == W_END and r['date'] == dt.date(2026, 11, 1):
        # 午睡日：漏氣統計的窗要含它（重算自 PLD/EVE）
        pld = EDF(SD / 'DATALOG/20261101/20261101_134253_PLD.edf')
        leak = [v for rec in pld.data for v in rec[pld.idx['Leak.2s']]]
        sub_leak.append((pct_(leak, 50), pct_(leak, 95), max(leak)))
        ev = EDF(SD / 'DATALOG/20261101/20261101_134247_EVE.edf')
        c = events(ev, 0, NAP_MIN * 60)
        n_ap = c['ca'] + c['oa'] + c['ua']
        sub_idx['ahi'].append((n_ap + c['hy']) * 60 / NAP_MIN)
        sub_idx['ai'].append(n_ap * 60 / NAP_MIN)
        sub_idx['hi_ev'].append(c['hy'] * 60 / NAP_MIN)
        sub_idx['cai'].append(c['ca'] * 60 / NAP_MIN)
        sub_idx['oai'].append(c['oa'] * 60 / NAP_MIN)
        sub_idx['uai'].append(c['ua'] * 60 / NAP_MIN)
    else:
        sub_leak.append((r['leak_50'], r['leak_95'], r['leak_max']))
        for k in sub_idx:
            sub_idx[k].append(r[k] or 0)

# AHI 族與壓力 = 全史各日值未加權平均，切至印表日前一天
idx_full = [dict(ahi=r['ahi'] or 0, cai=r['cai'] or 0, oai=r['oai'] or 0, uai=r['uai'] or 0)
            for d, r in rows.items()
            if d < W_END and r['ahi'] is not None and r['on_duration'] not in (None, 0)]
press_full = [(r['mask_press_50'], r['mask_press_95'], r['mask_press_max'])
              for d, r in rows.items()
              if d < W_END and r['mask_press_50'] is not None and r['on_duration'] not in (None, 0)]

ahi_m = mean([x['ahi'] for x in idx_full])
ai_m = sum(mean([x[k] for x in idx_full]) for k in ['cai', 'oai', 'uai'])
hi_display = round(ahi_m, 1) - math.floor(ai_m * 10) / 10   # 顯示鏈 HI = AHI−AI
csr_usage_min = mean([r['on_duration'] for d, r in rows.items()
                      if r['csr'] is not None and r['on_duration'] not in (None, 0) and d < W_END])
csr_min = mean([r['csr'] for d, r in rows.items()
                if r['csr'] is not None and r['on_duration'] not in (None, 0) and d < W_END])

raw_targets = [
    ('AHI', 4.1, ahi_m),
    ('AI', 3.0, ai_m),
    ('HI', 1.1, hi_display),
    ('CAI', 2.6, mean([x['cai'] for x in idx_full])),
    ('OAI', 0.2, mean([x['oai'] for x in idx_full])),
    ('UAI', 0.3, mean([x['uai'] for x in idx_full])),
    ('壓力 median', 7.1, mean([p[0] for p in press_full])),
    ('壓力 95th', 9.5, mean([p[1] for p in press_full])),
    ('壓力 max', 10.7, mean([p[2] for p in press_full])),
    ('漏氣 median', 5.3, mean([l[0] for l in sub_leak]) * 60),
    ('漏氣 95th', 31.8, mean([l[1] for l in sub_leak]) * 60),
    ('漏氣 max', 47.9, mean([l[2] for l in sub_leak]) * 60),
    ('CSR %', 0.1, csr_min / csr_usage_min * 100),
]
print(f'{"指標":<14s}{"AirView":>9s}{"重現 raw":>10s}  判定')
for name, tgt, got in raw_targets:
    r1, t1 = round(got, 1), math.floor(got * 10) / 10
    ok = 'OK' if abs(r1 - tgt) < 0.051 or abs(t1 - tgt) < 0.051 else f'D{abs(r1 - tgt):.2f}'
    print(f'{name:<14s}{tgt:>9.1f}{got:>10.3f}  {ok} (round {r1} / trunc {t1})')
print('(殘差 <=0.1：SD STR 首日前的雲端資料不在卡上，屬已知原因)')

# ---------------- 3. 每日詳細報告對照 ----------------
print()
print('=' * 72)
print('3) 每日詳細報告對照')
print('=' * 72)
# (date, dir, EVE-group prefix, AirView 印出值: 壓力med/95, 漏氣med/95, AHI, AI, HI, CAI)
pages = [
    (dt.date(2026, 10, 29), '20261029', '20261030_023101', (6.4, 8.9, 1.2, 34.8, 3.2, 2.8, 0.4, 2.8)),
    (dt.date(2026, 10, 30), '20261030', '20261031_023205', (6.5, 9.8, 7.2, 58.8, 1.7, 0.6, 1.1, 0.3)),
    (dt.date(2026, 10, 31), '20261031', '20261101_030619', (6.8, 11.2, 14.4, 40.8, 4.5, 3.2, 1.3, 1.9)),
    (dt.date(2026, 11, 1), '20261101', '20261101_134247', (9.0, 9.8, 0.0, 9.6, 9.6, 6.9, 2.7, 6.9)),
]
for d, dirname, eve_prefix, a in pages:
    ddir = SD / 'DATALOG' / dirname
    ev_edf = EDF(ddir / f'{eve_prefix}_EVE.edf')
    dstr = rows.get(d)
    ss = day_sessions(d)
    if dstr is not None and d != W_END:
        mins = int(round(dstr['duration']))
        rate_min = int(round(dstr['on_duration']))
        press_med = round(dstr['mask_press_50'], 1)
        press_95 = round(dstr['tgt_ipap_95'], 1)
        leak_med = round(dstr['leak_50'] * 60, 1)
        leak_95 = round(dstr['leak_95'] * 60, 1)
    elif len(ss) == 1:
        _, _, _, pld = ss[0]      # 機器日值已被合併覆寫 → 用 PLD 重算快照
        press = [v for rec in pld.data for v in rec[pld.idx['MaskPress.2s']]]
        leak = [v for rec in pld.data for v in rec[pld.idx['Leak.2s']]]
        mins, rate_min = NAP_MIN, NAP_MIN
        press_med, press_95 = round(pct_(press, 50), 1), round(pct_(press, 95), 1)
        leak_med, leak_95 = round(pct_(leak, 50) * 60, 1), round(pct_(leak, 95) * 60, 1)
    else:
        raise RuntimeError(f'{d}: session 結構不在 fixture 假設內')
    c = events(ev_edf, 0, mins * 60)
    n_ap = c['ca'] + c['oa'] + c['ua']
    tr = lambda x: math.floor(x * 10 + 1e-9) / 10
    idx = (tr((n_ap + c['hy']) * 60 / rate_min), tr(n_ap * 60 / rate_min),
           tr(c['hy'] * 60 / rate_min), tr(c['ca'] * 60 / rate_min))
    print(f'{d:%m/%d} AirView {mins:>4}m  {a[0]}/{a[1]:<5} {a[2]}/{a[3]:<5}  '
          f'AHI{a[4]} AI{a[5]} HI{a[6]} CAI{a[7]}')
    print(f'{d:%m/%d} 重現   {mins:>4}m  {press_med}/{press_95:<5} '
          f'{leak_med}/{leak_95:<5}  AHI{idx[0]} AI{idx[1]} HI{idx[2]} CAI{idx[3]}')

# ---------------- 4. CSV ----------------
out_csv = Path('daily_report.csv')
with open(out_csv, 'w', newline='', encoding='utf-8-sig') as fp:
    w = csv.writer(fp)
    w.writerow(['date', 'duration_min', 'on_duration_min', 'mask_on(12h差,分)', 'mask_off',
                'press50', 'press95', 'pressMax', 'tgtIPAP95',
                'leak50_Lmin', 'leak95_Lmin', 'leakMax_Lmin',
                'AHI', 'HI', 'AI', 'CAI', 'OAI', 'UAI', 'CSR_min'])
    for d, r in rows.items():
        if d is None:
            continue
        w.writerow([d, r['duration'], r['on_duration'], r['mask_on'], r['mask_off'],
                    r['mask_press_50'], r['mask_press_95'], r['mask_press_max'], r['tgt_ipap_95'],
                    None if r['leak_50'] is None else round(r['leak_50'] * 60, 1),
                    None if r['leak_95'] is None else round(r['leak_95'] * 60, 1),
                    None if r['leak_max'] is None else round(r['leak_max'] * 60, 1),
                    r['ahi'], r['hi'], r['ai'], r['cai'], r['oai'], r['uai'], r['csr']])
print(f'\n每日摘要：{out_csv.resolve()}（{len(rows)} 天）')
print('注意：mask_on/mask_off 為 12 小時制位差值，機器時刻 = (值-720) mod 1440 分。')
