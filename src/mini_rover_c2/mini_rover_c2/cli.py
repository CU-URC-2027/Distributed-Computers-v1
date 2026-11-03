"""Explicit simulation-only test client. Physical driving uses GUI hold-to-drive."""
import argparse
import json
import time
import rclpy
from .ros_client import C2Node


def wait(node, predicate, timeout=6):
    deadline = time.monotonic()+timeout
    next_send = 0.
    while time.monotonic() < deadline:
        rclpy.spin_once(node, timeout_sec=.01)
        now = time.monotonic()
        node.pump(publish=now >= next_send)
        if now >= next_send:
            next_send = now+.05
        if predicate():
            return
    raise RuntimeError(node.notice+' (wait timed out)')


def require_simulation(node):
    if not node.chassis_fresh() or not node.chassis.simulated:
        node.controller.disarm()
        raise RuntimeError('CLI motion is simulation-only; use GUI hold-to-drive for hardware')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--simulation', action='store_true', help='Required explicit simulation test mode')
    parser.add_argument('--throttle', type=float, default=0.)
    parser.add_argument('--steering', type=float, default=0.)
    parser.add_argument('--duration', type=float, default=5.)
    parser.add_argument('--namespace', default='/rover1')
    args = parser.parse_args(argv)
    if not args.simulation:
        parser.error('Pass --simulation for simulator tests; physical driving requires the GUI')
    if not 0 < args.duration <= 3600:
        parser.error('duration must be between 0 and 3600 seconds')
    rclpy.init(args=[])
    node = C2Node(namespace=args.namespace)
    try:
        node.controller.config.validate_input(args.throttle, args.steering)
        wait(node, lambda: node.claim_client.service_is_ready() and node.controller.fresh()
             and node.controller.state['enabled_known'] and node.chassis is not None)
        require_simulation(node)
        node.connect()
        wait(node, lambda: bool(node.controller.session_id) and not node.pending)
        wait(node, lambda: node.controller.fresh() and node.controller.state['session_id']==node.controller.session_id
             and node.controller.state['last_accepted_command_id'] >= node.controller.claim_ack_min)
        require_simulation(node)
        node.operation('enable')
        wait(node, lambda: not node.pending and node.controller.state['actual_enabled'])
        require_simulation(node)
        node.controller.apply(args.throttle, args.steering)
        node.controller.begin_hold()
        end = time.monotonic()+args.duration
        next_print = next_send = 0.
        while time.monotonic() < end:
            rclpy.spin_once(node, timeout_sec=.005)
            now = time.monotonic()
            node.pump(publish=now >= next_send)
            if now >= next_send:
                next_send = now+.05
            require_simulation(node)
            if not node.controller.enable_requested or not node.controller.session_id:
                raise RuntimeError('Control lost: '+node.notice)
            if now >= next_print:
                print(json.dumps(node.controller.state), flush=True)
                next_print = now+1
    except KeyboardInterrupt:
        pass
    finally:
        node.controller.disarm()
        try:
            node.operation('disable')
            wait(node, lambda: not node.pending, timeout=.5)
        except (ValueError, RuntimeError):
            pass
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
