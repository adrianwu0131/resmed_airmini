#!/usr/bin/env python3
"""ingest_stream.py — 把 AirMini 即時串流 jsonl 寫進彙整庫的 am_stream_wave 表

設計要點（對應 references/airmini-access.md「收後流程」）：

1. **一報多樣本要展開時間**：每次 StreamData 報含 N 個樣本但只有一個 startTime，
   樣點 i 的時刻 = startTime + i × interval_ms。不展開會讓同一報的樣本撞同一
   timestamp，被 UNIQUE 約束當重複丟棄（實測 10,125 筆只剩 3,093 筆）。
2. **不覆寫既有資料**：INSERT OR IGNORE + UNIQUE(ts, channel, interval_ms, stream_id)，
   可重跑、冪等。
3. **offset 由資料本身量，而且要「每個 stream 各自量」**：
   比較機器 startTime 與 jsonl 的 recv（本機 UTC）即得偏移。機器錶落後本機
   ⇒ ts_corrected = ts + offset；原始 ts 一併保留。

   **不要用寫死的常數**（2026-11-27 修正）：先前以為偏移是常數（-807 秒），
   實測跨 20 天漂了 11～15 秒，單次連線內也有 33 秒變動。同一個 jsonl 裡
   不同 streamId 的偏移可以差 15 秒以上，**用一個常數會讓部分資料錯到半分鐘**。
   現在改成：按 streamId 分組，各自用該組的實測中位數作為 offset，
   沒有的組才退回 `--offset` 或預設值。

用法：
    python3 ingest_stream.py --dry          # 只統計不寫入
    python3 ingest_stream.py                 # 增量匯入（預設庫 = ~/airmini/cpap_data.db）
    python3 ingest_stream.py --offset 807    # 資料缺 recv 時的退場值
    python3 ingest_stream.py --force-recalc  # 重算已入庫列的 ts_corrected
"""
import argparse
import json
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

DEFAULT_DB = os.path.expanduser('~/airmini/cpap_data.db')
DEFAULT_JSONL = os.path.expanduser('~/airmini/s10/pull/stream_data.jsonl')
# 退場值：只在 jsonl 缺 recv 欄位時才用（實測 11/7 約 807 秒，但會漂）
FALLBACK_OFFSET_S = 807.0

DDL = """
CREATE TABLE IF NOT EXISTS am_stream_wave (
    ts           TEXT NOT NULL,
    channel      TEXT NOT NULL,
    value        REAL,
    interval_ms  INTEGER,
    stream_id    INTEGER,
    ts_corrected TEXT,
    offset_s     REAL,
    UNIQUE(ts, channel, interval_ms, stream_id)
);
CREATE INDEX IF NOT EXISTS ix_am_stream_wave_ts ON am_stream_wave(ts);
CREATE INDEX IF NOT EXISTS ix_am_stream_wave_ch ON am_stream_wave(channel, ts);
"""


def parse_ts(s):
    # '2026-11-07T09:46:40.145Z' → naive datetime（與其他 am_* 表同口徑）
    return datetime.fromisoformat(s.replace('Z', ''))


def recv_to_naive(ms):
    """jsonl 的 recv（unix 毫秒）→ naive UTC datetime。"""
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).replace(tzinfo=None)


