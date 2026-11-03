"""Blaeck and its sub-devices: registration, tick(), built-in commands."""

from __future__ import annotations

import logging
from collections.abc import Callable
from importlib.metadata import version as _version

from . import _clock, _commands, _properties, _transport
from ._clock import TimestampMode
from ._events import Button, Event
from ._properties import NumberInput, Presentation, Select, Sensor, Switch, TextInput, check_host_name
from ._signal import ALWAYS, OnChange, Signal, check_name, check_unit, changed, type_and_value
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


class _DeviceBase:
    """What the board and each sub-device declare: signals, inputs, sensors, events, buttons and
    plain commands. A sub-device's are listed under it; names a host sends stay unique on the
    whole board."""

    _core: "Blaeck"
    _id: int

    # ----- registration -----

    def add_signal(self, name: str, type_or_initial, initial=None, *, unit: str | None = None,
                   write_at_interval=ALWAYS, write_on_change: float | None = None,
                   min_interval_ms: int = 100) -> Signal:
        """A value the device logs: ``add_signal("T", float)``, ``add_signal("T", float, 21.5)``
        or ``add_signal("T", 21.5)``. ``unit`` in Home Assistant's spelling, such as "°C"."""
        name = check_name(name, "signal")
        if any(s.name == name and s.device_id == self._id for s in self._core._signals):
            raise ValueError(f"signal {name!r} exists already")
        type_, value = type_and_value(type_or_initial, initial)
        signal = Signal(self._core, self._id, name, type_, value, write_at_interval, write_on_change,
                        min_interval_ms, check_unit(unit))
        self._core._signals.append(signal)
        return signal

    # ----- entities: what a host shows and controls -----

    def add_number_input(self, name: str, type_or_initial, initial=None, *, min=None, max=None,
                         step=None, mode=None, unit=None, display_name=None, icon=None,
                         device_class=None, state_class=None, display_precision=None,
                         category="none", disabled_by_default=False, force_update=False,
                         write_on_change: float = 0.0, min_interval_ms: int = 100) -> NumberInput:
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

    def add_switch(self, name: str, initial: bool = False, *, display_name=None, icon=None,
                   device_class=None, category="none", disabled_by_default=False,
                   force_update=False, min_interval_ms: int = 100) -> Switch:
        name = self._core._host_name(name, "switch")
        presentation = Presentation(number=False, display_name=display_name, icon=icon,
                                    device_class=device_class, category=category,
                                    disabled_by_default=disabled_by_default, force_update=force_update)
        return self._core._add_property(Switch(self._core, self._id, name, initial, presentation=presentation,
                                         min_interval_ms=min_interval_ms))

    def add_select(self, name: str, index: int = 0, *, options, display_name=None, icon=None,
                   device_class=None, category="none", disabled_by_default=False,
                   force_update=False, min_interval_ms: int = 100) -> Select:
        """One of ``options``, held as its index; a host sends the index or the option's name."""
        name = self._core._host_name(name, "select")
        presentation = Presentation(number=False, display_name=display_name, icon=icon,
                                    device_class=device_class, category=category,
                                    disabled_by_default=disabled_by_default, force_update=force_update)
        return self._core._add_property(Select(self._core, self._id, name, index, options=options,
                                         presentation=presentation, min_interval_ms=min_interval_ms))

    def add_text_input(self, name: str, initial: str = "", *, max_length: int = 255, mode=None,
                       display_name=None, icon=None, device_class=None, category="none",
                       disabled_by_default=False, force_update=False,
                       min_interval_ms: int = 100) -> TextInput:
        name = self._core._host_name(name, "text input")
        presentation = Presentation(number=False, display_name=display_name, icon=icon,
                                    device_class=device_class, category=category,
                                    disabled_by_default=disabled_by_default, force_update=force_update)
        return self._core._add_property(TextInput(self._core, self._id, name, initial, max_length=max_length, mode=mode,
                                            presentation=presentation, min_interval_ms=min_interval_ms))

    def add_sensor(self, name: str, source, initial=None, *, get=None, options=None,
                   write_on_change: float = 0.0, min_interval_ms: int = 100,
                   **presentation) -> Sensor:
        """A value a host shows: ``add_sensor("T", signal)`` shows a signal's value,
        ``add_sensor("T", float, 21.5)`` or ``add_sensor("T", 21.5)`` holds its own,
        ``add_sensor("T", float, get=fn)`` reads a function, and
        ``add_sensor("Phase", 0, options=[...])`` holds the index of an option."""
        name = self._core._host_name(name, "sensor")
        return self._core._add_property(Sensor(self._core, self._id, name, source, initial, get=get, options=options,
                                         presentation=presentation, write_on_change=write_on_change,
                                         min_interval_ms=min_interval_ms))

    def add_event(self, name: str, types, *, display_name=None, icon=None, device_class=None,
                  category="none", disabled_by_default=False) -> Event:
        """A moment the device reports, of one of ``types``; ``event.write(type)`` reports it."""
        name = check_name(name, "event")
        if any(e.name == name and e.device_id == self._id for e in self._core._events):
            raise ValueError(f"event {name!r} exists already")
        presentation = Presentation(number=False, display_name=display_name, icon=icon,
                                    device_class=device_class, category=category,
                                    disabled_by_default=disabled_by_default)
        event = Event(self._core, self._id, name, types, presentation)
        self._core._events.append(event)
        self._core._catalog_changed()
        return event

    def add_button(self, name: str, press: Callable[[], None], *, display_name=None, icon=None,
                   device_class=None, category="none", disabled_by_default=False) -> Button:
        """A button a host presses; ``press`` runs with no arguments, parameters are ignored."""
        name = self._core._host_name(name, "button")
        presentation = Presentation(number=False, display_name=display_name, icon=icon,
                                    device_class=device_class, category=category,
                                    disabled_by_default=disabled_by_default)
        button = Button(self._id, name, press, presentation)
        self._core._buttons.append(button)
        self._core._catalog_changed()
        return button

    def on_command(self, name: str, handler: Callable | None = None):
        """A command a host sends by name and no host lists: ``handler(*params)`` runs with its
        parameters as text. Works as ``@device.on_command("MOTOR")`` or as a plain call."""
        def register(fn):
            if not callable(fn):
                raise TypeError("a command needs a function")
            self._core._plain_commands[self._core._host_name(name, "command")] = (self._id, fn)
            return fn
        return register if handler is None else register(handler)



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
                         core._now() if timestamp is None else timestamp)


