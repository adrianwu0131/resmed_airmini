"""AirView 原版版式圖表產生器（conda ml env 執行）
產出：
  f1_usage.png   檔案1 Usage 面板（含面板框、標題、機器時間軸長條）
  f2_mini.png    檔案2 頁1 Usage-hours 小圖
  p2_usage/leak/press/ahi.png  檔案2 頁2 四面板（僅圖，標題與左欄由 PDF 畫）
  det_YYYYMMDD_{A|M}.png      檔案3 每夜三面板（Leak/Pressure/Events，機器時間軸）
"""
import os
import sys, json, collections
from pathlib import Path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from resmed_s10 import EDF, decode_annotations, parse_str
from datetime import timedelta, datetime as dt

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, Rectangle, Patch
from matplotlib.lines import Line2D
from matplotlib import font_manager

font_manager.fontManager.addfont(os.environ.get('AIRMINI_FONT', '/usr/share/fonts/truetype/noto/NotoSansTC.ttf'))
plt.rcParams.update({'font.family': 'Noto Sans TC', 'font.size': 7})

OUT = Path(os.environ.get('AIRMINI_CHART_DIR', 'charts'))
OUT.mkdir(parents=True, exist_ok=True)

BLUE = '#4a78c2'
GREEN = '#76be60'
RED = '#d23b2e'
BLUE_AM = '#3b82f6'
GRAY_L = '#d9d9d9'
TXT = '#3b4048'

# ---------------- 資料 ----------------
rows = {r['date']: r for r in parse_str(os.environ.get('AIRMINI_STR', 'STR.edf'))}
objs = [json.loads(l) for l in open(os.environ.get('AIRMINI_JSONL', 'pull/logged_data.jsonl'), encoding='utf-8') if l.strip()]
runs = collections.defaultdict(list)
for j in objs:
    for d in j.get('params', {}).get('data', []):
        runs[d['dataId']].append(d)

_OFF_OLD = timedelta(hours=7, minutes=46, seconds=35)   # 11/6 量測（機器快 13m25s）
_OFF_NEW = timedelta(hours=8, minutes=13, seconds=33)   # 11/27 量測（機器落後 13.5 分）
_CUT = dt(2026, 11, 4)

def _clock_off(machine_t):
    """機器時鐘漂移非常數：11/04 前後用不同偏移（舊：快 13m25s；新：落後 13.5 分）"""
    return _OFF_OLD if machine_t < _CUT else _OFF_NEW

def T(s):
    t_ = dt.fromisoformat(s.replace('Z', '+00:00')) if s else None
    if t_ is None:
        return None
    t_ = t_.replace(tzinfo=None)
    return t_ + _clock_off(t_)

def data_day(t_):
    return t_.date() if t_.hour >= 12 else t_.date() - timedelta(days=1)

# AirMini sessions（機器本地時間）
mask_ev = []
_mk_seen = set()
for d in runs['UsageEvents-TherapyStatusEvent']:
    for e in d.get('events', []):
        _mk = (e['time'], e['event'])
        if _mk in _mk_seen:
            continue
        _mk_seen.add(_mk)
        mask_ev.append((T(e['time']), e['event']))
mask_ev.sort()
am_sessions, open_on = [], None
for tt, e in mask_ev:
    if e == 'MaskOn' and open_on is None:
        open_on = tt
    elif e in ('MaskOff', 'PowerOff') and open_on is not None:
        if tt > open_on:
            am_sessions.append((open_on, tt))
        open_on = None

leak_mins, press_mins = [], []
_lm_seen, _pm_seen = set(), set()
for d in runs['TherapyOneMinutePeriodic-Leak']:
    t0, iv = T(d['periodic']['startTime']), d['periodic'].get('interval', 60)
    for i, v in enumerate(d['periodic']['values']):
        if v is not None:
            _tk = str(t0 + timedelta(seconds=iv * i))
            if _tk in _lm_seen:
                continue
            _lm_seen.add(_tk)
            leak_mins.append((t0 + timedelta(seconds=iv * i), v * 60))
for d in runs['TherapyOneMinutePeriodic-InspiratoryPressure']:
    t0, iv = T(d['periodic']['startTime']), d['periodic'].get('interval', 60)
    for i, v in enumerate(d['periodic']['values']):
        if v is not None:
            _tk = str(t0 + timedelta(seconds=iv * i))
            if _tk in _pm_seen:
                continue
            _pm_seen.add(_tk)
            press_mins.append((t0 + timedelta(seconds=iv * i), v))

am_events = []
_ev_seen = set()
for d in runs['TherapyEvents-RespiratoryEvent']:
    for e in d.get('events', []):
        _ek = (e['time'], e['event'])
        if _ek in _ev_seen:
            continue
        _ev_seen.add(_ek)
        am_events.append((T('Z'.join([e['time'].replace('+00:00', '')]) if '+00:00' in e['time'] else e['time']) if T(e['time']) else None,
                          e['event'], float(e.get('durationSeconds', 0) or 0)))
am_events = [(t, x, du) for t, x, du in am_events if t]

# AirMini night rows（統計值）
import csv
am_rows = {}
with open(os.environ.get('AIRMINI_NIGHTS_CSV', 'airmini_nights.csv'), encoding='utf-8-sig') as fp:
    for r_ in csv.DictReader(fp):
        am_rows[r_['date']] = r_

# ---------------- 機器時間軸 helper ----------------
def hsn_min(m):
    """正午起算分鐘 → 小時(0..24)；m 已是自正午的值"""
    return (int(m) % 1440) / 60

def hsn_dt(t_):
    return ((t_.hour * 60 + t_.minute - 720) % 1440) / 60

def clk_label(h):
    m = int(round(h * 60)) % 1440
    mm = (m + 720) % 1440
    return f'{mm // 60:02d}:{mm % 60:02d}'

def hourly_ticks(x0, x1):
    t0 = int(x0) + 1 if x0 != int(x0) else int(x0)
    ts = list(range(t0, int(x1) + 1))
    return ts

EV_ROWS = [('Hypopnoea', 'Hypopnea'), ('Central', 'CentralApneaEnd'),
           ('Obstructive', 'ObstructiveApneaEnd'), ('Unknown', 'ApneaEnd')]

def ev_row(text):
    for i, (disp, key) in enumerate(EV_ROWS):
        if text == key:
            return i
    if 'entral Apnea' in text:
        return 1
    if 'bstructive Apnea' in text:
        return 2
    if 'ypopnea' in text:
        return 0
    if text == 'RERA':
        return 4
    return 3

