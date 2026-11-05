"""Rules for inputs, sensors, events, buttons and commands that the golden scenarios cannot show."""

import logging
import math
import re

import pytest

from blaeckpy import Blaeck, Category, Mode
from blaeckpy.protocol import _decode


class Port:
    def __init__(self):
        self.incoming, self.outgoing = bytearray(), bytearray()

    @property
    def in_waiting(self):
        return len(self.incoming)

    def read(self, n):
        data = bytes(self.incoming[:n])
        del self.incoming[:n]
        return data

    def write(self, data):
        self.outgoing += data


def ack_reason(device, port, command):
    from blaeckpy.protocol import envelope
    port.outgoing.clear()
    port.incoming += command.encode()
    device.tick()
    acks = [envelope.open_frame(f + b"/>\n") for f in bytes(port.outgoing).split(b"/>\n")[:-1]]
    return [a for a in acks if a.key == 0xA5][0].fields[9]


@pytest.mark.parametrize("name", ["Pump speed", "Ölstand", "a,b", "", "BLAECK.MINE"])
def test_a_name_a_host_sends_is_ascii_letters_digits_and_dash_dot_underscore(name):
    with pytest.raises(ValueError):
        Blaeck().add_switch(name)


def test_inputs_sensors_buttons_and_commands_share_one_set_of_names():
    device = Blaeck()
    device.add_switch("Heater")
    with pytest.raises(ValueError):
        device.add_button("Heater", lambda: None)
    with pytest.raises(ValueError):
        device.on_command("Heater", lambda *p: None)
    device.add_event("Heater", ["on"])  # events are not addressed by a host


def test_modes_and_categories_take_enums_or_strings():
    device = Blaeck()
    device.add_number_input("A", 0.0, mode=Mode.SLIDER, category=Category.CONFIG)
    device.add_number_input("B", 0.0, mode="slider", category="config")
    with pytest.raises(ValueError):
        device.add_number_input("C", 0.0, mode="password")
    with pytest.raises(ValueError):
        device.add_text_input("D", mode="slider")
    with pytest.raises(ValueError):
        device.add_switch("E", category="main")


def test_a_range_is_whole_for_an_int_input_and_min_is_below_max():
    device = Blaeck()
    with pytest.raises(TypeError):
        device.add_number_input("A", int, min=0, max=2.5)
    with pytest.raises(ValueError):
        device.add_number_input("B", 0.0, min=5.0, max=5.0)
    with pytest.raises(ValueError):
        device.add_number_input("C", 0.0, min=0.0, max=1.0, step=0)


def test_a_select_holds_at_most_256_options_and_a_valid_index():
    device = Blaeck()
    device.add_select("A", options=[str(i) for i in range(256)])
    with pytest.raises(ValueError):
        device.add_select("B", options=[str(i) for i in range(257)])
    mode = device.add_select("C", options=["Auto", "Manual"])
    with pytest.raises(ValueError):
        mode.value = 2
    mode.value = 1
    assert mode.option == "Manual"


def test_a_sensor_reads_its_function_and_has_no_value_to_set():
    device = Blaeck()
    readings = iter([1.5, 2.5])
    sensor = device.add_sensor("T", float, get=lambda: next(readings))
    assert sensor.value == 1.5 and sensor.value == 2.5
    with pytest.raises(AttributeError):
        sensor.value = 3.0
    with pytest.raises(TypeError):
        device.add_sensor("U", 1.0, get=lambda: 1.0)


def test_a_sensor_showing_a_signal_follows_it():
    device = Blaeck()
    signal = device.add_signal("Temperature", 21.0)
    sensor = device.add_sensor("Temperature", signal, unit="°C")
    signal.value = 22.5
    assert sensor.value == 22.5


def test_unit_is_for_numbers_only():
    with pytest.raises(TypeError):
        Blaeck().add_sensor("Door", True, unit="x")


def test_a_raising_callback_is_logged_and_the_host_still_gets_its_answer(caplog):
    device, port = Blaeck(), Port()

    @device.on_command("BOOM")
    def boom(*params):
        raise RuntimeError("bad parameter")

    device.begin(port)
    with caplog.at_level(logging.ERROR, logger="blaeckpy"):
        assert ack_reason(device, port, "<BOOM,1>") == 0
    assert "bad parameter" in caplog.text


def test_a_raising_get_is_logged_once_and_its_last_value_stands_in(caplog):
    device, port = Blaeck(), Port()
    readings = [1.5]

    def read():
        if not readings:
            raise OSError("sensor gone")
        return readings.pop()

    sensor = device.add_sensor("T", float, get=read)
    device.begin(port)
    with caplog.at_level(logging.ERROR, logger="blaeckpy"):
        assert ack_reason(device, port, "<BLAECK.ENTITIES_START>") == 0
        for _ in range(3):
            device.tick()
        assert ack_reason(device, port, "<BLAECK.WRITE_ENTITIES>") == 0
    assert len([r for r in caplog.records if "get= of sensor" in r.getMessage()]) == 1
    assert sensor._current() == 1.5
    # Read by the script, it raises.
    with pytest.raises(OSError):
        sensor.value


