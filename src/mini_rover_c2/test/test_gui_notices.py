"""GUI presentation behavior without opening a window or touching hardware."""
from types import SimpleNamespace
import pytest
pytest.importorskip('rclpy')
from mini_rover_c2.gui import RosStation
from mini_rover_c2.ros_client import C2Node


class Label:
    def __init__(self): self.text = ''
    def config(self, *, text): self.text = text


@pytest.fixture
def station():
    app = object.__new__(RosStation)
    app.ros = SimpleNamespace(notice='Ready', notice_revision=1)
    app.hint = Label()
    app._last_notice = app._notice_key()
    return app


def test_local_validation_error_persists_until_new_async_notice(station):
    def invalid():
        raise ValueError('Return inputs to zero')
    station.action(invalid)
    station.update_notice()
    assert station.hint.text == 'Return inputs to zero'
    station.ros.notice = 'Enable refused: MCU not confirmed'
    station.ros.notice_revision += 1
    station.update_notice()
    assert station.hint.text == 'Enable refused: MCU not confirmed'


def test_next_action_clears_previous_validation_error(station):
    def invalid():
        raise ValueError('Invalid value')
    station.action(invalid)
    station.action(lambda: None)
    assert station.hint.text == 'Ready'


def test_repeated_async_response_is_still_a_new_notice(station):
    def invalid():
        raise ValueError('Invalid value')
    station.action(invalid)
    station.ros.notice_revision += 1  # Repeated response with identical message.
    station.update_notice()
    assert station.hint.text == 'Ready'


def test_adapter_increments_revision_even_for_identical_notice():
    node = SimpleNamespace()
    C2Node.notice.fset(node, 'Refused')
    first = node.notice_revision
    C2Node.notice.fset(node, 'Refused')
    assert node.notice_revision == first + 1
    assert C2Node.notice.fget(node) == 'Refused'


def test_fault_display_retains_identity_and_clock_domain():
    text = RosStation.fault_text(dict(source='MCU', code='COMMAND_TIMEOUT',
        severity='ERROR', command_id=123, time_ms=456))
    assert 'MCU:COMMAND_TIMEOUT' in text
    assert 'command 123' in text and 'source-clock time 456 ms' in text
