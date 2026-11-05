"""Signals and their reporting policy."""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from typing import Any

from ._clock import check_timestamp
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


def check_delta(delta: object, what: str = "a change threshold") -> float:
    """A change threshold: a number, 0 or above and finite."""
    if isinstance(delta, bool) or not isinstance(delta, (int, float)):
        raise TypeError(f"{what} is a number, not {delta!r}")
    delta = float(delta)
    if not delta >= 0 or math.isinf(delta):
        raise ValueError(f"{what} is 0 or above and finite, not {delta!r}")
    return delta


@dataclass(frozen=True)
class OnChange:
    """In an interval report only if the value moved by at least ``delta`` since last sent."""

    delta: float = ANY_CHANGE

    def __post_init__(self) -> None:
        check_delta(self.delta, "OnChange(delta)")


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
        found = next((t for t in (bool, int, float, str) if isinstance(value, t)), None)
        if found is None:
            raise TypeError(f"a value is a bool, int, float or str, not {type(value).__name__}")
        type_ = found
    return type_, checked(type_, value, "starting value")


def checked(type_: type, value: object, what: str = "value") -> Any:
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
    if len(value.encode("utf-8", "surrogateescape")) > TEXT_MAX:
        raise ValueError(f"a text holds at most {TEXT_MAX} bytes")
    return value


def check_text(text: object, what: str) -> str:
    """Text a frame carries as it stands: no control characters, and valid Unicode."""
    if not isinstance(text, str):
        raise TypeError(f"{what} must be a str, not {type(text).__name__}")
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in text):
        raise ValueError(f"{what} {text!r} holds a control character")
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError(f"{what} {text!r} is not valid Unicode") from None
    return text


def check_unit(unit: object) -> str:
    """A signal's unit: "" for none."""
    return "" if unit is None else check_text(unit, "a unit")


def check_name(name: object, what: str) -> str:
    name = check_text(name, f"a {what} name")
    if not name:
        raise ValueError(f"a {what} name cannot be empty")
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


def changed(type_: type, value: Any, reporting: _Reporting, delta: float) -> bool:
    """Whether value moved by at least delta from the baseline; always without one."""
    if not reporting.valid:
        return True
    previous: Any = reporting.baseline
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


class Signal[T: (bool, int, float, str)]:
    """A value the device logs. Set :attr:`value`; the device sends it when it is due."""

    def __init__(self, device, device_id: int, name: str, type_: type[T], value: T,
                 write_at_interval, write_on_change, min_interval_ms: int, unit: str | None = "") -> None:
        self._device = device
        self.device_id = device_id
        self.name = name
        self.unit = unit
        self._removed = False
        self.type: type[T] = type_
        self.dtype = _DTYPES[type_]
        self._value: T = value
        # Only a policy that compares keeps a baseline.
        self._reporting: _Reporting | None = None
        self.interval_mode: _Mode | OnChange = ALWAYS
        self._min_interval_ms = 100
        self.min_interval_ms = min_interval_ms
        self.write_at_interval = write_at_interval
        self.write_on_change = write_on_change

    def _ensure_reporting(self) -> _Reporting:
        if self._reporting is None:
            self._reporting = _Reporting()
            self._reporting.min_interval_ms = self._min_interval_ms
        return self._reporting

    def _drop_unused_reporting(self) -> None:
        r = self._reporting
        if r is not None and not r.immediate and not isinstance(self.interval_mode, OnChange):
            self._reporting = None

    @property
    def unit(self) -> str:
        """The unit, "" for none. A host learns of a new one from the next device list; one that is
        logging stops, as the schema hash changes."""
        return self._unit

    @unit.setter
    def unit(self, unit: str | None) -> None:
        self._unit = check_unit(unit)

    @property
    def write_at_interval(self) -> _Mode | OnChange:
        """ALWAYS, OFF or OnChange(delta): whether the signal is in an interval report."""
        return self.interval_mode

    @write_at_interval.setter
    def write_at_interval(self, mode: _Mode | OnChange) -> None:
        if mode is not ALWAYS and mode is not OFF and not isinstance(mode, OnChange):
            raise TypeError("write_at_interval is ALWAYS, OFF or OnChange(delta)")
        self.interval_mode = mode
        if isinstance(mode, OnChange):
            self._ensure_reporting().interval_delta = float(mode.delta)
        self._drop_unused_reporting()

    @property
    def write_on_change(self) -> float | None:
        """How far the value moves before it goes out at once; None for not at once."""
        r = self._reporting
        return r.change_delta if r is not None and r.immediate else None

    @write_on_change.setter
    def write_on_change(self, delta: float | None) -> None:
        if delta is None:
            if self._reporting is not None:
                self._reporting.immediate = False
            self._drop_unused_reporting()
            return
        delta = check_delta(delta, "write_on_change")
        r = self._ensure_reporting()
        r.immediate = True
        r.change_delta = delta

    @property
    def min_interval_ms(self) -> int:
        """The least time between two sends of a change."""
        return self._min_interval_ms

    @min_interval_ms.setter
    def min_interval_ms(self, ms: int) -> None:
        if isinstance(ms, bool) or not isinstance(ms, int) or ms < 0:
            raise ValueError(f"min_interval_ms is a whole number of milliseconds, 0 or above, not {ms!r}")
        self._min_interval_ms = ms
        if self._reporting is not None:
            self._reporting.min_interval_ms = ms

    @property
    def value(self) -> T:
        return self._value

    @value.setter
    def value(self, value: T) -> None:
        self._value = checked(self.type, value)

    def write(self, value: T | None = None, timestamp: int | None = None) -> None:
        """Sends the signal now, set to ``value`` if one is given."""
        if self._removed:
            raise RuntimeError(f"signal {self.name!r} was removed by clear_all_signals()")
        if value is not None:
            self.value = value
        self._device._write_signal_now(self, check_timestamp(timestamp))

    def __repr__(self) -> str:
        return f"Signal({self.name!r}, {self.type.__name__}, {self._value!r})"
