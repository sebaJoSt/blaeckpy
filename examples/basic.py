"""Two numbers, registered as signals and sent at the interval a host requests.

The host selects the logging interval.
"""

import random
import socket

from blaeckpy import Blaeck

device = Blaeck("Basic", fw_version="1.0")
small = device.add_signal("Small Number", float)
big = device.add_signal("Big Number", int)

# Loggbok connects to this computer on TCP port 2323. For serial instead, use
# device.begin(serial.Serial("COM3", 115200, timeout=0)) after import serial (pip install pyserial).
device.begin(socket.create_server(("", 2323)))
print("##LOGGBOK:READY##")  # Sentinel for Loggbok's process launcher — safe to remove
while True:
    small.value = random.randint(0, 1000) / 100
    big.value = random.randint(2_000_000_000, 2_100_000_000)
    device.tick(timeout=0.1)