class Blaeck(_DeviceBase):
    """A device a host such as Loggbok logs and controls, over serial or TCP.

    Declare signals, then :meth:`begin` with an opened port and call :meth:`tick` in a loop.
    """

    def __init__(self, name: str = "Unnamed", *, hw_version: str = "n/a", fw_version: str = "n/a"):
        self._core, self._id = self, 0
        self._devices: list[SubDevice] = []
        self.name = check_name(name, "device")
        self.hw_version = hw_version
        self.fw_version = fw_version
        self._signals: list[Signal] = []
        self._transport = None
        self._receiver = _commands.Receiver()
        self._clock = _clock.Clock()
        self._timestamp_mode = TimestampMode.NONE
        self._timestamp_clock: Callable[[], int] | None = None

        # What the host switched on and off. A device starts silent.
        self._data_stopped = True
        self._entities_stopped = True
        self._timed = False
        self._timed_first = False
        self._interval_ms = 1000
        self._last_interval_ms = 0

        self._answering = False
        self._requested = False
        self._restart_reported = False
        self._restart_flag = True

        self._before_write: Callable[[], None] | None = None
        self._callbacks: dict[str, Callable] = {}

        self._properties: list[_properties.Property] = []
        self._events: list[Event] = []
        self._buttons: list[Button] = []
        self._plain_commands: dict[str, Callable] = {}
        self._any_command: Callable | None = None
        self._catalog_dirty = False

    def set_timestamp_mode(self, mode: TimestampMode, clock: Callable[[], int] | None = None) -> None:
        """How data frames are timestamped; ``clock`` returns microseconds, replacing the default."""
        self._timestamp_mode = TimestampMode(mode)
        self._timestamp_clock = clock


    def add_device(self, name: str, *, hw_version: str = "n/a", fw_version: str = "n/a") -> SubDevice:
        """A device a host shows below this board, such as a second board it polls."""
        name = check_name(name, "device")
        if name == self.name or any(d.name == name for d in self._devices):
            raise ValueError(f"device {name!r}: no two devices on a board, the board included, "
                             "share a name")
        if len(self._devices) >= 254:
            raise ValueError("a board has at most 254 devices")
        device = SubDevice(self, len(self._devices) + 1, name, hw_version, fw_version)
        self._devices.append(device)
        return device

    def _device(self, device_id: int) -> SubDevice | None:
        return self._devices[device_id - 1] if device_id > 0 else None

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
        for d in self._devices:
            if d._restart_pending:
                self._send(frames.device_notification(d._id, frames.DeviceEvent.RESTARTED))
                d._restart_pending = False
            if d._missing != d._reported_missing:
                event = frames.DeviceEvent.NOT_RESPONDING if d._missing else frames.DeviceEvent.RESPONDING_AGAIN
                self._send(frames.device_notification(d._id, event))
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
        self._properties.append(prop)
        self._catalog_changed()
        return prop

    def _catalog_changed(self) -> None:
        self._catalog_dirty = True

    def on_any_command(self, fn: Callable):
        """Sees every command, after its own function: ``fn(command, *params)``. Its answer
        counts only where nothing took the command: True answers OK, False or None UNKNOWN."""
        self._any_command = fn
        return fn

    # ----- callbacks: each works as a decorator or a plain call -----

    def _register(self, key: str, fn: Callable) -> Callable:
        self._callbacks[key] = fn
        return fn

    def on_before_write(self, fn):
        self._before_write = fn
        return fn

    def on_data_start(self, fn):
        return self._register("data_start", fn)

    def on_data_stop(self, fn):
        return self._register("data_stop", fn)

    def on_interval_start(self, fn):
        return self._register("interval_start", fn)

    def on_interval_stop(self, fn):
        return self._register("interval_stop", fn)

    def on_entities_start(self, fn):
        return self._register("entities_start", fn)

    def on_entities_stop(self, fn):
        return self._register("entities_stop", fn)

    def _call(self, key: str, *args) -> None:
        fn = self._callbacks.get(key)
        if fn is not None:
            self._safe(fn, *args)

    def _safe(self, fn, *args):
        """Runs a script's function inside tick(). An exception is logged with its traceback and
        tick() goes on: one bad command must not end the device."""
        try:
            return fn(*args)
        except Exception:
            log.exception("%s raised inside tick()", getattr(fn, "__qualname__", repr(fn)))
            return None

    # ----- transport and loop -----

    def begin(self, transport) -> None:
        """Starts using an opened serial port. Once only."""
        if self._transport is not None:
            raise RuntimeError("begin() was called already")
        self._transport = _transport.transport_for(transport)

    def close(self) -> None:
        """Stops using the transport; it stays open, for the script to close."""
        self._transport = None

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def tick(self, timestamp: int | None = None, timeout: float = 0.0) -> None:
        """Reads a command and answers it, then sends what is due."""
        if timeout:
            raise NotImplementedError("tick(timeout=) comes with the TCP transport")
        self._read()
        self._write_if_due(timestamp)

    def write_all(self, timestamp: int | None = None) -> None:
        """Sends every signal now."""
        self._write_all(0, self._now() if timestamp is None else timestamp)

    # ----- what read() does -----

    def _ready(self) -> bool:
        return self._transport is not None

    def _send(self, frame: bytes) -> None:
        self._transport.write(frame)

    def _read(self) -> None:
        if not self._ready():
            return
        self._write_restarted()
        self._write_device_notices()
        self._receiver.feed(self._transport.read_available())
        received = self._receiver.next_command()
        if received is None:
            return
        self._answering = True
        try:
            self._answer(_commands.parse(*received))
            # Send any catalog a handler changed.
            self._flush_catalogs()
        finally:
            self._answering = False

    def _write_restarted(self) -> None:
        if self._restart_reported:
            return
        self._restart_reported = True
        self._send(frames.device_notification(0, frames.DeviceEvent.RESTARTED))
        self._send_entity_list(0)

    def _ack(self, cmd: _commands.Command, reason: int) -> None:
        name_hash = frames.fnv1a32(cmd.name.encode("utf-8", "surrogateescape")) if cmd.name else 0
        self._send(frames.command_ack(frames.fnv1a32(cmd.raw[cmd.prefix_len:]), name_hash, reason,
                                      cmd.message_id))

    def _answer(self, cmd: _commands.Command) -> None:
        if cmd.truncated:
            self._ack(cmd, _ACK_TRUNCATED)
            return
        name, msg_id = cmd.name, cmd.message_id
        builtin = True
        if name == "BLAECK.WRITE_DATA":
            self._ack(cmd, _ACK_OK)
            self._requested = True
            try:
                self._write_all(msg_id, self._now())
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
                    else:
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
            self._write_property_frame(written)

    # ----- the entity list, property values and events -----

    def _entries(self) -> list[frames.Entry]:
        entries = [p._entry(p.value) for p in self._properties]
        entries += [e._entry() for e in self._events]
        entries += [b._entry() for b in self._buttons]
        return entries

    def _send_entity_list(self, msg_id: int) -> None:
        self._send(frames.entity_list(self._entries(), msg_id))
        self._catalog_dirty = False

    def _flush_catalogs(self) -> None:
        if self._catalog_dirty and self._ready():
            self._send_entity_list(0)

    def _may_write_entities(self) -> bool:
        return (not self._entities_stopped or self._answering) and self._ready()

    def _write_property_frame(self, prop) -> bool:
        if not self._may_write_entities():
            return False
        # A host must know the property before a value of it arrives.
        self._flush_catalogs()
        value = prop.value
        self._send(frames.property_value(self._properties.index(prop), prop.dtype, value))
        prop._reporting.valid = True
        prop._reporting.baseline = value
        prop._reporting.last_write_ms = self._clock.millis()
        return True

    def _write_changed_properties(self) -> None:
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
        self._flush_catalogs()
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
        self._send(frames.device_list(LIBRARY_NAME, LIBRARY_VERSION,
                                      _commands.COMMAND_MAX_CHARS - 1, entries, msg_id))
        # The list told the host every state, so no notice for it is still due.
        self._restart_reported = True
        for d in self._devices:
            d._restart_pending = False
            d._reported_missing = d._missing

    # ----- data -----

    def _now(self) -> int:
        return _clock.timestamp(self._timestamp_mode, self._clock, self._timestamp_clock)

    def _may_write_data(self) -> bool:
        return (not self._data_stopped or self._answering) and self._ready()

    def _write_all(self, msg_id: int, timestamp: int) -> None:
        if not self._signals:
            return
        if self._before_write is not None:
            self._safe(self._before_write)
        self._write_data(msg_id, self._signals, timestamp)

    def _write_signal_now(self, signal: Signal, timestamp: int | None) -> None:
        if self._is_missing(signal.device_id):
            log.debug("write() dropped for %r: its device is marked missing", signal.name)
            return
        self._write_data(0, [signal], self._now() if timestamp is None else timestamp)

    def _write_if_due(self, timestamp: int | None) -> None:
        if timestamp is None:
            timestamp = self._now()
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
                isinstance(s.interval_mode, OnChange)
                and (initial or changed(s.type, s.value, r, r.interval_delta))))
            immediate = (r is not None and r.immediate
                         and (not r.valid or change_now - r.last_write_ms >= r.min_interval_ms)
                         and changed(s.type, s.value, r, r.change_delta))
            if interval or immediate:
                selected.append(s)
            interval_report |= interval
        self._write_data(0, selected, timestamp, interval_report)
        self._write_changed_properties()

    def _write_data(self, msg_id: int, selected: list[Signal], timestamp: int,
                    interval_report: bool = False) -> None:
        if not self._may_write_data():
            return
        # A missing device's signals are left out; the host learned of it from C1 or B7.
        chosen = {id(sig) for sig in selected if not self._is_missing(sig.device_id)}
        if not chosen:
            return
        flags = frames.FIRST_AFTER_RESTART if self._restart_flag else 0
        if self._requested:
            flags |= frames.ANSWERS_WRITE_DATA
        elif interval_report:
            flags |= frames.INTERVAL_REPORT
        ordered = self._ordered_signals()
        values = [(i, s.dtype, s.value) for i, s in enumerate(ordered) if id(s) in chosen]
        has_ts = self._timestamp_mode is not TimestampMode.NONE
        hash_ = schema_hash([(None if s.device_id == 0 else self._device(s.device_id).name,
                              s.name, s.unit, s.dtype) for s in ordered])
        self._send(frames.data(values, schema_hash=hash_, flags=flags,
                               timestamp_mode=self._timestamp_mode,
                               timestamp=timestamp if has_ts else None, message_id=msg_id))
        sent_at = self._clock.millis()
        for s in self._signals:
            if id(s) in chosen and s._reporting is not None:
                s._reporting.valid = True
                s._reporting.last_write_ms = sent_at
                s._reporting.baseline = s.value
        self._restart_flag = False
