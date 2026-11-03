"""The frame builders against what blaeck itself sent, byte for byte.

tests/fixtures/blaeck_session.txt holds the frames blaeck sent while playing
tests/fixtures/blaeck_session.cpp. Each frame is rebuilt here from what that program declared;
a difference of one byte fails. The golden scenarios replace this once they exist.
"""

from pathlib import Path

import pytest

from blaeckpy.protocol import frames
from blaeckpy.protocol.frames import DType, ValueKind
from blaeckpy.protocol.schema_hash import schema_hash

FIXTURE = Path(__file__).parent / "fixtures" / "blaeck_session.txt"


def _recorded() -> list[tuple[str, bytes]]:
    lines = [line for line in FIXTURE.read_text().splitlines() if line and not line.startswith("#")]
    return [(label, bytes.fromhex(hex_)) for label, hex_ in (line.rsplit(" ", 1) for line in lines)]


def _ack(command: str, reason: int = 0, message_id: int = 0) -> bytes:
    name = command.split(",", 1)[0]
    return frames.command_ack(frames.fnv1a32(command.encode()), frames.fnv1a32(name.encode()),
                              reason, message_id)


ENTITIES = [
    frames.property_entry(0, "Setpoint", ValueKind.NUMBER, DType.FLOAT, 20.0, writable=True,
                          range_min=10.0, range_max=30.0, step=0.5, unit="°C", mode=2),
    frames.property_entry(0, "Heater", ValueKind.BOOL, DType.BOOL, False, writable=True),
    frames.property_entry(0, "Mode", ValueKind.ENUM, DType.BYTE, 1, writable=True,
                          options=["Auto", "Manual"]),
    frames.property_entry(0, "Note", ValueKind.TEXT, DType.TEXT, "a<b", writable=True,
                          text_max_len=32),
    frames.property_entry(0, "Room", ValueKind.NUMBER, DType.FLOAT, 19.75, writable=False,
                          display_name="Room air", icon="mdi:home", device_class="temperature",
                          state_class=1, display_precision=1),
    frames.event_entry(1, "Alarms", ["restarted"]),
    frames.event_entry(0, "Alarms", ["Overheat", "Dry run"], display_name="Alarm", category=1),
    frames.button_entry(0, "PrintStatus", icon="mdi:printer"),
]

SIGNALS = [
    (None, "Temperature", "", DType.FLOAT),
    (None, "Count", "", DType.INT64),
    (None, "Label", "", DType.TEXT),
    (None, "Flag", "", DType.BOOL),
    ("Pump controller", "Flow", "", DType.FLOAT),
]

EXPECTED = [
    ("<#7:BLAECK.GET_DEVICES>", frames.device_notification(0, frames.DeviceEvent.RESTARTED)),
    ("<#7:BLAECK.GET_DEVICES>", frames.entity_list(ENTITIES)),
    ("<#7:BLAECK.GET_DEVICES>", _ack("BLAECK.GET_DEVICES", message_id=7)),
    ("<#7:BLAECK.GET_DEVICES>", frames.device_list("blaeck", "1.0.0", 127, [
        frames.DeviceEntry(0, "Greenhouse", "n/a", "1.0",
                           [("Temperature", "", DType.FLOAT), ("Count", "", DType.INT64),
                            ("Label", "", DType.TEXT), ("Flag", "", DType.BOOL)]),
        frames.DeviceEntry(1, "Pump controller", "Simulated", "1.0", [("Flow", "", DType.FLOAT)]),
    ], message_id=7)),
    ("<BLAECK.WRITE_ENTITIES>", _ack("BLAECK.WRITE_ENTITIES")),
    ("<BLAECK.WRITE_ENTITIES>", frames.entity_list(ENTITIES)),
    ("<BLAECK.DATA_START>", _ack("BLAECK.DATA_START")),
    ("<BLAECK.WRITE_DATA>", _ack("BLAECK.WRITE_DATA")),
    ("<BLAECK.WRITE_DATA>", frames.data(
        [(0, DType.FLOAT, 21.5), (1, DType.INT64, 7), (2, DType.TEXT, "hi"),
         (3, DType.BOOL, True), (4, DType.FLOAT, 1.25)],
        schema_hash=schema_hash(SIGNALS),
        flags=frames.FIRST_AFTER_RESTART | frames.ANSWERS_WRITE_DATA)),
    ("<BLAECK.ENTITIES_START>", _ack("BLAECK.ENTITIES_START")),
    ("<BLAECK.ENTITIES_START>", frames.property_value(0, DType.FLOAT, 20.0)),
    ("<BLAECK.ENTITIES_START>", frames.property_value(1, DType.BOOL, False)),
    ("<BLAECK.ENTITIES_START>", frames.property_value(2, DType.BYTE, 1)),
    ("<BLAECK.ENTITIES_START>", frames.property_value(3, DType.TEXT, "a<b")),
    ("<BLAECK.ENTITIES_START>", frames.property_value(4, DType.FLOAT, 19.75)),
    ("<Setpoint,25>", _ack("Setpoint,25")),
    ("<Setpoint,25>", frames.property_value(0, DType.FLOAT, 25.0)),
    ("event", frames.event(1, 1)),
    ("<Nope,1>", _ack("Nope,1", reason=1)),
]


def test_the_session_has_the_frames_expected():
    assert [label for label, _ in _recorded()] == [label for label, _ in EXPECTED]


@pytest.mark.parametrize("index", range(len(EXPECTED)), ids=lambda i: f"{i}-{EXPECTED[i][0]}")
def test_frame_matches_blaeck_byte_for_byte(index):
    assert EXPECTED[index][1] == _recorded()[index][1]
