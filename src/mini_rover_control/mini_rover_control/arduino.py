"""MCU v2 behavior model. Simulated pulse counts are not calibrated measurements."""
from collections import deque
import math
import time
from uuid import uuid4
from .contracts import Config, WHEELS, DRIVE_TIMEOUT, TOKEN_AGE, VERSION, MODE, UINT64_MAX, zeros, fault
from .protocol import valid_command, identity


class MotorModel:
    """Illustrative PWM-to-pulses plant; no physical speed or direction claim."""
    def __init__(self, config):
        self.config = config
        self.speeds = dict.fromkeys(WHEELS, 0.)
        self.fractions = dict.fromkeys(WHEELS, 0.)
        self.counts = dict.fromkeys(WHEELS, 0)
        self.stalled = set()

    def step(self, targets, dt):
        dt = max(0., dt)
        alpha = -math.expm1(-dt / self.config.response_time)
        for i, name in enumerate(WHEELS):
            target = 0. if name in self.stalled else abs(targets['left' if i < 2 else 'right']) * 4.
            self.speeds[name] += (target-self.speeds[name])*alpha
            total = self.fractions[name] + self.speeds[name]*dt
            pulses = int(total)
            self.fractions[name] = total-pulses
            self.counts[name] = (self.counts[name]+pulses) & 0xffffffff


class ArduinoController:
    def __init__(self, config=None, clock=time.monotonic, boot_id=None):
        self.config = config or Config()
        self.clock = clock
        self.boot_id = uuid4().hex if boot_id is None else boot_id
        self.boot_valid = identity(self.boot_id)
        # Invalid identity is never accepted for control; placeholder permits diagnostic framing.
        if not self.boot_valid:
            self.boot_id = '0'*32
        self.started = self.last_step = clock()
        self.session_id = self.source_session = self.drive_source = ''
        self.generation = 1
        self.seq = self.sample_seq = self.last_applied_command_id = self.last_applied_seq = 0
        self.drive_command_id = 0
        self.enabled = self.stopped = False
        self.last_drive = None
        self.reason = 'STARTUP' if self.boot_valid else 'BOOT_ID_UNAVAILABLE'
        self.applied = zeros()
        self.missing = set()
        self.model = MotorModel(self.config)
        self.tokens = deque(maxlen=32)

    @property
    def uptime_ms(self):
        return int((self.clock()-self.started)*1000 + 1e-6) & 0xffffffff

    @property
    def faults(self):
        if not self.boot_valid:
            code = 'COUNTER_EXHAUSTED' if self.reason == 'COUNTER_EXHAUSTED' else 'BOOT_ID_UNAVAILABLE'
            return [fault('MCU', code, latched=True)]
        suffixes = ('FL', 'RL', 'FR', 'RR')
        return [fault('MCU', 'ENCODER_UNAVAILABLE_'+s, 'WARNING', time_ms=self.uptime_ms)
                for w, s in zip(WHEELS, suffixes) if w in self.missing]

    def disable(self, reason, latch=False):
        if self.enabled or (latch and not self.stopped):
            if self.generation == UINT64_MAX:
                self.boot_valid = False
                reason = 'COUNTER_EXHAUSTED'
            else:
                self.generation += 1
        self.enabled = False
        self.stopped |= latch
        self.applied = zeros()
        self.reason = reason

    def tick(self):
        now = self.clock()
        # Match MCU integral-millisecond wrap-safe timing.
        if self.enabled and ((self.uptime_ms-self.last_drive) & 0xffffffff) >= int(DRIVE_TIMEOUT*1000):
            self.disable('COMMAND_TIMEOUT')
        self.model.step(self.applied, now-self.last_step)
        self.last_step = now

    def handle(self, p):
        self.tick()
        now_ms = self.uptime_ms
        if not self.boot_valid or not valid_command(p) or p['boot_id'] != self.boot_id:
            return False
        if not any(seq == p['sample_seq'] and ((now_ms-issued) & 0xffffffff) <= int(TOKEN_AGE*1000)
                   for seq, issued in self.tokens):
            return False
        op = p['op']
        new_bind = op == 'BIND' and p['session_id'] != self.session_id
        if op == 'BIND':
            if self.enabled or p['generation'] != self.generation:
                return False
        elif p['session_id'] != self.session_id:
            return False
        if p['seq'] <= (0 if new_bind else self.seq):
            return False
        if op not in ('BIND', 'STOP', 'DISABLE') and p['generation'] != self.generation:
            return False
        if new_bind:
            if self.generation == UINT64_MAX:
                self.boot_valid = False
                self.disable('COUNTER_EXHAUSTED')
                return False
            self.session_id, self.seq = p['session_id'], 0
            self.generation += 1
            self.source_session = self.drive_source = ''
            self.last_applied_command_id = self.last_applied_seq = self.drive_command_id = 0
        if op == 'ENABLE':
            if self.enabled or self.stopped:
                return False
            self.enabled, self.last_drive, self.reason = True, now_ms, 'ENABLED'
            self.drive_source, self.drive_command_id = '', 0
        elif op == 'TARGETS':
            if not self.enabled or self.stopped:
                return False
            if self.drive_source and (p['source_session'] != self.drive_source or p['command_id'] <= self.drive_command_id):
                return False
            self.applied = dict(p['targets'])
            self.last_drive = now_ms
            self.last_applied_command_id = self.drive_command_id = p['command_id']
            self.source_session = self.drive_source = p['source_session']
            self.last_applied_seq = p['seq']
            self.reason = 'RUNNING'
        elif op in ('STOP', 'DISABLE'):
            self.disable(op, op == 'STOP')
        elif op == 'RESET':
            if self.enabled:
                return False
            if self.generation == UINT64_MAX:
                self.boot_valid = False
                self.disable('COUNTER_EXHAUSTED')
                return False
            self.stopped = False
            self.generation += 1
            self.reason = 'RESET_DISABLED'
        self.seq = p['seq']
        return True

    def sample(self):
        self.tick()
        if self.sample_seq == UINT64_MAX:
            self.disable('COUNTER_EXHAUSTED')
            self.boot_valid = False
        else:
            self.sample_seq += 1
        self.tokens.append((self.sample_seq, self.uptime_ms))
        return dict(v=VERSION, type='feedback', mode=MODE, boot_id=self.boot_id,
                    session_id=self.session_id, generation=self.generation, sample_seq=self.sample_seq,
                    ack_seq=self.seq, last_applied_seq=self.last_applied_seq, source_session=self.source_session,
                    last_applied_command_id=self.last_applied_command_id, uptime_ms=self.uptime_ms,
                    enabled=self.enabled, stopped=self.stopped, applied=dict(self.applied),
                    counts=dict(self.model.counts), valid={k: k not in self.missing for k in WHEELS},
                    direction_valid=dict.fromkeys(WHEELS, False), faults=self.faults, reason=self.reason)
