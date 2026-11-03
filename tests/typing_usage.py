# pyright: reportUnnecessaryTypeIgnoreComment=true
"""What a type checker infers from a script. Not run: tests/test_typing.py checks it with mypy
and basedpyright. A line meant to be flagged carries ``# type: ignore``; both checkers report the
comment if it ever goes unneeded."""

from collections.abc import Callable
from typing import assert_type

from blaeckpy import Blaeck, Button, NumberInput, Select, Sensor, Signal, Switch, TextInput

device = Blaeck("Bench")

assert_type(device.add_signal("A", float), Signal[float])
assert_type(device.add_signal("B", 21.5), Signal[float])
assert_type(device.add_signal("C", float, 21.5), Signal[float])
assert_type(device.add_signal("D", float, 21), Signal[float])
assert_type(device.add_signal("E", int), Signal[int])
assert_type(device.add_signal("F", True), Signal[bool])
assert_type(device.add_signal("G", "Idle"), Signal[str])

temp = device.add_signal("Temp", 21.5)
count = device.add_signal("Count", 0)
assert_type(temp.value, float)
temp.value += 0.5
temp.value = 3
count.value += 1
count.value = 0.5  # type: ignore

assert_type(device.add_number_input("N1", 20.0), NumberInput[float])
assert_type(device.add_number_input("N2", int, 5, min=0, max=10), NumberInput[int])
setpoint = device.add_number_input("Setpoint", 20.0)
setpoint.value = min(setpoint.value + 2.0, 30.0)

fan = device.add_switch("Fan")
assert_type(fan, Switch)
assert_type(fan.value, bool)
fan.value = "on"  # type: ignore

mode = device.add_select("Mode", options=["Off", "Heat"])
assert_type(mode, Select)
assert_type(mode.value, int)
assert_type(mode.option, str)

note = device.add_text_input("Note")
assert_type(note, TextInput)
assert_type(note.value, str)


def read_load() -> float:
    return 0.5


assert_type(device.add_sensor("S1", temp), Sensor[float])
assert_type(device.add_sensor("S2", float, get=read_load), Sensor[float])
assert_type(device.add_sensor("S3", 21.5), Sensor[float])
assert_type(device.add_sensor("S4", 0, options=["Idle", "Run"]), Sensor[int])
assert_type(device.add_sensor("S5", True).value, bool)

# A button returns the Button, or as a decorator leaves the function as it is.
assert_type(device.add_button("Reset", lambda: None), Button)


@device.add_button("Boost")
def boost() -> None: ...


assert_type(boost, Callable[[], None])
