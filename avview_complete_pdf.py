"""AirView 原版版式 PDF（20261127 吳俊毅 202607-202611_{1,2,3}.pdf）
幾何依原版 20261102 版實測：標題 18pt x=151；表格 x=151 寬 397、行距 19、字 9pt；
頁首病患 11pt；頁尾 7pt y=18 'Page 1 of 1'；版本字串 4.5.1-1.0.0。
檔案2 頁1＝原版數值＋AirMini 補充；頁2＝原版左欄＋雙機同軸四面板＋AirMini 補充表；
檔案3＝睡眠夜逐夜（雙機同軸、時間連續）。
"""
import collections, csv, json, statistics, sys
import datetime as dtm
from datetime import timedelta
from pathlib import Path

from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

pdfmetrics.registerFont(TTFont('TC', os.environ.get('AIRMINI_FONT', '/usr/share/fonts/truetype/noto/NotoSansTC.ttf')))
pdfmetrics.registerFont(TTFont('TCB', os.environ.get('AIRMINI_FONT', '/usr/share/fonts/truetype/noto/NotoSansTC.ttf')))

AV = Path('/home/adrian/.hermes/cache/scratch/avview')
OUTDIR = Path('/home/adrian/airmini/報告')
OUTDIR.mkdir(parents=True, exist_ok=True)

NAVY = '#2e5e8e'
DARKTX = '#33383e'
GRAYTX = '#8a9098'
TBLHD = '#454d52'
HAIR = '#e4e6e8'
ORANGE = '#e8a33d'
GREENLB = '#4f9e3f'
REDLB = '#d23b2e'

PATIENT1 = 'RHA0418689 - 吳俊毅, 北區台大睡眠-'
PATIENT2 = '眠-蕭純聿'
FOOT = 'Printed on 27/11/2026 - ResMed AirView version 4.5.1-1.0.0'
TX = 151
TW = 397

# ---------------- 資料 ----------------
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from resmed_s10 import parse_str

rowsA = {}
for r in parse_str(os.environ.get('AIRMINI_STR', 'STR.edf')):
    rowsA[r['date']] = r
rowsM = {}
with open(os.environ.get('AIRMINI_NIGHTS_CSV', 'airmini_nights.csv'), encoding='utf-8-sig') as fp:
    for r in csv.DictReader(fp):
        rowsM[r['date']] = r
_mkeys = sorted(rowsM)
MW0 = dtm.datetime.strptime(_mkeys[0], '%Y-%m-%d').date()
MW1 = dtm.datetime.strptime(_mkeys[-1], '%Y-%m-%d').date()

def win_stats_M(w0, w1):
    used, ge4, lt4 = 0, 0, 0
    total_m = 0.0
    durs = []
    for k in sorted(rowsM):
        d = dtm.datetime.strptime(k, '%Y-%m-%d').date()
        if not (w0 <= d <= w1):
            continue
        r = rowsM[k]
        try:
            m = float(r['使用分']) if r['使用分'] not in ('', None) else 0.0
        except (ValueError, KeyError):
            m = 0.0
        if m > 0:
            total_m += m
            durs.append(m)
            if m >= 240:
                ge4 += 1
            else:
                lt4 += 1
            used += 1
    days = (w1 - w0).days + 1
    return dict(days=days, used=used, ge4=ge4, lt4=lt4, unused=days - used,
                minutes=total_m, durs=durs)

def agg_M(w0, w1):
    vs = collections.defaultdict(list)

    def g(r, k):
        try:
            return float(r[k]) if r[k] not in ('', None) else 0.0
        except (ValueError, KeyError):
            return 0.0
    for k, r in rowsM.items():
        d = dtm.datetime.strptime(k, '%Y-%m-%d').date()
        if not (w0 <= d <= w1):
            continue
        for kk, col in (('p50', '壓力med'), ('p95', '壓力95'), ('pmax', '壓力max'),
                        ('l50', '漏氣med'), ('l95', '漏氣95'), ('lmax', '漏氣max'),
                        ('ahi', 'AHI'), ('ai', 'AI'), ('hi', 'HI'), ('cai', 'CAI'),
                        ('oai', 'OAI'), ('uai', 'UAI'), ('rera', 'RERA')):
            vs[kk].append(g(r, col))
    return {k: (statistics.mean(x) if x else 0.0) for k, x in vs.items()}

