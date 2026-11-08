"""ResMed AirSense 10 SD 卡解析模組（stdlib only；驗證於 AirView 4.51 報告對照）

檔案結構：
  STR.edf                 每天 1 筆記錄，80 通道（使用時間/壓力/漏氣/AHI 摘要）
  DATALOG/<date>/xxx.edf  每晚 session：
    CSL  EDF+ 註記（session/mask 事件，多為空）
    EVE  EDF+ 註記（呼吸事件：Obstructive/Central/Unknown Apnea、Hypopnea…）
    PLD  2s 時序（MaskPress/Press/EprPress/Leak/RespRate/TidVol/MinVent/Snore/FlowLim）
    SAD  1s（Pulse/SpO2；無血氧模組時全 0）
    BRP  75Hz 流量波形（未解析）

已知非標準點（詳 file-format.md）：
  - header 數值欄為 ASCII；欄位逐欄排列；hdr_bytes = 256 + ns*256
  - Date 通道 = Unix 天數；-1（0xFFFF）= 缺值
  - STR MaskOn/MaskOff 為 12 小時制位差值：(值-720) mod 1440 = 機器時刻(分)
  - 註記通道每 int16 裝 2 字元（little-endian 原始位元組）
"""
import struct
from datetime import date, timedelta
from pathlib import Path


class EDF:
    """EDF 讀取：header + int16 記錄（raw 樣本與 physical 值並存）"""

    def __init__(self, path):
        self.path = Path(path)
        raw = self.path.read_bytes()
        self._bytes = raw

        def s(off, n):
            return raw[off:off + n].decode('ascii', 'replace').strip(' \x00')

        def num(off, n):
            try:
                return int(s(off, n))
            except ValueError:
                try:
                    return float(s(off, n))
                except ValueError:
                    return 0.0

        self.version = s(0, 8)
        self.patient = s(8, 80)
        self.recording = s(88, 80)
        self.startdate = s(168, 8)
        self.starttime = s(176, 8)
        self.header_bytes = num(184, 8)
        self.n_records = num(236, 8)
        self.record_dur = float(s(244, 8) or '0')
        self.ns = num(252, 4)
        if self.header_bytes != 256 + self.ns * 256:
            raise ValueError(f'header size {self.header_bytes} != 256+{self.ns}*256')

        # header 欄位逐欄排列
        off = 256
        n = self.ns
        self.labels = [s(off + i * 16, 16) for i in range(n)]; off += 16 * n
        off += 80 * n                                   # transducer（略）
        self.units = [s(off + i * 8, 8) for i in range(n)]; off += 8 * n
        phys_min = [float(s(off + i * 8, 8) or 0) for i in range(n)]; off += 8 * n
        phys_max = [float(s(off + i * 8, 8) or 0) for i in range(n)]; off += 8 * n
        dig_min = [float(s(off + i * 8, 8) or 0) for i in range(n)]; off += 8 * n
        dig_max = [float(s(off + i * 8, 8) or 0) for i in range(n)]; off += 8 * n
        off += 80 * n                                   # prefilter（略）
        self.sps = [int(num(off + i * 8, 8) or 0) for i in range(n)]
        self.minmax = list(zip(phys_min, phys_max, dig_min, dig_max))

        self.idx = {lab: i for i, lab in enumerate(self.labels)}
        self.total_sps = sum(self.sps)

        # 資料記錄
        self.rawsamples = []
        self.data = []
        for r in range(self.n_records):
            ro = self.header_bytes + r * self.total_sps * 2
            chunk = raw[ro:ro + self.total_sps * 2]
            if len(chunk) < self.total_sps * 2:
                break
            vals, raws, o = [], [], 0
            for i in range(n):
                cnt = self.sps[i]
                samples = struct.unpack(f'<{cnt}h', chunk[o:o + 2 * cnt])
                raws.append(samples)
                pmin, pmax, dmin, dmax = self.minmax[i]
                if dmax != dmin:
                    k = (pmax - pmin) / (dmax - dmin)
                    b = pmin - dmin * k
                    vals.append([v * k + b for v in samples])
                else:
                    vals.append([float(v) for v in samples])
                o += 2 * cnt
            self.rawsamples.append(raws)
            self.data.append(vals)

    def chan(self, label, rec=0):
        return self.data[rec][self.idx[label]]


