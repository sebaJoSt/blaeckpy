"""Events and their types; buttons."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from .protocol import frames


class Event:
    """A moment the device reports, of one of a closed list of types."""

    def __init__(self, device, device_id: int, name: str, types: Sequence[str], presentation):
        if isinstance(types, str) or not isinstance(types, Sequence) \
                or any(not isinstance(t, str) for t in types):
            raise TypeError("types is a list of str")
        types = list(types)
        frames.check_listed(types, "event type")
        if len(set(types)) != len(types):
            raise ValueError("an event's types are each named once")
        self._device = device
        self.device_id = device_id
        self.name = name
        self.types = types
        self._presentation = presentation

    def add_type(self, type_: str) -> None:
        """One more type, at the end of the list. Hosts learn of it from the entity list."""
        frames.check_listed([type_], "event type")
        if type_ in self.types:
            raise ValueError(f"event type {type_!r} exists already")
        self.types.append(type_)
        self._device._catalog_changed()

    def write(self, type_: str) -> None:
        """Reports the event now. While entities are stopped it is lost, as in blaeck."""
        if type_ not in self.types:
            raise ValueError(f"{self.name!r} has no event type {type_!r}")
        self._device._write_event(self, self.types.index(type_))

    def _entry(self) -> frames.Entry:
        return frames.event_entry(self.device_id, self.name, self.types,
                                  **self._presentation.entry_args())


class Button:
    """A moment a host triggers by sending the button's name. A press carries no value."""

    def __init__(self, device_id: int, name: str, press: Callable[[], None], presentation):
        if not callable(press):
            raise TypeError("a button needs a function to call when it is pressed")
        self.device_id = device_id
        self.name = name
        self.press = press
        self._presentation = presentation

    def _entry(self) -> frames.Entry:
        return frames.button_entry(self.device_id, self.name, **self._presentation.entry_args())
