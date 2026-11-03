"""OC-specific authority, recovery, and acknowledgment tests (no hardware)."""
from dataclasses import replace
from uuid import uuid4
from mini_rover_control.arduino import ArduinoController
from mini_rover_control.c2 import C2Controller
from mini_rover_control.contracts import DriveIntent
from mini_rover_control.oc import OCController


class Clock:
    def __init__(self): self.now = 1.
    def __call__(self): return self.now
    def step(self, amount): self.now += amount


class Harness:
    def __init__(self):
        self.clock = Clock()
        self.mcu = ArduinoController(clock=self.clock)
        self.connected = True
        self.fail_send = self.drop_enable = False
        self.inject = []
        self.sent = []
        self.oc = OCController(self, clock=self.clock)
        self.c2 = C2Controller(clock=self.clock)
        for _ in range(3): self.oc.tick(); self.clock.step(.01)
        self.claim()

    def poll(self):
        if self.inject:
            injected, self.inject = self.inject, []
            return injected
        return [self.mcu.sample()]

    def send(self, p):
        self.sent.append(p)
        if self.fail_send: return False
        if not (self.drop_enable and p['op'] == 'ENABLE'):
            self.mcu.handle(p)
        return True

    def claim(self):
        ok, reason, session = self.oc.claim(self.c2.client_id)
        assert ok, reason
        self.c2.connected(session)
        self.pump()

    def pump(self):
        self.c2.observe(self.oc.status())
        intent = self.c2.intent()
        if intent: self.oc.receive(intent)
        self.oc.tick()
        self.c2.observe(self.oc.status())

    def operation(self, op):
        s = self.oc.status()
        return self.oc.operation(self.c2.session_id, op, oc_boot_id=s['boot_id'],
                                 generation=s['generation'], challenge_seq=s['challenge_seq'])

    def enable(self):
        self.c2.enable()
        ok, reason = self.operation('enable')
        assert ok, reason
        self.pump()
        assert self.mcu.enabled


def test_enable_timeout_recovers_only_after_explicit_claim_and_enable():
    h = Harness(); h.drop_enable = True
    h.c2.enable(); assert h.operation('enable')[0]
    for _ in range(8): h.clock.step(.05); h.pump()
    assert not h.oc.session_id and not h.mcu.enabled
    assert h.oc.status()['first_fault']['code'] == 'ENABLE_NOT_CONFIRMED'
    h.drop_enable = False
    h.claim()
    assert not h.mcu.enabled
    h.enable()
    assert h.oc.status()['first_fault_valid']
    assert not any(f['severity'] == 'ERROR' for f in h.oc.status()['faults'])


def test_send_failure_does_not_authorize_enable():
    h = Harness(); h.fail_send = True
    assert not h.operation('enable')[0]
    assert not h.oc.desired_enabled and h.oc.pending_operation is None
    assert not h.mcu.enabled


def test_lower_ack_with_new_sample_cannot_renew_feedback_or_application():
    h = Harness(); h.enable()
    f = h.mcu.sample()
    f['ack_seq'] = 0
    f['last_applied_seq'] = f['last_applied_command_id'] = 0
    f['source_session'] = ''
    at = h.oc.feedback_at
    h.clock.step(.01); h.inject = [f]; h.oc.tick()
    assert h.oc.feedback_at == at
    assert not h.oc.desired_enabled
    assert h.oc.status()['first_fault']['code'] == 'ACK_MISMATCH'


def test_retired_boot_cannot_replace_current_mcu():
    h = Harness(); old = h.mcu.sample()
    h.mcu = ArduinoController(clock=h.clock)
    for _ in range(4): h.clock.step(.05); h.oc.tick()
    current = h.oc.feedback['boot_id']
    h.inject = [old]; h.oc.tick()
    assert h.oc.feedback['boot_id'] == current
    assert not h.oc.desired_enabled


def test_reclaim_keeps_source_command_highwater():
    h = Harness()
    last = h.oc.last_accepted
    h.clock.step(.251); h.oc.tick()
    h.c2.observe(h.oc.status())
    h.claim()
    assert h.oc.last_accepted > last
    packet = h.c2.intent()
    assert not h.oc.receive(replace(packet, command_id=last))


def test_successful_enable_does_not_rotate_oc_generation():
    h = Harness(); generation = h.oc.generation
    h.enable()
    assert h.oc.generation == generation
    h.c2.apply(.3, -.2); h.c2.begin_hold(); h.pump(); h.pump()
    assert h.mcu.applied == {'left': 32, 'right': 6}


def test_disabled_feedback_retains_valid_historical_application():
    h = Harness(); h.enable()
    h.c2.apply(.5, 0); h.c2.begin_hold(); h.pump(); h.pump(); h.oc.tick()
    applied = h.oc.applied_id
    h.c2.disarm(); assert h.operation('disable')[0]
    h.oc.tick()
    assert not h.oc.feedback['enabled'] and h.oc.feedback['applied'] == {'left': 0, 'right': 0}
    assert h.oc.applied_id == applied
    assert not h.oc.current_faults


def test_prior_disabled_ack_cannot_confirm_cancellation_of_later_enable():
    h = Harness()
    assert h.operation('disable')[0]; h.oc.tick()
    old_disabled = h.mcu.sample()
    h.pump(); h.c2.enable(); assert h.operation('enable')[0]
    h.drop_enable = True
    # Cancel ENABLE while withholding all newer MCU feedback.
    h.inject = [dict(old_disabled, sample_seq=h.mcu.sample_seq + 1)]
    h.oc._disable('DISARMED')
    h.oc.tick()
    assert h.oc.disable_pending
    assert not h.oc.desired_enabled