def decode_annotations(edf):
    """EDF+ 註記 → [(record, onset秒str, duration|None, text)]

    機器非標 EDF+：字元 = 原始位元組串（每 int16 裝 2 字元, little-endian）。
    每記錄：先 +<timekeep>，之後成對 token：+onset[\x15dur] 與描述文字。
    注意 Hypopnea 的 \x15 後是子型（0/1），非持續秒數。
    """
    if 'EDF Annotations' not in edf.idx:
        return []
    chan = edf.idx['EDF Annotations']
    out = []
    for r in range(len(edf.data)):
        buf = b''.join(struct.pack('<h', v) for v in edf.rawsamples[r][chan])
        txt = buf.decode('latin-1')
        toks = [t.lstrip('\x00').strip('\x00 ') for t in txt.split('\x14')]
        toks = [t for t in toks if t]
        if toks and toks[0].startswith('+') and len(toks[0]) <= 32 and 'A' not in toks[0]:
            toks = toks[1:]          # 去掉 timekeeping（如 +0）
        i = 0
        while i < len(toks):
            t = toks[i]
            if t.startswith('+'):
                body = t[1:]
                dur = None
                digits = ''
                if '\x15' in body:
                    onset, _, tail = body.partition('\x15')
                    for ch in tail:
                        if ch.isdigit() or ch == '.':
                            digits += ch
                        else:
                            break
                    dur = digits or None
                    text = tail[len(digits):].strip('\x00 ')
                    onset = onset.strip('\x00 ')
                else:
                    for ch in body:
                        if ch.isdigit() or ch == '.':
                            digits += ch
                        else:
                            break
                    onset = digits
                    text = body[len(digits):].strip('\x00 ')
                if not text and i + 1 < len(toks) and not toks[i + 1].startswith('+'):
                    text = toks[i + 1].strip('\x00 ')
                    i += 1
                out.append((r, onset, dur, text))
            i += 1
    return out


# ---------------- STR.edf 每日摘要 ----------------

def _unix_to_date(days):
    return date(1970, 1, 1) + timedelta(days=int(days))


def _s(v):
    """physical 值；-1（浮點負值）視為缺值 → None"""
    if v is None:
        return None
    if v < 0:
        return None
    return round(v, 4)


def parse_str(path):
    """STR.edf → 每天一個 dict 的 list（percentile 通道、事件指標、mask 槽位）"""
    edf = EDF(path)
    rows = []
    for r in range(len(edf.data)):
        dvals = edf.chan('Date', r)
        day = _unix_to_date(dvals[0]) if dvals else None

        def first(lab):
            vals = edf.chan(lab, r)
            return _s(vals[0]) if vals else None

        rows.append(dict(
            date=day,
            mask_on=[round(v) for v in edf.chan('MaskOn', r)],
            mask_off=[round(v) for v in edf.chan('MaskOff', r)],
            mask_events=first('MaskEvents'),
            duration=first('Duration'),
            on_duration=first('OnDuration'),
            patient_hours=first('PatientHours'),
            mode=first('Mode'),
            mask_press_50=first('MaskPress.50'),
            mask_press_95=first('MaskPress.95'),
            mask_press_max=first('MaskPress.Max'),
            tgt_ipap_50=first('TgtIPAP.50'),
            tgt_ipap_95=first('TgtIPAP.95'),
            tgt_ipap_max=first('TgtIPAP.Max'),
            tgt_epap_50=first('TgtEPAP.50'),
            tgt_epap_95=first('TgtEPAP.95'),
            tgt_epap_max=first('TgtEPAP.Max'),
            leak_50=first('Leak.50'),
            leak_95=first('Leak.95'),
            leak_70=first('Leak.70'),
            leak_max=first('Leak.Max'),
            min_vent_50=first('MinVent.50'),
            resp_rate_50=first('RespRate.50'),
            tid_vol_50=first('TidVol.50'),
            spo2_50=first('SpO2.50'),
            spo2_95=first('SpO2.95'),
            spo2_max=first('SpO2.Max'),
            ahi=first('AHI'),
            hi=first('HI'),
            ai=first('AI'),
            oai=first('OAI'),
            cai=first('CAI'),
            uai=first('UAI'),
            csr=first('CSR'),
        ))
    return rows
