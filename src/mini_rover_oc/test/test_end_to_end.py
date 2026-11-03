"""Real DDS + separate OC process + separate PTY Arduino, without physical hardware."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from uuid import uuid4

import pytest
import rclpy
from mini_rover_c2.ros_client import C2Node
from mini_rover_interfaces.msg import ChassisState
from mini_rover_control.protocol import Decoder
from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy
from rclpy.duration import Duration
import serial


class Demo:
    def __init__(self, directory):
        self.directory = directory
        self.path = directory/'port'
        self.faults = directory/'faults.json'
        self.namespace = '/test_'+uuid4().hex[:8]
        self.processes = []
        self.start_sim()
        self.oc = self.start('oc', ['-c', 'from mini_rover_oc.node import main; main()',
                                   '--ros-args', '-r', '__ns:='+self.namespace,
                                   '-p', 'port:='+str(self.path), '-p', 'log_directory:='+str(directory/'runs')])
        self.node = C2Node(namespace=self.namespace)
        self.samples = []
        self.sub = self.node.create_subscription(ChassisState, 'chassis/state',
                       lambda m: self.samples.append((time.monotonic(), m)),
                       QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT,
                                  durability=DurabilityPolicy.VOLATILE, lifespan=Duration(seconds=.25)))
        self.last_send = 0.
        self.wait(lambda: self.node.claim_client.service_is_ready() and self.node.controller.fresh()
                  and self.node.controller.state['enabled_known'], timeout=8)

    def start(self, label, args):
        out=(self.directory/(label+'-'+uuid4().hex[:4]+'.log')).open('w')
        p=subprocess.Popen([sys.executable]+args, stdout=out, stderr=subprocess.STDOUT,
                           start_new_session=True)
        self.processes.append((p,out))
        return p

    def start_sim(self):
        self.sim=self.start('sim', ['-m','mini_rover_sim.simulator','--port-link',str(self.path),
                                    '--fault-file',str(self.faults)])

    def stop(self,p):
        if p.poll() is None:
            os.killpg(p.pid,signal.SIGTERM)
            try:p.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(p.pid,signal.SIGKILL);p.wait(timeout=3)

    def step(self, publish=True):
        rclpy.spin_once(self.node, timeout_sec=.005)
        now=time.monotonic()
        send=publish and now-self.last_send>=.05
        self.node.pump(publish=send)
        if send:self.last_send=now

    def wait(self, predicate, timeout=4, publish=True):
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            self.step(publish)
            if predicate():return
        logs='\n'.join(p.read_text()[-3000:] for p in self.directory.glob('*.log'))
        raise AssertionError(f'Timed out: {self.node.notice}; state={self.node.controller.state}\n{logs}')

    def run_for(self, seconds, publish=True):
        end=time.monotonic()+seconds
        while time.monotonic()<end:self.step(publish)

    def enable(self):
        self.node.connect()
        self.wait(lambda: bool(self.node.controller.session_id) and not self.node.pending)
        self.wait(lambda: self.node.controller.state and self.node.controller.state['session_id']==self.node.controller.session_id and self.node.controller.state['last_accepted_command_id']>=self.node.controller.claim_ack_min)
        self.node.operation('enable')
        self.wait(lambda: not self.node.pending and self.node.controller.state['actual_enabled'])
        assert self.node.controller.enable_requested

    def drive(self, throttle=.5, steering=0.):
        self.enable()
        self.node.controller.apply(throttle,steering)
        self.node.controller.begin_hold()
        self.wait(lambda: self.node.chassis and self.node.chassis.applied_left_pwm!=0)

    def close(self):
        if getattr(self, 'node', None):self.node.destroy_node()
        for p,out in reversed(self.processes):
            self.stop(p);out.close()


@pytest.fixture
def demo(tmp_path):
    rclpy.init(args=[])
    d=None
    try:
        d=Demo.__new__(Demo)
        Demo.__init__(d,tmp_path)
        yield d
    finally:
        if d:d.close()
        rclpy.shutdown()


def test_drive_feedback_pwm_identity_provenance_rate_and_log(demo):
    d=demo;d.drive(.2,.4);d.run_for(1.)
    m=d.node.chassis
    assert m.protocol_version==2 and m.mode=='OPEN_LOOP_PWM' and m.simulated
    assert (m.applied_left_pwm,m.applied_right_pwm)==(-13,38)
    assert m.last_applied_command_id>0 and m.last_applied_seq>0
    assert m.source_session==d.node.controller.session_id
    assert not m.front_left.direction_valid
    state=d.node.controller.state
    assert state['oc_authorized'] and state['actual_enabled']
    assert state['limited_left_pwm']==-13 and state['limited_right_pwm']==38
    assert state['last_applied_command_id']<=state['last_accepted_command_id']
    recent=[stamp for stamp,_ in d.samples if stamp>time.monotonic()-1.]
    assert 7<=len(recent)<=24
    lines=[json.loads(l) for p in (d.directory/'runs').glob('*.jsonl') for l in p.read_text().splitlines()]
    assert {'configuration','intent','operation','control_state','arduino_feedback'} <= {r['kind'] for r in lines}


def test_hold_release_zero_remains_armed_focus_disarm_separate(demo):
    d=demo;d.drive()
    d.node.controller.release()
    d.wait(lambda:d.node.chassis.applied_left_pwm==0 and d.node.chassis.applied_right_pwm==0)
    assert d.node.controller.state['actual_enabled']
    d.node.operation('disable')
    d.wait(lambda:not d.node.controller.state['actual_enabled'])


def test_c2_silence_revokes_generation_reclaim_preserves_process_identity(demo):
    d=demo;d.drive()
    session=d.node.controller.session_id
    command=d.node.controller.command_id
    generation=d.node.controller.state['generation']
    d.run_for(.55,publish=False)
    assert not d.node.controller.state['actual_enabled']
    assert d.node.controller.state['session_id']==''
    d.run_for(.3)
    assert not d.node.controller.state['actual_enabled']
    d.enable()
    assert d.node.controller.session_id==session
    assert d.node.controller.command_id>command
    assert d.node.controller.state['generation']>generation
    assert d.node.chassis.applied_left_pwm==0


def test_stop_reset_encoder_unavailable_is_warning_and_does_not_stop_pwm(demo):
    d=demo;d.drive()
    d.node.operation('stop')
    d.wait(lambda:not d.node.pending and d.node.controller.state['stop_latched'])
    assert not d.node.controller.state['actual_enabled']
    d.node.operation('reset')
    d.wait(lambda:not d.node.pending and not d.node.controller.state['stop_latched'])
    assert not d.node.controller.state['actual_enabled']
    d.node.operation('enable')
    d.wait(lambda:not d.node.pending and d.node.controller.state['actual_enabled'])
    d.node.controller.apply(.5,0);d.node.controller.begin_hold()
    d.faults.write_text(json.dumps({'missing':['rear_right']}))
    d.wait(lambda:not d.node.chassis.rear_right.measurement_valid)
    assert d.node.controller.state['actual_enabled']
    assert d.node.chassis.applied_left_pwm==32
    d.wait(lambda:any(f['severity']=='WARNING' for f in d.node.controller.state['faults']))


def test_feedback_silence_no_republish_then_recovery_disabled(demo):
    d=demo;d.drive();d.faults.write_text('{"silence":true}')
    d.run_for(.6);count=len(d.samples)
    assert not d.node.controller.state['enabled_known']
    d.run_for(.3);assert len(d.samples)==count
    d.faults.write_text('{}')
    d.wait(lambda:d.node.controller.state['enabled_known'])
    assert not d.node.controller.state['actual_enabled']


def test_arduino_restart_reconnects_new_boot_disabled(demo):
    d=demo;d.drive();boot=d.node.chassis.arduino_boot_id
    d.stop(d.sim);d.wait(lambda:not d.node.controller.state['enabled_known'])
    d.start_sim()
    d.wait(lambda:d.node.controller.state['enabled_known'] and d.node.chassis.arduino_boot_id!=boot,timeout=7)
    assert not d.node.controller.state['actual_enabled']
    d.enable()


def test_oc_process_death_independent_arduino_watchdog(demo):
    d=demo;d.drive()
    os.killpg(d.oc.pid,signal.SIGKILL);d.oc.wait(timeout=3)
    time.sleep(.4)
    dec=Decoder();feedback=[]
    with serial.Serial(str(d.path),115200,timeout=.05) as port:
        port.reset_input_buffer();end=time.monotonic()+1
        while time.monotonic()<end and not feedback:feedback.extend(dec.feed(port.read(2048)))
    assert feedback and not feedback[-1]['enabled']
    assert feedback[-1]['reason']=='COMMAND_TIMEOUT'
    assert not any(feedback[-1]['applied'].values())


def test_delayed_feedback_tokens_cannot_keep_motion_alive(demo):
    d=demo;d.drive();d.faults.write_text('{"delay_ms":400}')
    d.run_for(1.)
    assert not d.node.controller.state['actual_enabled']
    d.faults.write_text('{}');d.run_for(.7)
    assert not d.node.controller.state['actual_enabled']
    d.enable()


def test_oc_restart_changes_authority_and_never_restores_motion(demo):
    d=demo;d.drive();boot=d.node.controller.state['boot_id']
    d.stop(d.oc);d.run_for(.45)
    assert not d.node.controller.enable_requested
    d.oc=d.start('oc-restart',['-c','from mini_rover_oc.node import main; main()',
        '--ros-args','-r','__ns:='+d.namespace,'-p','port:='+str(d.path),
        '-p','log_directory:='+str(d.directory/'runs')])
    d.wait(lambda:d.node.controller.state['boot_id']!=boot and d.node.controller.state['enabled_known'],timeout=7)
    assert not d.node.controller.state['actual_enabled']


@pytest.fixture
def manual_gui(demo):
    import argparse
    import tkinter as tk
    from mini_rover_c2.gui import RosStation
    d=demo;root=tk.Tk();root.withdraw()
    app=RosStation(root,argparse.Namespace(xbox=False),d.node)
    root.tk.eval('rename focus original_focus')
    root.tk.eval('proc focus {args} {return $::test_focus}')
    root.tk.setvar('test_focus','.')
    errors=[];root.report_callback_exception=lambda *v:errors.append(v)
    root.update();original_step=d.step
    def gui_step(publish=True):
        original_step(publish=False)
        root.update()
    d.step=gui_step
    try:yield app,root,errors
    finally:
        d.step=original_step
        if not app._closing:app.close()
        assert not errors,errors


def test_real_gui_hold_release_focus_and_labels(demo,manual_gui):
    d=demo;app,root,errors=manual_gui
    d.enable()
    app.numeric_throttle.set('0.5');app.numeric_steering.set('0.0')
    app.hold_start()
    d.wait(lambda:d.node.chassis.applied_left_pwm==32)
    app.hold_end()
    d.wait(lambda:d.node.chassis.applied_left_pwm==0)
    assert d.node.controller.state['actual_enabled']
    app.hold_start();d.wait(lambda:d.node.chassis.applied_left_pwm==32)
    root.tk.setvar('test_focus','');app.focus_out(None)
    d.wait(lambda:not d.node.controller.state['actual_enabled'])
    assert not d.node.controller.holding
    assert not errors


def test_hardware_launch_builds_only_oc_and_requires_explicit_stable_port():
    import importlib.util
    from launch import LaunchContext
    path=Path('/home/liamolczak/weeny_ws/src/mini_rover_oc/launch/hardware.launch.py')
    spec=importlib.util.spec_from_file_location('hardware_launch',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    context=LaunchContext()
    context.launch_configurations.update(port='/dev/serial/by-id/test-v2',namespace='rover1',gui='false')
    nodes=module.hardware_nodes(context)
    assert [(n.node_package,n.node_executable) for n in nodes]==[('mini_rover_oc','oc')]
    for bad in ('','/tmp/mini-rover-arduino','/dev/ttyACM0'):
        context.launch_configurations['port']=bad
        with pytest.raises(ValueError):module.hardware_nodes(context)
