"""Shared interface vectors; these are independent of the component implementations."""
import json
from pathlib import Path
import pytest
from mini_rover_control.contracts import (Config, DriveIntent, WHEELS, VERSION, MODE,
                                         UINT64_MAX, UINT32_MAX, fault)
from mini_rover_control.protocol import (Decoder, encode, valid_command, valid_feedback,
                                        MAX_LINE)

FIXTURES = Path('/home/liamolczak/weeny_ws/contracts')


@pytest.mark.parametrize('vector', json.loads((FIXTURES/'command_vectors.json').read_text()),
                         ids=lambda v:v['name'])
def test_shared_command_vectors(vector):
    assert valid_command(vector['packet']) is vector['valid']


@pytest.mark.parametrize('vector', json.loads((FIXTURES/'mixer_vectors.json').read_text()))
def test_shared_mixer_vectors(vector):
    requested, limited = Config().targets(vector['throttle'], vector['steering'])
    assert limited == {k:vector[k] for k in ('left','right')}
    assert max(map(abs,limited.values())) <= 63


@pytest.mark.parametrize('value',[True,None,float('nan'),float('inf'),-1.01,1.01,'0'])
def test_inputs_and_pwm_cap_reject_wrong_units_or_types(value):
    with pytest.raises(ValueError):Config().targets(value,0)
    with pytest.raises(ValueError):Config().targets(0,value)


@pytest.mark.parametrize('limit',[64,255,0,-1,True,63.0])
def test_limit_cannot_exceed_bench_ceiling(limit):
    with pytest.raises(ValueError):Config(pwm_limit=limit)


def test_clock_timestamp_is_audit_only_but_receiver_identity_is_required():
    packet=DriveIntent('1'*32,1,stamp_ns=1,oc_boot_id='2'*32,generation=1,challenge_seq=1)
    packet.validate(Config(),10**18)
    with pytest.raises(ValueError):DriveIntent('1'*32,1).validate(Config())


def test_fragment_coalesce_exact_integer_and_legacy_letters_do_not_form_commands():
    packet=json.loads((FIXTURES/'command_vectors.json').read_text())[0]['packet']
    data=encode(packet)
    decoder=Decoder(); result=[]
    for byte in data:result.extend(decoder.feed(bytes([byte])))
    assert result == [packet]
    assert result[0]['command_id']==9007199254740993
    assert decoder.feed(data+data)==[packet,packet]
    assert decoder.feed(b'WASD X B Q \n')==[]


@pytest.mark.parametrize('data',[
    b'{"v":2,"v":2}\n',b'{"targets":{"left":0,"left":1}}\n',
    b'{"value":NaN}\n',b'{"value":Infinity}\n', b'[]\n',b'\xff\n',
])
def test_invalid_frames_recover(data):
    decoder=Decoder()
    assert decoder.feed(data+b'{"ok":true}\n')==[{'ok':True}]
    assert decoder.invalid==1


def test_exact_line_limit_and_discard_until_newline():
    decoder=Decoder()
    prefix=b'{"pad":"'; suffix=b'"}\n'
    line=prefix+b'a'*(MAX_LINE-len(prefix)-len(suffix))+suffix
    assert len(line)==MAX_LINE
    assert decoder.feed(line)
    oversized=prefix+b'a'*(MAX_LINE-len(prefix)-len(suffix)+1)+suffix
    assert decoder.feed(oversized+b'{"ok":true}\n')==[{'ok':True}]
    assert decoder.invalid==1


def maximum_feedback():
    return dict(v=VERSION,type='feedback',mode=MODE,boot_id='a'*32,session_id='b'*32,
        generation=UINT64_MAX,sample_seq=UINT64_MAX,ack_seq=UINT64_MAX,
        last_applied_seq=UINT64_MAX,source_session='c'*32,last_applied_command_id=UINT64_MAX,
        uptime_ms=UINT32_MAX,enabled=True,stopped=False,applied={'left':-63,'right':-63},
        counts={w:UINT32_MAX for w in WHEELS},valid={w:False for w in WHEELS},
        direction_valid={w:False for w in WHEELS},
        faults=[fault('MCU','A'*32,'WARNING',False,UINT64_MAX,UINT32_MAX) for _ in range(4)],
        reason='A'*32)


def test_maximum_feedback_fits_and_preserves_exact_ids():
    feedback=maximum_feedback()
    assert valid_feedback(feedback)
    assert len(encode(feedback))<=MAX_LINE
    assert Decoder().feed(encode(feedback))==[feedback]


@pytest.mark.parametrize('update',[
    {'v':1},{'counts':{}},{'enabled':False},{'stopped':True},
    {'last_applied_seq':0},{'source_session':''},{'measured':{}},
    {'faults':[fault('MCU','A')]*5},
])
def test_inconsistent_feedback_is_rejected(update):
    assert not valid_feedback({**maximum_feedback(),**update})
