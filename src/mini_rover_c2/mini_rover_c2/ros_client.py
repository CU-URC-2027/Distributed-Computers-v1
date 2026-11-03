"""ROS adapter for the versioned manual hold-to-drive contract."""
from dataclasses import fields
from queue import Queue, Empty
import time
from uuid import uuid4
from rclpy.node import Node
from rclpy.duration import Duration
from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy
from mini_rover_interfaces.msg import DriveIntent, ControlState, ChassisState
from mini_rover_interfaces.srv import Claim, Control
from mini_rover_control.c2 import C2Controller
from mini_rover_control.contracts import VERSION, MODE, STATE_TIMEOUT, FEEDBACK_TIMEOUT, OPERATION_TIMEOUT


def set_stamp(stamp, ns):
    stamp.sec, stamp.nanosec = divmod(ns, 1_000_000_000)


def latest(queue, value):
    try:
        queue.get_nowait()
    except Empty:
        pass
    queue.put_nowait(value)


def plain(value):
    if hasattr(value, 'get_fields_and_field_types'):
        return {name: plain(getattr(value, name)) for name in value.get_fields_and_field_types()}
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    return value


def qos():
    return QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT,
        durability=DurabilityPolicy.VOLATILE, lifespan=Duration(seconds=.25))


class C2Node(Node):
    @property
    def notice(self):
        return self._notice

    @notice.setter
    def notice(self, value):
        self._notice = value
        self.notice_revision = getattr(self, 'notice_revision', 0) + 1

    def __init__(self, namespace='/rover1', **kwargs):
        super().__init__('c2_operator', namespace=namespace, **kwargs)
        self.controller = C2Controller()
        self.client_id = self.controller.client_id
        self.messages, self.chassis_messages = Queue(maxsize=1), Queue(maxsize=1)
        self.chassis = self.chassis_at = None
        self._chassis_boot = None
        self._chassis_seq = 0
        self._retired_chassis_boots = set()
        self.pending = {}
        self.reset_barrier = None
        self.notice = 'Connect, enable at neutral, then hold to drive'
        self.publisher = self.create_publisher(DriveIntent, 'c2/drive_intent', qos())
        self.create_subscription(ControlState, 'control/state', lambda m: latest(self.messages, (time.monotonic(), m)), qos())
        self.create_subscription(ChassisState, 'chassis/state', lambda m: latest(self.chassis_messages, (time.monotonic(), m)), qos())
        self.claim_client = self.create_client(Claim, 'control/claim')
        self.control_clients = {name: self.create_client(Control, 'control/'+name)
            for name in ('set_enabled', 'stop', 'reset_stop')}

    def _cancel_pending(self):
        for future, _ in self.pending.values():
            future.cancel()
        self.pending.clear()

    def _call(self, name, client, req):
        if not client.service_is_ready():
            raise ValueError('OC service unavailable; start the selected OC launch first')
        if self.pending:
            raise ValueError('Wait for the pending OC operation')
        req.request_id = uuid4().hex
        set_stamp(req.stamp, time.time_ns())
        self.pending[name] = (client.call_async(req), time.monotonic())

    def connect(self):
        self.controller.disarm()
        self.reset_barrier = None
        self._call('claim', self.claim_client, Claim.Request(protocol_version=VERSION,
            client_id=self.client_id, operator_source='manual'))

    def _control_request(self, enabled=False):
        s = self.controller.state
        if not s or not self.controller.fresh():
            raise ValueError('Wait for fresh OC feedback before this operation')
        return Control.Request(protocol_version=VERSION, mode=MODE,
            session_id=self.controller.session_id, oc_boot_id=s['boot_id'],
            generation=s['generation'], challenge_seq=s['challenge_seq'], enabled=enabled)

    def operation(self, name):
        if name not in ('enable', 'disable', 'stop', 'reset'):
            raise ValueError('Unknown operation')
        if name in ('stop', 'disable'):
            self.reset_barrier = None
            self._cancel_pending()
            if name == 'stop':
                self.controller.stop()
            else:
                self.controller.disarm()
        elif self.pending or self.reset_barrier:
            raise ValueError('Wait for the pending OC operation')
        if name == 'reset':
            if not self.controller.session_id or not self.controller.fresh():
                raise ValueError('Claim ownership and wait for fresh OC feedback')
            self.controller.disarm()
            self.controller.stop_requested = False
            command_id = self._publish_intent()
            self.reset_barrier = (self.controller.session_id, command_id, time.monotonic())
            self.notice = 'Waiting for neutral acknowledgment before resetting STOP'
            return
        if name == 'enable':
            self.controller.enable()
        try:
            service = 'stop' if name == 'stop' else 'set_enabled'
            self._call(name, self.control_clients[service], self._control_request(name == 'enable'))
        except ValueError:
            if name == 'enable':
                self.controller.disarm()
            raise

    def _observe_chassis(self, received, msg):
        if msg.protocol_version != VERSION or msg.mode != MODE:
            return False
        boot, seq = msg.arduino_boot_id, msg.sample_seq
        if boot in self._retired_chassis_boots or (boot == self._chassis_boot and seq <= self._chassis_seq):
            return False
        s = self.controller.state
        if s and s['arduino_boot_id'] and boot != s['arduino_boot_id']:
            return False
        if self._chassis_boot and boot != self._chassis_boot:
            self._retired_chassis_boots.add(self._chassis_boot)
        self._chassis_boot, self._chassis_seq = boot, seq
        self.chassis_at, self.chassis = received, msg
        return True

    def chassis_age_ms(self):
        if self.chassis is None or self.chassis_at is None:
            return float('inf')
        return self.chassis.mcu_age_ms + max(0., time.monotonic()-self.chassis_at)*1000

    def chassis_fresh(self):
        s = self.controller.state
        return bool(s and self.controller.fresh() and s['enabled_known'] and self.chassis
            and self.chassis.arduino_boot_id == s['arduino_boot_id']
            and self.chassis_age_ms() < FEEDBACK_TIMEOUT*1000)

    def pump(self, publish=True):
        now = time.monotonic()
        try:
            received, msg = self.messages.get_nowait()
            if now-received < STATE_TIMEOUT and self.controller.observe(plain(msg)):
                self.controller.state_at = received
        except Empty:
            pass
        try:
            received, msg = self.chassis_messages.get_nowait()
            self._observe_chassis(received, msg)
        except Empty:
            pass
        for name, (future, started) in list(self.pending.items()):
            if future.done() or now-started >= OPERATION_TIMEOUT:
                del self.pending[name]
                if not future.done():
                    future.cancel()
                    self.controller.disarm()
                    self.notice = 'OC operation timed out; inspect state before retrying'
                    continue
                try:
                    response = future.result()
                    self.notice = response.reason
                    if not response.accepted and name == 'enable':
                        self.controller.disarm()
                    elif response.accepted and name == 'claim':
                        self.controller.connected(response.session_id)
                except Exception as exc:
                    self.controller.disarm()
                    self.notice = str(exc)
        if self.reset_barrier:
            session, command_id, started = self.reset_barrier
            state = self.controller.state
            if now-started >= OPERATION_TIMEOUT or session != self.controller.session_id:
                self.reset_barrier = None
                self.notice = 'STOP reset not sent: neutral acknowledgment timed out or ownership changed'
            elif (self.controller.fresh() and state['session_id'] == session
                  and state['last_accepted_command_id'] >= command_id):
                self.reset_barrier = None
                try:
                    self._call('reset', self.control_clients['reset_stop'], self._control_request())
                except ValueError as exc:
                    self.notice = str(exc)
        if publish:
            self._publish_intent()

    def _publish_intent(self):
        intent = self.controller.intent()
        if intent:
            msg = DriveIntent()
            for field in fields(intent):
                if field.name == 'stamp_ns':
                    set_stamp(msg.stamp, intent.stamp_ns)
                else:
                    setattr(msg, field.name, getattr(intent, field.name))
            self.publisher.publish(msg)
            return intent.command_id
        return 0
