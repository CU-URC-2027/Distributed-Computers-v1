"""Bounded streaming decoder; deliberately independent of ROS and serial I/O."""

from dataclasses import dataclass
import re

UINT32_MAX = 2**32 - 1
INT32_MIN = -(2**31)
INT32_MAX = 2**31 - 1
MAX_LINE_BYTES = 256
INTEGER = re.compile(rb'-?[0-9]+')


@dataclass(frozen=True, slots=True)
class MotorRecord:
    uptime_ms: int
    fl_count: int
    rl_count: int
    fr_count: int
    rr_count: int
    throttle: int
    steering: int
    driver_enabled: bool


def parse_record(line: bytes) -> MotorRecord | None:
    """Ignore debug text; raise ValueError for malformed MOTOR1 records."""
    if not line.startswith(b'MOTOR1'):
        return None
    fields = line.removesuffix(b'\r').split(b',')
    if len(fields) != 9 or fields[0] != b'MOTOR1':
        raise ValueError('expected MOTOR1 followed by eight fields')
    if any(INTEGER.fullmatch(field) is None for field in fields[1:]):
        raise ValueError('fields must contain decimal integers')
    values = [int(field) for field in fields[1:]]
    if any(not 0 <= value <= UINT32_MAX for value in values[:5]):
        raise ValueError('uptime and counts must fit uint32')
    if any(not INT32_MIN <= value <= INT32_MAX for value in values[5:7]):
        raise ValueError('commands must fit int32')
    if fields[8] not in (b'0', b'1'):
        raise ValueError('enabled must be 0 or 1')
    return MotorRecord(*values[:7], bool(values[7]))


class StreamDecoder:
    """Retain at most MAX_LINE_BYTES, discarding oversized lines through LF."""

    def __init__(self):
        self.invalid_lines = 0
        self.oversized_lines = 0
        self.reset()

    def reset(self):
        self._buffer = bytearray()
        self._discarding = False

    def feed(self, data: bytes) -> list[MotorRecord]:
        records = []
        for byte in data:
            if byte == 10:
                if not self._discarding:
                    try:
                        record = parse_record(bytes(self._buffer))
                    except ValueError:
                        self.invalid_lines += 1
                    else:
                        if record is not None:
                            records.append(record)
                self.reset()
            elif not self._discarding:
                if len(self._buffer) >= MAX_LINE_BYTES:
                    self._buffer.clear()
                    self._discarding = True
                    self.oversized_lines += 1
                else:
                    self._buffer.append(byte)
        return records