# ---------------- 檔案 1 面板 ----------------
def draw_f1():
    """檔案1 Usage 面板：與原版同規格的 90 天視窗（08/03-11/01），AirMini 疊加補充"""
    w_end = dt(2026, 11, 22).date()
    w_start = w_end - timedelta(days=89)
    dates = [w_start + timedelta(days=i) for i in range(90)]
    as_sessions, am_by_day = {}, {}
    for d in dates:
        r = rows.get(d)
        if r:
            ons = [x for x in r['mask_on'] if x not in (-1, None)]
            offs = [x for x in r['mask_off'] if x not in (-1, None)]
            ss = [(o, f) for o, f in zip(ons, offs) if f > o]
            if ss:
                as_sessions[d] = ss
    for s, e in am_sessions:
        am_by_day.setdefault(data_day(s), []).append((s, e))

    fig = plt.figure(figsize=(9.194, 2.486), dpi=150)
    ax = fig.add_axes([0.055, 0.16, 0.930, 0.70])
    ax.axhspan(12, 20, color='#eaf1fb', zorder=0)
    for i, d in enumerate(dates):
        a_on = d in as_sessions
        m_on = d in am_by_day
        if not a_on:
            pass                      # 2026-11-27：未使用日不再畫紅色空心框
        elif m_on:
            for o, f in as_sessions[d]:
                h0, h1 = hsn_min(o), hsn_min(f)
                if h1 < h0:
                    ax.add_patch(Rectangle((i - 0.44, h0), 0.40, 24 - h0,
                                           color=GREEN if (24 - h0 + h1) >= 4 else RED, lw=0))
                    ax.add_patch(Rectangle((i - 0.44, 0), 0.40, h1,
                                           color=GREEN if (24 - h0 + h1) >= 4 else RED, lw=0))
                else:
                    ax.add_patch(Rectangle((i - 0.44, h0), 0.40, h1 - h0,
                                           color=GREEN if (h1 - h0) >= 4 else RED, lw=0))
            for s, e in am_by_day[d]:
                h0, h1 = hsn_dt(s), hsn_dt(e)
                if h1 < h0:
                    if (h1 - h0) < -0.08:
                        ax.add_patch(Rectangle((i + 0.04, h0), 0.40, 24 - h0, color=BLUE_AM, lw=0))
                        ax.add_patch(Rectangle((i + 0.04, 0), 0.40, h1, color=BLUE_AM, lw=0))
                elif (h1 - h0) >= 0.08:
                    ax.add_patch(Rectangle((i + 0.04, h0), 0.40, h1 - h0, color=BLUE_AM, lw=0))
        else:
            gm = sum(f - o for o, f in as_sessions[d]) / 60 >= 4
            for o, f in as_sessions[d]:
                h0, h1 = hsn_min(o), hsn_min(f)
                if h1 < h0:
                    ax.add_patch(Rectangle((i - 0.19, h0), 0.38, 24 - h0,
                                           color=GREEN if gm else RED, lw=0))
                    ax.add_patch(Rectangle((i - 0.19, 0), 0.38, h1,
                                           color=GREEN if gm else RED, lw=0))
                else:
                    ax.add_patch(Rectangle((i - 0.19, h0), 0.38, h1 - h0,
                                           color=GREEN if gm else RED, lw=0))
        if not a_on and m_on:
            for s, e in am_by_day[d]:
                h0, h1 = hsn_dt(s), hsn_dt(e)
                if h1 < h0:
                    continue
                if (h1 - h0) >= 0.08:
                    ax.add_patch(Rectangle((i - 0.19, h0), 0.38, h1 - h0, color=BLUE_AM, lw=0))
    ax.set_xlim(-0.6, 89.6)
    ax.set_ylim(24, 0)
    for yy in (4, 8, 12, 16, 20):
        ax.axhline(yy, color=GRAY_L, lw=0.7, zorder=0)
    ax.set_yticks([0, 4, 8, 12, 16, 20, 24])
    ax.set_yticklabels(['12:00', '16:00', '20:00', '00:00', '04:00', '08:00', '12:00'], fontsize=6.8)
    sundays = [i for i, d in enumerate(dates) if d.weekday() == 6]
    ax.set_xticks(sundays)
    ax.set_xticklabels([f'Sun\n{dates[i].strftime("%d/%m")}' for i in sundays], fontsize=6.2)
    ax.set_xticks([i + 0.5 for i in range(90)], minor=True)
    ax.grid(which='minor', axis='x', color='#f2f2f2', lw=0.3)
    for s in ('top', 'right', 'left'):
        ax.spines[s].set_visible(False)
    ax.spines['bottom'].set_color('#7a88a8')
    ax.tick_params(length=2, width=0.5)
    fig.patches.append(FancyBboxPatch((0.006, 0.035), 0.988, 0.955,
                                      boxstyle='round,pad=0.004,rounding_size=0.012',
                                      transform=fig.transFigure, fill=False,
                                      edgecolor='#7a88a8', lw=0.9))
    fig.text(0.024, 0.925, 'Usage', color='#2e6da4', fontsize=10.5, weight='bold')
    fig.savefig(OUT / 'f1_usage.png')
    plt.close(fig)
    print('f1_usage OK（90 天視窗）')

# ---------------- 檔案 3 每夜面板 ----------------
BASE = Path(os.environ.get('AIRMINI_DATALOG', 'DATALOG'))

def airsense_day(ddir):
    plds = sorted(ddir.glob('*_PLD.edf'))
    if not plds:
        return None
    eve_f = sorted(ddir.glob('*_EVE.edf'))
    eve0, events = None, []
    if eve_f:
        e = EDF(eve_f[0])
        pref = dt.strptime(eve_f[0].stem.rsplit('_', 1)[0], '%Y%m%d_%H%M%S')
        eve0 = pref
        for rec, onset, dur, text in decode_annotations(e):
            if onset:
                events.append((float(onset) / 60, text, float(dur) if dur else 0))
    sessions = []
    for f in plds:
        start = dt.strptime(f.stem.rsplit('_', 1)[0], '%Y%m%d_%H%M%S')
        e = EDF(f)
        leak = [v for rec_ in e.data for v in rec_[e.idx['Leak.2s']]]
        press = [v for rec_ in e.data for v in rec_[e.idx['MaskPress.2s']]]
        sessions.append(dict(start=start, nmin=e.n_records, leak=[x * 60 for x in leak], press=press))
    return dict(sessions=sessions, eve0=eve0, events=events)

def am_day(d):
    ms = sorted(x for x in am_sessions if data_day(x[0]) == d)
    if not ms:
        return None
    leak_pts, press_pts, evs = [], [], []
    for s, e in ms:
        leak_pts += [(t, v) for t, v in leak_mins if s <= t <= e]
        press_pts += [(t, v) for t, v in press_mins if s <= t <= e]
        evs += [(t, x, du) for t, x, du in am_events if s <= t <= e]
    return dict(sessions=ms, leak=leak_pts, press=press_pts, events=evs)

