"""Onboard v2 supervisor; local monotonic leases and MCU-confirmed outputs."""
import time
from collections import deque, OrderedDict
from uuid import uuid4
from .contracts import (Config, VERSION, MODE, INPUT_TIMEOUT, FEEDBACK_TIMEOUT,
                        OPERATION_TIMEOUT, TOKEN_AGE, zeros, fault, inhibiting)
from .protocol import valid_feedback, identity


class OCController:
    def __init__(self, port, config=None, clock=time.monotonic, wall_ns=time.time_ns):
        self.port, self.config = port, config or Config()
        self.clock, self.wall_ns = clock, wall_ns
        self.started = clock()
        self.boot_id, self.serial_session = uuid4().hex, uuid4().hex
        self.generation = 1
        self.state_seq = self.challenge_seq = 0
        self.challenges = deque(maxlen=64)
        self.session_id = self.owner = self.active_source = ''
        self.last_intent = self.feedback_at = None
        self.intent = self.feedback = None
        self.last_accepted = self.forwarded = self.seq = self.last_forwarded_seq = 0
        self.desired_enabled = False
        self.pending_enable = None
        self.pending_operation = None
        self.requested, self.limited = zeros(), zeros()
        self.reason = 'STARTUP'
        self.last_send = -float('inf')
        self.feedback_stamp_ns = 0
        self.accepted_feedback = deque(maxlen=256)
        self.sent = OrderedDict()
        self.highwater = OrderedDict()
        self.applied_id = self.applied_seq = 0
        self.applied_session = ''
        self.current_faults = []
        self.first_fault = None
        self.first_fault_session = ''
        self.disable_pending = False
        self.disable_seq = 0
        self.retired_boots = set()

    def fresh(self):
        return (self.port.connected and self.feedback_at is not None
                and self.clock() - self.feedback_at < FEEDBACK_TIMEOUT)

    def bound(self):
        return bool(self.fresh() and self.feedback['session_id'] == self.serial_session)

    def _fault(self, code):
        f = fault('OC', code, command_id=self.last_accepted,
                  time_ms=int((self.clock()-self.started)*1000))
        self.current_faults = [f]
        self._remember_fault(f)

    def _remember_fault(self, f):
        # Preserve the first inhibiting event; ordinary encoder warnings cannot hide it.
        if f['severity'] == 'ERROR' and self.first_fault is None:
            self.first_fault, self.first_fault_session = dict(f), self.session_id

    def _advance_generation(self):
        self.generation += 1
        self.challenges.clear()

    def _authority(self, oc_boot_id, generation, challenge_seq):
        now = self.clock()
        return (oc_boot_id == self.boot_id and generation == self.generation
                and any(seq == challenge_seq and 0 <= now-at <= TOKEN_AGE
                        for seq, at in self.challenges))

    def _send(self, op, targets=None):
        if not self.fresh():
            return 0
        self.seq += 1
        f = self.feedback
        packet = dict(v=VERSION, type='command', mode=MODE, op=op, boot_id=f['boot_id'],
                      session_id=self.serial_session, generation=f['generation'],
                      sample_seq=f['sample_seq'], seq=self.seq,
                      source_session=self.session_id,
                      command_id=self.last_accepted if op == 'TARGETS' else 0,
                      targets=targets if targets is not None else zeros())
        self.last_send = self.clock()
        if not self.port.send(packet):
            return 0
        self.sent[self.seq] = (op, self.session_id, packet['command_id'])
        if op in ('STOP', 'DISABLE'):
            self.disable_seq = self.seq
        if len(self.sent) > 2048:
            # Keep the last confirmed target mapping even during long disabled periods.
            for key in list(self.sent):
                if key != self.applied_seq:
                    del self.sent[key]
                    break
        return self.seq

    def _disable(self, reason, invalidate=False, force=False):
        active = self.desired_enabled or self.pending_operation is not None
        if active or force or (invalidate and self.session_id):
            self._advance_generation()
        self.desired_enabled = False
        self.pending_enable = self.pending_operation = None
        self.requested, self.limited = zeros(), zeros()
        self.reason = reason
        # A queued ENABLE may take effect after currently cached disabled feedback.
        if active or (self.bound() and self.feedback['enabled']):
            self.disable_pending = True
        if self.disable_pending and self.bound() and (active or self.clock()-self.last_send >= .05):
            self._send('DISABLE')
        if invalidate:
            self.session_id = self.owner = self.active_source = ''
            self.intent = None
            self.last_intent = None

    def claim(self, owner, operator_source='manual'):
        self.tick()
        if not identity(owner) or operator_source != 'manual':
            return False, 'Invalid identity or input source', ''
        if not self.bound() or self.disable_pending:
            return False, 'Wait for confirmed disabled Arduino link', ''
        if self.session_id:
            if self.owner == owner and self.active_source == operator_source:
                return True, 'Existing ownership', self.session_id
            return False, 'Already owned', ''
        if self.feedback['enabled']:
            return False, 'Disable first', ''
        self.session_id = self.owner = owner
        self.active_source = operator_source
        self.last_accepted = self.highwater.get(owner, 0)
        self.forwarded = self.last_accepted
        self.last_intent = self.clock()
        self.intent = None
        self.current_faults = []
        self.reason = 'CLAIMED_DISABLED'
        return True, self.reason, self.session_id

    def receive(self, intent):
        self.tick()
        try:
            intent.validate(self.config)
        except (ValueError, TypeError):
            return False
        if (not self.session_id or intent.session_id != self.session_id
                or intent.operator_source != self.active_source
                or intent.command_id <= self.last_accepted
                or not self._authority(intent.oc_boot_id, intent.generation, intent.challenge_seq)):
            return False
        self.intent, self.last_intent = intent, self.clock()
        self.last_accepted = intent.command_id
        self.highwater[self.session_id] = intent.command_id
        self.highwater.move_to_end(self.session_id)
        if len(self.highwater) > 128:
            self.highwater.popitem(last=False)
        if intent.stop_requested:
            self._disable('STOP_REQUESTED', force=True)
            self._send('STOP')
        elif not intent.enable_requested:
            # A neutral disabled stream does not continuously rotate the authority epoch.
            if self.desired_enabled or self.pending_operation is not None:
                self._disable('DISARMED')
        elif self.desired_enabled:
            self.requested, self.limited = self.config.targets(intent.throttle, intent.steering)
        return True

    def operation(self, session, op, *, oc_boot_id='', generation=0, challenge_seq=0):
        self.tick()
        if (not session or session != self.session_id or not self.bound()
                or not self._authority(oc_boot_id, generation, challenge_seq)):
            return False, 'Claim ownership and supply fresh OC authority'
        f = self.feedback
        if op in ('disable', 'stop'):
            self._disable('STOP_REQUESTED' if op == 'stop' else 'DISARMED', force=True)
            sent = self._send('STOP' if op == 'stop' else 'DISABLE')
            self.disable_pending = True
            return bool(sent), self.reason if sent else 'Serial send failed'
        if self.pending_operation is not None:
            return False, 'Operation awaiting MCU confirmation'
        neutral = bool(self.intent and self.last_intent is not None
                       and self.clock()-self.last_intent < INPUT_TIMEOUT
                       and not self.intent.throttle and not self.intent.steering
                       and not self.intent.deadman_held)
        if op == 'enable':
            if self.desired_enabled:
                return True, 'Already enabled'
            if (self.disable_pending or f['enabled'] or f['stopped'] or inhibiting(f['faults'])
                    or not neutral or self.intent.stop_requested):
                return False, 'Fresh neutral released input, healthy feedback and reset STOP required'
            self.current_faults = []
            seq = self._send('ENABLE')
            if not seq:
                self._fault('SERIAL_SEND_FAILED')
                self.disable_pending = True
                self._disable('SERIAL_SEND_FAILED', invalidate=True, force=True)
                return False, 'Serial send failed'
            self.desired_enabled = True
            self.pending_enable = self.clock()
            self.pending_operation = dict(op=op, seq=seq, at=self.clock())
            self.reason = 'ENABLING'
        elif op == 'reset':
            if (self.disable_pending or f['enabled'] or inhibiting(f['faults']) or not neutral
                    or self.intent.stop_requested or self.intent.enable_requested):
                return False, 'Disable and provide fresh neutral non-STOP input before reset'
            seq = self._send('RESET')
            if not seq:
                return False, 'Serial send failed'
            self.pending_operation = dict(op=op, seq=seq, at=self.clock())
            self.reason = 'RESET_PENDING'
        else:
            return False, 'Unknown operation'
        return True, self.reason

    def _correlate(self, f):
        if f['session_id'] != self.serial_session:
            return True  # Unbound telemetry may advertise the previous owner's identities.
        if f['ack_seq'] and f['ack_seq'] not in self.sent:
            return False
        old = self.feedback
        if (old and old['boot_id'] == f['boot_id'] and old['session_id'] == f['session_id']
                and f['ack_seq'] < old['ack_seq']):
            return False
        applied = f['last_applied_seq']
        if applied < self.applied_seq:
            return False
        if applied:
            expected = self.sent.get(applied)
            if expected != ('TARGETS', f['source_session'], f['last_applied_command_id']):
                return False
        self.applied_seq, self.applied_id = applied, f['last_applied_command_id']
        self.applied_session = f['source_session']
        return True

    def tick(self):
        now = self.clock()
        for f in self.port.poll():
            if not valid_feedback(f):
                continue
            if f['boot_id'] in self.retired_boots:
                continue
            old = self.feedback
            if old and f['boot_id'] == old['boot_id'] and f['sample_seq'] <= old['sample_seq']:
                continue
            if old and f['boot_id'] != old['boot_id']:
                self.retired_boots.add(old['boot_id'])
                self._fault('MCU_RESTART')
                self._disable('MCU_RESTART', invalidate=True, force=True)
                self.serial_session = uuid4().hex
                self.sent.clear()
                self.applied_id = self.applied_seq = 0
                self.applied_session = ''
                self.disable_pending = False
                self.disable_seq = 0
            if not self._correlate(f):
                self._fault('ACK_MISMATCH')
                self._disable('ACK_MISMATCH', invalidate=True)
                continue
            self.feedback, self.feedback_at = f, now
            self.feedback_stamp_ns = self.wall_ns()
            self.accepted_feedback.append((self.feedback_stamp_ns, f))
            for item in f['faults']:
                self._remember_fault(item)
            if not f['enabled'] and f['session_id'] == self.serial_session:
                # Require an ACK for the disabling packet, not a pre-enable disabled sample.
                ack = self.sent.get(f['ack_seq'])
                if ack and ack[0] in ('DISABLE', 'STOP', 'BIND') and f['ack_seq'] >= self.disable_seq:
                    self.disable_pending = False
        if not self.fresh():
            reason = 'MCU_LINK_STALE' if self.port.connected else 'MCU_DISCONNECTED'
            if self.session_id or self.desired_enabled:
                self._fault(reason)
            self._disable(reason, invalidate=True)
            return
        if not self.bound():
            self._disable('BINDING', invalidate=True)
            if now-self.last_send >= .1:
                self._send('BIND')
            return
        f = self.feedback
        if self.reason == 'BINDING':
            self.reason = 'READY_DISABLED'
        if self.session_id and self.last_intent is not None and now-self.last_intent >= INPUT_TIMEOUT:
            self._fault('C2_INPUT_TIMEOUT')
            self._disable('C2_INPUT_TIMEOUT', invalidate=True)
        if inhibiting(f['faults']):
            self._disable('MCU_FAULT')
        pending = self.pending_operation
        if pending:
            confirmed = f['ack_seq'] >= pending['seq']
            if confirmed and ((pending['op'] == 'enable' and f['enabled'])
                              or (pending['op'] == 'reset' and not f['enabled'] and not f['stopped'])):
                self.pending_operation = self.pending_enable = None
                self.reason = 'ENABLED' if pending['op'] == 'enable' else 'RESET_DISABLED'
            elif now-pending['at'] >= OPERATION_TIMEOUT:
                self._fault('ENABLE_NOT_CONFIRMED' if pending['op'] == 'enable' else 'RESET_NOT_CONFIRMED')
                self._disable(self.current_faults[0]['code'], invalidate=True, force=True)
        if self.desired_enabled and self.pending_operation is None:
            if not f['enabled']:
                self._fault('MCU_DISABLED')
                self._disable('MCU_DISABLED', invalidate=True)
            elif self.intent and self.last_accepted > self.forwarded:
                seq = self._send('TARGETS', self.limited)
                if seq:
                    self.forwarded, self.last_forwarded_seq = self.last_accepted, seq
                    self.reason = 'ENABLED'
        elif not self.desired_enabled and (f['enabled'] or self.disable_pending) and now-self.last_send >= .05:
            self.disable_pending = True
            self._send('DISABLE')

    def status(self):
        now = self.clock()
        self.state_seq += 1
        self.challenge_seq += 1
        self.challenges.append((self.challenge_seq, now))
        f, fresh = self.feedback, self.fresh()
        known = bool(self.bound())
        enabled = bool(f and known and f['enabled'])
        faults = list(f['faults']) if f and fresh else []
        faults.extend(self.current_faults)
        age = now-self.feedback_at if self.feedback_at is not None else -1.
        input_age = now-self.last_intent if self.last_intent is not None else -1.
        limited = max(map(abs, self.requested.values())) > self.config.pwm_limit
        return dict(protocol_version=VERSION, mode=MODE, boot_id=self.boot_id,
                    state_seq=self.state_seq, challenge_seq=self.challenge_seq, generation=self.generation,
                    session_id=self.session_id, oc_authorized=self.desired_enabled,
                    actual_enabled=enabled, enabled_known=known, stop_latched=bool(f and f['stopped']),
                    controller_state='ENABLING' if self.pending_enable is not None else
                    'ENABLED' if enabled else 'DISABLED' if known else 'UNKNOWN',
                    reason=self.reason, active_source=self.active_source,
                    last_accepted_command_id=self.last_accepted, last_forwarded_seq=self.last_forwarded_seq,
                    last_applied_command_id=self.applied_id, last_applied_seq=self.applied_seq,
                    applied_session_id=self.applied_session, arduino_connected=bool(self.port.connected),
                    arduino_boot_id=f['boot_id'] if f else '', feedback_age_ms=age*1000, input_age_ms=input_age*1000,
                    requested_left_pwm=float(self.requested['left']), requested_right_pwm=float(self.requested['right']),
                    limited_left_pwm=self.limited['left'], limited_right_pwm=self.limited['right'],
                    target_limited=limited, limit_reason='PWM_LIMIT' if limited else '', faults=faults,
                    first_fault_valid=self.first_fault is not None,
                    first_fault=self.first_fault or fault('OC', 'NONE', severity='WARNING'),
                    first_fault_session_id=self.first_fault_session)
