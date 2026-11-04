"""TCP with real sockets on localhost: hosts and terminals, the newest host wins, slots, the
connection callbacks, notices held back until a host can hear them, and tick(timeout=)."""

import socket
import time

import pytest

from blaeckpy import Blaeck
from blaeckpy.protocol import envelope


class Client:
    def __init__(self, server: socket.socket):
        self.sock = socket.create_connection(server.getsockname()[:2], timeout=1.0)
        self.buffer = bytearray()
        self.closed = False

    def send(self, text: str) -> None:
        self.sock.sendall(text.encode())

    def take(self) -> None:
        self.sock.settimeout(0.01)
        try:
            while True:
                data = self.sock.recv(4096)
                if not data:
                    self.closed = True
                    return
                self.buffer += data
        except (TimeoutError, BlockingIOError):
            pass
        except OSError:
            self.closed = True

    def frames(self):
        """The frames received so far, opened, and anything else as text."""
        data, self.buffer = bytes(self.buffer), bytearray()
        out = []
        while data:
            start = data.find(envelope.START)
            if start != 0:
                end = len(data) if start < 0 else start
                out.append(data[:end].decode())
                data = data[end:]
                continue
            end = data.index(envelope.END) + len(envelope.END)
            out.append(envelope.open_frame(data[:end]))
            data = data[end:]
        return out

    def keys(self):
        return [f.key for f in self.frames() if not isinstance(f, str)]

    def close(self):
        self.sock.close()