def test_a_get_is_read_once_a_pass_and_not_while_nothing_can_go_out():
    device, port, calls = Blaeck(), Port(), []
    device.add_sensor("T", float, get=lambda: calls.append(1) or float(len(calls)),
                      min_interval_ms=0)
    device.begin(port)
    for _ in range(5):
        device.tick()
    # Only for the entity list announcing the restart; with entities stopped, not since.
    assert len(calls) == 1
    ack_reason(device, port, "<BLAECK.ENTITIES_START>")
    calls.clear()
    port.outgoing.clear()
    for _ in range(5):
        device.tick()
    assert len(calls) == 5
    # Each pass sent the value it compared: 1.0 to 5.0, none skipped or read twice.
    from blaeckpy.protocol import envelope
    sent = [envelope.open_frame(f + b"/>\n") for f in bytes(port.outgoing).split(b"/>\n")[:-1]]
    assert len([f for f in sent if f.key == 0x95]) == 5


def test_close_ends_the_device_as_end_does_in_blaeck():
    device, port, called = Blaeck(), Port(), []
    device.add_signal("T", 1.0)
    device.on_data_stop(lambda: called.append("data"))
    device.on_entities_stop(lambda: called.append("entities"))
    device.on_interval_stop(lambda: called.append("interval"))
    device.begin(port)
    ack_reason(device, port, "<BLAECK.DATA_START>")
    ack_reason(device, port, "<BLAECK.ENTITIES_START>")
    ack_reason(device, port, "<BLAECK.INTERVAL_START,100>")
    device.close()
    assert called == []
    with pytest.raises(RuntimeError, match="begins once"):
        device.begin(Port())
    port.outgoing.clear()
    device.tick()
    device.close()
    assert port.outgoing == b""


def test_the_catch_all_takes_a_command_only_by_returning_true():
    device, port = Blaeck(), Port()
    seen = []

    @device.on_any_command
    def everything(command, *params):
        seen.append((command, params))

    device.begin(port)
    assert ack_reason(device, port, "<Nope,1,2>") == 1
    device.on_any_command(lambda command, *params: True)
    assert ack_reason(device, port, "<Nope>") == 0
    assert seen == [("Nope", ("1", "2"))]


def test_a_button_ignores_parameters():
    device, port, presses = Blaeck(), Port(), []
    device.add_button("Reboot", lambda: presses.append(1))
    device.begin(port)
    assert ack_reason(device, port, "<Reboot,now>") == 0
    assert presses == [1]


def test_add_button_works_as_a_decorator_and_leaves_the_function():
    device, port, presses = Blaeck(), Port(), []

    @device.add_button("Reboot", icon="mdi:restart")
    def reboot():
        presses.append(1)

    reboot()
    device.begin(port)
    assert ack_reason(device, port, "<Reboot>") == 0
    assert presses == [1, 1]


def test_an_event_writes_only_its_own_types():
    event = Blaeck().add_event("Alarms", ["Overheat"])
    with pytest.raises(ValueError):
        event.write("Dry run")
    with pytest.raises(ValueError):
        Blaeck().add_event("Alarms", ["a", "a"])


def test_device_names_are_unique_on_the_board_the_board_included():
    device = Blaeck("Greenhouse")
    device.add_device("Pump controller")
    with pytest.raises(ValueError):
        device.add_device("Pump controller")
    with pytest.raises(ValueError):
        device.add_device("Greenhouse")
    with pytest.raises(ValueError):
        device.add_device("")


def test_signals_and_events_are_unique_per_device_host_names_on_the_board():
    device = Blaeck()
    pump = device.add_device("Pump")
    device.add_signal("Flow", 0.0)
    pump.add_signal("Flow", 0.0)
    device.add_event("Alarms", ["a"])
    pump.add_event("Alarms", ["restarted"])
    device.add_number_input("Speed", 0)
    with pytest.raises(ValueError):
        pump.add_number_input("Speed", 0)


def test_a_missing_device_refuses_commands_and_comes_back():
    device, port, calls = Blaeck(), Port(), []
    pump = device.add_device("Pump")
    pump.add_number_input("PumpSpeed", 0)
    pump.on_command("PRIME", lambda *p: calls.append(p))
    device.begin(port)
    pump.mark_missing()
    assert pump.missing
    assert ack_reason(device, port, "<PumpSpeed,5>") == 8
    assert ack_reason(device, port, "<PRIME,1>") == 8 and calls == []
    pump.mark_present()
    assert ack_reason(device, port, "<PRIME,1>") == 0 and calls == [("1",)]


