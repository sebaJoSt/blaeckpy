"""Sensors are what Home Assistant shows but the user can't set. What add_sensor() gets decides
the kind:

    Temperature    a float          the simulated room temperature
    DoorOpen       a bool           a binary sensor, open for 20 s every 90 s
    Cold           a function       worked out on every check
    Phase          an index         Idle, Heating or Cooling, shown by name
    LastError      a text           set when a fault is simulated, cleared 10 s later

A port of blaeck's Sensors example. tick() checks every sensor and sends the ones that changed.
Temperature is sent when it moves by 0.1 or more, at most once a second.
"""

import time

from blaeckpy import Blaeck, Category
from connect import open_transport

device = Blaeck("Sensors", fw_version="1.0")
temperature = device.add_sensor("Temperature", 18.0, unit="°C", device_class="temperature",
                                display_precision=1, write_on_change=0.1, min_interval_ms=1000)
door_open = device.add_sensor("DoorOpen", False, display_name="Door", device_class="door")
device.add_sensor("Cold", bool, get=lambda: temperature.value < 19.0, device_class="cold")
phase = device.add_sensor("Phase", 0, options=["Idle", "Heating", "Cooling"])
last_error = device.add_sensor("LastError", "", icon="mdi:alert-circle",
                               category=Category.DIAGNOSTIC)

device.begin(open_transport(__doc__))
start = last_update = time.monotonic()
while True:
    now = time.monotonic()
    if now - last_update >= 0.5:
        last_update = now
        seconds = int(now - start)
        door_open.value = seconds % 90 < 20
        if temperature.value < 20.0:
            phase.value = 1
        elif phase.value == 1 and temperature.value >= 22.0:
            phase.value = 0
        elif phase.value != 1:
            phase.value = 2 if door_open.value else 0
        target = 26.0 if phase.value == 1 else (10.0 if door_open.value else 16.0)
        temperature.value += (target - temperature.value) * 0.02
        if seconds % 120 == 60 and not last_error.value:
            last_error.value = f"sensor timeout at {seconds} s"
        elif seconds % 120 == 70:
            last_error.value = ""
    device.tick(timeout=0.1)
