"""Operator-owned process identity and explicit hold-to-drive state."""
import time
from uuid import uuid4
from .contracts import (Config, DriveIntent, STATE_TIMEOUT, INPUT_TIMEOUT,
                        VERSION, MODE, inhibiting)


class C2Controller:
    def __init__(self, config=None, clock=time.monotonic, wall_ns=time.time_ns):
        self.config = config or Config()
        self.clock, self.wall_ns = clock, wall_ns
        self.client_id = uuid4().hex
        self.session_id = ''
        self.source = 'manual'
        self.command_id = 0
        self.claim_ack_min = 1
        self.throttle = self.steering = 0.
        self.enable_requested = self.holding = self.stop_requested = False
        self.state = self.state_at = self.last_tick = None
        self.claim_started = self.enable_started = None
        self._boot = None
        self._state_seq = 0
        self._retired_boots = set()
        self.reason = 'Connect to OC, then enable at neutral with hold released'

    def connected(self, session):
        if session != self.client_id:
            raise ValueError('OC returned a different C2 process identity')
        self.session_id = session
        self.claim_started = self.clock()
        self.claim_ack_min = self.command_id + 1
        self.state = self.state_at = None
        self.disarm()

    def observe(self, state):
        if state.get('protocol_version') != VERSION or state.get('mode') != MODE:
            self.disarm()
            return False
        boot, seq = state['boot_id'], state['state_seq']
        if boot in self._retired_boots or (boot == self._boot and seq <= self._state_seq):
            return False
        if self._boot and boot != self._boot:
            self._retired_boots.add(self._boot)
            self.session_id = ''
            self.disarm()
        previous = self.state
        self._boot, self._state_seq = boot, seq
        if (self.session_id and state['session_id'] != self.session_id and
                self.claim_started is not None and self.clock()-self.claim_started < .25):
            return False
        self.state, self.state_at = state, self.clock()
        if self.session_id and state['session_id'] != self.session_id:
            self.session_id = ''
            self.disarm()
        if previous and previous['generation'] != state['generation']:
            self.disarm()
        if not state['enabled_known'] or state['stop_latched'] or inhibiting(state['faults']):
            self.disarm()
        elif (self.enable_requested and (not state['actual_enabled'] or not state['oc_authorized']) and
              state['controller_state'] != 'ENABLING' and
              (self.enable_started is None or self.clock()-self.enable_started >= .25)):
            self.disarm()
        return True

    def fresh(self):
        return self.state is not None and self.state_at is not None and self.clock()-self.state_at < STATE_TIMEOUT

    def enable(self):
        s = self.state
        if (not self.session_id or not self.fresh() or not s['enabled_known'] or
                s['session_id'] != self.session_id or s['last_accepted_command_id'] < self.claim_ack_min):
            raise ValueError('Connect and wait for fresh OC/MCU feedback and neutral acknowledgment')
        if s['stop_latched'] or inhibiting(s['faults']) or self.throttle or self.steering or self.holding:
            raise ValueError('Reset STOP, clear errors, return input to zero and release hold')
        self.enable_requested = True
        self.enable_started = self.clock()
        self.reason = 'Enable requested; waiting for MCU confirmation'

    def apply(self, throttle, steering):
        self.config.validate_input(throttle, steering)
        if not self.enable_requested or not self.fresh() or not self.state['actual_enabled']:
            raise ValueError('Enable and wait for confirmed MCU enable first')
        self.throttle, self.steering = float(throttle), float(steering)
        self.reason = 'Values selected; motion requires holding the drive control'

    def begin_hold(self):
        if (not self.enable_requested or not self.fresh() or not self.state['actual_enabled']
                or not self.state['oc_authorized']):
            raise ValueError('Wait for OC authorization and confirmed MCU enable')
        self.holding = True

    def release(self):
        self.throttle = self.steering = 0.
        self.holding = False

    def disarm(self):
        self.release()
        self.enable_requested = False
        self.enable_started = None

    def stop(self):
        self.disarm()
        self.stop_requested = True

    def intent(self):
        now = self.clock()
        if (self.last_tick is not None and now-self.last_tick >= INPUT_TIMEOUT) or not self.fresh():
            self.disarm()
        self.last_tick = now
        if not self.session_id or not self.state:
            return None
        self.command_id += 1
        return DriveIntent(session_id=self.session_id, command_id=self.command_id,
            stamp_ns=self.wall_ns(), throttle=self.throttle if self.holding else 0.,
            steering=self.steering if self.holding else 0., enable_requested=self.enable_requested,
            deadman_held=self.holding, stop_requested=self.stop_requested, operator_source=self.source,
            oc_boot_id=self.state['boot_id'], generation=self.state['generation'],
            challenge_seq=self.state['challenge_seq'])
