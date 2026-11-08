#!/usr/bin/env python3
"""逐夜換氣/壓力/漏氣曲線 + 呼吸事件對位檢查（資料源：~/airmini/cpap_data.db）。

用法（需 matplotlib 的 python，例：miniconda3/envs/ml/bin/python）:
  night_curve_check.py --night 2026-11-01                # AirSense 資料日，畫圖+列印統計
  night_curve_check.py --mini 2026-11-04                 # AirMini 夜（校正後時間窗，畫壓力/漏氣）
  night_curve_check.py --night 2026-10-24 --stats-only   # 只印中樞事件先行檢驗
  night_curve_check.py --night 2026-11-01 --out /tmp/x.png

輸出：
  1) 圖：MinVent（L/min）與 MaskPress（cmH2O）時間曲線，事件以垂直色線標記
     （AirMini 夜：分鐘級壓力/漏氣 + 事件）。
  2) 文字：「中樞事件前 1-4 分鐘最高換氣 > 全夜中位」的比例（過度換氣先行檢驗）。

陷阱內建：s10_events.session 與 s10_pld_2s.session 的檔名時間戳會差數秒
（同一物理 session），以 ±90 秒容差對位；事件時刻 = 事件自身 session 起點 + onset_s。
"""
import os
import argparse
import sqlite3
from datetime import datetime as dt, timedelta

DB = os.environ.get('CPAP_DB', '/path/to/cpap_data.db')
FONT = os.environ.get('CPAP_FONT', '/path/to/NotoSansTC.ttf')
KIND = {'Hypopnea': ('#f2a93b', '低通氣'), 'Central Apnea': ('#c3407e', '中樞'),
        'Obstructive Apnea': ('#4a78c2', '阻塞'), 'Apnea': ('#8a9098', '不明')}
MKIND = {'CentralApneaEnd': ('#c3407e', '中樞'), 'HypopneaEnd': ('#f2a93b', '低通氣'),
         'ObstructiveApneaEnd': ('#4a78c2', '阻塞'), 'ApneaEnd': ('#8a9098', '不明')}


def cjk_font():
    try:
        import matplotlib
        from matplotlib import font_manager
        font_manager.fontManager.addfont(FONT)
        matplotlib.rcParams['font.family'] = 'Noto Sans TC'
        matplotlib.rcParams['axes.unicode_minus'] = False
    except Exception:
        pass


def xfmt(ax, lo, hi):
    import matplotlib.dates as mdates
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
    ax.tick_params(labelsize=7)
    for s in ('top', 'right'):
        ax.spines[s].set_visible(False)
    ax.set_xlim(lo, hi)


def as_sessions(cur, d):
    return [r[0] for r in cur.execute(
        'SELECT session FROM s10_pld_2s WHERE date=? GROUP BY session ORDER BY session', (d,)).fetchall()]


def events_for_session(cur, d, psess, tol=90):
    t0 = dt.strptime(psess, '%Y%m%d_%H%M%S')
    out = []
    for esess, on, k in cur.execute(
            'SELECT session, onset_s, kind FROM s10_events WHERE date=? ORDER BY onset_s', (d,)).fetchall():
        try:
            e0 = dt.strptime(esess, '%Y%m%d_%H%M%S')
        except (ValueError, TypeError):
            continue
        if abs(e0 - t0) <= timedelta(seconds=tol):
            out.append((on, k, e0))
    return out


