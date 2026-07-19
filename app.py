from __future__ import annotations

import asyncio
import json
import os
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from discovery import scan
from remote import (
    PairingRequired,
    RemoteError,
    RemoteService,
    VALID_KEYS,
    fetch_device_name,
)

DATA_DIR = Path(os.environ.get("ONNREMOTE_DATA_DIR", "data"))
CONFIG_PATH = DATA_DIR / "config.json"
STATIC_DIR = Path(__file__).with_name("static")
MAX_BODY_BYTES = 64 * 1024
PAIR_CODE_RE = re.compile(r"[0-9A-Fa-f]{6}")

service = RemoteService(DATA_DIR)


def config() -> dict[str, str]:
    try:
        data = json.loads(CONFIG_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    return {"host": str(data.get("host", "")), "name": str(data.get("name", ""))}


def save_config(value: dict[str, str]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(value, indent=2) + "\n")


def valid_host(host: str) -> bool:
    return 0 < len(host) <= 253 and not any(c.isspace() for c in host)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: object) -> None:
        print("%s - %s" % (self.address_string(), fmt % args))

    def respond(self, status: HTTPStatus, body: dict) -> None:
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def read_body(self) -> dict | None:
        """Parse the JSON request body; responds with 400 and returns None if bad."""
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if not 0 <= length <= MAX_BODY_BYTES:
            self.respond(HTTPStatus.BAD_REQUEST, {"error": "Invalid request body."})
            return None
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            body = None
        if not isinstance(body, dict):
            self.respond(HTTPStatus.BAD_REQUEST, {"error": "Invalid JSON."})
            return None
        return body

    def target_host(self, body: dict) -> str | None:
        """Host from the request body, falling back to the saved device."""
        host = str(body.get("host", "")).strip() or config()["host"]
        if not valid_host(host):
            self.respond(HTTPStatus.BAD_REQUEST,
                         {"error": "Pick a device first.", "needsDevice": True})
            return None
        return host

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/config":
            self.respond(HTTPStatus.OK, config())
            return
        if path in ("/", "/index.html"):
            raw = (STATIC_DIR / "index.html").read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        self.respond(HTTPStatus.NOT_FOUND, {"error": "Not found"})

    def do_POST(self) -> None:
        body = self.read_body()
        if body is None:
            return
        route = urlparse(self.path).path
        handler = {
            "/api/config": self.handle_config,
            "/api/key": self.handle_key,
            "/api/scan": self.handle_scan,
            "/api/probe": self.handle_probe,
            "/api/pair/start": self.handle_pair_start,
            "/api/pair/finish": self.handle_pair_finish,
        }.get(route)
        if handler is None:
            self.respond(HTTPStatus.NOT_FOUND, {"error": "Not found"})
            return
        handler(body)

    def handle_config(self, body: dict) -> None:
        host = str(body.get("host", "")).strip()
        name = str(body.get("name", "")).strip()[:80]
        if not valid_host(host):
            self.respond(HTTPStatus.BAD_REQUEST,
                         {"error": "Enter a valid device IP or hostname."})
            return
        save_config({"host": host, "name": name})
        self.respond(HTTPStatus.OK, {"host": host, "name": name})

    def handle_key(self, body: dict) -> None:
        key = body.get("key")
        if key not in VALID_KEYS:
            self.respond(HTTPStatus.BAD_REQUEST, {"error": "Unsupported remote command."})
            return
        host = config()["host"]
        if not host:
            self.respond(HTTPStatus.BAD_REQUEST,
                         {"error": "Pick a device first.", "needsDevice": True})
            return
        try:
            service.send_key(host, key)
        except PairingRequired as exc:
            self.respond(HTTPStatus.CONFLICT, {"error": str(exc), "needsPairing": True})
        except RemoteError as exc:
            self.respond(HTTPStatus.BAD_GATEWAY, {"error": str(exc)})
        else:
            self.respond(HTTPStatus.OK, {"ok": True})

    def handle_scan(self, body: dict) -> None:
        try:
            service.ensure_certificate()
        except RemoteError:
            pass  # names then fall back to the DIAL lookup; scanning still works
        certfile, keyfile = service.certificate_paths()

        async def name_lookup(ip: str) -> str | None:
            return await fetch_device_name(ip, certfile, keyfile)

        result = asyncio.run(scan(self.client_address[0], name_lookup))
        self.respond(HTTPStatus.OK, result)

    def handle_probe(self, body: dict) -> None:
        host = self.target_host(body)
        if host is None:
            return
        try:
            paired = service.probe(host)
        except RemoteError as exc:
            self.respond(HTTPStatus.BAD_GATEWAY, {"error": str(exc)})
            return
        self.respond(HTTPStatus.OK, {"host": host, "paired": paired})

    def handle_pair_start(self, body: dict) -> None:
        host = self.target_host(body)
        if host is None:
            return
        try:
            service.start_pairing(host)
        except RemoteError as exc:
            self.respond(HTTPStatus.BAD_GATEWAY, {"error": str(exc)})
            return
        self.respond(HTTPStatus.OK, {"ok": True})

    def handle_pair_finish(self, body: dict) -> None:
        host = self.target_host(body)
        if host is None:
            return
        code = str(body.get("code", "")).strip()
        if not PAIR_CODE_RE.fullmatch(code):
            self.respond(HTTPStatus.BAD_REQUEST,
                         {"error": "Enter the 6-character code exactly as shown."})
            return
        try:
            service.finish_pairing(host, code)
        except RemoteError as exc:
            self.respond(HTTPStatus.BAD_GATEWAY, {"error": str(exc)})
            return
        self.respond(HTTPStatus.OK, {"ok": True})


if __name__ == "__main__":
    address = ("0.0.0.0", int(os.environ.get("PORT", "4897")))
    print(f"onnremote listening at http://{address[0]}:{address[1]}")
    ThreadingHTTPServer(address, Handler).serve_forever()