def pump(device, clients, seconds=0.15):
    """Ticks the device and lets the clients take what it sends."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        device.tick()
        for c in clients:
            c.take()


@pytest.fixture
def server():
    s = socket.create_server(("127.0.0.1", 0))
    yield s
    s.close()


def test_a_connection_is_a_terminal_until_it_sends_a_builtin(server):
    device, ran = Blaeck("Bench"), []
    device.on_command("MOTOR", lambda *p: ran.append(p))
    device.begin(server)
    a = Client(server)
    pump(device, [a])
    a.send("<MOTOR,1>")
    pump(device, [a])
    device.terminal.print("hello", 42)
    pump(device, [a])
    assert ran == [("1",)]
    # A terminal's command runs, unanswered, and the terminal gets the terminal text.
    assert a.frames() == ["hello 42\n"]


def test_the_first_builtin_makes_its_sender_the_host(server):
    device = Blaeck("Bench")
    device.begin(server)
    a = Client(server)
    pump(device, [a])
    a.send("<BLAECK.GET_DEVICES>")
    pump(device, [a])
    # The device list counts as telling the host of the restart: no notice follows it.
    assert a.keys() == [0xA5, 0xB7]
    device.terminal.print("not for the host")
    pump(device, [a])
    assert a.frames() == []


def test_the_newest_host_wins_and_the_previous_one_is_closed(server):
    device, gone = Blaeck("Bench"), []
    device.on_disconnect(lambda slot, address: gone.append(slot))
    device.begin(server)
    a, b = Client(server), Client(server)
    pump(device, [a, b])
    a.send("<BLAECK.GET_DEVICES>")
    pump(device, [a, b])
    b.send("<BLAECK.GET_DEVICES>")
    pump(device, [a, b])
    assert a.closed and gone == [0]
    b.frames()
    b.send("<#3:BLAECK.WRITE_ENTITIES>")
    pump(device, [b])
    acks = [f for f in b.frames() if not isinstance(f, str) and f.key == 0xA5]
    assert acks and acks[0].message_id == 3


def test_a_connection_beyond_the_slots_is_closed(server):
    device, connected = Blaeck("Bench"), []
    device.on_connect(lambda slot, address: connected.append((slot, address)))
    device.begin(server, max_clients=1)
    a = Client(server)
    pump(device, [a])
    b = Client(server)
    pump(device, [a, b])
    assert b.closed and not a.closed
    assert connected == [(0, a.sock.getsockname())]


def test_notices_wait_for_a_host_and_the_device_list_counts_as_telling(server):
    device = Blaeck("Bench")
    pump_device = device.add_device("Pump")
    device.begin(server)
    pump_device.mark_missing()
    pump_device.write_restarted()
    a = Client(server)
    pump(device, [a])
    a.send("<BLAECK.GET_DEVICES>")
    pump(device, [a])
    frames = a.frames()
    assert [f.key for f in frames] == [0xA5, 0xB7]
    # The device list says the pump is missing and restarted; no notice repeats it.
    assert b"Pump\x00n/a\x00n/a\x00" in frames[1].fields
    assert frames[1].fields[frames[1].fields.index(b"Pump") - 1] == 0x03


def test_begin_takes_max_clients_only_for_a_server():
    class Port:
        in_waiting = 0

        def read(self, n):
            return b""

        def write(self, data):
            pass

    with pytest.raises(TypeError):
        Blaeck().begin(Port(), max_clients=2)


def test_tick_with_a_timeout_waits_answers_at_once_and_sends_on_time(server):
    device = Blaeck("Bench")
    device.add_signal("T", 1.0)
    device.begin(server)
    a = Client(server)
    pump(device, [a])
    a.send("<BLAECK.DATA_START>")
    pump(device, [a])
    a.send("<BLAECK.INTERVAL_START,100>")
    a.frames()
    start = time.monotonic()
    device.tick(timeout=0.35)
    took = time.monotonic() - start
    a.take()
    keys = a.keys()
    assert 0.3 <= took < 0.6
    # The ack at once, then a data frame at 0, 100, 200 and 300 ms.
    assert keys[0] == 0xA5 and keys.count(0xD3) >= 3


def test_a_timestamp_and_a_timeout_dont_go_together():
    with pytest.raises(ValueError):
        Blaeck().tick(timestamp=5, timeout=0.1)


def test_a_bare_number_is_the_timeout():
    start = time.monotonic()
    Blaeck().tick(0.05)
    assert time.monotonic() - start >= 0.04
    with pytest.raises(TypeError):
        Blaeck().tick(0, 5)


def test_the_host_gets_no_answer_to_a_terminals_command(server):
    device, ran = Blaeck("Bench"), []
    device.on_command("MOTOR", lambda *p: ran.append(p))
    device.begin(server)
    host, terminal = Client(server), Client(server)
    pump(device, [host, terminal])
    host.send("<BLAECK.GET_DEVICES>")
    pump(device, [host, terminal])
    host.frames()
    terminal.send("<MOTOR,2>")
    pump(device, [host, terminal])
    assert ran == [("2",)]
    assert host.frames() == [] and terminal.frames() == []


def test_close_closes_the_connections_without_on_disconnect(server):
    device, events = Blaeck(), []
    device.on_connect(lambda client_no, address: events.append("connect"))
    device.on_disconnect(lambda client_no, address: events.append("disconnect"))
    device.begin(server)
    client = socket.create_connection(server.getsockname(), timeout=2)
    while not events:
        device.tick(0.01)
    device.close()
    assert events == ["connect"]
    assert client.recv(1) == b""  # The device's end closed.
    client.close()


def test_commands_sent_before_a_half_close_are_all_answered(server):
    device, ran = Blaeck("Bench"), []
    device.on_command("MOTOR", lambda *p: ran.append(p))
    device.begin(server)
    a = Client(server)
    pump(device, [a])
    a.send("<MOTOR,1><MOTOR,2><MOTOR,3>")
    a.sock.shutdown(socket.SHUT_WR)
    pump(device, [a])
    assert ran == [("1",), ("2",), ("3",)]
    assert a.closed
    device.close()


def test_closing_from_on_disconnect_while_a_host_is_replaced_ends_tick_quietly(server):
    device = Blaeck("Bench")
    device.on_disconnect(lambda slot, address: device.close())
    device.begin(server)
    a, b = Client(server), Client(server)
    pump(device, [a, b])
    a.send("<BLAECK.GET_DEVICES>")
    pump(device, [a, b])
    b.send("<BLAECK.GET_DEVICES>")
    pump(device, [a, b])
    assert a.closed and b.closed

def test_closing_from_on_disconnect_when_a_notice_fails_ends_tick_quietly(server):
    import struct
    device = Blaeck("Bench")
    device.on_disconnect(lambda slot, address: device.close())
    device.begin(server)
    a = Client(server)
    pump(device, [a])
    a.send("<BLAECK.WRITE_ENTITIES>")
    # Until the command, there is no host for the restart notice; it goes out at the next tick.
    for _ in range(50):
        device.tick()
        if device._transport.has_host():
            break
        time.sleep(0.01)
    a.sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    a.close()
    time.sleep(0.1)
    device.tick()
    device.tick()