SM = win_stats_M(MW0, MW1)
MM = agg_M(MW0, MW1)
DATERANGE_A = '26/07/2026 - 26/11/2026'
DATERANGE_M = MW0.strftime('%d/%m/%Y') + ' - ' + MW1.strftime('%d/%m/%Y')

def fmt_hm(mins):
    m = int(round(mins))
    return f'{m // 60} hours {m % 60} minutes'

def fmt_hm2(mins):
    m = int(round(mins))
    return f'{m // 60} hr {m % 60:02d} min'

def mean_hm(mins, n):
    return fmt_hm(mins / n) if n else '—'

# ---------------- 共用元件（原版幾何）----------------
ARCS = (('#7c3fa5', 0), ('#3b7bd0', -4.6), ('#e0455a', -9.2))

def header(c, datestr=DATERANGE_A, date_y=776):
    c.setLineWidth(2.4)
    for col, dy in ARCS:
        c.setStrokeColor(colors.HexColor(col))
        c.setLineCap(1)
        c.arc(30, 830 + dy, 50, 842 + dy, 105, 150)
    c.setFillColor(colors.HexColor('#20315e'))
    c.setFont('TCB', 15)
    c.drawString(30, 816, 'Resmed')
    c.setFont('TC', 12)
    c.drawString(30, 800, 'AirView')
    c.setFont('TC', 6)
    c.setFillColor(colors.HexColor(GRAYTX))
    c.drawString(151, 804, 'Northern Territory')
    c.setFont('TCB', 11)
    c.setFillColor(colors.HexColor(DARKTX))
    c.drawRightString(561, 812, PATIENT1)
    c.drawRightString(561, date_y, datestr)
    c.setFont('TC', 9)
    c.setFillColor(colors.HexColor(GRAYTX))
    c.drawRightString(561, 760, 'DOB: 31/01/1983')
    c.drawRightString(561, 748, 'Age: 43 years')

def footer(c):
    c.setFont('TC', 7)
    c.setFillColor(colors.HexColor(GRAYTX))
    c.drawString(45, 18, FOOT)
    c.drawRightString(556, 18, 'Page 1 of 1')

def title(c, text, y=720, fs=18):
    c.setFont('TC', fs)
    c.setFillColor(colors.HexColor(NAVY))
    c.drawString(151, y, text)

def hrule(c, x, y, w):
    c.setStrokeColor(colors.HexColor('#b9c1c9'))
    c.setLineWidth(0.7)
    c.line(x, y, x + w, y)

