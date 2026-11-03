"""Two numbers, registered as signals and sent at the interval a host requests.

A port of blaeck's Basic example. The host selects the logging interval.
"""

import random

from blaeckpy import Blaeck
from connect import open_transport

device = Blaeck("Basic", fw_version="1.0")
small = device.add_signal("Small Number", float)
big = device.add_signal("Big Number", int)

device.begin(open_transport(__doc__))
while True:
    small.value = random.randint(0, 1000) / 100
    big.value = random.randint(2_000_000_000, 2_100_000_000)
    device.tick(timeout=0.1)
