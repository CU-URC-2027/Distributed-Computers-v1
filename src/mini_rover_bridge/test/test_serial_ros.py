"""Exercise real pySerial, a Linux PTY, and ROS publishers/subscribers."""

import os
import pty
import random
import time

import pytest
from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus
from rclpy.context import Context
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node
from rclpy.parameter import Parameter
from mini_rover_interfaces.msg import MotorTelemetry

from mini_rover_bridge.node import MotorBridge


class Harness:
    def __init__(self, tmp_path):
        self.context = Context()
        self.context.init(domain_id=random.randint(120, 180))
        self.executor = SingleThreadedExecutor(context=self.context)
        self.link = tmp_path / 'rover'
        self.master = None
        self.slave = None
        self.attach()
        self.bridge = MotorBridge(context=self.context, parameter_overrides=[
            Parameter('port', value=str(self.link)),
        ])
        self.observer = Node('telemetry_test_observer', context=self.context)
        self.messages = []
        self.diagnostics = []
        self.observer.create_subscription(MotorTelemetry, '/rover/motor_telemetry', self.messages.append, 10)
        self.observer.create_subscription(DiagnosticArray, '/diagnostics', self.diagnostics.append, 10)
        self.executor.add_node(self.bridge)
        self.executor.add_node(self.observer)
        self.until(lambda: self.bridge.serial_port is not None)
        self.until(lambda: self.bridge.publisher.get_subscription_count() > 0)

    def attach(self):
        self.master, self.slave = pty.openpty()
        os.set_blocking(self.master, False)
        self.link.symlink_to(os.ttyname(self.slave))

    def detach(self):
        self.link.unlink(missing_ok=True)
        for descriptor in (self.master, self.slave):
            if descriptor is not None:
                os.close(descriptor)
        self.master = self.slave = None

    def spin(self, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.executor.spin_once(timeout_sec=0.01)

    def until(self, condition, timeout=5.0):
        deadline = time.monotonic() + timeout
        while not condition() and time.monotonic() < deadline:
            self.executor.spin_once(timeout_sec=0.01)
        assert condition(), 'timed out waiting for serial/ROS state'

    def send(self, data):
        os.write(self.master, data)

    def latest_level(self):
        return self.diagnostics[-1].status[0].level if self.diagnostics else None

    def close(self):
        self.executor.shutdown()
        self.bridge.destroy_node()
        self.observer.destroy_node()
        self.context.shutdown()
        self.detach()


@pytest.fixture
def harness(tmp_path):
    h = Harness(tmp_path)
    try:
        yield h
    finally:
        h.close()


def test_publication_framing_staleness_and_read_only(harness):
    h = harness
    h.send(b'[BOOT] ready\nMOTOR1,100,11,22,')
    h.spin(0.1)
    assert not h.messages
    before = h.observer.get_clock().now().nanoseconds
    h.send(b'33,44,180,-45,1\n')
    h.until(lambda: len(h.messages) == 1)
    message = h.messages[0]
    after = h.observer.get_clock().now().nanoseconds
    assert (message.fl_count, message.rl_count, message.fr_count, message.rr_count) == (11, 22, 33, 44)
    assert (message.uptime_ms, message.throttle, message.steering, message.driver_enabled) == (100, 180, -45, True)
    assert before <= message.stamp.sec * 10**9 + message.stamp.nanosec <= after
    h.until(lambda: h.latest_level() == DiagnosticStatus.OK)
    h.send(b'MOTOR1,bad\n' + b'x' * 1000 + b'\n')
    h.until(lambda: h.latest_level() == DiagnosticStatus.STALE, timeout=2.0)
    assert len(h.messages) == 1  # Diagnostics never republish a stale sample.
    assert h.bridge.decoder.invalid_lines == 1
    assert h.bridge.decoder.oversized_lines == 1
    with pytest.raises(BlockingIOError):
        os.read(h.master, 4096)  # The bridge sent no serial bytes.
    h.send(b'MOTOR1,200,12,23,34,45,0,0,0\n')
    h.until(lambda: len(h.messages) == 2)
    h.until(lambda: h.latest_level() == DiagnosticStatus.OK)


def test_unplug_reconnect_discards_partial_and_accepts_restart(harness):
    h = harness
    h.send(b'MOTOR1,4294967295,99,98,97,96,0,0,0\nMOTOR1,123,')
    h.until(lambda: len(h.messages) == 1)
    h.detach()
    h.until(lambda: h.latest_level() == DiagnosticStatus.ERROR)
    h.attach()
    h.until(lambda: h.bridge.serial_port is not None, timeout=4.0)
    h.send(b'11,22,33,44,180,-45,1\nMOTOR1,0,0,0,0,0,0,0,0\n')
    h.until(lambda: len(h.messages) == 2)
    assert h.messages[-1].uptime_ms == 0
    assert h.messages[-1].fl_count == 0
    h.spin(0.2)
    assert len(h.messages) == 2


def test_missing_device_recovers_and_no_first_record_becomes_stale(harness):
    h = harness
    h.detach()
    h.until(lambda: h.bridge.serial_port is None)
    # Remain absent through an entire retry, then attach a replacement device.
    h.spin(2.2)
    assert h.latest_level() == DiagnosticStatus.ERROR
    h.attach()
    h.until(lambda: h.bridge.serial_port is not None, timeout=3.0)
    h.until(lambda: h.latest_level() == DiagnosticStatus.STALE, timeout=2.0)
    assert not h.messages


def test_port_is_required():
    context = Context()
    context.init(domain_id=random.randint(120, 180))
    try:
        with pytest.raises(ValueError, match='port is required'):
            MotorBridge(context=context)
    finally:
        context.shutdown()
