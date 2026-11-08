#!/usr/bin/env python3
"""plot_stream.py — 把 AirMini 即時串流畫成波形圖（需 matplotlib）

用法：
    python3 plot_stream.py --out pull/stream.png
    python3 plot_stream.py --minutes 60 --out pull/last_hour.png

畫圖要點（對應 references/airmini-access.md「收後流程」）：
  * **跨時段要插 NaN 斷線**：不同 streamId 或時間跳 >5s 的兩段若直接畫，會出現
    一條假的斜坡長條把兩段連起來——在斷點插 NaN。
  * **中文字型**：系統 .ttc 可用於 matplotlib（reportlab 不吃 .ttc）：
    font_manager.addfont('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc')
"""
import argparse
import json
import os
from datetime import datetime

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager

for _fp in ('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
            '/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc',
            '/usr/share/fonts/truetype/arphic/uming.ttc'):
    if os.path.exists(_fp):
        font_manager.fontManager.addfont(_fp)
        matplotlib.rcParams['font.family'] = \
            font_manager.FontProperties(fname=_fp).get_name()
        break
matplotlib.rcParams['axes.unicode_minus'] = False

DEFAULT_JSONL = os.path.expanduser('~/airmini/s10/pull/stream_data.jsonl')
CHANNELS = ('MaskPressure-100hz', 'InspiratoryPressure-50hz', 'Leak-TwoSecond')
COLORS = {'MaskPressure-100hz': '#e4572e',
          'InspiratoryPressure-50hz': '#2e86ab',
          'Leak-TwoSecond': '#54a24b'}
LABELS = {'MaskPressure-100hz': 'MaskPressure-100hz (面罩壓力)',
          'InspiratoryPressure-50hz': 'InspiratoryPressure-50hz (吸氣壓力)',
          'Leak-TwoSecond': 'Leak-TwoSecond (漏氣)'}


def parse_ts(s):
    return datetime.fromisoformat(s.replace('Z', '+00:00'))


def load(path, since=None, minutes=None):
    recs = []
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
            ts = parse_ts(st)
            if since and ts < since:
                continue
            recs.append((ts, p))
    if minutes and recs:
        cut = recs[-1][0].timestamp() - minutes * 60
        recs = [x for x in recs if x[0].timestamp() >= cut]
    return recs


def build_series(recs):
    series = {c: [] for c in CHANNELS}
    t0 = recs[0][0].timestamp()
    prev_stream, prev_base = None, None
    for ts, p in recs:
        base = ts.timestamp()
        iv = (p.get('intervalMs') or 40) / 1000.0
        sid = p.get('streamId')
        # 換 stream 或時間跳躍 > 5 秒 → 插 NaN 斷線
        if prev_stream is not None and (sid != prev_stream or base - prev_base > 5):
            for v in series.values():
                v.append((base, float('nan')))
        prev_stream, prev_base = sid, base
        for d in p['data']:
            for ch, vals in d.items():
                if ch in series:
                    for i, v in enumerate(vals):
                        series[ch].append((base + i * iv, v))
    return series, t0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--file', default=DEFAULT_JSONL)
    ap.add_argument('--minutes', type=float, default=None)
    ap.add_argument('--since', default=None)
    ap.add_argument('--out', default='pull/stream.png')
    args = ap.parse_args()

    if not os.path.exists(args.file):
        print(f'找不到 {args.file}')
        return 1
    since = parse_ts(args.since) if args.since else None
    recs = load(args.file, since, args.minutes)
    if not recs:
        print('沒有資料')
        return 1

    series, t0 = build_series(recs)
    fig, axes = plt.subplots(3, 1, figsize=(16, 9), sharex=True,
                             gridspec_kw={'height_ratios': [3, 3, 2]})
    for ax, ch in zip(axes, CHANNELS):
        d = series[ch]
        ys = [y for _, y in d if y == y]        # 排除 NaN
        if not ys:
            ax.set_title(f'{LABELS[ch]} — 無資料')
            continue
        ax.plot([x - t0 for x, _ in d], [y for _, y in d],
                lw=0.9, color=COLORS[ch])
        ax.set_ylabel(COLORS[ch])
        ax.grid(alpha=0.3)
        ax.set_title(f'{LABELS[ch]}  n={len(ys)}  min={min(ys):.2f} '
                     f'max={max(ys):.2f} avg={sum(ys)/len(ys):.2f}', fontsize=10)
    axes[-1].set_xlabel(f'秒（起點 = {recs[0][0].isoformat()} 機器時間）')
    fig.suptitle(f'AirMini 即時串流 {recs[0][0].isoformat()} → '
                 f'{recs[-1][0].isoformat()}（機器時鐘）')
    fig.tight_layout()
    os.makedirs(os.path.dirname(args.out) or '.', exist_ok=True)
    fig.savefig(args.out, dpi=110)
    print(f'已輸出 {args.out}')

    for ch in CHANNELS:
        ys = [y for _, y in series[ch] if y == y]
        if not ys:
            continue
        nz = sum(1 for y in ys if y != 0)
        print(f'{ch}: n={len(ys)} 非零={nz} ({100*nz/len(ys):.1f}%) '
              f'min={min(ys):.2f} max={max(ys):.2f} avg={sum(ys)/len(ys):.3f}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
