"""Transport/logging regressions retained across the v1→v2 migration.
Component behavior now has dedicated v2 tests; cross-process tests live in OC.
"""
import json
from mini_rover_control.protocol import Decoder

class Clock:
    def __init__(self): self.now=1.
    def __call__(self): return self.now
    def step(self,dt): self.now+=dt

def test_serial_partial_writes_preserve_frame_and_refuse_backlog(monkeypatch):
    from mini_rover_control.serial_port import SerialArduinoPort
    from mini_rover_control import serial_port
    class Device:
        in_waiting=0
        def __init__(self,*args,**kwargs):self.out=bytearray()
        def reset_input_buffer(self):pass
        def reset_output_buffer(self):pass
        def close(self):pass
        def read(self,n):return b''
        def write(self,data):
            self.out.extend(data[:3]);return min(3,len(data))
    monkeypatch.setattr(serial_port.serial,'Serial',Device)
    clock=Clock();p=SerialArduinoPort('/fake',clock=clock)
    p.poll();frame={'kind':'TARGETS','id':42}
    assert p.send(frame)
    assert not p.send({'kind':'other'})
    for _ in range(30):p.poll()
    assert Decoder().feed(p.serial.out)==[frame]


def test_log_handles_rejected_nonfinite_values_without_failing(tmp_path):
    from mini_rover_control.run_log import RunLog
    log=RunLog(tmp_path)
    log.write('invalid_intent',{'throttle':float('nan')})
    log.close()
    assert not log.error
    assert json.loads(log.path.read_text())['data']['throttle']=='nan'


def test_serial_stalled_partial_frame_expires_and_discards_on_reconnect(monkeypatch):
    from mini_rover_control.serial_port import SerialArduinoPort
    from mini_rover_control import serial_port
    class Device:
        in_waiting=0
        def __init__(self,*args,**kwargs):self.out=bytearray();self.block=False
        def reset_input_buffer(self):pass
        def reset_output_buffer(self):pass
        def close(self):pass
        def read(self,n):return b''
        def write(self,data):
            if self.block:return 0
            self.out.extend(data);return len(data)
    monkeypatch.setattr(serial_port.serial,'Serial',Device)
    clock=Clock();p=SerialArduinoPort('/fake',clock=clock);p.poll()
    old=p.serial;old.block=True
    assert p.send({'type':'command','op':'TARGETS'})
    clock.step(.101);p.poll()
    assert not p.connected and not p.pending
    clock.step(2.1);p.poll()
    assert p.connected and p.serial is not old
    assert bytes(p.serial.out)==b'\n'
