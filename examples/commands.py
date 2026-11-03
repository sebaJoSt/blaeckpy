"""Commands: a name with parameters, sent to the script. A function registered with
on_command() handles it and decides what the parameters mean. Commands are not entities, so
Home Assistant shows no control for them; see controls.py for those.

A port of blaeck's Commands example. Type these into a TCP terminal (telnet), or send them
from Loggbok:

    <SwitchLED,1>      the "LED" goes on; 0, ON and OFF work too
    <SwitchLED,>       an empty parameter: the LED goes off
    <Print,Hello,3>    prints Hello three times; the count is 1 to 10

on_any_command() sees every command; here it logs each one by name, except the BLAECK.*
commands Loggbok sends.
"""

import socket

from blaeckpy import Blaeck

device = Blaeck("Commands", fw_version="1.0")
led_on = False


@device.on_command("SwitchLED")
def switch_led(*params: str):
    global led_on
    if len(params) != 1:
        device.terminal.print("SwitchLED takes one value: 0, 1, ON or OFF.")
    elif params[0] in ("1", "ON"):
        led_on = True
        device.terminal.print("LED is ON.")
    elif params[0] in ("", "0", "OFF"):
        led_on = False
        device.terminal.print("LED is OFF.")
    else:
        device.terminal.print("SwitchLED takes 0, 1, ON or OFF; LED unchanged.")


@device.on_command("Print")
def print_text(*params: str):
    if len(params) != 2:
        device.terminal.print("Print takes two parameters: text and count.")
        return
    text, count = params
    if not count.isdigit() or not 1 <= int(count) <= 10:
        device.terminal.print("Print takes a count from 1 to 10.")
        return
    for _ in range(int(count)):
        device.terminal.print(text)


@device.on_any_command
def log_command(command: str, *params: str) -> bool:
    # Returns False, as it only logs: a mistyped name is still answered as unknown.
    if not command.startswith("BLAECK."):
        device.terminal.print(f"Command: {command}, parameters: {len(params)}")
    return False


# Loggbok connects to this computer on TCP port 2323. For serial instead, use
# device.begin(serial.Serial("COM3", 115200, timeout=0)) after import serial (pip install pyserial).
device.begin(socket.create_server(("", 2323)))
while True:
    device.tick(timeout=0.1)
