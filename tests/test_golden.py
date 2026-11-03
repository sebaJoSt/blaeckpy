"""blaeckpy plays blaeck's golden scenarios and must send the same frames, byte for byte.

tests/golden/ is copied from blaeck by scripts/copy_golden.py. Each scenario is declared here
through blaeckpy's public API, played against a fake port and a fixed clock, and every frame is
compared with what blaeck sent. A scenario that needs what blaeckpy does not have yet is skipped,
naming what is missing.
"""

import json
from pathlib import Path

import pytest

import blaeckpy
from blaeckpy import _device
from blaeckpy._signal import ALWAYS, ANY_CHANGE, OFF, OnChange
from blaeckpy.protocol import envelope

GOLDEN = Path(__file__).parent / "golden"
SCENARIOS = sorted((GOLDEN / "scenarios").glob("*.json"))

# What blaeck's golden program declares, in place of blaeckpy's own name and version.
BLAECK_LIBRARY = ("blaeck", "7.0.0")


class FakePort:
    """A serial port: what the host sends waits in ``incoming``, what the device writes lands
    in ``outgoing``."""

    def __init__(self):
        self.incoming = bytearray()
        self.outgoing = bytearray()

    @property
    def in_waiting(self):
        return len(self.incoming)

    def read(self, n):
        data = bytes(self.incoming[:n])
        del self.incoming[:n]
        return data

    def write(self, data):
        self.outgoing += data
        return len(data)


class FakeClock:
    """Starts at 0 and moves only when a step says so, as in blaeck's golden program."""

    def __init__(self):
        self.ms = 0

    def millis(self):
        return self.ms

    def micros(self):
        return self.ms * 1000


SUPPORTED_STEPS = {"command", "set_signal", "advance_ms", "tick", "write_all"}


def missing_features(scenario):
    missing = [key for key in ("properties", "events", "buttons", "commands", "devices")
               if scenario.get(key)]
    missing += sorted({next(iter(s)) for s in scenario["steps"]} - SUPPORTED_STEPS)
    if scenario.get("timestamp_mode") == "unix":
        missing.append("unix timestamps")
    return missing


def interval_policy(spec):
    if spec == "always":
        return ALWAYS
    if spec == "off":
        return OFF
    return OnChange(spec["on_change"])


def declare(scenario):
    board = scenario.get("board", {})
    device = blaeckpy.Blaeck(board.get("name", "Unnamed"),
                             hw_version=board.get("hw_version", "n/a"),
                             fw_version=board.get("fw_version", "n/a"))
    device._clock = FakeClock()
    if scenario.get("timestamp_mode") == "micros":
        device.set_timestamp_mode(blaeckpy.TimestampMode.MICROS)
    signals = {}
    for s in scenario.get("signals", []):
        type_ = {"bool": bool, "int": int, "float": float, "str": str}[s["type"]]
        kwargs = {}
        if "write_at_interval" in s:
            kwargs["write_at_interval"] = interval_policy(s["write_at_interval"])
        if "write_on_change" in s and s["write_on_change"] != "off":
            delta = s["write_on_change"]
            kwargs["write_on_change"] = ANY_CHANGE if delta == "any" else float(delta)
            kwargs["min_interval_ms"] = s.get("min_interval_ms", 100)
        default = {"bool": False, "int": 0, "float": 0.0, "str": ""}[s["type"]]
        signals[s["name"]] = device.add_signal(s["name"], type_, s.get("value", default),
                                               unit=s.get("unit"), **kwargs)
    return device, signals


def play(scenario):
    device, signals = declare(scenario)
    port = FakePort()
    device.begin(port)
    recorded = []

    def collect(label):
        data = bytes(port.outgoing)
        port.outgoing.clear()
        for frame in data.split(b"/>\n")[:-1]:
            recorded.append((label, frame + b"/>\n"))
        assert data.endswith(b"/>\n") or not data, f"{label}: a frame left unfinished"

    for i, step in enumerate(scenario["steps"]):
        (kind, arg), *_ = step.items()
        label = f"{i} {kind}" + (f" {arg}" if not isinstance(arg, (dict, list)) else "")
        if kind == "command":
            port.incoming += arg.encode("utf-8")
            device.tick()
        elif kind == "set_signal":
            signals[arg].value = step["value"]
            device.tick()
        elif kind == "advance_ms":
            device._clock.ms += int(arg)
            device.tick()
        elif kind == "tick":
            device.tick()
        elif kind == "write_all":
            device.write_all()
        collect(label)
    return recorded


def blaeck_frames(name):
    lines = (GOLDEN / "frames" / f"{name}.txt").read_text(encoding="utf-8").splitlines()
    return [(label, bytes.fromhex(hex_)) for label, hex_ in
            (line.split("\t") for line in lines if line and not line.startswith("#"))]


def describe(frame):
    try:
        e = envelope.open_frame(frame)
        return f"key 0x{e.key:02X} id {e.message_id} fields {e.fields.hex(' ')}"
    except ValueError as err:
        return f"unreadable ({err}): {frame.hex(' ')}"


@pytest.mark.parametrize("path", SCENARIOS, ids=lambda p: p.stem)
def test_scenario_sends_what_blaeck_sends(path, monkeypatch):
    scenario = json.loads(path.read_text(encoding="utf-8"))
    missing = missing_features(scenario)
    if missing:
        pytest.skip("needs " + ", ".join(missing))
    monkeypatch.setattr(_device, "LIBRARY_NAME", BLAECK_LIBRARY[0])
    monkeypatch.setattr(_device, "LIBRARY_VERSION", BLAECK_LIBRARY[1])

    expected = blaeck_frames(path.stem)
    actual = play(scenario)
    for n, (want, got) in enumerate(zip(expected, actual)):
        assert got == want, (
            f"frame {n}, blaeck sent it at step '{want[0]}', blaeckpy at '{got[0]}':\n"
            f"  blaeck:   {describe(want[1])}\n  blaeckpy: {describe(got[1])}")
    assert len(actual) == len(expected), (
        f"blaeck sent {len(expected)} frames, blaeckpy {len(actual)}; first extra or missing: "
        f"{(expected + actual)[min(len(expected), len(actual))][0]}")
