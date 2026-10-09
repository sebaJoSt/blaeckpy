"""Blaeck and its sub-devices: registration, tick(), built-in commands."""

from __future__ import annotations

from collections.abc import Callable
from importlib.metadata import version as _version

from . import _clock, _commands, _transport
from ._clock import TimestampMode
from ._signal import ALWAYS, OnChange, Signal, check_name, changed, type_and_value
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


class Blaeck:
    """A device a host such as Loggbok logs and controls, over serial or TCP.

    Declare signals, then :meth:`begin` with an opened port and call :meth:`tick` in a loop.
    """

    def __init__(self, name: str = "Unnamed", *, hw_version: str = "n/a", fw_version: str = "n/a"):
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

    # ----- registration -----

    def add_signal(self, name: str, type_or_initial, initial=None, *, write_at_interval=ALWAYS,
                   write_on_change: float | None = None, min_interval_ms: int = 100) -> Signal:
        """A value the device logs: ``add_signal("T", float)``, ``add_signal("T", float, 21.5)``
        or ``add_signal("T", 21.5)``."""
        name = check_name(name, "signal")
        if any(s.name == name and s.device_id == 0 for s in self._signals):
            raise ValueError(f"signal {name!r} exists already")
        type_, value = type_and_value(type_or_initial, initial)
        signal = Signal(self, 0, name, type_, value, write_at_interval, write_on_change,
                        min_interval_ms)
        self._signals.append(signal)
        return signal

    def set_timestamp_mode(self, mode: TimestampMode, clock: Callable[[], int] | None = None) -> None:
        """How data frames are timestamped; ``clock`` returns microseconds, replacing the default."""
        self._timestamp_mode = TimestampMode(mode)
        self._timestamp_clock = clock

    @property
    def timestamp_mode(self) -> TimestampMode:
        return self._timestamp_mode

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
            fn(*args)

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
        self._receiver.feed(self._transport.read_available())
        received = self._receiver.next_command()
        if received is None:
            return
        self._answering = True
        try:
            self._answer(_commands.parse(*received))
        finally:
            self._answering = False

    def _write_restarted(self) -> None:
        if self._restart_reported:
            return
        self._restart_reported = True
        self._send(frames.device_notification(0, frames.DeviceEvent.RESTARTED))
        self._send(frames.entity_list([]))

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
            self._send(frames.entity_list([], msg_id))
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
            self._ack(cmd, _ACK_OK)
            self._call("entities_start")
        elif name == "BLAECK.ENTITIES_STOP":
            self._ack(cmd, _ACK_OK)
            self._call("entities_stop")
            self._entities_stopped = True
        else:
            builtin = False
        if not builtin and name:
            self._ack(cmd, _ACK_UNKNOWN)

    def _set_timed(self, on: bool, interval_ms: int) -> None:
        self._timed = on
        if on:
            self._interval_ms = interval_ms
            self._timed_first = True

    def _write_devices(self, msg_id: int) -> None:
        board = frames.DeviceEntry(0, self.name, self.hw_version, self.fw_version,
                                   [(s.name, s.dtype) for s in self._signals],
                                   restarted=not self._restart_reported)
        self._send(frames.device_list(LIBRARY_NAME, LIBRARY_VERSION,
                                      _commands.COMMAND_MAX_CHARS - 1, [board], msg_id))
        self._restart_reported = True

    # ----- data -----

    def _now(self) -> int:
        return _clock.timestamp(self._timestamp_mode, self._clock, self._timestamp_clock)

    def _may_write_data(self) -> bool:
        return (not self._data_stopped or self._answering) and self._ready()

    def _write_all(self, msg_id: int, timestamp: int) -> None:
        if not self._signals:
            return
        if self._before_write is not None:
            self._before_write()
        self._write_data(msg_id, self._signals, timestamp)

    def _write_signal_now(self, signal: Signal, timestamp: int | None) -> None:
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
                self._before_write()
        if not self._may_write_data():
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

    def _write_data(self, msg_id: int, selected: list[Signal], timestamp: int,
                    interval_report: bool = False) -> None:
        if not self._may_write_data() or not selected:
            return
        chosen = set(map(id, selected))
        flags = frames.FIRST_AFTER_RESTART if self._restart_flag else 0
        if self._requested:
            flags |= frames.ANSWERS_WRITE_DATA
        elif interval_report:
            flags |= frames.INTERVAL_REPORT
        values = [(i, s.dtype, s.value) for i, s in enumerate(self._signals) if id(s) in chosen]
        has_ts = self._timestamp_mode is not TimestampMode.NONE
        hash_ = schema_hash([(None, s.name, s.dtype) for s in self._signals])
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
