"""Signals and their reporting policy."""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass

from .protocol.frames import DType

_DTYPES = {bool: DType.BOOL, int: DType.INT64, float: DType.DOUBLE, str: DType.TEXT}
_DEFAULTS = {bool: False, int: 0, float: 0.0, str: ""}

INT64_MIN, INT64_MAX = -(2**63), 2**63 - 1
TEXT_MAX = 255

#: A change of any size counts, for ``write_on_change=`` and :class:`OnChange`.
ANY_CHANGE = 0.0


class _Mode:
    def __init__(self, name: str) -> None:
        self._name = name

    def __repr__(self) -> str:
        return self._name


#: In every interval report: the default.
ALWAYS = _Mode("ALWAYS")
#: In no interval report.
OFF = _Mode("OFF")


@dataclass(frozen=True)
class OnChange:
    """In an interval report only if the value moved by at least ``delta`` since last sent."""

    delta: float = ANY_CHANGE


def type_and_value(type_or_initial: object, initial: object) -> tuple[type, object]:
    """A type and its starting value, from ``(float)``, ``(float, 21.5)`` or ``(21.5)``."""
    if isinstance(type_or_initial, type):
        if type_or_initial not in _DTYPES:
            raise TypeError(f"a value is a bool, int, float or str, not {type_or_initial.__name__}")
        type_ = type_or_initial
        value = _DEFAULTS[type_] if initial is None else initial
    else:
        if initial is not None:
            raise TypeError("give a type and a starting value, or a starting value alone")
        value = type_or_initial
        # bool before int: True is an int too.
        type_ = next((t for t in (bool, int, float, str) if isinstance(value, t)), None)
        if type_ is None:
            raise TypeError(f"a value is a bool, int, float or str, not {type(value).__name__}")
    return type_, checked(type_, value, "starting value")


def checked(type_: type, value: object, what: str = "value") -> object:
    """The value as the type holds it; TypeError or ValueError for what it cannot hold."""
    if type_ is bool:
        if not isinstance(value, bool):
            raise TypeError(f"a bool {what} is True or False, not {value!r}")
        return value
    if type_ is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"an int {what} is a whole number, not {value!r}")
        if not INT64_MIN <= value <= INT64_MAX:
            raise ValueError(f"{value} does not fit 64 bits")
        return value
    if type_ is float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"a float {what} is a number, not {value!r}")
        return float(value)
    if not isinstance(value, str):
        raise TypeError(f"a str {what} is text, not {value!r}")
    if len(value.encode("utf-8")) > TEXT_MAX:
        raise ValueError(f"a text holds at most {TEXT_MAX} bytes")
    return value


def check_unit(unit: object) -> str:
    """A signal's unit: "" for none, otherwise text without control characters."""
    if unit is None:
        return ""
    if not isinstance(unit, str):
        raise TypeError(f"a unit is a str, not {type(unit).__name__}")
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in unit):
        raise ValueError(f"unit {unit!r} holds a control character")
    return unit


def check_name(name: object, what: str) -> str:
    if not isinstance(name, str):
        raise TypeError(f"a {what} name is a str, not {type(name).__name__}")
    if not name:
        raise ValueError(f"a {what} name cannot be empty")
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in name):
        raise ValueError(f"{what} name {name!r} holds a control character")
    return name


class _Reporting:
    """The baseline both policies compare against: the value last sent, and when."""

    def __init__(self) -> None:
        self.valid = False
        self.baseline: object = None
        self.last_write_ms = 0
        self.immediate = False
        self.change_delta = ANY_CHANGE
        self.min_interval_ms = 100
        self.interval_delta = ANY_CHANGE


def _same_bits(a: float, b: float) -> bool:
    return struct.pack("<d", a) == struct.pack("<d", b)


def changed(type_: type, value: object, reporting: _Reporting, delta: float) -> bool:
    """Whether value moved by at least delta from the baseline; always without one."""
    if not reporting.valid:
        return True
    previous = reporting.baseline
    if type_ is bool or type_ is str:
        return value != previous
    if type_ is int:
        if value == previous:
            return False
        if delta >= 2.0**64:
            return False
        return abs(value - previous) >= math.ceil(delta)
    # float: as blaeck compares doubles.
    if _same_bits(value, previous):
        return False
    min_normal = 2.2250738585072014e-308
    if (math.isnan(value) or math.isnan(previous) or math.isinf(value) or math.isinf(previous)
            or (value != 0 and abs(value) < min_normal) or (previous != 0 and abs(previous) < min_normal)):
        return True
    return abs(value - previous) >= delta


class Signal:
    """A value the device logs. Set :attr:`value`; the device sends it when it is due."""

    def __init__(self, device, device_id: int, name: str, type_: type, value: object,
                 write_at_interval, write_on_change, min_interval_ms: int, unit: str = "") -> None:
        self._device = device
        self.device_id = device_id
        self.name = name
        self.unit = unit
        self._removed = False
        self.type = type_
        self.dtype = _DTYPES[type_]
        self._value = value
        if write_at_interval is not ALWAYS and write_at_interval is not OFF \
                and not isinstance(write_at_interval, OnChange):
            raise TypeError("write_at_interval is ALWAYS, OFF or OnChange(delta)")
        self.interval_mode = write_at_interval
        # Only a policy that compares keeps a baseline, as in blaeck.
        self._reporting: _Reporting | None = None
        if isinstance(write_at_interval, OnChange):
            self._ensure_reporting().interval_delta = float(write_at_interval.delta)
        if write_on_change is not None:
            r = self._ensure_reporting()
            r.immediate = True
            r.change_delta = float(write_on_change)
            r.min_interval_ms = int(min_interval_ms)

    def _ensure_reporting(self) -> _Reporting:
        if self._reporting is None:
            self._reporting = _Reporting()
        return self._reporting

    @property
    def value(self):
        return self._value

    @value.setter
    def value(self, value) -> None:
        self._value = checked(self.type, value)

    def write(self, value=None, timestamp: int | None = None) -> None:
        """Sends the signal now, set to ``value`` if one is given."""
        if value is not None:
            self.value = value
        if self._removed:
            raise RuntimeError(f"signal {self.name!r} was removed by clear_all_signals()")
        self._device._write_signal_now(self, timestamp)

    def __repr__(self) -> str:
        return f"Signal({self.name!r}, {self.type.__name__}, {self._value!r})"