def airview_table(c, x, y, w, head, head_right, rows, row_h=19, head_h=13,
                  fs_head=12, fs=9, lab_dx=5):
    """原版表格：深色表頭 + 19pt 行距 + 9pt 字。rows=(label, v) 或 (label, (lab,val),(lab,val),(lab,val))"""
    c.setFillColor(colors.HexColor(TBLHD))
    c.rect(x, y - head_h, w, head_h, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont('TCB', fs_head)
    c.drawString(x + lab_dx, y - head_h + 3.5, head)
    if head_right:
        c.setFillColor(colors.HexColor('#d5d9dc'))
        c.setFont('TC', 9)
        c.drawString(x + 290, y - head_h + 4, head_right)
    yy = y - head_h
    cols = [(112, 166), (206, 286), (323, 376)]
    for item in rows:
        c.setStrokeColor(colors.HexColor(HAIR))
        c.setLineWidth(0.5)
        c.line(x, yy, x + w, yy)
        label, values = item[0], item[1:]
        c.setFillColor(colors.HexColor(DARKTX))
        c.setFont('TC', fs)
        c.drawString(x + lab_dx, yy - row_h + 8, label)
        if len(values) == 1 and not isinstance(values[0], tuple):
            v = values[0]
            if isinstance(v, tuple):
                v = v[0]
            c.setFillColor(colors.HexColor(DARKTX))
            c.setFont('TCB', fs)
            c.drawRightString(x + w - 6, yy - row_h + 8, str(v))
        else:
            k = 0
            for v in values:
                if isinstance(v, (tuple, list)) and len(v) == 2 and isinstance(v[0], str):
                    c.setFillColor(colors.HexColor(GRAYTX))
                    c.setFont('TC', fs)
                    c.drawString(x + cols[k][0], yy - row_h + 8, str(v[0]) + ':')
                    c.setFillColor(colors.HexColor(DARKTX))
                    c.setFont('TCB', fs)
                    c.drawString(x + cols[k][1], yy - row_h + 8, str(v[1]))
                    k += 1
                elif v == '' or v is None:
                    continue
                else:
                    c.setFillColor(colors.HexColor(DARKTX))
                    c.setFont('TCB', fs)
                    c.drawRightString(x + w - 6, yy - row_h + 8, str(v))
                    k += 1
        yy -= row_h
    c.setStrokeColor(colors.HexColor(HAIR))
    c.line(x, yy, x + w, yy)
    return yy

# ---------------- 檔案 1（原版 90 天視窗）----------------
def file1():
    p1 = OUTDIR / '20261127 吳俊毅 202607-202611_1.pdf'
    c = canvas.Canvas(str(p1), pagesize=(842, 595))
    c.setTitle('Charts - 吳俊毅')
    c.setLineWidth(2.4)
    for col, dy in ARCS:
        c.setStrokeColor(colors.HexColor(col))
        c.setLineCap(1)
        c.arc(30, 528 + dy, 50, 540 + dy, 105, 150)
    c.setFillColor(colors.HexColor('#20315e'))
    c.setFont('TCB', 15)
    c.drawString(30, 514, 'Resmed')
    c.setFont('TC', 12)
    c.drawString(30, 498, 'AirView')
    c.setFont('TC', 6)
    c.setFillColor(colors.HexColor(GRAYTX))
    c.drawString(97, 540, 'Northern Territory')
    c.setFont('TCB', 11)
    c.setFillColor(colors.HexColor(DARKTX))
    c.drawRightString(561, 520, PATIENT1)
    c.setFont('TC', 8)
    c.setFillColor(colors.HexColor(GRAYTX))
    c.drawString(40, 486, 'Back to patients')
    pairs = [('--', 'Patient ID'), ('31/01/1983', 'D.O.B.'), ('17/04/2026', 'Setup date'),
             ('OK', 'Compliant'), ('70%', 'Last 30'), ('AutoSet', 'AirSense 10 AutoSet')]
    xs = [330, 396, 468, 545, 596, 680]
    for (val, lab), x in zip(pairs, xs):
        c.setFont('TCB', 10)
        if lab == 'Compliant':
            c.setStrokeColor(colors.HexColor(GREENLB))
            c.setLineWidth(1.6)
            c.setLineCap(1)
            c.line(x - 4, 460, x - 1.5, 457.4)
            c.line(x - 1.5, 457.4, x + 4.5, 462.5)
        else:
            c.setFillColor(colors.HexColor(DARKTX))
            c.drawCentredString(x, 457, val)
        c.setFont('TC', 7.8)
        c.setFillColor(colors.HexColor(GRAYTX))
        c.drawCentredString(x, 447, lab)
    tabs = ['Charts', 'Patient details', 'Prescription', 'Remote Assist', 'Notes', 'Logs', 'Preferences']
    tab_w = [34, 62, 58, 64, 30, 26, 58]
    tx = 44
    divs = [44, 44 + tab_w[0] + 22]
    c.setFont('TC', 8.8)
    for i, t in enumerate(tabs):
        c.setFillColor(colors.HexColor(DARKTX if i == 0 else '#a7aeb8'))
        c.drawString(tx, 435, t)
        if i < len(tabs) - 1:
            tx += tab_w[i] + 22
            divs.append(tx - 11)
    for dxx in divs[1:-1]:
        c.setStrokeColor(colors.HexColor('#c9cfd6'))
        c.setLineWidth(0.7)
        c.line(dxx, 430, dxx, 446)
    c.setFillColor(colors.HexColor(DARKTX))
    c.setFont('TC', 8.6)
    c.setStrokeColor(colors.HexColor('#b9c2cc'))
    c.setLineWidth(0.8)
    c.roundRect(80, 395, 122, 19, 2.5, fill=0, stroke=1)
    c.drawString(88, 401.5, 'Fixed time period')
    c.setFillColor(colors.HexColor('#5a626c'))
    pth = c.beginPath()
    pth.moveTo(194, 406)
    pth.lineTo(204, 406)
    pth.lineTo(199, 400.5)
    pth.close()
    c.drawPath(pth, fill=1, stroke=0)
    c.setFont('TC', 8.6)
    c.setFillColor(colors.HexColor(DARKTX))
    c.drawString(222, 401.5, '90 day period ending')
    c.setFont('TCB', 9)
    c.drawString(334, 401.5, '22/11/2026')
    c.setFont('TC', 9)
    c.setFillColor(colors.HexColor('#b6bcc4'))
    c.drawString(414, 401.5, 'Update')
    c.setFont('TC', 8.3)
    c.setFillColor(colors.HexColor(GRAYTX))
    c.drawRightString(752, 375, 'Compliant use     Non-compliant use     AirMini use')
    c.drawImage(str(AV / 'f1_usage.png'), 88.6, 200, width=662, height=179)
    footer(c)
    c.save()
    print('檔案1 OK', p1.name)

# ---------------- 檔案 2（原版數值＋AirMini 補充；頁2 雙機同軸圖）----------------
def file2():
    p2 = OUTDIR / '20261127 吳俊毅 202607-202611_2.pdf'
    c = canvas.Canvas(str(p2), pagesize=(595, 869))
    c.setTitle('Compliance and Therapy Report - 吳俊毅')

    def device_bar(y, name, sn):
        c.setFillColor(colors.HexColor(TBLHD))
        c.rect(TX, y, TW, 13.5, fill=1, stroke=0)
        c.setFillColor(colors.white)
        c.setFont('TCB', 12)
        c.drawString(TX + 5, y + 3.5, name)
        c.setFont('TC', 10)
        c.setFillColor(colors.HexColor('#d5d9dc'))
        c.drawRightString(TX + TW - 8, y + 3.8, sn)

    def panel_title(y, t_main, t_sub):
        c.setFont('TCB', 11)
        c.setFillColor(colors.HexColor(DARKTX))
        c.drawString(151, y, t_main)
        c.setFont('TC', 8)
        c.setFillColor(colors.HexColor(GRAYTX))
        c.drawString(151 + c.stringWidth(t_main, 'TCB', 11) + 2, y, t_sub)

    def left_stats(ypad, rows):
        ry = ypad
        lcol = {'g': GREENLB, 'r': REDLB, 'o': ORANGE, 'k': DARKTX, '': GRAYTX}
        for lab, val, cc in rows:
            ry -= 10
            if lab:
                c.setFont('TC', 7)
                c.setFillColor(colors.HexColor(lcol.get(cc, GRAYTX) if cc != 'k' else DARKTX))
                c.drawString(47, ry, lab)
            if val:
                c.setFont('TCB', 7)
                c.setFillColor(colors.HexColor(ORANGE if cc == 'o' else DARKTX))
                c.drawRightString(148, ry, val)
        return ry

    # ---- 頁 1：Compliance（原版數值）+ AirMini 補充 ----
    header(c, DATERANGE_A, date_y=776)
    title(c, 'Compliance Report', y=720)
    yy = airview_table(c, TX, 703, TW, 'Usage', '26/07/2026 - 01/11/2026', [
        ('Usage days', '71/124 days (57%)'),
        ('>= 4 hours', '63 days (51%)'),
        ('< 4 hours', '8 days (6%)'),
        ('Usage hours', '436 hours 41 minutes'),
        ('Average usage (total days)', '3 hours 31 minutes'),
        ('Average usage (days used)', '6 hours 09 minutes'),
        ('Median usage (days used)', '6 hours 23 minutes'),
        ('Total used hours (value since last reset - 26/11/2026)', '631 hours')])
    yy = airview_table(c, TX, yy - 13, TW, 'AirSense 10 AutoSet', '', [
        ('Serial number', '22261252530'), ('Mode', 'AutoSet'),
        ('Min Pressure', '4 cmH2O'), ('Max Pressure', '20 cmH2O'),
        ('EPR', 'Off'), ('Response', 'Standard')])
    yy = airview_table(c, TX, yy - 13, TW, 'Therapy', '', [
        ('Pressure - cmH2O', ('Median:', 7.1), ('95th percentile:', 9.5), ('Maximum:', 10.7)),
        ('Leaks - L/min', ('Median:', 5.8), ('95th percentile:', 32.4), ('Maximum:', 48.3)),
        ('Events per hour', ('AI:', 3.4), ('HI:', 1.0), ('AHI:', 4.5)),
        ('Apnoea Index', ('Central:', 2.8), ('Obstructive:', 0.3), ('Unknown:', 0.3)),
        ('Cheyne-Stokes respiration (average duration per night)', '', '', '1 minute (0%)')])
    title(c, 'Usage - hours', y=261, fs=15)
    c.drawImage(str(AV / 'b1_mini.png'), 155, 88, width=406, height=68)
    c.setFont('TC', 7)
    c.setFillColor(colors.HexColor(GRAYTX))
    c.drawString(155, 68, 'AirSense（綠＝>=4h／紅＝<4h）＋ AirMini（藍），時間連續')
    footer(c)
    c.showPage()
    # ---- 頁 2：Therapy（原版左欄＋雙機同軸圖）＋補充表 ----
    header(c, DATERANGE_A, date_y=783)
    title(c, 'Therapy Report', y=720)
    device_bar(690, 'AirSense 10 AutoSet', 'SN: 22261252530')
    panel_title(666, 'Usage', '(hours)')
    left_stats(658, [('Usage days', '71/124 (57%)', 'k'), ('>= 4 hour days', '63 (51%)', 'g'),
                     ('< 4 hour days', '8 (6%)', 'r'), ('Days not used', '53 (43%)', ''),
                     ('Days no data', '3 (2%)', ''), ('Used/day (avg.)', '6.2 hrs.', 'k')])
    c.drawImage(str(AV / 'b2_usage.png'), 155, 560, width=406, height=87.9)
    panel_title(519, 'Leak', '(L/min)')
    left_stats(511, [('Set threshold', '24.0 L/min', ''), ('Maximum (avg)', '48.3', 'o'),
                     ('95th % (avg)', '32.4', 'o'), ('Median (avg)', '5.8', ''),
                     ('Mask', 'N20 / Large', ''),])
    c.drawImage(str(AV / 'b2_leak.png'), 155, 400, width=406, height=101)
    panel_title(370, 'Pressure', '(cmH2O)')
    left_stats(362, [('Mode', 'AutoSet', 'k'), ('Set EPR', 'Off', ''),
                     ('Set Max Pressure', '20.0', ''), ('Set Min Pressure', '4.0', ''),
                     ('Maximum (avg)', '10.7', ''), ('95th % (avg)', '9.5', ''),
                     ('Median (avg)', '7.1', '')])
    c.drawImage(str(AV / 'b2_press.png'), 155, 256, width=406, height=96.3)
    panel_title(221, 'AHI', '(events/hour)')
    # 原版圖例：AHI 紫字+空心方塊 / HI 淺紫 / AI 深紫 / CAI-OAI-UAI-CSR 灰字，值粗體右對齊
    ahi_leg = [('AHI', '4.5', '#8d6cb8'), ('HI', '1.0', '#c3aae0'), ('AI', '3.4', '#5b3e8e'),
               ('CAI', '2.8', ''), ('OAI', '0.3', ''), ('UAI', '0.3', ''),
               ('CSR% (avg)', '0.1', '')]
    ly = 204
    for lab, val, col in ahi_leg:
        ly -= 10
        if lab in ('AHI', 'HI', 'AI'):
            if lab == 'AHI':
                c.setFillColor(colors.white)
                c.setStrokeColor(colors.HexColor('#8d6cb8'))
                c.setLineWidth(0.8)
                c.rect(72, ly + 0.5, 5.5, 5.5, fill=1, stroke=1)
            else:
                c.setFillColor(colors.HexColor('#c3aae0' if lab == 'HI' else '#5b3e8e'))
                c.rect(72, ly + 0.5, 5.5, 5.5, fill=1, stroke=0)
            c.setFont('TCB', 7)
            c.setFillColor(colors.HexColor(col))
            c.drawString(47, ly, lab)
            c.setFillColor(colors.HexColor(DARKTX))
            c.drawRightString(148, ly, val)
        else:
            c.setFont('TC', 7)
            c.setFillColor(colors.HexColor('#8a9098'))
            c.drawString(47, ly, lab)
            c.setFont('TCB', 7)
            c.setFillColor(colors.HexColor(DARKTX))
            c.drawRightString(148, ly, val)
    c.drawImage(str(AV / 'b2_ahi.png'), 155, 116, width=406, height=87.9)
    # AirMini 補充表（並排）
    airview_table(c, 151, 106, 195, 'Usage (AirMini)', DATERANGE_M, [
        ('Nights', f"{SM['used']} ({round(SM['used'] / SM['days'] * 100)}%)"),
        ('Hours', fmt_hm(SM['minutes'])),
        ('Avg (used)', mean_hm(SM['minutes'], SM['used'])),
        ('Median', fmt_hm(statistics.median(SM['durs'])) if SM['durs'] else '—')],
        row_h=13, head_h=12, fs_head=9, fs=7.5, lab_dx=4)
    airview_table(c, 353, 106, 195, 'Therapy (AirMini)', '', [
        ('Press m/95/max', f"{MM['p50']:.1f}/{MM['p95']:.1f}/{MM['pmax']:.1f}"),
        ('Leaks m/95/max', f"{MM['l50']:.1f}/{MM['l95']:.1f}/{MM['lmax']:.1f}"),
        ('AI/HI/AHI', f"{MM['ai']:.1f}/{MM['hi']:.1f}/{MM['ahi']:.1f}"),
        ('C/O/U', f"{MM['cai']:.1f}/{MM['oai']:.1f}/{MM['uai']:.1f}"),
        ('RERA', f"{MM['rera']:.1f} 筆/晚")],
        row_h=13, head_h=12, fs_head=9, fs=7.5, lab_dx=4)
    footer(c)
    c.save()
    print('檔案2 OK', p2.name, '(2 頁)')

# ---------------- 檔案 3（睡眠夜）----------------
def compact_table(c, x, y, w, head, rows, row_h=12.0, head_h=12.5):
    c.setFillColor(colors.HexColor(TBLHD))
    c.rect(x, y - head_h, w, head_h, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont('TCB', 7.8)
    c.drawString(x + 5, y - head_h + 3.4, head)
    yy = y - head_h
    for lab, val in rows:
        c.setStrokeColor(colors.HexColor(HAIR))
        c.setLineWidth(0.4)
        c.line(x, yy, x + w, yy)
        c.setFillColor(colors.HexColor('#5a6168'))
        c.setFont('TC', 7.2)
        c.drawString(x + 6, yy + 3.2, lab)
        c.setFillColor(colors.HexColor(DARKTX))
        c.setFont('TCB', 7.7)
        c.drawRightString(x + w - 5, yy + 3.2, val)
        yy -= row_h
    c.setStrokeColor(colors.HexColor(HAIR))
    c.line(x, yy, x + w, yy)
    return yy

def as_rows(d):
    r = rowsA[d]
    l50, l95 = (r['leak_50'] or 0) * 60, (r['leak_95'] or 0) * 60
    p50, p95 = r['mask_press_50'] or 0, r['mask_press_95'] or 0
    csr, dur = r['csr'] or 0, r['duration'] or 0
    return [('S/N / Mode', '22261252530 / AutoSet 4-20'),
            ('Usage', fmt_hm2(dur)),
            ('Leak med/95th', f'{l50:.1f} / {l95:.1f}'),
            ('Press med/95th', f'{p50:.1f} / {p95:.1f}'),
            ('AHI / AI / HI', f"{r['ahi']:.1f} / {r['ai']:.1f} / {r['hi']:.1f}"),
            ('CA / OA / UA', f"{r['cai']:.1f} / {r['oai']:.1f} / {r['uai']:.1f}"),
            ('CSR (min / %)', f"{int(csr)} ({csr / dur * 100:.1f}%)" if dur else '—')]

def as_stat_rows(d, sA):
    if d in rowsA:
        return as_rows(d)
    csr = int(sA.get('csr_m') or 0)
    return [('S/N / Mode', '22261252530 / AutoSet 4-20'),
            ('Usage', sA['usage']),
            ('Leak med/95th', f"{sA['l50']:.1f} / {sA['l95']:.1f}"),
            ('Press med/95th', f"{sA['p50']:.1f} / {sA['p95']:.1f}"),
            ('AHI / AI / HI', f"{sA['ahi']:.1f} / {sA['ai']:.1f} / {sA['hi']:.1f}"),
            ('CA / OA / UA', f"{sA['cai']:.1f} / {sA['oai']:.1f} / {sA['uai']:.1f}"),
            ('CSR (min)', f"{csr}")]

def am_rows(d):
    r = rowsM[d.strftime('%Y-%m-%d')]

    def gv(k):
        try:
            return float(r[k]) if r[k] not in ('', None) else 0.0
        except (ValueError, KeyError):
            return 0.0
    return [('S/N / Mode', '— / AutoSet 5-20'),
            ('Usage', fmt_hm2(gv('使用分'))),
            ('Leak med/95th', f"{gv('漏氣med'):.1f} / {gv('漏氣95'):.1f}"),
            ('Press med/95th', f"{gv('壓力med'):.1f} / {gv('壓力95'):.1f}"),
            ('AHI / AI / HI', f"{gv('AHI'):.1f} / {gv('AI'):.1f} / {gv('HI'):.1f}"),
            ('CA / OA / UA', f"{gv('CAI'):.1f} / {gv('OAI'):.1f} / {gv('UAI'):.1f}"),
            ('RERA（筆）', f"{int(gv('RERA'))}")]

def file3():
    NS = json.load(open(AV / 'night_stats.json', encoding='utf-8'))
    pages = sorted(NS.keys(), reverse=True)
    p3 = OUTDIR / '20261127 吳俊毅 202607-202611_3.pdf'
    c = canvas.Canvas(str(p3), pagesize=(595, 869))
    c.setTitle('Detailed Report - 吳俊毅')
    total = len(pages)
    for i, nd in enumerate(pages):
        d = dtm.datetime.strptime(nd, '%Y-%m-%d').date()
        S = NS[nd]
        title(c, 'Detailed report', y=720, fs=18)
        header(c, d.strftime('%d/%m/%Y'), date_y=783)
        hasA, hasM = bool(S.get('A')), bool(S.get('M'))
        if hasA and hasM:
            compact_table(c, 151, 700, 195, 'AirSense 10 AutoSet', as_stat_rows(d, S['A']))
            compact_table(c, 353, 700, 195, 'AirMini AutoSet（藍牙直連）', am_rows(d))
        elif hasA:
            r = rowsA.get(d)
            if r is None:
                sA = S['A']
                yy = airview_table(c, TX, 700, TW, 'AirSense 10 AutoSet with AirFit N20', '', [
                    ('Serial number', '22261252530'),
                    ('Mode / min - max pressure', 'AutoSet / 4.0 - 20.0 cmH2O'),
                    ('EPR / level', 'Off')])
                yy = airview_table(c, TX, yy - 13, TW, 'Statistics', '', [
                    ('Usage', sA['usage']),
                    ('Leak - L/min', ('Median:', round(sA['l50'], 1)), ('95th %:', round(sA['l95'], 1))),
                    ('Pressure - cmH2O', ('Median:', round(sA['p50'], 1)), ('95th %:', round(sA['p95'], 1))),
                    ('Events per hour', ('AI:', round(sA['ai'], 1)), ('HI:', round(sA['hi'], 1)), ('AHI:', round(sA['ahi'], 1))),
                    ('', ('Central:', round(sA['cai'], 1)), ('Obstructive:', round(sA['oai'], 1)), ('Unknown:', round(sA['uai'], 1))),
                    ('CSR (min)', '', '', str(int(sA.get('csr_m') or 0)))])
                footer(c)
                img = AV / f"detN_{nd.replace('-', '')}.png"
                if img.exists():
                    c.drawImage(str(img), 155, 58, width=406, height=501 * 406 / 533)
                c.showPage()
                continue
            l50, l95 = (r['leak_50'] or 0) * 60, (r['leak_95'] or 0) * 60
            p50, p95 = r['mask_press_50'] or 0, r['mask_press_95'] or 0
            csr, dur = r['csr'] or 0, r['duration'] or 0
            yy = airview_table(c, TX, 700, TW, 'AirSense 10 AutoSet with AirFit N20', '', [
                ('Serial number', '22261252530'),
                ('Mode / min - max pressure', 'AutoSet / 4.0 - 20.0 cmH2O'),
                ('EPR / level', 'Off')])
            yy = airview_table(c, TX, yy - 13, TW, 'Statistics', '', [
                ('Usage', fmt_hm2(dur)),
                ('Leak - L/min', ('Median:', round(l50, 1)), ('95th %:', round(l95, 1))),
                ('Pressure - cmH2O', ('Median:', round(p50, 1)), ('95th %:', round(p95, 1))),
                ('Events per hour', ('AI:', round(r['ai'] or 0, 1)), ('HI:', round(r['hi'] or 0, 1)), ('AHI:', round(r['ahi'] or 0, 1))),
                ('', ('Central:', round(r['cai'] or 0, 1)), ('Obstructive:', round(r['oai'] or 0, 1)), ('Unknown:', round(r['uai'] or 0, 1))),
                ('Cheyne-Stokes respiration (duration)', '', '', f'{int(csr)} min ({csr / dur * 100:.1f}%)' if dur else '—')])
        else:
            k = d.strftime('%Y-%m-%d')
            r = rowsM[k]

            def gv(kk):
                try:
                    return float(r[kk]) if r[kk] not in ('', None) else 0.0
                except (ValueError, KeyError):
                    return 0.0
            yy = airview_table(c, TX, 700, TW, 'AirMini AutoSet（藍牙直連資料）', '', [
                ('Serial number', '—'),
                ('Mode / min - max pressure', 'AutoSet / 5.0 - 20.0 cmH2O'),
                ('EPR / level', 'Ramp / 1')])
            yy = airview_table(c, TX, yy - 13, TW, 'Statistics', '', [
                ('Usage', fmt_hm2(gv('使用分'))),
                ('Leak - L/min', ('Median:', round(gv('漏氣med'), 1)), ('95th %:', round(gv('漏氣95'), 1))),
                ('Pressure - cmH2O', ('Median:', round(gv('壓力med'), 1)), ('95th %:', round(gv('壓力95'), 1))),
                ('Events per hour', ('AHI:', round(gv('AHI'), 1)), ('AI:', round(gv('AI'), 1)), ('HI:', round(gv('HI'), 1))),
                ('', ('Central:', round(gv('CAI'), 1)), ('Obstructive:', round(gv('OAI'), 1)), ('Unknown:', round(gv('UAI'), 1))),
                ('RERA（筆數）', '', '', f"{int(gv('RERA'))} 筆")])
        footer(c)
        img = AV / f"detN_{nd.replace('-', '')}.png"
        if img.exists():
            c.drawImage(str(img), 155, 58, width=406, height=501 * 406 / 533)
        else:
            c.setFont('TC', 9)
            c.setFillColor(colors.HexColor(GRAYTX))
            c.drawString(151, 300, '此夜僅有彙總統計值，原始波形未收錄於兩部裝置資料中。')
        c.showPage()
    c.save()
    print('檔案3 OK', p3.name, '頁數', total)

if __name__ == '__main__':
    file1()
    file2()
    file3()
