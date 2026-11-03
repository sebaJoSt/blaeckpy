"""Controls are what Home Assistant lets the user operate. A heater with one of each:

    <Setpoint,22.5>      number   5 to 30 °C in steps of 0.5
    <Room,Kitchen>       text     up to 23 characters
    <HeaterEnabled,1>    switch   0 or 1
    <Mode,Heat>          select   Off, Heat or Auto; <Mode,1> picks it by position
    <Reset>              button   back to 21 °C and Auto
    <Boost>              button   2 °C more, up to 30 °C

A port of blaeck's Controls example. Each value is checked, stored, acknowledged and sent back;
the functions given to on_change run after it is stored. A simulated room temperature follows
the setpoint while the heater is on.
"""

import socket

from blaeckpy import Blaeck, Category

device = Blaeck("Controls", fw_version="1.0")
setpoint = device.add_number_input("Setpoint", 21.0, min=5.0, max=30.0, step=0.5, unit="°C",
                                   device_class="temperature")
room = device.add_text_input("Room", "Living room", max_length=23, icon="mdi:sofa",
                             category=Category.CONFIG)
heater_enabled = device.add_switch("HeaterEnabled", True, display_name="Heater enabled")
mode = device.add_select("Mode", 2, options=["Off", "Heat", "Auto"], icon="mdi:thermostat")
temperature = device.add_sensor("Temperature", 18.0, unit="°C", device_class="temperature",
                                display_precision=1, write_on_change=0.1, min_interval_ms=1000)


@setpoint.on_change
def setpoint_changed():
    device.terminal.print(f"Setpoint: {setpoint.value}")


@room.on_change
def room_changed():
    device.terminal.print(f"Room: {room.value}")


@mode.on_change
def mode_changed():
    device.terminal.print(f"Mode: {mode.option}")


def reset():
    setpoint.value = 21.0
    mode.value = 2


device.add_button("Reset", reset, icon="mdi:restore")
device.add_button("Boost", lambda: setattr(setpoint, "value", min(setpoint.value + 2.0, 30.0)),
                  icon="mdi:fire")

# Loggbok connects to this computer on TCP port 2323. For serial instead, use
# device.begin(serial.Serial("COM3", 115200, timeout=0)) after import serial (pip install pyserial).
device.begin(socket.create_server(("", 2323)))
while True:
    heating = heater_enabled.value and mode.option != "Off"
    target = setpoint.value if heating else 15.0
    temperature.value += (target - temperature.value) * 0.01
    device.tick(timeout=0.1)