def measure_offsets(path):
    """量每個 streamId 的時鐘偏移（機器落後本機，所以為負）。

    回傳 {stream_id: (中位偏移, 筆數, 範圍最小, 範圍最大)}。
    """
    samples = defaultdict(list)
    with open(path) as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            p = r.get('data', {}).get('params', {})
            st = p.get('startTime')
            if not st or 'recv' not in r:
                continue
            dev = parse_ts(st)
            recv = recv_to_naive(r['recv'])
            samples[p.get('streamId')].append((dev - recv).total_seconds())
    out = {}
    for sid, offs in samples.items():
        offs.sort()
        out[sid] = (offs[len(offs) // 2], len(offs), offs[0], offs[-1])
    return out


def load_rows(path, offsets, fallback):
    """展開一報多樣本的時間，並套用各 stream 自己的 offset。"""
    rows, meta = [], {}
    for line in open(path):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        p = r.get('data', {}).get('params', {})
        st = p.get('startTime')
        if not st or 'data' not in p:
            continue
        iv = p.get('intervalMs')
        sid = p.get('streamId')
        off = offsets.get(sid, (fallback, 0, 0, 0))[0]
        t = parse_ts(st)
        for d in p['data']:
            for ch, vals in d.items():
                for i, v in enumerate(vals):
                    # 樣點 i 的真實時刻 = 報的 startTime + i × interval
                    ti = t + timedelta(milliseconds=(i * iv) if iv else 0)
                    rows.append((ti.isoformat(), ch, v, iv, sid,
                                 (ti + timedelta(seconds=off)).isoformat(),
                                 off))
        meta.setdefault(sid, (iv, st, off))
    return rows, meta


def recalc_corrected(con, offsets):
    """用新量到的 offset 重算既有列的 ts_corrected（不動 ts、不動 value）。"""
    changed = 0
    for sid, (off, _n, _lo, _hi) in offsets.items():
        pairs = con.execute(
            'SELECT ts, channel, interval_ms FROM am_stream_wave '
            'WHERE stream_id = ? AND offset_s IS NOT ?', (sid, off)).fetchall()
        if not pairs:
            continue
        con.executemany(
            'UPDATE am_stream_wave SET ts_corrected = ?, offset_s = ? '
            'WHERE ts = ? AND channel = ? AND interval_ms IS ? AND stream_id = ?',
            [(f'{(parse_ts(ts) + timedelta(seconds=off)).isoformat()}', off,
              ts, ch, iv, sid) for (ts, ch, iv) in pairs])
        changed += len(pairs)
    return changed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', default=DEFAULT_DB)
    ap.add_argument('--jsonl', default=DEFAULT_JSONL)
    ap.add_argument('--offset', type=float, default=FALLBACK_OFFSET_S,
                    help='退場偏移（只在資料缺 recv 時用）')
    ap.add_argument('--dry', action='store_true')
    ap.add_argument('--force-recalc', action='store_true',
                    help='用新量到的 offset 重算既有列的 ts_corrected')
    args = ap.parse_args()

    if not os.path.exists(args.jsonl):
        print(f'找不到 {args.jsonl}')
        return 1

    offsets = measure_offsets(args.jsonl)
    print('各 stream 實測時鐘偏移（機器落後本機）:')
    for sid in sorted(offsets, key=lambda x: (x is None, x)):
        med, n, lo, hi = offsets[sid]
        print(f'  streamId={sid}: 中位 {med:+8.1f}s  n={n:>6}  範圍 {lo:+.1f}~{hi:+.1f}s')
    if offsets:
        meds = [v[0] for v in offsets.values()]
        if max(meds) - min(meds) > 1.0:
            print(f'  ⚠ 各 stream 偏移相差 {max(meds) - min(meds):.1f}s'
                  f' → 必須分 stream 校正；用單一常數會錯到約 {(max(meds) - min(meds)) / 2:.0f}s')

    rows, meta = load_rows(args.jsonl, offsets, args.offset)
    print(f'解析出 {len(rows)} 個高解析資料點')
    for sid, (iv, st, off) in sorted(meta.items(), key=lambda x: str(x[0])):
        print(f'  streamId={sid} intervalMs={iv} offset={off:+.1f}s 起始(機器時間)={st}')
    if not rows:
        return 0
    if args.dry:
        print('--dry：不寫入')
        return 0

    con = sqlite3.connect(args.db, timeout=30)
    con.execute('PRAGMA busy_timeout=30000')   # WAL 下多寫入者共存
    try:
        con.execute('BEGIN IMMEDIATE')
        con.executescript(DDL)
        before = con.execute('SELECT COUNT(*) FROM am_stream_wave').fetchone()[0]
        con.executemany(
            'INSERT OR IGNORE INTO am_stream_wave '
            '(ts, channel, value, interval_ms, stream_id, ts_corrected, offset_s) '
            'VALUES (?,?,?,?,?,?,?)', rows)
        after = con.execute('SELECT COUNT(*) FROM am_stream_wave').fetchone()[0]
        con.commit()
        print(f'寫入完成：am_stream_wave {before} → {after}（新增 {after-before}，'
              f'忽略重複 {len(rows)-(after-before)}）')
        for ch, n in con.execute(
                'SELECT channel, COUNT(*) FROM am_stream_wave GROUP BY channel'):
            print(f'    {ch}: {n}')
        if args.force_recalc:
            con.execute('BEGIN IMMEDIATE')
            n = recalc_corrected(con, offsets)
            con.commit()
            print(f'  重算 ts_corrected：{n} 列')
    except Exception as e:
        con.rollback()
        print(f'寫入失敗（已回滾）：{e}')
        return 1
    finally:
        con.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
