#!/usr/bin/env python3
"""fix_am_tables.py — 修正 11/27 新建的 AirMini 表：續批合併 + 校正方向

兩個 bug：
1. GetLoggedData 同一 startTime 的批次是「續段」（values 延續），按 per-sample
   UNIQUE(ts) 入庫會把續段整段丟掉（p1min 只剩第一截，11/05-11/23 全洞）。
   → 合併策略：同 (dataId, startTime) 取 values 最長那批。
2. 校正方向：機器鐘**落後**本機 UTC ~813s（11/27 實測）→
   UTC = machine + 813s；此前 ingest_logged 寫成 machine − 813s（方向反了）。
   → 本腳本統一：am_leak_p1min / am_press_p1min / am_respiratory_events2 的
     ts = machine + 813s（≈ 真 UTC，ISO 無時區）。

 am_events：>2026-11-04 的舊列（+7:46:35 慣例）刪除後以新慣例重插；
 舊日期（<=11/04）的 am_events/am_sessions 為 11/06 產物，不動。
 am_nights：DELETE date>'2026-11-04' 後重算（夜別 = (start_utc + 4h).date()
 ＝ (start_local − 12h).date()，等價舊口徑）。
 最後匯出 airmini_nights.csv（同欄序）。
"""
import json
import os
import re
import statistics as st
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime as dt, timedelta
from pathlib import Path

PULL = Path(os.environ.get('AIRMINI_JSONL', 'pull/logged_data.jsonl'))
CSV_OUT = Path(os.environ.get('AIRMINI_NIGHTS_CSV', 'airmini_nights.csv'))
DELTA = timedelta(seconds=813)          # 機器 − UTC 的偏移量（機器落後）
OLD_MAX = '2026-11-04'
_TZ = re.compile(r'(Z|[+-]\d{2}:?\d{2})$')


def _naive(machine_s):
    s = _TZ.sub('', machine_s.replace('+00:00', ''))
    d = dt.fromisoformat(s)
    return d.replace(tzinfo=None) if d.tzinfo else d


def utc_s(machine_s):
    return (_naive(machine_s) + DELTA).isoformat(timespec='seconds')

con = sqlite3.connect('/home/adrian/airmini/cpap_data.db', timeout=60)
con.execute('PRAGMA busy_timeout=60000')
cur = con.cursor()

# ---- 一次掃 JSONL；同 (dataId, startTime) 保留最長 values ----
longest = {}          # (dataId, startTime) -> (interval, values)
events = {}           # (time, kind) -> duration
mask = {}             # time -> kind
for line in open(PULL, encoding='utf-8'):
    if not line.strip():
        continue
    j = json.loads(line)
    for d in j.get('params', {}).get('data', []):
        did = d.get('dataId', '')
        if did.startswith('TherapyOneMinutePeriodic'):
            pr = d.get('periodic')
            if not pr or pr.get('startTime') is None:
                continue
            k = (did, pr['startTime'])
            v = pr.get('values') or []
            if k not in longest or len(v) > len(longest[k][1]):
                longest[k] = (pr.get('interval', 60), v)
        elif did == 'TherapyEvents-RespiratoryEvent':
            for e in d.get('events', []):
                events.setdefault((e['time'], e['event']),
                                  float(e.get('durationSeconds', 0) or 0))
        elif did == 'UsageEvents-TherapyStatusEvent':
            for e in d.get('events', []):
                mask.setdefault(e['time'], e['event'])
print(f'periodic 批次（去重後）: {len(longest)}　事件 {len(events)}　lifecycle {len(mask)}')

# ---- 重建 p1min（ts = machine + 813s）----
cur.execute('DROP TABLE IF EXISTS am_leak_p1min_tmp')
cur.execute('DROP TABLE IF EXISTS am_press_p1min_tmp')
cur.execute('CREATE TABLE am_leak_p1min_tmp (ts TEXT PRIMARY KEY, leak_lmin REAL)')
cur.execute('CREATE TABLE am_press_p1min_tmp (ts TEXT PRIMARY KEY, press_cmh2o REAL)')
L, P = [], []
for (did, st_), (iv, vals) in longest.items():
    t0 = _naive(st_) + DELTA
    for i, v in enumerate(vals):
        if v is None:
            continue
        ts = (t0 + timedelta(seconds=iv * i)).isoformat(timespec='seconds')
        if 'Leak' in did:
            L.append((ts, round(v * 60, 3)))
        else:
            P.append((ts, round(v, 3)))
cur.executemany('INSERT INTO am_leak_p1min_tmp VALUES (?,?)', L)
cur.executemany('INSERT INTO am_press_p1min_tmp VALUES (?,?)', P)
cur.execute('DROP TABLE am_leak_p1min')
cur.execute('DROP TABLE am_press_p1min')
cur.execute('ALTER TABLE am_leak_p1min_tmp RENAME TO am_leak_p1min')
cur.execute('ALTER TABLE am_press_p1min_tmp RENAME TO am_press_p1min')
con.commit()
print(f'p1min 重建：leak {len(L):,}、press {len(P):,}')

# ---- 重建 events2（ts = machine + 813s）----
cur.execute('DROP TABLE IF EXISTS am_respiratory_events2')
cur.execute("CREATE TABLE am_respiratory_events2 (ts TEXT NOT NULL, kind TEXT NOT NULL, "
            "duration_s REAL, UNIQUE(ts, kind))")
