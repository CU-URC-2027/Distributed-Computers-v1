#!/usr/bin/env python3
"""Package-local, simulation-only orchestration. Does not change the ROS workspace."""
import argparse
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
from uuid import uuid4
import yaml

ROOT = Path(__file__).resolve().parents[1]
WHEELS = {'front_left', 'rear_left', 'front_right', 'rear_right'}
DEFAULTS = dict(workspace='~/weeny_ws', domain=92, namespace='/rover1', gui=True,
    command=dict(throttle=.20, steering=-.25, duration=5.), oc=dict(pwm_limit=63),
    simulator=dict(response_time=.20),
    fault=dict(delay_ms=0, missing=[], drop_commands=False, silence=False))


class ToolError(RuntimeError): pass


def number(value, name, low, high, *, integer=False, exclusive_low=False):
    if (type(value) not in ((int,) if integer else (int, float)) or not math.isfinite(value)
            or (value <= low if exclusive_low else value < low) or value > high):
        raise ToolError(f'{name}: expected {"integer" if integer else "finite number"} '
                        f'{"(" if exclusive_low else "["}{low}, {high}]')


def merge_strict(base, supplied, path='config'):
    if not isinstance(supplied, dict): raise ToolError(f'{path} must be a mapping')
    for key, value in supplied.items():
        if key not in base: raise ToolError(f'Unknown setting: {path}.{key}; protocol/timers are fixed')
        if isinstance(base[key], dict): merge_strict(base[key], value, path+'.'+key)
        else: base[key] = value


def validate(config):
    if not isinstance(config['workspace'], str) or not config['workspace']:
        raise ToolError('workspace must be a nonempty path')
    config['workspace'] = str(Path(config['workspace']).expanduser().resolve())
    number(config['domain'], 'domain', 0, 232, integer=True)
    if (not isinstance(config['namespace'], str)
            or not re.fullmatch(r'/[A-Za-z_][A-Za-z0-9_]*(/[A-Za-z_][A-Za-z0-9_]*)*', config['namespace'])):
        raise ToolError('namespace must be an absolute non-root ROS namespace, e.g. /rover1')
    if type(config['gui']) is not bool: raise ToolError('gui must be true or false')
    for field in ('throttle', 'steering'): number(config['command'][field], field, -1, 1)
    number(config['command']['duration'], 'duration', 0, 3600, exclusive_low=True)
    number(config['oc']['pwm_limit'], 'pwm_limit', 1, 63, integer=True)
    number(config['simulator']['response_time'], 'response_time', 0, float('inf'), exclusive_low=True)
    validate_fault(config['fault'])
    return config


def validate_fault(fault):
    if set(fault) != set(DEFAULTS['fault']): raise ToolError('Unknown or missing fault key')
    number(fault['delay_ms'], 'delay_ms', 0, 2000)
    if (not isinstance(fault['missing'], list) or any(not isinstance(w, str) for w in fault['missing'])
            or len(set(fault['missing'])) != len(fault['missing']) or not set(fault['missing']) <= WHEELS):
        raise ToolError('missing must contain distinct named FL/RL/FR/RR wheel fields')
    for name in ('drop_commands', 'silence'):
        if type(fault[name]) is not bool: raise ToolError(f'{name} must be boolean')


