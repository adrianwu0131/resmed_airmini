#!/usr/bin/env python3
"""ready_capture.py — 一次性完整抓取（可重複測試用）

單一程序、單一輪詢器、無槽位爭用。按一下 Bluetooth 鈕後自動做完：
  1. ch5 → open_session(masterPairKey)
  2. GetLoggedData 全史（追加寫入 pull/logged_data.jsonl）
  3. StartStream 100Hz（追加寫入 pull/stream_data.jsonl）
  4. 每 55 秒保活，連線不關

用法：
    python3 ready_capture.py --hours 1           # 串流跑 1 小時
    python3 ready_capture.py --tries 30          # 最多試 30 輪（60/90s 間隔）
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
WAITS = [60, 90]

# 單一輸出檔柄，用 O_APPEND 讓多筆寫入不互踩
FLOG = None
FSTR = None
S = {'sock': None, 'am': None, 'lg': 0, 'st': 0, 'hb': 0, 'ka': 0,
     'tag': '', 'session': False, 'err': None}


def log(m):
    print(f'[{time.strftime("%H:%M:%S")}] {m}', flush=True)


def _open(name):
    return open(os.path.join(OUTDIR, name), 'a')


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
    if S['tag'] == 'session':
        S['session'] = True


resp_ref = RESP_CB(on_result)


def on_notify(user, method, js):
    m = (method or b'').decode(errors='ignore')
    j = (js or b'{}').decode(errors='ignore')
    global FLOG, FSTR
    if m == 'LoggedData':
        try:
            if FLOG is None:
                FLOG = _open('logged_data.jsonl')
            FLOG.write(j + '\n')
            FLOG.flush()
            S['lg'] += 1
            if S['lg'] % 100 == 0:
                log(f'  LoggedData {S["lg"]} 筆')
        except Exception:
            pass
    elif 'Stream' in m:
        try:
            if FSTR is None:
                FSTR = _open('stream_data.jsonl')
            FSTR.write(json.dumps({'recv': int(time.time() * 1000),
                                   'method': m, 'data': json.loads(j)},
                                  ensure_ascii=False) + '\n')
            FSTR.flush()
            S['st'] += 1
            if S['st'] % 200 == 0:
                log(f'  StreamData {S["st"]} 筆（hb {S["hb"]}/ka {S["ka"]}）')
        except Exception:
            pass
    elif 'Heart' in m:
        S['hb'] += 1


notify_ref = NOTIFY_CB(on_notify)


def keepalive():
    S['ka'] += 1
    S['tag'] = 'ka'
    sig = json.dumps(["TherapyOneMinutePeriodic-Leak"], separators=(',', ':'))
    lib.airmini_call(S['am'], b'GetLoggedData',
                     ('{"signals":' + sig + ',"settings":{"fromDateTime":'
                      '"2026-11-01T00:00:00.000Z"}}').encode(),
                     resp_ref, None)


def run_once(stream_hours):
    """一次完整抓取；回傳 True 表示有咬上並進入串流。"""
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
        return False

    cfg = Config(send=send_ref, send_user=None,
                 crypto=lib.airmini_crypto_openssl(), log=None, log_user=None)
    S['am'] = lib.airmini_create(byref(cfg))
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
            return True  # 連上也被 reset，仍算「咬上」

    if not S['session']:
        log('★ 沒 sessionKey → 金鑰失效，重新配對')
        return False

    log('session ✓')
    sigs = (c_char_p * len(SIGNALS))(*SIGNALS)
    S['tag'] = 'logged'
    lib.airmini_get_logged_data(S['am'], sigs, len(SIGNALS),
                                EPOCH.encode(), resp_ref, None)
    time.sleep(1)
    S['tag'] = 'stream'
    lib.airmini_start_stream(S['am'], STREAM_TYPES, 3, 10, 50, resp_ref, None)

    deadline = time.time() + stream_hours * 3600
    last = time.time()
    next_ka = time.time() + KEEPALIVE
    log(f'保持連線 {stream_hours} 小時（保活每 {KEEPALIVE}s）')
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
                log('180 秒無資料')
                break
            if now >= next_ka:
                keepalive()
                next_ka = now + KEEPALIVE
        except OSError as e:
            log(f'讀取結束 {e}')
            break
        if S['err']:
            log(f'RPC 錯：{S["err"]}')
            break
    log(f'結束：LoggedData {S["lg"]} 筆、StreamData {S["st"]} 筆')
    return True


def main():
    args = sys.argv[1:]
    hours = 1.0
    if '--hours' in args:
        hours = float(args[args.index('--hours') + 1])
    tries = 30
    if '--tries' in args:
        tries = int(args[args.index('--tries') + 1])

    log(f'待機獵手啟動：等視窗開（60/90s 輪替，最多 {tries} 輪）、'
        f'咬上後串流 {hours} 小時')
    ok = 0
    for i in range(tries):
        S.update({'lg': 0, 'st': 0, 'hb': 0, 'ka': 0, 'session': False,
                  'err': None})
        if run_once(hours):
            ok += 1
            break
        w = WAITS[i % len(WAITS)]
        log(f'→ 等 {w}s 再試（{i + 1}/{tries}）')
        time.sleep(w)
    log(f'完成（咬上 {ok} 次）')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
