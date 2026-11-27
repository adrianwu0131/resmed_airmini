#!/usr/bin/env python3
"""session_all2.py — 一次連線：拉全史 + 開 100Hz 串流 + **保持連線不關**

2026-11-27 的策略修正：先前「咬上就抓完然後 close()」——關閉連線很可能**正是**
裝置收回 SPP 槽位的原因（實測：12:59 咬上、13:00 關閉、之後 30 分鐘 100+ 次
重試全失敗）。這版改成**抓完不關**，並每 55 秒送一輪 RPC 保活（App 的做法），
讓槽位維持開啟。

用法：按一下機器的 Bluetooth 鈕（閃藍）後立刻跑這個，會：
  1. ch5 → open_session(masterPairKey)（免 PIN、免按鈕）
  2. GetLoggedData 全史（88 夜逐分鐘漏氣/壓力 + 呼吸事件 + therapy 時段）
  3. StartStream 100Hz（sampleIntervalMs=10, reportIntervalMs=50）
  4. 每 55 秒保活一輪 GetLoggedData/GetHistory/Get，**連線不關**

用法：
    python3 session_all2.py --hours 8
"""
import ctypes
import json
import os
import socket
import sys
import time
from ctypes import (CFUNCTYPE, POINTER, Structure, c_char_p, c_int, c_size_t,
                    c_ubyte, c_void_p, byref)

LIB = os.environ.get('AIRMINI_LIB', '/path/to/libairmini.so')
ADDR = os.environ.get('AIRMINI_ADDR', '<BT_ADDR>')
KEYFILE = os.environ.get('AIRMINI_KEYFILE', 'pull/master_pair_key.json')
OUTDIR = os.environ.get('AIRMINI_OUTDIR', 'pull')
CHANNEL = int(os.environ.get('AIRMINI_CHANNEL', '5'))
KEEPALIVE = float(os.environ.get('AIRMINI_KEEPALIVE', '55'))

lib = ctypes.CDLL(LIB)
lib.airmini_crypto_openssl.restype = c_void_p
lib.airmini_create.restype = c_void_p

RESP_CB = CFUNCTYPE(None, c_void_p, c_int, c_char_p, c_char_p)
NOTIFY_CB = CFUNCTYPE(None, c_void_p, c_char_p, c_char_p)
SEND_FN = CFUNCTYPE(c_int, c_void_p, POINTER(c_ubyte), c_size_t)


class Config(Structure):
    _fields_ = [('send', SEND_FN), ('send_user', c_void_p),
                ('crypto', c_void_p), ('log', c_void_p), ('log_user', c_void_p)]


lib.airmini_create.argtypes = [POINTER(Config)]
lib.airmini_open_session.argtypes = [c_void_p, c_char_p, RESP_CB, c_void_p]
lib.airmini_get_logged_data.argtypes = [c_void_p, POINTER(c_char_p), c_size_t,
                                         c_char_p, RESP_CB, c_void_p]
lib.airmini_start_stream.argtypes = [c_void_p, POINTER(c_char_p), c_size_t,
                                      c_int, c_int, RESP_CB, c_void_p]
lib.airmini_call.argtypes = [c_void_p, c_char_p, c_char_p, RESP_CB, c_void_p]
lib.airmini_feed.argtypes = [c_void_p, POINTER(c_ubyte), c_size_t]
lib.airmini_set_notify.argtypes = [c_void_p, NOTIFY_CB, c_void_p]

SIGNALS = [b'TherapyOneMinutePeriodic-Leak',
           b'TherapyOneMinutePeriodic-InspiratoryPressure',
           b'TherapyEvents-RespiratoryEvent',
           b'UsageEvents-TherapyStatusEvent']
STREAM_TYPES = (c_char_p * 3)(b'MaskPressure-100hz',
                              b'InspiratoryPressure-50hz',
                              b'Leak-TwoSecond')
EPOCH = '2008-01-01T00:00:00.000Z'

S = {'sock': None, 'am': None, 'lg': 0, 'st': 0, 'hb': 0, 'ka': 0,
     'tag': '', 'session': False, 'err': None}
OUT = None


def log(m):
    print(f'[{time.strftime("%H:%M:%S")}] {m}', flush=True)


def out_file(name, obj):
    global OUT
    if OUT is None:
        os.makedirs(OUTDIR, exist_ok=True)
        OUT = open(os.path.join(OUTDIR, name), 'a')
    OUT.write(json.dumps(obj, ensure_ascii=False) + '\n')
    OUT.flush()


def SEND_CB(user, data, length):
    try:
        S['sock'].sendall(bytes(data[:length]) if length else b'')
        return 0
    except Exception:
        return -1


send_ref = SEND_FN(SEND_CB)