def parse(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['start', 'command', 'test', 'fault', 'graph', 'status', 'stop'])
    p.add_argument('--config', type=Path, default=ROOT/'config/settings.yaml')
    p.add_argument('--workspace'); p.add_argument('--domain', type=int); p.add_argument('--namespace')
    ui = p.add_mutually_exclusive_group(); ui.add_argument('--headless', action='store_true'); ui.add_argument('--gui', action='store_true')
    p.add_argument('--pwm-limit', type=int); p.add_argument('--response-time', type=float)
    p.add_argument('--throttle', type=float); p.add_argument('--steering', type=float); p.add_argument('--duration', type=float)
    p.add_argument('--delay-ms', type=float); p.add_argument('--missing', help='comma-separated wheel names; empty string clears')
    p.add_argument('--drop-commands', action=argparse.BooleanOptionalAction, default=None)
    p.add_argument('--silence', action=argparse.BooleanOptionalAction, default=None)
    p.add_argument('--clear', action='store_true'); p.add_argument('--output', type=Path)
    a = p.parse_args(argv)
    allowed = {
        'start': {'headless', 'gui', 'pwm_limit', 'response_time'},
        'command': {'throttle', 'steering', 'duration'},
        'test': {'output', 'pwm_limit', 'response_time'},
        'fault': {'delay_ms', 'missing', 'drop_commands', 'silence', 'clear'},
        'graph': set(), 'status': set(), 'stop': set()}
    for name in set().union(*allowed.values()):
        value = getattr(a, name)
        supplied = value is not None and (name not in {'headless', 'gui', 'clear'} or value)
        if supplied and name not in allowed[a.action]:
            p.error(f'--{name.replace("_", "-")} is not valid for {a.action}')
    config = deepcopy(DEFAULTS)
    try:
        with a.config.expanduser().open() as stream:
            # Duplicate YAML keys are ambiguous and must not silently override values.
            class UniqueLoader(yaml.SafeLoader): pass
            def mapping(loader, node, deep=False):
                pairs = loader.construct_pairs(node, deep=deep); result = {}
                for key, value in pairs:
                    if key in result: raise ToolError(f'Duplicate YAML key: {key}')
                    result[key] = value
                return result
            UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
            merge_strict(config, yaml.load(stream, Loader=UniqueLoader))
    except (OSError, yaml.YAMLError, TypeError) as exc:
        raise ToolError(f'Cannot load settings: {exc}') from exc
    for key in ('workspace', 'domain', 'namespace'):
        if getattr(a, key) is not None: config[key] = getattr(a, key)
    for section, names in [('command', ('throttle', 'steering', 'duration')), ('oc', ('pwm_limit',)),
                           ('simulator', ('response_time',))]:
        for name in names:
            if getattr(a, name) is not None: config[section][name] = getattr(a, name)
    if a.headless: config['gui'] = False
    if a.gui: config['gui'] = True
    if a.action == 'test':
        config['gui'] = False
        if a.domain is None: config['domain'] = 97
        if a.namespace is None: config['namespace'] = '/dcv1_test_'+uuid4().hex[:8]
    return a, validate(config)


def bootstrap(config, argv):
    workspace = Path(config['workspace'])
    if not (workspace/'install/setup.bash').is_file():
        raise ToolError(f'Built ROS workspace not found: {workspace}/install/setup.bash')
    if not Path('/opt/ros/jazzy/setup.bash').is_file(): raise ToolError('ROS Jazzy is required')
    if os.environ.get('DCV1_ENV_READY') != str(workspace):
        env = dict(os.environ, DCV1_ENV_READY=str(workspace))
        env.pop('ROS_STATIC_PEERS', None)
        os.execve('/bin/bash', ['bash', '-c',
            'source /opt/ros/jazzy/setup.bash && source "$1/install/setup.bash" && shift && exec /usr/bin/python3 "$@"',
            'dcv1-bootstrap', str(workspace), str(Path(__file__).resolve()), *argv], env)
    os.environ['ROS_DOMAIN_ID'] = str(config['domain'])
    os.environ['ROS_AUTOMATIC_DISCOVERY_RANGE'] = 'LOCALHOST'
    os.environ.pop('ROS_STATIC_PEERS', None)
    import mini_rover_control
    if not Path(mini_rover_control.__file__).resolve().is_relative_to(workspace):
        raise ToolError(f'Imported control package is outside configured workspace: {mini_rover_control.__file__}')


def atomic_json(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name+'.'+uuid4().hex+'.tmp')
    temp.write_text(json.dumps(value, indent=2, sort_keys=True)+'\n'); os.replace(temp, path)


def key(config):
    return str(config['domain'])+'-'+hashlib.sha256(config['namespace'].encode()).hexdigest()[:16]


def registry(config): return ROOT/'.runtime'/('session-'+key(config)+'.json')