def overvent_stats(cur, d, psess):
    rows = cur.execute("""SELECT t_rel_s, min_vent FROM s10_pld_2s
                          WHERE date=? AND session=? ORDER BY t_rel_s""", (d, psess)).fetchall()
    mv = sorted(r[1] or 0 for r in rows)
    if not mv:
        return None
    p50 = mv[len(mv) // 2]
    pres = [(on, k) for on, k, _ in events_for_session(cur, d, psess) if k == 'Central Apnea']
    if not pres:
        print(f'{d} {psess}: 中樞 0（對照段）')
        return 0
    pmax = []
    for on, k in pres:
        w = [r[1] for r in rows if 60 <= on - r[0] <= 240 and r[1] is not None]
        if w:
            pmax.append(max(w))
    frac = sum(1 for x in pmax if x > p50) / len(pmax) * 100 if pmax else 0
    print(f'{d} {psess}: 中樞 {len(pres)}；事件前1-4分 Max換氣 > 全夜p50({p50:.1f}) 比例 {frac:.0f}%')
    return frac


def plot_as(cur, d, psess, ax_mv, ax_p):
    rows = cur.execute("""SELECT t_rel_s, min_vent, mask_press FROM s10_pld_2s
                          WHERE date=? AND session=? ORDER BY t_rel_s""", (d, psess)).fetchall()
    t0 = dt.strptime(psess, '%Y%m%d_%H%M%S')
    ts = [t0 + timedelta(seconds=r[0]) for r in rows]
    mv = [r[1] or 0 for r in rows]
    mp = [r[2] or 0 for r in rows]
    done = set()
    for on, k, e0 in events_for_session(cur, d, psess):
        c, lab = KIND.get(k, ('#999', k))
        ax_mv.axvline(e0 + timedelta(seconds=on), color=c, lw=1.1,
                      label=lab if k not in done else None, alpha=0.85)
        done.add(k)
    ax_mv.plot(ts, mv, color='#159a86', lw=0.8)
    ax_mv.set_ylabel('MinVent L/min', fontsize=7.5)
    if done:
        ax_mv.legend(fontsize=6, loc='upper right', ncol=2, framealpha=0.4)
    ax_p.plot(ts, mp, color='#4a78c2', lw=0.8)
    ax_p.set_ylabel('MaskPress', fontsize=7.5)
    ax_p.set_ylim(0, max(mp) + 3)
    for ax in (ax_mv, ax_p):
        xfmt(ax, ts[0], ts[-1])


def plot_mini(cur, night, out):
    import matplotlib.pyplot as plt
    win = cur.execute("SELECT MIN(start), MAX(end) FROM am_sessions WHERE night=?",
                      (night,)).fetchone()
    if not win[0]:
        print(f'{night}: 無 AirMini session')
        return
    lo, hi = win[0][:16] + ':00', win[1][:16] + ':59'
    rows = cur.execute("""SELECT p.ts, p.pressure_cmh2o, l.leak_lmin
                          FROM am_pressure_1min p JOIN am_leak_1min l ON l.ts=p.ts
                          WHERE p.ts BETWEEN ? AND ? ORDER BY p.ts""", (lo, hi)).fetchall()
    if not rows:
        print(f'{night}: 無分鐘級資料（窗 {lo}..{hi}）')
        return
    ts = [dt.fromisoformat(r[0]) for r in rows]
    pr = [r[1] for r in rows]
    lk = [r[2] for r in rows]
    fig, ax = plt.subplots(figsize=(9, 3.2), dpi=140)
    done = set()
    for t, k in cur.execute("""SELECT ts, kind FROM am_respiratory_events
                               WHERE ts BETWEEN ? AND ? ORDER BY ts""", (lo, hi)).fetchall():
        c, lab = MKIND.get(k, ('#999', k))
        ax.axvline(dt.fromisoformat(t), color=c, lw=0.9,
                   label=lab if k not in done else None, alpha=0.8)
        done.add(k)
    ax.plot(ts, pr, color='#4a78c2', lw=0.9, label='壓力')
    ax.plot(ts, lk, color='#159a86', lw=0.9, label='漏氣')
    ax.legend(fontsize=6.5, loc='upper left')
    ax.set_title(f'AirMini 夜 {night}（校正後時間窗）壓力/漏氣 + 事件', fontsize=9)
    xfmt(ax, ts[0], ts[-1])
    out = out or f'/tmp/night_curve_mini_{night.replace("-", "")}.png'
    fig.savefig(out)
    print('圖：', out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--night', help='AirSense 資料日 YYYY-MM-DD（DATALOG 目錄日期）')
    ap.add_argument('--mini', help='AirMini 夜 YYYY-MM-DD（畫該夜校正後時間窗）')
    ap.add_argument('--out', default=None, help='輸出 PNG 路徑')
    ap.add_argument('--stats-only', action='store_true', help='只列印統計，不畫圖')
    a = ap.parse_args()
    cjk_font()
    con = sqlite3.connect(DB)
    cur = con.cursor()
    if not (a.night or a.mini):
        a.night = cur.execute('SELECT MAX(date) FROM s10_daily').fetchone()[0]
        print(f'未指定，用最後一天 {a.night}')
    if a.night:
        if a.out is None and a.stats_only:
            for s in as_sessions(cur, a.night):
                overvent_stats(cur, a.night, s)
            return
        slist = as_sessions(cur, a.night)
        if not slist:
            print(f'{a.night}: 無 PLD 資料')
            return
    if a.mini:
        plot_mini(cur, a.mini, a.out)
        return
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(2 * len(slist), 1, figsize=(8, 2.6 * len(slist)),
                             dpi=140, squeeze=False,
                             gridspec_kw={'height_ratios': [2, 1] * len(slist)})
    plt.subplots_adjust(hspace=0.5, left=0.08, right=0.98, top=0.93, bottom=0.07)
    for i, s in enumerate(slist):
        plot_as(cur, a.night, s, axes[2*i][0], axes[2*i+1][0])
        axes[2*i][0].set_title(f'{a.night} session {s}', fontsize=8.5)
    out = a.out or f'/tmp/night_curve_{a.night.replace("-", "")}.png'
    fig.savefig(out)
    print('圖：', out)
    for s in slist:
        overvent_stats(cur, a.night, s)


if __name__ == '__main__':
    main()