def test_clearing_lets_a_new_set_be_declared_and_old_handles_say_so():
    device = Blaeck()
    pump = device.add_device("Pump")
    old_signal = pump.add_signal("Flow", 0.0)
    old_event = device.add_event("Alarms", ["a"])
    device.add_switch("Heater")
    device.add_button("Reset", lambda: None)
    device.add_sensor("Room", 1.0)
    device.on_command("MOTOR", lambda *p: None)
    device.clear_all_signals()
    device.clear_all_events()
    device.clear_all_controls()
    device.clear_all_commands()
    with pytest.raises(RuntimeError):
        old_signal.write(1.0)
    # Refused before anything changes: the old value stays.
    assert old_signal.value != 1.0
    with pytest.raises(RuntimeError):
        old_event.write("a")
    # The names are free again; the sensor stays, as sensors go with clear_all_sensors().
    pump.add_signal("Flow", 0)
    device.add_event("Alarms", ["b"])
    device.add_switch("Heater")
    device.add_button("Reset", lambda: None)
    device.on_command("MOTOR", lambda *p: None)
    with pytest.raises(ValueError):
        device.add_sensor("Room", 2.0)
    device.clear_all_sensors()
    device.add_sensor("Room", 2.0)


def test_a_255_byte_text_fits_a_command_even_fully_percent_encoded():
    from blaeckpy.protocol import _decode
    device, port = Blaeck(), Port()
    label = device.add_text_input("Label", max_length=255)
    device.on_command("Long", lambda *p: None)
    device.begin(port)
    text = "é" * 127 + "x"  # 255 bytes
    encoded = "".join(f"%{b:02X}" for b in text.encode())
    assert ack_reason(device, port, f"<#65535:Label,{encoded}>") == 0
    assert label.value == text
    # One character more than the device list advertises is refused as cut off.
    port.outgoing.clear()
    port.incoming += b"<BLAECK.GET_DEVICES>"
    device.tick()
    advertised = [f.body for f in _decode.Decoder().feed(bytes(port.outgoing))
                  if isinstance(f, _decode.Frame) and isinstance(f.body, _decode.DeviceList)][0]
    assert advertised.command_payload_max == 1023
    assert ack_reason(device, port, "<Long," + "x" * 1018 + ">") == 0  # 1023 characters
    assert ack_reason(device, port, "<Long," + "x" * 1019 + ">") == 7


def test_a_text_that_is_not_utf8_goes_back_out_as_the_same_bytes():
    # blaeck stores what it decoded and sends it back, without looking at the encoding.
    device, port = Blaeck(), Port()
    note = device.add_text_input("Note")
    copy = device.add_signal("Copy", str)
    device.begin(port)
    device.tick()

    assert ack_reason(device, port, "<Note,%FF>") == 0
    assert note.value == "\udcff"
    assert b"\xff" in bytes(port.outgoing)

    # It stays a value like any other: in the entity list, a signal, and the terminal.
    copy.value = note.value
    device.terminal.print(note.value)
    port.outgoing.clear()
    port.incoming += b"<BLAECK.WRITE_ENTITIES><BLAECK.WRITE_DATA>"
    device.tick()
    frames = _decode.Decoder().feed(bytes(port.outgoing))
    frames = [f for f in frames if isinstance(f, _decode.Frame)]
    assert [f.body.properties[0].value for f in frames if f.key == 0x90] == ["\udcff"]


def test_a_change_on_a_missing_device_doesnt_keep_tick_from_waiting_and_goes_out_on_its_return():
    device, port = Blaeck(), Port()
    pump = device.add_device("Pump")
    flow = pump.add_signal("Flow", 1.0, write_on_change=0.0, min_interval_ms=0)
    device.begin(port)
    port.incoming += b"<BLAECK.GET_DEVICES><BLAECK.DATA_START>"
    device.tick()
    device.tick()

    pump.mark_missing()
    flow.value = 2.0
    passes = device._pass
    device.tick(timeout=0.1)
    assert device._pass - passes < 100  # With nothing to select on it polls every 10 ms.

    def values():
        frames = _decode.Decoder().feed(bytes(port.outgoing))
        return [f.body.values for f in frames if isinstance(f, _decode.Frame) and f.key == 0xD3]

    assert {0: 2.0} not in values()
    pump.mark_present()
    device.tick()
    assert values()[-1] == {0: 2.0}


def clock_device():
    device, port = Blaeck(), Port()
    speed = device.add_signal("Speed", 1.0, write_on_change=0.0, min_interval_ms=0)
    device.begin(port)
    port.incoming += b"<BLAECK.GET_DEVICES>"
    device.tick()
    return device, port, speed


