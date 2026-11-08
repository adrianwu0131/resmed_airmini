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

---

## The five things that cost the most time

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

---

## Layout

```
SKILL.md                         skill entry point (procedures + hard rules)
scripts/
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
references/
  airmini-access.md              Bluetooth protocol, channels, windows, error triage
  cpap-data-sources.md           AirSense ↔ AirMini signal-by-signal mapping, schema
  file-format.md                 EDF channels, header layout, file formats
  airview-aggregation.md         date attribution, cut points, aggregation formulas
  airview-pdf-report.md          AirView-layout PDF generation
  clinical-notes.md              central apnea terminology and treatment options
```

## Quick start

```bash
# capture at true 100 Hz (ch5, key-only reconnect)
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
