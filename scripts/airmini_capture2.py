#!/usr/bin/env python3
"""airmini_capture2.py — AirMini 正規路線即時串流擷取。

以 libairmini 的公開 API 實作，重點：
  * 有 master_pair_key.json → airmini_open_session(mpk)（免 PIN、免按機器按鈕）
  * 無 key → airmini_pair(PIN)（回應含 masterPairKey+sessionKey，立刻回寫 key 檔）
  * pair 與 reconnect 兩條路徑都會自行進入 SESSION_OPEN，不需要多呼 open_session
    （多呼回 rc=2 = 會話已開，非拒絕）
  * StartStream 一律用 airmini_start_stream()，params 由
    airmini_params_start_stream() 產生（含 sampleIntervalMs/reportIntervalMs）
  * 斷線自動重連

用法：
  python3 airmini_capture2.py --minutes 60
  python3 airmini_capture2.py --minutes 60 --sample 1000 --report 1000

**取樣率＝sampleIntervalMs**（通道名後綴 -100hz/-50hz 是來源訊號頻率標示，不是你會
拿到的解析度）：實測 10 → 回 intervalMs=10（真 100Hz，每報 5 樣）、40 → 25Hz、
1000 → 1Hz。整夜錄 8 小時 × 100Hz × 3 通道 ≈ 860 萬列，量大的話用 1000 降載；
報告節奏 reportIntervalMs 與取樣率無關。

輸出：pull/stream_data.jsonl（append，每行 {"recv":ms,"method":...,"data":...}）
"""
import ctypes
import json
import os
import re
import socket
import subprocess
import sys
import time
from ctypes import (CFUNCTYPE, POINTER, Structure, c_char_p, c_int, c_size_t,
                    c_ubyte, c_void_p, byref)

LIB = os.environ.get('AIRMINI_LIB',
                     '/path/to/libairmini.so')
ADDR = os.environ.get('AIRMINI_ADDR', '<BT_ADDR>')
KEYFILE = os.environ.get('AIRMINI_KEYFILE', 'pull/master_pair_key.json')
OUTFILE = os.environ.get('AIRMINI_OUT', 'pull/stream_data.jsonl')


def find_pin():
    """取得配對碼。

    配對碼是**機身貼紙上印刷的固定配對碼**，屬憑證——只從環境變數讀取，
    不要寫進版控。設定方式：
        export AIRMINI_PIN=1234
    """
    pin = os.environ.get('AIRMINI_PIN', '').strip()
    return pin or None


lib = ctypes.CDLL(LIB)
AIRMINI_OK = 0

RESP_CB = CFUNCTYPE(None, c_void_p, c_int, c_char_p, c_char_p)
NOTIFY_CB = CFUNCTYPE(None, c_void_p, c_char_p, c_char_p)
SEND_FN = CFUNCTYPE(c_int, c_void_p, POINTER(c_ubyte), c_size_t)


class Config(Structure):
    _fields_ = [('send', SEND_FN), ('send_user', c_void_p),
                ('crypto', c_void_p), ('log', c_void_p), ('log_user', c_void_p)]


lib.airmini_crypto_openssl.restype = c_void_p
lib.airmini_create.restype = c_void_p
lib.airmini_create.argtypes = [POINTER(Config)]
lib.airmini_pair.argtypes = [c_void_p, c_char_p, RESP_CB, c_void_p]
lib.airmini_open_session.argtypes = [c_void_p, c_char_p, RESP_CB, c_void_p]
lib.airmini_get_datetime.argtypes = [c_void_p, RESP_CB, c_void_p]
lib.airmini_start_stream.argtypes = [c_void_p, POINTER(c_char_p), c_size_t,
                                      c_int, c_int, RESP_CB, c_void_p]
lib.airmini_feed.argtypes = [c_void_p, POINTER(c_ubyte), c_size_t]
lib.airmini_set_notify.argtypes = [c_void_p, NOTIFY_CB, c_void_p]

# 型別字串一律用 protocol.h 的 AIRMINI_STREAM_* 常數值，勿自寫
STREAM_TYPES = (c_char_p * 3)(b'MaskPressure-100hz',
                              b'InspiratoryPressure-50hz',
                              b'Leak-TwoSecond')

S = {'sock': None, 'am': None, 'n': 0, 'stream': False, 'tag': '',
     'session_ok': False, 'stream_err': None, 'deadline': 0.0,
     'sample': 1000, 'report': 1000}


