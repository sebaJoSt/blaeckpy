"""A waveform generator to drive from a Home Assistant dashboard through Loggbok's MQTT bridge.
The device declares its inputs, sensors, button and event, and Loggbok turns them into Home
Assistant entities, so the dashboard comes from this script rather than being built by hand.

Log fast enough to resolve the wave: at 1 Hz, a 20 ms interval gives 50 points per cycle, at
10 Hz only 5. Sample slower and Output aliases into a waveform the device never made.

    signal   logged                      Output, Frequency, Annotation
    input    set by a host, sent back    Frequency, Amplitude, Offset, Waveform, OutputEnabled,
                                         DeviceLabel, Annotation
    sensor   only shown                  Output, Uptime, Status, StatusOnDemand
    button   STATUS                      fills StatusOnDemand
    event    Activity                    idle_warning after 5 s with the output off, resumed

Frequency and Annotation are inputs and signals: the input shows and sets the value, the signal
logs it.
"""

import math
import socket
import time

from blaeckpy import Blaeck, Category, Mode, StateClass

WAVES = ["Sine", "Square", "Triangle", "Sawtooth"]
ICONS = ["mdi:sine-wave", "mdi:square-wave", "mdi:triangle-wave", "mdi:sawtooth-wave"]

device = Blaeck("WaveformGenerator", fw_version="1.0")
device.set_timestamp_mode("micros")

output = device.add_signal("Output", 0.0, unit="V")
frequency_signal = device.add_signal("Frequency", 1.0, unit="Hz")
annotation_signal = device.add_signal("Annotation", "")

frequency = device.add_number_input("Frequency", 1.0, min=0.0, max=10.0, step=0.01, unit="Hz",
                                    mode=Mode.BOX)
amplitude = device.add_number_input("Amplitude", 1.0, min=0.0, max=100.0, step=0.1,
                                    mode=Mode.SLIDER)
offset = device.add_number_input("Offset", 0.0, min=-100.0, max=100.0, step=0.1)
waveform = device.add_select("Waveform", 0, options=WAVES, icon="mdi:waveform")
enabled = device.add_switch("OutputEnabled", True, display_name="Output enabled")
label = device.add_text_input("DeviceLabel", "wave-gen", max_length=32,
                              display_name="Device label", icon="mdi:tag",
                              category=Category.CONFIG)
annotation = device.add_text_input("Annotation", "", max_length=24)

output_sensor = device.add_sensor("Output", output, unit="V", device_class="voltage",
                                  state_class=StateClass.MEASUREMENT, display_precision=3,
                                  icon=ICONS[0], write_on_change=0.001, min_interval_ms=500)
uptime = device.add_sensor("Uptime", 0, unit="s", device_class="duration",
                           category=Category.DIAGNOSTIC, write_on_change=0, min_interval_ms=10000)
status = device.add_sensor("Status", "", icon="mdi:pulse", category=Category.DIAGNOSTIC)
status_on_demand = device.add_sensor("StatusOnDemand", "", icon="mdi:message-text",
                                     category=Category.DIAGNOSTIC)
activity = device.add_event("Activity", ["idle_warning", "resumed"], icon="mdi:bell-alert")


def status_line() -> str:
    state = "running" if enabled.value else "stopped"
    return f"{label.value}: {state} {waveform.option} @ {frequency.value:.2f} Hz"


@frequency.on_change
def frequency_changed():
    frequency_signal.value = frequency.value


@annotation.on_change
def annotation_changed():
    annotation_signal.value = annotation.value


@waveform.on_change
def waveform_changed():
    output_sensor.icon = ICONS[waveform.value]


@label.on_change
def label_changed():
    status.value = status_line()


@device.add_button("STATUS", display_name="Request status", category=Category.DIAGNOSTIC)
def request_status():
    status_on_demand.value = status_line()


phase = 0.0
last = time.monotonic()


@device.on_before_write
def update_waveform():
    global phase, last
    now = time.monotonic()
    dt, last = now - last, now
    if not enabled.value:
        output.value = offset.value
        return
    phase = (phase + frequency.value * dt) % 1.0
    wave = waveform.option
    if wave == "Square":
        w = 1.0 if phase < 0.5 else -1.0
    elif wave == "Triangle":
        w = 1.0 - 4.0 * abs(phase - 0.5)
    elif wave == "Sawtooth":
        w = 2.0 * phase - 1.0
    else:
        w = math.sin(2 * math.pi * phase)
    output.value = offset.value + amplitude.value * w


# Loggbok connects to this computer on TCP port 2323. For serial instead, use
# device.begin(serial.Serial("COM3", 115200, timeout=0)) after import serial (pip install pyserial).
device.begin(socket.create_server(("", 2323)))
print("##LOGGBOK:READY##")  # Sentinel for Loggbok's process launcher — safe to remove
start = last_status = time.monotonic()
status.value = status_line()
idle_since = None
warned = False
while True:
    now = time.monotonic()
    uptime.value = int(now - start)
    if now - last_status >= 10.0:
        last_status = now
        status.value = status_line()
    if not enabled.value:
        if idle_since is None:
            idle_since = now
        if not warned and now - idle_since >= 5.0:
            activity.write("idle_warning")
            warned = True
    else:
        if warned:
            activity.write("resumed")
        idle_since = None
        warned = False
    update_waveform()
    device.tick(timeout=0.005)
