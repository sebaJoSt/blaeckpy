"""Inputs and sensors: values, getters, checks on a host's write, change reporting."""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Callable, Sequence
from enum import StrEnum
from typing import Any

from ._signal import ANY_CHANGE, Signal, _Reporting, changed, check_delta, check_unit, checked, type_and_value
from .protocol import frames
from .protocol.frames import DType, ValueKind

# Ack reasons a host's write can get.
OK = 0
OUT_OF_RANGE = 2
BAD_SWITCH = 3
BAD_SELECT = 4
TOO_LONG = 5
MISSING_VALUE = 6
DEVICE_NOT_RESPONDING = 8
NOT_AN_INTEGER = 9
READ_ONLY = 10
NOT_A_NUMBER = 11


class Mode(StrEnum):
    """How a host offers an input: a number as a box or a slider, a text plain or as a password."""

    AUTO = "auto"
    BOX = "box"
    SLIDER = "slider"
    PLAIN = "plain"
    PASSWORD = "password"


class StateClass(StrEnum):
    MEASUREMENT = "measurement"
    TOTAL = "total"
    TOTAL_INCREASING = "total_increasing"
    MEASUREMENT_ANGLE = "measurement_angle"


class Category(StrEnum):
    """Where a host files an entity: with the main controls, the configuration or diagnostics."""

    NONE = "none"
    CONFIG = "config"
    DIAGNOSTIC = "diagnostic"


_NUMBER_MODES = {Mode.AUTO: 0, Mode.BOX: 1, Mode.SLIDER: 2}
_TEXT_MODES = {Mode.PLAIN: 0, Mode.PASSWORD: 1}
_STATE_CLASSES = {None: 0, StateClass.MEASUREMENT: 1, StateClass.TOTAL: 2,
                  StateClass.TOTAL_INCREASING: 3, StateClass.MEASUREMENT_ANGLE: 4}
_CATEGORIES = {Category.NONE: 0, Category.CONFIG: 1, Category.DIAGNOSTIC: 2}

_HOST_NAME = re.compile(r"[A-Za-z0-9_.\-]+")
_SELECT_INDEX = re.compile(r"[+-]?[0-9]+")
# JSON's number, with an optional leading '+'.
_NUMBER = re.compile(r"[+-]?(0|[1-9][0-9]*)(\.[0-9]+)?([eE][+-]?[0-9]+)?")
_WHOLE = re.compile(r"[+-]?(0|[1-9][0-9]*)")

INT64_MIN, INT64_MAX = -(2**63), 2**63 - 1

log = logging.getLogger("blaeckpy")


def check_host_name(name: object, what: str) -> str:
    """A name a host sends: ASCII letters, digits, _ - and . only, and not BLAECK.'s."""
    if not isinstance(name, str):
        raise TypeError(f"a {what} name is a str, not {type(name).__name__}")
    if not _HOST_NAME.fullmatch(name):
        raise ValueError(f"{what} name {name!r}: a host sends it, so ASCII letters, digits, _ - . only")
    if name.startswith("BLAECK."):
        raise ValueError(f"{what} name {name!r}: BLAECK. is reserved for built-in commands")
    return name


def _enum(value, enum_type, what: str):
    try:
        return enum_type(value)
    except ValueError:
        allowed = ", ".join(m.value for m in enum_type)
        raise ValueError(f"{what} {value!r} is not one of {allowed}") from None


class Presentation:
    """How a host shows an entity. Numbers alone have a unit, state class and precision."""

    def __init__(self, *, number: bool, display_name=None, icon=None, device_class=None,
                 category: Category | str = Category.NONE, disabled_by_default=False, force_update=False,
                 unit=None, state_class=None, display_precision=None):
        if not number and (unit is not None or state_class is not None or display_precision is not None):
            raise TypeError("unit, state_class and display_precision are for numbers")
        for text in (display_name, icon, device_class):
            if text is not None:
                check_unit(text)  # no control characters
        self.display_name = display_name
        self.icon = icon
        self.device_class = device_class
        self.category = _enum(category, Category, "category")
        self.disabled_by_default = bool(disabled_by_default)
        self.force_update = bool(force_update)
        self.unit = None if unit is None else check_unit(unit)
        self.state_class = None if state_class is None else _enum(state_class, StateClass, "state class")
        if display_precision is not None and not 0 <= display_precision <= 255:
            raise ValueError("display_precision is 0 to 255 decimal places")
        self.display_precision = display_precision

    def entry_args(self) -> dict:
        return dict(display_name=self.display_name, icon=self.icon, device_class=self.device_class,
                    category=_CATEGORIES[self.category], disabled_by_default=self.disabled_by_default)

    def property_args(self) -> dict:
        return dict(self.entry_args(), unit=self.unit, state_class=_STATE_CLASSES[self.state_class],
                    display_precision=self.display_precision, force_update=self.force_update)


