---
name: verify
description: Run onnremote against a fake TV and drive the web UI end-to-end.
---

# Verify onnremote

No real TV is needed. The stack is: `app.py` (stdlib HTTP server) + a fake
Android TV that behaves like an unpaired device.

## Setup (once per session)

```bash
python3 -m venv "$SCRATCH/venv" && "$SCRATCH/venv/bin/pip" install -r requirements.txt
```

## Run

1. **Fake TV** — a script that listens on 6466 (TLS, `CERT_REQUIRED` with no CA
   loaded and `maximum_version = TLSv1_2`, via *blocking* `wrap_socket` so the
   rejection alert is flushed → client sees `InvalidAuth`, like a real unpaired
   TV), 6467 (TLS that completes the handshake, serving an Android-TV-format
   CN like `atvremote/board/board/Name/AA:BB:..` for the cert-name fallback),
   and 8008 (DIAL `device-desc.xml` with a `friendlyName`). Pass `nodial` to
   test the cert-name fallback.
2. **Server** — `ONNREMOTE_DATA_DIR=$SCRATCH/data PORT=4897 venv/bin/python app.py`
3. **Drive** — Playwright (`playwright-core` + `/opt/pw-browsers/chromium`)
   against `http://<container-ip>:4897` (use the container IP, not localhost,
   so `/api/scan` derives a scannable client subnet). Delete
   `$SCRATCH/data/config.json` to re-test the first-launch auto-scan/auto-select.

## Gotchas

- asyncio TLS servers RST instead of flushing the client-cert rejection alert;
  only the blocking `wrap_socket` fake produces `InvalidAuth` on the client.
- UI states are transient: the device row is visible <1s before auto-select
  closes settings; sequence Playwright waits on `#pairing.open` first, and use
  `waitForFunction` (not `:not(.open)` visibility) for "panel closed".
- `pkill -f fake_tv.py` inside a compound command kills the shell itself
  (pattern matches bash's own argv). Kill and start in separate Bash calls.
- Against the fake, `pair/start` fails after the 8s timeout (it cannot speak
  the pairing protobuf), so pairing ends back in settings with an error —
  that is the expected terminal state here.
