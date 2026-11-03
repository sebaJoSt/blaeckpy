"""Rules for inputs, sensors, events, buttons and commands that the golden scenarios cannot show."""

import logging

import pytest

from blaeckpy import Blaeck, Category, Mode


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


def test_an_event_writes_only_its_own_types():
    event = Blaeck().add_event("Alarms", ["Overheat"])
    with pytest.raises(ValueError):
        event.write("Dry run")
    with pytest.raises(ValueError):
        Blaeck().add_event("Alarms", ["a", "a"])
