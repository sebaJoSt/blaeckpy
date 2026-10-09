"""Markers, escaping, CRC32: what every frame shares.

A frame on the wire::

    <blaeck: KEY(1B) : MSGID(4B) : FIELDS CRC32(4B) /> LF

The CRC32 covers the unescaped bytes from KEY to the last field. Everything between the start
marker and ``/>`` is escaped, the CRC included.
"""

from __future__ import annotations

import zlib
from typing import NamedTuple

START = b"<blaeck:"
END = b"/>\n"

ESCAPE = 0x5C
# A start marker, an escape, CR or LF inside a frame is sent as the escape byte followed by the
# byte XOR 0x20, so a raw LF always ends a frame and a raw < always starts one.
_ESCAPED = frozenset({0x3C, 0x5C, 0x0D, 0x0A})


def escape(data: bytes) -> bytes:
    """The bytes as they are sent between the markers."""
    out = bytearray()
    for b in data:
        if b in _ESCAPED:
            out.append(ESCAPE)
            out.append(b ^ 0x20)
        else:
            out.append(b)
    return bytes(out)


def unescape(data: bytes) -> bytes:
    """The bytes as they were before :func:`escape`.

    Raises :class:`ValueError` for what a sender never writes: an escape at the end, an escape
    followed by a byte that was not escaped, or a raw ``<``, which means the frame was cut off.
    """
    out = bytearray()
    i = 0
    while i < len(data):
        b = data[i]
        if b == 0x3C:
            raise ValueError("raw '<' inside a frame: it was cut off")
        if b == ESCAPE:
            if i + 1 >= len(data):
                raise ValueError("escape at the end of a frame")
            original = data[i + 1] ^ 0x20
            if original not in _ESCAPED:
                raise ValueError(f"escape before 0x{data[i + 1]:02X}, which is never escaped")
            out.append(original)
            i += 2
        else:
            out.append(b)
            i += 1
    return bytes(out)


def crc32(data: bytes) -> int:
    """CRC-32/ISO-HDLC, as Ethernet and ZIP use it."""
    return zlib.crc32(data) & 0xFFFFFFFF


def build(key: int, message_id: int, fields: bytes) -> bytes:
    """A whole frame, markers to LF.

    ``message_id`` is the id of the command the frame answers, or 0 if no command asked for it.
    """
    if not 0 <= key <= 0xFF:
        raise ValueError(f"key {key} does not fit a byte")
    if not 0 <= message_id <= 0xFFFFFFFF:
        raise ValueError(f"message id {message_id} does not fit 4 bytes")
    body = bytes((key, 0x3A)) + message_id.to_bytes(4, "little") + b":" + fields
    body += crc32(body).to_bytes(4, "little")
    return START + escape(body) + END


class Envelope(NamedTuple):
    """A frame's key, message id and fields, its CRC checked and removed."""

    key: int
    message_id: int
    fields: bytes


def open_frame(frame: bytes) -> Envelope:
    """The inverse of :func:`build`: one frame, markers to LF, or without the LF.

    Raises :class:`ValueError` if the markers, the separators or the CRC are wrong.
    """
    if frame.endswith(b"\n"):
        frame = frame[:-1]
    if not frame.startswith(START) or not frame.endswith(b"/>"):
        raise ValueError("not a frame: start or end marker missing")
    body = unescape(frame[len(START):-2])
    if len(body) < 11 or body[1] != 0x3A or body[6] != 0x3A:
        raise ValueError("not a frame: too short, or a ':' separator missing")
    payload, crc = body[:-4], int.from_bytes(body[-4:], "little")
    if crc32(payload) != crc:
        raise ValueError(f"CRC mismatch: frame says 0x{crc:08X}, bytes give 0x{crc32(payload):08X}")
    return Envelope(body[0], int.from_bytes(body[2:6], "little"), payload[7:])