def draw_detail(d, kind):
    """kind='A'（AirSense）或 'M'（AirMini）；畫 Leak/Pressure/Events 三面板"""
    if kind == 'A':
        info = airsense_day(BASE / d.strftime('%Y%m%d'))
        if not info:
            return False
        sess = info['sessions']
        # 事件（絕對機器鐘）
        events = []
        if info['eve0']:
            for onset, text, dur in info['events']:
                events.append((info['eve0'] + timedelta(minutes=onset), text, dur))
        leaks = []
        for s in sess:
            for i, v in enumerate(s['leak']):
                leaks.append((s['start'] + timedelta(seconds=2 * i), v))
        presss = []
        for s in sess:
            for i, v in enumerate(s['press']):
                presss.append((s['start'] + timedelta(seconds=2 * i), v))
    else:
        info = am_day(d)
        if not info:
            return False
        events = [(t, x, du) for t, x, du in info['events']]
        leaks = info['leak']
        presss = info['press']

    t0 = min(x[0] for x in leaks + presss)
    t1 = max(x[0] for x in leaks + presss)
    x0 = hsn_dt(t0) - 0.3
    x1 = hsn_dt(t1) + 0.3
    if x1 - x0 < 4:
        x1 = x0 + 4
    if x1 > 24:
        x1 = 24.2

    fig = plt.figure(figsize=(7.403, 6.958), dpi=150)  # 533x501pt
    LAX = [0.115, 0.795, 0.868, 0.115]
    PAX = [0.115, 0.585, 0.868, 0.115]
    EAX = [0.115, 0.135, 0.868, 0.260]

    def panel_xticks(ax):
        ts = hourly_ticks(x0, x1)
        ax.set_xticks(ts)
        ax.set_xticklabels([clk_label(x) for x in ts], fontsize=6.2, rotation=0)
        ax.set_xlim(x0, x1)
        for xh in ts:
            ax.axvline(xh, color='#f0f0f0', lw=0.5, zorder=0)
        for s_ in ('top', 'right'):
            ax.spines[s_].set_visible(False)
        ax.spines['left'].set_color('#cccccc')
        ax.spines['bottom'].set_color('#aaaaaa')
        ax.tick_params(length=2, width=0.5, pad=2)

    # Leak
    ax = fig.add_axes(LAX)
    lx = [hsn_dt(t) for t, _ in leaks]
    ly = [v for _, v in leaks]
    ax.plot(lx, ly, color=BLUE, lw=0.8)
    ax.axhline(24, color='#8a8f96', lw=0.9, ls=(0, (5, 3)))
    panel_xticks(ax)
    ax.set_xticklabels([])
    ax.set_ylim(0, 80)
    ax.set_yticks(range(0, 81, 20))
    ax.set_yticklabels([str(v) for v in range(0, 81, 20)], fontsize=7)
    ax.set_title('')
    # 標題與圖例
    fig.text(0.002, 0.938, 'Leak', color='#1b2b40', fontsize=10.5, weight='bold')
    fig.text(0.047, 0.938, '(L/min)', color='#8a94a2', fontsize=8.5)
    ln = Line2D([0.700, 0.770], [0.952, 0.952], transform=fig.transFigure, color=BLUE, lw=1.2)
    fig.add_artist(ln)
    fig.text(0.778, 0.943, 'Leak', color='#33383e', fontsize=6.8)
    ln2 = Line2D([0.830, 0.900], [0.952, 0.952], transform=fig.transFigure,
                 color='#8a8f96', lw=1.0, ls=(0, (4, 2)))
    fig.add_artist(ln2)
    fig.text(0.908, 0.943, 'Set threshold', color='#33383e', fontsize=6.8)

    # Pressure
    ax = fig.add_axes(PAX)
    px_ = [hsn_dt(t) for t, _ in presss]
    py_ = [v for _, v in presss]
    ax.plot(px_, py_, color=BLUE, lw=0.8)
    ax.axhline(20, color='#8a8f96', lw=0.9, ls=(0, (5, 3)))
    ax.axhline(4, color='#8a8f96', lw=0.9, ls=(0, (5, 3)))
    panel_xticks(ax)
    ax.set_xticklabels([])
    ax.set_ylim(0, 24)
    ax.set_yticks(range(4, 25, 4))
    ax.set_yticklabels([str(v) for v in range(4, 25, 4)], fontsize=7)
    fig.text(0.002, 0.728, 'Pressure', color='#1b2b40', fontsize=10.5, weight='bold')
    fig.text(0.075, 0.728, '(cmH2O)', color='#8a94a2', fontsize=8.5)
    ln = Line2D([0.580, 0.650], [0.742, 0.742], transform=fig.transFigure, color=BLUE, lw=1.2)
    fig.add_artist(ln)
    fig.text(0.658, 0.733, 'Pressure', color='#33383e', fontsize=6.8)
    ln2 = Line2D([0.736, 0.806], [0.742, 0.742], transform=fig.transFigure, color='#8a8f96', lw=1.0, ls=(0, (4, 2)))
    fig.add_artist(ln2)
    fig.text(0.814, 0.733, 'Set pressure min/max', color='#33383e', fontsize=6.8)

    # Events
    ax = fig.add_axes(EAX)
    has_rera = any(t_ == 'RERA' or t_ == 'ReraEnd' for _, t_, _ in events)
    rows_n = 4 + (1 if has_rera else 0)
    rows_lab = ['Hypopnoea', 'Central', 'Obstructive', 'Unknown'] + (['RERA'] if has_rera else [])
    for i in range(rows_n - 1):
        ax.axhline(i + 0.55, color='#e8e8e8', lw=0.5, zorder=0)
    for t_, text, dur_ in events:
        ri = ev_row(text)
        if ri >= rows_n:
            continue
        if dur_ and dur_ < 10:
            continue
        xh = hsn_dt(t_)
        hh = 0.16 + min((dur_ or 10) / 60 * 0.4, 0.42)
        ax.vlines(xh, ri + 0.10, ri + 0.10 + hh, color=BLUE, lw=1.0)
    ax.set_ylim(rows_n, 0)
    ax.set_yticks([i + 0.06 for i in range(rows_n)])
    ax.set_yticklabels(rows_lab, fontsize=7, ha='right')
    ax.yaxis.set_tick_params(pad=2, length=0)
    panel_xticks(ax)
    fig.text(0.002, 0.500, 'Events', color='#1b2b40', fontsize=10.5, weight='bold')
    vb = Line2D([0.880, 0.880], [0.503, 0.517], transform=fig.transFigure, color=BLUE, lw=1.1)
    fig.add_artist(vb)
    fig.text(0.892, 0.503, '>= 10 sec', color='#33383e', fontsize=6.8)

    fig.savefig(OUT / f'det_{d.strftime("%Y%m%d")}_{kind}.png')
    plt.close(fig)
    return True


# ---------------- 檔案 2 AirMini 面板 ----------------
def draw_am_f2():
    import csv as _csv
    amr = {}
    with open(os.environ.get('AIRMINI_NIGHTS_CSV', 'airmini_nights.csv'), encoding='utf-8-sig') as fp:
        for r2 in _csv.DictReader(fp):
            amr[r2['date']] = r2
    all_d = sorted(amr.keys())
    w0, w1 = (lambda a, b: (dt.strptime(a, '%Y-%m-%d').date(), dt.strptime(b, '%Y-%m-%d').date()))(all_d[0], all_d[-1])
    dates = [w0 + timedelta(days=i) for i in range((w1 - w0).days + 1)]
    def col(d, key, mul=1.0):
        r2 = amr.get(d.strftime('%Y-%m-%d'))
        try:
            return float(r2[key]) * mul if r2 and r2[key] not in ('', None) else None
        except (ValueError, KeyError):
            return None
    months = [i for i, d in enumerate(dates) if d.day == 1]
    months += [len(dates) - 1]
    mlab = []
    seen = set()
    for i in months:
        mlab.append(f'{dates[i].strftime("%b")}\n{dates[i].year}' if dates[i].strftime('%b%Y') not in seen else dates[i].strftime('%b'))
        seen.add(dates[i].strftime('%b%Y'))

    def frame(png, wpt, hpt, ymax, ystep, n_last=1, extra=None):
        fig = plt.figure(figsize=(wpt / 72, hpt / 72), dpi=150)
        ax = fig.add_axes([0.028, 0.20, 0.965, 0.68])
        ax.set_xlim(-1, len(dates))
        ax.set_ylim(0, ymax)
        for yy in range(ystep, ymax + 1, ystep):
            ax.axhline(yy, color='#ececec', lw=0.6, zorder=0)
        ax.set_yticks(range(0, ymax + 1, ystep))
        ax.set_yticklabels([str(x) for x in range(0, ymax + 1, ystep)], fontsize=6)
        ax.set_xticks(months)
        ax.set_xticklabels([dates[i].strftime('%b') if dates[i].strftime('%b%Y') in seen
                            else f'{dates[i].strftime("%b")}\n{dates[i].year}' for i in months], fontsize=6)
        for s_ in ('top', 'right'):
            ax.spines[s_].set_visible(False)
        for s_ in ('left', 'bottom'):
            ax.spines[s_].set_color('#bbbbbb')
        ax.tick_params(length=2, width=0.5, pad=1)
        return fig, ax

    # 頁3 mini
    fig, ax = frame('mini', 434, 68, 10, 2, 1)
    for i, d in enumerate(dates):
        m = col(d, '使用分')
        if m and m > 0:
            ax.bar(i, min(m / 60, 10), width=0.72, color=GREEN if m >= 240 else RED, lw=0)
    fig.savefig(OUT / 'p3_mini.png'); plt.close(fig)

    # 頁4 usage
    fig, ax = frame('u', 434, 94, 8, 2, 1)
    for i, d in enumerate(dates):
        m = col(d, '使用分')
        if m and m > 0:
            ax.bar(i, min(m / 60, 8.0), width=0.72, color=GREEN if m >= 240 else RED, lw=0)
    fig.savefig(OUT / 'p4_usage.png'); plt.close(fig)

    # 頁4 leak：95th 橘條 + median 綠tick + 24 虛線
    fig, ax = frame('l', 434, 108, 80, 20, 1)
    for i, d in enumerate(dates):
        l95 = col(d, '漏氣95')
        l50 = col(d, '漏氣med')
        if l95 and l95 > 0:
            ax.bar(i, min(l95, 78), width=0.72, color='#f2a93b', lw=0)
        if l50 and l50 > 0:
            ax.plot([i - 0.36, i + 0.36], [min(l50, 78)] * 2, color='#5ba746', lw=1.6)
    ax.axhline(24, color='#8a8f96', lw=0.8, ls=(0, (4, 3)), zorder=0)
    fig.savefig(OUT / 'p4_leak.png'); plt.close(fig)

    # 頁4 pressure：95th 藍條 + median 綠tick
    fig, ax = frame('p', 434, 103, 32, 4, 1)
    for i, d in enumerate(dates):
        p95 = col(d, '壓力95')
        p50 = col(d, '壓力med')
        if p95 and p95 > 0:
            ax.bar(i, min(p95, 32), width=0.72, color='#5b87d0', lw=0)
        if p50 and p50 > 0:
            ax.plot([i - 0.36, i + 0.36], [min(p50, 32)] * 2, color='#5ba746', lw=1.6)
    fig.savefig(OUT / 'p4_press.png'); plt.close(fig)

    # 頁4 AHI：HI 淺紫 + AI 深紫堆疊
    fig, ax = frame('a', 434, 94, 50, 10, 1)
    for i, d in enumerate(dates):
        hi_, ai_ = col(d, 'HI') or 0, col(d, 'AI') or 0
        if hi_ > 0:
            ax.bar(i, hi_, width=0.72, color='#b39bdb', lw=0)
        if ai_ > 0:
            ax.bar(i, ai_, bottom=hi_, width=0.72, color='#7d55c7', lw=0)
    fig.savefig(OUT / 'p4_ahi.png'); plt.close(fig)
    print('am panels OK')