def on_result(user, st, result, error):
    result = result.decode(errors='ignore') if isinstance(result, bytes) else (result or '')
    error = error.decode(errors='ignore') if isinstance(error, bytes) else (error or '')
    if st != 0:
        S['err'] = error or f'status {st}'
        return
    if S['tag'] in ('session', 'logged', 'stream'):
        log(f'[ok:{S["tag"]}] {result[:110]}')
    if S['tag'] == 'session':
        S['session'] = True


resp_ref = RESP_CB(on_result)


def on_notify(user, method, js):
    m = (method or b'').decode(errors='ignore')
    j = (js or b'{}').decode(errors='ignore')
    if m == 'LoggedData':
        try:
            out_file('logged_data.jsonl', json.loads(j))
            S['lg'] += 1
            if S['lg'] % 50 == 0:
                log(f'  LoggedData {S["lg"]} 筆')
        except Exception:
            pass
    elif 'Stream' in m:
        try:
            out_file('stream_data.jsonl',
                     {'recv': int(time.time() * 1000), 'method': m,
                      'data': json.loads(j)})
            S['st'] += 1
            if S['st'] % 200 == 0:
                log(f'  StreamData {S["st"]} 筆（heartbeat {S["hb"]}／保活 {S["ka"]}）')
        except Exception:
            pass
    elif 'Heart' in m:
        S['hb'] += 1


notify_ref = NOTIFY_CB(on_notify)


def keepalive():
    """App 的 60 秒保活輪詢（目的不是拿資料，是讓裝置知道對端還活著）。"""
    S['ka'] += 1
    S['tag'] = 'ka'
    sig = json.dumps(["TherapyOneMinutePeriodic-Leak"], separators=(',', ':'))
    lib.airmini_call(S['am'], b'GetLoggedData',
                     ('{"signals":' + sig + ',"settings":{"fromDateTime":'
                      '"2026-11-01T00:00:00.000Z"}}').encode(),
                     resp_ref, None)


def main():
    args = sys.argv[1:]
    hours = 8.0
    if '--hours' in args:
        hours = float(args[args.index('--hours') + 1])

    try:
        s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM,
                          socket.BTPROTO_RFCOMM)
        s.settimeout(20)
        s.connect((ADDR, CHANNEL))
        s.settimeout(0.5)
        S['sock'] = s
        log(f'RFCOMM ch{CHANNEL} 已連線 ✓')
    except OSError as e:
        log(f'連線失敗 {e}（視窗未開？）')
        return 2

    cfg = Config(send=send_ref, send_user=None,
                 crypto=lib.airmini_crypto_openssl(), log=None, log_user=None)
    S['am'] = lib.airmini_create(byref(cfg))
    if not S['am']:
        log('create 失敗')
        return 1
    lib.airmini_set_notify(S['am'], notify_ref, None)

    mpk = open(KEYFILE).read().strip()
    S['tag'] = 'session'
    lib.airmini_open_session(S['am'], mpk.encode(), resp_ref, None)

    t0 = time.time()
    while time.time() - t0 < 12 and not S['session']:
        try:
            d = s.recv(4096)
            if not d:
                break
            b = (c_ubyte * len(d)).from_buffer_copy(d)
            lib.airmini_feed(S['am'], b, len(d))
        except socket.timeout:
            continue
        except OSError as e:
            log(f'讀取結束 {e}')
            return 2
    if not S['session']:
        log('★ 沒換到 sessionKey → 金鑰失效，需重新配對（-11005 VerificationFailure）')
        return 3
    log('session ✓')

    sigs = (c_char_p * len(SIGNALS))(*SIGNALS)
    S['tag'] = 'logged'
    lib.airmini_get_logged_data(S['am'], sigs, len(SIGNALS),
                                EPOCH.encode(), resp_ref, None)
    time.sleep(1)
    S['tag'] = 'stream'
    lib.airmini_start_stream(S['am'], STREAM_TYPES, 3, 10, 50, resp_ref, None)

    deadline = time.time() + hours * 3600
    last = time.time()
    next_ka = time.time() + KEEPALIVE
    log(f'串流已啟動，保持連線 {hours} 小時（保活每 {KEEPALIVE}s）——**不要關**')
    while time.time() < deadline:
        try:
            d = s.recv(8192)
            if not d:
                log('機器關閉連線')
                break
            last = time.time()
            b = (c_ubyte * len(d)).from_buffer_copy(d)
            lib.airmini_feed(S['am'], b, len(d))
        except socket.timeout:
            now = time.time()
            if now - last > 180:
                log('180 秒無資料，判定失效')
                break
            if now >= next_ka:
                keepalive()
                next_ka = now + KEEPALIVE
        except OSError as e:
            log(f'讀取結束 {e}')
            break
        if S['err']:
            log(f'RPC 錯誤：{S["err"]}')
            break

    log(f'結束：LoggedData {S["lg"]} 筆、StreamData {S["st"]} 筆、'
        f'heartbeat {S["hb"]}、保活 {S["ka"]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
