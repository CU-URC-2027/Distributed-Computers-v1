"""Observe one local process and the ROS graph without claiming control."""
import argparse
import json
import math
import os
from pathlib import Path
import sys
import time
from uuid import uuid4

import psutil
import rclpy
from rclpy.clock import Clock, ClockType
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy
from rclpy.utilities import remove_ros_args
from std_msgs.msg import String
from diagnostic_msgs.msg import DiagnosticArray
from mini_rover_interfaces.msg import DriveIntent, ControlState, ChassisState

from .metrics import SystemSampler, clean
from .observer import Observer
from .recording import Recorder


def qos():
    return QoSProfile(depth=5, reliability=ReliabilityPolicy.BEST_EFFORT,
                      durability=DurabilityPolicy.VOLATILE)


def parse(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--role', choices=['c2', 'oc'], required=True)
    parser.add_argument('--pid', type=int, required=True, help='PID of the node process on THIS computer')
    parser.add_argument('--namespace', default='/rover1')
    parser.add_argument('--interval', type=float, default=1.)
    parser.add_argument('--log-directory', type=Path, default=Path('~/weeny_ws/performance_logs'))
    args = parser.parse_args(remove_ros_args(args=sys.argv if argv is None else ['monitor', *argv])[1:])
    if args.pid <= 0:
        parser.error('pid must be positive')
    if not math.isfinite(args.interval) or not .2 <= args.interval <= 60:
        parser.error('interval must be between 0.2 and 60 seconds')
    args.namespace = '/'+args.namespace.strip('/')
    return args


class PerformanceMonitor(Node):
    def __init__(self, args):
        super().__init__(args.role+'_performance_monitor', namespace=args.namespace)
        self.args = args
        self.monitor_id = uuid4().hex
        self.seq, self.target_alive = 0, True
        self.recorder = None
        self.record_error = ''
        self.sampler = SystemSampler(args.pid, args.log_directory)
        self.observer = Observer()
        self.publisher = self.create_publisher(String, 'inspection/metrics', qos())
        for kind, topic, callback in (
                (DriveIntent, 'c2/drive_intent', self.observer.intent_message),
                (ControlState, 'control/state', self.observer.state_message),
                (ChassisState, 'chassis/state', self.observer.chassis_message),
                (DiagnosticArray, 'diagnostics', self.observer.diagnostics_message)):
            self.create_subscription(kind, topic, callback, qos())
        self.last_sample = time.monotonic()
        self.create_timer(args.interval, self.sample, clock=Clock(clock_type=ClockType.STEADY_TIME))
        try:
            self.recorder = Recorder(args.log_directory, f'{args.role}-{time.time_ns()}-{self.monitor_id[:8]}')
        except OSError as exc:
            self.record_error = str(exc)
            self.get_logger().error('Recording unavailable: '+self.record_error)
        self.get_logger().info(f'Inspecting {args.role.upper()} PID {args.pid}; metrics: {self.get_namespace()}/inspection/metrics; '
                               f'log: {self.recorder.path if self.recorder else "unavailable"}')

    def sample(self):
        started = time.monotonic()
        measurements = self.sampler.sample(started)
        self.seq += 1
        if not measurements['process']['alive']:
            self.observer.event('process_exit', 'Target process exited; monitor is stopping')
            self.target_alive = False
        record = dict(schema_version=1, monitor_id=self.monitor_id, role=self.args.role,
                      hostname=self.sampler.hostname, host_boot_id=self.sampler.boot_id,
                      namespace=self.get_namespace(), domain=os.environ.get('ROS_DOMAIN_ID', '0'),
                      seq=self.seq, wall_time_ns=time.time_ns(), monotonic_ns=time.monotonic_ns(),
                      interval_s=self.args.interval, sample_elapsed_s=started-self.last_sample,
                      system=measurements['system'], process=measurements['process'],
                      observer=self.observer.snapshot(started),
                      monitor=dict(**measurements['overhead'], collection_ms=(time.monotonic()-started)*1000,
                                   log_path=str(self.recorder.path) if self.recorder else None,
                                   log_error=self.recorder.error if self.recorder else self.record_error,
                                   log_dropped=self.recorder.dropped if self.recorder else self.seq))
        self.last_sample = started
        record = clean(record)
        self.publisher.publish(String(data=json.dumps(record, allow_nan=False, separators=(',', ':'))))
        if self.recorder:
            self.recorder.write(record)

    def destroy_node(self):
        self.sampler.close()
        if self.recorder:
            self.recorder.close()
            if self.recorder.error:
                self.get_logger().error('Recording: '+self.recorder.error)
        return super().destroy_node()


def main(argv=None):
    args = parse(argv)
    rclpy.init(args=argv)
    node = None
    try:
        node = PerformanceMonitor(args)
        while rclpy.ok() and node.target_alive:
            rclpy.spin_once(node, timeout_sec=.2)
    except (KeyboardInterrupt, rclpy.executors.ExternalShutdownException):
        pass
    except (psutil.Error, OSError, ValueError) as exc:
        print('Performance monitor: '+str(exc), file=sys.stderr)
        return 1
    finally:
        if node:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
