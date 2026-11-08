# resmed_airmini

Hermes Agent skill for working with **ResMed AirMini** (the travel CPAP) over Bluetooth,
and with **AirSense 10** SD-card / AirView data.

The hard-won part of this repo is the AirMini Bluetooth protocol work: `libairmini`
handshake semantics, the channel/window quirks of the device, and a working
**100 Hz live-streaming** capture path that needs neither the app nor the PIN.

```
git clone git@github.com:adrianwu0131/resmed_airmini.git
cp -r resmed_airmini ~/.hermes/skills/resmed-cpap-sdcard/
```

Then load the skill with `skill_view(name='resmed-cpap-sdcard')`.

---

## What works today

| Capability | Status |
|---|---|
| AirMini live stream at **true 100 Hz** (MaskPressure / InspiratoryPressure / Leak) | verified on hardware |
| Key-only reconnect (**no PIN, no button press**) | verified on hardware |
| Full-night capture → SQLite (`am_stream_wave`) → plots | verified, capacity measured |
| AirSense 10 SD-card parsing (STR/PLD/EVE/annotations) | verified |
| AirView-format PDF reproduction | verified against originals |
| Stable long-duration connection (app-compatible handshake + keepalive) | handshake rebuilt; duration still being measured |

---

## The six things that cost the most time

These are all in `references/airmini-access.md` with the supporting measurements.

**1. `airmini_pair()` already opens the session.**
It returns `masterPairKey` **and** `sessionKey`, and internally transitions to
`SESSION_OPEN`. Calling `airmini_open_session()` afterwards returns `rc=2`
(`AIRMINI_ERR_STATE`) — that means *"the session is already open"*, **not**
*"rejected"*. Reading it as a rejection is what creates a bogus fallback path.
Only the PIN-less reconnect path (`airmini_open_session(masterPairKey)`) needs that call.

**2. `StartStream` params shape — and a ratio limit that shares an error code.**
Always use `airmini_start_stream()`; never hand-write the JSON with `airmini_call()`.
But the params are not just shape-checked:

| `sampleIntervalMs` | `reportIntervalMs` | samples/report | result |
|---|---|---|---|
| 10 | **50** | 5 | ✅ 5,985 records, zero interruption |
| 10 | 500 | 50 | ❌ `-32600 Invalid Request` |
| 40 | **200** | 5 | ✅ 586 records |
| 1000 | 1000 | 1 | ✅ 445 records |

**`reportIntervalMs` must equal `sampleIntervalMs` × 5.** Both a malformed shape and a
wrong ratio return the same `-32600`, so check `report/sample ≤ 5` before suspecting
the JSON.

**3. Channel 5 is the data channel; channel 6 is iAP.**
`sdptool browse` lists two RFCOMM channels. Channel 6 (`00000000-deca-fade-deca-deafdecacaff`,
"Wireless iAP") accepts a connection but does not speak the libairmini protocol —
the handshake gets no reply at all. Channel 5 (Serial Port, `00001101-…`) is the one
that carries data.

**4. The SPP window is intermittent, and a probe that closes on success wastes it.**
In standby the device opens the window at random — measured: 20× `Host is down`,
5× `Connection reset by peer`, 2× success out of 40 attempts. When a connection
succeeds the peer stays **idle** (neither sends nor closes) — that is the window
in which a handshake will succeed. **Do not `close()` the socket when probing**;
hand it straight to the handshake. Connect timeout length is irrelevant (errno 112
returns in ~5 s regardless); dense polling every 2–4 s is what finds the window.

**5. A key solves authentication, not transport.**
`airmini_open_session(masterPairKey)` removes the PIN and the button press, but the
RFCOMM slot must still be open on the device. Also note `bluetoothctl connect`
reporting `Connected: yes` only means the **ACL** link is up — the SPP slot can
still be closed, and a Python `connect()` will still fail with `Host is down`.

**6. Connections die because the client only listens — copy the phone app.**
The app's BLE RPC log (`airmini_log_*.txt`) shows what a working client does:

```
OUT 1 StartStream    → TIMEOUT      ← app's deliberate probe; session not open yet
OUT 2 GetVersion     → Success
OUT 3 GetSessionKey  → Success      ← session opens here
OUT 4 SubscribeEvent → Success      ← subscribe to device events
OUT 5..8 Get / GetDateTime / Get
OUT 9 StartStream    → Success      ← second attempt really starts the stream
… every 60 s: GetLoggedData → GetHistory → Get
```

Three differences from a naive client, each of which we measured:

| | naive client | phone app |
|---|---|---|
| session lifetime | **0–299 s** (six streams) | **4–10 hours** (longest 36,650 s) |
| after opening the session | straight to `StartStream` | `GetVersion` → `GetSessionKey` → **`SubscribeEvent`** → `Get` → `StartStream` |
| while streaming | **only receives** | **sends a round of RPC every 60 s** |

`Heartbeat` travels device→client (the app log shows one every 60 s), so the
client does **not** send it — the app keeps the link alive by *originating*
`GetLoggedData`/`GetHistory`/`Get` every minute. A receive-only client is
treated as idle and dropped.

`scripts/stable_capture.py` reproduces this sequence: app-order handshake,
`SubscribeEvent`, and a 55-second keepalive round (55 s avoids landing in
phase with the device's own 60 s heartbeat).

Two notes from the app log worth keeping:
- `StartStream` timing out right after connect is **normal app behaviour**, not
  a handshake failure.
- The app's sessions cluster around 23:00–09:30 (while the device is being
  worn), and most are `INBOUND_CONNECTION` — *the device dials out*. That is
  why the SPP window is so hard to catch in standby.

