"""blaeckpy: the Python counterpart of the blaeck Arduino library.

Speaks protocol v2 as blaeck 7 does, so a host such as Loggbok cannot tell the two apart.
In development: signals, data and the built-in commands work; properties, events, buttons,\nsub-devices and TCP follow.
"""

from importlib.metadata import version as _version

from ._clock import TimestampMode
from ._device import Blaeck
from ._events import Button, Event
from ._properties import Category, Mode, NumberInput, Select, Sensor, StateClass, Switch, TextInput
from ._signal import ALWAYS, ANY_CHANGE, OFF, OnChange, Signal

__version__ = _version("blaeckpy")

__all__ = ["ALWAYS", "ANY_CHANGE", "Blaeck", "Button", "Category", "Event", "Mode", "NumberInput",
           "OFF", "OnChange", "Select", "Sensor", "Signal", "StateClass", "Switch", "TextInput",
           "TimestampMode", "__version__"]
