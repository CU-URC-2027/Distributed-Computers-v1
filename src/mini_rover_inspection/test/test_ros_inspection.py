"""Real DDS observation with a harmless process; no serial ports or control calls."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4


def _exercise(directory):
    import rclpy
    from rclpy.executors import SingleThreadedExecutor
    from rclpy.node import Node
    from std_msgs.msg import String
    from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus, KeyValue
    from mini_rover_interfaces.msg import ControlState, Fault
    from mini_rover_inspection.monitor import PerformanceMonitor, parse, qos

    target = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
    monitor = peer = executor = None
    initialized = False
    samples = []
    namespace = '/inspection_test_'+uuid4().hex[:12]

    def wait(predicate, timeout=8):
        deadline = time.monotonic()+timeout
        while time.monotonic() < deadline:
            executor.spin_once(timeout_sec=.02)
            if predicate():
                return
        raise AssertionError('Timed out waiting for isolated DDS observation')

    try:
        rclpy.init(args=[])
        initialized = True
        # Sample manually after both messages arrive. This proves a short fault
        # survives being cleared between resource samples without timer races.
        monitor = PerformanceMonitor(parse([
            '--role', 'oc', '--pid', str(target.pid), '--namespace', namespace,
            '--interval', '60', '--log-directory', str(directory),
        ]))
        peer = Node('inspection_test_peer', namespace=namespace)
        publisher = peer.create_publisher(ControlState, 'control/state', qos())
        diagnostics = peer.create_publisher(DiagnosticArray, 'diagnostics', qos())
        peer.create_subscription(String, 'inspection/metrics',
                                 lambda message: samples.append(json.loads(message.data)), qos())
        executor = SingleThreadedExecutor()
        executor.add_node(monitor)
        executor.add_node(peer)
        wait(lambda: publisher.get_subscription_count() >= 1
             and diagnostics.get_subscription_count() >= 1
             and monitor.publisher.get_subscription_count() >= 1)

        diagnostics.publish(DiagnosticArray(status=[DiagnosticStatus(
            name='mini_rover/oc', message='healthy',
            values=[KeyValue(key='serial_error', value='')])]))
        wait(lambda: monitor.observer.diagnostics is not None)

        fault = Fault(source='OC', code='INPUT_EXPIRED', severity='ERROR')
        publisher.publish(ControlState(boot_id='integration-boot', state_seq=1,
                                       controller_state='DISABLED', faults=[fault]))
        wait(lambda: monitor.observer.control is not None
             and bool(monitor.observer.control['faults']))
        publisher.publish(ControlState(boot_id='integration-boot', state_seq=2,
                                       controller_state='DISABLED', faults=[]))
        wait(lambda: monitor.observer.control is not None
             and not monitor.observer.control['faults'])
        assert monitor.seq == 0
        monitor.sample()
        wait(lambda: bool(samples))

        first = samples[0]
        assert first['process']['pid'] == target.pid
        assert first['process']['alive'] is True
        assert first['namespace'] == namespace
        assert first['domain'] == '186'
        assert first['observer']['control']['faults'] == []
        assert first['observer']['diagnostics']['level'] == 0
        event_pairs = {(e['kind'], e['message']) for e in first['observer']['events']}
        assert ('fault', 'OC / INPUT_EXPIRED / ERROR') in event_pairs
        assert ('fault_cleared', 'OC / INPUT_EXPIRED / ERROR') in event_pairs

        # Parameter services are standard ROS Node infrastructure. There must
        # be no motor-authority services/clients or control-command publisher.
        services = peer.get_service_names_and_types_by_node(monitor.get_name(), namespace)
        clients = peer.get_client_names_and_types_by_node(monitor.get_name(), namespace)
        publishers = peer.get_publisher_names_and_types_by_node(monitor.get_name(), namespace)
        assert all('/control/' not in name for name, _ in services+clients)
        assert not clients
        assert namespace+'/c2/drive_intent' not in {name for name, _ in publishers}
        assert {name for name, _ in publishers} <= {
            '/rosout', '/parameter_events', namespace+'/inspection/metrics',
        }

        target.terminate()
        target.wait(timeout=3)
        monitor.sample()
        wait(lambda: len(samples) >= 2)
        assert monitor.target_alive is False  # main's loop exit condition
        assert samples[-1]['process']['alive'] is False
        assert any(e['kind'] == 'process_exit' for e in samples[-1]['observer']['events'])
        log_path = monitor.recorder.path
        executor.remove_node(monitor)
        monitor.destroy_node()
        monitor = None
        recorded = [json.loads(line) for line in log_path.read_text().splitlines()]
        assert len(recorded) == 2
        assert recorded[0]['monitor_id'] == first['monitor_id']
        assert recorded[0]['observer']['events'] == first['observer']['events']
        assert recorded[-1]['process']['alive'] is False
    finally:
        if target.poll() is None:
            target.terminate()
            target.wait(timeout=3)
        if executor is not None:
            executor.shutdown(timeout_sec=2)
        if monitor is not None:
            monitor.destroy_node()
        if peer is not None:
            peer.destroy_node()
        if initialized and rclpy.ok():
            rclpy.shutdown()


def test_passive_ros_metrics_fault_retention_recording_and_target_exit(tmp_path):
    # Keep ROS environment and its default context wholly outside pytest's
    # process, including when this test runs alongside the control regressions.
    env = dict(os.environ, ROS_DOMAIN_ID='186', ROS_AUTOMATIC_DISCOVERY_RANGE='LOCALHOST',
               ROS_LOCALHOST_ONLY='1')
    env.pop('ROS_STATIC_PEERS', None)
    result = subprocess.run([sys.executable, str(Path(__file__).resolve()), str(tmp_path)],
                            env=env, capture_output=True, text=True, timeout=35)
    assert result.returncode == 0, result.stdout+'\n'+result.stderr


def test_monitor_executable_exits_after_target_process(tmp_path):
    env = dict(os.environ, ROS_DOMAIN_ID='186', ROS_AUTOMATIC_DISCOVERY_RANGE='LOCALHOST',
               ROS_LOCALHOST_ONLY='1')
    env.pop('ROS_STATIC_PEERS', None)
    target = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
    monitor = None
    try:
        monitor = subprocess.Popen([
            sys.executable, '-m', 'mini_rover_inspection.monitor',
            '--role', 'c2', '--pid', str(target.pid), '--namespace', '/inspection_exit_'+uuid4().hex[:12],
            '--interval', '.2', '--log-directory', str(tmp_path),
        ], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        deadline = time.monotonic()+8
        logs = []
        while time.monotonic() < deadline:
            logs = list(tmp_path.glob('*.jsonl'))
            if logs and logs[0].stat().st_size:
                break
            if monitor.poll() is not None:
                raise AssertionError(monitor.communicate()[0])
            time.sleep(.02)
        assert logs and logs[0].stat().st_size, 'Monitor did not record an initial sample'
        target.terminate()
        target.wait(timeout=3)
        output, _ = monitor.communicate(timeout=5)
        assert monitor.returncode == 0, output
        records = [json.loads(line) for line in logs[0].read_text().splitlines()]
        assert records[0]['process']['alive'] is True
        assert records[-1]['process']['alive'] is False
        assert any(event['kind'] == 'process_exit' for event in records[-1]['observer']['events'])
    finally:
        for process in (target, monitor):
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)


if __name__ == '__main__':
    _exercise(Path(sys.argv[1]))
