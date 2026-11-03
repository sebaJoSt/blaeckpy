"""blaeckpy: the Python counterpart of the blaeck Arduino library.

Speaks protocol v2 as blaeck does, so a host such as Loggbok cannot tell the two apart.
In development: signals, data, inputs, sensors, selects, events, buttons, commands and
sub-devices work, over a serial port or TCP.
"""

from importlib.metadata import version as _version

from ._clock import TimestampMode
from ._device import Blaeck, SubDevice
from ._events import Button, Event
from ._properties import Category, Mode, NumberInput, Select, Sensor, StateClass, Switch, TextInput
from ._signal import ALWAYS, ANY_CHANGE, OFF, OnChange, Signal

__version__ = _version("blaeckpy")

__all__ = ["ALWAYS", "ANY_CHANGE", "Blaeck", "Button", "Category", "Event", "Mode", "NumberInput",
           "OFF", "OnChange", "Select", "Sensor", "Signal", "StateClass", "SubDevice", "Switch",
           "TextInput", "TimestampMode", "__version__"]