def data_frames(port):
    frames = _decode.Decoder().feed(bytes(port.outgoing))
    return [f.body for f in frames if isinstance(f, _decode.Frame) and f.key == 0xD3]


@pytest.mark.parametrize("failure", ["raises", "negative", "too big", "not a number"])
def test_while_the_clock_fails_no_data_goes_out_and_a_change_goes_out_once_it_works(failure, caplog):
    device, port, speed = clock_device()
    broken = [True]

    def clock():
        if not broken[0]:
            return 5
        if failure == "raises":
            raise OSError("no RTC")
        return {"negative": -1, "too big": 2**64, "not a number": "soon"}[failure]

    device.set_timestamp_mode("unix", clock=clock)
    port.incoming += b"<BLAECK.DATA_START>"
    with caplog.at_level(logging.ERROR, logger="blaeckpy"):
        device.tick()
        speed.value = 2.0
        passes = device._pass
        device.tick(timeout=0.1)
    assert device._pass - passes < 100  # With nothing to select on it polls every 10 ms.
    assert data_frames(port) == []
    assert len([r for r in caplog.records if "clock" in r.getMessage()]) == 1

    broken[0] = False
    device.tick()
    frames = data_frames(port)
    assert frames[-1].values == {0: 2.0} and frames[-1].timestamp == 5


def test_a_clock_that_fails_again_after_working_is_logged_again(caplog):
    device, port, speed = clock_device()
    readings = iter([5])

    def clock():
        return next(readings, None) or 1 / 0

    device.set_timestamp_mode("unix", clock=clock)
    port.incoming += b"<BLAECK.DATA_START>"
    device.tick()
    assert len(data_frames(port)) == 1
    speed.value = 2.0
    with caplog.at_level(logging.ERROR, logger="blaeckpy"):
        device.tick()
        readings = iter([6])
        device.tick()
        speed.value = 3.0
        device.tick()
    assert len([r for r in caplog.records if "clock" in r.getMessage()]) == 2


def test_the_clock_is_read_only_when_a_frame_goes_out():
    device, port, speed = clock_device()
    calls = []
    device.set_timestamp_mode("unix", clock=lambda: calls.append(1) or 5)
    for _ in range(100):
        device.tick()
    assert calls == []
    port.incoming += b"<BLAECK.WRITE_DATA>"
    device.tick()
    assert len(calls) == 1 and data_frames(port)[-1].timestamp == 5


def test_a_request_while_the_clock_fails_is_acked_and_sends_nothing():
    device, port, _ = clock_device()
    device.set_timestamp_mode("unix", clock=lambda: 1 / 0)
    assert ack_reason(device, port, "<BLAECK.WRITE_DATA>") == 0
    assert data_frames(port) == []
    device.write_all()  # From the script too: logged, not raised.
    assert data_frames(port) == []


@pytest.mark.parametrize("declare, call", [
    (lambda d: d.add_button("Reset"), "add_button('Reset')"),
    (lambda d: d.add_button("Reset", None), "add_button('Reset')"),
    (lambda d: d.on_command("MOTOR"), "on_command('MOTOR')"),
    (lambda d: d.on_command("MOTOR", None), "on_command('MOTOR')"),
    (lambda d: d.add_device("Pump").add_button("Reset"), "add_button('Reset')"),
    (lambda d: d.add_device("Pump").on_command("MOTOR"), "on_command('MOTOR')"),
])
def test_begin_refuses_a_button_or_command_left_without_a_function(declare, call):
    device, port = Blaeck(), Port()
    declare(device)
    with pytest.raises(ValueError, match=re.escape(f"{call} has no function")):
        device.begin(port)


def test_a_decorator_given_its_function_before_begin_is_no_mistake():
    device, port, pressed = Blaeck(), Port(), []
    waiting = device.add_button("Reset")
    command = device.on_command("MOTOR")
    waiting(lambda: pressed.append("Reset"))
    command(lambda *params: pressed.append("MOTOR"))
    device.begin(port)
    assert ack_reason(device, port, "<Reset>") == 0
    assert ack_reason(device, port, "<MOTOR,1>") == 0
    assert pressed == ["Reset", "MOTOR"]


def entity_lists_after_tick(device, port):
    from blaeckpy.protocol import envelope
    port.outgoing.clear()
    device.tick()
    frames = [envelope.open_frame(f + b"/>\n") for f in bytes(port.outgoing).split(b"/>\n")[:-1]]
    return [f for f in frames if f.key == 0x90]