def _parse_whole(text: str) -> int | None:
    """A whole decimal number, read exactly as blaeck reads it into a long long; None if it does
    not fit."""
    value = int(text)
    return value if INT64_MIN <= value <= INT64_MAX else None


def _step_decimals(step: float) -> int:
    scale = 1.0
    for decimals in range(6):
        scaled = step * scale
        if abs(scaled - math.floor(scaled + 0.5)) < 1e-3:
            return decimals
        scale *= 10.0
    return 6


class Property[T]:
    """An entity a host shows: an input it can also set, or a sensor."""

    kind: ValueKind
    writable = False
    _value: T

    def __init__(self, device, device_id: int, name: str, dtype: DType, presentation: Presentation,
                 write_on_change: float, min_interval_ms: int):
        self._device = device
        self.device_id = device_id
        self.name = name
        self.dtype = dtype
        self._presentation = presentation
        self._reporting = _Reporting()
        self._reporting.change_delta = check_delta(write_on_change, "write_on_change")
        self._reporting.min_interval_ms = int(min_interval_ms)
        self._on_change: Callable[[], None] | None = None

    # The value as it goes out; subclasses say where it lives.
    def _current(self) -> T:
        return self._value

    @property
    def value(self) -> T:
        return self._current()

    def _compare_type(self) -> type:
        return {DType.BOOL: bool, DType.TEXT: str, DType.DOUBLE: float}.get(self.dtype, int)

    def _changed(self) -> bool:
        return changed(self._compare_type(), self._current(), self._reporting,
                       self._reporting.change_delta)

    def write(self) -> None:
        """Sends the value now, changed or not."""
        self._device._write_property_frame(self)

    def _entry_kwargs(self) -> dict:
        return {}

    def _entry(self, value) -> frames.Entry:
        return frames.property_entry(self.device_id, self.name, self.kind, self.dtype, value,
                                     writable=self.writable, **self._entry_kwargs())

    def _receive(self, params: list[str]) -> int:
        return READ_ONLY


class _Input[T](Property[T]):
    writable = True

    def on_change(self, fn: Callable[[], None]) -> Callable[[], None]:
        """A function to run after a host's write, before the new value goes out. Works as a
        decorator."""
        self._on_change = fn
        return fn

    @property
    def value(self) -> T:
        return self._value

    @value.setter
    def value(self, value: T) -> None:
        self._value = self._check(value)

    def _check(self, value) -> T:
        raise NotImplementedError

    def _accept(self, text: str) -> int:
        raise NotImplementedError

    def _receive(self, params: list[str]) -> int:
        if not params or (params[0] == "" and self.kind is not ValueKind.TEXT):
            return MISSING_VALUE
        return self._accept(params[0])


class NumberInput[T: (int, float)](_Input[T]):
    kind = ValueKind.NUMBER

    def __init__(self, device, device_id, name, type_or_initial, initial, *, min, max, step, mode,
                 presentation, write_on_change, min_interval_ms):
        type_, value = type_and_value(type_or_initial, initial)
        if type_ not in (int, float):
            raise TypeError("a number input holds an int or a float")
        self.type = type_
        super().__init__(device, device_id, name, DType.INT64 if type_ is int else DType.DOUBLE,
                         presentation, write_on_change, min_interval_ms)
        self._value = value  # type: ignore[assignment]
        if (min is None) != (max is None):
            raise ValueError("a range has both min and max, or neither")
        if step is not None and min is None:
            raise ValueError("a step needs a range")
        self.min = self._bound(min, "min")
        self.max = self._bound(max, "max")
        self.step = self._bound(step, "step")
        if self.min is not None and not self.min < self.max:
            raise ValueError("min must be below max")
        if self.step is not None and not self.step > 0:
            raise ValueError("step must be above 0")
        self.mode = _enum(mode, Mode, "mode") if mode is not None else Mode.AUTO
        if self.mode not in _NUMBER_MODES:
            raise ValueError(f"a number input's mode is auto, box or slider, not {self.mode}")

    def _bound(self, value, what) -> Any:
        if value is None:
            return None
        if self.type is int and (isinstance(value, bool) or not isinstance(value, int)):
            raise TypeError(f"{what} of an int input is a whole number")
        return checked(self.type, value, what)

    def _check(self, value):
        return checked(self.type, value)

    def _entry_kwargs(self) -> dict:
        return dict(self._presentation.property_args(), range_min=self.min, range_max=self.max,
                    step=self.step, mode=_NUMBER_MODES[self.mode])

    def _accept(self, text: str) -> int:
        if not _NUMBER.fullmatch(text):
            return NOT_A_NUMBER
        if _WHOLE.fullmatch(text):
            whole = _parse_whole(text)
            if whole is None:
                return OUT_OF_RANGE
            if self.min is not None:
                inside = (self.min <= whole <= self.max) if self.type is int \
                    else (float(self.min) <= float(whole) <= float(self.max))
                if not inside:
                    return OUT_OF_RANGE
            self._value = whole if self.type is int else float(whole)  # type: ignore[assignment]
            return OK
        if self.type is int:
            return NOT_AN_INTEGER
        number = float(text)
        if math.isinf(number):
            return OUT_OF_RANGE
        if self.min is not None and not self.min <= number <= self.max:
            return OUT_OF_RANGE
        if self.step is not None:
            assert self.min is not None  # a step needs a range
            steps = (number - self.min) / self.step
            nearest = math.floor(steps + 0.5)
            if abs(steps - nearest) < 1e-3:
                snapped = self.min + nearest * self.step
                scale = 1.0
                for _ in range(_step_decimals(self.step)):
                    scale *= 10.0
                if abs(snapped) * scale < 1e15:
                    snapped = math.floor(abs(snapped) * scale + 0.5) / scale * (-1.0 if snapped < 0.0 else 1.0)
                number = snapped
        self._value = number  # type: ignore[assignment]
        return OK