**Still open:** `EnterTherapy` / `EnterMaskFit` / `EnterStandby` exist as
protocol methods (no public libairmini API for them). Whether sending
`EnterTherapy` keeps the SPP window open in standby is **unverified** — it
changes device state, so test it with the mask on and only with the user
present.

---

## Layout

```
SKILL.md                         skill entry point (procedures + hard rules)
scripts/
  stable_capture.py              app-compatible capture: app-order handshake,
                                 SubscribeEvent, 55 s keepalive round
  capture_hunt.py                100 Hz capture; hunts the intermittent SPP window
  probe_intervals.py             discover which sampleIntervalMs the device accepts
  airmini_capture2.py            capture with auto-reconnect (key-first, PIN fallback)
  airmini_pull.c                 C driver: PIN pair → full GetLoggedData history
  ingest_stream.py               stream jsonl → SQLite `am_stream_wave`
  plot_stream.py                 three-channel waveform plots
  breath_period.py               autocorrelation breathing-period estimate
  resmed_s10.py                  AirSense SD-card EDF/STR/annotation parser
  s10_report.py                  compliance + per-night + daily CSV report
  night_curve_check.py           per-night curve/event alignment (pre-event hyperventilation)
  start_night.sh                 pi5 nightly launcher (stable capture, falls back to the hunter)
  ingest_night.sh                pi5 ingest launcher (measures the clock offset from the data)
references/
  airmini-access.md              Bluetooth protocol, channels, windows, error triage
  cpap-data-sources.md           AirSense ↔ AirMini signal-by-signal mapping, schema
  file-format.md                 EDF channels, header layout, file formats
  airview-aggregation.md         date attribution, cut points, aggregation formulas
  airview-pdf-report.md          AirView-layout PDF generation
  clinical-notes.md              central apnea terminology and treatment options
```

## Running it on another machine (pi5)

Pre-flight checks on the target — all five must pass before deploying:

```bash
uname -m                                     # aarch64 on the Pi
python3 -c 'import socket; socket.AF_BLUETOOTH'
ls /usr/include/openssl/ssl.h                # native gcc, no conda needed
lsmod | grep bluetooth
which rfcomm bluetoothctl
```

Copy across: `stable_capture.py`, `capture_hunt.py`, `ingest_stream.py`,
`start_night.sh`, `ingest_night.sh`, and a locally built `libairmini.so`
(`gcc -fPIC -shared src/*.c -lssl -lcrypto`). Copy the **credentials** —
`pull/master_pair_key.json` and `pull/pin.txt` — with `scp` (never echo them
to a terminal) and `chmod 600` them on the far side. Set the address and paths
through the environment variables shown above; the scripts read them, they do
not hardcode them.

Cron on the Pi:

```cron
0 22 * * * <capture dir>/start_night.sh 480 >> .../cron.log 2>&1
30 7 * * * <capture dir>/ingest_night.sh    >> .../cron.log 2>&1
```

`ingest_night.sh` measures the clock offset from the data itself (machine
`startTime` vs local `recv`) rather than reusing a hardcoded number — a fixed
offset is only valid for the batch it was measured on.

**Known limitation.** In standby the device's SPP window is rare (486 polls,
0 hits). The app's own connections cluster around 23:00–09:30 while the
device is being worn, and most are `INBOUND_CONNECTION` — the device dials
out. So unattended overnight capture only works when the device is actually
in use; waiting for a standby window is not reliable enough to build on.
Whether `EnterTherapy` keeps the window open is **unverified**.

**Two writers, one database.** The main machine and the Pi can both write
`cpap_data.db`. Keep the roles split (one rebuilds/reports, the other backs
up and audits), make every script idempotent, and check the file mtime and
`sync_meta` before rebuilding or altering a schema.

## Quick start

```bash
export AIRMINI_LIB=/path/to/libairmini.so
export AIRMINI_ADDR=<BT_ADDR>          # optional: default <BT_ADDR>
export AIRMINI_PIN=<PAIRING_CODE>      # only needed for the very first pairing

# stable capture at true 100 Hz (ch5, key-only reconnect, 55 s keepalive)
python3 scripts/stable_capture.py --minutes 480 --sample 10

# or: hunt the intermittent SPP window (useful when the device is in standby)
python3 scripts/capture_hunt.py --minutes 480 --sample 10

# ingest into the aggregate database
python3 scripts/ingest_stream.py

# plot and measure
python3 scripts/plot_stream.py --out pull/stream.png
python3 scripts/breath_period.py
```

`capture_hunt.py` defaults `reportIntervalMs = sampleIntervalMs × 5`; pass
`--report` only if you know why you are departing from that.

## Storage cost (measured, not estimated)

8 hours × 3 channels × 100 Hz = 8,640,000 rows → **3.17 GB** in SQLite,
**94 s** to ingest. That is ~400 MB/hour and ~100 GB/month if every night is kept
at full rate. Downsample or archive older waveforms rather than letting
`am_stream_wave` grow without bound.

## Clinical scope

`references/clinical-notes.md` supports clinician-facing discussion; it is not a
diagnosis. The CPAP data here belongs to the user's own device and their own
authorized use.
