"""Android TV Remote Service client shared by the HTTP handlers.

A single AndroidTVRemote lives on a background asyncio loop so the TLS session
survives between keypresses (fast keys instead of a handshake per press) and so
pairing can span the two HTTP requests it needs. The HTTP worker threads talk
to it through the small synchronous facade on RemoteService.
"""
from __future__ import annotations

import asyncio
import threading
from pathlib import Path

from androidtvremote2 import (
    AndroidTVRemote,
    CannotConnect,
    ConnectionClosed,
    InvalidAuth,
)

VALID_KEYS = frozenset({
    "POWER", "HOME", "BACK", "MENU", "VOLUME_UP", "VOLUME_DOWN", "MUTE",
    "DPAD_UP", "DPAD_DOWN", "DPAD_LEFT", "DPAD_RIGHT", "DPAD_CENTER",
    "INPUT", "MEDIA_PLAY_PAUSE", "MEDIA_REWIND", "MEDIA_FAST_FORWARD",
})

# Shown on the TV's pairing dialog.
CLIENT_NAME = "onn remote"
CONNECT_TIMEOUT = 8.0
CALL_TIMEOUT = 30.0


class RemoteError(RuntimeError):
    """A connection or protocol error that can safely be shown in the UI."""


class PairingRequired(RemoteError):
    """The device wants this client to pair before it accepts commands."""


async def fetch_device_name(host: str, certfile: str, keyfile: str,
                            timeout: float = 3.0) -> str | None:
    """Best-effort device name, read from the TV's certificate on the pair port."""
    remote = AndroidTVRemote(CLIENT_NAME, certfile, keyfile, host,
                             loop=asyncio.get_running_loop())
    try:
        name, _mac = await asyncio.wait_for(remote.async_get_name_and_mac(), timeout)
    except Exception:
        return None
    return name or None


class RemoteService:
    """One persistent AndroidTVRemote, driven from the HTTP worker threads."""

    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir
        self._remote: AndroidTVRemote | None = None
        self._host = ""
        self._connected = False
        self._lock = asyncio.Lock()
        self._loop = asyncio.new_event_loop()
        threading.Thread(target=self._loop.run_forever, name="remote-loop",
                         daemon=True).start()

    # ----- synchronous facade used by app.py -----

    def send_key(self, host: str, key: str) -> None:
        self._call(self._send(host, key))

    def probe(self, host: str) -> bool:
        """True if the device accepts us, False if it wants pairing first."""
        return self._call(self._probe(host))

    def start_pairing(self, host: str) -> None:
        self._call(self._start_pairing(host))

    def finish_pairing(self, host: str, code: str) -> None:
        self._call(self._finish_pairing(host, code))

    def ensure_certificate(self) -> None:
        self._call(self._ensure_certificate())

    def certificate_paths(self) -> tuple[str, str]:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        return str(self.state_dir / "cert.pem"), str(self.state_dir / "key.pem")

    def _call(self, coroutine):
        future = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
        try:
            return future.result(timeout=CALL_TIMEOUT)
        except TimeoutError:
            future.cancel()
            raise RemoteError("Timed out talking to the device.") from None

    # ----- everything below runs on the background loop -----

    async def _ensure_certificate(self) -> None:
        certfile, keyfile = self.certificate_paths()
        # The host is irrelevant here; this only creates the local cert files.
        helper = AndroidTVRemote(CLIENT_NAME, certfile, keyfile, "127.0.0.1",
                                 loop=self._loop)
        await helper.async_generate_cert_if_missing()

    async def _get_remote(self, host: str) -> AndroidTVRemote:
        if self._remote and host != self._host:
            self._drop_connection()
            self._remote = None
        if self._remote is None:
            certfile, keyfile = self.certificate_paths()
            remote = AndroidTVRemote(CLIENT_NAME, certfile, keyfile, host,
                                     loop=self._loop)
            await remote.async_generate_cert_if_missing()
            self._remote = remote
            self._host = host
        return self._remote

    def _drop_connection(self) -> None:
        if self._remote:
            self._remote.disconnect()
        self._connected = False

    def _on_invalid_auth(self) -> None:
        # Fired by keep_reconnecting if the TV forgets the pairing later on.
        self._connected = False

    async def _ensure_connected(self, host: str) -> AndroidTVRemote:
        remote = await self._get_remote(host)
        if self._connected:
            return remote
        try:
            await asyncio.wait_for(remote.async_connect(), CONNECT_TIMEOUT)
        except InvalidAuth as exc:
            raise PairingRequired(
                "The device asks for pairing before it takes commands."
            ) from exc
        except (CannotConnect, ConnectionClosed, OSError, asyncio.TimeoutError) as exc:
            raise RemoteError(
                f"Could not reach {host}. Is the device on and on this network?"
            ) from exc
        remote.keep_reconnecting(self._on_invalid_auth)
        self._connected = True
        return remote

    async def _send(self, host: str, key: str) -> None:
        if key not in VALID_KEYS:
            raise RemoteError("Unsupported remote command.")
        async with self._lock:
            remote = await self._ensure_connected(host)
            try:
                remote.send_key_command(key)
            except (ConnectionClosed, ValueError) as exc:
                self._drop_connection()
                raise RemoteError(
                    "Lost the connection to the device — press the key again."
                ) from exc

    async def _probe(self, host: str) -> bool:
        async with self._lock:
            try:
                await self._ensure_connected(host)
            except PairingRequired:
                return False
            return True

    async def _start_pairing(self, host: str) -> None:
        async with self._lock:
            remote = await self._get_remote(host)
            self._connected = False  # async_start_pairing drops the live session
            try:
                await asyncio.wait_for(remote.async_start_pairing(), CONNECT_TIMEOUT)
            except (CannotConnect, ConnectionClosed, OSError, asyncio.TimeoutError) as exc:
                raise RemoteError(
                    f"Could not start pairing with {host}. "
                    "Is the device on and on this network?"
                ) from exc

    async def _finish_pairing(self, host: str, code: str) -> None:
        async with self._lock:
            remote = await self._get_remote(host)
            try:
                await asyncio.wait_for(remote.async_finish_pairing(code), CONNECT_TIMEOUT)
            except InvalidAuth as exc:
                raise RemoteError("The device rejected that code.") from exc
            except (CannotConnect, ConnectionClosed, OSError, asyncio.TimeoutError) as exc:
                raise RemoteError(
                    "Pairing was interrupted. Start it again from the device list."
                ) from exc