class Switch(_Input[bool]):
    kind = ValueKind.BOOL

    def __init__(self, device, device_id, name, initial, *, presentation, min_interval_ms):
        super().__init__(device, device_id, name, DType.BOOL, presentation, ANY_CHANGE, min_interval_ms)
        self._value = checked(bool, initial)

    def _check(self, value):
        return checked(bool, value)

    def _entry_kwargs(self) -> dict:
        return self._presentation.property_args()

    def _accept(self, text: str) -> int:
        if text not in ("0", "1"):
            return BAD_SWITCH
        self._value = text == "1"
        return OK


def _check_options(options) -> list[str]:
    if isinstance(options, str) or not isinstance(options, Sequence):
        raise TypeError("options is a list of str")
    options = list(options)
    if any(not isinstance(o, str) for o in options):
        raise TypeError("options is a list of str")
    frames.check_listed(options, "option")
    # The index goes out as a byte.
    if len(options) > 256:
        raise ValueError("at most 256 options: the index is a byte")
    return options


def _check_index(options: list[str], index) -> int:
    if isinstance(index, bool) or not isinstance(index, int):
        raise TypeError(f"an index is a whole number, not {index!r}")
    if not 0 <= index < len(options):
        raise ValueError(f"index {index} is not one of the {len(options)} options")
    return index


class Select(_Input[int]):
    kind = ValueKind.ENUM

    def __init__(self, device, device_id, name, index, *, options, presentation, min_interval_ms):
        super().__init__(device, device_id, name, DType.BYTE, presentation, ANY_CHANGE, min_interval_ms)
        self.options = _check_options(options)
        self._value = _check_index(self.options, index)

    def _check(self, value):
        return _check_index(self.options, value)

    @property
    def option(self) -> str:
        """The selected option's name."""
        return self.options[self._value]

    def _entry_kwargs(self) -> dict:
        return dict(self._presentation.property_args(), options=self.options)

    def _accept(self, text: str) -> int:
        if text in self.options:
            index = self.options.index(text)
        elif _SELECT_INDEX.fullmatch(text):
            index = int(text)
        else:
            return BAD_SELECT
        if not 0 <= index < len(self.options):
            return BAD_SELECT
        self._value = index
        return OK


def _check_text(value, max_length: int) -> str:
    value = checked(str, value)
    if len(value.encode("utf-8", "surrogateescape")) > max_length:
        raise ValueError(f"text is longer than its {max_length} bytes")
    return value


