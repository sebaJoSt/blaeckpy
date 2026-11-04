"""The status page: a small web page showing the device, its connection, signals and entities,
refreshed every second. On by default, at http://<computer>:8080.

The HTTP server runs on a thread of its own, but never touches the device: tick() builds a
snapshot about twice a second, on the script's thread, and the server only hands out the latest.
A sensor's get= is read for it only while the page is open, so nobody looking costs nothing.
"""

from __future__ import annotations

import html
import ipaddress
import json
import logging
import math
import socket
import string
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files

log = logging.getLogger("blaeckpy")

DEFAULT_PORT = 8080
# How often tick() refreshes what the page shows, in seconds.
REFRESH = 0.5
# How long after the page last asked it still counts as open, in seconds. It asks every second.
VIEWING = 3.0

_KINDS = {"NumberInput": "Number", "TextInput": "Text"}
# What a password input shows: anyone who can reach the page can read it, and its length is the
# password's own business too.
_HIDDEN = "••••••••"


def _text(value) -> str:
    if isinstance(value, float) and not math.isfinite(value):
        return "NaN" if math.isnan(value) else ("Inf" if value > 0 else "-Inf")
    return str(value)


def _entity(prop, viewing: bool) -> dict:
    kind = type(prop).__name__
    entry = {"name": prop.name, "kind": _KINDS.get(kind, kind), "unit": None, "value": "",
             "types": [], "at": None}
    if kind == "Event":
        entry["types"] = list(prop.types)
        last = prop._last
        entry["value"], entry["at"] = (last if last is not None else (None, None))
        return entry
    if kind == "Button":
        return entry
    entry["unit"] = prop._presentation.unit
    options = getattr(prop, "options", None)
    if getattr(prop, "_signal", None) is not None:
        value = prop._signal.value
    elif viewing and getattr(prop, "_get", None) is not None:
        # Read as tick() reads it: once in a pass, and one that raises is logged, not raised.
        value = prop._current()
    else:
        value = prop._value
    if getattr(prop, "mode", None) == "password" and value:
        value = _HIDDEN
    if options is not None and isinstance(value, int) and 0 <= value < len(options):
        value = options[value]
    entry["value"] = _text(value)
    return entry


def snapshot(device, viewing: bool = False) -> dict:
    """What the page shows, taken on the script's thread. ``viewing`` reads the get= sensors, with
    the page open; otherwise they show the value they last gave."""
    boards = [device, *device._devices]
    devices = []
    for d in boards:
        entities = [p for p in device._properties if p.device_id == d._id] + \
                   [e for e in device._events if e.device_id == d._id] + \
                   [b for b in device._buttons if b.device_id == d._id]
        devices.append({
            "name": d.name, "hw_version": d.hw_version, "fw_version": d.fw_version,
            "missing": bool(getattr(d, "_missing", False)),
            "signals": [{"name": s.name, "type": s.type.__name__, "unit": s.unit, "value": _text(s.value)}
                        for s in device._signals if s.device_id == d._id],
            "entities": [_entity(e, viewing) for e in entities],
        })
    if device._data_stopped:
        data = "Stopped"
    elif device._timed:
        data = f"Running, every {device._interval_ms} ms"
    else:
        data = "Running, on request"
    transport = device._transport
    clients = None
    if transport is not None and hasattr(transport, "slots"):
        clients = [{"slot": i, "address": _address(c.address),
                    "role": "Host" if i == transport.host else "Terminal"}
                   for i, c in enumerate(transport.slots) if c is not None]
    last = device._last_command
    return {
        "device": {"name": device.name, "hw_version": device.hw_version, "fw_version": device.fw_version},
        "connection": device._connection,
        "data": data,
        "entities": not device._entities_stopped,
        "timestamp_mode": str(device._timestamp_mode),
        "last_command": None if last is None else {"name": last[0], "at": last[1]},
        "clients": clients,
        "devices": devices,
    }


def _address(address) -> str:
    if isinstance(address, tuple) and len(address) >= 2:
        host = address[0]
        return f"[{host}]:{address[1]}" if ":" in str(host) else f"{host}:{address[1]}"
    return str(address)


def describe(transport) -> str:
    """The connection in words: "TCP 0.0.0.0:2323 (127.0.0.1, 192.168.1.20)", "TCP
    127.0.0.1:2323" or "Serial COM3 at 115200 baud"."""
    if transport is None:
        return "Closed"
    server = getattr(transport, "server", None)
    if server is not None:
        try:
            name = server.getsockname()
        except OSError:
            return "TCP"
        text = "TCP " + _address(name)
        if is_everywhere(name[0]):
            text += " (" + ", ".join(reachable_hosts(name[0], server.family)) + ")"
        return text
    port = transport.port
    name = getattr(port, "port", None) or getattr(port, "name", None)
    text = f"Serial {name}" if isinstance(name, str) else type(port).__name__
    baud = getattr(port, "baudrate", None)
    return f"{text} at {baud} baud" if baud else text