def test_a_setting_changed_after_begin_sends_the_entity_list_again_and_only_once():
    device, port = Blaeck(), Port()
    output = device.add_sensor("Output", 0.0, icon="mdi:sine-wave")
    speed = device.add_number_input("Speed", 0, min=0, max=10)
    button = device.add_button("Reset", lambda: None)
    device.begin(port)
    port.incoming += b"<BLAECK.ENTITIES_START>"
    device.tick()
    assert entity_lists_after_tick(device, port) == []

    output.icon = "mdi:square-wave"
    lists = entity_lists_after_tick(device, port)
    assert len(lists) == 1 and b"mdi:square-wave" in lists[0].fields
    output.icon = "mdi:square-wave"
    assert entity_lists_after_tick(device, port) == []

    speed.set_range(0, 20, 2)
    button.display_name = "Reset all"
    assert len(entity_lists_after_tick(device, port)) == 1
    assert (speed.min, speed.max, speed.step) == (0, 20, 2)


def test_a_changed_setting_is_checked_as_add_does_and_a_refused_one_keeps_the_old():
    device = Blaeck()
    speed = device.add_number_input("Speed", 0, min=0, max=10)
    label = device.add_text_input("Label")
    with pytest.raises(ValueError):
        speed.set_range(10, 0)
    with pytest.raises(TypeError):
        speed.set_range(0, 1.5)
    assert (speed.min, speed.max) == (0, 10)
    with pytest.raises(ValueError):
        speed.mode = "password"
    with pytest.raises(TypeError):
        label.unit = "V"
    with pytest.raises(ValueError):
        label.icon = "mdi:\ntag"
    label.mode = "password"
    assert label.mode is Mode.PASSWORD


def test_a_signals_policies_and_unit_change_while_running():
    from blaeckpy import OFF, OnChange
    device = Blaeck()
    flow = device.add_signal("Flow", 0.0, unit="l/min")
    flow.write_on_change = 0.5
    flow.min_interval_ms = 250
    assert (flow.write_on_change, flow._reporting.min_interval_ms) == (0.5, 250)
    flow.write_on_change = None
    flow.write_at_interval = OFF
    assert flow._reporting is None
    flow.write_at_interval = OnChange(1.0)
    assert flow._reporting.interval_delta == 1.0
    flow.unit = None
    assert flow.unit == ""
    with pytest.raises(TypeError):
        flow.write_at_interval = "OFF"



@pytest.mark.parametrize("bounds", [dict(min=0.0, max=math.inf), dict(min=-math.inf, max=0.0),
                                    dict(min=0.0, max=1.0, step=math.inf)])
def test_a_float_range_and_step_are_finite(bounds):
    with pytest.raises(ValueError):
        Blaeck().add_number_input("N", 0.0, **bounds)


def test_an_option_with_a_null_byte_is_refused_when_declared():
    with pytest.raises(ValueError):
        Blaeck().add_select("Mode", options=["Off", "O\x00n"])


def test_on_change_may_clear_the_controls_it_belongs_to():
    device, port = Blaeck(), Port()
    device.add_switch("Heater").on_change(device.clear_all_controls)
    device.begin(port)
    assert ack_reason(device, port, "<BLAECK.ENTITIES_START><Heater,1>") == 0
    device.tick()
    assert device._properties == []


def test_a_huge_interval_is_capped_as_strtoul_caps_it():
    device, port = Blaeck(), Port()
    device.add_signal("S", 1.0)
    device.begin(port)
    port.incoming += b"<BLAECK.INTERVAL_START," + b"9" * 400 + b">"
    device.tick(timeout=0.01)
    assert device._interval_ms == 0xFFFFFFFF


def test_after_close_on_before_write_no_longer_runs():
    device, port = Blaeck(), Port()
    calls = []
    device.add_signal("S", 1.0)
    device.on_before_write(lambda: calls.append(1))
    device.begin(port)
    port.incoming += b"<BLAECK.DATA_START><BLAECK.INTERVAL_START,0>"
    device.tick()
    device.tick()
    assert calls
    device.close()
    calls.clear()
    device.tick()
    device.write_all()
    assert calls == []


@pytest.mark.parametrize("kind", ["property", "event"])
def test_properties_and_events_wait_for_their_catalog(monkeypatch, kind):
    from blaeckpy import _transport
    from blaeckpy.protocol import envelope
    monkeypatch.setattr(_transport, "_SEND_TIMEOUT", 0)

    class CatalogStalledPort(Port):
        stalled = False

        def write(self, data):
            if self.stalled and data.startswith(envelope.START + b"\x90"):
                return 0
            self.outgoing += data
            return len(data)

    device, port = Blaeck(), CatalogStalledPort()
    device.begin(port)
    assert ack_reason(device, port, "<BLAECK.ENTITIES_START>") == 0
    port.outgoing.clear()
    if kind == "property":
        write = device.add_switch("Heater", True).write
    else:
        event = device.add_event("Alarms", ["Overheat"])
        write = lambda: event.write("Overheat")

    port.stalled = True
    write()
    assert port.outgoing == b""

    port.stalled = False
    write()
    sent = [f for f in _decode.Decoder().feed(bytes(port.outgoing))
            if isinstance(f, _decode.Frame)]
    assert [f.key for f in sent] == [0x90, 0x95 if kind == "property" else 0x85]
    device.close()


