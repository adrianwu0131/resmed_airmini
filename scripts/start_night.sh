#!/bin/bash
# start_night.sh — AirMini 夜間錄製啟動腳本（穩定版）
#
# 2026-11-07 依手機 App 的 BLE RPC 日誌重建的握手段序與保活機制：
#   GetVersion → GetSessionKey → SubscribeEvent → Get → StartStream，
#   加上每 55 秒主動送一輪 RPC（App 靠這個保活；只收不送會被閒置收線）。
#   失敗時自動退回 capture_hunt.py（等待間歇性 SPP 視窗）。
#
# 用法：./start_night.sh [分鐘，預設 480]
set -u
cd ~/airmini_capture 2>/dev/null || cd "$(dirname "$0")"
mkdir -p pull

export AIRMINI_LIB="${AIRMINI_LIB:-$HOME/airmini_capture/libairmini.so}"
export AIRMINI_ADDR="${AIRMINI_ADDR:-<BT_ADDR>}"
export AIRMINI_KEYFILE="${AIRMINI_KEYFILE:-$HOME/airmini_capture/pull/master_pair_key.json}"
export AIRMINI_OUT="${AIRMINI_OUT:-$HOME/airmini_capture/pull/stream_data.jsonl}"
export AIRMINI_CHANNEL="${AIRMINI_CHANNEL:-5}"
export AIRMINI_KEEPALIVE="${AIRMINI_KEEPALIVE:-55}"

LOG="$HOME/airmini_capture/capture_$(date +%Y%m%d).log"
echo "=== $(date '+%F %T') 開始（addr=$AIRMINI_ADDR ch=$AIRMINI_CHANNEL keepalive=${AIRMINI_KEEPALIVE}s）===" >> "$LOG"

/usr/bin/python3 stable_capture.py --minutes "${1:-480}" --sample 10 >> "$LOG" 2>&1
RC=$?
echo "=== $(date '+%F %T') stable_capture 結束 rc=$RC ===" >> "$LOG"

if [ "$RC" -ne 0 ]; then
  echo "--- 退回 capture_hunt.py（等待間歇性 SPP 視窗）---" >> "$LOG"
  /usr/bin/python3 capture_hunt.py --minutes "${1:-480}" --sample 10 >> "$LOG" 2>&1
  echo "=== $(date '+%F %T') capture_hunt 結束 ===" >> "$LOG"
fi
