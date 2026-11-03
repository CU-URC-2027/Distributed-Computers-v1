"""Operator intent tests independent of ROS, widgets, and physical devices."""
import pytest
from mini_rover_control.c2 import C2Controller
from mini_rover_control.contracts import fault


class Clock:
    def __init__(self): self.now = 1.
    def __call__(self): return self.now
    def step(self, dt): self.now += dt


@pytest.fixture
def c2():
    clock = Clock()
    c = C2Controller(clock=clock, wall_ns=lambda: 0)
    c.connected(c.client_id)
    c.observe(state(c))
    return c, clock


def state(c, **changes):
    value = dict(protocol_version=2, mode='OPEN_LOOP_PWM', boot_id='a'*32,
        state_seq=1, challenge_seq=1, generation=1, session_id=c.client_id,
        enabled_known=True, actual_enabled=False, oc_authorized=False,
        stop_latched=False, faults=[], last_accepted_command_id=1,
        controller_state='DISABLED')
    value.update(changes)
    return value


def enabled(c):
    c.enable()
    c.observe(state(c, state_seq=2, challenge_seq=2, actual_enabled=True,
        oc_authorized=True, controller_state='ENABLED'))


def test_selecting_values_never_substitutes_for_hold(c2):
    c, _ = c2; enabled(c); c.apply(.7, -.2)
    i = c.intent()
    assert not i.deadman_held and i.throttle == i.steering == 0
    c.begin_hold(); i = c.intent()
    assert i.deadman_held and (i.throttle, i.steering) == (.7, -.2)
    c.release(); i = c.intent()
    assert i.enable_requested and not i.deadman_held
    assert i.throttle == i.steering == 0


def test_enable_requires_neutral_released_hold(c2):
    c, _ = c2
    c.throttle = .1
    with pytest.raises(ValueError): c.enable()
    c.throttle = 0; c.holding = True
    with pytest.raises(ValueError): c.enable()


def test_missing_encoder_warning_does_not_block_drive(c2):
    c, _ = c2
    c.observe(state(c, state_seq=2, faults=[fault('MCU', 'ENCODER_UNAVAILABLE', 'WARNING')]))
    c.enable()
    c.observe(state(c, state_seq=3, actual_enabled=True, oc_authorized=True,
        controller_state='ENABLED', faults=[fault('MCU', 'ENCODER_UNAVAILABLE', 'WARNING')]))
    c.apply(.2, 0); c.begin_hold()
    assert c.intent().throttle == .2
    c.observe(state(c, state_seq=4, faults=[fault('MCU', 'LINK_ERROR')]))
    assert not c.enable_requested and not c.holding


def test_stale_state_never_restores_motion_without_enable(c2):
    c, clock = c2; enabled(c); c.apply(.3, 0); c.begin_hold(); c.intent()
    clock.step(.351)
    assert not c.intent().enable_requested
    c.observe(state(c, state_seq=3, actual_enabled=True, oc_authorized=True, controller_state='ENABLED'))
    assert not c.enable_requested and not c.holding
    with pytest.raises(ValueError): c.begin_hold()


def test_frozen_input_loop_disarms_even_when_state_is_fresh(c2):
    c, clock = c2; enabled(c); c.apply(.3, 0); c.begin_hold(); c.intent()
    clock.step(.251)
    c.observe(state(c, state_seq=3, actual_enabled=True, oc_authorized=True, controller_state='ENABLED'))
    assert not c.intent().enable_requested


def test_duplicate_and_reordered_state_do_not_refresh(c2):
    c, clock = c2; before = c.state_at
    clock.step(.2)
    assert not c.observe(state(c))
    assert not c.observe(state(c, state_seq=0))
    assert c.state_at == before


def test_generation_change_invalidates_hold(c2):
    c, _ = c2; enabled(c); c.apply(.3, 0); c.begin_hold()
    c.observe(state(c, state_seq=3, generation=2, actual_enabled=True, oc_authorized=True))
    assert not c.enable_requested and not c.holding


def test_new_boot_revokes_ownership_and_old_boot_cannot_return(c2):
    c, _ = c2; enabled(c)
    assert c.observe(state(c, boot_id='b'*32, state_seq=1))
    assert not c.session_id and not c.enable_requested
    assert not c.observe(state(c, state_seq=999))
    assert c.state['boot_id'] == 'b'*32


def test_reclaim_preserves_process_identity_and_command_counter(c2):
    c, _ = c2
    a = c.intent()
    c.session_id = ''
    c.connected(c.client_id)
    c.observe(state(c, state_seq=2, generation=2))
    b = c.intent()
    assert a.session_id == b.session_id == c.client_id
    assert b.command_id > a.command_id
    with pytest.raises(ValueError): c.connected('c'*32)


def test_lease_challenge_is_echoed_but_wall_time_is_not_freshness(c2):
    c, _ = c2
    c.observe(state(c, state_seq=2, challenge_seq=42, generation=9))
    i = c.intent()
    assert (i.oc_boot_id, i.generation, i.challenge_seq) == ('a'*32, 9, 42)
    assert i.stamp_ns == 0 and c.fresh()


@pytest.mark.parametrize('values', [(float('nan'),0), (float('inf'),0), (1.01,0), (0,-1.01), (True,0)])
def test_invalid_normalized_inputs_do_not_change_previous_selection(c2, values):
    c, _ = c2; enabled(c); c.apply(.1,.2)
    with pytest.raises(ValueError): c.apply(*values)
    assert (c.throttle,c.steering) == (.1,.2)


def test_stop_withdraws_permission_and_zeroes(c2):
    c, _ = c2; enabled(c); c.apply(.2,.3); c.begin_hold(); c.stop()
    i = c.intent()
    assert i.stop_requested and not i.enable_requested and not i.deadman_held
    assert i.throttle == i.steering == 0


def test_reclaim_requires_post_claim_neutral_ack_not_historical_highwater(c2):
    c, clock = c2
    old = c.intent()
    clock.step(.351)
    c.intent()  # Frozen input loop clears motion permission.
    c.observe(state(c, state_seq=2, generation=2, session_id='',
                    last_accepted_command_id=old.command_id))
    c.connected(c.client_id)
    required = c.command_id + 1
    assert c.claim_ack_min == required
    c.observe(state(c, state_seq=3, generation=2,
                    last_accepted_command_id=old.command_id))
    with pytest.raises(ValueError, match='neutral acknowledgment'):
        c.enable()
    neutral = c.intent()
    assert neutral.command_id == required
    assert not neutral.deadman_held and neutral.throttle == neutral.steering == 0
    c.observe(state(c, state_seq=4, generation=2,
                    last_accepted_command_id=neutral.command_id))
    c.enable()
    assert c.enable_requested