@pytest.mark.parametrize("update", [True, False], ids=["changed", "requested"])
def test_a_failed_catalog_is_retried_after_the_port_recovers(monkeypatch, update):
    from blaeckpy import _transport
    monkeypatch.setattr(_transport, "_SEND_TIMEOUT", 0)
    device, port = Blaeck(), TrickledPort()
    device.add_switch("Heater")
    device.begin(port)
    assert ack_reason(device, port, "<BLAECK.ENTITIES_START>") == 0
    port.outgoing.clear()
    if update:
        device.add_switch("Pump", True)
    else:
        port.incoming += b"<BLAECK.WRITE_ENTITIES>"

    port.stalled = True
    device.tick()
    assert port.outgoing == b""

    port.stalled = False
    device.tick()
    device.tick()
    sent = [f for f in _decode.Decoder().feed(bytes(port.outgoing))
            if isinstance(f, _decode.Frame)]
    assert [f.key for f in sent] == ([0x90, 0x95] if update else [0x90])
    assert [p.name for p in sent[0].body.properties] == (["Heater", "Pump"] if update else ["Heater"])
    if update:
        assert sent[1].body.index == 1 and sent[1].body.value is True
    device.close()


class TrickledPort(Port):
    """Takes at most 3 bytes a write, and none while stalled."""
    stalled = False

    def write(self, data):
        if self.stalled:
            return 0
        self.outgoing += data[:3]
        return min(3, len(data))


def test_a_frame_the_port_takes_in_parts_goes_out_whole_and_a_stalled_one_is_sent_again(monkeypatch):
    from blaeckpy import _transport
    monkeypatch.setattr(_transport, "_SEND_TIMEOUT", 0.01)
    device, port = Blaeck(), TrickledPort()
    speed = device.add_signal("Speed", 1.0, write_on_change=0.0, min_interval_ms=0)
    device.begin(port)
    port.incoming += b"<BLAECK.GET_DEVICES><BLAECK.DATA_START>"
    device.tick()
    device.tick()
    assert [f.values for f in data_frames(port)] == [{0: 1.0}]

    port.stalled = True
    speed.value = 2.0
    device.tick()
    port.stalled = False
    device.tick()
    assert [f.values for f in data_frames(port)][-1] == {0: 2.0}


def test_device_notices_that_did_not_go_out_are_sent_once_the_port_recovers(monkeypatch):
    from blaeckpy import _transport
    monkeypatch.setattr(_transport, "_SEND_TIMEOUT", 0)
    device, port = Blaeck(), TrickledPort()
    pump = device.add_device("Pump")
    port.stalled = True
    device.begin(port)
    device.tick()
    pump.write_restarted()
    pump.mark_missing()
    port.stalled = False
    device.tick()
    sent = [f for f in _decode.Decoder().feed(bytes(port.outgoing)) if isinstance(f, _decode.Frame)]
    notices = [(f.body.device_id, f.body.event) for f in sent if f.key == 0xC1]
    assert notices == [(0, 1), (1, 1), (1, 2)]
    device.close()


def test_a_requested_write_carries_the_timestamp_tick_was_given():
    device, port, speed = clock_device()
    device.set_timestamp_mode("unix", clock=lambda: 5)
    port.incoming += b"<BLAECK.WRITE_DATA>"
    device.tick(timestamp=123)
    assert data_frames(port)[-1].timestamp == 123


def test_a_command_whose_function_closes_the_device_runs_nothing_after_it():
    device, port, seen = Blaeck(), Port(), []
    device.on_command("STOP", lambda *p: device.close())
    device.on_any_command(lambda name, *p: seen.append(name))
    device.begin(port)
    port.incoming += b"<STOP>"
    device.tick()
    assert seen == []
    device.tick()


def test_a_closed_device_cannot_begin():
    device = Blaeck()
    device.close()
    with pytest.raises(RuntimeError, match="closed"):
        device.begin(Port())


