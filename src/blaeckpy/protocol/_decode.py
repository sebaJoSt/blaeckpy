"""Bytes in, frames out. Internal, for blaeckpy's tests and blaeck's harness scripts; no stability
promise.

The inverse of the frame builders: split a byte stream at the markers, unescape, check the CRC,
and read each frame's fields into a small dataclass. A data frame's values are read with the
types of the last device list seen, as a host reads them.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from . import envelope
from .frames import DType, EntryKind, Key, ValueKind

_FORMATS = {
    DType.BOOL: "<B", DType.BYTE: "<B", DType.SHORT: "<h", DType.USHORT: "<H",
    DType.INT: "<h", DType.UINT: "<H", DType.LONG: "<i", DType.ULONG: "<I",
    DType.FLOAT: "<f", DType.DOUBLE: "<d", DType.INT64: "<q",
}


class _Reader:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.at = 0

    def take(self, n: int) -> bytes:
        if self.at + n > len(self.data):
            raise ValueError(f"frame ends {self.at + n - len(self.data)} bytes early")
        chunk = self.data[self.at:self.at + n]
        self.at += n
        return chunk

    def u8(self) -> int:
        return self.take(1)[0]

    def u16(self) -> int:
        return int.from_bytes(self.take(2), "little")

    def u32(self) -> int:
        return int.from_bytes(self.take(4), "little")

    def u64(self) -> int:
        return int.from_bytes(self.take(8), "little")

    def string(self) -> str:
        end = self.data.find(b"\x00", self.at)
        if end < 0:
            raise ValueError("a string has no terminating 0")
        text = self.data[self.at:end].decode("utf-8", "surrogateescape")
        self.at = end + 1
        return text

    def value(self, dtype: int):
        dtype = DType(dtype)
        if dtype is DType.TEXT:
            return self.take(self.u8()).decode("utf-8", "surrogateescape")
        if dtype is DType.BOOL:
            return self.u8() != 0
        return struct.unpack(_FORMATS[dtype], self.take(struct.calcsize(_FORMATS[dtype])))[0]

    @property
    def done(self) -> bool:
        return self.at >= len(self.data)


# ----- B7 Device List -----


@dataclass
class Signal:
    name: str
    unit: str
    dtype: int


@dataclass
class Device:
    device_id: int
    parent_id: int
    flags: int
    state: int
    name: str
    hw_version: str
    fw_version: str
    signals: list[Signal]

    @property
    def not_responding(self) -> bool:
        return bool(self.state & 0x01)

    @property
    def restarted(self) -> bool:
        return bool(self.state & 0x02)


@dataclass
class DeviceList:
    lib_name: str
    lib_version: str
    command_payload_max: int
    devices: list[Device]

    @property
    def signals(self) -> list[Signal]:
        """Every signal in device-list order, as data frames number them."""
        return [s for d in self.devices for s in d.signals]


def _device_list(r: _Reader) -> DeviceList:
    lib_name, lib_version = r.string(), r.string()
    payload_max, count = r.u16(), r.u8()
    devices = []
    for _ in range(count):
        device_id, parent_id, flags, state = r.u8(), r.u8(), r.u16(), r.u8()
        name, hw, fw = r.string(), r.string(), r.string()
        for _bit in range(bin(flags).count("1")):
            r.string()  # optional fields; none are defined yet
        signals = [Signal(r.string(), r.string(), r.u8()) for _ in range(r.u16())]
        devices.append(Device(device_id, parent_id, flags, state, name, hw, fw, signals))
    return DeviceList(lib_name, lib_version, payload_max, devices)


# ----- D3 Data -----


@dataclass
class Data:
    flags: int
    schema_hash: int
    timestamp_mode: int
    timestamp: int | None
    #: ``signal index -> value``, in the order the frame carries them.
    values: dict[int, object]

    @property
    def first_after_restart(self) -> bool:
        return bool(self.flags & 0x01)

    @property
    def answers_write_data(self) -> bool:
        return bool(self.flags & 0x02)

    @property
    def interval_report(self) -> bool:
        return bool(self.flags & 0x04)


def _data(r: _Reader, signals: list[Signal] | None) -> Data:
    flags, schema_hash, mode = r.u8(), r.u16(), r.u8()
    timestamp = r.u64() if mode else None
    values = {}
    while not r.done:
        index = r.u16()
        if index in values:
            raise ValueError(f"signal {index} twice in one data frame")
        if signals is None or index >= len(signals):
            raise ValueError(f"signal {index} is not in the last device list")
        values[index] = r.value(signals[index].dtype)
    return Data(flags, schema_hash, mode, timestamp, values)


# ----- C1, A5, 95, 85 -----


@dataclass
class DeviceNotification:
    device_id: int
    event: int


@dataclass
class CommandAck:
    cmd_hash: int
    cmd_name_hash: int
    status: int
    reason: int


@dataclass
class PropertyValue:
    index: int
    dtype: int
    value: object


@dataclass
class EventValue:
    event_index: int
    type_index: int


# ----- 90 Entity List -----


def _decode_option(option: str) -> str:
    out, i, raw = bytearray(), 0, option.encode("utf-8", "surrogateescape")
    while i < len(raw):
        if raw[i] == 0x25 and i + 2 < len(raw):
            try:
                value = int(raw[i + 1:i + 3], 16)
            except ValueError:
                value = 0
            if value:
                out.append(value)
                i += 3
                continue
        out.append(raw[i])
        i += 1
    return out.decode("utf-8", "surrogateescape").strip(" \t\r\n")


@dataclass
class PropertyEntry:
    device_id: int
    name: str
    value_kind: int
    flags: int
    dtype: int
    value: object
    options: list[str] | None = None
    text_max_len: int | None = None
    range_min: object = None
    range_max: object = None
    step: object = None
    unit: str | None = None
    display_name: str | None = None
    icon: str | None = None
    device_class: str | None = None
    display_precision: int | None = None

    @property
    def writable(self) -> bool:
        return self.flags & 0x3 == 0x3

    @property
    def state_class(self) -> int:
        return self.flags >> 8 & 0x7

    @property
    def category(self) -> int:
        return self.flags >> 12 & 0x3

    @property
    def disabled_by_default(self) -> bool:
        return bool(self.flags & 1 << 14)

    @property
    def force_update(self) -> bool:
        return bool(self.flags & 1 << 15)

    @property
    def mode(self) -> int:
        return self.flags >> 16 & 0x3


@dataclass
class EventEntry:
    device_id: int
    name: str
    flags: int
    types: list[str]
    display_name: str | None = None
    icon: str | None = None
    device_class: str | None = None

    @property
    def category(self) -> int:
        return self.flags >> 3 & 0x3

    @property
    def disabled_by_default(self) -> bool:
        return bool(self.flags & 0x20)


@dataclass
class ButtonEntry:
    device_id: int
    name: str
    flags: int
    display_name: str | None = None
    icon: str | None = None
    device_class: str | None = None

    @property
    def category(self) -> int:
        return self.flags >> 3 & 0x3

    @property
    def disabled_by_default(self) -> bool:
        return bool(self.flags & 0x20)


@dataclass
class EntityList:
    properties: list[PropertyEntry] = field(default_factory=list)
    events: list[EventEntry] = field(default_factory=list)
    buttons: list[ButtonEntry] = field(default_factory=list)
    #: Entries of a kind this decoder does not know, stepped over by their length.
    skipped: int = 0


def _shown(r: _Reader, flags: int, bits: tuple[int, int, int]) -> tuple:
    return tuple(r.string() if flags & bit else None for bit in bits)


def _property(device_id: int, r: _Reader) -> PropertyEntry:
    name, kind, flags, dtype = r.string(), r.u8(), r.u32(), r.u8()
    p = PropertyEntry(device_id, name, kind, flags, dtype, r.value(dtype))
    if kind == ValueKind.ENUM:
        p.options = [_decode_option(o) for o in r.string().split(",")]
    if kind == ValueKind.TEXT:
        p.text_max_len = r.u8()
    if flags & 1 << 2:
        p.range_min, p.range_max = r.value(dtype), r.value(dtype)
    if flags & 1 << 3:
        p.step = r.value(dtype)
    if flags & 1 << 4:
        p.unit = r.string()
    p.display_name, p.icon, p.device_class = _shown(r, flags, (1 << 5, 1 << 6, 1 << 7))
    if flags & 1 << 11:
        p.display_precision = r.u8()
    return p


def _entity_list(r: _Reader) -> EntityList:
    result = EntityList()
    while not r.done:
        device_id, kind, length = r.u8(), r.u8(), r.u16()
        entry = _Reader(r.take(length))
        # Fields past the ones known here are skipped: the length says where the next entry starts.
        if kind == EntryKind.PROPERTY:
            result.properties.append(_property(device_id, entry))
        elif kind == EntryKind.EVENT:
            name, flags = entry.string(), entry.u16()
            shown = _shown(entry, flags, (0x1, 0x2, 0x4))
            types = [entry.string() for _ in range(entry.u16())]
            result.events.append(EventEntry(device_id, name, flags, types, *shown))
        elif kind == EntryKind.BUTTON:
            name, flags = entry.string(), entry.u16()
            result.buttons.append(ButtonEntry(device_id, name, flags, *_shown(entry, flags, (0x1, 0x2, 0x4))))
        else:
            result.skipped += 1
    return result


# ----- frames and streams -----


@dataclass
class Frame:
    key: int
    message_id: int
    body: object
    #: The frame's fields as sent, unescaped, CRC removed: for checks that compare bytes.
    fields: bytes = b""


@dataclass
class Unknown:
    fields: bytes


def decode(frame: bytes, signals: list[Signal] | None = None) -> Frame:
    """One whole frame, markers to LF. A data frame needs the signals of the device list it was
    written with; :class:`Decoder` keeps them for a stream."""
    opened = envelope.open_frame(frame)
    r = _Reader(opened.fields)
    key = opened.key
    body: object
    if key == Key.DEVICE_LIST:
        body = _device_list(r)
    elif key == Key.DATA:
        body = _data(r, signals)
    elif key == Key.DEVICE_NOTIFICATION:
        body = DeviceNotification(r.u8(), r.u8())
    elif key == Key.COMMAND_ACK:
        body = CommandAck(r.u32(), r.u32(), r.u8(), r.u8())
    elif key == Key.PROPERTY:
        index, dtype = r.u16(), r.u8()
        body = PropertyValue(index, dtype, r.value(dtype))
    elif key == Key.EVENT:
        body = EventValue(r.u16(), r.u16())
    elif key == Key.ENTITY_LIST:
        body = _entity_list(r)
    else:
        body = Unknown(opened.fields)
    return Frame(key, opened.message_id, body, opened.fields)


class Decoder:
    """Reads a device's byte stream: frames, and the terminal text between them. A data frame
    that arrives before any device list is handed over with an :class:`Unknown` body."""

    def __init__(self) -> None:
        self._buffer = bytearray()
        self.device_list: DeviceList | None = None

    def feed(self, data: bytes) -> list[Frame | str]:
        """What the bytes so far complete: a :class:`Frame` each, and terminal text as str.
        An unfinished frame waits for the next call."""
        self._buffer += data
        out: list[Frame | str] = []
        while self._buffer:
            start = self._buffer.find(envelope.START)
            if start < 0:
                # Text to the end, but for what may be the beginning of a marker still arriving.
                cut = len(self._buffer)
                for k in range(min(len(envelope.START) - 1, len(self._buffer)), 0, -1):
                    if envelope.START.startswith(self._buffer[-k:]):
                        cut -= k
                        break
                if cut:
                    out.append(bytes(self._buffer[:cut]).decode("utf-8", "replace"))
                    del self._buffer[:cut]
                break
            if start > 0:
                out.append(bytes(self._buffer[:start]).decode("utf-8", "replace"))
                del self._buffer[:start]
            end = self._buffer.find(envelope.END)
            if end < 0:
                break
            frame = bytes(self._buffer[:end + len(envelope.END)])
            del self._buffer[:end + len(envelope.END)]
            if self.device_list is None and frame[len(envelope.START)] == Key.DATA:
                # No device list seen yet, so its values can't be read: handed over unread.
                opened = envelope.open_frame(frame)
                decoded = Frame(opened.key, opened.message_id, Unknown(opened.fields), opened.fields)
            else:
                decoded = decode(frame, self.device_list.signals if self.device_list else None)
            if isinstance(decoded.body, DeviceList):
                self.device_list = decoded.body
            out.append(decoded)
        return out
