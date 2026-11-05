<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/sebaJoSt/blaeckpy/main/extras/blaeckpy-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="https://raw.githubusercontent.com/sebaJoSt/blaeckpy/main/extras/blaeckpy-light.svg">
  <img src="https://raw.githubusercontent.com/sebaJoSt/blaeckpy/main/extras/blaeckpy-light.svg" alt="blaeckpy" height="75">
</picture>

---

A Python library for signals, Home Assistant entities and commands over serial or TCP,
using the [blaeck protocol](https://sebajost.github.io/blaeck-protocol/). Works with hosts such
as Loggbok.

With blaeckpy, a script can:

- **Log signals:** values such as a temperature or a counter.
- **Show up in Home Assistant:** with controls, sensors and events.
- **Take commands:** text such as `<SwitchLED,1>`.

blaeckpy replaces [blaecktcpy](https://github.com/sebaJoSt/blaecktcpy), which stays on PyPI for
protocol v1.

## Installation

```
pip install blaeckpy
```

Python 3.12 or later. blaeckpy itself needs nothing else; a serial port needs
[pyserial](https://pypi.org/project/pyserial/).

## A first script

Two simulated readings, logged at the interval the host chooses.

### TCP

```python
import random
import socket

from blaeckpy import Blaeck

device = Blaeck("Weather Station")
temperature = device.add_signal("Temperature", float, unit="°C")
pressure = device.add_signal("Pressure", int, unit="hPa")

server = socket.create_server(("", 2323))
device.begin(server)

while True:
    temperature.value = 20.0 + random.randint(0, 100) / 10
    pressure.value = 1000 + random.randint(0, 30)
    device.tick(timeout=0.1)
```

Connect the host to this computer on TCP port 2323.

### Serial

```python
import serial  # pip install pyserial

port = serial.Serial("/dev/ttyUSB0", 115200)   # COM3 on Windows
device.begin(port)
```

Everything else stays the same. Connect the host to the other end of the serial link at
115200 baud.

If the port goes away, a USB cable pulled say, `tick()` raises the port's error. A device begins
once, so to go on, start the script again.

Three calls do the work:

- `add_signal(...)` declares a value to log. Set its `.value`; blaeckpy reads it whenever it
  sends data. The type comes from a type (`float`) or from a starting value (`21.5`), and
  `.value` refuses what the type can't hold.
- `begin(...)` hands blaeckpy the serial port or listening socket you opened. It stays yours to
  close.
- `tick(timeout=0.1)` answers commands and sends what is due, then waits up to 0.1 s for the next
  command or the next write that falls due, so the loop doesn't use a whole CPU core. Call it in
  every pass of your loop.

The host decides how often data is sent: it sends `<BLAECK.DATA_START>` and
`<BLAECK.INTERVAL_START,1000>` for one frame a second.

## What a script declares

Signals, inputs, sensors, events, buttons and commands can also be declared on a sub-device.

| blaeckpy | What it is |
|---|---|
| `add_signal(name, float)` | A value to log; `.value` |
| `write_at_interval=ALWAYS / OFF / OnChange(0.5)`, `write_on_change=0.5`, `min_interval_ms=` | When a signal is sent |
| `add_number_input(name, 20.0, min=, max=, step=)`, `add_text_input`, `add_switch`, `add_select(name, 0, options=[...])` | Controls Home Assistant lets the user operate; `.value`, `@input.on_change` |
| `add_sensor(name, signal)`, `add_sensor(name, 21.5)`, `add_sensor(name, float, get=fn)`, `add_sensor(name, 0, options=[...])` | Values Home Assistant shows but the user can't set |
| `add_event(name, [types])`, `event.write(type)` | Occurrences, such as a doorbell |
| `add_button(name, fn)`, `@device.add_button(name)` | A press with no value |
| `@device.on_command(name)`, `@device.on_any_command` | Commands no host lists; parameters arrive as text |
| `add_device(name)`, `mark_missing()`, `mark_present()`, `write_restarted()` | A second board shown as its own device |
| `unit=`, `icon=`, `device_class=`, `category=Category.CONFIG`, ... | How a host shows it |
| `sensor.icon = ...`, `input.set_range(0, 20)`, `signal.write_on_change = 0.5`, ... | The same, changed while running. An entity's settings go out in a new entity list. A signal's reporting settings stay on the device. A signal's unit changes the schema hash, so a logging host stops until it reads the device list again |
| `@device.on_data_start`, `@device.on_interval_start`, ... | Following what the host switches on and off; `on_interval_start` gets the interval in ms, the others get nothing |
| `@device.on_connect`, `@device.on_disconnect` | TCP connections, with `(client_no, address)` |
| `device.terminal.print(...)` | Text for whoever looks into the running device |
| `set_timestamp_mode(TimestampMode.MICROS)` | Timestamps in data frames |
| `clear_all_signals()`, `clear_all_controls()`, ... | Declaring a new set while running |

A few things to know:

- **Errors at registration raise.** A bad name, a range with `min` above `max` or an option list
  with a blank entry raises `ValueError` or `TypeError` where it was written, instead of being
  reported on a debug stream. A button or command left without a function raises at `begin()`:
  until then it could still be a decorator.
- **A callback that raises doesn't stop the device.** The exception is logged with its traceback
  on the `blaeckpy` logger, `tick()` goes on, and the host still gets its answer. While a
  `clock=` fails, no data goes out; a signal that changed goes out once it works again.
- **One thread.** A device and what it made are used from the thread that calls `tick()`. To
  feed values from another thread, hand them over, with a `queue.Queue` say, and set them in the
  loop.
- **Time is in seconds** unless a name says otherwise: `tick(timeout=0.1)`, but
  `min_interval_ms=1000`. Timestamps are the exception: `tick(timestamp=)`, `write_all(timestamp)`
  and `clock=` are in microseconds, as they are on the wire.
- **The device list names blaeckpy** and its own version, so Loggbok and error reports show which
  library a device runs.
- **A command may be up to 1023 characters:** room for a 255-byte text, the most Home Assistant
  holds, even with every byte percent-encoded. The device list tells a host the limit.

## Status page and terminal output

`begin()` prints what the device is doing on the terminal, with colour where the terminal shows
it (`NO_COLOR` turns it off):

```
blaeckpy v1.0.0: 'Bench' listening on all addresses (0.0.0.0)
  TCP 127.0.0.1:2323
  TCP 192.168.1.20:2323
Status page:
  http://127.0.0.1:8080
  http://192.168.1.20:8080
Client 0 connected: 192.168.1.20:51234
```

These lines go through the `blaeckpy` logger. Once a script configures logging itself, with
`logging.basicConfig()` say, they go to its handlers instead. blaeckpy logs from level INFO
whatever `basicConfig()` was given, so the level is set on its logger:

```python
logging.getLogger("blaeckpy").setLevel(logging.WARNING)   # only warnings and errors
```

The status page shows the device in a browser: its connection, the clients connected, and each
device's signals and entities with their current values, refreshed every second. It needs no
internet. It runs on port 8080, or on a free port if 8080 is taken, and the terminal says
which. On TCP it listens on the same address as the device. On serial it stays on this computer,
unless `status_host` opens it to others, for a headless computer such as a Raspberry Pi. It has
no login, so anyone who can reach it can read it. The page only shows; it changes nothing on the
device. A sensor's `get=` is read for the page only while the page is open, and a password input
shows as `••••••••`.

```python
device.begin(server, status_port=8081)   # another port
device.begin(server, status_port=None)   # no status page
device.begin(port, status_host="0.0.0.0")   # a serial device's page, open to other computers
```

## Examples

The examples are in [`examples/`](https://github.com/sebaJoSt/blaeckpy/tree/main/examples). Each
one is a single file that listens on TCP port 2323; a comment shows the line to use for serial
instead. Run one and point Loggbok at it.

| Example | What it teaches |
|---|---|
| [basic.py](https://github.com/sebaJoSt/blaeckpy/blob/main/examples/basic.py) | The smallest script that logs two values |
| [signals.py](https://github.com/sebaJoSt/blaeckpy/blob/main/examples/signals.py) | Numeric, boolean and text signals, with units |
| [commands.py](https://github.com/sebaJoSt/blaeckpy/blob/main/examples/commands.py) | Plain commands, and a function that sees every command |
| [controls.py](https://github.com/sebaJoSt/blaeckpy/blob/main/examples/controls.py) | A number, text, switch, select and buttons |
| [sensors.py](https://github.com/sebaJoSt/blaeckpy/blob/main/examples/sensors.py) | A number, bool, function, index and text sensor |
| [events.py](https://github.com/sebaJoSt/blaeckpy/blob/main/examples/events.py) | Declaring and reporting occurrences |
| [write_modes.py](https://github.com/sebaJoSt/blaeckpy/blob/main/examples/write_modes.py) | Interval, on-change and explicit signal writes |
| [sub_devices.py](https://github.com/sebaJoSt/blaeckpy/blob/main/examples/sub_devices.py) | A second device shown below the board, going missing and restarting |
| [waveform_generator.py](https://github.com/sebaJoSt/blaeckpy/blob/main/examples/waveform_generator.py) | A waveform driven from a Home Assistant dashboard |

## Testing

Every frame is tested against recordings. blaeck records every frame it sends while playing a set
of scenarios ([golden scenarios](https://github.com/sebaJoSt/blaeck/tree/main/extras/tests/golden));
`scripts/copy_golden.py` copies them here, and blaeckpy must play each scenario through its public
API and send the same bytes. Real-socket tests cover TCP, and every example is run against a host.

```
pip install -e '.[test]'
pytest
```

## Licence

MIT, see [LICENSE](https://github.com/sebaJoSt/blaeckpy/blob/main/LICENSE). The status page uses [Pico CSS](https://picocss.com), MIT, see
[pico-LICENSE.md](https://github.com/sebaJoSt/blaeckpy/blob/main/src/blaeckpy/static/pico-LICENSE.md).
