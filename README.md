# blaeckpy

The Python counterpart of the [blaeck](https://github.com/sebaJoSt/blaeck) Arduino library. A
Python script logs signals, shows up in Home Assistant with controls, sensors and events, and
takes commands, over serial or TCP, using the
[blaeck protocol](https://sebajost.github.io/blaeck-protocol/). A host such as Loggbok cannot
tell it from a blaeck board.

**Status: in development.** Signals, data and the built-in commands work over a serial port; properties, events, buttons, sub-devices and TCP follow. It replaces
[blaecktcpy](https://github.com/sebaJoSt/blaecktcpy), which stays on PyPI for protocol v1.

## What it will look like

```python
import socket
from blaeckpy import Blaeck

device = Blaeck("Greenhouse", fw_version="1.0")
temperature = device.add_signal("Temperature", float)
device.add_sensor("Temperature", temperature, device_class="temperature", unit="°C")

server = socket.create_server(("", 23))   # or serial.Serial("/dev/ttyUSB0", 115200)
device.begin(server)
while True:
    temperature.value = read_sensor()
    device.tick(timeout=0.1)
```

## Licence

MIT, see [LICENSE](LICENSE).
