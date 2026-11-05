"""Passive ROS observations, measured at this observer's receipt time."""
from collections import deque
import time

from .metrics import TopicWindow, age


def select(message, fields):
    return {name: getattr(message, name) for name in fields.split()}


def fault_value(fault):
    return select(fault, 'source code severity latched command_id time_ms')


class Observer:
    def __init__(self, now=None):
        self.topics = {name: TopicWindow(now) for name in ('intent', 'state', 'chassis', 'diagnostics')}
        self.control = self.chassis = self.diagnostics = None
        self.control_at = self.chassis_at = self.diagnostics_at = None
        self.events = deque(maxlen=64)
        self.events_total = self.events_dropped = 0
        self.last_faults = set()
        self.last_serial_error = ''
        self.window_peaks = {'input_age_ms': None, 'feedback_age_ms': None}

    def event(self, kind, message):
        if len(self.events) == self.events.maxlen:
            self.events_dropped += 1
        self.events_total += 1
        self.events.append(dict(kind=kind, message=message, wall_time_ns=time.time_ns(),
                                monotonic_ns=time.monotonic_ns()))

    def received(self, topic, now, identity=None, sequence=None):
        window = self.topics[topic]
        fresh = sequence is None or identity != window.identity or window.seq is None or sequence > window.seq
        if identity != window.identity and window.identity is not None:
            self.event('identity_changed', topic+' identity changed')
        window.receive(now, identity, sequence)
        return fresh

    def intent_message(self, message):
        self.received('intent', time.monotonic(), message.session_id, message.command_id)

    def state_message(self, message):
        now = time.monotonic()
        if not self.received('state', now, message.boot_id, message.state_seq):
            return
        state = select(message, 'controller_state reason session_id oc_authorized actual_enabled enabled_known '
                       'stop_latched arduino_connected arduino_boot_id boot_id generation '
                       'last_accepted_command_id last_applied_command_id last_applied_seq applied_session_id')
        for name in ('input_age_ms', 'feedback_age_ms'):
            state[name] = age(getattr(message, name))
            if state[name] is not None and (name != 'input_age_ms' or state['session_id']):
                self.window_peaks[name] = max(self.window_peaks[name] or 0, state[name])
        state['faults'] = [fault_value(f) for f in message.faults]
        state['first_fault'] = fault_value(message.first_fault) if message.first_fault_valid else None
        faults = {(f['source'], f['code'], f['severity']) for f in state['faults']}
        for fault in faults-self.last_faults:
            self.event('fault', ' / '.join(fault))
        for fault in self.last_faults-faults:
            self.event('fault_cleared', ' / '.join(fault))
        if self.control:
            for field in ('controller_state', 'arduino_boot_id', 'generation', 'arduino_connected'):
                if state[field] != self.control[field]:
                    self.event('state_changed', f'{field}: {self.control[field]} → {state[field]}')
        self.last_faults, self.control, self.control_at = faults, state, now

    def chassis_message(self, message):
        now = time.monotonic()
        if not self.received('chassis', now, message.arduino_boot_id, message.sample_seq):
            return
        self.chassis = select(message, 'arduino_boot_id sample_seq sample_time_ms simulated source_session')
        self.chassis['mcu_age_ms'] = age(message.mcu_age_ms)
        self.chassis['encoders_valid'] = {w: getattr(message, w).measurement_valid
                                        for w in ('front_left', 'rear_left', 'front_right', 'rear_right')}
        self.chassis_at = now

    def diagnostics_message(self, message):
        now = time.monotonic()
        matching = [s for s in message.status if s.name == 'mini_rover/oc']
        if matching:
            self.received('diagnostics', now)
        for status in matching:
            values = {v.key: v.value[:512] for v in status.values}
            # diagnostic_msgs uses ROS 'byte', exposed as bytes by Python.
            level = status.level[0] if isinstance(status.level, bytes) else status.level
            self.diagnostics = dict(level=level, message=status.message[:512], values=values)
            self.diagnostics_at = now
            error = values.get('serial_error', '')
            if error != self.last_serial_error:
                self.event('serial', error or 'Serial error cleared')
                self.last_serial_error = error

    def snapshot(self, now):
        elapsed = lambda at: None if at is None else max(0, (now-at)*1000)
        result = dict(topics={name: window.snapshot(now) for name, window in self.topics.items()},
                      control=self.control, chassis=self.chassis, diagnostics=self.diagnostics,
                      control_receipt_age_ms=elapsed(self.control_at),
                      chassis_receipt_age_ms=elapsed(self.chassis_at),
                      diagnostics_receipt_age_ms=elapsed(self.diagnostics_at),
                      window_peak_reported_ages=self.window_peaks.copy(),
                      events=list(self.events), events_total=self.events_total, events_dropped=self.events_dropped)
        self.events.clear()
        self.window_peaks = dict.fromkeys(self.window_peaks)
        return result
