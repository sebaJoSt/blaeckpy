"""Timestamp modes: none, micros, UNIX. And the clock everything else is timed by."""

from __future__ import annotations

import time
from collections.abc import Callable
from enum import IntEnum


class TimestampMode(IntEnum):
    """How data frames are timestamped. The values are the protocol's."""

    NONE = 0
    #: Microseconds since the device started, from a monotonic clock.
    MICROS = 1
    #: Microseconds since 1970-01-01 UTC, from the system clock unless a clock is given.
    UNIX = 2


class Clock:
    """Monotonic time since the device was created: what intervals and minimum intervals count."""

    def __init__(self) -> None:
        self._start = time.monotonic_ns()

    def millis(self) -> int:
        return (time.monotonic_ns() - self._start) // 1_000_000

    def micros(self) -> int:
        return (time.monotonic_ns() - self._start) // 1_000


def unix_micros() -> int:
    return time.time_ns() // 1_000


def timestamp(mode: TimestampMode, clock: Clock, user_clock: Callable[[], int] | None) -> int:
    """The timestamp a data frame carries now; 0 without a mode."""
    if mode is TimestampMode.MICROS:
        return int(user_clock()) if user_clock is not None else clock.micros()
    if mode is TimestampMode.UNIX:
        return int(user_clock()) if user_clock is not None else unix_micros()
    return 0
