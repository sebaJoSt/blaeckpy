"""One function per frame: the fields as bytes, wrapped in the envelope.

Pure functions with no I/O. Each takes what the frame says and returns the whole frame, markers to
LF, ready to write. Strings are null-terminated UTF-8, except text values (DTYPE 10), which are
length-prefixed.
"""

from __future__ import annotations

import struct
from collections.abc import Iterable, Sequence
from enum import IntEnum
from typing import NamedTuple

from . import envelope


class Key(IntEnum):
    """Message keys of the frames a device sends."""

    EVENT = 0x85
    ENTITY_LIST = 0x90
    PROPERTY = 0x95
    COMMAND_ACK = 0xA5
    DEVICE_LIST = 0xB7
    DEVICE_NOTIFICATION = 0xC1
    DATA = 0xD3


class DType(IntEnum):
    """Datatype codes. blaeckpy sends BOOL, INT64, DOUBLE and TEXT; the rest are blaeck's."""

    BOOL = 0
    BYTE = 1
    SHORT = 2
    USHORT = 3
    INT = 4
    UINT = 5
    LONG = 6
    ULONG = 7
    FLOAT = 8
    DOUBLE = 9
    TEXT = 10
    INT64 = 11


# Fixed-width types and how they are packed, little-endian.
_FORMATS = {
    DType.BOOL: "<B",
    DType.BYTE: "<B",
    DType.SHORT: "<h",
    DType.USHORT: "<H",
    DType.INT: "<h",
    DType.UINT: "<H",
    DType.LONG: "<i",
    DType.ULONG: "<I",
    DType.FLOAT: "<f",
    DType.DOUBLE: "<d",
    DType.INT64: "<q",
}


class ValueKind(IntEnum):
    NUMBER = 0
    BOOL = 1
    ENUM = 2
    TEXT = 3


class EntryKind(IntEnum):
    PROPERTY = 0
    EVENT = 1
    BUTTON = 2


class DeviceEvent(IntEnum):
    RESTARTED = 0x01
    NOT_RESPONDING = 0x02
    RESPONDING_AGAIN = 0x03


class TimestampMode(IntEnum):
    NONE = 0
    MICROS = 1
    UNIX = 2


# Data frame flags.
FIRST_AFTER_RESTART = 0x01
ANSWERS_WRITE_DATA = 0x02
INTERVAL_REPORT = 0x04

TEXT_MAX = 255

# What a device drops around a command parameter, an option or an event type.
TRIM = " \t\r\n"


# ----- Field encoders -----


def u8(value: int) -> bytes:
    return value.to_bytes(1, "little")


def u16(value: int) -> bytes:
    return value.to_bytes(2, "little")


def u32(value: int) -> bytes:
    return value.to_bytes(4, "little")


def u64(value: int) -> bytes:
    return value.to_bytes(8, "little")


def string(text: str) -> bytes:
    """A null-terminated UTF-8 string, as names, versions and options are sent."""
    data = text.encode("utf-8")
    if b"\x00" in data:
        raise ValueError(f"{text!r} holds a null byte, which would end it early")
    return data + b"\x00"


def encode_option(option: str) -> str:
    """An option as the entity list sends it: a host splits the list on its commas and then
    decodes each one, so a comma or percent sign inside an option is percent-encoded."""
    return option.replace("%", "%25").replace(",", "%2C")


def check_listed(items: Sequence[str], what: str) -> None:
    """Options and event types: at least one, none blank, none with outer spaces."""
    if not items:
        raise ValueError(f"needs at least one {what}")
    for item in items:
        if not item.strip(TRIM):
            raise ValueError(f"a blank {what}")
        if item != item.strip(TRIM):
            raise ValueError(f"{what} {item!r} has leading or trailing spaces")


def text_value(text: str) -> bytes:
    """A DTYPE 10 value: a 1-byte length, then that many UTF-8 bytes. Bytes that came in as
    something else go back out unchanged, as blaeck sends them."""
    data = text.encode("utf-8", "surrogateescape")
    if len(data) > TEXT_MAX:
        raise ValueError(f"text is {len(data)} bytes; at most {TEXT_MAX} fit")
    return u8(len(data)) + data


def value(dtype: int, v: object) -> bytes:
    """A value as data frames, property frames and the entity list carry it."""
    dtype = DType(dtype)
    if dtype is DType.TEXT:
        if not isinstance(v, str):
            raise TypeError(f"a text value is a str, not {type(v).__name__}")
        return text_value(v)
    if dtype is DType.BOOL:
        if not isinstance(v, bool):
            raise TypeError(f"a bool value is a bool, not {type(v).__name__}")
        return u8(int(v))
    return struct.pack(_FORMATS[dtype], v)


