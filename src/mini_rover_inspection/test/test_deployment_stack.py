"""Real launchers, GUI, observers, and PTY simulator on an isolated local domain."""
import json
import os
from pathlib import Path
import signal
import subprocess
import time
from uuid import uuid4

import psutil
import pytest


@pytest.mark.skipif(not os.environ.get('DISPLAY'), reason='C2 GUI launch needs a graphical display')
def test_deployment_with_inspection_and_disabled_mode(tmp_path):
    env = dict(os.environ, ROS_DOMAIN_ID='187', ROS_AUTOMATIC_DISCOVERY_RANGE='LOCALHOST')
    env.pop('ROS_STATIC_PEERS', None)
    env.pop('ROS_LOCALHOST_ONLY', None)
    namespace = 'inspection_stack_'+uuid4().hex[:8]
    processes, streams = [], []

    def start(name, command):
        stream = (tmp_path/(name+'.log')).open('w')
        streams.append(stream)
        process = subprocess.Popen(command, env=env, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
        processes.append(process)
        return process

    def wait(predicate, timeout=15):
        end = time.monotonic()+timeout
        while time.monotonic() < end:
            if predicate():
                return
            time.sleep(.05)
        logs = '\n'.join(p.read_text() for p in tmp_path.glob('*.log'))
        raise AssertionError('Timed out in deployment stack:\n'+logs)

    def records(role):
        paths = list((tmp_path/'metrics').glob(role+'-*.jsonl'))
        if not paths:
            return []
        try:
            return [json.loads(line) for line in paths[0].read_text().splitlines()]
        except ValueError:
            return []

    try:
        start('simulator', ['ros2', 'run', 'mini_rover_sim', 'arduino_sim', '--port-link', str(tmp_path/'pty')])
        wait(lambda: (tmp_path/'pty').exists())
        oc = start('oc-launch', ['ros2', 'launch', 'mini_rover_oc', 'oc.launch.py',
            'namespace:='+namespace, 'simulated:=true', 'port:='+str(tmp_path/'pty'),
            'inspect_performance:=true', 'inspect_interval:=0.5',
            'inspect_log_directory:='+str(tmp_path/'metrics'), 'log_directory:='+str(tmp_path/'oc-runs')])
        c2 = start('c2-launch', ['ros2', 'launch', 'mini_rover_c2', 'c2.launch.py',
            'namespace:='+namespace, 'inspect_performance:=true', 'inspection_dashboard:=true',
            'inspect_interval:=0.5', 'inspect_log_directory:='+str(tmp_path/'metrics')])
        wait(lambda: len(records('oc')) >= 6 and len(records('c2')) >= 6)
        assert oc.poll() is None and c2.poll() is None
        oc_sample, c2_sample = records('oc')[-1], records('c2')[-1]
        for sample in (oc_sample, c2_sample):
            assert sample['process']['alive']
            assert sample['observer']['chassis']['simulated'] is True
            assert sample['observer']['control']['actual_enabled'] is False
            assert sample['observer']['topics']['state']['rate_hz'] > 10
            assert sample['observer']['diagnostics']['level'] in (0, 1, 2, 3)
            assert sample['monitor']['log_error'] == ''
            assert sample['observer']['window_peak_reported_ages']['input_age_ms'] is None
        assert oc_sample['process']['pid'] != c2_sample['process']['pid']
        assert any('performance_dashboard' in ' '.join(p.cmdline())
                   for p in psutil.Process(c2.pid).children(recursive=True))

        # Actual emitted records must be accepted and displayed by the viewer.
        import tkinter as tk
        from mini_rover_inspection.dashboard import Dashboard, load_recordings
        from mini_rover_inspection.recording import SampleStore
        store = SampleStore()
        count, skipped = load_recordings(store, list((tmp_path/'metrics').glob('*.jsonl')))
        assert count >= 12 and skipped == 0
        root = tk.Tk()
        try:
            viewer = Dashboard(root, store, '/'+namespace, replay=True, note='· Local simulator validation')
            root.update()
            assert all(panel.badge.cget('text') == 'RECORDED' for panel in viewer.panels.values())
            if os.environ.get('ROVER_INSPECTION_SCREENSHOT'):
                from PIL import ImageGrab
                destination = Path(os.environ['ROVER_INSPECTION_SCREENSHOT'])
                destination.parent.mkdir(parents=True, exist_ok=True)
                root.lift()
                root.update()
                ImageGrab.grab(bbox=(root.winfo_rootx(), root.winfo_rooty(),
                    root.winfo_rootx()+root.winfo_width(), root.winfo_rooty()+root.winfo_height()),
                    xdisplay=os.environ['DISPLAY']).save(destination)
        finally:
            for callback in root.tk.call('after', 'info'):
                root.after_cancel(callback)
            root.destroy()

        monitor = next(p for p in psutil.Process(oc.pid).children(recursive=True)
                       if 'performance_monitor' in ' '.join(p.cmdline()))
        monitor.terminate()
        wait(lambda: not monitor.is_running() or monitor.status() == psutil.STATUS_ZOMBIE)
        assert psutil.pid_exists(oc_sample['process']['pid']) and oc.poll() is None
        before = records('c2')[-1]['observer']['topics']['state']['total']
        wait(lambda: records('c2')[-1]['observer']['topics']['state']['total'] > before+25)

        disabled = start('c2-disabled', ['ros2', 'launch', 'mini_rover_c2', 'c2.launch.py',
            'namespace:='+namespace+'_disabled', 'inspect_performance:=false', 'inspection_dashboard:=true',
            'inspect_log_directory:='+str(tmp_path/'must-not-exist')])
        wait(lambda: any('c2_gui' in ' '.join(p.cmdline()) for p in psutil.Process(disabled.pid).children(recursive=True)))
        time.sleep(.5)
        assert not (tmp_path/'must-not-exist').exists()
        assert not any('performance_' in ' '.join(p.cmdline()) for p in psutil.Process(disabled.pid).children(recursive=True))
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                # Signal the launcher only; it forwards shutdown to its children.
                process.send_signal(signal.SIGINT)
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=3)
        for stream in streams:
            stream.close()
