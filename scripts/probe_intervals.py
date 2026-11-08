#!/usr/bin/env python3
"""probe_intervals.py — 探測 AirMini 接受的 sampleIntervalMs／reportIntervalMs

用途：一次連線內依序試多組 interval，確認裝置實際接受哪些取樣率。
（對應 references/airmini-access.md「sampleIntervalMs 就是實際取樣間隔」）

實測結論（2026-11-07 真機）：
  sample=10  → 回 intervalMs=10（真 100Hz，每報 5 樣）、dataIds 三型 valid:true
  sample=40  → 回 intervalMs=40（＝25Hz，不是 100Hz）
  sample=1000→ 1 樣/秒/型
  **通道名後綴 -100hz/-50hz 是來源訊號頻率標示，不是你會拿到的解析度。**

用法：
  python3 probe_intervals.py                 # 試 10/20/40/100
  python3 probe_intervals.py 10 1000         # 指定要試的值
"""
import ctypes
import json
import os
import re
import socket
import sys
import time
from ctypes import (CFUNCTYPE, POINTER, Structure, c_char_p, c_int, c_size_t,
                    c_ubyte, c_void_p)

LIB = os.environ.get('AIRMINI_LIB',
                     '/path/to/libairmini.so')
ADDR = os.environ.get('AIRMINI_ADDR', '<BT_ADDR>')
KEYFILE = os.environ.get('AIRMINI_KEYFILE', 'pull/master_pair_key.json')

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
lib.airmini_open_session.argtypes = [c_void_p, c_char_p, RESP_CB, c_void_p]
lib.airmini_start_stream.argtypes = [c_void_p, POINTER(c_char_p), c_size_t, c_int,
                                      c_int, RESP_CB, c_void_p]
lib.airmini_feed.argtypes = [c_void_p, POINTER(c_ubyte), c_size_t]
lib.airmini_set_notify.argtypes = [c_void_p, NOTIFY_CB, c_void_p]

# 型別字串取 protocol.h 的 AIRMINI_STREAM_* 常數值，勿自寫
STREAM_TYPES = (c_char_p * 3)(b'MaskPressure-100hz',
                              b'InspiratoryPressure-50hz',
                              b'Leak-TwoSecond')

S = {'sock': None, 'am': None, 'tag': '', 'err': None,
     'ivs': set(), 'ns': set(), 'n': 0}
INTERVALS = []


def send_cb(user, data, length):
    try:
        S['sock'].sendall(bytes(data[:length]) if length else b'')
        return 0
    except Exception:
        return -1


send_ref = SEND_FN(send_cb)


def on_result(user, st, result, error):
    result = result.decode(errors='ignore') if isinstance(result, bytes) else (result or '')
    error = error.decode(errors='ignore') if isinstance(error, bytes) else (error or '')
    tag = S['tag']
    if st != AIRMINI_OK:
        S['err'] = error or f'status {st}'
        print(f'  [err:{tag}] {S["err"]}', flush=True)
        return
    print(f'  [ok:{tag}] {result[:150]}', flush=True)
    if tag == 'session':
        probe_next()


resp_ref = RESP_CB(on_result)


def on_notify(user, method, js):
    m = (method or b'').decode(errors='ignore')
    if 'StreamData' not in m:
        return
    try:
        p = json.loads((js or b'{}').decode()).get('params', {})
    except Exception:
        return
    S['ivs'].add(p.get('intervalMs'))
    for d in p.get('data', []):
        for vals in d.values():
            S['ns'].add(len(vals))
    S['n'] += 1


notify_ref = NOTIFY_CB(on_notify)


def probe_next():
    """依序試下一組 interval；reportInterval = sample×5（每報 5 樣）。"""
    if not INTERVALS:
        return
    iv = INTERVALS.pop(0)
    S['tag'] = 'probe'
    S['err'] = None
    r = lib.airmini_start_stream(S['am'], STREAM_TYPES, 3, iv, iv * 5,
                                 resp_ref, None)
    print(f'→ 試 sampleIntervalMs={iv}（report={iv * 5}ms）rc={r}', flush=True)


def main():
    global INTERVALS
    INTERVALS = [int(x) for x in sys.argv[1:]] or [10, 20, 40, 100]

    if not os.path.exists(KEYFILE):
        print(f'找不到金鑰檔 {KEYFILE}——先跑一次配對取得')
        return 1
    mpk = open(KEYFILE).read().strip()
    if len(mpk) != 64:
        print('金鑰檔長度不對（應為 64 hex）')
        return 1

    try:
        s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM,
                          socket.BTPROTO_RFCOMM)
        s.settimeout(10)
        s.connect((ADDR, 5))
        s.settimeout(0.3)
        S['sock'] = s
        print('RFCOMM 已連線 ✓')
    except OSError as e:
        print(f'連線失敗 {e}')
        return 1

    cfg = Config(send=send_ref, send_user=None,
                 crypto=lib.airmini_crypto_openssl(), log=None, log_user=None)
    S['am'] = lib.airmini_create(byref(cfg))
    if not S['am']:
        print('create 失敗')
        return 1
    lib.airmini_set_notify(S['am'], notify_ref, None)

    S['tag'] = 'session'
    r = lib.airmini_open_session(S['am'], mpk.encode(), resp_ref, None)
    print(f'open_session 送出 rc={r}（rc=0 已送出；rc=2＝會話早已開，非拒絕）')

    t0 = time.time()
    while time.time() - t0 < 40:
        try:
            d = s.recv(4096)
            if not d:
                break
            b = (c_ubyte * len(d)).from_buffer_copy(d)
            lib.airmini_feed(S['am'], b, len(d))
        except socket.timeout:
            pass
        except OSError as e:
            print(f'讀取結束 {e}')
            break
        if not INTERVALS and S['n'] > 50:
            break

    print(f'\n結果：收到 {S["n"]} 筆；實際 intervalMs={sorted(x for x in S["ivs"] if x)} '
          f'每報樣本數={sorted(S["ns"])}')
    print('（通道名後綴不是解析度；sampleIntervalMs 才是）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