class TextInput(_Input[str]):
    kind = ValueKind.TEXT

    def __init__(self, device, device_id, name, initial, *, max_length, mode, presentation,
                 min_interval_ms):
        super().__init__(device, device_id, name, DType.TEXT, presentation, ANY_CHANGE, min_interval_ms)
        if isinstance(max_length, bool) or not isinstance(max_length, int) or not 0 <= max_length <= 255:
            raise ValueError("max_length is 0 to 255 bytes")
        self.max_length = max_length
        self.mode = _enum(mode, Mode, "mode") if mode is not None else Mode.PLAIN
        if self.mode not in _TEXT_MODES:
            raise ValueError(f"a text input's mode is plain or password, not {self.mode}")
        self._value = _check_text(initial, max_length)

    def _check(self, value):
        return _check_text(value, self.max_length)

    def _entry_kwargs(self) -> dict:
        return dict(self._presentation.property_args(), text_max_len=self.max_length,
                    mode=_TEXT_MODES[self.mode])

    def _accept(self, text: str) -> int:
        if len(text.encode("utf-8", "surrogateescape")) > self.max_length:
            return TOO_LONG
        self._value = text
        return OK


class Sensor[T: (bool, int, float, str)](Property[T]):
    """A value a host shows but cannot set: its own, a signal's, or what a function returns."""

    def __init__(self, device, device_id, name, source, initial, *, get, options, presentation,
                 write_on_change, min_interval_ms):
        """``presentation`` is the keywords for :class:`Presentation`; whether a unit is allowed
        depends on the kind, which is only known here."""
        self._signal: Signal | None = None
        self._get: Callable[[], object] | None = None
        self._get_failing = False
        self._read_pass: int | None = None
        self.options: list[str] | None = None
        value = None
        if options is not None:
            # An enum sensor holds the index of an option: add_sensor("Phase", 0, options=[...]).
            if get is not None or isinstance(source, (Signal, type)) or initial is not None:
                raise TypeError("an enum sensor takes its starting index alone")
            self.options = _check_options(options)
            type_, dtype, kind = int, DType.BYTE, ValueKind.ENUM
            value = _check_index(self.options, source)
        elif isinstance(source, Signal):
            if initial is not None or get is not None:
                raise TypeError("a sensor that shows a signal takes nothing else")
            self._signal = source
            type_ = source.type
            dtype, kind = _kind_for(type_)
        elif get is not None:
            if not isinstance(source, type) or initial is not None:
                raise TypeError("a sensor with get= takes its type alone: add_sensor(name, float, get=fn)")
            if not callable(get):
                raise TypeError("get is a function")
            # What goes out until get= first reads: the type's empty value.
            type_, value = type_and_value(source, None)
            self._get = get
            dtype, kind = _kind_for(type_)
        else:
            type_, value = type_and_value(source, initial)
            dtype, kind = _kind_for(type_)
        self.type = type_
        self.kind = kind
        super().__init__(device, device_id, name, dtype,
                         Presentation(number=kind is ValueKind.NUMBER, **presentation),
                         write_on_change, min_interval_ms)
        self._value = value  # type: ignore[assignment]

    def _current(self) -> T:
        """The value as tick() sends it. A get= is read once in each pass of tick(), so the
        value compared is the value sent. One that raises or returns the wrong type is logged
        once, until it reads again, and the last value it gave goes out in its place: like any
        other function of the script's, it must not stop the device."""
        if self._signal is not None:
            return self._signal.value
        if self._get is not None and self._read_pass != self._device._pass:
            self._read_pass = self._device._pass
            try:
                self._value = self._read_get()
                self._get_failing = False
            except Exception:
                if not self._get_failing:
                    self._get_failing = True
                    log.exception("get= of sensor %r raised inside tick(); its last value stands in",
                                  self.name)
        return self._value

    def write(self) -> None:
        """Sends the value now, changed or not; a get= reads afresh for it."""
        self._read_pass = None
        super().write()

    def _read_get(self) -> T:
        assert self._get is not None
        return checked(self.type, self._get(), "value from get=")

    @property
    def value(self) -> T:
        # Read by the script itself, a get= that raises raises here.
        if self._get is not None:
            return self._read_get()
        return self._current()

    @value.setter
    def value(self, value: T) -> None:
        if self._signal is not None:
            self._signal.value = value
        elif self._get is not None:
            raise AttributeError("a sensor with get= reads its function; it has no value to set")
        elif self.options is not None:
            self._value = _check_index(self.options, value)  # type: ignore[assignment]
        else:
            self._value = checked(self.type, value)

    def _entry_kwargs(self) -> dict:
        args = self._presentation.property_args()
        if self.options is not None:
            args["options"] = self.options
        if self.kind is ValueKind.TEXT:
            # A Python text has no buffer: it declares the most a text holds.
            args["text_max_len"] = 255
        return args


def _kind_for(type_: type) -> tuple[DType, ValueKind]:
    return {int: (DType.INT64, ValueKind.NUMBER), float: (DType.DOUBLE, ValueKind.NUMBER),
            bool: (DType.BOOL, ValueKind.BOOL), str: (DType.TEXT, ValueKind.TEXT)}[type_]
