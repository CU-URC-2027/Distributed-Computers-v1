#!/usr/bin/env python3
"""Optional desktop tooling verification; isolated domain and only owned sessions."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import time
from uuid import uuid4
import yaml

ROOT=Path(__file__).resolve().parents[1]

def main():
    output=ROOT/'evidence/tests'/('tooling-'+uuid4().hex[:8]);output.mkdir(parents=True)
    ns='/dcv1_smoke_'+uuid4().hex[:8];domain=97
    checks=[];owned=False;success=False
    base=['--domain',str(domain),'--namespace',ns]
    def call(action,*flags):
        process=subprocess.run([str(ROOT/'scripts/dcv1'),action,*base,*flags],text=True,capture_output=True,timeout=20)
        (output/(action+'-'+str(len(list(output.iterdir())))+'.stdout.log')).write_text(process.stdout)
        if process.returncode:raise RuntimeError(process.stderr or process.stdout)
        return json.loads(process.stdout)
    def record(name,**details):checks.append(dict(name=name,passed=True,**details))
    try:
        meta=call('start');owned=True
        time.sleep(1)
        state=call('status')
        assert all(p['alive'] for p in state['processes'])
        record('gui_start_and_liveness',processes=state['processes'],namespace=ns,domain=domain)
        command=call('command','--throttle','-0.4','--steering','0.15','--duration','.35')
        assert command['applied_pwm']==dict(left=-35,right=-16) and command['final_enabled'] is False
        record('command_cli_values_override_yaml',result=command)
        conf=yaml.safe_load((ROOT/'config/settings.yaml').read_text())
        conf['fault'].update(delay_ms=25,missing=['rear_left'])
        settings=output/'custom-settings.yaml';settings.write_text(yaml.safe_dump(conf,sort_keys=False))
        result=call('fault','--config',str(settings),'--missing','front_right','--delay-ms','0')
        assert result['fault']==dict(delay_ms=0,missing=['front_right'],drop_commands=False,silence=False)
        record('fault_cli_overrides_yaml_baseline',result=result)
        result=call('fault','--config',str(settings))
        assert result['fault']==conf['fault']
        record('fault_without_flags_reapplies_yaml',result=result)
        result=call('fault','--clear','--config',str(settings))
        assert result['fault']==dict(delay_ms=0,missing=[],drop_commands=False,silence=False)
        state=call('status');assert not state['control_state']['actual_enabled']
        record('clear_ignores_yaml_faults_and_keeps_disabled')
        call('graph');time.sleep(1.5)
        state=call('status');assert any(p['label']=='graph' and p['alive'] for p in state['processes'])
        record('graph_window_process_running',processes=state['processes'])
        stopped=call('stop');owned=False
        assert all(not p['alive'] for p in stopped['processes'])
        record('owned_gui_graph_oc_simulator_shutdown',result=stopped)
        # Severe transport faults must be valid startup configurations, applied after identity handshake.
        conf['gui']=False;conf['fault']=dict(delay_ms=500,missing=[],drop_commands=True,silence=True)
        settings.write_text(yaml.safe_dump(conf,sort_keys=False))
        meta=call('start','--config',str(settings));owned=True
        assert meta['startup_faults_applied']==conf['fault']
        time.sleep(.5);state=call('status')
        assert not state['control_state']['actual_enabled']
        record('faulted_startup_after_disabled_handshake',faults=meta['startup_faults_applied'])
        stopped=call('stop');owned=False
        assert all(not p['alive'] for p in stopped['processes'])
        success=True
    finally:
        if owned:
            try:call('stop')
            except Exception as exc:record('cleanup_error',error=str(exc));success=False
        result=dict(passed=success,checks=checks,completed_utc=datetime.now(timezone.utc).isoformat(),output=str(output))
        (output/'result.json').write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps(result,indent=2))
    return 0 if success else 1

if __name__=='__main__':raise SystemExit(main())
