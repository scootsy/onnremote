"""LAN discovery for Android TV Remote devices.

Scanner: walk the /24 networks around the requesting web client and the server
itself, trying TCP connects to the Android TV Remote Service port (6466).
Sifter: anything that accepts the connection speaks the protocol we need, so it
is compatible by construction; each hit is then given a display name. The
device list in the UI is the selector.

The client's subnet is scanned first because in a bridged Docker container the
server's own interfaces only see the container network — the browser's IP is
the reliable clue about where the TVs actually live.
"""
from __future__ import annotations

import asyncio
import html
import ipaddress
import re
import socket
from collections.abc import Awaitable, Callable

REMOTE_PORT = 6466
DIAL_PORT = 8008
PROBE_TIMEOUT = 1.0
NAME_TIMEOUT = 3.0
MAX_NETWORKS = 3
CONCURRENCY = 128

_FRIENDLY_NAME_RE = re.compile(rb"<friendlyName>\s*([^<]{1,100}?)\s*</friendlyName>")


def _local_addresses() -> list[str]:
    addresses: list[str] = []
    # The UDP "connection" is never sent; it only selects the outbound interface.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("198.51.100.1", 9))
            addresses.append(probe.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, family=socket.AF_INET):
            addresses.append(info[4][0])
    except OSError:
        pass
    return addresses


def candidate_networks(client_ip: str | None) -> list[ipaddress.IPv4Network]:
    """The /24s worth scanning: the web client's subnet first, then the server's."""
    networks: list[ipaddress.IPv4Network] = []
    for raw in [client_ip, *_local_addresses()]:
        try:
            address = ipaddress.ip_address(raw or "")
        except ValueError:
            continue
        if (address.version != 4 or not address.is_private
                or address.is_loopback or address.is_link_local):
            continue
        network = ipaddress.ip_network(f"{address}/24", strict=False)
        if network not in networks:
            networks.append(network)
    return networks[:MAX_NETWORKS]


async def _port_open(ip: str, port: int, timeout: float) -> bool:
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout)
    except (OSError, asyncio.TimeoutError):
        return False
    writer.close()
    try:
        await writer.wait_closed()
    except OSError:
        pass
    return True


async def _dial_name(ip: str) -> str | None:
    """Ask the DIAL/Cast web server (if any) for the user-chosen device name."""
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(ip, DIAL_PORT), PROBE_TIMEOUT)
    except (OSError, asyncio.TimeoutError):
        return None
    try:
        writer.write(b"GET /ssdp/device-desc.xml HTTP/1.1\r\n"
                     b"Host: " + ip.encode() + b"\r\n"
                     b"Connection: close\r\n\r\n")
        await writer.drain()
        raw = await asyncio.wait_for(reader.read(16384), NAME_TIMEOUT)
    except (OSError, asyncio.TimeoutError):
        return None
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass
    match = _FRIENDLY_NAME_RE.search(raw)
    if not match:
        return None
    return html.unescape(match.group(1).decode("utf-8", "replace"))


async def scan(client_ip: str | None,
               name_lookup: Callable[[str], Awaitable[str | None]]) -> dict:
    """Find remote-capable devices and label them.

    name_lookup is tried when the DIAL name is unavailable (it typically reads
    the name from the TV's pairing certificate).
    """
    networks = candidate_networks(client_ip)
    targets: list[str] = []
    seen: set[str] = set()
    for network in networks:
        for host in network.hosts():
            ip = str(host)
            if ip not in seen:
                seen.add(ip)
                targets.append(ip)

    semaphore = asyncio.Semaphore(CONCURRENCY)

    async def check(ip: str) -> str | None:
        async with semaphore:
            return ip if await _port_open(ip, REMOTE_PORT, PROBE_TIMEOUT) else None

    hits = [ip for ip in await asyncio.gather(*(check(ip) for ip in targets)) if ip]

    async def describe(ip: str) -> dict:
        name = await _dial_name(ip) or await name_lookup(ip)
        return {"ip": ip, "name": name or ""}

    devices = list(await asyncio.gather(*(describe(ip) for ip in hits)))
    devices.sort(key=lambda device: ipaddress.ip_address(device["ip"]))
    return {"devices": devices, "networks": [str(network) for network in networks]}
