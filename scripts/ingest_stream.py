#!/usr/bin/env python3
"""ingest_stream.py — 把 AirMini 即時串流 jsonl 寫進彙整庫的 am_stream_wave 表

設計要點（對應 references/airmini-access.md「收後流程」）：

1. **一報多樣本要展開時間**：每次 StreamData 報含 N 個樣本但只有一個 startTime，
   樣點 i 的時刻 = startTime + i × interval_ms。不展開會讓同一報的樣本撞同一
   timestamp，被 UNIQUE 約束當重複丟棄（實測 10,125 筆只剩 3,093 筆）。
2. **不覆寫既有資料**：INSERT OR IGNORE + UNIQUE(ts, channel, interval_ms, stream_id)，
   可重跑、冪等。
3. **offset 由資料本身量**：比較機器 startTime 與 jsonl 的 recv（本機 UTC）即得偏移。
   機器錶落後本機 ⇒ ts_corrected = ts + offset；原始 ts 一併保留。

用法：
    python3 ingest_stream.py --dry          # 只統計不寫入
    python3 ingest_stream.py                 # 增量匯入（預設庫 = ~/airmini/cpap_data.db）
    python3 ingest_stream.py --offset 807    # 指定偏移秒數
"""
import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta

DEFAULT_DB = os.path.expanduser('~/airmini/cpap_data.db')
DEFAULT_JSONL = os.path.expanduser('~/airmini/s10/pull/stream_data.jsonl')
# 實測：機器時鐘固定落後本機 UTC 約 807 秒（跨 4 小時漂移僅 0.7 秒）
DEFAULT_OFFSET_S = 807.0

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


def load_rows(path, offset_s):
    rows, meta = [], {}
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
            if not st or 'data' not in p:
                continue
            iv = p.get('intervalMs')
            sid = p.get('streamId')
            t = parse_ts(st)
            for d in p['data']:
                for ch, vals in d.items():
                    for i, v in enumerate(vals):
                        # 樣點 i 的真實時刻 = 報的 startTime + i × interval
                        ti = t + timedelta(milliseconds=(i * iv) if iv else 0)
                        rows.append((ti.isoformat(), ch, v, iv, sid,
                                     (ti + timedelta(seconds=offset_s)).isoformat(),
                                     offset_s))
            meta[sid] = (iv, st)
    return rows, meta


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', default=DEFAULT_DB)
    ap.add_argument('--jsonl', default=DEFAULT_JSONL)
    ap.add_argument('--offset', type=float, default=DEFAULT_OFFSET_S)
    ap.add_argument('--dry', action='store_true')
    args = ap.parse_args()

    if not os.path.exists(args.jsonl):
        print(f'找不到 {args.jsonl}')
        return 1

    rows, meta = load_rows(args.jsonl, args.offset)
    print(f'解析出 {len(rows)} 個高解析資料點')
    for sid, (iv, st) in sorted(meta.items(), key=lambda x: str(x[0])):
        print(f'  streamId={sid} intervalMs={iv} 起始(機器時間)={st}')
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
    except Exception as e:
        con.rollback()
        print(f'寫入失敗（已回滾）：{e}')
        return 1
    finally:
        con.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
