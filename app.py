from __future__ import annotations

import asyncio
import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from remote import GoogleTVRemote, RemoteError, VALID_KEYS

DATA_DIR = Path(os.environ.get("ONNREMOTE_DATA_DIR", "data"))
CONFIG_PATH = DATA_DIR / "config.json"
STATIC_DIR = Path(__file__).with_name("static")


def config() -> dict[str, str]:
    try:
        return json.loads(CONFIG_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {"host": ""}


def save_config(value: dict[str, str]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(value, indent=2) + "\n")


class Handler(BaseHTTPRequestHandler):
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

    def do_GET(self) -> None:
        if urlparse(self.path).path == "/api/config":
            self.respond(HTTPStatus.OK, config())
            return
        if urlparse(self.path).path in ("/", "/index.html"):
            raw = (STATIC_DIR / "index.html").read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        self.respond(HTTPStatus.NOT_FOUND, {"error": "Not found"})

    def do_POST(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length))
        except (ValueError, json.JSONDecodeError):
            self.respond(HTTPStatus.BAD_REQUEST, {"error": "Invalid JSON."})
            return

        route = urlparse(self.path).path
        if route == "/api/config":
            host = str(body.get("host", "")).strip()
            if not host or len(host) > 253 or any(c.isspace() for c in host):
                self.respond(HTTPStatus.BAD_REQUEST, {"error": "Enter a valid TV IP or hostname."})
                return
            save_config({"host": host})
            self.respond(HTTPStatus.OK, {"host": host})
            return
        if route == "/api/key":
            key = body.get("key")
            if key not in VALID_KEYS:
                self.respond(HTTPStatus.BAD_REQUEST, {"error": "Unsupported remote command."})
                return
            try:
                asyncio.run(GoogleTVRemote(config()["host"], DATA_DIR).send(key))
            except RemoteError as exc:
                self.respond(HTTPStatus.BAD_GATEWAY, {"error": str(exc)})
                return
            self.respond(HTTPStatus.OK, {"ok": True})
            return
        self.respond(HTTPStatus.NOT_FOUND, {"error": "Not found"})


if __name__ == "__main__":
    address = ("0.0.0.0", int(os.environ.get("PORT", "8080")))
    print(f"onnremote listening at http://{address[0]}:{address[1]}")
    ThreadingHTTPServer(address, Handler).serve_forever()
