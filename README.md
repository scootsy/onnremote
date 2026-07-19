# onnremote

A deliberately small, self-hosted web remote for onn. Google TV devices. It is
best used as an Unraid Docker container: opening it on an iPhone gives a simple,
home-screen-friendly remote without building or sideloading an iOS app.

## How this works

These TVs expose the **Android TV Remote Service** on the local network. This
is the same TLS/protobuf remote-control service used by third-party remotes;
the open-source `androidtvremote2` client implements its reverse-engineered
protocol (TCP 6466, with pairing on 6467). Google has not published a stable,
public Google TV remote-control API for third-party apps, so this project
intentionally uses that community client.

The UI sends Android key names such as `HOME`, `DPAD_CENTER`, and `POWER` to
the service. `POWER` is a toggle: whether it can wake a TV depends on the
model's standby/network and HDMI-CEC settings.

### Finding the TV

**Scan for devices** walks the `/24` networks around the browser and the
server (at most three) and tries a TCP connect to port 6466 on each address.
Anything that answers speaks the remote service, so it is compatible by
construction. Each hit is labelled with a display name — from the Cast/DIAL
web server (`:8008/ssdp/device-desc.xml`) when available, else from the
device's pairing certificate. If exactly one device turns up and none is
configured yet, it is selected automatically; with several, you tap the one
you want. Manual IP entry stays available as a fallback.

The browser's subnet is scanned first, so discovery also works when the
container runs on a bridged Docker network (where the server's own interfaces
only see the container network).

### Pairing

Pairing happens in the app: pick a device, the TV shows a six-character code,
type it in. The client certificate is stored in `/data`, so pairing survives
container upgrades. If the TV ever forgets the client, the app asks to pair
again by itself.

## Run it on Unraid

1. Copy this folder to the server and run `docker compose up -d --build`, or
   create an Unraid container from this `Dockerfile` with port `8080` mapped.
2. Browse to `http://YOUR-SERVER:8080` from the same LAN. The remote scans for
   devices on first launch — tap yours (a lone find selects itself).
3. Type the pairing code the TV displays. Keep `/data` persistent so the
   pairing certificate survives upgrades. On the TV, enable network remote
   control / Android TV Remote Service if that setting is available.

> **Important:** Do not expose this container to the internet. It is intended
> for a trusted LAN and has no authentication.

## Development checks

```bash
python -m py_compile app.py remote.py discovery.py
python app.py
```

Then open `http://localhost:8080`. The UI, configuration, and scan endpoints
work without a TV; sending a key requires a Google/Android TV on the LAN.