def log(msg):
    print(f'[{time.strftime("%H:%M:%S")}] {msg}', flush=True)


def out_jsonl(method, js):
    try:
        rec = {'recv': int(time.time() * 1000), 'method': method,
               'data': json.loads(js)}
    except Exception:
        rec = {'recv': int(time.time() * 1000), 'method': method, 'data': js}
    os.makedirs(os.path.dirname(OUTFILE) or '.', exist_ok=True)
    with open(OUTFILE, 'a') as fp:
        fp.write(json.dumps(rec, ensure_ascii=False) + '\n')
    S['n'] += 1
    if S['n'] % 200 == 0:
        log(f'已收 {S["n"]} 筆')


def SEND_CB(user, data, length):
    b = bytes(data[:length]) if length else b''
    try:
        S['sock'].sendall(b)
        return 0
    except Exception:
        return -1


send_ref = SEND_FN(SEND_CB)


def start_stream():
    """session 已開 → 直接開串流（interval 交給 libairmini 組 params）。"""
    S['tag'] = 'stream'
    r = lib.airmini_start_stream(S['am'], STREAM_TYPES, 3,
                                 S['sample'], S['report'], resp_ref, None)
    log(f'StartStream 送出 rc={r}'
        f'（sample={S["sample"]}ms report={S["report"]}ms）')


def on_result(user, st, result, error):
    result = result.decode(errors='ignore') if isinstance(result, bytes) else (result or '')
    error = error.decode(errors='ignore') if isinstance(error, bytes) else (error or '')
    tag = S['tag']
    if st != AIRMINI_OK:
        log(f'[err:{tag}] {error or "status " + str(st)}')
        if tag == 'stream':
            S['stream_err'] = error or f'status {st}'
        return
    log(f'[ok:{tag}] {result[:110]}')
    if tag == 'pair':
        m = re.search(r'"masterPairKey":"([0-9A-Fa-f]{64})"', result)
        if m:
            os.makedirs(os.path.dirname(KEYFILE) or '.', exist_ok=True)
            with open(KEYFILE, 'w') as fp:
                fp.write(m.group(1))
            log(f'金鑰已存檔 ✓（前12字 {m.group(1)[:12]}…）')
        # pair 內部已開會話 → 直接驗證 + 串流
        S['tag'] = 'datetime'
        lib.airmini_get_datetime(S['am'], resp_ref, None)
        start_stream()
    elif tag == 'session':
        S['session_ok'] = True
        S['tag'] = 'datetime'
        lib.airmini_get_datetime(S['am'], resp_ref, None)
        start_stream()
    elif tag == 'stream':
        S['stream'] = True
        log('*** 串流已啟動 ***')


resp_ref = RESP_CB(on_result)


def on_notify(user, method, js):
    m = (method or b'').decode(errors='ignore')
    j = (js or b'{}').decode(errors='ignore')
    if 'Stream' in m:
        out_jsonl(m, j)
    elif 'Heart' not in m:
        log(f'[notify] {m[:40]} ({len(j)}B)')


notify_ref = NOTIFY_CB(on_notify)


def bluetoothctl(*args, timeout=20):
    """呼叫 bluetoothctl（用於 BlueZ stale connection 復原）。"""
    try:
        r = subprocess.run(['bluetoothctl', *args], capture_output=True,
                           text=True, timeout=timeout)
        return (r.stdout + r.stderr).strip()
    except Exception as e:
        return f'bluetoothctl 失敗: {e}'


def bluez_recover():
    """復原本機 BlueZ 的 stale connection 狀態（實測有效）。

    徵狀：RFCOMM 一直 Errno 112 Host is down，但 bluetoothctl info 顯示
    Connected: no ＋ RSSI 正常（-70dBm）＝本機 BlueZ 還掛著前一次嘗試。
    處置：disconnect → 數秒 → connect（回到 Connected: yes）→ 直連才通。
    注意：Connected: yes 只是 ACL 層通了，RFCOMM 能否連仍要看 SPP 視窗。
    """
    log('BlueZ 復原：disconnect → connect')
    bluetoothctl('disconnect', ADDR)
    time.sleep(4)
    out = bluetoothctl('connect', ADDR)
    log(f'  bluetoothctl connect: {out.splitlines()[-1] if out else "(無輸出)"}')
    time.sleep(2)
    return out