# ---------------- 每夜合併面板（雙機同軸、連續時間）----------------
def draw_joint(d):
    """det2_YYYYMMDD.png：連續時間軸，AirSense+AirMini 曲線同圖"""
    ddir = BASE / d.strftime('%Y%m%d')
    asinfo = airsense_day(ddir) if ddir.exists() else None
    aminfo = am_day(d)
    if not asinfo and not aminfo:
        return False
    leaks, presss, events = [], [], []
    if asinfo:
        for s in asinfo['sessions']:
            for i, v in enumerate(s['leak']):
                leaks.append((s['start'] + timedelta(seconds=2 * i), v))
            for i, v in enumerate(s['press']):
                presss.append((s['start'] + timedelta(seconds=2 * i), v))
        if asinfo['eve0']:
            for onset, text, dur in asinfo['events']:
                events.append((asinfo['eve0'] + timedelta(minutes=onset), text, dur, 'A'))
    if aminfo:
        leaks += aminfo['leak']
        presss += aminfo['press']
        events += [(t, x, du, 'M') for t, x, du in aminfo['events']]
    t0 = min(x[0] for x in leaks + presss)
    t1 = max(x[0] for x in leaks + presss)
    w0 = t0 - timedelta(minutes=25)
    w1 = t1 + timedelta(minutes=25)
    fig = plt.figure(figsize=(7.403, 6.958), dpi=150)
    LAX = [0.115, 0.795, 0.868, 0.115]
    PAX = [0.115, 0.585, 0.868, 0.115]
    EAX = [0.115, 0.135, 0.868, 0.260]
    AS, AM = '#4a78c2', '#e0862a'

    def panel_xticks(ax, skip_labels=False):
        tk = []
        t = w0.replace(minute=0, second=0) + timedelta(hours=1)
        while t <= w1:
            tk.append(t)
            t += timedelta(hours=1)
        ax.set_xticks(tk)
        ax.set_xticklabels([] if skip_labels else [t.strftime('%H:%M') for t in tk], fontsize=6.2)
        ax.set_xlim(w0, w1)
        for t2 in tk:
            ax.axvline(t2, color='#f0f0f0', lw=0.5, zorder=0)
        for s_ in ('top', 'right'):
            ax.spines[s_].set_visible(False)
        ax.spines['left'].set_color('#cccccc')
        ax.spines['bottom'].set_color('#aaaaaa')
        ax.tick_params(length=2, width=0.5, pad=2)

    # Leak 面板
    ax = fig.add_axes(LAX)
    if asinfo:
        ser = []
        for s in asinfo['sessions']:
            ser += [(s['start'] + timedelta(seconds=2 * i), v) for i, v in enumerate(s['leak'])]
        if ser:
            ax.plot([p[0] for p in ser], [p[1] for p in ser], color=AS, lw=0.8)
    if aminfo and aminfo['leak']:
        ax.plot([p[0] for p in aminfo['leak']], [p[1] for p in aminfo['leak']], color=AM, lw=1.0)
    ax.axhline(24, color='#8a8f96', lw=0.9, ls=(0, (5, 3)))
    panel_xticks(ax, skip_labels=True)
    ax.set_ylim(0, 80)
    ax.set_yticks(range(0, 81, 20))
    ax.set_yticklabels([str(v) for v in range(0, 81, 20)], fontsize=7)
    fig.text(0.002, 0.938, 'Leak', color='#1b2b40', fontsize=10.5, weight='bold')
    fig.text(0.047, 0.938, '(L/min)', color='#8a94a2', fontsize=8.5)
    fig.add_artist(Line2D([0.640, 0.696], [0.952, 0.952], transform=fig.transFigure, color=AS, lw=1.2))
    fig.text(0.702, 0.943, 'AirSense', color='#33383e', fontsize=6.8)
    fig.add_artist(Line2D([0.768, 0.824], [0.952, 0.952], transform=fig.transFigure, color=AM, lw=1.2))
    fig.text(0.830, 0.943, 'AirMini', color='#33383e', fontsize=6.8)
    fig.add_artist(Line2D([0.892, 0.924], [0.952, 0.952], transform=fig.transFigure, color='#8a8f96', lw=1.0, ls=(0, (4, 2))))
    fig.text(0.930, 0.943, 'Set threshold', color='#33383e', fontsize=6.8)

    # Pressure 面板
    ax = fig.add_axes(PAX)
    if asinfo:
        ser = []
        for s in asinfo['sessions']:
            ser += [(s['start'] + timedelta(seconds=2 * i), v) for i, v in enumerate(s['press'])]
        if ser:
            ax.plot([p[0] for p in ser], [p[1] for p in ser], color=AS, lw=0.8)
    if aminfo and aminfo['press']:
        ax.plot([p[0] for p in aminfo['press']], [p[1] for p in aminfo['press']], color=AM, lw=1.0)
    ax.axhline(20, color='#8a8f96', lw=0.9, ls=(0, (5, 3)))
    ax.axhline(4, color='#8a8f96', lw=0.9, ls=(0, (5, 3)))
    panel_xticks(ax, skip_labels=True)
    ax.set_ylim(0, 24)
    ax.set_yticks(range(4, 25, 4))
    ax.set_yticklabels([str(v) for v in range(4, 25, 4)], fontsize=7)
    fig.text(0.002, 0.728, 'Pressure', color='#1b2b40', fontsize=10.5, weight='bold')
    fig.text(0.075, 0.728, '(cmH2O)', color='#8a94a2', fontsize=8.5)
    fig.add_artist(Line2D([0.560, 0.616], [0.742, 0.742], transform=fig.transFigure, color=AS, lw=1.2))
    fig.text(0.622, 0.733, 'AirSense', color='#33383e', fontsize=6.8)
    fig.add_artist(Line2D([0.700, 0.756], [0.742, 0.742], transform=fig.transFigure, color=AM, lw=1.2))
    fig.text(0.762, 0.733, 'AirMini', color='#33383e', fontsize=6.8)

    # Events 面板
    ax = fig.add_axes(EAX)
    has_rera = any(t_ in ('RERA', 'ReraEnd') for _, t_, _, _ in events)
    rows_n = 4 + (1 if has_rera else 0)
    rows_lab = ['Hypopnoea', 'Central', 'Obstructive', 'Unknown'] + (['RERA'] if has_rera else [])
    for i in range(rows_n - 1):
        ax.axhline(i + 0.55, color='#e8e8e8', lw=0.5, zorder=0)
    as_lo = asinfo['eve0'] - timedelta(minutes=2) if (asinfo and asinfo['eve0']) else None
    as_hi = (asinfo['eve0'] + timedelta(seconds=max((s['nmin'] for s in asinfo['sessions']), default=0) * 60 + 120)) if (asinfo and asinfo['eve0']) else None
    for t_, text, dur_, msrc in events:
        ri = ev_row(text)
        if ri >= rows_n or (dur_ and dur_ < 10):
            continue
        hh = 0.16 + min((dur_ or 10) / 60 * 0.4, 0.42)
        if as_lo is not None and as_lo <= t_ <= as_hi:
            col = AS
        else:
            col = AM
        ax.vlines(t_, ri + 0.10, ri + 0.10 + hh, color=col, lw=1.0)
    ax.set_ylim(rows_n, 0)
    ax.set_yticks([i + 0.06 for i in range(rows_n)])
    ax.set_yticklabels(rows_lab, fontsize=7, ha='right')
    ax.yaxis.set_tick_params(pad=2, length=0)
    panel_xticks(ax)
    fig.text(0.002, 0.500, 'Events', color='#1b2b40', fontsize=10.5, weight='bold')
    fig.add_artist(Line2D([0.700, 0.756], [0.517, 0.517], transform=fig.transFigure, color=AS, lw=1.4))
    fig.text(0.762, 0.505, 'AirSense', color='#33383e', fontsize=6.8)
    fig.add_artist(Line2D([0.830, 0.886], [0.517, 0.517], transform=fig.transFigure, color=AM, lw=1.4))
    fig.text(0.892, 0.505, 'AirMini', color='#33383e', fontsize=6.8)

    fig.savefig(OUT / f'det2_{d.strftime("%Y%m%d")}.png')
    plt.close(fig)
    return True




