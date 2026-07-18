# onnremote

A deliberately small, self-hosted web remote for onn. Google TV devices. It is
best used as an Unraid Docker container: opening it on an iPhone gives a simple,
home-screen-friendly remote without building or sideloading an iOS app.

## How this works

These TVs expose the **Android TV Remote Service** on the local network. This
is the same TLS/protobuf remote-control service used by third-party remotes;
the open-source `androidtvremote2` client implements its reverse-engineered
protocol (normally TCP 6466, with pairing on a neighbouring service port).
Google has not published a stable, public Google TV remote-control API for
third-party apps, so this project intentionally uses that community client.

The UI sends Android key names such as `HOME`, `DPAD_CENTER`, and `POWER` to
the service. `POWER` is a toggle: whether it can wake a TV depends on the
model's standby/network and HDMI-CEC settings.

## Run it on Unraid

1. Copy this folder to the server and run `docker compose up -d --build`, or
   create an Unraid container from this `Dockerfile` with port `8080` mapped.
2. Browse to `http://YOUR-SERVER:8080` from the same LAN and enter the TV's
   reserved IP address.
3. Pair the protocol client with the TV once. Pairing support and certificate
   handling are supplied by `androidtvremote2`; keep `/data` persistent so its
   certificate survives container upgrades. On the TV, enable network remote
   control / Android TV Remote Service if that setting is available.

> **Important:** Do not expose this container to the internet. It is intended
> for a trusted LAN and has no authentication.

## Development checks

```bash
python -m py_compile app.py remote.py
python app.py
```

Then open `http://localhost:8080`. The UI and configuration endpoint work
without a TV; sending a key requires a paired Google/Android TV on the LAN.
