"""blaeckpy plays blaeck's golden scenarios and must send the same frames, byte for byte.

tests/golden/ is copied from blaeck by scripts/copy_golden.py. Each scenario is declared here
through blaeckpy's public API, played against a fake port and a fixed clock, and every frame is
compared with what blaeck sent. A scenario that needs what blaeckpy does not have yet is skipped,
naming what is missing.
"""

import dataclasses
import json
from pathlib import Path

import pytest

import blaeckpy
from blaeckpy import _commands, _device
from blaeckpy._signal import ALWAYS, ANY_CHANGE, OFF, OnChange
from blaeckpy.protocol import _decode

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


SUPPORTED_STEPS = {"command", "set_signal", "set_property", "advance_ms", "tick", "write_all",
                   "write_event", "mark_missing", "mark_present", "write_restarted",
                   "clear_all_signals", "clear_all_controls", "clear_all_sensors", "clear_all_events",
                   "clear_all_commands"}


def missing_features(scenario):
    missing = []
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


PRESENTATION = ("display_name", "icon", "device_class", "category", "disabled_by_default")
NUMBER_PRESENTATION = PRESENTATION + ("unit", "state_class", "display_precision", "force_update")
PY_TYPES = {"bool": bool, "int": int, "float": float, "str": str}


def pick(spec, keys):
    return {k: spec[k] for k in keys if k in spec}


def declare_property(device, p, signals):
    kind, name = p["kind"], p["name"]
    reporting = {}
    if "write_on_change" in p:
        reporting["write_on_change"] = float(p["write_on_change"])
    if "min_interval_ms" in p:
        reporting["min_interval_ms"] = p["min_interval_ms"]
    if kind == "number_input":
        return device.add_number_input(name, PY_TYPES[p["type"]], p.get("value"),
                                       **pick(p, ("min", "max", "step", "mode")),
                                       **pick(p, NUMBER_PRESENTATION), **reporting)
    if kind == "switch":
        return device.add_switch(name, p.get("value", False), **pick(p, PRESENTATION + ("force_update",)))
    if kind == "select":
        return device.add_select(name, p.get("value", 0), options=p["options"],
                                 **pick(p, PRESENTATION + ("force_update",)))
    if kind == "text_input":
        return device.add_text_input(name, p.get("value", ""), max_length=p["max_length"],
                                     **pick(p, ("mode",) + PRESENTATION + ("force_update",)))
    if kind == "sensor":
        presentation = pick(p, NUMBER_PRESENTATION)
        if "signal" in p:
            return device.add_sensor(name, signals[p["signal"]], **presentation, **reporting)
        if "options" in p:
            return device.add_sensor(name, p.get("value", 0), options=p["options"], **presentation,
                                     **reporting)
        type_ = PY_TYPES[p["type"]]
        default = {"bool": False, "int": 0, "float": 0.0, "str": ""}[p["type"]]
        return device.add_sensor(name, type_, p.get("value", default), **presentation, **reporting)
    raise AssertionError(f"property kind {kind}")


def declare(scenario):
    board = scenario.get("board", {})
    device = blaeckpy.Blaeck(board.get("name", "Unnamed"),
                             hw_version=board.get("hw_version", "n/a"),
                             fw_version=board.get("fw_version", "n/a"))
    device._clock = FakeClock()
    if scenario.get("timestamp_mode") == "micros":
        device.set_timestamp_mode(blaeckpy.TimestampMode.MICROS)
    signals, properties, events = {}, {}, {}
    declare_entities(device, None, scenario, signals, properties, events)
    devices = {}
    for d in scenario.get("devices", []):
        sub = device.add_device(d["name"], hw_version=d.get("hw_version", "n/a"),
                                fw_version=d.get("fw_version", "n/a"))
        devices[d["name"]] = sub
        declare_entities(sub, d["name"], d, signals, properties, events)
    for c in scenario.get("commands", []):
        device.on_command(c, lambda *params: None)
    return device, signals, properties, events, devices


def declare_entities(device, device_name, holder, signals, properties, events):
    for s in holder.get("signals", []):
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
    for p in holder.get("properties", []):
        properties[p["name"]] = declare_property(device, p, signals)
    for e in holder.get("events", []):
        events[(device_name, e["name"])] = device.add_event(e["name"], e["types"], **pick(e, PRESENTATION))
    for b in holder.get("buttons", []):
        device.add_button(b["name"], lambda: None, **pick(b, PRESENTATION))


def play(scenario):
    device, signals, properties, events, devices = declare(scenario)
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
        elif kind == "set_property":
            properties[arg].value = step["value"]
            device.tick()
        elif kind == "write_event":
            events[(step.get("device"), arg[0])].write(arg[1])
        elif kind == "mark_missing":
            devices[arg].mark_missing()
        elif kind == "mark_present":
            devices[arg].mark_present()
        elif kind == "write_restarted":
            devices[arg].write_restarted()
        elif kind.startswith("clear_all_"):
            getattr(device, kind)()
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


def describe(want: bytes, got: bytes, signals) -> str:
    """Where two frames differ, field by field, read with the decoder."""
    try:
        a, b = _decode.decode(want, signals), _decode.decode(got, signals)
    except ValueError as err:
        return f"  blaeck:   {want.hex(' ')}\n  blaeckpy: {got.hex(' ')}\n  ({err})"
    if a.key != b.key or a.message_id != b.message_id:
        return (f"  blaeck:   key 0x{a.key:02X} message id {a.message_id}\n"
                f"  blaeckpy: key 0x{b.key:02X} message id {b.message_id}")
    left, right = dataclasses.asdict(a.body), dataclasses.asdict(b.body)
    lines = [f"  {k}: blaeck {left[k]!r}, blaeckpy {right.get(k)!r}"
             for k in left if left[k] != right.get(k)]
    return f"  key 0x{a.key:02X}, fields that differ:\n" + "\n".join(lines)


@pytest.mark.parametrize("path", SCENARIOS, ids=lambda p: p.stem)
def test_scenario_sends_what_blaeck_sends(path, monkeypatch):
    scenario = json.loads(path.read_text(encoding="utf-8"))
    missing = missing_features(scenario)
    if missing:
        pytest.skip("needs " + ", ".join(missing))
    monkeypatch.setattr(_device, "LIBRARY_NAME", BLAECK_LIBRARY[0])
    monkeypatch.setattr(_device, "LIBRARY_VERSION", BLAECK_LIBRARY[1])
    # blaeck's default command buffer, which its device list advertises.
    monkeypatch.setattr(_commands, "COMMAND_MAX_CHARS", 128)

    expected = blaeck_frames(path.stem)
    actual = play(scenario)
    signals = None
    for n, (want, got) in enumerate(zip(expected, actual)):
        assert got == want, (
            f"frame {n}, blaeck sent it at step '{want[0]}', blaeckpy at '{got[0]}':\n"
            + describe(want[1], got[1], signals))
        body = _decode.decode(want[1], signals).body
        if isinstance(body, _decode.DeviceList):
            signals = body.signals
    assert len(actual) == len(expected), (
        f"blaeck sent {len(expected)} frames, blaeckpy {len(actual)}; first extra or missing: "
        f"{(expected + actual)[min(len(expected), len(actual))][0]}")