def process_identity(pid):
    try:
        text = Path(f'/proc/{pid}/stat').read_text(); fields = text.rsplit(')', 1)[1].split()
        if fields[0] == 'Z': return None
        return fields[19]
    except (OSError, IndexError): return None


def alive(record): return process_identity(record['pid']) == record['starttime']


@contextmanager
def session_lock(config):
    directory = Path('/tmp')/f'dcv1-locks-{os.getuid()}'
    directory.mkdir(mode=0o700, exist_ok=True)
    if directory.stat().st_uid != os.getuid() or directory.is_symlink(): raise ToolError('Unsafe lock directory')
    with (directory/(key(config)+'.lock')).open('a+') as stream:
        try: fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise ToolError('Another operation is changing this session')
        yield


def load_session(config, required=True):
    path = registry(config)
    if not path.exists():
        if required: raise ToolError('No package-owned session. Run start first.')
        return None
    try: data = json.loads(path.read_text())
    except (ValueError, OSError) as exc: raise ToolError(f'Invalid ownership metadata: {exc}')
    if (data.get('package') != str(ROOT) or data.get('domain') != config['domain']
            or data.get('namespace') != config['namespace']):
        raise ToolError('Ownership metadata does not match this package/session')
    return data


def init_ros():
    import rclpy
    if not rclpy.ok(): rclpy.init(args=[])
    return rclpy


class Observer:
    def __init__(self, namespace):
        from rclpy.node import Node
        from mini_rover_interfaces.msg import ControlState, ChassisState
        from mini_rover_c2.ros_client import qos, plain
        self.ros = init_ros(); self.state = self.chassis = None
        self.state_at = self.chassis_at = 0.
        self.node = Node('dcv1_observer_'+uuid4().hex[:8], namespace=namespace,
                         cli_args=[], use_global_arguments=False)
        def state(m): self.state = plain(m); self.state_at = time.monotonic()
        def chassis(m): self.chassis = plain(m); self.chassis_at = time.monotonic()
        self.node.create_subscription(ControlState, 'control/state', state, qos())
        self.node.create_subscription(ChassisState, 'chassis/state', chassis, qos())
    def wait(self, predicate, timeout=6):
        end = time.monotonic()+timeout
        while time.monotonic()<end:
            self.ros.spin_once(self.node, timeout_sec=.03)
            if predicate(): return
        raise ToolError('Timed out waiting for simulated OC/chassis feedback')
    def close(self): self.node.destroy_node()


def preflight(config):
    previous = load_session(config, required=False)
    if previous and any(alive(p) for p in previous['processes']):
        raise ToolError('This domain/namespace already has a package-owned session; use status or stop')
    probe = Observer(config['namespace'])
    try:
        deadline = time.monotonic()+1.5
        conflicts = []
        while time.monotonic()<deadline:
            probe.ros.spin_once(probe.node, timeout_sec=.05)
            conflicts = [(name, ns) for name, ns in probe.node.get_node_names_and_namespaces()
                         if ns == config['namespace'] and name != probe.node.get_name()]
            if conflicts or probe.state is not None: break
        if conflicts or probe.state is not None:
            raise ToolError(f'Conflicting ROS nodes/state in domain {config["domain"]} '
                            f'namespace {config["namespace"]}: {conflicts}. Nothing was started or stopped.')
    finally: probe.close()


def launch_spec(config, run, session_id):
    sim = ['/usr/bin/python3', '-m', 'mini_rover_sim.simulator', '--port-link', str(run/'arduino.pty'),
           '--fault-file', str(run/'faults.json'), '--response-time', str(config['simulator']['response_time']),
           '--pwm-limit', '63']
    oc = ['/usr/bin/python3', '-c', 'from mini_rover_oc.node import main; main()', '--ros-args',
          '-r', '__ns:='+config['namespace'], '-r', '__node:=oc',
          '-p', 'port:='+str(run/'arduino.pty'), '-p', 'baud:=115200', '-p', 'simulated:=true',
          '-p', 'pwm_limit:='+str(config['oc']['pwm_limit']), '-p', 'log_directory:='+str(run/'oc-runs')]
    gui = ['/usr/bin/python3', str(ROOT/'scripts/gui_session.py'), '--namespace', config['namespace'],
           '--node-name', 'c2_operator']
    return dict(simulator=sim, oc=oc, gui=gui if config['gui'] else None,
                environment=dict(ROS_DOMAIN_ID=str(config['domain']), ROS_AUTOMATIC_DISCOVERY_RANGE='LOCALHOST'),
                settings=config, simulated=True, physical_device_allowed=False)


