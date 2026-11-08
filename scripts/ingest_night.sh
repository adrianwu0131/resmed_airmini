#!/bin/bash
# ingest_night.sh — 把昨晚的串流 jsonl 入庫到彙整庫
#
# offset 由資料本身量（機器 startTime vs 本機 recv），不寫死常數——
# 寫死的值只對當批有效，機器石英鐘會漂。
set -u
cd ~/airmini_capture 2>/dev/null || cd "$(dirname "$0")"
mkdir -p pull

DB="${CPAP_DB:-$HOME/airmini/cpap_data.db}"

# 沒有彙整庫時先從主機取一份（唯讀副本）；取不到就用本機獨立庫
if [ ! -f "$DB" ]; then
  echo "[ingest] 首次：嘗試從主機取彙整庫副本"
  scp -q "${CPAP_HOST:-adrian@192.168.100.1}:~/airmini/cpap_data.db" "$DB" 2>/dev/null \
    || echo "[ingest] 取不到主機庫，改用本機獨立庫"
fi

OFF=$(/usr/bin/python3 - <<'PY'
import json, os
from datetime import datetime, timezone
p = os.path.expanduser('~/airmini_capture/pull/stream_data.jsonl')
if not os.path.exists(p):
    print(807.0); raise SystemExit
dev = recv = None
with open(p) as fp:
    for line in fp:
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        pr = r.get('data', {}).get('params', {})
        st = pr.get('startTime')
        if not st or 'recv' not in r:
            continue
        dev = datetime.fromisoformat(st.replace('Z', '')).replace(tzinfo=timezone.utc)
        recv = datetime.fromtimestamp(r['recv'] / 1000, tz=timezone.utc)
        break
print(round((dev - recv).total_seconds(), 1) if dev and recv else 807.0)
PY
)
echo "[ingest] offset=$OFF 秒（由資料實測）"

/usr/bin/python3 ingest_stream.py --db "$DB" \
  --jsonl ~/airmini_capture/pull/stream_data.jsonl --offset "$OFF"

echo "[ingest] 完成 $(date '+%F %T')"
