"""A second device shown below the board: a pump controller with its own signals, a speed input
and an alarm, as blaeck's SubDevices/MainBoard shows a second board polled over a UART.

A port of that example, with the pump simulated in this script instead of polled. Watering
raises the humidity and cools the air a little as it evaporates. Every 60 s the pump goes
missing for 10 s, and when it comes back it reports a restart, so a host can be seen dealing
with both.
"""

import math
import time

from blaeckpy import Blaeck
from connect import open_transport

device = Blaeck("Greenhouse", fw_version="1.0")
temperature = device.add_signal("Temperature", float, 21.0, unit="°C")
humidity = device.add_signal("Humidity", float, 50.0, unit="%")
# Sensors too, so Home Assistant shows the board as a device of its own.
device.add_sensor("Temperature", temperature, device_class="temperature", unit="°C",
                  write_on_change=0.1, min_interval_ms=1000)
device.add_sensor("Humidity", humidity, device_class="humidity", unit="%",
                  write_on_change=0.5, min_interval_ms=1000)

# The pump's entries; a host shows them under "Pump controller".
pump = device.add_device("Pump controller", hw_version="Simulated", fw_version="1.0")
flow = pump.add_signal("Flow", float, unit="L/min")
pressure = pump.add_signal("Pressure", float, unit="bar")
pump.add_sensor("Flow", flow, unit="L/min")
pump.add_sensor("Pressure", pressure, unit="bar")
speed = pump.add_number_input("PumpSpeed", int, min=0, max=100, step=1, unit="%")
alarms = pump.add_event("Alarms", ["restarted"])


@speed.on_change
def run_pump():
    # The range is checked; running the pump is up to the script.
    flow.value = 12.0 * speed.value / 100
    pressure.value = 2.5 * speed.value / 100


device.begin(open_transport(__doc__))
start = last_update = time.monotonic()
cooling = 0.0
while True:
    now = time.monotonic()
    seconds = now - start
    if now - last_update >= 1.0:
        last_update = now
        watering = flow.value / 12.0
        cooling += (watering - cooling) * 0.05
        humidity.value += (50.0 + 35.0 * watering - humidity.value) * 0.05
        temperature.value = 21.0 + math.sin(seconds / 60.0) - cooling
    gone = seconds % 60 >= 50
    if gone and not pump.missing:
        pump.mark_missing()
    elif not gone and pump.missing:
        pump.mark_present()
        pump.write_restarted()
        alarms.write("restarted")
    device.tick(timeout=0.1)
