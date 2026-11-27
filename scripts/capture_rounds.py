#!/usr/bin/env python3
"""capture_rounds.py — 多輪穩定性測試驅動器（2026-11-27）

把 ready_capture 的「武裝 → 按一下鈕 → 3 秒咬上 → 全史 + 串流 + 保活」流程
連續跑 N 輪，每輪記錄統計到 pull/capture_rounds.jsonl，用來驗證
「每次抓都順利」。

輪與輪之間需要**使用者按一下 Bluetooth 鈕**（視窗一次性：上一輪 connect
收線後視窗就關了；驅動器每輪一開始就宣佈「武裝完成，請按一下鈕」）。

實測（2026-11-27，6 輪各 3 分鐘）：6/6 咬上、每輪一按；串流在「上一條
串流剛結束後 <180s 內的新連線」會收不到資料（殘留 TTL），--cooldown
120s + ready_capture 的 StartStream 重送（25s 無資料→重送，最多 3 次）
就是為它加的。

用法（在 AirMini 工作目錄下）：
    env AIRMINI_LIB=... AIRMINI_ADDR=<BT_ADDR> ... \
        python3 capture_rounds.py --rounds 6 --minutes 3 --cooldown 120
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ready_capture import S, RESET, log, run_once  # noqa: E402

WAITS = ([3.0] * 25 + [15.0] * 20 + [60.0, 90.0] * 20)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--rounds', type=int, default=6)
    ap.add_argument('--minutes', type=float, default=3.0)
    ap.add_argument('--cooldown', type=float, default=120.0,
                    help='輪與輪之間的冷卻秒數（避開裝置串流殘留 TTL ~180s）')
    args = ap.parse_args()
    hours = args.minutes / 60.0

    out = os.path.join(os.environ.get('AIRMINI_OUTDIR', 'pull'),
                       'capture_rounds.jsonl')
    os.makedirs(os.path.dirname(out), exist_ok=True)
    results = []
    for r in range(1, args.rounds + 1):
        log(f'===== 第 {r}/{args.rounds} 輪：武裝完成，**請按一下 Bluetooth 鈕** =====')
        t_arm = time.time()
        caught = False
        for w in WAITS:
            S.update(RESET)
            good, eno = run_once(hours)
            if good:
                lat = time.time() - t_arm
                rec = {'round': r, 'caught': True,
                       'arm_to_finish_s': round(lat, 1),
                       'history_msgs': S['lg'], 'stream_msgs': S['st'],
                       'hb': S['hb'], 'ka': S['ka'],
                       'stream_minutes': args.minutes}
                results.append(rec)
                with open(out, 'a') as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + '\n')
                log(f'第 {r} 輪 ✓（全史 {S["lg"]}、串流 {S["st"]}）')
                caught = True
                break
            if eno == 104:
                log('104＝視窗開著！立刻連續重試')
                for _ in range(12):
                    S.update(RESET)
                    time.sleep(2)
                    good, _ = run_once(hours)
                    if good:
                        lat = time.time() - t_arm
                        rec = {'round': r, 'caught': True,
                               'arm_to_finish_s': round(lat, 1), 'via_104': True,
                               'history_msgs': S['lg'], 'stream_msgs': S['st'],
                               'hb': S['hb'], 'ka': S['ka'],
                               'stream_minutes': args.minutes}
                        results.append(rec)
                        with open(out, 'a') as f:
                            f.write(json.dumps(rec, ensure_ascii=False) + '\n')
                        log(f'第 {r} 輪 ✓（104 立搶）')
                        caught = True
                        break
                if caught:
                    break
            log(f'→ 等 {w:.0f}s')
            time.sleep(w)
        if not caught:
            rec = {'round': r, 'caught': False}
            with open(out, 'a') as f:
                f.write(json.dumps(rec, ensure_ascii=False) + '\n')
            results.append(rec)
            log(f'第 {r} 輪 ✗（輪詢用盡未咬上）')
        if r < args.rounds and args.cooldown > 0:
            log(f'冷卻 {args.cooldown:.0f}s（讓裝置端串流殘留過期）')
            time.sleep(args.cooldown)
    n_ok = sum(1 for x in results if x['caught'])
    log(f'=== 總計：{n_ok}/{args.rounds} 輪成功 ===')
    return 0 if n_ok == args.rounds else 1


if __name__ == '__main__':
    sys.exit(main())
