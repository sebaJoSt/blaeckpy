"""The protocol core against the numbers and examples the spec itself gives."""

import pytest

from blaeckpy.protocol import envelope, frames
from blaeckpy.protocol.frames import DType
from blaeckpy.protocol.schema_hash import crc16_xmodem, schema_hash


def test_crc32_check_value():
    assert envelope.crc32(b"123456789") == 0xCBF43926


def test_crc16_xmodem_check_value():
    assert crc16_xmodem(b"123456789") == 0x31C3


@pytest.mark.parametrize(
    "raw, sent",
    [(b"<", b"\x5c\x1c"), (b"\\", b"\x5c\x7c"), (b"\r", b"\x5c\x2d"), (b"\n", b"\x5c\x2a"), (b">", b">")],
)
def test_escaping_table(raw, sent):
    assert envelope.escape(raw) == sent
    assert envelope.unescape(sent) == raw


def test_a_ten_byte_text_has_its_length_escaped():
    # Datatypes: a text of 10 bytes has the length 0x0A, which is sent as 5C 2A.
    assert envelope.escape(frames.text_value("0123456789"))[:2] == b"\x5c\x2a"


def test_schema_hash_worked_example():
    signals = [(None, "Temperature", DType.FLOAT), ("Zone A", "Flow", DType.ULONG)]
    assert schema_hash(signals) == 0xE658


def test_no_signals_hash_to_zero():
    assert schema_hash([]) == 0


@pytest.mark.parametrize("cut", [b"\x5c", b"ab\x5c\x41", b"a<b"])
def test_unescape_refuses_what_a_sender_never_writes(cut):
    with pytest.raises(ValueError):
        envelope.unescape(cut)


def test_a_frame_opens_to_what_built_it():
    frame = frames.event(3, 1, message_id=0x0A0D3C5C)  # an id made of bytes that need escaping
    assert frame.startswith(b"<blaeck:") and frame.endswith(b"/>\n")
    assert frame.count(b"\n") == 1
    opened = envelope.open_frame(frame)
    assert opened == envelope.Envelope(0x85, 0x0A0D3C5C, b"\x03\x00\x01\x00")


def test_a_changed_byte_fails_the_crc():
    frame = bytearray(frames.event(3, 1))
    frame[len(envelope.START) + 7] ^= 0x01
    with pytest.raises(ValueError, match="CRC"):
        envelope.open_frame(bytes(frame))


def test_text_longer_than_255_bytes_is_refused():
    frames.text_value("x" * 255)
    with pytest.raises(ValueError):
        frames.text_value("x" * 256)


def test_a_timestamp_goes_with_its_mode():
    with pytest.raises(ValueError):
        frames.data([], schema_hash=0, timestamp_mode=frames.TimestampMode.MICROS)
    with pytest.raises(ValueError):
        frames.data([], schema_hash=0, timestamp=5)