@pytest.mark.parametrize("low, high, step, sent", [
    (1.0001, 1.1001, 0.1, 1.0001),
    (1.0001, 1.1001, 0.1, 1.1001),
    (0.05, 0.15, 0.1, 0.15),
    (0.01, 0.02, 0.0001, 0.015),
    (0.0, 1.0, 0.1, 0.3),
    (-1.25, 1.25, 0.25, -0.75),
    (1000000.0001, 1000001.0001, 0.1, 1000000.1001),
    (1.0000000001, 2.0000000001, 0.1, 1.1000000001),
    (0.05, 0.050001, 0.0000001, 0.050001),
    (0.0, 1.0, 5e-324, 0.5),
    (-1e308, 1e308, 1.0, 1e308),
])
def test_a_value_on_a_step_is_stored_as_the_decimal_sent_and_never_leaves_the_range(low, high, step, sent):
    device, port = Blaeck(), Port()
    value = device.add_number_input("Value", low, min=low, max=high, step=step)
    device.begin(port)
    assert ack_reason(device, port, f"<Value,{sent!r}>") == 0
    assert value.value == sent


@pytest.mark.parametrize("low, high, step, sent, expected", [
    (0.0, 0.299999, 0.1, 0.299999, 0.299999),
    (-1.0, -0.700001, 0.1, -0.700001, -0.700001),
    (0.0, 1.0, 0.1, 0.30000000000000004, 0.3),
])
def test_a_near_step_is_snapped_only_if_the_result_fits_the_range(low, high, step, sent, expected):
    device, port = Blaeck(), Port()
    value = device.add_number_input("Value", low, min=low, max=high, step=step)
    device.begin(port)
    assert ack_reason(device, port, f"<Value,{sent!r}>") == 0
    assert value.value == expected

def test_a_flood_on_a_port_stays_in_the_port_and_all_of_it_runs():
    device, port, runs = Blaeck(), Port(), []
    device.on_command("a", lambda *params: runs.append(1))
    device.begin(port)
    port.incoming += b"<a>" * 100_000
    most = 0
    while len(runs) < 100_000:
        device.tick()
        receiver = device._transport._receiver
        most = max(most, len(receiver._pending) - receiver._read)
    assert most <= 2 * 4096
    device.close()


@pytest.mark.parametrize("bad", [2, None, "1\x002", "1\n", "\ud83d"])
@pytest.mark.parametrize("key", ["hw_version", "fw_version"])
def test_a_version_a_frame_cant_carry_is_refused_where_it_was_written(key, bad):
    with pytest.raises((TypeError, ValueError)):
        Blaeck("Bench", **{key: bad})
    with pytest.raises((TypeError, ValueError)):
        Blaeck("Bench").add_device("Pump", **{key: bad})


@pytest.mark.parametrize("declare", [
    lambda d: Blaeck("\ud83d"),
    lambda d: d.add_device("\ud83d"),
    lambda d: d.add_signal("\ud83d", 1.0),
    lambda d: d.add_signal("S", 1.0, unit="\ud83d"),
    lambda d: d.add_event("\ud83d", ["a"]),
    lambda d: d.add_event("E", ["\ud83d"]),
    lambda d: d.add_event("E", ["a"]).add_type("\ud83d"),
    lambda d: d.add_select("M", 0, options=["\ud83d"]),
    lambda d: d.add_switch("S", display_name="\ud83d"),
    lambda d: d.add_button("B", lambda: None, icon="\ud83d"),
], ids=["device", "sub-device", "signal", "unit", "event", "event type", "added type", "option",
        "display name", "icon"])
def test_text_that_isnt_valid_unicode_is_refused_where_it_was_written(declare):
    with pytest.raises(ValueError):
        declare(Blaeck())


def test_emojis_go_out_in_everything_a_script_names_freely():
    device, port = Blaeck("🌡️ Bench", hw_version="Pi 🥧", fw_version="1.0 🚀"), Port()
    device.add_signal("Temperatur 🌡️", 21.5, unit="°C 🔥")
    status = device.add_signal("Status", str)
    status.value = "ok ✅"
    device.add_event("Alarm 🚨", ["heiß 🔥"])
    device.add_select("Mode", 0, options=["Aus 💤", "An ⚡"], display_name="Modus 🎛️")
    note = device.add_text_input("Note", "hi 👋")
    device.add_button("Reset", lambda: None, display_name="Neustart 🔄")
    device.add_device("Pumpe 💧", hw_version="🛠️").add_signal("Flow 🌊", 1.0)
    device.begin(port)
    port.incoming += "<BLAECK.GET_DEVICES><BLAECK.WRITE_ENTITIES><BLAECK.WRITE_DATA><Note,neu 🎉>".encode()
    for _ in range(6):
        device.tick()
    frames = [f.body for f in _decode.Decoder().feed(bytes(port.outgoing))
              if isinstance(f, _decode.Frame)]
    devices = next(b for b in frames if isinstance(b, _decode.DeviceList))
    assert [d.name for d in devices.devices] == ["🌡️ Bench", "Pumpe 💧"]
    assert devices.devices[0].hw_version == "Pi 🥧" and devices.devices[1].hw_version == "🛠️"
    assert [(s.name, s.unit) for s in devices.signals][0] == ("Temperatur 🌡️", "°C 🔥")
    entities = next(b for b in frames if isinstance(b, _decode.EntityList))
    assert entities.properties[0].options == ["Aus 💤", "An ⚡"]
    assert entities.properties[0].display_name == "Modus 🎛️"
    assert entities.events[0].name == "Alarm 🚨" and entities.events[0].types == ["heiß 🔥"]
    assert entities.buttons[0].display_name == "Neustart 🔄"
    assert next(b for b in frames if isinstance(b, _decode.Data)).values[1] == "ok ✅"
    assert note.value == "neu 🎉"
    device.close()