# ----- B7 Device List -----


class DeviceEntry(NamedTuple):
    """One device in the device list: the board (id 0) or a sub-device. Its signals are
    ``(name, unit, dtype)``, the unit "" for none."""

    device_id: int
    name: str
    hw_version: str
    fw_version: str
    signals: Sequence[tuple[str, str, int]] = ()
    parent_id: int = 0
    not_responding: bool = False
    restarted: bool = False


def device_list(
    lib_name: str,
    lib_version: str,
    command_payload_max: int,
    devices: Sequence[DeviceEntry],
    message_id: int = 0,
) -> bytes:
    """The board and its sub-devices, each with its signals as ``(name, unit, dtype)``."""
    if not 1 <= len(devices) <= 255:
        raise ValueError(f"{len(devices)} devices; a device list holds 1 to 255")
    fields = bytearray()
    fields += string(lib_name) + string(lib_version) + u16(command_payload_max) + u8(len(devices))
    for d in devices:
        state = (0x01 if d.not_responding else 0) | (0x02 if d.restarted else 0)
        # DeviceFlags announces optional fields; none are defined yet.
        fields += u8(d.device_id) + u8(d.parent_id) + u16(0) + u8(state)
        fields += string(d.name) + string(d.hw_version) + string(d.fw_version)
        fields += u16(len(d.signals))
        for name, unit, dtype in d.signals:
            fields += string(name) + string(unit) + u8(dtype)
    return envelope.build(Key.DEVICE_LIST, message_id, bytes(fields))


# ----- D3 Data -----


def data(
    values: Iterable[tuple[int, int, object]],
    *,
    schema_hash: int,
    flags: int = 0,
    timestamp_mode: int = TimestampMode.NONE,
    timestamp: int | None = None,
    message_id: int = 0,
) -> bytes:
    """Signal values as ``(signal_index, dtype, value)``; the timestamp only with a mode."""
    if (timestamp_mode == TimestampMode.NONE) != (timestamp is None):
        raise ValueError("a timestamp goes with a timestamp mode, and only with one")
    fields = bytearray(u8(flags) + u16(schema_hash) + u8(timestamp_mode))
    if timestamp is not None:
        fields += u64(timestamp)
    for index, dtype, v in values:
        fields += u16(index) + value(dtype, v)
    return envelope.build(Key.DATA, message_id, bytes(fields))


# ----- C1 Device Notification, A5 Command Ack, 95 Property, 85 Event -----


def device_notification(device_id: int, event: int, message_id: int = 0) -> bytes:
    return envelope.build(Key.DEVICE_NOTIFICATION, message_id, u8(device_id) + u8(event))


def fnv1a32(data: bytes) -> int:
    """FNV-1a, 32 bits: how an ack names the command it answers."""
    h = 0x811C9DC5
    for byte in data:
        h = ((h ^ byte) * 0x01000193) & 0xFFFFFFFF
    return h


def command_ack(cmd_hash: int, cmd_name_hash: int, reason: int, message_id: int = 0) -> bytes:
    """The answer to a command; reason 0 is accepted, any other rejected."""
    status = 0 if reason == 0 else 1
    fields = u32(cmd_hash) + u32(cmd_name_hash) + u8(status) + u8(reason)
    return envelope.build(Key.COMMAND_ACK, message_id, fields)


def property_value(index: int, dtype: int, v: object, message_id: int = 0) -> bytes:
    """A property's current value, addressed by its position among the properties."""
    return envelope.build(Key.PROPERTY, message_id, u16(index) + u8(dtype) + value(dtype, v))


def event(event_index: int, type_index: int, message_id: int = 0) -> bytes:
    return envelope.build(Key.EVENT, message_id, u16(event_index) + u16(type_index))


# ----- 90 Entity List -----


class Entry(NamedTuple):
    """One entry of the entity list: its device, its kind and its fields."""

    device_id: int
    kind: int
    fields: bytes


# Property flags.
_ACCESS_READ = 0x1
_ACCESS_READWRITE = 0x3
_HAS_RANGE = 1 << 2
_HAS_STEP = 1 << 3
_HAS_UNIT = 1 << 4
_HAS_DISPLAY_NAME = 1 << 5
_HAS_ICON = 1 << 6
_HAS_DEVICE_CLASS = 1 << 7
_HAS_DISPLAY_PRECISION = 1 << 11
_DISABLED_BY_DEFAULT = 1 << 14
_FORCE_UPDATE = 1 << 15


