#!/usr/bin/env python3
"""pull_history.py — AirMini 全史 GetLoggedData（金鑰直連，免 PIN）

2026-11-27 新增：C 驅動 airmini_pull.c 走的是「每次重新配對」的路線，
但金鑰直連才不會輪換金鑰、且能在視窗開著時一次拉完。

流程：ch5 → open_session(masterPairKey) → GetLoggedData（多訊號）
→ pull/logged_data.jsonl（JSONL，每行一個 JSON-RPC 物件）

**視窗是「用完即收」**：一次連線內做完拉取＋開串流，斷線後立刻再連會得到
`Host is down`——要把多件事塞進同一個 session。

用法：
    python3 pull_history.py                 # 全史
    python3 pull_history.py --days 30       # 只拉近 30 天
"""
import ctypes
import json
import os
import socket
import sys
import time
from ctypes import (CFUNCTYPE, POINTER, Structure, c_char_p, c_int, c_size_t,
                    c_ubyte, c_void_p, byref)
from datetime import datetime, timedelta, timezone

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
lib.airmini_get_datetime.argtypes = [c_void_p, RESP_CB, c_void_p]
lib.airmini_feed.argtypes = [c_void_p, POINTER(c_ubyte), c_size_t]
lib.airmini_set_notify.argtypes = [c_void_p, NOTIFY_CB, c_void_p]

# 訊號名取自 protocol.h 的 AIRMINI_SIG_* 常數
SIGNALS = [
    b'TherapyOneMinutePeriodic-Leak',
    b'TherapyOneMinutePeriodic-InspiratoryPressure',
    b'TherapyEvents-RespiratoryEvent',
    b'UsageEvents-TherapyStatusEvent',
    b'Diagnostic25HzPeriodic-BlowerFlow',
]
EPOCH = '2008-01-01T00:00:00.000Z'

S = {'sock': None, 'am': None, 'n': 0, 'tag': '', 'session': False,
     'err': None}
OUT = None


def log(m):
    print(f'[{time.strftime("%H:%M:%S")}] {m}', flush=True)


def SEND_CB(user, data, length):
    try:
        S['sock'].sendall(bytes(data[:length]) if length else b'')
        return 0
    except Exception:
        return -1


send_ref = SEND_FN(SEND_CB)


def out_file(obj):
    global OUT
    if OUT is None:
        os.makedirs(OUTDIR, exist_ok=True)
        OUT = open(os.path.join(OUTDIR, 'logged_data.jsonl'), 'a')
    OUT.write(json.dumps(obj, ensure_ascii=False) + '\n')
    OUT.flush()


def on_result(user, st, result, error):
    result = result.decode(errors='ignore') if isinstance(result, bytes) else (result or '')
    error = error.decode(errors='ignore') if isinstance(error, bytes) else (error or '')
    if st != 0:
        S['err'] = error or f'status {st}'
        log(f'[err:{S["tag"]}] {S["err"][:200]}')
        return
    log(f'[ok:{S["tag"]}] {result[:140]}')
    if S['tag'] == 'session':
        S['session'] = True


resp_ref = RESP_CB(on_result)


def on_notify(user, method, js):
    m = (method or b'').decode(errors='ignore')
    j = (js or b'{}').decode(errors='ignore')
    if m == 'LoggedData':
        try:
            out_file(json.loads(j))
            S['n'] += 1
            if S['n'] % 50 == 0:
                log(f'  已收 {S["n"]} 筆 LoggedData')
        except Exception as e:
            log(f'  notify 解析失敗 {e}')
    elif 'Heart' not in m:
        log(f'[notify] {m[:40]} ({len(j)}B)')


notify_ref = NOTIFY_CB(on_notify)


def main():
    args = sys.argv[1:]
    days = None
    if '--days' in args:
        days = int(args[args.index('--days') + 1])

    mpk = open(KEYFILE).read().strip()
    if len(mpk) != 64:
        print('金鑰檔長度不對')
        return 1

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

    S['tag'] = 'session'
    r = lib.airmini_open_session(S['am'], mpk.encode(), resp_ref, None)
    log(f'open_session 送出 rc={r}')

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
        return 3

    log('session 已開 ✓ 開始拉全史')
    from_iso = EPOCH
    if days:
        from_iso = (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
            '%Y-%m-%dT%H:%M:%S.000Z')

    # 每批最多 6 個訊號（AIRMINI_MAX_SIGNALS_PER_REQUEST）
    sigs = (c_char_p * len(SIGNALS))(*SIGNALS)
    S['tag'] = 'logged'
    r = lib.airmini_get_logged_data(S['am'], sigs, len(SIGNALS),
                                    from_iso.encode(), resp_ref, None)
    log(f'GetLoggedData 送出 rc={r}（{len(SIGNALS)} 訊號，自 {from_iso}）')

    t0 = time.time()
    last = time.time()
    while time.time() - t0 < 600:
        try:
            d = s.recv(8192)
            if not d:
                log('機器關閉連線')
                break
            last = time.time()
            b = (c_ubyte * len(d)).from_buffer_copy(d)
            lib.airmini_feed(S['am'], b, len(d))
        except socket.timeout:
            if time.time() - last > 60:
                log('60 秒無資料，結束')
                break
        except OSError as e:
            log(f'讀取結束 {e}')
            break
        if S['err']:
            break

    if OUT:
        OUT.close()
    log(f'完成：LoggedData {S["n"]} 筆 → {OUTDIR}/logged_data.jsonl')
    return 0


if __name__ == '__main__':
    sys.exit(main())
