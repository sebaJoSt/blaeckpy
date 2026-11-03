# blaeckpy

The Python counterpart of the [blaeck](https://github.com/sebaJoSt/blaeck) Arduino library. A
Python script sends the values it holds over a serial port or TCP, using the
[blaeck protocol](https://sebajost.github.io/blaeck-protocol/), and a host such as Loggbok
cannot tell it from a blaeck board.

With blaeckpy, a script can:

- **Log signals:** values such as a temperature or a counter.
- **Show up in Home Assistant:** with controls, sensors and events.
- **Take commands:** text such as `<SwitchLED,1>`.

**Status: in development, not on PyPI yet.** Everything above works, over serial and TCP, and is
checked byte for byte against blaeck (see [Testing](#testing)). It replaces
[blaecktcpy](https://github.com/sebaJoSt/blaecktcpy), which stays on PyPI for protocol v1.

Python 3.12 or later. blaeckpy itself needs nothing else; a serial port needs
[pyserial](https://pypi.org/project/pyserial/).

```
pip install git+https://github.com/sebaJoSt/blaeckpy
```

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

If the port goes away, a USB cable pulled say, `tick()` raises the port's error. Close the
device, reopen the port and begin again; the device is silent until the host asks again:

```python
try:
    device.tick(timeout=0.1)
except serial.SerialException:
    device.close()
    port.close()
    port = serial.Serial("/dev/ttyUSB0", 115200)   # once it is back
    device.begin(port)
```

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

The names are blaeck's, in Python style. Everything a script declares on the board it can also
declare on a sub-device.

| blaeck | blaeckpy | What it is |
|---|---|---|
| `addSignal(name, &var)` | `add_signal(name, float)` | A value to log; `.value` |
| `writeAtInterval`, `writeOnChange` | `write_at_interval=ALWAYS / OFF / OnChange(0.5)`, `write_on_change=0.5`, `min_interval_ms=` | When a signal is sent |
| `addNumberInput`, `addTextInput`, `addSwitch`, `addSelect` | `add_number_input(name, 20.0, min=, max=, step=)`, `add_text_input`, `add_switch`, `add_select(name, 0, options=[...])` | Controls Home Assistant lets the user operate; `.value`, `@input.on_change` |
| `addSensor` | `add_sensor(name, signal)`, `add_sensor(name, 21.5)`, `add_sensor(name, float, get=fn)`, `add_sensor(name, 0, options=[...])` | Values Home Assistant shows but the user can't set |
| `addEvent`, `writeEvent` | `add_event(name, [types])`, `event.write(type)` | Occurrences, such as a doorbell |
| `addButton` | `add_button(name, fn)` | A press with no value |
| `onCommand`, `onAnyCommand` | `@device.on_command(name)`, `@device.on_any_command` | Commands no host lists; parameters arrive as text |
| `addDevice`, `markMissing`, `markPresent`, `writeRestarted` | `add_device(name)`, `mark_missing()`, `mark_present()`, `write_restarted()` | A second board shown as its own device |
| `withUnit`, `withIcon`, `withDeviceClass`, `config()`, ... | `unit=`, `icon=`, `device_class=`, `category=Category.CONFIG`, ... | How a host shows it |
| `onDataStart`, `onIntervalStart`, `onEntitiesStart`, ... | `@device.on_data_start`, `@device.on_interval_start`, ... | Following what the host switches on and off |
| `onConnect`, `onDisconnect` | `@device.on_connect`, `@device.on_disconnect` | TCP connections, with `(client_no, address)` |
| `Terminal` | `device.terminal.print(...)` | Text for whoever looks into the running device |
| `setTimestampMode` | `set_timestamp_mode(TimestampMode.MICROS)` | Timestamps in data frames |
| `clearAllSignals`, `clearAllControls`, `clearAllSensors`, `clearAllEvents`, `clearAllCommands` | `clear_all_signals()`, `clear_all_controls()`, ... | Declaring a new set while running |

A few things are Python's own:

- **Errors at registration raise.** A bad name, a range with `min` above `max` or an option list
  with a blank entry raises `ValueError` or `TypeError` where it was written, instead of being
  reported on a debug stream.
- **A callback that raises doesn't stop the device.** The exception is logged with its traceback
  on the `blaeckpy` logger, `tick()` goes on, and the host gets the answer blaeck would send.
- **Time is in seconds** unless a name says otherwise: `tick(timeout=0.1)`, but blaeck's
  millisecond values keep their unit and say so, as in `min_interval_ms=1000`.
- **The device list names blaeckpy** and its own version, so Loggbok and error reports show which
  library a device runs.
- **A command may be up to 1023 characters,** where blaeck's default buffer takes 127: room for a
  255-byte text, the most Home Assistant holds, even with every byte percent-encoded. The device
  list tells a host the limit.

## Examples

The examples are in [`examples/`](examples). Run one with `--tcp PORT` (2323 by default) or
`--serial PORT`, and point Loggbok at it. They are ports of blaeck's examples of the same names.

| Example | What it teaches |
|---|---|
| [basic.py](examples/basic.py) | The smallest script that logs two values |
| [signals.py](examples/signals.py) | Numeric, boolean and text signals, with units |
| [commands.py](examples/commands.py) | Plain commands, and a function that sees every command |
| [controls.py](examples/controls.py) | A number, text, switch, select and buttons |
| [sensors.py](examples/sensors.py) | A number, bool, function, index and text sensor |
| [events.py](examples/events.py) | Declaring and reporting occurrences |
| [write_modes.py](examples/write_modes.py) | Interval, on-change and explicit signal writes |
| [sub_devices.py](examples/sub_devices.py) | A second device shown below the board, going missing and restarting |

## Testing

Parity with blaeck is tested, not assumed. blaeck records every frame it sends while playing a set
of scenarios ([golden scenarios](https://github.com/sebaJoSt/blaeck/tree/main/extras/tests/golden));
`scripts/copy_golden.py` copies them here, and blaeckpy must play each scenario through its public
API and send the same bytes. Real-socket tests cover TCP, and every example is run against a host.

```
pip install -e '.[test]'
pytest
```

## Licence

MIT, see [LICENSE](LICENSE).
