"""The serial port or TCP server the script hands to begin(): non-blocking reads, waiting for
tick(timeout=), newest host wins.

Both transports answer the same questions for the device: is there a host to send frames to,
what command came in next and from whom, and where does terminal text go.
"""

from __future__ import annotations

import logging
import select
import socket
import sys
import time

from ._commands import Receiver

log = logging.getLogger("blaeckpy")
# How long a write to a host or terminal may take before it counts as failed.
_SEND_TIMEOUT = 2.0


class StreamTransport:
    """A port the script opened, such as a pyserial ``Serial``: anything with ``read``, ``write``
    and ``in_waiting``. Only what is waiting is read, so a read never blocks, whatever timeout
    the script opened the port with. The port stays the script's to close. Whoever is on the
    other end is the host."""

    def __init__(self, port) -> None:
        self.port = port
        self._receiver = Receiver()

    def has_host(self) -> bool:
        return True

    def requester_is_host(self) -> bool:
        return True

    def receive(self):
        """The next complete command as ``(raw, overflowed)``, or None."""
        waiting = self.port.in_waiting
        if waiting:
            self._receiver.feed(bytes(self.port.read(waiting)))
        return self._receiver.next_command()

    def builtin_received(self) -> None:
        pass

    def send(self, data: bytes) -> bool:
        return self._write(data)

    def terminal(self, data: bytes) -> None:
        self._write(data)

    def _write(self, data: bytes) -> bool:
        """Writes all of ``data``. A port may take only part of it; the rest follows, for up to
        _SEND_TIMEOUT while the port takes nothing. False if the port never took it all."""
        stalled_since = None
        while data:
            n = self.port.write(data)
            if n is None:  # a port that doesn't count took it all
                return True
            if n > 0:
                data = data[n:]
                stalled_since = None
                continue
            now = time.monotonic()
            if stalled_since is None:
                stalled_since = now
            elif now - stalled_since >= _SEND_TIMEOUT:
                log.warning("The port took no bytes for %g s; %d bytes of a frame were dropped",
                            _SEND_TIMEOUT, len(data))
                return False
            time.sleep(0.001)
        return True

    def selectables(self) -> list:
        """What tick(timeout=) can wait on: the port, where the platform lets a port be waited
        on. Windows can't, so there tick() polls it in short steps."""
        if sys.platform == "win32":
            return []
        try:
            self.port.fileno()
        except (AttributeError, OSError, ValueError):
            return []
        return [self.port]

    def close(self) -> None:
        pass


class _Connection:
    def __init__(self, sock: socket.socket, address) -> None:
        self.sock = sock
        self.address = address
        self.receiver = Receiver()
        # The peer closed its side: what it sent is still answered, then the connection drops.
        self.eof = False


class TcpTransport:
    """A listening socket the script opened. Up to ``max_clients`` connections at once: the
    newest to send a BLAECK. command is the host and gets the frames; the others are terminals,
    whose commands run unanswered and which get the terminal text. The socket stays the script's
    to close; the connections are blaeckpy's."""

    def __init__(self, server: socket.socket, max_clients: int, on_connect, on_disconnect) -> None:
        if isinstance(max_clients, bool) or not isinstance(max_clients, int) or not 1 <= max_clients <= 255:
            raise ValueError("max_clients is 1 to 255")
        server.setblocking(False)
        self.server = server
        self.slots: list[_Connection | None] = [None] * max_clients
        self.host: int | None = None
        self.requester = 0
        self._on_connect = on_connect
        self._on_disconnect = on_disconnect

    def has_host(self) -> bool:
        return self.host is not None and self.slots[self.host] is not None

    def requester_is_host(self) -> bool:
        return self.requester == self.host

    def _accept(self) -> None:
        try:
            sock, address = self.server.accept()
        except (BlockingIOError, InterruptedError):
            return
        free = next((i for i, c in enumerate(self.slots) if c is None), None)
        if free is None:
            # Every slot is taken.
            sock.close()
            return
        sock.settimeout(_SEND_TIMEOUT)
        self.slots[free] = _Connection(sock, address)
        self._on_connect(free, address)

    def _release(self, slot: int) -> None:
        connection = self.slots[slot]
        self.slots[slot] = None
        if self.host == slot:
            self.host = None
        if connection is None:
            return
        try:
            connection.sock.close()
        except OSError:
            pass

    def _drop(self, slot: int, reason: str | None = None) -> None:
        connection = self.slots[slot]
        address = connection.address if connection is not None else None
        self._release(slot)
        self._on_disconnect(slot, address, reason)

    def _pull(self) -> None:
        """Takes in what each connection has, and notices which ones closed. A closed one is
        dropped once the commands it sent before closing are answered."""
        for slot, connection in enumerate(self.slots):
            if connection is not None and connection.eof and not connection.receiver.has_command():
                self._drop(slot)
        open_ = [c.sock for c in self.slots if c is not None and not c.eof]
        if not open_:
            return
        readable, _, _ = select.select(open_, [], [], 0)
        for slot, connection in enumerate(self.slots):
            if connection is None or connection.sock not in readable:
                continue
            try:
                data = connection.sock.recv(4096)
            except (BlockingIOError, InterruptedError, TimeoutError):
                continue
            except OSError:
                self._drop(slot)
                continue
            if data:
                connection.receiver.feed(data)
            elif connection.receiver.has_command():
                connection.eof = True
            else:
                self._drop(slot)

    def receive(self):
        self._accept()
        self._pull()
        count = len(self.slots)
        # Start after the connection served last, so one busy connection can't starve the rest.
        for k in range(1, count + 1):
            slot = (self.requester + k) % count
            connection = self.slots[slot]
            if connection is None:
                continue
            command = connection.receiver.next_command()
            if command is not None:
                self.requester = slot
                return command
        return None

    def builtin_received(self) -> None:
        """The requester sent a BLAECK. command: it is the host now. One host at a time, as on a
        serial port; the previous one is closed, so it learns it was replaced."""
        if self.host == self.requester:
            return
        previous = self.host
        self.host = self.requester
        if previous is not None and self.slots[previous] is not None:
            self._drop(previous, "replaced as host")

    def _write(self, slot: int, data: bytes) -> bool:
        connection = self.slots[slot]
        if connection is None:
            return False
        try:
            connection.sock.sendall(data)
            return True
        except OSError:
            self._drop(slot)
            return False

    def send(self, data: bytes) -> bool:
        if self.host is not None and self.slots[self.host] is not None:
            return self._write(self.host, data)
        return False

    def terminal(self, data: bytes) -> None:
        for slot, connection in enumerate(self.slots):
            if connection is not None and slot != self.host:
                self._write(slot, data)

    def selectables(self) -> list:
        return [self.server] + [c.sock for c in self.slots if c is not None and not c.eof]

    def close(self) -> None:
        for slot, connection in enumerate(self.slots):
            if connection is not None:
                self._release(slot)


def transport_for(obj, max_clients: int, on_connect, on_disconnect):
    if isinstance(obj, socket.socket):
        if obj.type != socket.SOCK_STREAM:
            raise TypeError("begin() takes a TCP socket")
        try:
            listening = obj.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN)
        except OSError:
            listening = True  # not every platform tells
        if not listening:
            raise TypeError("begin() takes a listening socket: socket.create_server(...)")
        return TcpTransport(obj, max_clients, on_connect, on_disconnect)
    if all(hasattr(obj, a) for a in ("read", "write", "in_waiting")):
        return StreamTransport(obj)
    raise TypeError(
        "begin() takes an opened serial port (read, write, in_waiting) or a listening socket; "
        f"got {type(obj).__name__}")
