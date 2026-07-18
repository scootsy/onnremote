"""Small compatibility wrapper around androidtvremote2.

Keeping the protocol client out of the HTTP handlers makes it possible to swap
libraries without exposing a television directly to the web interface.
"""
from __future__ import annotations

import asyncio
import inspect
from pathlib import Path


VALID_KEYS = frozenset({
    "POWER", "HOME", "BACK", "MENU", "VOLUME_UP", "VOLUME_DOWN", "MUTE",
    "DPAD_UP", "DPAD_DOWN", "DPAD_LEFT", "DPAD_RIGHT", "DPAD_CENTER",
    "INPUT", "MEDIA_PLAY_PAUSE", "MEDIA_REWIND", "MEDIA_FAST_FORWARD",
})


class RemoteError(RuntimeError):
    """A connection or protocol error that can safely be shown in the UI."""


class GoogleTVRemote:
    def __init__(self, host: str, state_dir: Path) -> None:
        self.host = host
        self.state_dir = state_dir
        self._lock = asyncio.Lock()

    async def send(self, key: str) -> None:
        if key not in VALID_KEYS:
            raise RemoteError("Unsupported remote command.")
        if not self.host:
            raise RemoteError("Set the TV address first.")

        try:
            from androidtvremote2 import AndroidTVRemote  # type: ignore
        except ImportError as exc:
            raise RemoteError("Android TV protocol support is not installed.") from exc

        # androidtvremote2 has used both certificate_file and certfile over its
        # lifetime. Inspecting the constructor keeps existing paired volumes
        # usable across those releases.
        cert = self.state_dir / "androidtvremote2.cert"
        kwargs = {}
        params = inspect.signature(AndroidTVRemote).parameters
        if "certificate_file" in params:
            kwargs["certificate_file"] = str(cert)
        elif "certfile" in params:
            kwargs["certfile"] = str(cert)

        async with self._lock:
            try:
                remote = AndroidTVRemote(self.host, **kwargs)
                await remote.async_connect()
                await remote.async_send_key_command(key)
            except Exception as exc:  # library errors differ by release
                raise RemoteError(
                    "Could not reach or authenticate with the TV. Pair it first "
                    "and confirm its IP address."
                ) from exc
            finally:
                if "remote" in locals() and hasattr(remote, "async_disconnect"):
                    await remote.async_disconnect()
