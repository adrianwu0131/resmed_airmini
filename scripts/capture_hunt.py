#!/usr/bin/env python3
"""capture_hunt.py — AirMini 間歇性 SPP 視窗的獵手式抓取

2026-11-07 19:00 實測發現（修正前面所有嘗試的錯誤）：
  1. **這台的 SPP 視窗是間歇性的**：機器待機時 SPP 槽位不斷開→關，
     實測 15 次輪詢中 ch5 成功 1 次、Errno 104（reset by peer）5 次、
     Errno 112（host down）7 次。
  2. **ch5 才是資料通道**（歷史所有成功串流都用 ch5）；
     SDP 廣播的 ch6 是「Wireless iAP」通道——能連上但沒有我們的協定。
  3. **通了之後絕對不要立刻 close()**：之前的探測腳本一通就關，把寶貴的
     視窗浪費掉了、還擋住後續握手。連上就要直接交棒給 libairmini 握手。
  4. 輪詢節奏：連線逾時設短（3s）、間隔 2~4s。**不要**用 60~90s 的長間隔
     ——那是給「整包重試」的保守做法；純 connect 探測密集輪詢不會塞死
     （112 是對端沒開窗的正常回應）。

用法：python3 capture_hunt.py [--minutes 45] [--sample 10] [--report 500]
"""
import ctypes
import json
import os
import socket
import sys
import time
from ctypes import (CFUNCTYPE, POINTER, Structure, c_char_p, c_int, c_size_t,
                    c_ubyte, c_void_p, byref)

LIB = os.environ.get('AIRMINI_LIB',
                     '/path/to/libairmini.so')
ADDR = os.environ.get('AIRMINI_ADDR', '<BT_ADDR>')
KEYFILE = os.environ.get('AIRMINI_KEYFILE', 'pull/master_pair_key.json')
OUTFILE = os.environ.get('AIRMINI_OUT', 'pull/stream_data.jsonl')
CHANNELS = [int(x) for x in os.environ.get('AIRMINI_CHANNELS', '5,6').split(',')]

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
lib.airmini_start_stream.argtypes = [c_void_p, POINTER(c_char_p), c_size_t,
                                      c_int, c_int, RESP_CB, c_void_p]
lib.airmini_feed.argtypes = [c_void_p, POINTER(c_ubyte), c_size_t]
lib.airmini_set_notify.argtypes = [c_void_p, NOTIFY_CB, c_void_p]

STREAM_TYPES = (c_char_p * 3)(b'MaskPressure-100hz',
                              b'InspiratoryPressure-50hz',
                              b'Leak-TwoSecond')

S = {'sock': None, 'am': None, 'n': 0, 'tag': '', 'stream': False,
     'sample': 10, 'report': 500, 'deadline': 0.0, 'polls': 0, 'hits': 0}


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
    if S['n'] % 200 == 0:
        log(f'已收 {S["n"]} 筆')


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
    log(f'[ok:{tag}] {result[:120]}')
    if tag == 'session':
        S['tag'] = 'datetime'
        lib.airmini_get_datetime(S['am'], resp_ref, None)
        S['tag'] = 'stream'
        r = lib.airmini_start_stream(S['am'], STREAM_TYPES, 3,
                                     S['sample'], S['report'], resp_ref, None)
        log(f'StartStream 送出 rc={r}（sample={S["sample"]}ms report={S["report"]}ms）')
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


def try_connect():
    """輪詢各通道；通了就**保持連線**回傳 socket（不要 close！）。

    實測（19:10）：ch5 通了之後對端會 idle（4 秒內不送資料也不關閉）——
    這正是可握手的狀態。**不要在握手前 close**，之前的探測就是因為
    「通了立刻 close」把寶貴的視窗浪費掉。
    ch6 是 iAP 通道（能連但不講我們的協定，握手 30s 無回應），只用 ch5。

    connect timeout 給 20s：實測 err112 都在 ~5s 就回，timeout 再長也沒用，
    但偶爾整個 connect 會慢，timeout 太短會誤殺。
    """
    for ch in CHANNELS:
        s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM,
                          socket.BTPROTO_RFCOMM)
        s.settimeout(20)
        try:
            s.connect((ADDR, ch))
            s.settimeout(0.5)
            S['hits'] += 1
            log(f'  ★ ch{ch} 連上了！立刻握手（不放手）')
            return s
        except OSError as e:
            try:
                s.close()
            except Exception:
                pass
            last = e
    return None


def handshake(sock):
    """在已建立的連線上完成金鑰開會話 + 開串流。"""
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

    t0 = time.time()
    while time.time() - t0 < 30:
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
    log('30 秒內未啟動串流')
    return False


def main():
    args = sys.argv[1:]
    mins = 45
    if '--minutes' in args:
        mins = int(args[args.index('--minutes') + 1])
    if '--sample' in args:
        S['sample'] = int(args[args.index('--sample') + 1])
    if '--report' in args:
        S['report'] = int(args[args.index('--report') + 1])
    else:
        # **reportIntervalMs 必須 = sampleIntervalMs × 5**（每報 5 樣）。
        # 實測（2026-11-07）：sample=10/report=50 ✓；sample=10/report=500 ✗
        # （-32600 Invalid Request）；sample=40/report=200 ✓。
        # 比例不對＝每報樣本數超限，裝置直接拒——不是參數名稱錯。
        S['report'] = S['sample'] * 5
    log(f'取樣 {S["sample"]}ms、每報 {S["report"] / S["sample"]:.0f} 樣'
        f'（report={S["report"]}ms）')
    S['deadline'] = time.time() + mins * 60

    log(f'開始獵手：通道 {CHANNELS}，取樣 {S["sample"]}ms，共 {mins} 分鐘')
    last_report = time.time()
    while time.time() < S['deadline']:
        S['polls'] += 1
        sock = try_connect()
        if sock is None:
            if time.time() - last_report > 60:
                last_report = time.time()
                log(f'…已輪詢 {S["polls"]} 次，仍未開窗（等機器待機窗）')
            time.sleep(2.5)
            continue

        if handshake(sock):
            # 串流中——保持連線持續收
            log('*** 開始接收串流 ***')
            last = time.time()
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
                    if time.time() - last > 180:
                        log('180 秒無資料，判定失效')
                        break
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
            time.sleep(5)
        else:
            try:
                sock.close()
            except Exception:
                pass
            time.sleep(3)

    log(f'結束：StreamData 共 {S["n"]} 筆（輪詢 {S["polls"]} 次，連上 {S["hits"]} 次）')


if __name__ == '__main__':
    main()
