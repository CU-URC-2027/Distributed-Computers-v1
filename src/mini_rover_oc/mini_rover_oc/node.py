"""ROS adapter for the v2 OC supervisor; no operator-device dependencies."""
from collections import OrderedDict
from dataclasses import asdict, fields
import os
import time
import rclpy
from rclpy.node import Node
from rclpy.clock import Clock, ClockType
from rclpy.duration import Duration
from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy
from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus, KeyValue
from mini_rover_interfaces.msg import DriveIntent as IntentMessage, ControlState, ChassisState, Fault
from mini_rover_interfaces.srv import Claim, Control
from mini_rover_control.contracts import Config, DriveIntent, WHEELS, VERSION, MODE, TOKEN_AGE, inhibiting
from mini_rover_control.protocol import identity
from mini_rover_control.oc import OCController
from mini_rover_control.serial_port import SerialArduinoPort
from mini_rover_control.run_log import RunLog


def stamp_ns(stamp):
    return stamp.sec * 1_000_000_000 + stamp.nanosec


def put_stamp(stamp, ns):
    stamp.sec, stamp.nanosec = divmod(ns, 1_000_000_000)


def intent_qos():
    return QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT,
                      durability=DurabilityPolicy.VOLATILE, lifespan=Duration(seconds=.25))


