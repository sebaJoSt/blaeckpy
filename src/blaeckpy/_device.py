"""Blaeck and its sub-devices: registration, tick(), built-in commands."""

from __future__ import annotations

import logging
import select
import time
from collections.abc import Callable, Sequence
from importlib.metadata import version as _version
from typing import overload

from . import _clock, _commands, _console, _properties, _status, _transport
from ._clock import TimestampMode
from ._events import Button, Event
from ._properties import (Category, Mode, NumberInput, Presentation, Select, Sensor, StateClass, Switch,
                          TextInput, check_host_name)
from ._signal import ALWAYS, OnChange, Signal, _Mode, check_name, check_text, check_unit, changed, type_and_value
from .protocol import frames
from .protocol.schema_hash import schema_hash

# What the device list says this library is. Loggbok gates every protocol v2 capability on the
# name and version, so these are its own, never blaeck's. The golden tests set blaeck's in their
# place while they play a scenario, so the device list matches blaeck's byte for byte.
LIBRARY_NAME = "blaeckpy"
LIBRARY_VERSION = _version("blaeckpy")

_ACK_OK = 0
_ACK_UNKNOWN = 1
_ACK_TRUNCATED = 7

log = logging.getLogger("blaeckpy")
_console.install(log)

# begin()'s status_port when the script doesn't say; the tests set None.
_DEFAULT = object()

_Value = bool | int | float | str
_TypeOrValue = type | bool | int | float | str


