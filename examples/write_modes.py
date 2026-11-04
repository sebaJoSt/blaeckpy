"""Four signals get the same value every 100 ms; only how they report it differs:

    Interval            every host interval, changed or not (the default)
    OnChangeAtInterval  at the host interval, if it changed by at least 0.01
    OnChange            as soon as it changes by at least 0.01; needs no interval
    Explicit            .write() sends every value

The value is a 12-second sine wave around 1.8 V, in a 36-second pattern:

    0-8 s    amplitude 0.8, with two short spikes at 5 and 6 s, peaking near 5 V
    8-12 s   fades out
    12-30 s  flat, flickering by one 10-bit ADC step (5 V / 1024, about 0.005)
    30-36 s  fades back in

Try this: set the host's logging interval to 2000 ms. Interval and OnChangeAtInterval only
check every 2 s, so they can miss the spikes; OnChange and Explicit catch them. While flat, the
flicker stays below 0.01: OnChangeAtInterval and OnChange go quiet, while Interval and Explicit
keep sending. Stop the interval: only Interval and OnChangeAtInterval stop.
"""

import math
import random
import socket
import time

from blaeckpy import OFF, Blaeck, OnChange

device = Blaeck("WriteModes", fw_version="1.0")
interval = device.add_signal("Interval", float)
on_change_at_interval = device.add_signal("OnChangeAtInterval", float,
                                          write_at_interval=OnChange(0.01))
on_change = device.add_signal("OnChange", float, write_at_interval=OFF, write_on_change=0.01,
                              min_interval_ms=0)
explicit = device.add_signal("Explicit", float, write_at_interval=OFF)


def value_at(phase_ms: int) -> float:
    amplitude = 0.8
    if phase_ms >= 30000:
        amplitude = 0.4 * (1 - math.cos(math.pi * (phase_ms - 30000) / 6000))
    elif phase_ms >= 12000:
        amplitude = 0.0
    elif phase_ms >= 8000:
        amplitude = 0.4 * (1 + math.cos(math.pi * (phase_ms - 8000) / 4000))
    jitter = random.randint(0, 1) * 5 / 1024 if amplitude == 0.0 else 0.0
    value = 1.8 + amplitude * math.sin((phase_ms % 12000) * 2 * math.pi / 12000) + jitter
    if 5000 <= phase_ms < 5600 or 6000 <= phase_ms < 6600:
        value += 1.45 * (1 - math.cos(2 * math.pi * (phase_ms % 1000) / 600))
    return value


# Loggbok connects to this computer on TCP port 2323. For serial instead, use
# device.begin(serial.Serial("COM3", 115200, timeout=0)) after import serial (pip install pyserial).
device.begin(socket.create_server(("", 2323)))
print("##LOGGBOK:READY##")  # Sentinel for Loggbok's process launcher — safe to remove
start = time.monotonic()
while True:
    value = value_at(int((time.monotonic() - start) * 1000) % 36000)
    interval.value = on_change_at_interval.value = on_change.value = value
    explicit.write(value)
    device.tick(timeout=0.1)