EV = []
for (t, kind), dur in events.items():
    EV.append((utc_s(t), kind, dur))
cur.executemany('INSERT INTO am_respiratory_events2 VALUES (?,?,?)', EV)
con.commit()
print(f'events2 重建: {len(EV):,} 筆')

# ---- am_events（>OLD_MAX 重插新慣例；舊不動）----
cur.execute('DELETE FROM am_events WHERE ts > ?', (OLD_MAX + 'T23:59:59',))
for t, kind in mask.items():
    if kind in ('MaskOn', 'MaskOff', 'PowerOff', 'PowerUp'):
        ts = utc_s(t)
        if ts > OLD_MAX + 'T00:00:00':
            cur.execute('INSERT OR IGNORE INTO am_events VALUES (?,?,?,?)',
                        (ts, kind, '', t))
con.commit()

# ---- sessions（UTC 域配對）→ 新夜重算 am_nights ----
pairs = sorted((_naive(t), k) for t, k in mask.items()
               if k in ('MaskOn', 'MaskOff', 'PowerOff'))
am_iv, open_on = [], None
for t, k in pairs:
    if k == 'MaskOn' and open_on is None:
        open_on = t
    elif k in ('MaskOff', 'PowerOff') and open_on is not None:
        if t > open_on:
            am_iv.append((open_on, t))
        open_on = None

def night_of(t_utc):
    # 夜 = (start_local − 12h).date() = (start_utc + 8h − 12h) = (start_utc − 4h)
    return (t_utc - timedelta(hours=4)).date().isoformat()

by_night = defaultdict(list)
for s, e in am_iv:
    by_night[night_of(s)].append((s, e))
new_nights = sorted(n for n in by_night if n > OLD_MAX)
print('新夜:', new_nights)

def pct(arr, q):
    arr = sorted(arr)
    return arr[max(0, int(len(arr) * q) - 1)] if arr else 0.0

cur.execute("DELETE FROM am_nights WHERE date > ?", (OLD_MAX,))
added = 0
for nd in new_nights:
    ivs = by_night[nd]
    mins = sum((e - s).total_seconds() / 60 for s, e in ivs)
    lo = min(s for s, e in ivs) - timedelta(minutes=1)
    hi = max(e for s, e in ivs) + timedelta(minutes=1)
    ls = cur.execute('SELECT leak_lmin FROM am_leak_p1min WHERE leak_lmin IS NOT NULL '
                     'AND ts BETWEEN ? AND ?', (lo.isoformat('T'), hi.isoformat('T'))).fetchall()
    ps = cur.execute('SELECT press_cmh2o FROM am_press_p1min WHERE press_cmh2o IS NOT NULL '
                     'AND ts BETWEEN ? AND ?', (lo.isoformat('T'), hi.isoformat('T'))).fetchall()
    leak_l = [v for (v,) in ls]
    press_l = [v for (v,) in ps]
    ev = Counter()
    for s, e in ivs:
        for tsx, kind in cur.execute(
                'SELECT ts, kind FROM am_respiratory_events2 WHERE ts >= ? AND ts <= ?',
                ((s - timedelta(minutes=1)).isoformat('T'), e.isoformat('T'))):
            t2 = dt.fromisoformat(tsx)
            if s - timedelta(minutes=1) <= t2 <= e:
                ev[kind] += 1
    ca = ev.get('CentralApneaEnd', 0)
    oa = ev.get('ObstructiveApneaEnd', 0)
    ua = ev.get('ApneaEnd', 0)
    hy = ev.get('HypopneaEnd', 0)
    hours = mins / 60 or 1e-9
    cur.execute('INSERT OR REPLACE INTO am_nights VALUES (%s)' % ','.join('?' * 17), (
        nd, round(mins), len(ivs),
        (ca + oa + ua + hy) / hours, (ca + oa + ua) / hours, hy / hours,
        ca / hours, oa / hours, ua / hours, ev.get('ReraEnd', 0),
        json.dumps(dict(CA事件=ca, OA=oa, UApnea=ua, Hypopnea=hy)),
        st.median(leak_l) if leak_l else 0.0, pct(leak_l, .95),
        max(leak_l) if leak_l else 0.0,
        st.median(press_l) if press_l else 0.0, pct(press_l, .95),
        max(press_l) if press_l else 0.0))
    added += 1
con.commit()
print(f'am_nights 重算 {added} 夜')

# 匯出 CSV
import csv
cols = ['date', '使用分', 'sessions', 'AHI', 'AI', 'HI', 'CAI', 'OAI', 'UAI', 'RERA',
        'events_json', '漏氣med', '漏氣95', '漏氣max', '壓力med', '壓力95', '壓力max']
rows = cur.execute(
    "SELECT date, minutes, sessions, ahi, ai, hi, cai, oai, uai, rera, events_json, "
    "leak_med, leak_95, leak_max, press_med, press_95, press_max FROM am_nights ORDER BY date").fetchall()
with open(CSV_OUT, 'w', encoding='utf-8-sig', newline='') as fp:
    w = csv.writer(fp)
    w.writerow(cols)
    w.writerows(rows)
print(f'CSV 匯出 {len(rows)} 夜')
con.close()
