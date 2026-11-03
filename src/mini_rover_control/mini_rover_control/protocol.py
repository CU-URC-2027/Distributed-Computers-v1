"""Strict, bounded v2 JSON lines. Unknown fields/versions are rejected."""
import json
import re
from .contracts import integer, sides, WHEELS, VERSION, MODE, UINT32_MAX

MAX_LINE = 2048
OPS = {'BIND', 'ENABLE', 'TARGETS', 'DISABLE', 'STOP', 'RESET', 'PING'}
COMMAND_KEYS = set('v type mode op boot_id session_id seq generation sample_seq command_id source_session targets'.split())
FEEDBACK_KEYS = set('v type mode boot_id session_id generation sample_seq ack_seq last_applied_seq source_session last_applied_command_id uptime_ms enabled stopped applied counts valid direction_valid faults reason'.split())
FAULT_KEYS = set('source code severity latched command_id time_ms'.split())


def encode(packet):
    data = json.dumps(packet, allow_nan=False, separators=(',', ':')).encode('ascii') + b'\n'
    if len(data) > MAX_LINE:
        raise ValueError('Packet exceeds line limit')
    return data


def _unique(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError('Duplicate JSON key')
        obj[key] = value
    return obj


class Decoder:
    def __init__(self):
        self.buffer = bytearray()
        self.dropping = False
        self.invalid = 0

    def reset(self):
        self.buffer.clear()
        self.dropping = False

    def feed(self, data):
        result = []
        for byte in data:
            if byte == 10:
                if not self.dropping and self.buffer:
                    try:
                        obj = json.loads(self.buffer, object_pairs_hook=_unique,
                                         parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite')))
                        if not isinstance(obj, dict):
                            raise ValueError('Expected object')
                        result.append(obj)
                    except (ValueError, UnicodeError, RecursionError):
                        self.invalid += 1
                self.reset()
            elif not self.dropping:
                self.buffer.append(byte)
                if len(self.buffer) >= MAX_LINE:
                    self.invalid += 1
                    self.buffer.clear()
                    self.dropping = True
        return result


def identity(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{32}', value) is not None


def optional_identity(value):
    return value == '' or identity(value)


def token(value, maximum=32):
    return isinstance(value, str) and re.fullmatch('[A-Z][A-Z0-9_]{0,'+str(maximum-1)+'}', value) is not None


def valid_fault(f):
    return (isinstance(f, dict) and set(f) == FAULT_KEYS and f.get('source') in ('MCU', 'OC', 'C2')
            and token(f.get('code')) and f.get('severity') in ('WARNING', 'ERROR')
            and type(f.get('latched')) is bool and integer(f.get('command_id'))
            and integer(f.get('time_ms'), 0, UINT32_MAX))


def valid_command(p):
    return (isinstance(p, dict) and set(p) == COMMAND_KEYS
            and type(p.get('v')) is int and p['v'] == VERSION
            and p.get('type') == 'command' and p.get('mode') == MODE
            and isinstance(p.get('op'), str) and p['op'] in OPS
            and all(identity(p.get(k)) for k in ('boot_id', 'session_id'))
            and all(integer(p.get(k), 1) for k in ('seq', 'generation', 'sample_seq'))
            and integer(p.get('command_id')) and optional_identity(p.get('source_session'))
            and sides(p.get('targets'))
            and (p['op'] == 'TARGETS' or not any(p['targets'].values()))
            and (p['op'] != 'TARGETS' or (p['command_id'] > 0 and identity(p['source_session']))))


def valid_feedback(p):
    return (isinstance(p, dict) and set(p) == FEEDBACK_KEYS
            and type(p.get('v')) is int and p['v'] == VERSION
            and p.get('type') == 'feedback' and p.get('mode') == MODE and identity(p.get('boot_id'))
            and optional_identity(p.get('session_id')) and optional_identity(p.get('source_session'))
            and all(integer(p.get(k), 1) for k in ('generation', 'sample_seq'))
            and all(integer(p.get(k)) for k in ('ack_seq', 'last_applied_seq', 'last_applied_command_id'))
            and p['last_applied_seq'] <= p['ack_seq']
            and ((p['last_applied_command_id'] == 0 and p['last_applied_seq'] == 0 and p['source_session'] == '')
                 or (p['last_applied_command_id'] > 0 and p['last_applied_seq'] > 0 and identity(p['source_session'])))
            and integer(p.get('uptime_ms'), 0, UINT32_MAX)
            and all(type(p.get(k)) is bool for k in ('enabled', 'stopped'))
            and not (p['enabled'] and p['stopped']) and sides(p.get('applied'))
            and (p['enabled'] or not any(p['applied'].values()))
            and isinstance(p.get('counts'), dict) and set(p['counts']) == set(WHEELS)
            and all(integer(v, 0, UINT32_MAX) for v in p['counts'].values())
            and all(isinstance(p.get(k), dict) and set(p[k]) == set(WHEELS)
                    and all(type(v) is bool for v in p[k].values()) for k in ('valid', 'direction_valid'))
            and all(not p['direction_valid'][w] or p['valid'][w] for w in WHEELS)
            and isinstance(p.get('faults'), list) and len(p['faults']) <= 4
            and all(valid_fault(f) for f in p['faults']) and token(p.get('reason')))
