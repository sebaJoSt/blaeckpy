"""The schema hash a data frame carries: which signal list it was written with.

CRC-16/XMODEM over the signals in device-list order, each as the device list sends it: its name,
0x00 and its DTYPE, with a sub-device's name and 0x00 in front.
"""

from __future__ import annotations

from collections.abc import Iterable


def crc16_xmodem(data: bytes, crc: int = 0) -> int:
    """CRC-16/XMODEM: polynomial 0x1021, initial value 0, not reflected."""
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else crc << 1
            crc &= 0xFFFF
    return crc


def schema_hash(signals: Iterable[tuple[str | None, str, int]]) -> int:
    """The hash of ``(device_name, signal_name, dtype)`` triples, in device-list order.

    ``device_name`` is ``None`` for the board's own signals. No signals give 0.
    """
    crc = 0
    for device_name, signal_name, dtype in signals:
        if device_name is not None:
            crc = crc16_xmodem(device_name.encode("utf-8") + b"\x00", crc)
        crc = crc16_xmodem(signal_name.encode("utf-8") + b"\x00" + bytes((dtype,)), crc)
    return crc