class _DeviceBase:
    """What the board and each sub-device declare: signals, inputs, sensors, events, buttons and
    plain commands. A sub-device's are listed under it; names a host sends stay unique on the
    whole board."""

    _core: "Blaeck"
    _id: int

    # ----- registration -----

    def add_signal[T: (bool, int, float, str)](
            self, name: str, type_or_initial: type[T] | T, initial: T | None = None, *,
            unit: str | None = None, write_at_interval: _Mode | OnChange = ALWAYS,
            write_on_change: float | None = None, min_interval_ms: int = 100) -> Signal[T]:
        """A value the device logs: ``add_signal("T", float)``, ``add_signal("T", float, 21.5)``
        or ``add_signal("T", 21.5)``. ``unit`` in Home Assistant's spelling, such as "°C"."""
        name = check_name(name, "signal")
        if any(s.name == name and s.device_id == self._id for s in self._core._signals):
            raise ValueError(f"signal {name!r} exists already")
        type_, value = type_and_value(type_or_initial, initial)
        signal: Signal[T] = Signal(self._core, self._id, name, type_, value,  # type: ignore[arg-type]
                                   write_at_interval, write_on_change, min_interval_ms, unit)
        self._core._signals.append(signal)
        return signal

    # ----- entities: what a host shows and controls -----

    def add_number_input[T: (int, float)](self, name: str, type_or_initial: type[T] | T,
                         initial: T | None = None, *, min: float | None = None, max: float | None = None,
                         step: float | None = None, mode: Mode | str | None = None,
                         unit: str | None = None, display_name: str | None = None,
                         icon: str | None = None, device_class: str | None = None,
                         state_class: StateClass | str | None = None,
                         display_precision: int | None = None, category: Category | str = Category.NONE,
                         disabled_by_default: bool = False, force_update: bool = False,
                         write_on_change: float = 0.0, min_interval_ms: int = 100) -> NumberInput[T]:
        """A number a host sets, within ``min`` and ``max`` and on ``step`` if given."""
        name = self._core._host_name(name, "input")
        presentation = Presentation(number=True, unit=unit, display_name=display_name, icon=icon,
                                    device_class=device_class, state_class=state_class,
                                    display_precision=display_precision, category=category,
                                    disabled_by_default=disabled_by_default, force_update=force_update)
        return self._core._add_property(NumberInput(self._core, self._id, name, type_or_initial, initial, min=min, max=max,
                                              step=step, mode=mode, presentation=presentation,
                                              write_on_change=write_on_change,
                                              min_interval_ms=min_interval_ms))

    def add_switch(self, name: str, initial: bool = False, *, display_name: str | None = None,
                   icon: str | None = None, device_class: str | None = None,
                   category: Category | str = Category.NONE, disabled_by_default: bool = False,
                   force_update: bool = False, min_interval_ms: int = 100) -> Switch:
        name = self._core._host_name(name, "switch")
        presentation = Presentation(number=False, display_name=display_name, icon=icon,
                                    device_class=device_class, category=category,
                                    disabled_by_default=disabled_by_default, force_update=force_update)
        return self._core._add_property(Switch(self._core, self._id, name, initial, presentation=presentation,
                                         min_interval_ms=min_interval_ms))

    def add_select(self, name: str, index: int = 0, *, options: Sequence[str],
                   display_name: str | None = None, icon: str | None = None,
                   device_class: str | None = None, category: Category | str = Category.NONE,
                   disabled_by_default: bool = False, force_update: bool = False,
                   min_interval_ms: int = 100) -> Select:
        """One of ``options``, held as its index; a host sends the index or the option's name."""
        name = self._core._host_name(name, "select")
        presentation = Presentation(number=False, display_name=display_name, icon=icon,
                                    device_class=device_class, category=category,
                                    disabled_by_default=disabled_by_default, force_update=force_update)
        return self._core._add_property(Select(self._core, self._id, name, index, options=options,
                                         presentation=presentation, min_interval_ms=min_interval_ms))

    def add_text_input(self, name: str, initial: str = "", *, max_length: int = 255,
                       mode: Mode | str | None = None, display_name: str | None = None,
                       icon: str | None = None, device_class: str | None = None,
                       category: Category | str = Category.NONE, disabled_by_default: bool = False,
                       force_update: bool = False, min_interval_ms: int = 100) -> TextInput:
        name = self._core._host_name(name, "text input")
        presentation = Presentation(number=False, display_name=display_name, icon=icon,
                                    device_class=device_class, category=category,
                                    disabled_by_default=disabled_by_default, force_update=force_update)
        return self._core._add_property(TextInput(self._core, self._id, name, initial, max_length=max_length, mode=mode,
                                            presentation=presentation, min_interval_ms=min_interval_ms))

    def add_sensor[T: (bool, int, float, str)](
                   self, name: str, source: Signal[T] | type[T] | T, initial: T | None = None, *,
                   get: Callable[[], T] | None = None, options: Sequence[str] | None = None,
                   unit: str | None = None, display_name: str | None = None, icon: str | None = None,
                   device_class: str | None = None, state_class: StateClass | str | None = None,
                   display_precision: int | None = None, category: Category | str = Category.NONE,
                   disabled_by_default: bool = False, force_update: bool = False,
                   write_on_change: float = 0.0, min_interval_ms: int = 100) -> Sensor[T]:
        """A value a host shows: ``add_sensor("T", signal)`` shows a signal's value,
        ``add_sensor("T", float, 21.5)`` or ``add_sensor("T", 21.5)`` holds its own,
        ``add_sensor("T", float, get=fn)`` reads a function, and
        ``add_sensor("Phase", 0, options=[...])`` holds the index of an option.
        ``unit``, ``state_class`` and ``display_precision`` are for numbers."""
        name = self._core._host_name(name, "sensor")
        presentation = dict(unit=unit, display_name=display_name, icon=icon, device_class=device_class,
                            state_class=state_class, display_precision=display_precision,
                            category=category, disabled_by_default=disabled_by_default,
                            force_update=force_update)
        return self._core._add_property(Sensor(self._core, self._id, name, source, initial, get=get, options=options,
                                         presentation=presentation, write_on_change=write_on_change,
                                         min_interval_ms=min_interval_ms))

    def add_event(self, name: str, types: Sequence[str], *, display_name: str | None = None,
                  icon: str | None = None, device_class: str | None = None,
                  category: Category | str = Category.NONE, disabled_by_default: bool = False) -> Event:
        """A moment the device reports, of one of ``types``; ``event.write(type)`` reports it."""
        name = check_name(name, "event")
        if any(e.name == name and e.device_id == self._id for e in self._core._events):
            raise ValueError(f"event {name!r} exists already")
        presentation = Presentation(number=False, display_name=display_name, icon=icon,
                                    device_class=device_class, category=category,
                                    disabled_by_default=disabled_by_default)
        event = Event(self._core, self._id, name, types, presentation)
        frames.check_entry(event._entry())
        self._core._events.append(event)
        self._core._catalog_changed()
        return event

    @overload
    def add_button(self, name: str, press: Callable[[], None], *,
                   display_name: str | None = None, icon: str | None = None,
                   device_class: str | None = None, category: Category | str = Category.NONE,
                   disabled_by_default: bool = False) -> Button: ...

    @overload
    def add_button(self, name: str, press: None = None, *,
                   display_name: str | None = None, icon: str | None = None,
                   device_class: str | None = None, category: Category | str = Category.NONE,
                   disabled_by_default: bool = False) -> Callable[[Callable[[], None]], Callable[[], None]]: ...

    def add_button(self, name: str, press: Callable[[], None] | None = None, *,
                   display_name: str | None = None, icon: str | None = None,
                   device_class: str | None = None, category: Category | str = Category.NONE,
                   disabled_by_default: bool = False) -> Button | Callable[[Callable[[], None]], Callable[[], None]]:
        """A button a host presses; ``press`` runs with no arguments, parameters are ignored.
        Returns the :class:`Button`. Works as ``@device.add_button("Reset")`` too, which leaves
        the function as it is."""
        name = self._core._host_name(name, "button")
        presentation = Presentation(number=False, display_name=display_name, icon=icon,
                                    device_class=device_class, category=category,
                                    disabled_by_default=disabled_by_default)

        def register(fn: Callable[[], None]) -> Button:
            button = Button(self._core, self._id, name, fn, presentation)
            self._core._buttons.append(button)
            self._core._catalog_changed()
            return button

        if press is None:
            def decorate(fn: Callable[[], None]) -> Callable[[], None]:
                register(fn)
                return fn
            return self._core._awaiting_function(f"add_button({name!r})", decorate)
        return register(press)

    def on_command(self, name: str, handler: Callable[..., object] | None = None):
        """A command a host sends by name and no host lists: ``handler(*params)`` runs with its
        parameters as text. Works as ``@device.on_command("MOTOR")`` or as a plain call."""
        def register(fn):
            if not callable(fn):
                raise TypeError("a command needs a function")
            self._core._plain_commands[self._core._host_name(name, "command")] = (self._id, fn)
            return fn
        if handler is None:
            return self._core._awaiting_function(f"on_command({name!r})", register)
        return register(handler)