def _presentation(display_name: str | None, icon: str | None, device_class: str | None,
                  bits: tuple[int, int, int]) -> tuple[int, bytes]:
    flags, fields = 0, b""
    for text, bit in zip((display_name, icon, device_class), bits):
        if text is not None:
            flags |= bit
            fields += string(text)
    return flags, fields


def property_entry(
    device_id: int,
    name: str,
    value_kind: int,
    dtype: int,
    v: object,
    *,
    writable: bool,
    options: Sequence[str] | None = None,
    text_max_len: int | None = None,
    range_min: object = None,
    range_max: object = None,
    step: object = None,
    unit: str | None = None,
    display_name: str | None = None,
    icon: str | None = None,
    device_class: str | None = None,
    state_class: int = 0,
    display_precision: int | None = None,
    category: int = 0,
    disabled_by_default: bool = False,
    force_update: bool = False,
    mode: int = 0,
) -> Entry:
    """An input (writable) or a sensor. Kind-bound fields follow the value, then the optional
    ones in flag-bit order."""
    value_kind = ValueKind(value_kind)
    if (value_kind is ValueKind.ENUM) != (options is not None):
        raise ValueError("options go with an enum, and only with one")
    if (value_kind is ValueKind.TEXT) != (text_max_len is not None):
        raise ValueError("text_max_len goes with a text, and only with one")
    if (range_min is None) != (range_max is None):
        raise ValueError("a range has both limits or none")

    flags = _ACCESS_READWRITE if writable else _ACCESS_READ
    flags |= (state_class & 0x7) << 8 | (category & 0x3) << 12 | (mode & 0x3) << 16
    if disabled_by_default:
        flags |= _DISABLED_BY_DEFAULT
    if force_update:
        flags |= _FORCE_UPDATE

    tail = b""
    if range_min is not None:
        flags |= _HAS_RANGE
        tail += value(dtype, range_min) + value(dtype, range_max)
    if step is not None:
        flags |= _HAS_STEP
        tail += value(dtype, step)
    if unit is not None:
        flags |= _HAS_UNIT
        tail += string(unit)
    shown_flags, shown = _presentation(display_name, icon, device_class,
                                       (_HAS_DISPLAY_NAME, _HAS_ICON, _HAS_DEVICE_CLASS))
    flags |= shown_flags
    tail += shown
    if display_precision is not None:
        flags |= _HAS_DISPLAY_PRECISION
        tail += u8(display_precision)

    fields = string(name) + u8(value_kind) + u32(flags) + u8(dtype) + value(dtype, v)
    if options is not None:
        check_listed(options, "option")
        fields += string(",".join(encode_option(o) for o in options))
    if text_max_len is not None:
        fields += u8(text_max_len)
    return Entry(device_id, EntryKind.PROPERTY, fields + tail)


def _moment_flags(category: int, disabled_by_default: bool) -> int:
    return (category & 0x3) << 3 | (0x20 if disabled_by_default else 0)


def event_entry(
    device_id: int,
    name: str,
    types: Sequence[str],
    *,
    display_name: str | None = None,
    icon: str | None = None,
    device_class: str | None = None,
    category: int = 0,
    disabled_by_default: bool = False,
) -> Entry:
    """An event with the closed list of its types; a type's position is its index."""
    check_listed(types, "event type")
    shown_flags, shown = _presentation(display_name, icon, device_class, (0x1, 0x2, 0x4))
    flags = shown_flags | _moment_flags(category, disabled_by_default)
    fields = string(name) + u16(flags) + shown + u16(len(types))
    fields += b"".join(string(t) for t in types)
    return Entry(device_id, EntryKind.EVENT, fields)


def button_entry(
    device_id: int,
    name: str,
    *,
    display_name: str | None = None,
    icon: str | None = None,
    device_class: str | None = None,
    category: int = 0,
    disabled_by_default: bool = False,
) -> Entry:
    shown_flags, shown = _presentation(display_name, icon, device_class, (0x1, 0x2, 0x4))
    flags = shown_flags | _moment_flags(category, disabled_by_default)
    return Entry(device_id, EntryKind.BUTTON, string(name) + u16(flags) + shown)


def entity_list(entries: Iterable[Entry], message_id: int = 0) -> bytes:
    """The entries in the order given: properties, then events, then buttons, as declared.

    Each entry carries its length, so a host can step over a kind it does not know.
    """
    fields = bytearray()
    for e in entries:
        if len(e.fields) > 0xFFFF:
            raise ValueError(f"an entry of {len(e.fields)} bytes; its length is 2 bytes")
        fields += u8(e.device_id) + u8(e.kind) + u16(len(e.fields)) + e.fields
    return envelope.build(Key.ENTITY_LIST, message_id, bytes(fields))