def test_a_list_too_long_for_one_entry_is_refused_where_it_was_written():
    many = [f"option {'x' * 300} {i}" for i in range(250)]
    with pytest.raises(ValueError, match="too long"):
        Blaeck().add_select("Mode", 0, options=many)
    with pytest.raises(ValueError, match="too long"):
        Blaeck().add_sensor("Phase", 0, options=many)
    device = Blaeck()
    with pytest.raises(ValueError, match="too long"):
        device.add_event("Alarms", many)
    assert device._events == []
    event = device.add_event("Alarms", many[:100])
    with pytest.raises(ValueError, match="too long"):
        for t in many[100:]:
            event.add_type(t)
    assert len(event._entry().fields) <= 0xFFFF
    port = Port()
    device.begin(port)
    device.tick()


@pytest.mark.parametrize("bad, error", [(1.5e15, TypeError), ("5", TypeError), (True, TypeError),
                                        (-1, ValueError), (2**64, ValueError)])
def test_a_timestamp_a_frame_cant_carry_raises_from_the_call_that_gave_it(bad, error):
    device, port = Blaeck(), Port()
    signal = device.add_signal("T", 1.0)
    pump = device.add_device("Pump")
    device.begin(port)
    for call in (lambda: device.tick(timestamp=bad), lambda: device.write_all(bad),
                 lambda: pump.write_all(bad), lambda: signal.write(timestamp=bad)):
        with pytest.raises(error):
            call()
    device.tick(timestamp=5)
    device.write_all(2**64 - 1)
    device.close()


def test_a_commands_ack_goes_before_its_function_and_what_that_sends():
    device, port, log = Blaeck(), Port(), []
    device.add_signal("Value", 1.0)

    def seen(who):
        frames = _decode.Decoder().feed(bytes(port.outgoing))
        acked = any(isinstance(f, _decode.Frame) and f.key == 0xA5 for f in frames)
        log.append((who, "after" if acked else "before"))

    def press():
        seen("press")
        device.write_all()

    def run(*params):
        seen("handler")
        device.write_all()

    device.add_button("PRESS", press)
    device.on_command("RUN", run)
    setpoint = device.add_number_input("Setpoint", 0.0)
    setpoint.on_change(lambda: seen("input"))

    @device.on_any_command
    def catch_all(command, *params):
        seen("catch-all")
        return command == "TAKEN"

    device.begin(port)
    device.tick()

    def send(command):
        port.outgoing.clear()
        log.clear()
        port.incoming += command.encode()
        device.tick()
        return [f.key for f in _decode.Decoder().feed(bytes(port.outgoing)) if isinstance(f, _decode.Frame)]

    assert send("<PRESS>")[:2] == [0xA5, 0xD3]
    assert log == [("press", "after"), ("catch-all", "after")]
    assert send("<RUN,1>")[:2] == [0xA5, 0xD3]
    assert log == [("handler", "after"), ("catch-all", "after")]
    send("<Setpoint,2>")
    assert log == [("input", "after"), ("catch-all", "after")] and setpoint.value == 2.0

    # The catch-all's answer decides the ack, so it runs first for a name nothing has.
    send("<TAKEN>")
    assert log == [("catch-all", "before")]
    assert ack_reason(device, port, "<TAKEN>") == 0
    send("<NOBODY>")
    assert log == [("catch-all", "before")]
    assert ack_reason(device, port, "<NOBODY>") == 1
    send("<Setpoint,abc>")
    assert log == [("catch-all", "after")]
    device.close()


def test_an_interval_started_before_data_doesnt_run_on_before_write_until_data_can_go_out():
    device, port, calls = Blaeck(), Port(), []
    device.add_signal("Value", 1.0)
    device.on_before_write(lambda: calls.append(1))
    device.begin(port)
    port.incoming += b"<BLAECK.INTERVAL_START,1000>"
    for _ in range(50):
        device.tick()
    assert calls == []

    port.outgoing.clear()
    port.incoming += b"<BLAECK.DATA_START>"
    device.tick()
    device.tick()
    assert calls == [1]
    frames = _decode.Decoder().feed(bytes(port.outgoing))
    assert any(isinstance(f, _decode.Frame) and f.key == 0xD3 for f in frames)
    device.close()
