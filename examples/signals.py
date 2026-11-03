"""Numbers, a bool and text, sampled and logged on the interval the host asks for.

A port of blaeck's Signals example. Set a signal's value and tick() sends it when a frame is
due; no write() call is needed. A signal is a name, a type and optionally a unit: it becomes a
column in the log. How a dashboard shows a value belongs to a sensor; see sensors.py.

    Temperature      a float, in degrees Celsius
    DoorOpen         a bool, logged as 0 or 1
    Mode             text
    Uptime           an int, in seconds
    Sine_1..Sine_5   five sine waves, a fifth of a turn apart
"""

import math
import time

from blaeckpy import Blaeck
from connect import open_transport

device = Blaeck("Signals", fw_version="1.0")
temperature = device.add_signal("Temperature", float, 21.5, unit="°C")
door_open = device.add_signal("DoorOpen", False)
mode = device.add_signal("Mode", "warming")
uptime = device.add_signal("Uptime", int, unit="s")
sines = [device.add_signal(f"Sine_{i + 1}", float) for i in range(5)]

device.begin(open_transport(__doc__))
start = last_update = time.monotonic()
while True:
    now = time.monotonic() - start
    uptime.value = int(now)
    for i, sine in enumerate(sines):
        sine.value = math.sin(now * 0.05 + i * 2 * math.pi / len(sines))
    if now - last_update >= 1.0:
        last_update = now
        door_open.value = (uptime.value // 10) % 2 != 0
        temperature.value += ((18.0 if door_open.value else 22.0) - temperature.value) * 0.25
        mode.value = "cooling" if door_open.value else "warming"
    device.tick(timeout=0.1)