class Terminal:
    """Text to whoever looks into the running device: on serial the port itself, on TCP every
    connection but the host. Use it like print()."""

    def __init__(self, device: "Blaeck") -> None:
        self._device = device

    def write(self, text: str) -> None:
        t = self._device._transport
        if t is not None:
            t.terminal(text.encode("utf-8", "surrogateescape"))

    def print(self, *values: object, sep: str = " ", end: str = "\n") -> None:
        self.write(sep.join(str(v) for v in values) + end)


class SubDevice(_DeviceBase):
    """A second board, or a part of this one, that a host shows below the board as a device of
    its own. Declare its signals and entities on it, as on the board. The script talks to it and
    says when it goes missing, comes back or restarts."""

    def __init__(self, core: "Blaeck", device_id: int, name: str, hw_version: str, fw_version: str):
        self._core, self._id = core, device_id
        self.name = name
        self.hw_version = hw_version
        self.fw_version = fw_version
        self._missing = False
        self._reported_missing = False
        self._restart_pending = False

    @property
    def missing(self) -> bool:
        """True between mark_missing() and mark_present()."""
        return self._missing

    def mark_missing(self) -> None:
        """It stopped answering: its signals leave the data, commands to it are refused with
        DEVICE_NOT_RESPONDING, and a host is told."""
        self._core._set_missing(self, True)

    def mark_present(self) -> None:
        """It answers again. Each of its on-change signals is sent anew."""
        self._core._set_missing(self, False)

    def write_restarted(self) -> None:
        """It restarted: a host is told, so it knows the values went back to their defaults."""
        self._restart_pending = True
        self._core._write_device_notices()

    def write_all(self, timestamp: int | None = None) -> None:
        """Sends this device's signals now."""
        core = self._core
        core._write_data(0, [s for s in core._signals if s.device_id == self._id],
                         _clock.check_timestamp(timestamp))


