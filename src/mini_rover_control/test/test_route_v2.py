"""Deterministic cross-component tests with real OC and MCU state machines."""
from dataclasses import replace
from uuid import uuid4
import pytest
from mini_rover_control.contracts import DriveIntent
from mini_rover_control.arduino import ArduinoController
from mini_rover_control.oc import OCController


class Clock:
    def __init__(self):self.now=1.
    def __call__(self):return self.now
    def step(self,dt):self.now+=dt
    def wall(self):return int((self.now+123456789)*1e9)


class Rig:
    def __init__(self):
        self.clock=Clock();self.mcu=ArduinoController(clock=self.clock)
        self.connected=True;self.commands=[];self.drop=set();self.silence=False
        self.oc=OCController(self,clock=self.clock,wall_ns=self.clock.wall)
        self.source=uuid4().hex;self.id=0
        for _ in range(4):self.oc.tick();self.clock.step(.01)
        ok,reason,self.session=self.oc.claim(self.source)
        assert ok,reason
    def poll(self):return [self.mcu.sample()] if self.connected and not self.silence else []
    def send(self,packet):
        self.commands.append(packet)
        if packet['op'] not in self.drop:self.mcu.handle(packet)
        return self.connected
    def close(self):self.connected=False
    def intent(self,**changes):
        state=self.oc.status();self.id+=1
        return DriveIntent(session_id=self.session,command_id=self.id,stamp_ns=1,
            oc_boot_id=state['boot_id'],generation=state['generation'],
            challenge_seq=state['challenge_seq'],**changes)
    def receive(self,**changes):return self.oc.receive(self.intent(**changes))
    def operation(self,op):
        s=self.oc.status()
        return self.oc.operation(self.session,op,oc_boot_id=s['boot_id'],
                                 generation=s['generation'],challenge_seq=s['challenge_seq'])
    def enable(self):
        assert self.receive()
        ok,reason=self.operation('enable');assert ok,reason
        assert self.receive(enable_requested=True)
        self.oc.tick()
        assert self.mcu.enabled
    def drive(self,throttle=.5,steering=0):
        assert self.receive(throttle=throttle,steering=steering,enable_requested=True,deadman_held=True)
        self.oc.tick();self.oc.tick()


def test_full_route_applied_correlation_and_encoder_warning():
    r=Rig();r.enable();r.drive(.2,.4)
    assert r.mcu.applied=={'left':-13,'right':38}
    s=r.oc.status()
    assert s['last_applied_command_id']==r.id
    assert s['last_applied_seq']>0 and s['applied_session_id']==r.session
    r.mcu.missing.add('rear_right');r.clock.step(.05);r.drive()
    assert r.mcu.enabled and not r.oc.feedback['valid']['rear_right']
    assert any(f['severity']=='WARNING' for f in r.oc.status()['faults'])


def test_input_replay_or_expired_challenge_does_not_renew_permission():
    r=Rig();r.enable();r.drive()
    packet=r.intent(throttle=.5,enable_requested=True,deadman_held=True)
    assert r.oc.receive(packet)
    accepted_at=r.oc.last_intent
    r.clock.step(.1)
    assert not r.oc.receive(packet)
    assert r.oc.last_intent==accepted_at
    r.clock.step(.101)
    assert not r.oc.receive(replace(packet,command_id=packet.command_id+1))
    assert r.oc.last_intent==accepted_at
    r.clock.step(.06);r.oc.tick()
    assert not r.oc.desired_enabled and not r.oc.session_id
    assert not r.mcu.enabled


def test_same_c2_command_in_new_serial_envelope_cannot_renew_drive():
    r=Rig();r.enable();r.drive()
    original=next(p for p in reversed(r.commands) if p['op']=='TARGETS')
    before=r.mcu.last_drive
    r.clock.step(.1)
    f=r.mcu.sample()
    replay={**original,'seq':original['seq']+10,'sample_seq':f['sample_seq']}
    assert not r.mcu.handle(replay)
    assert r.mcu.last_drive==before
    r.clock.step(.151);r.mcu.tick()
    assert not r.mcu.enabled


def test_stop_preempts_unconfirmed_enable_and_reset_stays_disabled():
    r=Rig();assert r.receive()
    r.drop.add('ENABLE');assert r.operation('enable')[0]
    assert r.operation('stop')[0]
    r.oc.tick()
    assert r.mcu.stopped and not r.mcu.enabled
    assert not r.oc.desired_enabled
    assert r.receive()
    assert r.operation('reset')[0]
    r.oc.tick()
    assert not r.mcu.stopped and not r.mcu.enabled


def test_enable_ack_timeout_never_recovers_automatically():
    r=Rig();assert r.receive();r.drop.add('ENABLE')
    assert r.operation('enable')[0]
    for _ in range(8):
        r.clock.step(.05)
        if r.oc.session_id:r.receive(enable_requested=True)
        r.oc.tick()
    assert not r.oc.desired_enabled and not r.mcu.enabled
    r.drop.clear();r.oc.tick()
    assert not r.mcu.enabled
    assert r.oc.status()['first_fault_valid']


def test_invalid_applied_mapping_is_not_reported_as_confirmation():
    r=Rig();r.enable();r.drive()
    known=r.oc.status()['last_applied_command_id']
    r.mcu.last_applied_command_id=known+100
    r.oc.tick()
    assert r.oc.status()['last_applied_command_id']!=known+100
    assert not r.oc.desired_enabled


def test_encoder_and_mcu_freshness_are_independent_of_oc_publications():
    r=Rig();r.enable();r.drive();sample=r.oc.feedback['sample_seq']
    r.silence=True
    for _ in range(8):
        r.clock.step(.05)
        if r.oc.session_id:r.receive(enable_requested=True)
        r.oc.tick();r.oc.status()
    assert r.oc.feedback['sample_seq']==sample
    assert not r.oc.status()['enabled_known']
    assert not r.oc.desired_enabled


def test_reboot_revokes_old_c2_and_serial_authority():
    r=Rig();r.enable();r.drive()
    old=next(p for p in reversed(r.commands) if p['op']=='TARGETS')
    r.mcu=ArduinoController(clock=r.clock)
    assert not r.mcu.handle(old)
    r.oc.tick()
    assert not r.oc.session_id and not r.oc.desired_enabled
