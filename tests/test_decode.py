"""The decoder: it reads every frame blaeck recorded, inverts the builders, and takes a stream in
any pieces."""

from pathlib import Path

import pytest

from blaeckpy.protocol import _decode, frames
from blaeckpy.protocol.frames import DType, ValueKind

GOLDEN = Path(__file__).parent / "golden" / "frames"


def recorded_stream(path: Path) -> bytes:
    lines = [l for l in path.read_text(encoding="utf-8").splitlines() if l and not l.startswith("#")]
    return b"".join(bytes.fromhex(l.split("\t")[1]) for l in lines)


@pytest.mark.parametrize("path", sorted(GOLDEN.glob("*.txt")), ids=lambda p: p.stem)
def test_every_frame_blaeck_recorded_decodes(path):
    decoder = _decode.Decoder()
    out = decoder.feed(recorded_stream(path))
    assert out and all(isinstance(f, _decode.Frame) for f in out)
    assert not any(isinstance(f.body, _decode.Unknown) for f in out)


def test_a_stream_decodes_the_same_in_any_pieces_with_text_between():
    stream = (b"hello\n" + frames.device_list("blaeckpy", "1.0.0", 127, [
        frames.DeviceEntry(0, "Bench", "n/a", "1.0", [("T", "°C", DType.DOUBLE), ("S", "", DType.TEXT)])])
        + b"terminal <text>\n"
        + frames.data([(0, DType.DOUBLE, 21.5), (1, DType.TEXT, "a<b\n")], schema_hash=7)
        + frames.event(1, 2))
    whole = _decode.Decoder().feed(stream)
    piecewise_decoder, piecewise = _decode.Decoder(), []
    for b in stream:
        piecewise += piecewise_decoder.feed(bytes([b]))
    joined = lambda items: [x if isinstance(x, _decode.Frame) else ("text", x) for x in items]
    texts = lambda items: "".join(x for x in items if isinstance(x, str))
    assert [type(x) for x in joined(whole) if not isinstance(x, tuple)] == \
           [type(x) for x in joined(piecewise) if not isinstance(x, tuple)]
    assert texts(whole) == texts(piecewise) == "hello\nterminal <text>\n"
    data = [f for f in whole if isinstance(f, _decode.Frame) and f.key == frames.Key.DATA][0].body
    assert data.values == {0: 21.5, 1: "a<b\n"} and data.schema_hash == 7


def test_device_list_round_trip():
    devices = [frames.DeviceEntry(0, "Greenhouse", "n/a", "1.0", [("T", "°C", DType.DOUBLE)]),
               frames.DeviceEntry(1, "Pump", "Sim", "2.0", [("Flow", "L/min", DType.INT64)],
                                  not_responding=True, restarted=True)]
    body = _decode.decode(frames.device_list("blaeckpy", "1.0.0", 127, devices, message_id=9))
    assert body.message_id == 9
    dl = body.body
    assert (dl.lib_name, dl.lib_version, dl.command_payload_max) == ("blaeckpy", "1.0.0", 127)
    assert [s.name for s in dl.signals] == ["T", "Flow"]
    assert dl.devices[1].not_responding and dl.devices[1].restarted and not dl.devices[0].restarted


def test_entity_list_round_trip():
    entries = [
        frames.property_entry(0, "Setpoint", ValueKind.NUMBER, DType.DOUBLE, 20.0, writable=True,
                              range_min=10.0, range_max=30.0, step=0.5, unit="°C", mode=2,
                              display_name="Set", icon="mdi:x", device_class="temperature",
                              state_class=1, display_precision=1, category=1,
                              disabled_by_default=True, force_update=True),
        frames.property_entry(1, "Mode", ValueKind.ENUM, DType.BYTE, 1, writable=True,
                              options=["a,b", "50%", "c"]),
        frames.property_entry(0, "Note", ValueKind.TEXT, DType.TEXT, "hi", writable=False,
                              text_max_len=255),
        frames.event_entry(0, "Alarms", ["x", "y"], display_name="A", category=2),
        frames.button_entry(1, "Reset", icon="mdi:restore", disabled_by_default=True),
    ]
    el = _decode.decode(frames.entity_list(entries)).body
    p = el.properties[0]
    assert (p.name, p.value, p.range_min, p.range_max, p.step, p.unit, p.mode) == \
           ("Setpoint", 20.0, 10.0, 30.0, 0.5, "°C", 2)
    assert p.writable and p.state_class == 1 and p.category == 1 and p.display_precision == 1
    assert p.disabled_by_default and p.force_update and p.device_class == "temperature"
    assert el.properties[1].options == ["a,b", "50%", "c"] and el.properties[1].device_id == 1
    assert el.properties[2].text_max_len == 255 and not el.properties[2].writable
    assert el.events[0].types == ["x", "y"] and el.events[0].category == 2
    assert el.buttons[0].icon == "mdi:restore" and el.buttons[0].disabled_by_default


def test_an_unknown_entry_kind_and_unknown_trailing_fields_are_stepped_over():
    known = frames.button_entry(0, "Reset")
    longer = frames.Entry(0, frames.EntryKind.BUTTON, known.fields + b"future")
    unknown = frames.Entry(0, 7, b"whatever comes")
    el = _decode.decode(frames.entity_list([unknown, longer, known])).body
    assert el.skipped == 1 and [b.name for b in el.buttons] == ["Reset", "Reset"]


def test_small_frames_round_trip():
    assert _decode.decode(frames.command_ack(1, 2, 9, message_id=4)).body == _decode.CommandAck(1, 2, 1, 9)
    assert _decode.decode(frames.property_value(3, DType.BOOL, True)).body == \
           _decode.PropertyValue(3, DType.BOOL, True)
    assert _decode.decode(frames.event(2, 5)).body == _decode.EventValue(2, 5)
    assert _decode.decode(frames.device_notification(1, 2)).body == _decode.DeviceNotification(1, 2)


def test_a_data_frame_needs_its_device_list():
    with pytest.raises(ValueError):
        _decode.decode(frames.data([(0, DType.DOUBLE, 1.0)], schema_hash=0))


def test_a_signal_named_twice_in_one_data_frame_is_refused():
    signals = [_decode.Signal("T", "", DType.FLOAT)]
    frame = frames.data([(0, DType.FLOAT, 1.0), (0, DType.FLOAT, 2.0)], schema_hash=0)
    with pytest.raises(ValueError, match="twice"):
        _decode.decode(frame, signals)


def test_a_frame_keeps_its_raw_fields():
    frame = _decode.decode(frames.event(1, 2))
    assert frame.fields == b"\x01\x00\x02\x00"


def test_a_stream_hands_over_a_data_frame_before_any_device_list_unread():
    out = _decode.Decoder().feed(frames.data([(0, DType.FLOAT, 1.0)], schema_hash=0))
    assert out[0].key == frames.Key.DATA and isinstance(out[0].body, _decode.Unknown)
