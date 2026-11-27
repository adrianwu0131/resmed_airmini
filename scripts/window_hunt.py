#!/usr/bin/env python3
"""window_hunt.py — 等 AirMini 開 SPP 視窗，咬上就一次做完

**為什麼要有這支**：AirMini 的 SPP 槽位在待機時會隨機開關。實測 40 次輪詢中
只有 2 次連得上；而 11/27 一整晚密集輪詢 100+ 次全部失敗——那不是機器不開窗，
是**輪詢太密把自己搞死了**。

**重試間隔的鐵則（2026-11-07/11-27 兩次實測）**：
  - <30 秒的密集重試會把介面塞死成 EHOSTDOWN/EBUSY，**必須 60~90 秒冷卻再單發**。
  - 間隔長不代表咬不到——但間隔短一定咬不到（而且可能讓狀態更糟）。
  - 本腳本用 **60/75/90 秒輪替**，避免固定節奏。

**一次連線做完所有事**：視窗是「用完即收」——連線一斷（即使我方主動關），
機器立刻收回槽位。所以 GetLoggedData 全史與 StartStream 必須在**同一條 session**
裡一起送，不要分兩次連。

用法：
    python3 window_hunt.py                      # 一次做完（全史 + 串流）
    python3 window_hunt.py --minutes 60         # 串流要跑多久
"""
import argparse
import json
import os
import socket
import subprocess
import sys
import time
from ctypes import (CFUNCTYPE, POINTER, Structure, c_char_p, c_int, c_size_t,
                    c_ubyte, c_void_p, byref)

LIB = os.environ.get('AIRMINI_LIB', '/path/to/libairmini.so')
ADDR = os.environ.get('AIRMINI_ADDR', '<BT_ADDR>')
KEYFILE = os.environ.get('AIRMINI_KEYFILE', 'pull/master_pair_key.json')
OUTDIR = os.environ.get('AIRMINI_OUTDIR', 'pull')
CHANNEL = int(os.environ.get('AIRMINI_CHANNEL', '5'))

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

# 60/75/90 輪替；<30s 會塞死介面（實測）
WAITS = [60, 75, 90]

S = {'sock': None, 'am': None, 'n': 0, 'lg': 0, 'st': 0, 'tag': '',
     'session': False, 'err': None}
OUT = None


def log(m):
    print(f'[{time.strftime("%H:%M:%S")}] {m}', flush=True)


def out_jsonl(method, js):
    global OUT
    if OUT is None:
        os.makedirs(OUTDIR, exist_ok=True)
        OUT = open(os.path.join(OUTDIR, 'stream_data.jsonl'), 'a')
    try:
        rec = {'recv': int(time.time() * 1000), 'method': method,
               'data': json.loads(js)}
    except Exception:
        rec = {'recv': int(time.time() * 1000), 'method': method, 'data': js}
    OUT.write(json.dumps(rec, ensure_ascii=False) + '\n')
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
        log(f'[err:{S["tag"]}] {S["err"][:180]}')
        return
    log(f'[ok:{S["tag"]}] {result[:120]}')
    if S['tag'] == 'session':
        S['session'] = True


resp_ref = RESP_CB(on_result)


def on_notify(user, method, js):
    m = (method or b'').decode(errors='ignore')
    j = (js or b'{}').decode(errors='ignore')
    if m == 'LoggedData':
        try:
            global OUT
            if OUT is None:
                os.makedirs(OUTDIR, exist_ok=True)
                OUT = open(os.path.join(OUTDIR, 'logged_data.jsonl'), 'a')
            OUT.write(j + '\n')
            OUT.flush()
            S['lg'] += 1
            if S['lg'] % 50 == 0:
                log(f'  LoggedData {S["lg"]} 筆')
        except Exception as e:
            log(f'  {e}')
    elif 'Stream' in m:
        out_jsonl(m, j)
        S['st'] += 1
        if S['st'] % 200 == 0:
            log(f'  StreamData {S["st"]} 筆')


notify_ref = NOTIFY_CB(on_notify)


def one_shot(stream_minutes):
    """一次連線：拉全史 + 開串流。回傳 (logged 筆數, stream 筆數) 或 None。"""
    try:
        s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM,
                          socket.BTPROTO_RFCOMM)
        s.settimeout(20)
        s.connect((ADDR, CHANNEL))
        s.settimeout(0.5)
        S['sock'] = s
        log(f'RFCOMM ch{CHANNEL} 已連線 ✓')
    except OSError as e:
        log(f'連線失敗 {e}')
        return None

    cfg = Config(send=send_ref, send_user=None,
                 crypto=lib.airmini_crypto_openssl(), log=None, log_user=None)
    S['am'] = lib.airmini_create(byref(cfg))
    if not S['am']:
        log('create 失敗')
        return None
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
            break
    if not S['session']:
        log('★ 沒換到 sessionKey → 金鑰失效，需重新配對（-11005 VerificationFailure）')
        try:
            s.close()
        except Exception:
            pass
        return None
    log('session ✓ 拉全史 + 開串流')

    sigs = (c_char_p * len(SIGNALS))(*SIGNALS)
    S['tag'] = 'logged'
    lib.airmini_get_logged_data(S['am'], sigs, len(SIGNALS),
                                EPOCH.encode(), resp_ref, None)
    time.sleep(1)
    S['tag'] = 'stream'
    lib.airmini_start_stream(S['am'], STREAM_TYPES, 3, 10, 50, resp_ref, None)

    deadline = time.time() + stream_minutes * 60
    last = time.time()
    while time.time() < deadline:
        try:
            d = s.recv(8192)
            if not d:
                log('連線關閉')
                break
            last = time.time()
            b = (c_ubyte * len(d)).from_buffer_copy(d)
            lib.airmini_feed(S['am'], b, len(d))
        except socket.timeout:
            if time.time() - last > 120:
                log('120 秒無資料，結束')
                break
        except OSError as e:
            log(f'讀取結束 {e}')
            break
    try:
        s.close()
    except Exception:
        pass
    return (S['lg'], S['st'])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--minutes', type=float, default=1,
                    help='咬上後串流要跑幾分鐘')
    ap.add_argument('--rounds', type=int, default=60,
                    help='最多嘗試幾輪')
    args = ap.parse_args()

    log(f'獵手啟動：ch{CHANNEL}、間隔 60/75/90s、最多 {args.rounds} 輪、'
        f'咬上後串流 {args.minutes} 分鐘')
    for i in range(args.rounds):
        S.update({'lg': 0, 'st': 0, 'session': False, 'err': None})
        res = one_shot(args.minutes)
        if res:
            log(f'★ 完成：LoggedData {res[0]} 筆、StreamData {res[1]} 筆')
            return 0
        w = WAITS[i % len(WAITS)]
        log(f'→ 等 {w}s 再試（<30s 密集重試會塞死介面）')
        time.sleep(w)
    log('輪次用完')
    return 1


if __name__ == '__main__':
    sys.exit(main())
