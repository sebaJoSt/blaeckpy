"""The rules of blaeckpy's API that the golden scenarios cannot show: what a script may declare."""

import pytest

from blaeckpy import ANY_CHANGE, Blaeck, OnChange


class Port:
    in_waiting = 0

    def read(self, n):
        return b""

    def write(self, data):
        return len(data)


@pytest.mark.parametrize(
    "args, type_, value",
    [((float,), float, 0.0), ((float, 21.5), float, 21.5), ((21.5,), float, 21.5),
     ((0,), int, 0), ((True,), bool, True), (("Idle",), str, "Idle"), ((int, 7), int, 7)],
)
def test_a_signal_takes_its_type_from_a_type_or_its_starting_value(args, type_, value):
    signal = Blaeck().add_signal("S", *args)
    assert signal.type is type_ and signal.value == value and type(signal.value) is type_


@pytest.mark.parametrize(
    "type_, bad",
    [(int, 21.0), (int, True), (bool, 0), (bool, 1), (str, 5), (float, "1"), (float, False)],
)
def test_value_refuses_what_its_type_cannot_hold(type_, bad):
    signal = Blaeck().add_signal("S", type_)
    with pytest.raises(TypeError):
        signal.value = bad


def test_an_int_widens_to_a_float():
    signal = Blaeck().add_signal("S", float)
    signal.value = 21
    assert signal.value == 21.0 and type(signal.value) is float


@pytest.mark.parametrize("bad", [2**63, -(2**63) - 1])
def test_an_int_must_fit_64_bits(bad):
    with pytest.raises(ValueError):
        Blaeck().add_signal("S", int).value = bad


def test_a_text_holds_at_most_255_bytes():
    signal = Blaeck().add_signal("S", str)
    signal.value = "é" * 127 + "x"  # 255 bytes
    with pytest.raises(ValueError):
        signal.value = "é" * 128


@pytest.mark.parametrize("name", ["", "Tab\there", "Bell\x07"])
def test_a_signal_name_is_not_empty_and_has_no_control_characters(name):
    with pytest.raises(ValueError):
        Blaeck().add_signal(name, float)


def test_a_slash_is_allowed_in_a_signal_name():
    Blaeck().add_signal("Zone A/Flow", float)


def test_two_signals_cannot_share_a_name():
    device = Blaeck()
    device.add_signal("S", float)
    with pytest.raises(ValueError):
        device.add_signal("S", int)


def test_begin_takes_an_opened_port_once():
    device = Blaeck()
    with pytest.raises(TypeError):
        device.begin("/dev/ttyUSB0")
    device.begin(Port())
    with pytest.raises(RuntimeError):
        device.begin(Port())


def test_policies_are_checked():
    with pytest.raises(TypeError):
        Blaeck().add_signal("S", float, write_at_interval="always")
    Blaeck().add_signal("S", float, write_at_interval=OnChange(0.5), write_on_change=ANY_CHANGE)


def test_callbacks_work_as_decorator_and_as_call():
    device = Blaeck()

    @device.on_data_start
    def started():
        pass

    assert started is not None
    assert device.on_data_stop(print) is print


def test_a_signal_brings_its_unit():
    assert Blaeck().add_signal("T", float, unit="°C").unit == "°C"
    assert Blaeck().add_signal("T", float).unit == ""
    with pytest.raises(ValueError):
        Blaeck().add_signal("T", float, unit="°\nC")


@pytest.mark.parametrize("delta", [-1, float("nan"), float("inf")])
def test_a_change_threshold_is_0_or_above_and_finite(delta):
    with pytest.raises(ValueError):
        Blaeck().add_signal("T", int, write_on_change=delta)
    with pytest.raises(ValueError):
        OnChange(delta)
    with pytest.raises(ValueError):
        Blaeck().add_number_input("N", 0.0, write_on_change=delta)
    with pytest.raises(ValueError):
        Blaeck().add_sensor("S", 0.0, write_on_change=delta)


@pytest.mark.parametrize(
    "raw, params",
    [(b"Label, Hallway %20", ["Hallway"]), (b"Label,%09a b%0D%0A", ["a b"]), (b"X, ,y", ["", "y"]),
     (b"X,a%2Cb", ["a,b"]), (b"X,a%00b", ["a%00b"])],
)
def test_parameters_are_decoded_then_trimmed(raw, params):
    from blaeckpy._commands import parse
    assert parse(raw, False).params == params


def test_the_receiver_keeps_commands_in_order_however_they_arrive():
    from blaeckpy._commands import Receiver
    receiver = Receiver()
    # A flood at once, with noise, an abandoned command and one split across two feeds.
    receiver.feed(b"noise" + b"".join(b"<C,%d>" % i for i in range(20000)) + b"<lost<A,")
    receiver.feed(b"1>tail")
    taken = []
    while (command := receiver.next_command()) is not None:
        taken.append(command[0])
    assert taken == [b"C,%d" % i for i in range(20000)] + [b"A,1"]
    assert not receiver.has_command()
    receiver.feed(b"<B>")
    assert receiver.has_command() and receiver.next_command() == (b"B", False)


@pytest.mark.parametrize("mode", ["none", "micros", "unix"])
def test_timestamp_modes_take_enums_or_strings(mode):
    from blaeckpy import TimestampMode
    device = Blaeck()
    device.set_timestamp_mode(mode)
    device.set_timestamp_mode(TimestampMode(mode), clock=lambda: 5)


def test_an_unknown_timestamp_mode_is_refused():
    with pytest.raises(ValueError):
        Blaeck().set_timestamp_mode("rtc")
    with pytest.raises(TypeError):
        Blaeck().set_timestamp_mode("unix", clock=5)