def spawn(label, argv, run, metadata, config):
    with (run/(label+'.log')).open('ab') as stream:
        child = subprocess.Popen(argv, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
    record = dict(label=label, pid=child.pid, starttime=process_identity(child.pid), argv=argv)
    if record['starttime'] is None: raise ToolError(f'{label} exited immediately; inspect {run}/{label}.log')
    metadata['processes'].append(record); atomic_json(registry(config), metadata)
    return child


def terminate_records(metadata):
    result = []
    for record in reversed(metadata['processes']):
        was_alive = alive(record)
        if was_alive:
            try: os.kill(record['pid'], signal.SIGTERM)
            except ProcessLookupError: pass
            deadline = time.monotonic()+3
            while alive(record) and time.monotonic()<deadline: time.sleep(.03)
            if alive(record): os.kill(record['pid'], signal.SIGKILL)
        result.append(dict(label=record['label'], pid=record['pid'], matched=was_alive, alive=alive(record)))
    return result


def start_session(config, run_directory=None):
    with session_lock(config):
        preflight(config)
        session_id = uuid4().hex
        run = (Path(run_directory) if run_directory else ROOT/'evidence/runs'/session_id).resolve()
        if run.exists() and any(run.iterdir()): raise ToolError(f'Run directory must be empty: {run}')
        run.mkdir(parents=True, exist_ok=True)
        if config['gui'] and not (os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY')):
            raise ToolError('GUI display unavailable; use start --headless')
        spec = launch_spec(config, run, session_id); atomic_json(run/'launch_spec.json', spec)
        # Establish identity while disabled before applying requested transport faults.
        atomic_json(run/'faults.json', DEFAULTS['fault'])
        metadata = dict(format=1, package=str(ROOT), id=session_id, created_utc=datetime.now(timezone.utc).isoformat(),
            domain=config['domain'], namespace=config['namespace'], workspace=config['workspace'],
            run_directory=str(run), port=str(run/'arduino.pty'), fault_file=str(run/'faults.json'),
            config=config, processes=[])
        observer = None
        try:
            spawn('simulator', spec['simulator'], run, metadata, config)
            deadline = time.monotonic()+4
            while not (run/'arduino.pty').is_symlink() and time.monotonic()<deadline: time.sleep(.02)
            if not (run/'arduino.pty').is_symlink() or not str((run/'arduino.pty').readlink()).startswith('/dev/pts/'):
                raise ToolError('Simulator did not create an owned PTY; refusing OC start')
            spawn('oc', spec['oc'], run, metadata, config)
            observer = Observer(config['namespace'])
            observer.wait(lambda: observer.state and observer.state['enabled_known'] and observer.chassis
                and observer.chassis['simulated'] is True)
            if config['gui']:
                spawn('gui', spec['gui'], run, metadata, config)
                gui_record = metadata['processes'][-1]
                observer.wait(lambda: (any(name == 'c2_operator' and ns == config['namespace']
                    for name, ns in observer.node.get_node_names_and_namespaces())
                    and alive(gui_record)))
                # Allow Tk initialization failures to appear before reporting success.
                until = time.monotonic()+.35
                while time.monotonic()<until:
                    if not alive(gui_record): raise ToolError('GUI exited during startup; inspect gui.log')
                    time.sleep(.03)
            set_fault(metadata, config['fault'])
            metadata['startup_faults_applied'] = deepcopy(config['fault'])
            metadata['ready'] = True; atomic_json(registry(config), metadata)
            return metadata
        except BaseException:
            terminate_records(metadata)
            if registry(config).exists(): registry(config).unlink()
            raise
        finally:
            if observer: observer.close()


def stop_session(config):
    with session_lock(config):
        metadata = load_session(config)
        result = terminate_records(metadata)
        current = load_session(config, required=False)
        if current and current['id'] == metadata['id']: registry(config).unlink()
        atomic_json(Path(metadata['run_directory'])/'cleanup.json', dict(processes=result, own_session_only=True))
        return dict(stopped=metadata['id'], processes=result)


def active_session(config):
    metadata = load_session(config)
    required = [p for p in metadata['processes'] if p['label'] in ('oc', 'simulator')]
    if len(required) != 2 or not all(alive(p) for p in required):
        raise ToolError('Owned simulator/OC is not running; inspect status, then stop and start')
    port = Path(metadata['port'])
    if not port.is_symlink() or not str(port.readlink()).startswith('/dev/pts/'):
        raise ToolError('Owned session is not a PTY simulation')
    return metadata


def set_fault(metadata, value):
    validate_fault(value); atomic_json(metadata['fault_file'], value)


def fault_action(args, config):
    with session_lock(config):
        metadata = active_session(config)
        value = deepcopy(DEFAULTS['fault'] if args.clear else config['fault'])
        if not args.clear:
            for field in ('delay_ms', 'drop_commands', 'silence'):
                if getattr(args, field) is not None: value[field] = getattr(args, field)
            if args.missing is not None: value['missing'] = [] if not args.missing else args.missing.split(',')
        set_fault(metadata, value)
        return dict(fault=value, file=metadata['fault_file'], note='Clearing faults never re-enables motion')


def status_action(config):
    metadata = load_session(config, required=False)
    if not metadata: return dict(running=False, domain=config['domain'], namespace=config['namespace'])
    data = dict(session=metadata['id'], domain=metadata['domain'], namespace=metadata['namespace'],
                run_directory=metadata['run_directory'],
                processes=[dict(label=p['label'], pid=p['pid'], alive=alive(p)) for p in metadata['processes']])
    data['running'] = all(p['alive'] for p in data['processes'] if p['label'] in ('oc','simulator'))
    if data['running']:
        observer = Observer(config['namespace'])
        try:
            observer.wait(lambda: observer.state is not None, timeout=2)
            data['control_state'] = observer.state
        finally: observer.close()
    return data


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    args, config = parse(argv); bootstrap(config, argv)
    try:
        if args.action == 'start': result = start_session(config)
        elif args.action == 'stop': result = stop_session(config)
        elif args.action == 'status': result = status_action(config)
        elif args.action == 'fault': result = fault_action(args, config)
        elif args.action == 'command':
            from authority_check import run_command
            result = run_command(config, active_session(config))
        elif args.action == 'test':
            from authority_check import run_tests
            result = run_tests(config, args.output)
        elif args.action == 'graph':
            metadata = active_session(config)
            with session_lock(config):
                if any(p['label']=='graph' and alive(p) for p in metadata['processes']):
                    raise ToolError('An owned graph window is already running')
                spawn('graph', ['/usr/bin/python3', str(ROOT/'scripts/show_graph.py'), '--namespace', config['namespace']],
                      Path(metadata['run_directory']), metadata, config)
            result = dict(graph_started=True, namespace=config['namespace'])
        print(json.dumps(result, indent=2, sort_keys=True))
        if args.action == 'test' and not result['passed']: return 1
        return 0
    finally:
        if 'rclpy' in sys.modules and sys.modules['rclpy'].ok(): sys.modules['rclpy'].shutdown()


if __name__ == '__main__':
    # Shared exception/session types must remain identical when helpers import dcv1.
    sys.modules['dcv1'] = sys.modules[__name__]
    try: sys.exit(main())
    except (ToolError, ValueError, OSError) as exc:
        print(f'dcv1: {exc}', file=sys.stderr); sys.exit(2)
    except KeyboardInterrupt:
        print('dcv1: interrupted', file=sys.stderr); sys.exit(130)