def connect():
    s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM,
                      socket.BTPROTO_RFCOMM)
    s.settimeout(10)
    s.connect((ADDR, 5))
    # 短逾時要放在 connect() 之後（connect 需要完整窗口）
    s.settimeout(0.5)
    return s


def handshake():
    try:
        S['sock'] = connect()
    except OSError as e:
        log(f'連線失敗 {e}')
        # Host is down / EALREADY：先試 BlueZ 復原再重試一次
        if getattr(e, 'errno', None) in (112, 114):
            bluez_recover()
            try:
                S['sock'] = connect()
                log('  ✓ BlueZ 復原後直連成功')
            except OSError as e2:
                log(f'  復原後仍失敗: {e2}')
                return False
        else:
            return False
    log('RFCOMM 已連線 ✓')

    cfg = Config(send=send_ref, send_user=None,
                 crypto=lib.airmini_crypto_openssl(), log=None, log_user=None)
    S['am'] = lib.airmini_create(byref(cfg))
    if not S['am']:
        log('create 失敗')
        return False
    lib.airmini_set_notify(S['am'], notify_ref, None)

    mpk = None
    if os.path.exists(KEYFILE):
        mpk = open(KEYFILE).read().strip()
    if mpk and len(mpk) == 64:
        log(f'用金鑰直連（免 PIN、免按鈕）✓ 前12字 {mpk[:12]}…')
        S['tag'] = 'session'
        r = lib.airmini_open_session(S['am'], mpk.encode(), resp_ref, None)
        log(f'open_session 送出 rc={r}')
    else:
        pin = find_pin()
        if not pin:
            log('無金鑰且取不到 PIN，無法握手')
            return False
        log('送出 PIN 配對…')
        S['tag'] = 'pair'
        if lib.airmini_pair(S['am'], pin.encode(), resp_ref, None) != AIRMINI_OK:
            log('pair 送出失敗')
            return False

    t0 = time.time()
    while time.time() - t0 < 30:
        if S['stream']:
            return True
        if S['stream_err']:
            log(f'StartStream 被拒：{S["stream_err"]}')
            return False
        try:
            data = S['sock'].recv(4096)
            if not data:
                log('連線關閉')
                return False
            b = (c_ubyte * len(data)).from_buffer_copy(data)
            lib.airmini_feed(S['am'], b, len(data))
        except socket.timeout:
            continue
        except OSError as e:
            log(f'讀取結束 {e}')
            return False
    log('30 秒內未啟動串流')
    return False


def main():
    args = sys.argv[1:]
    mins = 60
    if '--minutes' in args:
        mins = int(args[args.index('--minutes') + 1])
    if '--sample' in args:
        S['sample'] = int(args[args.index('--sample') + 1])
    if '--report' in args:
        S['report'] = int(args[args.index('--report') + 1])
    S['deadline'] = time.time() + mins * 60

    attempt = 0
    while time.time() < S['deadline']:
        attempt += 1
        S.update({'stream': False, 'session_ok': False, 'stream_err': None,
                  'tag': ''})
        log(f'===== 第 {attempt} 次嘗試 =====')
        if not handshake():
            try:
                S['sock'].close()
            except Exception:
                pass
            if time.time() >= S['deadline']:
                break
            # 重試間隔必須 ≥60s：<30s 的狂轟會把介面塞死成 EHOSTDOWN/EBUSY
            # （實測教訓，見 references/airmini-access.md「連線穩定法」）。
            wait = 60 + (attempt % 3) * 15   # 60/75/90s 輪替，避免固定節奏
            log(f'{wait} 秒後重試')
            time.sleep(wait)
            continue

        last = time.time()
        while time.time() < S['deadline']:
            try:
                data = S['sock'].recv(4096)
                if not data:
                    log('機器關閉連線')
                    break
                last = time.time()
                b = (c_ubyte * len(data)).from_buffer_copy(data)
                lib.airmini_feed(S['am'], b, len(data))
            except socket.timeout:
                if time.time() - last > 180:
                    log('180 秒無資料，判定失效')
                    break
            except OSError as e:
                log(f'讀取結束 {e}')
                break
        try:
            S['sock'].close()
        except Exception:
            pass
        if time.time() >= S['deadline']:
            break
        log('重連中…')
        time.sleep(10)

    log(f'結束：StreamData 共 {S["n"]} 筆')


if __name__ == '__main__':
    main()