def is_everywhere(host: str) -> bool:
    """0.0.0.0 or ::, listening on all of the computer's addresses."""
    try:
        return ipaddress.ip_address(host).is_unspecified
    except ValueError:
        return host == ""


def network_address(family: int) -> str | None:
    """The address this computer has on its network, as Flask finds it: a UDP socket
    "connected" to a private address sends nothing, but the system picks the interface it would
    use. None without a network. On a VPN it may be the VPN's address: it is only a hint."""
    target = "fd31:f903:5ab5:1::1" if family == socket.AF_INET6 else "10.253.155.219"
    try:
        with socket.socket(family, socket.SOCK_DGRAM) as s:
            s.connect((target, 58162))
            address = s.getsockname()[0]
    except OSError:
        return None
    try:
        if ipaddress.ip_address(address).is_loopback or ipaddress.ip_address(address).is_unspecified:
            return None
    except ValueError:
        return None
    return address


def reachable_hosts(host: str, family: int) -> list[str]:
    """Where a socket bound to ``host`` is reached: this computer and, when it listens on all
    addresses, the network address too. IPv6 in brackets, as in a URL."""
    if is_everywhere(host):
        v6 = family == socket.AF_INET6
        hosts = ["::1" if v6 else "127.0.0.1"]
        network = network_address(family)
        if network is not None:
            hosts.append(network)
    else:
        hosts = [host]
    return [f"[{h}]" if ":" in h else h for h in hosts]


def _uptime(seconds: float) -> str:
    s = int(seconds)
    days, s = divmod(s, 86400)
    hours, s = divmod(s, 3600)
    minutes, s = divmod(s, 60)
    if days:
        return f"{days} d {hours} h {minutes} min"
    if hours:
        return f"{hours} h {minutes} min {s} s"
    if minutes:
        return f"{minutes} min {s} s"
    return f"{s} s"


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    # On Windows, SO_REUSEADDR would let a second program take a port in use.
    allow_reuse_address = sys.platform != "win32"

    def handle_error(self, request, client_address) -> None:
        # A browser reloading or closing the page mid-reply is no error.
        if not isinstance(sys.exc_info()[1], ConnectionError):
            super().handle_error(request, client_address)


def _handler(status: "StatusPage"):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]
            if path == "/":
                self._reply(200, "text/html; charset=utf-8", status.page)
            elif path == "/api":
                # One assignment, read on the script's thread.
                status.asked = time.monotonic()
                data = dict(status.data)
                now = time.time()
                data["now"] = now
                data["uptime"] = _uptime(time.monotonic() - status.started)
                self._reply(200, "application/json", json.dumps(data).encode())
            elif path == "/pico.min.css":
                self._reply(200, "text/css", _pico(), cache=True)
            else:
                self._reply(404, "text/plain", b"Not found")

        def _reply(self, code: int, content_type: str, body: bytes, cache: bool = False) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "max-age=86400" if cache else "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args) -> None:
            pass

    return Handler


_pico_css: bytes | None = None


def _pico() -> bytes:
    global _pico_css
    if _pico_css is None:
        _pico_css = files("blaeckpy").joinpath("static", "pico.min.css").read_bytes()
    return _pico_css


def bind_address(transport) -> tuple[str, int]:
    """Where the page listens: on TCP, where the device does; on serial, everywhere, so the
    page of a headless computer can be reached from another."""
    server = getattr(transport, "server", None)
    if server is not None:
        try:
            host = server.getsockname()[0]
            return host, server.family
        except OSError:
            pass
    return "0.0.0.0", socket.AF_INET


class StatusPage:
    def __init__(self, device, port: int) -> None:
        from ._device import LIBRARY_VERSION

        host, family = bind_address(device._transport)
        template = files("blaeckpy").joinpath("static", "status.html").read_text(encoding="utf-8")
        self.page = string.Template(template).safe_substitute(
            device_name=html.escape(device.name), lib_version=html.escape(LIBRARY_VERSION)).encode()
        self.data = snapshot(device)
        self.started = time.monotonic()
        self._taken = self.started
        self.asked: float | None = None

        server_class = type("_Server", (_Server,), {"address_family": family})
        try:
            self._server = server_class((host, port), _handler(self))
        except OSError:
            self._server = server_class((host, 0), _handler(self))
            log.warning("Port %d is in use; the status page is on port %d instead",
                        port, self._server.server_address[1])
        self.port = self._server.server_address[1]
        self.urls = [f"http://{h}:{self.port}" for h in reachable_hosts(host, family)]
        self.url = self.urls[0]
        self._thread = threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.2},
                                        name="blaeckpy status page", daemon=True)
        self._thread.start()

    def refresh(self, device) -> None:
        now = time.monotonic()
        if now - self._taken >= REFRESH:
            self._taken = now
            # One assignment: the server thread sees the old snapshot or the new, never a mix.
            self.data = snapshot(device, self.asked is not None and now - self.asked < VIEWING)

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join()
