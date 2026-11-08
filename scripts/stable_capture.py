#!/usr/bin/env python3
"""stable_capture.py — AirMini 穩定串流抓取（依手機 App 的真實行為重建）

2026-11-07 反編譯對照 App 日誌（airmini_log_*.txt）得到的穩定握手與保活模式，
以及今晚反覆斷線的根因與對策。

**App 的真實序列**（App 日誌 2026-10-13 23:17:43 起）：
    OUT 1 StartStream   → TIMEOUT（App 故意先探，session 還沒開）
    OUT 2 GetVersion    → Success
    OUT 3 GetSessionKey → Success      ← 開 session 在這裡
    OUT 4 SubscribeEvent → Success
    OUT 5..8 Get / GetDateTime / Get
    OUT 9 StartStream   → Success      ← 第二次才真的開串流
    然後每 60 秒：GetLoggedData → GetHistory → Get

**App 的保活關鍵**：每 60 秒主動送一輪 RPC（Heartbeat 是裝置→主機方向）。
我們先前只收不送 → 閒置時被裝置視為無效連線而關閉。

**今晚斷線的三個根因（皆已實測）**：
  1. **沒有訂閱事件**：App 開 session 後立刻 SubscribeEvent，我們沒有。
  2. **沒有主動輪詢**：App 每 60 秒送 GetLoggedData/GetHistory/Get，我們沒有。
  3. **StartStream 送太早**：session 未開就送，App 這時也會 TIMEOUT。

用法：
    python3 stable_capture.py --minutes 60 --sample 10
    AIRMINI_LIB=... AIRMINI_ADDR=... python3 stable_capture.py
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
OUTFILE = os.environ.get('AIRMINI_OUT', 'pull/stream_data.jsonl')
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
lib.airmini_get_datetime.argtypes = [c_void_p, RESP_CB, c_void_p]
lib.airmini_get_version.argtypes = [c_void_p, RESP_CB, c_void_p]
lib.airmini_start_stream.argtypes = [c_void_p, POINTER(c_char_p), c_size_t,
                                      c_int, c_int, RESP_CB, c_void_p]
lib.airmini_call.argtypes = [c_void_p, c_char_p, c_char_p, RESP_CB, c_void_p]
lib.airmini_feed.argtypes = [c_void_p, POINTER(c_ubyte), c_size_t]
lib.airmini_set_notify.argtypes = [c_void_p, NOTIFY_CB, c_void_p]

STREAM_TYPES = (c_char_p * 3)(b'MaskPressure-100hz',
                              b'InspiratoryPressure-50hz',
                              b'Leak-TwoSecond')

# App 每 60 秒送一輪；比 Heartbeat（60s）稍快，確保不會錯過一個週期
KEEPALIVE_PERIOD = float(os.environ.get('AIRMINI_KEEPALIVE', '55'))

S = {'sock': None, 'am': None, 'n': 0, 'tag': '', 'stream': False,
     'sample': 10, 'report': 50, 'deadline': 0.0, 'hb': 0, 'ka': 0,
     'events': 0}


def log(m):
    print(f'[{time.strftime("%H:%M:%S")}] {m}', flush=True)


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
    if S['n'] % 500 == 0:
        log(f'已收 {S["n"]} 筆（heartbeat {S["hb"]}／保活 {S["ka"]}）')


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
    tag = S['tag']
    if st != 0:
        log(f'[err:{tag}] {error or "status " + str(st)}')
        return
    log(f'[ok:{tag}] {result[:100]}')
    if tag == 'stream':
        S['stream'] = True


resp_ref = RESP_CB(on_result)


def on_notify(user, method, js):
    m = (method or b'').decode(errors='ignore')
    j = (js or b'{}').decode(errors='ignore')
    if 'Stream' in m:
        out_jsonl(m, j)
    elif 'HeartBeat' in m or 'Heartbeat' in m:
        S['hb'] += 1
    elif 'Event' in m or 'State' in m:
        S['events'] += 1
        log(f'[notify] {m[:50]} ({len(j)}B)')


notify_ref = NOTIFY_CB(on_notify)


def send_rpc(method, params=None):
    """送一個 RPC（用 raw airmini_call；params 由呼叫端給）。"""
    lib.airmini_call(S['am'], method.encode(),
                     (params or '').encode(), resp_ref, None)


def subscribe_events():
    """App 在開 session 後立刻 SubscribeEvent（4 個事件）。

    事件名取自 protocol.h 的常數值；App 日誌顯示參數是 4 個事件的陣列。
    這一步很可能是「不被裝置閒置踢線」的關鍵。
    """
    ev = json.dumps(["FGState", "SystemError", "RecoverableError",
                     "TherapyState"], separators=(',', ':'))
    S['tag'] = 'subscribe'
    lib.airmini_call(S['am'], b'SubscribeEvent', ev.encode(), resp_ref, None)


def keepalive():
    """App 的 60 秒保活輪詢：GetLoggedData → GetHistory → Get。

    GetLoggedData 的訊號取自 protocol.h 的 AIRMINI_SIG_* 常數；
    目的不是拿資料，而是讓裝置知道對端還活著。
    """
    S['ka'] += 1
    sig = json.dumps(["TherapyOneMinutePeriodic-Leak"], separators=(',', ':'))
    S['tag'] = 'ka'
    lib.airmini_call(S['am'], b'GetLoggedData',
                     ('{"signals":' + sig + ',"settings":{"fromDateTime":'
                      '"2026-11-01T00:00:00.000Z"}}').encode(),
                     resp_ref, None)


def handshake(sock):
    """依 App 的順序重建握手。"""
    S['sock'] = sock
    cfg = Config(send=send_ref, send_user=None,
                 crypto=lib.airmini_crypto_openssl(), log=None, log_user=None)
    S['am'] = lib.airmini_create(byref(cfg))
    if not S['am']:
        log('create 失敗')
        return False
    lib.airmini_set_notify(S['am'], notify_ref, None)

    mpk = open(KEYFILE).read().strip()
    log(f'金鑰直連（免 PIN）前12字 {mpk[:12]}…')
    S['tag'] = 'session'
    r = lib.airmini_open_session(S['am'], mpk.encode(), resp_ref, None)
    log(f'open_session 送出 rc={r}')

    # App 順序：GetSessionKey → SubscribeEvent → Get → StartStream
    time.sleep(1.0)
    subscribe_events()
    time.sleep(0.5)
    S['tag'] = 'version'
    lib.airmini_get_version(S['am'], resp_ref, None)
    time.sleep(0.5)
    S['tag'] = 'datetime'
    lib.airmini_get_datetime(S['am'], resp_ref, None)
    time.sleep(0.5)
    S['tag'] = 'stream'
    r = lib.airmini_start_stream(S['am'], STREAM_TYPES, 3,
                                 S['sample'], S['report'], resp_ref, None)
    log(f'StartStream 送出 rc={r}（sample={S["sample"]}ms report={S["report"]}ms）')

    t0 = time.time()
    while time.time() - t0 < 25:
        if S['stream']:
            return True
        try:
            data = sock.recv(4096)
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
    log('25 秒內未啟動串流')
    return False


def main():
    global S
    args = sys.argv[1:]
    mins = 60
    if '--minutes' in args:
        mins = int(args[args.index('--minutes') + 1])
    if '--sample' in args:
        S['sample'] = int(args[args.index('--sample') + 1])
    if '--report' in args:
        S['report'] = int(args[args.index('--report') + 1])
    else:
        # reportIntervalMs 必須 = sampleIntervalMs × 5（每報 5 樣）
        S['report'] = S['sample'] * 5
    if '--channel' in args:
        globals()['CHANNEL'] = int(args[args.index('--channel') + 1])
    S['deadline'] = time.time() + mins * 60

    log(f'穩定模式：ch{CHANNEL}、{S["sample"]}ms、每報 {S["report"]}ms、'
        f'保活 {KEEPALIVE_PERIOD}s、共 {mins} 分鐘')

    attempt = 0
    while time.time() < S['deadline']:
        attempt += 1
        S.update({'stream': False, 'tag': ''})
        log(f'===== 第 {attempt} 次嘗試 =====')
        try:
            sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM,
                                 socket.BTPROTO_RFCOMM)
            sock.settimeout(20)
            sock.connect((ADDR, CHANNEL))
            sock.settimeout(0.5)
            log(f'RFCOMM ch{CHANNEL} 已連線 ✓')
        except OSError as e:
            log(f'連線失敗 {e}')
            time.sleep(20)
            continue

        if not handshake(sock):
            try:
                sock.close()
            except Exception:
                pass
            time.sleep(15)
            continue

        # 串流中：同時跑 60 秒保活輪詢
        log('*** 串流已啟動，開始保活 ***')
        last = time.time()
        next_ka = time.time() + KEEPALIVE_PERIOD
        while time.time() < S['deadline']:
            try:
                data = sock.recv(4096)
                if not data:
                    log('機器關閉連線')
                    break
                last = time.time()
                b = (c_ubyte * len(data)).from_buffer_copy(data)
                lib.airmini_feed(S['am'], b, len(data))
            except socket.timeout:
                now = time.time()
                if now - last > 180:
                    log('180 秒無資料，判定失效')
                    break
                if now >= next_ka:
                    keepalive()
                    next_ka = now + KEEPALIVE_PERIOD
            except OSError as e:
                log(f'讀取結束 {e}')
                break

        try:
            sock.close()
        except Exception:
            pass
        S['stream'] = False
        if time.time() >= S['deadline']:
            break
        log('重連中…')
        time.sleep(8)

    log(f'結束：StreamData 共 {S["n"]} 筆（heartbeat {S["hb"]}、保活 {S["ka"]}、事件 {S["events"]}）')


if __name__ == '__main__':
    main()
