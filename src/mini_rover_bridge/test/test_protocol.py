import pytest

from mini_rover_bridge.protocol import MotorRecord, StreamDecoder, parse_record

VALID = b'MOTOR1,100,11,22,33,44,180,-45,1'
EXPECTED = MotorRecord(100, 11, 22, 33, 44, 180, -45, True)


def test_named_fields_and_crlf():
    assert parse_record(VALID) == EXPECTED
    assert parse_record(VALID + b'\r') == EXPECTED


def test_every_possible_split_boundary():
    packet = VALID + b'\r\n'
    for index in range(len(packet)):
        decoder = StreamDecoder()
        assert decoder.feed(packet[:index]) == []
        assert decoder.feed(packet[index:]) == [EXPECTED]


def test_debug_and_multiple_records():
    decoder = StreamDecoder()
    data = b'Booting\n[MODE] Teleop\n\n' + VALID + b'\n' + VALID + b'\n'
    assert decoder.feed(data) == [EXPECTED, EXPECTED]
    assert decoder.invalid_lines == 0


@pytest.mark.parametrize('line', [
    b'MOTOR1',
    VALID + b',extra',
    b'MOTOR1,100,11,22,33,44,180,-45',
    b'MOTOR1,100,11,22,33,44,180,-45,2',
    b'MOTOR1,100,11,22,33,44,180,-45,-0',
    b'MOTOR1,100,-1,22,33,44,180,-45,1',
    b'MOTOR1,4294967296,11,22,33,44,180,-45,1',
    b'MOTOR1,100,11,22,33,44,2147483648,-45,1',
    b'MOTOR1,100,11,22,33,44,180,-2147483649,1',
    b'MOTOR1,100,11,22,33,44,18.0,-45,1',
    b'MOTOR1,100,11,22,33,44, 180,-45,1',
    b'MOTOR1,100,11,22,33,44,1_80,-45,1',
    b'MOTOR1,100,11,22,33,44,\xff,-45,1',
])
def test_invalid_record_does_not_poison_next_line(line):
    decoder = StreamDecoder()
    assert decoder.feed(line + b'\n' + VALID + b'\n') == [EXPECTED]
    assert decoder.invalid_lines == 1


def test_oversized_line_discards_through_newline():
    decoder = StreamDecoder()
    for _ in range(100):
        assert decoder.feed(b'x' * 1024) == []
        assert len(decoder._buffer) <= 256
    # A valid-looking suffix on the same oversized line must not be accepted.
    assert decoder.feed(VALID + b'\n' + VALID + b'\n') == [EXPECTED]
    assert decoder.oversized_lines == 1


def test_reset_clears_partial_and_discard_state():
    decoder = StreamDecoder()
    decoder.feed(VALID[:15])
    decoder.reset()
    assert decoder.feed(VALID[15:] + b'\n') == []
    decoder.feed(b'x' * 1000)
    decoder.reset()
    assert decoder.feed(VALID + b'\n') == [EXPECTED]


def test_counter_wrap_and_reboot_are_raw_values():
    decoder = StreamDecoder()
    maximum = b'MOTOR1,4294967295,4294967295,4294967295,4294967295,4294967295,-2147483648,2147483647,0\n'
    reset = b'MOTOR1,0,0,0,0,0,0,0,0\n'
    records = decoder.feed(maximum + reset)
    assert records[0].fl_count == 2**32 - 1
    assert records[0].throttle == -(2**31)
    assert records[1] == MotorRecord(0, 0, 0, 0, 0, 0, 0, False)
