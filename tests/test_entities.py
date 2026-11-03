"""Rules for inputs, sensors, events, buttons and commands that the golden scenarios cannot show."""

import logging

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


def test_close_lets_begin_follow_and_stops_what_the_host_started():
    device, stopped = Blaeck(), []
    device.add_signal("T", 1.0)
    device.on_data_stop(lambda: stopped.append("data"))
    device.on_entities_stop(lambda: stopped.append("entities"))
    port = Port()
    device.begin(port)
    ack_reason(device, port, "<BLAECK.DATA_START>")
    ack_reason(device, port, "<BLAECK.ENTITIES_START>")
    device.close()
    assert stopped == ["data", "entities"]
    reopened = Port()
    device.begin(reopened)
    assert ack_reason(device, reopened, "<BLAECK.GET_DEVICES>") == 0
    with pytest.raises(RuntimeError):
        device.begin(Port())


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
