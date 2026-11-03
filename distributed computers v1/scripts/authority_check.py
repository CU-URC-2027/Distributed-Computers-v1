#!/usr/bin/env python3
"""Simulation-only C2 command and regression driver used by dcv1."""
from copy import deepcopy
from datetime import datetime, timezone
import json
import subprocess
from pathlib import Path
import time
from uuid import uuid4
from dcv1 import (ROOT, DEFAULTS, ToolError, active_session, alive, atomic_json, init_ros,
                 launch_spec, load_session, registry, set_fault, start_session, stop_session, parse)


class Fleet:
    """Explicit command driver; a separate graph observer never calls these methods."""
    def __init__(self, config, directory, count=1):
        from mini_rover_c2.ros_client import C2Node
        from rclpy.executors import SingleThreadedExecutor
        self.ros=init_ros();self.executor=SingleThreadedExecutor()
        self.nodes=[];self.publishing=[];self.last_send=0.
        self.log=(Path(directory)/('c2-'+uuid4().hex[:8]+'.jsonl')).open('w')
        self.last_sample={}
        for i in range(count):
            node=C2Node(namespace=config['namespace'],
                cli_args=['--ros-args','-r','__node:=dcv1_command_'+uuid4().hex[:8]], use_global_arguments=False)
            self.executor.add_node(node);self.nodes.append(node);self.publishing.append(i==0)
        self.a=self.nodes[0]
    def step(self):
        from mini_rover_c2.ros_client import plain
        self.executor.spin_once(timeout_sec=.005)
        now=time.monotonic();send=now-self.last_send>=.05
        for i,node in enumerate(self.nodes):
            node.pump(publish=send and self.publishing[i])
            state=node.controller.state
            if state and state['state_seq']!=self.last_sample.get(i):
                self.last_sample[i]=state['state_seq']
                self.log.write(json.dumps(dict(monotonic=now,client_id=node.client_id,state=state,
                    chassis=plain(node.chassis) if node.chassis else None))+'\n')
        if send:self.last_send=now
    def wait(self,predicate,timeout=5,message='condition'):
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            self.step()
            if predicate():return
        raise ToolError(f'Timed out waiting for {message}; notice={self.a.notice}; state={self.a.controller.state}')
    def run_for(self,seconds):
        end=time.monotonic()+seconds
        while time.monotonic()<end:self.step()
    def ready(self):
        self.wait(lambda:self.a.claim_client.service_is_ready() and self.a.controller.fresh()
            and self.a.controller.state['enabled_known'] and self.a.chassis_fresh(),timeout=8,message='fresh simulator')
        if not self.a.chassis.simulated:raise ToolError('Refusing command: feedback is physical hardware, not simulation')
    def claim(self):
        self.ready()
        state=self.a.controller.state
        if state['session_id'] and state['session_id']!=self.a.controller.session_id:
            raise ToolError('Another C2 (possibly the GUI) owns this OC. Disarm and release its claim; no takeover attempted.')
        self.a.connect();self.wait(lambda:not self.a.pending,message='claim response')
        if not self.a.controller.session_id:raise ToolError('C2 claim refused: '+self.a.notice)
        self.wait(lambda:self.a.controller.state and self.a.controller.state['session_id']==self.a.controller.session_id
            and self.a.controller.state['last_accepted_command_id']>=self.a.controller.claim_ack_min,message='fresh neutral acknowledgment')
    def enable(self):
        if not self.a.controller.session_id:self.claim()
        self.a.operation('enable')
        self.wait(lambda:not self.a.pending and self.a.controller.state['actual_enabled']
            and self.a.controller.state['oc_authorized'],message='MCU-confirmed enable')
    def drive(self,throttle,steering,pwm_limit):
        from mini_rover_control.contracts import Config
        self.enable();self.a.controller.apply(throttle,steering);self.a.controller.begin_hold()
        expected=Config(pwm_limit=pwm_limit).targets(throttle,steering)[1]
        self.wait(lambda:self.a.chassis_fresh() and self.a.chassis.simulated
            and self.a.chassis.source_session==self.a.controller.session_id
            and self.a.chassis.applied_left_pwm==expected['left']
            and self.a.chassis.applied_right_pwm==expected['right'],message='correlated applied PWM')
        return expected
    def release(self):
        self.a.controller.release()
        self.wait(lambda:self.a.chassis_fresh() and self.a.chassis.applied_left_pwm==0
            and self.a.chassis.applied_right_pwm==0,message='zero PWM after hold release')
    def disarm(self):
        if self.a.controller.session_id:
            self.a.operation('disable')
            self.wait(lambda:not self.a.pending and self.a.controller.state
                      and not self.a.controller.state['actual_enabled'],message='confirmed disarm')
    def close(self):
        for node in self.nodes:
            node.controller.disarm()
            state=node.controller.state
            if state and node.controller.session_id and state['session_id']==node.controller.session_id:
                try:node.operation('disable');node.pump()
                except (ValueError,RuntimeError):pass
        self.executor.shutdown(timeout_sec=1)
        for node in self.nodes:node.destroy_node()
        self.log.close()


