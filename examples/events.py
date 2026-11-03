"""Every kind of metadata an event can carry, one event each, to compare how a dashboard
renders them. An event is an occurrence, not a state: it carries no value and nothing to switch
off again, which is what separates it from a bool sensor.

A port of blaeck's Events example. Every 5 s the next event in the list is reported:

    Doorbell    device class doorbell, which Home Assistant expects with a "ring" type
    Button      device class button, using Home Assistant's own names for press and hold
    Motion      device class motion, a pair of types rather than one
    System      no device class, diagnostic, and one type added only with an overheat sensor

Events are reported only while entities are started; one raised meanwhile is lost.
"""

import time

from blaeckpy import Blaeck, Category
from connect import open_transport

HAS_OVERHEAT_SENSOR = True

device = Blaeck("Events", fw_version="1.0")
uptime = device.add_signal("Uptime", int, unit="s")
event_count = device.add_signal("EventCount", int)
doorbell = device.add_event("Doorbell", ["ring"], icon="mdi:doorbell", device_class="doorbell")
button = device.add_event("Button", ["press_start", "press_end", "long_press_start",
                                     "long_press_end"],
                          icon="mdi:gesture-tap-button", device_class="button")
motion = device.add_event("Motion", ["motion_detected", "motion_cleared"], device_class="motion")
system = device.add_event("System", ["started", "config_changed"], icon="mdi:cog",
                          category=Category.DIAGNOSTIC, disabled_by_default=True)
if HAS_OVERHEAT_SENSOR:
    system.add_type("overheated")

steps = [(doorbell, "ring"), (button, "press_start"), (button, "press_end"),
         (button, "long_press_start"), (button, "long_press_end"),
         (motion, "motion_detected"), (motion, "motion_cleared"), (system, "config_changed")]
if HAS_OVERHEAT_SENSOR:
    steps.append((system, "overheated"))

device.begin(open_transport(__doc__))
system.write("started")
event_count.value += 1
start = last_fired = time.monotonic()
step = 0
while True:
    uptime.value = int(time.monotonic() - start)
    device.tick(timeout=0.1)
    if time.monotonic() - last_fired >= 5.0:
        last_fired = time.monotonic()
        event, type_ = steps[step]
        event.write(type_)
        event_count.value += 1
        step = (step + 1) % len(steps)
