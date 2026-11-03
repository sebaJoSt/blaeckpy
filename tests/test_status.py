"""The status page: what /api shows, the page and its stylesheet, the port, and that tick()
keeps it current. Also how blaeckpy's log lines look on a terminal."""

import json
import logging
import socket
import urllib.error
import urllib.request

import pytest

from blaeckpy import Blaeck, _console, _status


def get(url: str) -> tuple[str, bytes]:
    with urllib.request.urlopen(url, timeout=2) as r:
        return r.headers["Content-Type"], r.read()


def api(device) -> dict:
    return json.loads(get(device._status.url + "/api")[1])


@pytest.fixture
def server():
    s = socket.create_server(("127.0.0.1", 0))
    yield s
    s.close()


class SerialPort:
    port = "COM7"
    baudrate = 115200
    in_waiting = 0

    def read(self, n):
        return b""

    def write(self, data):
        return len(data)


def test_api_shows_the_device_its_signals_and_entities(server, monkeypatch):
    monkeypatch.setattr(_status, "REFRESH", 0)
    device = Blaeck("Bench", hw_version="Pi 5", fw_version="2.1")
    temp = device.add_signal("Temp", 21.5, unit="°C")
    device.add_number_input("Setpoint", 40.0, unit="°C")
    device.add_select("Mode", 1, options=["Off", "Heat"])
    alarm = device.add_event("Alarm", ["Low", "High"])
    pump = device.add_device("Pump", fw_version="0.3")
    pump.add_signal("Flow", int)
    device.begin(server, status_port=0)
    try:
        data = api(device)
        assert data["device"] == {"name": "Bench", "hw_version": "Pi 5", "fw_version": "2.1"}
        assert data["connection"] == "TCP 127.0.0.1:%d" % server.getsockname()[1]
        assert data["data"] == "Stopped" and data["clients"] == [] and data["last_command"] is None
        board, sub = data["devices"]
        assert board["signals"] == [{"name": "Temp", "type": "float", "unit": "°C", "value": "21.5"}]
        assert [(e["name"], e["kind"], e["value"], e["unit"]) for e in board["entities"]] == [
            ("Setpoint", "Number", "40.0", "°C"), ("Mode", "Select", "Heat", None),
            ("Alarm", "Event", None, None)]
        assert sub["name"] == "Pump" and sub["signals"][0]["name"] == "Flow" and not sub["missing"]

        # What changes in the script shows once tick() has run.
        temp.value = float("nan")
        alarm.write("High")
        pump.mark_missing()
        client = socket.create_connection(server.getsockname()[:2], timeout=1)
        client.sendall(b"<BLAECK.DATA_START><BLAECK.INTERVAL_START,500>")
        for _ in range(20):
            device.tick(0.01)
        data = api(device)
        board, sub = data["devices"]
        assert board["signals"][0]["value"] == "NaN"
        assert board["entities"][2]["value"] == "High" and board["entities"][2]["at"] <= data["now"]
        assert sub["missing"]
        assert data["data"] == "Running, every 500 ms"
        assert data["last_command"]["name"] == "BLAECK.INTERVAL_START"
        assert [c["role"] for c in data["clients"]] == ["Host"]
        client.close()
    finally:
        device.close()


def test_the_page_and_its_stylesheet_are_served(server):
    device = Blaeck("Bench")
    device.begin(server, status_port=0)
    try:
        kind, page = get(device._status.url + "/")
        assert kind.startswith("text/html") and b"<title>Bench" in page and b"/pico.min.css" in page
        assert b"$" not in page.split(b"<script>")[0]
        kind, css = get(device._status.url + "/pico.min.css")
        assert kind == "text/css" and len(css) > 50_000
        with pytest.raises(urllib.error.HTTPError):
            get(device._status.url + "/nothing")
    finally:
        device.close()


def test_a_port_in_use_moves_the_page_to_a_free_one(server, caplog):
    taken = socket.create_server(("127.0.0.1", 0))
    port = taken.getsockname()[1]
    device = Blaeck("Bench")
    try:
        with caplog.at_level(logging.INFO, logger="blaeckpy"):
            device.begin(server, status_port=port)
        assert device._status.port != port
        assert f"Port {port} is in use" in caplog.text
        assert f"Status page: http://127.0.0.1:{device._status.port}" in caplog.text
        assert "'Bench' listening on TCP 127.0.0.1:" in caplog.text
    finally:
        device.close()
        taken.close()


def test_close_stops_the_page_and_begin_starts_it_again(server):
    device = Blaeck("Bench")
    device.begin(server, status_port=0)
    url = device._status.url
    device.close()
    assert device._status is None
    with pytest.raises(urllib.error.URLError):
        get(url + "/api")
    device.begin(server, status_port=0)
    try:
        assert api(device)["device"]["name"] == "Bench"
    finally:
        device.close()


def test_no_status_page_with_none_and_a_bad_port_is_refused(server):
    device = Blaeck("Bench")
    device.begin(server, status_port=None)
    assert device._status is None
    device.close()
    for bad in (-1, 65536, True, "8080"):
        with pytest.raises(ValueError):
            device.begin(server, status_port=bad)


def test_a_serial_device_listens_everywhere_and_get_is_not_read_for_the_page():
    device = Blaeck("Bench")
    calls = []
    device.add_sensor("Load", float, get=lambda: calls.append(1) or 0.5)
    device.begin(SerialPort(), status_port=None)
    assert _status.bind_address(device._transport) == ("0.0.0.0", socket.AF_INET)
    data = _status.snapshot(device)
    assert data["connection"] == "Serial COM7 at 115200 baud" and data["clients"] is None
    assert calls == []
    device.close()


@pytest.mark.parametrize("host, shown", [("0.0.0.0", "127.0.0.1"), ("::", "[::1]"),
                                         ("192.168.1.5", "192.168.1.5"), ("fe80::1", "[fe80::1]")])
def test_the_link_reaches_the_page_from_this_computer(host, shown):
    assert _status.url_host(host) == shown


def test_colour_is_by_level_and_a_styled_line_brings_its_own():
    record = logging.LogRecord("blaeckpy", logging.WARNING, "", 0, "careful", None, None)
    assert _console._Formatter(False).format(record) == "careful"
    assert _console._Formatter(True).format(record) == "\033[33mcareful\033[0m"
    record = logging.LogRecord("blaeckpy", logging.INFO, "", 0, "Status page: x", None, None)
    record.__dict__.update(_console.styled("Status page: " + _console.LINK + "x"))
    assert _console._Formatter(False).format(record) == "Status page: x"
    assert _console._Formatter(True).format(record) == "Status page: \033[94;4mx\033[0m"


def test_no_colour_with_no_color_or_off_a_terminal(monkeypatch):
    class Tty:
        def isatty(self):
            return True

    monkeypatch.delenv("FORCE_COLOR", raising=False)
    monkeypatch.setenv("NO_COLOR", "1")
    assert not _console.use_color(Tty())
    monkeypatch.delenv("NO_COLOR")
    assert not _console.use_color(object())