class Blaeck(_DeviceBase):
    """A device a host such as Loggbok logs and controls, over serial or TCP.

    Declare signals, then :meth:`begin` with an opened port and call :meth:`tick` in a loop.

    Not thread-safe: use a device, and what it made, from the thread that calls tick(). Hand
    values from other threads over, with a queue.Queue say, and set them in the loop.
    """

    def __init__(self, name: str = "Unnamed", *, hw_version: str = "n/a", fw_version: str = "n/a"):
        self._core, self._id = self, 0
        self._devices: list[SubDevice] = []
        self.name = check_name(name, "device")
        self.hw_version = check_text(hw_version, "hw_version")
        self.fw_version = check_text(fw_version, "fw_version")
        self._signals: list[Signal] = []
        self._transport: _transport.StreamTransport | _transport.TcpTransport | None = None
        self._clock = _clock.Clock()
        self.terminal = Terminal(self)
        self._ticks_without_wait = 0
        # Counts tick()'s passes; a get= sensor reads its function once in each.
        self._pass = 0
        self._tick_second = 0
        self._hinted = False
        self._timestamp_mode = TimestampMode.NONE
        self._timestamp_clock: Callable[[], int] | None = None
        self._clock_failing = False

        # What the host switched on and off. A device starts silent.
        self._data_stopped = True
        self._entities_stopped = True
        self._timed = False
        self._timed_first = False
        self._interval_ms = 1000
        self._last_interval_ms = 0

        self._began = False
        self._closed = False
        # The calls still waiting for their function, for begin() to refuse.
        self._without_function: list[str] = []
        self._status: _status.StatusPage | None = None
        self._connection = "Closed"
        # The last command received, as (name, time.time()), for the status page.
        self._last_command: tuple[str, float] | None = None
        self._answering = False
        self._requested = False
        self._restart_reported = False
        self._restart_flag = True

        self._before_write: Callable[[], None] | None = None
        self._callbacks: dict[str, Callable] = {}

        self._properties: list[_properties.Property] = []
        self._events: list[Event] = []
        self._buttons: list[Button] = []
        self._plain_commands: dict[str, tuple[int, Callable[..., object]]] = {}
        self._any_command: Callable | None = None
        self._catalog_dirty = False

    def set_timestamp_mode(self, mode: TimestampMode | str,
                           clock: Callable[[], int] | None = None) -> None:
        """How data frames are timestamped: "none", "micros" (since the script started) or "unix"
        (the computer's clock). ``clock`` returns microseconds, replacing the default. It is read
        when a data frame goes out. While it raises or returns what no frame can carry, no data
        goes out, since a time can't be vouched for; the failure is logged, and a signal that
        changed goes out once the clock works again."""
        try:
            self._timestamp_mode = TimestampMode(mode)
        except ValueError:
            raise ValueError(f"timestamp mode {mode!r} is not one of none, micros, unix") from None
        if clock is not None and not callable(clock):
            raise TypeError("clock is a function returning microseconds")
        self._timestamp_clock = clock


    def add_device(self, name: str, *, hw_version: str = "n/a", fw_version: str = "n/a") -> SubDevice:
        """A device a host shows below this board, such as a second board it polls."""
        name = check_name(name, "device")
        if name == self.name or any(d.name == name for d in self._devices):
            raise ValueError(f"device {name!r}: no two devices on a board, the board included, "
                             "share a name")
        if len(self._devices) >= 254:
            raise ValueError("a board has at most 254 devices")
        device = SubDevice(self, len(self._devices) + 1, name, check_text(hw_version, "hw_version"),
                           check_text(fw_version, "fw_version"))
        self._devices.append(device)
        return device

    def _is_missing(self, device_id: int) -> bool:
        return device_id > 0 and self._devices[device_id - 1]._missing

    def _set_missing(self, device: SubDevice, missing: bool) -> None:
        if device._missing == missing:
            return
        device._missing = missing
        if not missing:
            # The host got none of its values meanwhile, so each on-change signal is due again.
            for sig in self._signals:
                if sig.device_id == device._id and sig._reporting is not None:
                    sig._reporting.valid = False
        self._write_device_notices()

    def _write_device_notices(self) -> None:
        """Restarts, then the latest missing state, of each sub-device, once a host can hear them.
        Missing and back again before that is no change."""
        if not self._ready():
            return
        # A notice that didn't go out stays due, and those after it wait, so they keep their order.
        for d in self._devices:
            if d._restart_pending:
                if not self._send(frames.device_notification(d._id, frames.DeviceEvent.RESTARTED)):
                    return
                d._restart_pending = False
            if d._missing != d._reported_missing:
                event = frames.DeviceEvent.NOT_RESPONDING if d._missing else frames.DeviceEvent.RESPONDING_AGAIN
                if not self._send(frames.device_notification(d._id, event)):
                    return
                d._reported_missing = d._missing

    def _ordered_signals(self) -> list[Signal]:
        """The signals in device-list order: the board's, then each sub-device's. Data frames
        number them by this order."""
        return [sig for device_id in range(len(self._devices) + 1)
                for sig in self._signals if sig.device_id == device_id]

    def _host_name(self, name: str, what: str) -> str:
        """A name a host sends, unique among inputs, sensors, buttons and plain commands."""
        name = check_host_name(name, what)
        if (any(p.name == name for p in self._properties) or any(b.name == name for b in self._buttons)
                or name in self._plain_commands):
            raise ValueError(f"{name!r} is taken: inputs, sensors, buttons and commands share names")
        return name

    def _add_property(self, prop):
        # A get= isn't called just to declare it; its entry holds no list to outgrow.
        if getattr(prop, "_get", None) is None:
            frames.check_entry(prop._entry(prop._current()))
        self._properties.append(prop)
        self._catalog_changed()
        return prop

    def _catalog_changed(self) -> None:
        self._catalog_dirty = True

    # ----- clearing, to declare a new set while running -----

    def clear_all_signals(self) -> None:
        """Removes every signal, the sub-devices' too, so a new set can be added. A host learns
        of the new signals from the next device list it asks for; one that is logging stops at
        the next data frame, whose schema hash no longer matches its columns.

        A sensor that shows a signal keeps showing that Signal object; one added anew under the
        same name is another object. To show the new one, clear_all_sensors() and add the sensor
        again."""
        for sig in self._signals:
            sig._removed = True
        self._signals.clear()

    def clear_all_controls(self) -> None:
        """Removes every input and button, the sub-devices' too. Sensors stay. The new entity
        list goes to the host at the next tick()."""
        self._clear_properties(writable=True)
        if self._buttons:
            self._buttons.clear()
            self._catalog_changed()

    def clear_all_sensors(self) -> None:
        """Removes every sensor, the sub-devices' too. Controls stay. The new entity list goes
        to the host at the next tick()."""
        self._clear_properties(writable=False)

    def clear_all_events(self) -> None:
        """Removes every event and its types, the sub-devices' too. The new entity list goes to
        the host at the next tick()."""
        if self._events:
            for event in self._events:
                event._removed = True
            self._events.clear()
            self._catalog_changed()

    def clear_all_commands(self) -> None:
        """Removes every plain command from on_command(), and the on_any_command() function.
        Buttons stay: they go with clear_all_controls()."""
        self._plain_commands.clear()
        self._any_command = None

    def _clear_properties(self, writable: bool) -> None:
        kept = [p for p in self._properties if p.writable != writable]
        if len(kept) != len(self._properties):
            self._properties[:] = kept
            self._catalog_changed()

    def on_any_command[F: Callable](self, fn: F) -> F:
        """Sees every command, after its own function: ``fn(command, *params)``. Its answer
        counts only where nothing took the command: True answers OK, False or None UNKNOWN."""
        self._any_command = fn
        return fn

    # ----- callbacks: each works as a decorator or a plain call -----

    def _register[F: Callable](self, key: str, fn: F) -> F:
        self._callbacks[key] = fn
        return fn

    def on_before_write[F: Callable](self, fn: F) -> F:
        self._before_write = fn
        return fn

    def on_data_start[F: Callable](self, fn: F) -> F:
        return self._register("data_start", fn)

    def on_data_stop[F: Callable](self, fn: F) -> F:
        return self._register("data_stop", fn)

    def on_interval_start[F: Callable](self, fn: F) -> F:
        return self._register("interval_start", fn)

    def on_interval_stop[F: Callable](self, fn: F) -> F:
        return self._register("interval_stop", fn)

    def on_entities_start[F: Callable](self, fn: F) -> F:
        return self._register("entities_start", fn)

    def on_entities_stop[F: Callable](self, fn: F) -> F:
        return self._register("entities_stop", fn)

    def _call(self, key: str, *args) -> None:
        fn = self._callbacks.get(key)
        if fn is not None:
            self._safe(fn, *args)

    def _awaiting_function(self, call: str, register):
        """``register`` as the decorator a call without a function returns. Whether it gets one
        can't be told yet, so begin() looks at what is still waiting."""
        self._without_function.append(call)

        def decorate(fn):
            if call in self._without_function:
                self._without_function.remove(call)
            return register(fn)
        return decorate

    def _safe(self, fn, *args):
        """Runs a script's function inside tick(). An exception is logged with its traceback and
        tick() goes on: one bad command must not end the device. Once a callback closed the
        device, no other runs."""
        if self._closed:
            return None
        try:
            return fn(*args)
        except Exception:
            log.exception("%s raised inside tick()", getattr(fn, "__qualname__", repr(fn)))
            return None

    # ----- transport and loop -----

    def begin(self, transport: object, *, max_clients: int | None = None,
              status_port: int | None = _DEFAULT,  # type: ignore[assignment]
              status_host: str | None = None) -> None:
        """Starts using a serial port or a listening TCP socket the script opened. Once only, and
        never after close().

        On TCP, up to ``max_clients`` connections (4 by default): the newest to send a BLAECK.
        command is the host, the others are terminals.

        A status page shows the device in a browser, on ``status_port`` (8080 by default, or a
        free one if that is taken; None for none). On TCP it listens where the device does. On
        serial, on this computer only, unless ``status_host`` says otherwise: "0.0.0.0" for every
        interface, so another computer can open it.
        """
        if self._began:
            raise RuntimeError("begin() was called already; a device begins once")
        if self._closed:
            raise RuntimeError("close() was called; a closed device can't begin")
        if self._without_function:
            raise ValueError(f"{self._without_function[0]} has no function: pass one, or use it "
                             "as a decorator")
        if max_clients is not None and not hasattr(transport, "accept"):
            raise TypeError("max_clients is for a TCP server, not a serial port")
        if status_host is not None:
            if hasattr(transport, "accept"):
                raise TypeError("status_host is for a serial port; on TCP the status page listens "
                                "where the device does")
            if not isinstance(status_host, str):
                raise TypeError("status_host is text, an address such as \"0.0.0.0\"")
        if status_port is _DEFAULT:
            status_port = _status.DEFAULT_PORT
        if status_port is not None and (isinstance(status_port, bool) or not isinstance(status_port, int)
                                        or not 0 <= status_port <= 65535):
            raise ValueError("status_port is 0 to 65535, or None for no status page")
        self._transport = _transport.transport_for(
            transport, 4 if max_clients is None else max_clients,
            self._connected,
            lambda slot, address, reason: self._disconnected(slot, address, reason))
        self._began = True
        self._last_command = None
        self._connection = _status.describe(self._transport)
        server = getattr(self._transport, "server", None)
        name = server.getsockname() if server is not None else None
        # (text, the part shown bold): the library and where it is reached stand out.
        if server is not None and name is not None and _status.is_everywhere(name[0]):
            # As Flask does: the addresses it can be reached on, not 0.0.0.0 alone.
            where = ("listening on ", f"all addresses ({name[0]})")
            more = [f"TCP {h}:{name[1]}" for h in _status.reachable_hosts(name[0], server.family)]
        elif self._connection.startswith("TCP"):
            where, more = ("listening on ", self._connection), []
        else:
            where, more = ("on ", self._connection[0].lower() + self._connection[1:]), []
        B, R = _console.BOLD, _console.RESET
        library = f"blaeckpy v{LIBRARY_VERSION}"
        banner = f"{library}: {self.name!r} {where[0]}{where[1]}"
        log.info("%s", banner, extra=_console.styled(
            f"{B}{library}{R}: {self.name!r} {where[0]}{B}{where[1]}{R}"))
        for line in more:
            log.info("  %s", line, extra=_console.styled(f"  {line}"))
        if status_port is not None:
            try:
                self._status = _status.StatusPage(self, status_port, status_host)
            except OSError as e:
                log.warning("No status page: %s", e)
            else:
                urls = self._status.urls
                if len(urls) == 1:
                    log.info("Status page: %s", urls[0],
                             extra=_console.styled(f"Status page: {_console.LINK}{urls[0]}"))
                else:
                    log.info("Status page:", extra=_console.styled("Status page:"))
                    for url in urls:
                        log.info("  %s", url, extra=_console.styled(f"  {_console.LINK}{url}"))

    def _connected(self, slot: int, address) -> None:
        log.info("Client %d connected: %s", slot, _status._address(address))
        self._call("connect", slot, address)

    def _disconnected(self, slot: int, address, reason: str | None) -> None:
        log.info("Client %d disconnected%s", slot, f": {reason}" if reason else "")
        self._call("disconnect", slot, address)

    def close(self) -> None:
        """Stops using the transport. A serial port or listening socket stays open, for the
        script to close; the connections blaeckpy accepted are closed. This is the end of the
        device: no callback runs for it, and no begin() may follow. Safe to call again."""
        self._closed = True
        if self._transport is not None:
            self._transport.close()
        self._transport = None
        self._connection = "Closed"
        if self._status is not None:
            self._status.close()
            self._status = None

    def on_connect[F: Callable](self, fn: F) -> F:
        """``fn(client_no, address)`` when a TCP connection opens: its slot, from 0, and the
        peer's ``(host, port)``."""
        return self._register("connect", fn)

    def on_disconnect[F: Callable](self, fn: F) -> F:
        """``fn(client_no, address)`` when a TCP connection closes or is replaced as host; not
        for those close() ends."""
        return self._register("disconnect", fn)

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def tick(self, timeout: float = 0.0, *, timestamp: int | None = None) -> None:
        """Reads a command and answers it, then sends what is due.

        With ``timeout`` (seconds, as paho-mqtt's ``loop(timeout)``), it then waits up to that
        long, answering commands as they arrive and sending what falls due in the meantime on
        time. ``timeout=0``, the default, never waits.

        ``timestamp`` stamps the data frames this call sends with a time of the script's own,
        such as when the values were measured, in microseconds in the timestamp mode's epoch.
        By name only, so a bare number is always the timeout. It belongs to the moment of the
        call, so it can't be combined with a timeout.
        """
        timestamp = _clock.check_timestamp(timestamp)
        if timeout and timestamp is not None:
            raise ValueError("pass a timestamp or a timeout, not both")
        if timeout < 0:
            raise ValueError("timeout is 0 or more seconds")
        if not timeout:
            self._hint_busy_loop()
            self._pass += 1
            self._read(timestamp)
            self._write_if_due(timestamp)
            self._refresh_status()
            return
        deadline = time.monotonic() + timeout
        while True:
            self._pass += 1
            self._read()
            self._write_if_due(None)
            self._refresh_status()
            now = time.monotonic()
            if now >= deadline:
                return
            if self._transport is not None and self._transport_pending():
                continue
            wait = deadline - now
            due = self._next_due_ms()
            if due is not None:
                wait = min(wait, max(0.0, (due - self._clock.millis()) / 1000))
            waitables: list = self._transport.selectables() if self._transport is not None else []
            if waitables:
                select.select(waitables, [], [], wait)
            else:
                # Nothing to wait on (no transport, or a serial port on Windows): poll in steps.
                time.sleep(min(wait, 0.01))

    def _refresh_status(self) -> None:
        if self._status is not None:
            self._status.refresh(self)

    def _transport_pending(self) -> bool:
        """A complete command already taken in, which select() would not wake for."""
        t = self._transport
        assert t is not None
        receivers = [t._receiver] if isinstance(t, _transport.StreamTransport) else \
            [c.receiver for c in t.slots if c is not None]
        return any(r.has_command() for r in receivers)

    def _next_due_ms(self) -> int | None:
        """When the next write falls due, by this device's clock, or None if nothing waits."""
        now = self._clock.millis()
        times = []
        if self._timed and self._may_write_data():
            times.append(now if self._timed_first or self._interval_ms == 0
                         else self._last_interval_ms + self._interval_ms)
        if self._may_write_data():
            for sig in self._signals:
                r = sig._reporting
                # While the clock fails a write is refused, so it isn't due; the caller's timeout
                # or the next interval tries again.
                if (r is not None and r.immediate and not self._clock_failing
                        and not self._is_missing(sig.device_id)
                        and changed(sig.type, sig.value, r, r.change_delta)):
                    times.append(r.last_write_ms + r.min_interval_ms if r.valid else now)
        if self._may_write_entities():
            for prop in self._properties:
                r = prop._reporting
                if not self._is_missing(prop.device_id) and prop._changed():
                    times.append(r.last_write_ms + r.min_interval_ms if r.valid else now)
        return min(times) if times else None

    def _hint_busy_loop(self) -> None:
        """One hint on the logger when tick() spins without ever waiting."""
        second = int(time.monotonic())
        if second != self._tick_second:
            self._tick_second, self._ticks_without_wait = second, 0
        self._ticks_without_wait += 1
        if self._ticks_without_wait == 1000 and not self._hinted:
            self._hinted = True
            log.info("tick() is called more than 1000 times a second; pass timeout= to let it "
                     "wait instead of using a whole CPU core")

    def write_all(self, timestamp: int | None = None) -> None:
        """Sends every signal now."""
        self._write_all(0, _clock.check_timestamp(timestamp))

    # ----- what read() does -----

    def _ready(self) -> bool:
        """A host to send frames to: on serial always, on TCP once one sent a BLAECK. command."""
        return self._transport is not None and self._transport.has_host()

    def _send(self, frame: bytes) -> bool:
        """False if the frame didn't go out whole, so nothing counts as delivered. Also after a
        callback closed the device."""
        if self._transport is None:
            return False
        return self._transport.send(frame)

    def _read(self, timestamp: int | None = None) -> None:
        if self._transport is None:
            return
        self._write_restarted()
        self._write_device_notices()
        # A write that failed may have dropped the host, and on_disconnect closed the device.
        if self._transport is None:
            return
        received = self._transport.receive()
        if received is not None:
            cmd = _commands.parse(*received)
            self._last_command = (cmd.name, time.time())
            # Before the truncation check, so even a cut-off built-in makes its sender the host.
            if cmd.name.startswith("BLAECK."):
                self._transport.builtin_received()
                # on_disconnect for the host it replaced may have closed the device.
                if self._closed:
                    return
            self._answering = True
            try:
                self._answer(cmd, timestamp)
                self._flush_catalogs()
            finally:
                self._answering = False
        else:
            # Send any catalog the script changed since, as blaeck's read() does on every call.
            self._flush_catalogs()

    def _write_restarted(self) -> None:
        # Held back until a host can hear it.
        if self._restart_reported or not self._ready():
            return
        # Tried again at the next tick if it didn't go out.
        if not self._send(frames.device_notification(0, frames.DeviceEvent.RESTARTED)):
            return
        self._restart_reported = True
        self._send_entity_list(0)

    def _ack(self, cmd: _commands.Command, reason: int) -> None:
        # A terminal's commands run, but only the host is answered.
        if self._transport is None or not self._ready() or not self._transport.requester_is_host():
            return
        name_hash = frames.fnv1a32(cmd.name.encode("utf-8", "surrogateescape")) if cmd.name else 0
        self._send(frames.command_ack(frames.fnv1a32(cmd.raw[cmd.prefix_len:]), name_hash, reason,
                                      cmd.message_id))

    def _answer(self, cmd: _commands.Command, timestamp: int | None = None) -> None:
        if cmd.truncated:
            self._ack(cmd, _ACK_TRUNCATED)
            return
        name, msg_id = cmd.name, cmd.message_id
        builtin = True
        if name == "BLAECK.WRITE_DATA":
            self._ack(cmd, _ACK_OK)
            self._requested = True
            try:
                self._write_all(msg_id, timestamp)
            finally:
                self._requested = False
        elif name == "BLAECK.GET_DEVICES":
            self._ack(cmd, _ACK_OK)
            self._write_devices(msg_id)
        elif name == "BLAECK.WRITE_ENTITIES":
            self._ack(cmd, _ACK_OK)
            self._send_entity_list(msg_id)
        elif name == "BLAECK.INTERVAL_START":
            interval = _commands.parse_interval(cmd.params[0]) if cmd.params else 0
            self._ack(cmd, _ACK_OK)
            self._set_timed(True, interval)
            self._call("interval_start", self._interval_ms)
        elif name == "BLAECK.INTERVAL_STOP":
            self._ack(cmd, _ACK_OK)
            self._set_timed(False, self._interval_ms)
            self._call("interval_stop")
        elif name == "BLAECK.DATA_START":
            self._data_stopped = False
            for s in self._signals:
                if s._reporting is not None:
                    s._reporting.valid = False
            self._ack(cmd, _ACK_OK)
            self._call("data_start")
        elif name == "BLAECK.DATA_STOP":
            self._ack(cmd, _ACK_OK)
            was_running = self._timed
            self._set_timed(False, self._interval_ms)
            if was_running:
                self._call("interval_stop")
            self._call("data_stop")
            self._data_stopped = True
        elif name == "BLAECK.ENTITIES_START":
            self._entities_stopped = False
            # Every property counts as changed at the next tick, as DATA_START does for signals.
            for p in self._properties:
                p._reporting.valid = False
            self._ack(cmd, _ACK_OK)
            self._call("entities_start")
        elif name == "BLAECK.ENTITIES_STOP":
            self._ack(cmd, _ACK_OK)
            self._call("entities_stop")
            self._entities_stopped = True
        else:
            builtin = False
        self._dispatch(cmd, send_ack=not builtin)

    def _dispatch(self, cmd: _commands.Command, send_ack: bool) -> None:
        """An input, a button or a plain command, then the catch-all, then the one ack."""
        if not cmd.name:
            return
        reason, matched, written = _ACK_UNKNOWN, False, None
        prop = next((p for p in self._properties if p.name == cmd.name), None)
        if prop is not None:
            matched = True
            if self._is_missing(prop.device_id):
                reason = _properties.DEVICE_NOT_RESPONDING
            else:
                reason = prop._receive(cmd.params)
            if reason == _ACK_OK:
                written = prop
        if not matched:
            button = next((b for b in self._buttons if b.name == cmd.name), None)
            command = self._plain_commands.get(cmd.name)
            target = button.device_id if button is not None else (command[0] if command else None)
            if target is not None:
                matched = True
                # Refused for a missing device, so its function doesn't forward to nothing.
                if self._is_missing(target):
                    reason = _properties.DEVICE_NOT_RESPONDING
                else:
                    reason = _ACK_OK
                    if button is not None:
                        self._safe(button.press)
                    elif command is not None:
                        self._safe(command[1], *cmd.params)
        if self._any_command is not None:
            took = self._safe(self._any_command, cmd.name, *cmd.params)
            if not matched and took is True:
                matched, reason = True, _ACK_OK
        if send_ack:
            self._ack(cmd, reason)
        if written is not None:
            if written._on_change is not None:
                self._safe(written._on_change)
            # on_change may have removed it, as clear_all_controls() does.
            if any(p is written for p in self._properties):
                self._write_property_frame(written)

    # ----- the entity list, property values and events -----

    def _entries(self) -> list[frames.Entry]:
        entries = [p._entry(p._current()) for p in self._properties]
        entries += [e._entry() for e in self._events]
        entries += [b._entry() for b in self._buttons]
        return entries

    def _send_entity_list(self, msg_id: int) -> None:
        self._catalog_dirty = not self._send(frames.entity_list(self._entries(), msg_id))

    def _flush_catalogs(self) -> bool:
        if self._catalog_dirty and self._ready():
            self._send_entity_list(0)
        return not self._catalog_dirty

    def _may_write_entities(self) -> bool:
        return (not self._entities_stopped or self._answering) and self._ready()

    def _write_property_frame(self, prop) -> bool:
        if not self._may_write_entities():
            return False
        # A host must know the property before a value of it arrives.
        if not self._flush_catalogs():
            return False
        value = prop._current()
        if not self._send(frames.property_value(self._properties.index(prop), prop.dtype, value)):
            return False
        prop._reporting.valid = True
        prop._reporting.baseline = value
        prop._reporting.last_write_ms = self._clock.millis()
        return True

    def _write_changed_properties(self) -> None:
        # Nothing can go out, so no get= needs to read.
        if not self._may_write_entities():
            return
        now = self._clock.millis()
        for prop in self._properties:
            if self._is_missing(prop.device_id):
                continue
            r = prop._reporting
            if r.valid and now - r.last_write_ms < r.min_interval_ms:
                continue
            if prop._changed() and not self._write_property_frame(prop):
                return

    def _write_event(self, event: Event, type_index: int) -> None:
        if not self._may_write_entities():
            return
        if not self._flush_catalogs():
            return
        self._send(frames.event(self._events.index(event), type_index))

    def _set_timed(self, on: bool, interval_ms: int) -> None:
        self._timed = on
        if on:
            self._interval_ms = interval_ms
            self._timed_first = True

    def _write_devices(self, msg_id: int) -> None:
        def signals_of(device_id):
            return [(sig.name, sig.unit, sig.dtype) for sig in self._signals if sig.device_id == device_id]
        entries = [frames.DeviceEntry(0, self.name, self.hw_version, self.fw_version, signals_of(0),
                                      restarted=not self._restart_reported)]
        entries += [frames.DeviceEntry(d._id, d.name, d.hw_version, d.fw_version, signals_of(d._id),
                                       not_responding=d._missing, restarted=d._restart_pending)
                    for d in self._devices]
        if not self._send(frames.device_list(LIBRARY_NAME, LIBRARY_VERSION,
                                             _commands.COMMAND_MAX_CHARS - 1, entries, msg_id)):
            return
        # The list told the host every state, so no notice for it is still due.
        self._restart_reported = True
        for d in self._devices:
            d._restart_pending = False
            d._reported_missing = d._missing

    # ----- data -----

    def _now(self) -> int | None:
        """The timestamp a data frame carries now; None when the script's clock failed, logged the
        first time in a row: like any other function of the script's, it must not stop the device."""
        try:
            value = _clock.timestamp(self._timestamp_mode, self._clock, self._timestamp_clock)
            if not 0 <= value < 2**64:
                raise ValueError(f"{value} does not fit a timestamp")
        except Exception:
            if not self._clock_failing:
                self._clock_failing = True
                log.exception("The timestamp clock failed; no data goes out until it works again")
            return None
        self._clock_failing = False
        return value

    def _may_write_data(self) -> bool:
        return (not self._data_stopped or self._answering) and self._ready()

    def _write_all(self, msg_id: int, timestamp: int | None) -> None:
        if not self._signals or self._closed:
            return
        if self._before_write is not None:
            self._safe(self._before_write)
        self._write_data(msg_id, self._signals, timestamp)

    def _write_signal_now(self, signal: Signal, timestamp: int | None) -> None:
        if self._is_missing(signal.device_id):
            log.debug("write() dropped for %r: its device is marked missing", signal.name)
            return
        self._write_data(0, [signal], timestamp)

    def _write_if_due(self, timestamp: int | None) -> None:
        if self._closed:
            return
        now = self._clock.millis()
        elapsed = now - self._last_interval_ms
        initial = self._timed and self._timed_first
        due = self._timed and (self._timed_first or self._interval_ms == 0
                               or elapsed >= self._interval_ms)
        if due:
            if self._timed_first or self._interval_ms == 0:
                self._last_interval_ms = now
            else:
                self._last_interval_ms += elapsed - elapsed % self._interval_ms
            if self._signals and self._before_write is not None:
                self._safe(self._before_write)
        if not self._may_write_data():
            # Property changes don't depend on DATA_STOP; only data waits.
            self._write_changed_properties()
            return
        if due:
            self._timed_first = False

        change_now = self._clock.millis()
        selected, interval_report = [], False
        for s in self._signals:
            r = s._reporting
            interval = due and (s.interval_mode is ALWAYS or (
                isinstance(s.interval_mode, OnChange) and r is not None
                and (initial or changed(s.type, s.value, r, r.interval_delta))))
            immediate = (r is not None and r.immediate
                         and (not r.valid or change_now - r.last_write_ms >= r.min_interval_ms)
                         and changed(s.type, s.value, r, r.change_delta))
            if interval or immediate:
                selected.append(s)
            interval_report |= interval
        self._write_data(0, selected, timestamp, interval_report)
        self._write_changed_properties()

    def _write_data(self, msg_id: int, selected: list[Signal], timestamp: int | None,
                    interval_report: bool = False) -> None:
        if not self._may_write_data():
            return
        # A missing device's signals are left out; the host learned of it from C1 or B7.
        chosen = {id(sig) for sig in selected if not self._is_missing(sig.device_id)}
        if not chosen:
            return
        if timestamp is None:
            timestamp = self._now()
            if timestamp is None:
                return
        flags = frames.FIRST_AFTER_RESTART if self._restart_flag else 0
        if self._requested:
            flags |= frames.ANSWERS_WRITE_DATA
        elif interval_report:
            flags |= frames.INTERVAL_REPORT
        ordered = self._ordered_signals()
        values = [(i, s.dtype, s.value) for i, s in enumerate(ordered) if id(s) in chosen]
        has_ts = self._timestamp_mode is not TimestampMode.NONE
        hash_ = schema_hash([(None if s.device_id == 0 else self._devices[s.device_id - 1].name,
                              s.name, s.unit, s.dtype) for s in ordered])
        if not self._send(frames.data(values, schema_hash=hash_, flags=flags,
                                      timestamp_mode=_clock.WIRE[self._timestamp_mode],
                                      timestamp=timestamp if has_ts else None, message_id=msg_id)):
            return
        sent_at = self._clock.millis()
        for s in self._signals:
            if id(s) in chosen and s._reporting is not None:
                s._reporting.valid = True
                s._reporting.last_write_ms = sent_at
                s._reporting.baseline = s.value
        self._restart_flag = False
