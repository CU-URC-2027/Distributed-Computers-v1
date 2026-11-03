"""ROS request idempotency tested without starting a node or opening serial."""
from collections import OrderedDict
from types import SimpleNamespace
from uuid import uuid4
from mini_rover_interfaces.srv import Control
from mini_rover_control.contracts import VERSION, MODE
from mini_rover_oc.node import OCNode


def adapter():
    return SimpleNamespace(cache=OrderedDict(), log=SimpleNamespace(write=lambda *args: None))


def request(**changes):
    values = dict(protocol_version=VERSION, mode=MODE, session_id=uuid4().hex,
                  request_id=uuid4().hex, oc_boot_id=uuid4().hex,
                  generation=1, challenge_seq=1, enabled=False)
    values.update(changes)
    return Control.Request(**values)


def call(node, req, mutation, name='reset_stop'):
    return OCNode._service(node, name, req, Control.Response(), mutation)


def test_same_ros_request_returns_result_without_repeating_reset():
    node = adapter(); req = request(); mutations = []
    def reset():
        mutations.append('reset')
        return True, 'RESET_PENDING'
    assert call(node, req, reset).accepted
    assert call(node, req, reset).accepted
    assert mutations == ['reset']


def test_changed_payload_cannot_reuse_identity_within_epoch():
    node = adapter(); req = request(); mutations = []
    def reset():
        mutations.append('reset')
        return True, 'RESET_PENDING'
    assert call(node, req, reset).accepted
    req.challenge_seq += 1
    rejected = call(node, req, reset)
    assert not rejected.accepted and 'reused' in rejected.reason
    assert mutations == ['reset']


def test_new_epoch_identity_is_validated_again_instead_of_reusing_old_success():
    node = adapter(); req = request()
    assert call(node, req, lambda: (True, 'RESET_PENDING')).accepted
    req.generation += 1
    rejected = call(node, req, lambda: (False, 'Fresh authority required'))
    assert not rejected.accepted


def test_live_cache_overflow_cannot_evict_replay_protection(monkeypatch):
    from mini_rover_oc import node as module
    monkeypatch.setattr(module.time, 'monotonic', lambda: 1.)
    node = adapter(); first = request(); mutations = []
    def reset():
        mutations.append('reset')
        return True, 'RESET_PENDING'
    assert call(node, first, reset).accepted
    for _ in range(1023):
        assert call(node, request(), lambda: (True, 'RESET_PENDING')).accepted
    assert not call(node, request(), reset).accepted
    assert call(node, first, reset).accepted
    assert mutations == ['reset']
    # Expired requests may be evicted; authority must still pass the controller.
    monkeypatch.setattr(module.time, 'monotonic', lambda: 1.201)
    assert not call(node, request(), lambda: (False, 'Expired challenge')).accepted
    assert len(node.cache) == 1


def test_unsupported_wire_contract_never_calls_operation():
    node = adapter()
    def forbidden():
        raise AssertionError('Invalid ROS contract reached controller')
    for req in (request(protocol_version=1), request(mode='WHEEL_SPEED'), request(request_id='short')):
        assert not call(node, req, forbidden).accepted