def run_command(config,metadata):
    fleet=Fleet(config,metadata['run_directory']);result=None
    try:
        fleet.claim()
        cmd=config['command'];limit=metadata['config']['oc']['pwm_limit']
        expected=fleet.drive(cmd['throttle'],cmd['steering'],limit)
        fleet.run_for(cmd['duration'])
        if not fleet.a.controller.state['actual_enabled'] or not fleet.a.controller.holding:
            raise ToolError('Command lost authority before requested duration')
        sample=dict(source_session=fleet.a.chassis.source_session,
                    last_applied_command_id=fleet.a.chassis.last_applied_command_id,
                    last_applied_seq=fleet.a.chassis.last_applied_seq)
        fleet.release();fleet.disarm()
        result=dict(passed=True,simulated=True,namespace=config['namespace'],domain=config['domain'],
            requested=cmd,active_pwm_limit=limit,applied_pwm=expected,application=sample,
            final_enabled=fleet.a.controller.state['actual_enabled'],
            note='Simulator output application; not evidence of physical motor movement')
        atomic_json(Path(metadata['run_directory'])/('command-'+uuid4().hex[:8]+'.json'),result)
        return result
    finally:fleet.close()


def run_tests(config,output=None):
    directory=(Path(output).expanduser() if output else ROOT/'evidence/tests'/uuid4().hex).resolve()
    directory.mkdir(parents=True,exist_ok=True)
    if any(directory.iterdir()):raise ToolError(f'Test output must be empty: {directory}')
    result=dict(passed=False,simulated=True,started_utc=datetime.now(timezone.utc).isoformat(),checks=[],
                domain=config['domain'],namespace=config['namespace'],output=str(directory))
    owned=[];fleet=None
    def record(name,**evidence):
        result['checks'].append(dict(name=name,passed=True,**evidence))
        atomic_json(directory/'result.json',result)
    def launch(name,cfg):
        metadata=start_session(cfg,directory/name);owned.append((deepcopy(cfg),metadata));return metadata
    def stop_owned(cfg,metadata):
        current=load_session(cfg,required=False)
        if current and current['id']==metadata['id']:return stop_session(cfg)
        raise ToolError('Test ownership changed; refusing cleanup of another session')
    try:
        # Parse settings without launching ROS processes. Record meaningful boundary cases.
        valid_cases=[['command','--duration','3600'],['command','--throttle','-1','--steering','1'],
                     ['start','--response-time','20','--pwm-limit','1']]
        invalid_cases=[['command','--duration','3600.1'],['command','--duration','0'],
                       ['command','--throttle','nan'],['command','--steering','1.01'],
                       ['start','--pwm-limit','64'],['start','--response-time','inf'],
                       ['start','--response-time','0']]
        for args in valid_cases:parse(args)
        for args in invalid_cases:
            try:parse(args)
            except ToolError:pass
            else:raise AssertionError('Invalid settings accepted: '+str(args))
        for name,text in [('unknown','watchdog: 2\n'),('duplicate','domain: 97\ndomain: 98\n')]:
            path=directory/(name+'.yaml');path.write_text(text)
            try:parse(['start','--config',str(path)])
            except ToolError:pass
            else:raise AssertionError('Bad YAML accepted: '+name)
        record('settings_bounds_unknown_and_duplicate_keys',valid_cases=valid_cases,rejected_cases=invalid_cases)
        healthy=deepcopy(DEFAULTS['fault']);base=deepcopy(config);base['fault']=healthy;base['gui']=False
        sentinel=deepcopy(base);sentinel['namespace']=base['namespace']+'_unrelated'
        unrelated=launch('unrelated-session',sentinel)
        metadata=launch('normal-session',base)
        spec=json.loads((Path(metadata['run_directory'])/'launch_spec.json').read_text())
        assert spec['simulator'][spec['simulator'].index('--response-time')+1]==str(base['simulator']['response_time'])
        assert 'pwm_limit:='+str(base['oc']['pwm_limit']) in spec['oc']
        assert spec['simulator'][spec['simulator'].index('--pwm-limit')+1]=='63'
        record('normal_launch_parameters',launch_spec=str(Path(metadata['run_directory'])/'launch_spec.json'))
        # Reject duplicate namespace without disrupting the already-running OC.
        try:start_session(base,directory/'should-not-start')
        except ToolError:pass
        else:raise AssertionError('Conflicting start unexpectedly succeeded')
        assert all(alive(p) for p in metadata['processes'])
        record('conflicting_start_refused_without_killing_owner')
        fleet=Fleet(base,metadata['run_directory'],count=2);fleet.claim();a,b=fleet.nodes
        b.connect();fleet.wait(lambda:not b.pending,message='competing claim refusal')
        assert not b.controller.session_id and 'owned' in b.notice.lower()
        record('competing_c2_claim_refused')
        from mini_rover_interfaces.srv import Control
        from mini_rover_control.contracts import VERSION,MODE
        state=b.controller.state
        request=Control.Request(protocol_version=VERSION,mode=MODE,session_id=b.client_id,request_id=uuid4().hex,
            oc_boot_id=state['boot_id'],generation=state['generation'],challenge_seq=state['challenge_seq'],enabled=True)
        future=b.control_clients['set_enabled'].call_async(request)
        fleet.wait(lambda:future.done(),message='nonowner enable refusal');assert not future.result().accepted
        record('nonowner_enable_refused')
        # Run a real competing CLI while this owner continues publishing its lease.
        with (directory/'competing-cli.stdout.log').open('w') as out, (directory/'competing-cli.stderr.log').open('w') as err:
            cli=subprocess.Popen([str(ROOT/'scripts/dcv1'),'command','--domain',str(base['domain']),
                '--namespace',base['namespace'],'--duration','.2'],stdout=out,stderr=err)
            end=time.monotonic()+10
            while cli.poll() is None and time.monotonic()<end:fleet.step()
            if cli.poll() is None:
                cli.terminate();cli.wait(timeout=3);raise ToolError('Competing CLI did not finish')
        rejection=(directory/'competing-cli.stderr.log').read_text()
        assert cli.returncode==2 and 'owns this OC' in rejection
        assert a.controller.session_id and a.controller.state['session_id']==a.controller.session_id
        record('competing_cli_command_refused_without_takeover',exit_code=cli.returncode)
        expected=fleet.drive(config['command']['throttle'],config['command']['steering'],base['oc']['pwm_limit'])
        fleet.run_for(.35)
        record('normal_command_and_simulated_feedback',applied_pwm=expected,command_id=a.chassis.last_applied_command_id)
        fleet.release();assert a.controller.state['actual_enabled']
        record('hold_release_zero_while_armed')
        a.operation('stop');fleet.wait(lambda:not a.pending and a.controller.state['stop_latched'],message='STOP')
        assert not a.controller.state['actual_enabled'];record('stop_latched_disabled')
        a.operation('reset');fleet.wait(lambda:not a.pending and not a.reset_barrier
            and not a.controller.state['stop_latched'],message='reset STOP')
        assert not a.controller.state['actual_enabled'];record('reset_stays_disabled')
        fleet.drive(.2,-.25,base['oc']['pwm_limit'])
        old_generation=a.controller.state['generation'];fleet.publishing[0]=False
        fleet.wait(lambda:a.controller.state['session_id']=='' and not a.controller.state['actual_enabled'],message='C2 timeout')
        assert a.controller.state['generation']>old_generation;record('c2_timeout_revokes_authority')
        fleet.publishing[0]=True;fleet.drive(.2,-.25,base['oc']['pwm_limit'])
        faults=deepcopy(healthy);faults['missing']=['rear_right'];set_fault(metadata,faults)
        fleet.wait(lambda:a.chassis and not a.chassis.rear_right.measurement_valid and
            any(f['severity']=='WARNING' for f in a.controller.state['faults']),message='missing encoder warning')
        assert a.controller.state['actual_enabled'];record('missing_encoder_warns_without_stopping_open_loop')
        set_fault(metadata,healthy);fleet.run_for(.3)
        for name,patch in [('delayed_feedback',{'delay_ms':400}),('dropped_commands',{'drop_commands':True}),
                           ('silent_feedback',{'silence':True})]:
            if not a.controller.state['actual_enabled']:fleet.drive(.2,-.25,base['oc']['pwm_limit'])
            faults=deepcopy(healthy);faults.update(patch);set_fault(metadata,faults)
            fleet.wait(lambda:not a.controller.state['actual_enabled'],timeout=4,message=name+' disable')
            set_fault(metadata,healthy)
            fleet.wait(lambda:a.controller.state['enabled_known'] and a.chassis_fresh(),timeout=5,message=name+' recovery')
            fleet.run_for(.45);assert not a.controller.state['actual_enabled']
            record(name+'_disables_and_clear_does_not_reenable')
        fleet.close();fleet=None
        cleanup=stop_owned(base,metadata)
        assert all(not p['alive'] for p in cleanup['processes'])
        assert all(alive(p) for p in unrelated['processes'])
        record('cleanup_leaves_unrelated_session_running',unrelated_pids=[p['pid'] for p in unrelated['processes']])
        changed=deepcopy(base);changed['namespace']=base['namespace']+'_changed'
        changed['oc']['pwm_limit']=31 if base['oc']['pwm_limit']!=31 else 17
        changed['simulator']['response_time']=.5 if base['simulator']['response_time']!=.5 else .8
        variant=launch('changed-parameters',changed)
        spec=json.loads((Path(variant['run_directory'])/'launch_spec.json').read_text())
        assert spec['simulator'][spec['simulator'].index('--response-time')+1]==str(changed['simulator']['response_time'])
        assert 'pwm_limit:='+str(changed['oc']['pwm_limit']) in spec['oc']
        fleet=Fleet(changed,variant['run_directory']);fleet.claim()
        expected=fleet.drive(.2,-.25,changed['oc']['pwm_limit']);fleet.run_for(.3)
        fleet.release();fleet.disarm();fleet.close();fleet=None
        record('changed_pwm_and_response_time',pwm_limit=changed['oc']['pwm_limit'],
               response_time=changed['simulator']['response_time'],applied_pwm=expected,
               launch_spec=str(Path(variant['run_directory'])/'launch_spec.json'))
        # Observe the configured response on fresh simulator processes at identical PWM.
        pulse_results=[]
        for label,response in [('fast',.05),('slow',1.5)]:
            cfg=deepcopy(base);cfg['namespace']=base['namespace']+'_'+label
            cfg['simulator']['response_time']=response;cfg['oc']['pwm_limit']=31
            plant=launch('response-'+label,cfg)
            fleet=Fleet(cfg,plant['run_directory']);fleet.claim()
            fleet.drive(1.,0.,31)
            before=fleet.a.chassis.front_left.raw_count;sample_before=fleet.a.chassis.sample_seq
            started=time.monotonic();fleet.run_for(.6)
            elapsed=time.monotonic()-started;after=fleet.a.chassis.front_left.raw_count
            pulse_results.append(dict(response_time=response,elapsed_seconds=elapsed,
                synthetic_pulse_increment=(after-before)&0xffffffff,
                sample_seq_start=sample_before,sample_seq_end=fleet.a.chassis.sample_seq))
            fleet.release();fleet.disarm();fleet.close();fleet=None
            stop_owned(cfg,plant)
        assert pulse_results[0]['synthetic_pulse_increment']>pulse_results[1]['synthetic_pulse_increment']+10
        record('response_time_changes_observed_synthetic_pulse_response',observations=pulse_results,
               note='Illustrative unsigned simulated pulses; no calibrated physical speed claim')
        result['passed']=True
    except Exception as exc:
        result['error']=f'{type(exc).__name__}: {exc}'
    finally:
        if fleet:fleet.close()
        cleanup=[]
        for cfg,metadata in reversed(owned):
            try:
                current=load_session(cfg,required=False)
                if current and current['id']==metadata['id']:cleanup.append(stop_owned(cfg,metadata))
            except Exception as exc:cleanup.append(dict(error=str(exc)));result['passed']=False
        result['cleanup']=cleanup
        result['completed_utc']=datetime.now(timezone.utc).isoformat()
        atomic_json(directory/'result.json',result)
    return result
