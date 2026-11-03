"""Version 2 open-loop contract. PWM is duty, never calibrated velocity."""
from dataclasses import dataclass
import math
from typing import Protocol

WHEELS = ('front_left', 'rear_left', 'front_right', 'rear_right')
SIDES = ('left', 'right')
VERSION = 2
MODE = 'OPEN_LOOP_PWM'
PWM_LIMIT = 63
INPUT_TIMEOUT = DRIVE_TIMEOUT = .250
FEEDBACK_TIMEOUT = STATE_TIMEOUT = OPERATION_TIMEOUT = .350
TOKEN_AGE = .200
TIMEOUT = INPUT_TIMEOUT  # Compatibility name for a drive/input lease only.
UINT64_MAX = 2**64 - 1
UINT32_MAX = 2**32 - 1


def zeros():
    return dict.fromkeys(SIDES, 0)


def integer(value, low=0, high=UINT64_MAX):
    return type(value) is int and low <= value <= high


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def wheels(value, limit=None):
    return (isinstance(value, dict) and set(value) == set(WHEELS)
            and all(finite(v) and (limit is None or abs(v) <= limit) for v in value.values()))


def sides(value, limit=PWM_LIMIT):
    return (isinstance(value, dict) and set(value) == set(SIDES)
            and all(integer(v, -limit, limit) for v in value.values()))


def fault(source, code, severity='ERROR', latched=False, command_id=0, time_ms=0):
    return dict(source=source, code=code, severity=severity, latched=latched,
                command_id=command_id, time_ms=time_ms & UINT32_MAX)


def inhibiting(faults):
    return any(f.get('severity') == 'ERROR' for f in faults)


@dataclass(frozen=True)
class Config:
    pwm_limit: int = PWM_LIMIT
    response_time: float = .20  # Simulator response only; no hardware speed calibration.

    def __post_init__(self):
        if not integer(self.pwm_limit, 1, PWM_LIMIT):
            raise ValueError('PWM limit must be an integer from 1 to 63')
        if not finite(self.response_time) or self.response_time <= 0:
            raise ValueError('Response time must be finite and positive')

    def validate_input(self, throttle, steering):
        if not finite(throttle) or not finite(steering):
            raise ValueError('Inputs must be finite numbers')
        if abs(throttle) > 1 or abs(steering) > 1:
            raise ValueError('Throttle and steering must be within [-1, 1]')

    def targets(self, throttle, steering):
        self.validate_input(throttle, steering)
        requested = dict(left=(throttle-steering)*self.pwm_limit,
                         right=(throttle+steering)*self.pwm_limit)
        scale = max(1., max(map(abs, requested.values()))/self.pwm_limit)
        # Symmetric round-half-away-from-zero, shared with contract test vectors.
        def rounded(v):
            return int(math.copysign(math.floor(abs(v)+.5), v))
        return requested, {k: rounded(v/scale) for k, v in requested.items()}


@dataclass(frozen=True)
class DriveIntent:
    session_id: str
    command_id: int
    stamp_ns: int = 0  # Audit only: never subtract unsynchronized host clocks.
    throttle: float = 0.0
    steering: float = 0.0
    enable_requested: bool = False
    deadman_held: bool = False
    stop_requested: bool = False
    operator_source: str = 'manual'
    protocol_version: int = VERSION
    mode: str = MODE
    oc_boot_id: str = ''
    generation: int = 0
    challenge_seq: int = 0

    def validate(self, config, wall_ns=None):
        from .protocol import identity
        if type(self.protocol_version) is not int or self.protocol_version != VERSION or self.mode != MODE:
            raise ValueError('Unsupported version or mode')
        if not identity(self.session_id) or not identity(self.oc_boot_id):
            raise ValueError('Invalid identity')
        if not all(integer(v, 1) for v in (self.command_id, self.generation, self.challenge_seq)):
            raise ValueError('Invalid sequence or generation')
        if not integer(self.stamp_ns):
            raise ValueError('Invalid audit timestamp')
        if self.operator_source != 'manual':
            raise ValueError('Only manual GUI ownership is supported')
        if any(type(v) is not bool for v in (self.enable_requested, self.deadman_held, self.stop_requested)):
            raise ValueError('Flags must be boolean')
        config.validate_input(self.throttle, self.steering)
        if not self.deadman_held and (self.throttle or self.steering):
            raise ValueError('Motion requires held input')


class ArduinoPort(Protocol):
    connected: bool
    def poll(self) -> list[dict]: ...
    def send(self, packet: dict) -> bool: ...
    def close(self) -> None: ...