class OCNode(Node):
    def __init__(self, **kwargs):
        super().__init__('oc', **kwargs)
        if self.get_parameter('use_sim_time').value:
            raise ValueError('This prototype uses local monotonic leases; use_sim_time must be false')
        self.declare_parameter('port', '/tmp/mini-rover-arduino')
        self.declare_parameter('baud', 115200)
        self.declare_parameter('simulated', True)
        self.declare_parameter('log_directory', os.path.expanduser('~/weeny_ws/run_logs'))
        values = {field.name: self.declare_parameter(field.name, field.default).value for field in fields(Config)}
        config = Config(**values)
        self.port = SerialArduinoPort(self.get_parameter('port').value, self.get_parameter('baud').value)
        self.controller = OCController(self.port, config)
        self.log = RunLog(self.get_parameter('log_directory').value)
        self.log.write('configuration', dict(config=asdict(config), oc_boot_id=self.controller.boot_id,
            protocol_version=VERSION, mode=MODE, simulated=self.get_parameter('simulated').value))
        self.get_logger().info(f'Whole-run log: {self.log.path}')
        self.create_subscription(IntentMessage, 'c2/drive_intent', self.intent, intent_qos())
        self.state_pub = self.create_publisher(ControlState, 'control/state', intent_qos())
        self.wheel_pub = self.create_publisher(ChassisState, 'chassis/state', intent_qos())
        self.diag_pub = self.create_publisher(DiagnosticArray, 'diagnostics', intent_qos())
        self.cache = OrderedDict()
        self.create_service(Claim, 'control/claim', self.claim)
        for name in ('set_enabled', 'stop', 'reset_stop'):
            self.create_service(Control, 'control/'+name,
                                lambda req, res, name=name: self.control(name, req, res))
        self.last_published_sample = None
        self.steady = Clock(clock_type=ClockType.STEADY_TIME)
        self.create_timer(.01, self.tick, clock=self.steady)
        self.create_timer(.05, self.publish, clock=self.steady)

    def intent(self, msg):
        values = {f.name: getattr(msg, f.name) for f in fields(DriveIntent) if f.name != 'stamp_ns'}
        intent = DriveIntent(stamp_ns=stamp_ns(msg.stamp), **values)
        accepted = self.controller.receive(intent)
        self.log.write('intent', dict(accepted=accepted, **asdict(intent)))

    def _service(self, name, req, res, fn):
        owner = getattr(req, 'client_id', getattr(req, 'session_id', ''))
        generation = getattr(req, 'generation', 0)
        key = (name, owner, generation, req.request_id)
        signature = tuple((field, repr(getattr(req, field)))
                          for field in req.get_fields_and_field_types())
        if req.protocol_version != VERSION or not identity(req.request_id):
            res.accepted, res.reason = False, 'Invalid protocol version or request ID'
            return res
        if hasattr(req, 'mode') and req.mode != MODE:
            res.accepted, res.reason = False, 'Unsupported controller mode'
            return res
        if key in self.cache:
            previous_signature, values, _ = self.cache[key]
            if signature != previous_signature:
                res.accepted, res.reason = False, 'Request ID reused with different contents'
                return res
        else:
            now = time.monotonic()
            if len(self.cache) >= 1024:
                # Never evict an identity whose token could still authorize replay.
                expired = [key for key, (_, _, at) in self.cache.items()
                           if now-at > TOKEN_AGE]
                for expired_key in expired:
                    del self.cache[expired_key]
                if len(self.cache) >= 1024:
                    res.accepted, res.reason = False, 'Operation request capacity exceeded; retry with a new request ID'
                    return res
            values = fn()
            self.cache[key] = (signature, values, now)
            self.log.write('operation', dict(operation=name, owner=owner, request_id=req.request_id,
                generation=generation, accepted=values[0], reason=values[1]))
        res.accepted, res.reason = values[:2]
        if hasattr(res, 'session_id'):
            res.session_id = values[2]
        return res

    def claim(self, req, res):
        return self._service('claim', req, res,
                             lambda: self.controller.claim(req.client_id, req.operator_source))

    def control(self, name, req, res):
        op = ('enable' if req.enabled else 'disable') if name == 'set_enabled' else 'stop' if name == 'stop' else 'reset'
        return self._service(name, req, res, lambda: self.controller.operation(req.session_id, op,
            oc_boot_id=req.oc_boot_id, generation=req.generation, challenge_seq=req.challenge_seq))

    def tick(self):
        self.controller.tick()
        while self.controller.accepted_feedback:
            stamp, feedback = self.controller.accepted_feedback.popleft()
            self.log.write('arduino_feedback', dict(receipt_stamp_ns=stamp, **feedback))
        if self.log.error:
            self.controller._fault('LOGGING_FAILED')
            self.controller._disable('LOGGING_FAILED', invalidate=True)

    def publish(self):
        status = self.controller.status()
        msg = ControlState()
        put_stamp(msg.stamp, time.time_ns())
        for key, value in status.items():
            if key == 'faults':
                value = [Fault(**item) for item in value]
            elif key == 'first_fault':
                value = Fault(**value)
            setattr(msg, key, value)
        self.state_pub.publish(msg)
        self.log.write('control_state', status)
        f = self.controller.feedback
        sample = (f['boot_id'], f['sample_seq']) if f else None
        if f and self.controller.bound() and sample != self.last_published_sample:
            self.last_published_sample = sample
            chassis = ChassisState()
            put_stamp(chassis.stamp, self.controller.feedback_stamp_ns)
            chassis.protocol_version, chassis.mode = VERSION, MODE
            chassis.arduino_boot_id, chassis.source_session = f['boot_id'], self.controller.applied_session
            chassis.sample_seq, chassis.sample_time_ms = f['sample_seq'], f['uptime_ms']
            chassis.last_applied_command_id, chassis.last_applied_seq = self.controller.applied_id, self.controller.applied_seq
            chassis.applied_left_pwm, chassis.applied_right_pwm = f['applied']['left'], f['applied']['right']
            chassis.mcu_age_ms = max(0., (time.monotonic()-self.controller.feedback_at)*1000)
            chassis.simulated = self.get_parameter('simulated').value
            for name in WHEELS:
                wheel = getattr(chassis, name)
                wheel.raw_count = f['counts'][name]
                wheel.measurement_valid = f['valid'][name]
                wheel.direction_valid = f['direction_valid'][name]
            self.wheel_pub.publish(chassis)
        diag = DiagnosticArray()
        put_stamp(diag.header.stamp, time.time_ns())
        level = DiagnosticStatus.OK
        if status['faults']:
            level = DiagnosticStatus.ERROR if inhibiting(status['faults']) else DiagnosticStatus.WARN
        if not self.controller.fresh():
            level = DiagnosticStatus.STALE
        if self.log.error:
            level = DiagnosticStatus.ERROR
        diag.status = [DiagnosticStatus(level=level, name='mini_rover/oc', hardware_id=self.port.path,
            message=self.log.error or status['reason'],
            values=[KeyValue(key='feedback_age_ms', value=str(status['feedback_age_ms'])),
                    KeyValue(key='serial_error', value=self.port.error)])]
        self.diag_pub.publish(diag)

    def destroy_node(self):
        self.controller._send('DISABLE')
        self.port.close()
        self.log.close()
        return super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = None
    try:
        node = OCNode()
        rclpy.spin(node)
    except (KeyboardInterrupt, rclpy.executors.ExternalShutdownException):
        pass
    finally:
        if node:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
