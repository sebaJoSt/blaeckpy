"""A Python library for signals, Home Assistant entities and commands over serial or TCP, using
the blaeck protocol."""

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
