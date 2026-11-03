"""The status page: what /api shows, the page and its stylesheet, the port, and that tick()
keeps it current. Also how blaeckpy's log lines look on a terminal."""

import contextlib
import io
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


def test_close_stops_the_page(server):
    device = Blaeck("Bench")
    device.begin(server, status_port=0)
    url = device._status.url
    device.close()
    assert device._status is None
    with pytest.raises(urllib.error.URLError):
        get(url + "/api")


def test_no_status_page_with_none_and_a_bad_port_is_refused(server):
    device = Blaeck("Bench")
    device.begin(server, status_port=None)
    assert device._status is None
    device.close()
    for bad in (-1, 65536, True, "8080"):
        with pytest.raises(ValueError):
            Blaeck("Bench").begin(server, status_port=bad)


def test_a_serial_device_listens_everywhere():
    device = Blaeck("Bench")
    device.begin(SerialPort(), status_port=None)
    assert _status.bind_address(device._transport) == ("0.0.0.0", socket.AF_INET)
    data = _status.snapshot(device)
    assert data["connection"] == "Serial COM7 at 115200 baud" and data["clients"] is None
    device.close()


def load_value(data) -> str:
    return data["devices"][0]["entities"][0]["value"]


def test_a_get_is_read_for_the_page_only_while_it_is_open(server, monkeypatch):
    monkeypatch.setattr(_status, "REFRESH", 0)
    device, calls = Blaeck("Bench"), []
    device.add_sensor("Load", float, get=lambda: calls.append(1) or 42.0)
    device.begin(server, status_port=0)
    try:
        # Nobody looks and no host listens: the function isn't called.
        for _ in range(5):
            device.tick()
        assert calls == []
        assert load_value(api(device)) == "0.0"

        # The page asked, so the next snapshot reads it.
        device.tick()
        assert calls and load_value(api(device)) == "42.0"

        # Nobody has asked for a while: it stops again.
        monkeypatch.setattr(_status, "VIEWING", 0)
        calls.clear()
        for _ in range(5):
            device.tick()
        assert calls == []
    finally:
        device.close()


def test_a_get_is_read_once_in_a_pass_and_a_raising_one_is_logged_once(caplog):
    device, readings = Blaeck("Bench"), [1.5]

    def load():
        return readings.append(readings[-1]) or readings[-1] if readings[-1] else 1 / 0

    device.add_sensor("Load", float, get=load)
    device.begin(SerialPort(), status_port=None)
    first = _status.snapshot(device, viewing=True)
    second = _status.snapshot(device, viewing=True)
    assert load_value(first) == load_value(second) == "1.5" and len(readings) == 2

    readings.append(0)
    with caplog.at_level(logging.ERROR, logger="blaeckpy"):
        for _ in range(3):
            device.tick()
            assert load_value(_status.snapshot(device, viewing=True)) == "1.5"
    assert len([r for r in caplog.records if "Load" in r.getMessage()]) == 1
    device.close()


@pytest.mark.parametrize("host, family, shown", [
    ("0.0.0.0", socket.AF_INET, ["127.0.0.1", "192.168.1.20"]),
    ("::", socket.AF_INET6, ["[::1]", "192.168.1.20"]),
    ("192.168.1.5", socket.AF_INET, ["192.168.1.5"]),
    ("fe80::1", socket.AF_INET6, ["[fe80::1]"])])
def test_all_addresses_are_shown_as_this_computer_and_the_network(host, family, shown, monkeypatch):
    monkeypatch.setattr(_status, "network_address", lambda family: "192.168.1.20")
    assert _status.reachable_hosts(host, family) == shown


def test_without_a_network_only_this_computer_is_shown(monkeypatch):
    monkeypatch.setattr(_status, "network_address", lambda family: None)
    assert _status.reachable_hosts("0.0.0.0", socket.AF_INET) == ["127.0.0.1"]


def test_listening_on_all_addresses_lists_where_to_connect(monkeypatch, caplog):
    monkeypatch.setattr(_status, "network_address", lambda family: "192.168.1.20")
    server = socket.create_server(("", 0))
    port = server.getsockname()[1]
    device = Blaeck("Bench")
    try:
        with caplog.at_level(logging.INFO, logger="blaeckpy"):
            device.begin(server, status_port=0)
        lines = caplog.messages
        assert lines[0].endswith("'Bench' listening on all addresses (0.0.0.0)")
        assert lines[1:3] == [f"  TCP 127.0.0.1:{port}", f"  TCP 192.168.1.20:{port}"]
        page = device._status.port
        assert lines[3:6] == ["Status page:", f"  http://127.0.0.1:{page}", f"  http://192.168.1.20:{page}"]
        assert api(device)["connection"] == f"TCP 0.0.0.0:{port} (127.0.0.1, 192.168.1.20)"
    finally:
        device.close()
        server.close()


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


@contextlib.contextmanager
def console(monkeypatch, stderr):
    """blaeckpy's logger as a script without any logging setup has it, on the given stderr. Used
    in a test's body: pytest adds its own handlers to the root logger once the fixtures have run."""
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    root = logging.getLogger()
    handlers, root.handlers = root.handlers, []
    try:
        with contextlib.redirect_stderr(stderr):
            yield logging.getLogger("blaeckpy")
    finally:
        root.handlers = handlers


def test_a_closed_earlier_stderr_doesnt_stop_the_log(monkeypatch, tmp_path):
    # A real file: flushing a closed StringIO raises nothing.
    now = io.StringIO()
    with open(tmp_path / "log", "w") as gone, console(monkeypatch, gone) as log:
        log.info("one")
    assert (tmp_path / "log").read_text() == "one\n"
    with console(monkeypatch, now) as log:
        log.info("two")
        log.info("three")
    assert now.getvalue() == "two\nthree\n"


@pytest.mark.parametrize("closed", [True, False], ids=["closed", "none"])
def test_a_stderr_that_cant_be_written_doesnt_stop_the_log(monkeypatch, closed):
    stderr = io.StringIO() if closed else None
    if closed:
        stderr.close()
    with console(monkeypatch, stderr) as log:
        log.info("one")
        log.info("two")


def test_colour_goes_with_the_stderr_of_the_moment(monkeypatch):
    class Tty(io.StringIO):
        def isatty(self):
            return True

    tty, plain = Tty(), io.StringIO()
    with console(monkeypatch, tty) as log:
        log.info("one")
    with console(monkeypatch, plain) as log:
        log.info("two")
    assert "\033[" in tty.getvalue() and plain.getvalue() == "two\n"