# ---------------- 睡眠夜分組（夜 = session 結束 +6h 的日期）----------------
NIGHTS = {}

def build_nights():
    """一次掃 DATALOG + AirMini，組成 NIGHTS[night_date]。
    AirSense: {'A': {'sessions': [(start, nmin, leak[], press[])], 'events': [(t,kind,dur)]}}
    AirMini:  {'M': {'ivs': [(s,e)], 'leak': [(t,v)], 'press': [(t,v)], 'events': [(t,kind,dur)]}}
    """
    # 建立每個 AirSense session 的 (start, nmin, 檔名)
    as_sess = {}
    for ddir in sorted(BASE.iterdir()):
        if not ddir.name.isdigit():
            continue
        for f in sorted(ddir.glob('*_PLD.edf')):
            try:
                start = dt.strptime(f.stem.rsplit('_', 1)[0], '%Y%m%d_%H%M%S')
            except ValueError:
                continue
            try:
                e = EDF(f)
            except Exception:
                continue
            if not e.data or not e.data[0]:
                continue
            leak = [v for rec in e.data for v in rec[e.idx['Leak.2s']]]
            press = [v for rec in e.data for v in rec[e.idx['MaskPress.2s']]]
            as_sess[f.stem.rsplit('_', 1)[0]] = (start, len(leak), leak, press)
    # EVE 事件（絕對時間）
    as_Events = []
    from datetime import datetime as _dt
    for ddir in sorted(BASE.iterdir()):
        if not ddir.name.isdigit():
            continue
        for f in sorted(ddir.glob('*_EVE.edf')):
            try:
                e0 = _dt.strptime(f.stem.rsplit('_', 1)[0], '%Y%m%d_%H%M%S')
            except ValueError:
                continue
            try:
                e = EDF(f)
            except Exception:
                continue
            for rec, onset, dur, text in decode_annotations(e):
                if onset is not None:
                    as_Events.append((e0 + timedelta(seconds=float(onset)), text,
                                      float(dur) if dur is not None else None))
    # AirMini 1 分值
    from datetime import datetime as _dt2
    am_leak, am_press = [], []
    _alm, _apm = set(), set()
    for d in runs['TherapyOneMinutePeriodic-Leak']:
        t0, iv = T(d['periodic']['startTime']), d['periodic'].get('interval', 60)
        for i, v in enumerate(d['periodic']['values']):
            if v is not None:
                _tk = str(t0 + timedelta(seconds=iv * i))
                if _tk in _alm:
                    continue
                _alm.add(_tk)
                am_leak.append((t0 + timedelta(seconds=iv * i), v * 60))
    for d in runs['TherapyOneMinutePeriodic-InspiratoryPressure']:
        t0, iv = T(d['periodic']['startTime']), d['periodic'].get('interval', 60)
        for i, v in enumerate(d['periodic']['values']):
            if v is not None:
                _tk = str(t0 + timedelta(seconds=iv * i))
                if _tk in _apm:
                    continue
                _apm.add(_tk)
                am_press.append((t0 + timedelta(seconds=iv * i), v))
    am_Ev = []
    _ae_seen = set()
    for d in runs['TherapyEvents-RespiratoryEvent']:
        for e in d.get('events', []):
            _ek = (e['time'], e['event'])
            if _ek in _ae_seen:
                continue
            _ae_seen.add(_ek)
            t_ = T(e['time'])
            if t_:
                am_Ev.append((t_, e['event'], float(e.get('durationSeconds', 0) or 0)))

    def night_of_start(t_):
        # 統一口徑：夜別 = (session 開始 − 12h) 的日期（=「睡覺那晚」的晚上日期）
        return (t_ - timedelta(hours=12)).date()

    # AirSense session → night（夜別用開始時間 −12h，與 DB/airmini_nights 同規則）
    for key, (start, n, leak, press) in as_sess.items():
        nd = night_of_start(start)
        N = NIGHTS.setdefault(nd, {})
        A = N.setdefault('A', {'sessions': [], 'events': []})
        A['sessions'].append((start, n, leak, press))
    for t_, text, dur in as_Events:
        cand = [nd for nd, N in NIGHTS.items() if 'A' in N and
                any(s0 - timedelta(minutes=2) <= t_ <= s0 + timedelta(seconds=n * 2 + 120)
                    for s0, n, _, _ in N['A']['sessions'])]
        for nd in cand:
            NIGHTS[nd]['A']['events'].append((t_, text, dur))
    # AirMini session → night
    am_iv = []
    open_on = None
    for tt, kind in mask_ev:
        if kind == 'MaskOn' and open_on is None:
            open_on = tt
        elif kind in ('MaskOff', 'PowerOff') and open_on is not None:
            if tt > open_on:
                am_iv.append((open_on, tt))
            open_on = None
    for s0, e0 in am_iv:
        nd = night_of_start(e0)
        N = NIGHTS.setdefault(nd, {})
        M = N.setdefault('M', {'ivs': [], 'leak': [], 'press': [], 'events': []})
        M['ivs'].append((s0, e0))
    for t_, v in am_leak:
        nd = night_of_start(t_)
        if nd in NIGHTS and 'M' in NIGHTS[nd]:
            NIGHTS[nd]['M']['leak'].append((t_, v))
    for t_, v in am_press:
        nd = night_of_start(t_)
        if nd in NIGHTS and 'M' in NIGHTS[nd]:
            NIGHTS[nd]['M']['press'].append((t_, v))
    iv_by_start = {s0: e0 for s0, e0 in am_iv}
    for t_, text, dur in am_Ev:
        hit = [s0 for s0 in iv_by_start if s0 - timedelta(minutes=1) <= t_ <= iv_by_start[s0]]
        if hit:
            e0 = iv_by_start[hit[0]]
            nd = night_of_start(e0)
            if 'M' in NIGHTS.get(nd, {}):
                NIGHTS[nd]['M']['events'].append((t_, text, dur))
    print('nights grouped:', len(NIGHTS))

