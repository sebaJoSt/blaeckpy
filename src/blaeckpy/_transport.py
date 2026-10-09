"""The serial port or TCP server the script hands to begin(): non-blocking reads, waiting for
tick(timeout=), newest host wins."""

from __future__ import annotations


class StreamTransport:
    """A port the script opened, such as a pyserial ``Serial``: anything with ``read``, ``write``
    and ``in_waiting``. Only what is waiting is read, so a read never blocks, whatever timeout
    the script opened the port with. The port stays the script's to close."""

    def __init__(self, port) -> None:
        self.port = port

    def read_available(self) -> bytes:
        waiting = self.port.in_waiting
        return bytes(self.port.read(waiting)) if waiting else b""

    def write(self, data: bytes) -> None:
        self.port.write(data)


def transport_for(obj) -> StreamTransport:
    if all(hasattr(obj, a) for a in ("read", "write", "in_waiting")):
        return StreamTransport(obj)
    raise TypeError(
        "begin() takes an opened serial port (read, write, in_waiting) or a listening socket; "
        f"got {type(obj).__name__}")
