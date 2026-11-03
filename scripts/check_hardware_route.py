#!/usr/bin/env python3
"""Verify the actual USB→OC→C2 route. Default: neutral/disabled monitoring only.

Optional control checks require the explicit motor-supply-disconnected declaration.
They verify software acknowledgments, never electrical waveforms or motor motion.
"""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import rclpy
from mini_rover_c2.ros_client import C2Node


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',required=True)
    parser.add_argument('--duration',type=float,default=10.)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--unpowered-control-checks',action='store_true')
    parser.add_argument('--motor-supply-disconnected',action='store_true')
    args=parser.parse_args()
    if not args.port.startswith('/dev/serial/by-id/') or not Path(args.port).exists():
        parser.error('Specify an existing stable /dev/serial/by-id/ path for the verified v2 board')
    if args.unpowered_control_checks and not args.motor_supply_disconnected:
        parser.error('Unpowered control checks require --motor-supply-disconnected')
    if not 2<=args.duration<=60:parser.error('duration must be between 2 and 60 seconds')
    args.output.mkdir(parents=True,exist_ok=True)
    env=dict(os.environ,ROS_DOMAIN_ID='92',ROS_AUTOMATIC_DISCOVERY_RANGE='LOCALHOST')
    os.environ.update(ROS_DOMAIN_ID='92',ROS_AUTOMATIC_DISCOVERY_RANGE='LOCALHOST')
    namespace='/rover_board_check'
    log=(args.output/'oc.log').open('w')
    process=subprocess.Popen([sys.executable,'-c','from mini_rover_oc.node import main; main()',
        '--ros-args','-r','__ns:='+namespace,'-p','port:='+args.port,'-p','simulated:=false',
        '-p','log_directory:='+str(args.output/'runs')],env=env,stdout=log,stderr=subprocess.STDOUT,
        start_new_session=True)
    rclpy.init(args=[]);node=C2Node(namespace=namespace)
    last_send=0.;states={};samples={};events=[]
    def step(publish=True):
        nonlocal last_send
        rclpy.spin_once(node,timeout_sec=.005)
        now=time.monotonic();send=publish and now-last_send>=.05
        node.pump(publish=send)
        if send:last_send=now
        s=node.controller.state
        if s:states[(s['boot_id'],s['state_seq'])]=s
        m=node.chassis
        if m:samples[(m.arduino_boot_id,m.sample_seq)]={
            'sample_seq':m.sample_seq,'boot_id':m.arduino_boot_id,'simulated':m.simulated,
            'source_session':m.source_session,'last_applied_command_id':m.last_applied_command_id,
            'last_applied_seq':m.last_applied_seq,'left':m.applied_left_pwm,'right':m.applied_right_pwm,
            'valid':{w:getattr(m,w).measurement_valid for w in ('front_left','rear_left','front_right','rear_right')}}
    def wait(predicate,timeout=5.):
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            step()
            if predicate():return
            if process.poll() is not None:raise RuntimeError('OC process exited; inspect oc.log')
        raise RuntimeError('Timed out: '+node.notice+'; state='+str(node.controller.state))
    def event(name):
        events.append(dict(name=name,monotonic=time.monotonic(),state=node.controller.state))
    def enable():
        node.operation('enable')
        wait(lambda:not node.pending and node.controller.state['actual_enabled'])
    result=dict(port=args.port,motor_supply_disconnected=args.motor_supply_disconnected,
        control_checks_requested=args.unpowered_control_checks,physical_motion_verified=False,
        electrical_outputs_measured=False,passed=False)
    try:
        wait(lambda:node.claim_client.service_is_ready() and node.controller.fresh()
             and node.controller.state['enabled_known'],timeout=8)
        wait(lambda:node.chassis is not None and not node.chassis.simulated)
        if node.controller.state['actual_enabled']:raise RuntimeError('Unexpected enabled state at initial connection')
        node.connect()
        wait(lambda:bool(node.controller.session_id) and not node.pending)
        wait(lambda:node.controller.state is not None and
             node.controller.state['session_id']==node.controller.session_id and
             node.controller.state['last_accepted_command_id']>=node.controller.claim_ack_min)
        event('claimed_neutral_disabled')
        start=time.monotonic()
        while time.monotonic()-start<args.duration:
            step()
            s=node.controller.state
            if not node.controller.fresh() or not s['enabled_known'] or s['actual_enabled']:
                raise RuntimeError('Disabled monitoring lost expected fresh disabled state')
        event('stable_disabled_monitoring')
        if args.unpowered_control_checks:
            enable();event('neutral_enable_confirmed')
            for name,t,s,left,right in [('forward',.25,0,16,16),('reverse',-.25,0,-16,-16),
                                      ('left',0,.25,-16,16),('right',0,-.25,16,-16)]:
                node.controller.apply(t,s);node.controller.begin_hold()
                wait(lambda:node.chassis.applied_left_pwm==left and node.chassis.applied_right_pwm==right
                     and node.controller.state['last_applied_command_id']>0)
                event(name+'_applied_unpowered')
                node.controller.release()
                wait(lambda:not node.chassis.applied_left_pwm and not node.chassis.applied_right_pwm)
            node.operation('stop');wait(lambda:not node.pending and node.controller.state['stop_latched'])
            event('stop_confirmed')
            node.operation('reset');wait(lambda:not node.pending and not node.controller.state['stop_latched'])
            if node.controller.state['actual_enabled']:raise RuntimeError('Reset unexpectedly enabled')
            event('reset_disabled_confirmed')
            enable();node.controller.apply(.25,0);node.controller.begin_hold()
            wait(lambda:node.chassis.applied_left_pwm==16)
            end=time.monotonic()+.7
            while time.monotonic()<end:step(publish=False)
            if node.controller.state['actual_enabled']:raise RuntimeError('C2 silence did not disable')
            event('c2_silence_disabled')
        result.update(passed=True,state_samples=len(states),mcu_samples=len(samples),
                      final_state=node.controller.state,events=events)
    except Exception as exc:
        result.update(error=str(exc),events=events)
        raise
    finally:
        try:
            if node.controller.session_id:node.operation('disable')
            node.controller.disarm()
            for _ in range(10):step()
        finally:
            node.destroy_node();rclpy.shutdown()
            if process.poll() is None:
                os.killpg(process.pid,signal.SIGTERM)
                try:process.wait(timeout=3)
                except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL);process.wait(timeout=3)
            log.close()
            (args.output/'result.json').write_text(json.dumps(result,indent=2)+'\n')
            (args.output/'c2-state.jsonl').write_text(''.join(json.dumps(s)+'\n' for s in states.values()))
            (args.output/'c2-samples.jsonl').write_text(''.join(json.dumps(s)+'\n' for s in samples.values()))
    print(json.dumps({k:v for k,v in result.items() if k not in ('final_state','events')},indent=2))


if __name__=='__main__':main()