def joint_stats(N):
    """由波形與事件直接算該夜統計：回傳 {'A': {...}, 'M': {...}}（缺席的機器無鍵）"""
    import statistics as st
    out = {}
    if 'A' in N:
        leak, press, dur_m, ev = [], [], 0.0, collections.Counter()
        ivs = []
        for s0, n, leak_s, press_s in N['A']['sessions']:
            e0 = s0 + timedelta(seconds=n * 2)
            ivs.append((s0, e0))
            dur_m += n
        def in_iv(t_):
            return any(s0 - timedelta(seconds=2) <= t_ <= e0 for s0, e0 in ivs)
        for s0, n2, leak_s, press_s in N['A']['sessions']:
            leak += leak_s
            press += press_s
        for t_, text, dur in N['A']['events']:
            if in_iv(t_):
                ev[text] += 1
        dur_m = sum(n2 * 2 / 60 for _, n2, _, _ in N['A']['sessions'])
        ca = ev.get('Central Apnea', 0) + ev.get('CentralApneaEnd', 0)
        oa = ev.get('Obstructive Apnea', 0) + ev.get('ObstructiveApneaEnd', 0)
        ua = ev.get('Unknown Apnea', 0) + ev.get('ApneaEnd', 0) + ev.get('Apnea', 0)
        hy = ev.get('Hypopnea', 0) + ev.get('HypopneaEnd', 0)
        hours = dur_m / 60
        out['A'] = {'usage': fmt2m(dur_m), 'dur_m': dur_m,
                    'l50': st.median(leak) if leak else 0, 'l95': (sorted(leak)[int(len(leak) * .95) - 1] if leak else 0),
                    'p50': st.median(press) if press else 0, 'p95': (sorted(press)[int(len(press) * .95) - 1] if press else 0),
                    'ahi': (ca + oa + ua + hy) / hours if hours else 0,
                    'ai': (ca + oa + ua) / hours if hours else 0,
                    'hi': hy / hours if hours else 0,
                    'cai': ca / hours if hours else 0, 'oai': oa / hours if hours else 0,
                    'uai': ua / hours if hours else 0, 'rera': ev.get('RERA', 0),
                    'csr_m': int(ev.get('CSR', 0))}
    if 'M' in N:
        leak, press, dur_m, ev = [], [], 0.0, collections.Counter()
        leak += [v for _, v in N['M']['leak']]
        press += [v for _, v in N['M']['press']]
        for s0, e0 in N['M']['ivs']:
            dur_m += (e0 - s0).total_seconds() / 60
        for t_, text, dur in N['M']['events']:
            ev[text] += 1
        ca = ev.get('CentralApneaEnd', 0)
        oa = ev.get('ObstructiveApneaEnd', 0)
        ua = ev.get('ApneaEnd', 0)
        hy = ev.get('HypopneaEnd', 0)
        hours = dur_m / 60
        out['M'] = {'usage': fmt2m(dur_m), 'dur_m': dur_m,
                    'l50': st.median(leak) if leak else 0, 'l95': (sorted(leak)[int(len(leak) * .95) - 1] if leak else 0),
                    'p50': st.median(press) if press else 0, 'p95': (sorted(press)[int(len(press) * .95) - 1] if press else 0),
                    'ahi': (ca + oa + ua + hy) / hours if hours else 0,
                    'ai': (ca + oa + ua) / hours if hours else 0,
                    'hi': hy / hours if hours else 0,
                    'cai': ca / hours if hours else 0, 'oai': oa / hours if hours else 0,
                    'uai': ua / hours if hours else 0, 'rera': ev.get('ReraEnd', 0),
                    'csr_m': None}
    return out

def fmt2m(mins):
    m = int(round(mins))
    return f'{m // 60} hr {m % 60:02d} min'

def draw_jointN(nd):
    N = NIGHTS.get(nd)
    if not N:
        return False
    t0 = t1 = None
    allp = []
    if 'A' in N:
        for s0, n2, _, _ in N['A']['sessions']:
            t0 = s0 if t0 is None else min(t0, s0)
            t1 = s0 + timedelta(seconds=n2 * 2) if t1 is None else max(t1, s0 + timedelta(seconds=n2 * 2))
    if 'M' in N:
        for s0, e0 in N['M']['ivs']:
            t0 = s0 if t0 is None else min(t0, s0)
            t1 = e0 if t1 is None else max(t1, e0)
    if t0 is None:
        return False
    w0 = t0 - timedelta(minutes=25)
    w1 = t1 + timedelta(minutes=25)
    fig = plt.figure(figsize=(7.403, 6.958), dpi=150)
    LAX = [0.115, 0.795, 0.868, 0.115]
    PAX = [0.115, 0.585, 0.868, 0.115]
    EAX = [0.115, 0.135, 0.868, 0.260]
    AS, AM = '#4a78c2', '#e0862a'

    def panel_xticks(ax, skip=False):
        tk = []
        t = w0.replace(minute=0, second=0) + timedelta(hours=1)
        while t <= w1:
            tk.append(t)
            t += timedelta(hours=1)
        ax.set_xticks(tk)
        ax.set_xticklabels([] if skip else [t.strftime('%H:%M') for t in tk], fontsize=6.2)
        ax.set_xlim(w0, w1)
        for t2 in tk:
            ax.axvline(t2, color='#f0f0f0', lw=0.5, zorder=0)
        for s_ in ('top', 'right'):
            ax.spines[s_].set_visible(False)
        ax.spines['left'].set_color('#cccccc')
        ax.spines['bottom'].set_color('#aaaaaa')
        ax.tick_params(length=2, width=0.5, pad=2)

    ax = fig.add_axes(LAX)
    if 'A' in N:
        for s0, n2, leak_s, _ in N['A']['sessions']:
            xs = [s0 + timedelta(seconds=2 * i) for i in range(n2)]
            ax.plot(xs, [v2 * 60 for v2 in leak_s], color=AS, lw=0.8)  # L/s → L/min
    if 'M' in N and N['M']['leak']:
        ax.plot([p[0] for p in N['M']['leak']], [p[1] for p in N['M']['leak']], color=AM, lw=1.0)
    ax.axhline(24, color='#8a8f96', lw=0.9, ls=(0, (5, 3)))
    panel_xticks(ax, skip=True)
    ax.set_ylim(0, 80)
    ax.set_yticks(range(0, 81, 20))
    ax.set_yticklabels([str(v) for v in range(0, 81, 20)], fontsize=7)
    fig.text(0.002, 0.938, 'Leak', color='#1b2b40', fontsize=10.5, weight='bold')
    fig.text(0.047, 0.938, '(L/min)', color='#8a94a2', fontsize=8.5)
    fig.add_artist(Line2D([0.640, 0.696], [0.952, 0.952], transform=fig.transFigure, color=AS, lw=1.2))
    fig.text(0.702, 0.943, 'AirSense', color='#33383e', fontsize=6.8)
    fig.add_artist(Line2D([0.768, 0.824], [0.952, 0.952], transform=fig.transFigure, color=AM, lw=1.2))
    fig.text(0.830, 0.943, 'AirMini', color='#33383e', fontsize=6.8)
    fig.add_artist(Line2D([0.892, 0.924], [0.952, 0.952], transform=fig.transFigure, color='#8a8f96', lw=1.0, ls=(0, (4, 2))))
    fig.text(0.930, 0.943, 'Set threshold', color='#33383e', fontsize=6.8)

    ax = fig.add_axes(PAX)
    if 'A' in N:
        for s0, n2, _, press_s in N['A']['sessions']:
            xs = [s0 + timedelta(seconds=2 * i) for i in range(n2)]
            ax.plot(xs, press_s, color=AS, lw=0.8)
    if 'M' in N and N['M']['press']:
        ax.plot([p[0] for p in N['M']['press']], [p[1] for p in N['M']['press']], color=AM, lw=1.0)
    ax.axhline(20, color='#8a8f96', lw=0.9, ls=(0, (5, 3)))
    ax.axhline(4, color='#8a8f96', lw=0.9, ls=(0, (5, 3)))
    panel_xticks(ax, skip=True)
    ax.set_ylim(0, 24)
    ax.set_yticks(range(4, 25, 4))
    ax.set_yticklabels([str(v) for v in range(4, 25, 4)], fontsize=7)
    fig.text(0.002, 0.728, 'Pressure', color='#1b2b40', fontsize=10.5, weight='bold')
    fig.text(0.075, 0.728, '(cmH2O)', color='#8a94a2', fontsize=8.5)
    fig.add_artist(Line2D([0.560, 0.616], [0.742, 0.742], transform=fig.transFigure, color=AS, lw=1.2))
    fig.text(0.622, 0.733, 'AirSense', color='#33383e', fontsize=6.8)
    fig.add_artist(Line2D([0.700, 0.756], [0.742, 0.742], transform=fig.transFigure, color=AM, lw=1.2))
    fig.text(0.762, 0.733, 'AirMini', color='#33383e', fontsize=6.8)

    ax = fig.add_axes(EAX)
    ev = N['A']['events'] if 'A' in N else []
    ev += N['M']['events'] if 'M' in N else []
    has_rera = any(t2[1] in ('RERA', 'ReraEnd') for t2 in ev)
    rows_n = 4 + (1 if has_rera else 0)
    rows_lab = ['Hypopnoea', 'Central', 'Obstructive', 'Unknown'] + (['RERA'] if has_rera else [])
    for i in range(rows_n - 1):
        ax.axhline(i + 0.55, color='#e8e8e8', lw=0.5, zorder=0)
    for src_, marker in (('A', AS), ('M', AM)):
        items = []
        if src_ == 'A' and 'A' in N:
            items = N['A']['events']
        elif src_ == 'M' and 'M' in N:
            items = N['M']['events']
        for t_, text, dur_ in items:
            ri = ev_row(text)
            if ri >= rows_n or (dur_ and dur_ < 10):
                continue
            hh = 0.16 + min((dur_ or 10) / 60 * 0.4, 0.42)
            ax.vlines(t_, ri + (0.10 if src_ == 'A' else 0.06), ri + 0.06 + hh, color=marker, lw=1.0)
    ax.set_ylim(rows_n, 0)
    ax.set_yticks([i + 0.06 for i in range(rows_n)])
    ax.set_yticklabels(rows_lab, fontsize=7, ha='right')
    ax.yaxis.set_tick_params(pad=2, length=0)
    panel_xticks(ax)
    fig.text(0.002, 0.500, 'Events', color='#1b2b40', fontsize=10.5, weight='bold')
    fig.add_artist(Line2D([0.700, 0.756], [0.517, 0.517], transform=fig.transFigure, color=AS, lw=1.4))
    fig.text(0.762, 0.505, 'AirSense', color='#33383e', fontsize=6.8)
    fig.add_artist(Line2D([0.830, 0.886], [0.517, 0.517], transform=fig.transFigure, color=AM, lw=1.4))
    fig.text(0.892, 0.505, 'AirMini', color='#33383e', fontsize=6.8)

    fig.savefig(OUT / f'detN_{nd.strftime("%Y%m%d")}.png')
    plt.close(fig)
    return True


