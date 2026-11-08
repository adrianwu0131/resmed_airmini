#!/usr/bin/env python3
"""breath_period.py — 從串流 jsonl 量呼吸週期與事件前後的過度換氣

用途（實證 2026-11-07）：AirMini 即時串流的 MaskPressure 通道在清醒/治療中
會呈明顯週期性鋸齒＝逐次呼吸波形。用自相關（autocorrelation）估呼吸週期，
再對照呼吸事件標記，量化「中樞事件前 1–4 分的過度換氣」比例。

與 `night_curve_check.py` 的分工：那一支處理 SD 卡（AirSense）逐夜曲線與事件對位；
本支處理 AirMini 藍牙即時串流（jsonl），解析度高一到兩個數量級。

用法：
  python3 breath_period.py                          # 全部串流資料
  python3 breath_period.py --minutes 30             # 只看最後 30 分鐘
  python3 breath_period.py --channel MaskPressure-100hz
"""
import argparse
import json
import os
from collections import Counter
from datetime import datetime, timedelta

DEF_JSONL = 'pull/stream_data.jsonl'


def parse_ts(s):
    return datetime.fromisoformat(s.replace('Z', ''))


def load(path, minutes=None):
    """回傳 [(ts, channel, value, interval_ms)]，樣點時間展開為 startTime+i×interval。"""
    out = []
    if not os.path.exists(path):
        print(f'找不到 {path}')
        return out
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
        iv = p.get('intervalMs') or 1000
        t = parse_ts(st)
        for d in p['data']:
            for ch, vals in d.items():
                for i, v in enumerate(vals):
                    out.append((t + timedelta(milliseconds=i * iv), ch, v, iv))
    if minutes and out:
        cut = out[-1][0] - timedelta(minutes=minutes)
        out = [x for x in out if x[0] >= cut]
    return out


def autocorr_period(vals, max_lag):
    """自相關估週期（回傳 (lag, r)；lag 單位＝樣點數）。"""
    if len(vals) < 3 * max_lag:
        return None
    mu = sum(vals) / len(vals)
    var = sum((x - mu) ** 2 for x in vals) / len(vals)
    if var == 0:
        return None
    best = None
    for lag in range(2, max_lag + 1):
        a, b = vals[:-lag], vals[lag:]
        ma = sum(a) / len(a)
        mb = sum(b) / len(b)
        va = sum((x - ma) ** 2 for x in a) / len(a)
        vb = sum((x - mb) ** 2 for x in b) / len(b)
        if va == 0 or vb == 0:
            continue
        r = sum((a[i] - ma) * (b[i] - mb) for i in range(len(a))) / len(a) / ((va * vb) ** 0.5)
        if best is None or r > best[1]:
            best = (lag, r)
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--file', default=DEF_JSONL)
    ap.add_argument('--minutes', type=float, default=None)
    ap.add_argument('--channel', default='MaskPressure-100hz')
    ap.add_argument('--max-lag-s', type=float, default=20.0)
    args = ap.parse_args()

    rows = load(args.file, args.minutes)
    if not rows:
        return 1

    sel = [(t, v, iv) for (t, ch, v, iv) in rows if ch == args.channel]
    if not sel:
        print(f'找不到通道 {args.channel}；可用：{sorted({c for _, c, _, _ in rows})}')
        return 1

    iv = sel[0][2]
    max_lag = max(2, int(args.max_lag_s * 1000 / iv))

    # 以「非零」片段為治療進行中的區段（待機只有平線 0）
    nz = [v for _, v, _ in sel if v != 0]
    print(f'通道 {args.channel}：{len(sel)} 點，interval={iv}ms，'
          f'非零 {len(nz)} ({100 * len(nz) / len(sel):.1f}%)')
    if nz:
        print(f'  非零值 min={min(nz):.2f} max={max(nz):.2f} avg={sum(nz) / len(nz):.3f}')

    # 找最長的連續非零區段做週期估計
    best_seg, cur = [], []
    for t, v, _ in sel:
        if v != 0:
            cur.append(v)
            if len(cur) > len(best_seg):
                best_seg = cur
        else:
            cur = []
    if len(best_seg) < 50:
        print('  非零區段太短，無法估呼吸週期（機器可能未在治療）')
        return 0

    ac = autocorr_period(best_seg, max_lag)
    if ac:
        lag, r = ac
        print(f'  呼吸週期 ≈ {lag * iv / 1000:.2f} 秒（自相關 r={r:.3f}，'
              f'最長非零段 {len(best_seg)} 點）')

    # 依時間分桶做局部週期估計，看週期是否隨時間變化（過度換氣會縮短）
    seg_len = max(200, int(60 * 1000 / iv))
    buckets = []
    for i in range(0, len(sel) - seg_len, seg_len // 2):
        chunk = [v for _, v, _ in sel[i:i + seg_len]]
        if sum(1 for v in chunk if v != 0) < seg_len * 0.3:
            continue
        ac = autocorr_period(chunk, max_lag)
        if ac:
            buckets.append((sel[i][0], ac[0] * iv / 1000, ac[1]))
    if buckets:
        print(f'  局部週期（每 {seg_len * iv / 1000:.0f}s 窗）：')
        for t, per, r in buckets:
            print(f'    {t:%H:%M:%S}  {per:5.2f}s  r={r:.3f}')
        pers = [p for _, p, _ in buckets]
        print(f'  週期範圍 {min(pers):.2f}–{max(pers):.2f}s（中位 '
              f'{sorted(pers)[len(pers) // 2]:.2f}s）')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
