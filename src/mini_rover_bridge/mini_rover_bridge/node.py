"""Read-only serial bridge. Never sends drive, LED, or polling commands."""

import time

import rclpy
from rclpy.clock import Clock, ClockType
from rclpy.exceptions import ParameterException
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from rcl_interfaces.msg import ParameterDescriptor
from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus, KeyValue
from mini_rover_interfaces.msg import MotorTelemetry
import serial

from .protocol import StreamDecoder

RECONNECT_SECONDS = 2.0
STALE_SECONDS = 1.0
READ_LIMIT = 4096


class MotorBridge(Node):
    def __init__(self, **kwargs):
        super().__init__('mini_rover_bridge', **kwargs)
        descriptor = ParameterDescriptor(read_only=True)
        self.declare_parameter('port', '', descriptor)
        self.declare_parameter('baud', 115200, descriptor)
        self.port = self.get_parameter('port').value
        self.baud = self.get_parameter('baud').value
        if not self.port or not self.port.strip():
            self.destroy_node()
            raise ValueError('port is required: --ros-args -p port:=/dev/serial/by-id/...')
        if self.baud <= 0:
            self.destroy_node()
            raise ValueError('baud must be positive')

        qos = QoSProfile(depth=10, reliability=ReliabilityPolicy.RELIABLE,
                         durability=DurabilityPolicy.VOLATILE)
        self.publisher = self.create_publisher(MotorTelemetry, '/rover/motor_telemetry', qos)
        self.diagnostics = self.create_publisher(DiagnosticArray, '/diagnostics', qos)
        self.decoder = StreamDecoder()
        self.serial_port = None
        self._retry_at = 0.0
        self._opened_at = 0.0
        self._last_valid = None
        self._last_error = ''
        self.received_records = 0
        # Transport health must not pause when ROS simulated time is stopped.
        self._steady_clock = Clock(clock_type=ClockType.STEADY_TIME)
        self.create_timer(0.01, self.poll, clock=self._steady_clock)
        self.create_timer(0.2, self.publish_diagnostics, clock=self._steady_clock)

    def _disconnect(self, error):
        if self.serial_port is not None:
            try:
                self.serial_port.close()
            except (OSError, serial.SerialException):
                pass
        self.serial_port = None
        self.decoder.reset()
        self._last_valid = None
        self._retry_at = time.monotonic() + RECONNECT_SECONDS
        error_text = str(error)
        if error_text != self._last_error:
            self.get_logger().warning(f'Serial unavailable: {error_text}')
        self._last_error = error_text

    def poll(self):
        now = time.monotonic()
        try:
            if self.serial_port is None:
                if now < self._retry_at:
                    return
                self.serial_port = serial.Serial(
                    port=self.port, baudrate=self.baud, timeout=0,
                    write_timeout=0, exclusive=True,
                )
                # Drop OS-buffered old samples after (re)opening the device.
                self.serial_port.reset_input_buffer()
                self.decoder.reset()
                self._opened_at = time.monotonic()
                self._last_valid = None
                self._last_error = ''
                self.get_logger().info(f'Reading {self.port} at {self.baud} baud')

            # A bounded read also keeps a noisy device from starving ROS timers.
            data = self.serial_port.read(min(READ_LIMIT, self.serial_port.in_waiting))
        except (OSError, serial.SerialException, ValueError) as error:
            self._disconnect(error)
            return

        for record in self.decoder.feed(data):
            message = MotorTelemetry()
            message.stamp = self.get_clock().now().to_msg()
            for field in record.__dataclass_fields__:
                setattr(message, field, getattr(record, field))
            self.publisher.publish(message)
            self._last_valid = time.monotonic()
            self.received_records += 1

    def publish_diagnostics(self):
        now = time.monotonic()
        status = DiagnosticStatus(name='mini_rover/usb_motor_telemetry', hardware_id=self.port)
        age = None if self._last_valid is None else now - self._last_valid
        if self.serial_port is None:
            status.level = DiagnosticStatus.ERROR
            status.message = f'Disconnected: {self._last_error or "waiting to open port"}'
        elif (age if age is not None else now - self._opened_at) >= STALE_SECONDS:
            status.level = DiagnosticStatus.STALE
            status.message = 'No valid motor telemetry for at least 1 second'
        elif age is None:
            status.level = DiagnosticStatus.WARN
            status.message = 'Connected; waiting for first motor record'
        else:
            status.level = DiagnosticStatus.OK
            status.message = 'Receiving motor telemetry'
        status.values = [
            KeyValue(key='valid_records', value=str(self.received_records)),
            KeyValue(key='invalid_records', value=str(self.decoder.invalid_lines)),
            KeyValue(key='oversized_lines', value=str(self.decoder.oversized_lines)),
            KeyValue(key='last_valid_age_s', value='never' if age is None else f'{age:.3f}'),
        ]
        message = DiagnosticArray()
        message.header.stamp = self.get_clock().now().to_msg()
        message.status = [status]
        self.diagnostics.publish(message)

    def destroy_node(self):
        connection = getattr(self, 'serial_port', None)
        if connection is not None:
            try:
                connection.close()
            except (OSError, serial.SerialException):
                pass
            self.serial_port = None
        return super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = None
    try:
        node = MotorBridge()
        rclpy.spin(node)
    except (ValueError, ParameterException) as error:
        raise SystemExit(str(error)) from error
    except (KeyboardInterrupt, rclpy.executors.ExternalShutdownException):
        pass
    finally:
        if node is not None:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