# ---------------- 檔案 2 合併頁面板（雙機同軸、連續日期）----------------
def draw_both_f2():
    import csv as _csv
    amr = {}
    with open(os.environ.get('AIRMINI_NIGHTS_CSV', 'airmini_nights.csv'), encoding='utf-8-sig') as fp:
        for r2 in _csv.DictReader(fp):
            amr[r2['date']] = r2
    all_d = sorted(amr.keys())
    w0 = dt.strptime(all_d[0], '%Y-%m-%d').date()
    w1 = dt.strptime(all_d[-1], '%Y-%m-%d').date()
    dates = [w0 + timedelta(days=i) for i in range((w1 - w0).days + 1)]

    def amv(d, key, mul=1.0):
        r2 = amr.get(d.strftime('%Y-%m-%d'))
        try:
            return float(r2[key]) * mul if r2 and r2[key] not in ('', None) else None
        except (ValueError, KeyError):
            return None

    def asv(d, key, mul=1.0):
        """AirSense 值（匯出口徑：11/01 只有第一段午睡 2h10）"""
        r = rows.get(d)
        if not r:
            return None
        if key in ('duration',):
            if d == dt(2026, 11, 1).date():
                v = 130.0
            else:
                ons = [x for x in r['mask_on'] if x not in (-1, None)]
                offs = [x for x in r['mask_off'] if x not in (-1, None)]
                v = sum(b - a for a, b in zip(ons, offs) if b > a)
            return v * mul if v else None
        val = r.get(key)
        return val * mul if val else None

    months = [i for i, d in enumerate(dates) if d.day == 1]
    if len(dates) - 1 - months[-1] >= 12:
        months.append(len(dates) - 1)
    AS, AM, ASL, AML = '#5b87d0', '#f2a93b', '#244f8a', '#b86a12'
    HI_A, AI_A = '#c3aae0', '#6a4a9e'
    HI_M, AI_M = '#8fd0c2', '#1a7f70'

    def frame(hpt, ymax, ystep, wpt=434):
        fig = plt.figure(figsize=(wpt / 72, hpt / 72), dpi=150)
        ax = fig.add_axes([0.028, 0.20, 0.965, 0.68])
        ax.set_xlim(-1, len(dates))
        ax.set_ylim(0, ymax)
        for yy in range(ystep, ymax + 1, ystep):
            ax.axhline(yy, color='#ececec', lw=0.6, zorder=0)
        ax.set_yticks(range(0, ymax + 1, ystep))
        ax.set_yticklabels([str(x) for x in range(0, ymax + 1, ystep)], fontsize=6)
        ax.set_xticks(months)
        ax.set_xticklabels([dates[i].strftime('%b') for i in months], fontsize=6)
        for s_ in ('top', 'right'):
            ax.spines[s_].set_visible(False)
        for s_ in ('left', 'bottom'):
            ax.spines[s_].set_color('#bbbbbb')
        ax.tick_params(length=2, width=0.5, pad=1)
        return fig, ax

    def bars(ax, i, ha, hm):
        if ha and hm:
            ax.bar(i - 0.19, ha, width=0.36, lw=0)
            ax.bar(i + 0.04, hm, width=0.36, lw=0)
        elif ha:
            ax.bar(i, ha, width=0.72, lw=0)
        elif hm:
            ax.bar(i, hm, width=0.72, lw=0)

    # 使用（小時）：AirSense 綠/紅、AirMini 藍
    fig, ax = frame(94, 10, 2)
    for i, d in enumerate(dates):
        ha = asv(d, 'duration', 1 / 60.0)
        hm = amv(d, '使用分', 1 / 60.0)
        if ha and hm:
            ax.bar(i - 0.19, min(ha, 9.8), width=0.36, color=GREEN if ha >= 4 else RED, lw=0)
            ax.bar(i + 0.04, min(hm, 9.8), width=0.36, color=BLUE_AM, lw=0)
        elif ha:
            ax.bar(i, min(ha, 9.8), width=0.72, color=GREEN if ha >= 4 else RED, lw=0)
        elif hm:
            ax.bar(i, min(hm, 9.8), width=0.72, color=BLUE_AM, lw=0)
    fig.savefig(OUT / 'b2_usage.png')
    plt.close(fig)

    # 漏氣 95th：AirSense 藍 / AirMini 橘 + median 綠tick + 24 虛線
    fig, ax = frame(108, 80, 20)
    for i, d in enumerate(dates):
        ha = asv(d, 'leak_95', 60)
        hm = amv(d, '漏氣95')
        if ha and hm:
            ax.bar(i - 0.19, min(ha, 78), width=0.36, color=AS, lw=0)
            ax.bar(i + 0.04, min(hm, 78), width=0.36, color=AM, lw=0)
            ta = asv(d, 'leak_50', 60)
            tm = amv(d, '漏氣med')
            if ta:
                ax.plot([i - 0.36, i - 0.02], [min(ta, 78)] * 2, color=ASL, lw=1.4)
            if tm:
                ax.plot([i + 0.02, i + 0.38], [min(tm, 78)] * 2, color=AML, lw=1.4)
        elif ha:
            ax.bar(i, min(ha, 78), width=0.72, color=AS, lw=0)
            ta = asv(d, 'leak_50', 60)
            if ta:
                ax.plot([i - 0.36, i + 0.36], [min(ta, 78)] * 2, color=ASL, lw=1.4)
        elif hm:
            ax.bar(i, min(hm, 78), width=0.72, color=AM, lw=0)
            tm = amv(d, '漏氣med')
            if tm:
                ax.plot([i - 0.36, i + 0.36], [min(tm, 78)] * 2, color=AML, lw=1.4)
    ax.axhline(24, color='#8a8f96', lw=0.8, ls=(0, (4, 3)), zorder=0)
    fig.savefig(OUT / 'b2_leak.png')
    plt.close(fig)

    # 壓力 95th：AirSense 藍 / AirMini 橘 + median
    fig, ax = frame(103, 32, 4)
    for i, d in enumerate(dates):
        ha = asv(d, 'mask_press_95')
        hm = amv(d, '壓力95')
        if ha and hm:
            ax.bar(i - 0.19, min(ha, 31), width=0.36, color=AS, lw=0)
            ax.bar(i + 0.04, min(hm, 31), width=0.36, color=AM, lw=0)
            ta, tm = asv(d, 'mask_press_50'), amv(d, '壓力med')
            if ta:
                ax.plot([i - 0.36, i - 0.02], [min(ta, 31)] * 2, color=ASL, lw=1.4)
            if tm:
                ax.plot([i + 0.02, i + 0.38], [min(tm, 31)] * 2, color=AML, lw=1.4)
        elif ha:
            ax.bar(i, min(ha, 31), width=0.72, color=AS, lw=0)
            ta = asv(d, 'mask_press_50')
            if ta:
                ax.plot([i - 0.36, i + 0.36], [min(ta, 31)] * 2, color=ASL, lw=1.4)
        elif hm:
            ax.bar(i, min(hm, 31), width=0.72, color=AM, lw=0)
            tm = amv(d, '壓力med')
            if tm:
                ax.plot([i - 0.36, i + 0.36], [min(tm, 31)] * 2, color=AML, lw=1.4)
    fig.savefig(OUT / 'b2_press.png')
    plt.close(fig)

    # AHI：AI（深）在下、HI（淺）在上；y 0-40；AirSense 紫 / AirMini 藍
    fig, ax = frame(94, 40, 10)
    for i, d in enumerate(dates):
        ra = rows.get(d)
        rm = amr.get(d.strftime('%Y-%m-%d'))

        def g(r2, k):
            if not r2:
                return 0.0
            try:
                return float(r2[k]) if r2[k] not in ('', None) else 0.0
            except (ValueError, KeyError):
                return 0.0
        ha_h = g(ra, 'hi')
        ha_a = g(ra, 'ai')
        hm_h = g(rm, 'HI')
        hm_a = g(rm, 'AI')
        if ha_h > 0 or ha_a > 0:
            if ha_a:
                ax.bar(i - 0.19, ha_a, width=0.36, color=AI_A, lw=0)
            if ha_h:
                ax.bar(i - 0.19, ha_h, bottom=ha_a, width=0.36, color=HI_A, lw=0)
        if hm_h > 0 or hm_a > 0:
            if hm_a:
                ax.bar(i + 0.04, hm_a, width=0.36, color=AI_M, lw=0)
            if hm_h:
                ax.bar(i + 0.04, hm_h, bottom=hm_a, width=0.36, color=HI_M, lw=0)
    ax.set_xticklabels([t.get_text() + '\n2026' for t in ax.get_xticklabels()], fontsize=6)
    ax.set_yticks(range(0, 41, 10))
    ax.set_yticklabels([str(x) for x in range(0, 41, 10)], fontsize=6)
    for yy2 in range(10, 41, 10):
        ax.axhline(yy2, color='#ececec', lw=0.6, zorder=0)
    fig.savefig(OUT / 'b2_ahi.png')
    plt.close(fig)

    # 合規頁 mini A（原版 26/07-01/11 視窗，AirSense；11/01 = 2h10 紅柱）
    w0s, w1s = dt(2026, 7, 26).date(), dt(2026, 11, 1).date()
    ds = [w0s + timedelta(days=i) for i in range((w1s - w0s).days + 1)]
    fig, ax = frame(68, 10, 2)
    for i, d in enumerate(ds):
        h = asv(d, 'duration', 1 / 60.0)
        if h:
            ax.bar(i, min(h, 10), width=0.72, color=GREEN if h >= 4 else RED, lw=0)
    fig.savefig(OUT / 'f2s_mini.png')
    plt.close(fig)
    # 合規頁 mini B（雙機並排、連續視窗 434x68）
    fig, ax = frame(68, 10, 2)
    for i, d in enumerate(dates):
        ha = asv(d, 'duration', 1 / 60.0)
        hm = amv(d, '使用分', 1 / 60.0)
        if ha and hm:
            ax.bar(i - 0.19, min(ha, 9.8), width=0.36, color=GREEN if ha >= 4 else RED, lw=0)
            ax.bar(i + 0.04, min(hm, 9.8), width=0.36, color=BLUE_AM, lw=0)
        elif ha:
            ax.bar(i, min(ha, 9.8), width=0.72, color=GREEN if ha >= 4 else RED, lw=0)
        elif hm:
            ax.bar(i, min(hm, 9.8), width=0.72, color=BLUE_AM, lw=0)
    fig.savefig(OUT / 'b1_mini.png')
    plt.close(fig)
    print('both panels OK（匯出口徑：11/01 = 2h10 紅柱）')
