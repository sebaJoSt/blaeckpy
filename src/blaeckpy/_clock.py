"""Timestamp modes: none, micros, UNIX. And the clock everything else is timed by."""

from __future__ import annotations

import time
from collections.abc import Callable
from enum import StrEnum


class TimestampMode(StrEnum):
    """How data frames are timestamped. Strings work too: "none", "micros", "unix"."""

    NONE = "none"
    #: Microseconds since the device started, from a monotonic clock.
    MICROS = "micros"
    #: Microseconds since 1970-01-01 UTC, from the system clock unless a clock is given.
    UNIX = "unix"


# What a data frame says the mode is.
WIRE = {TimestampMode.NONE: 0, TimestampMode.MICROS: 1, TimestampMode.UNIX: 2}


class Clock:
    """Monotonic time since the device was created: what intervals and minimum intervals count."""

    def __init__(self) -> None:
        self._start = time.monotonic_ns()

    def millis(self) -> int:
        return (time.monotonic_ns() - self._start) // 1_000_000

    def micros(self) -> int:
        return (time.monotonic_ns() - self._start) // 1_000


def check_timestamp(timestamp: int | None) -> int | None:
    """A time a script gives: None for now, or whole microseconds that fit 64 bits."""
    if timestamp is None:
        return None
    if isinstance(timestamp, bool) or not isinstance(timestamp, int):
        raise TypeError(f"a timestamp is a whole number of microseconds, not {timestamp!r}")
    if not 0 <= timestamp < 2**64:
        raise ValueError(f"timestamp {timestamp} does not fit 64 bits")
    return timestamp


def unix_micros() -> int:
    return time.time_ns() // 1_000


def timestamp(mode: TimestampMode, clock: Clock, user_clock: Callable[[], int] | None) -> int:
    """The timestamp a data frame carries now; 0 without a mode."""
    if mode is TimestampMode.MICROS:
        return int(user_clock()) if user_clock is not None else clock.micros()
    if mode is TimestampMode.UNIX:
        return int(user_clock()) if user_clock is not None else unix_micros()
    return 0