if __name__ == '__main__':
    draw_f1()
    draw_both_f2()
    build_nights()
    # detN：每一夜（A 或 M）一張
    n = 0
    for nd in sorted(NIGHTS):
        try:
            if draw_jointN(nd):
                n += 1
        except Exception as ex:
            print('WARN detN skip', nd, type(ex).__name__, ex)
    # night_stats.json：書 3 頁清單 + rowsA 缺值日的 A 統計來源
    NS = {}
    for nd, N in sorted(NIGHTS.items()):
        k = str(nd)
        S = NS.setdefault(k, {})
        if 'A' in N:
            sA = {}
            r = rows.get(nd)
            if r is not None:
                sA = dict(usage='%.1f hr' % ((r['duration'] or 0) / 60),
                          l50=(r['leak_50'] or 0) * 60, l95=(r['leak_95'] or 0) * 60,
                          p50=r['mask_press_50'] or 0, p95=r['mask_press_95'] or 0,
                          ahi=r['ahi'] or 0, ai=r['ai'] or 0, hi=r['hi'] or 0,
                          cai=r['cai'] or 0, oai=r['oai'] or 0, uai=r['uai'] or 0,
                          csr_m=r['csr'] or 0)
            S['A'] = sA
        if 'M' in N:
            S['M'] = {}
    import json as _json
    (OUT / 'night_stats.json').write_text(_json.dumps(NS, ensure_ascii=False), encoding='utf-8')
    print('detN charts:', n, '、night_stats.json:', len(NS), '夜')
