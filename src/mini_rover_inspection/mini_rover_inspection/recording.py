"""Bounded asynchronous JSONL recording, with rotation and visible failures."""
from collections import deque
import json
import logging.handlers
import math
from pathlib import Path
from queue import Empty, Full, Queue
import threading
import time

from .metrics import clean


class Recorder:
    def __init__(self, directory, name, max_bytes=20*1024*1024, backups=4):
        directory = Path(directory).expanduser()
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / (name+'.jsonl')
        self.handler = logging.handlers.RotatingFileHandler(self.path, maxBytes=max_bytes, backupCount=backups)
        self.queue = Queue(maxsize=256)
        self.stop = threading.Event()
        self.error = ''
        self.dropped = 0
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def write(self, sample):
        if self.error:
            self.dropped += 1
            return
        try:
            self.queue.put_nowait(json.dumps(clean(sample), allow_nan=False, separators=(',', ':')))
        except Full:
            self.dropped += 1

    def _run(self):
        try:
            while not self.stop.is_set() or not self.queue.empty():
                try:
                    line = self.queue.get(timeout=.1)
                except Empty:
                    continue
                # Write directly: logging.emit normally suppresses I/O errors.
                encoded_size = len((line+'\n').encode('utf-8'))
                if self.handler.stream.tell()+encoded_size >= self.handler.maxBytes:
                    self.handler.doRollover()
                self.handler.stream.write(line+'\n')
                self.handler.stream.flush()
        except (OSError, ValueError) as exc:
            self.error = str(exc)
        finally:
            try:
                self.handler.close()
            except (OSError, ValueError) as exc:
                self.error = self.error or str(exc)

    def close(self):
        self.stop.set()
        self.thread.join(timeout=2)
        if self.thread.is_alive():
            self.error = 'Recorder did not finish within 2 seconds'


def valid_sample(sample):
    """Reject malformed display inputs while allowing unavailable optional metrics."""
    def number(value):
        return type(value) in (int, float) and -1e308 <= value <= 1e308 and math.isfinite(value)

    def counter(value):
        return type(value) is int and 0 <= value <= 2**63-1

    def timestamp(value):
        # Keep datetime.fromtimestamp in the dashboard's supported range.
        return type(value) is int and 0 <= value < 253402300799_000000000

    def numeric_fields(mapping, names):
        return all(mapping.get(name) is None or number(mapping[name]) for name in names.split())

    def optional_dict(mapping, name):
        return mapping.get(name) is None or isinstance(mapping[name], dict)

    def numeric_mapping(value):
        return isinstance(value, dict) and all(v is None or number(v) for v in value.values())

    if (not isinstance(sample, dict) or type(sample.get('schema_version')) is not int
            or sample['schema_version'] != 1 or sample.get('role') not in ('c2', 'oc')
            or not isinstance(sample.get('monitor_id'), str) or not sample['monitor_id']
            or not isinstance(sample.get('hostname'), str) or not sample['hostname']
            or not counter(sample.get('seq')) or not counter(sample.get('monotonic_ns'))
            or not timestamp(sample.get('wall_time_ns'))
            or not all(isinstance(sample.get(k), dict) for k in ('system', 'process', 'observer', 'monitor'))):
        return False
    interval = sample.get('interval_s', 1.)
    if not number(interval) or not .2 <= interval <= 60:
        return False
    system, process, observer, monitor = (sample[k] for k in ('system', 'process', 'observer', 'monitor'))
    if not numeric_fields(system, 'cpu_percent cpu_iowait_percent memory_total_bytes memory_available_bytes '
                          'memory_percent swap_used_bytes disk_free_bytes cpu_frequency_mhz'):
        return False
    if not numeric_fields(process, 'pid created cpu_percent rss_bytes threads file_descriptors context_switches_per_s'):
        return False
    if 'alive' in process and type(process['alive']) is not bool:
        return False
    for name in ('core_percent', 'load_average'):
        values = system.get(name)
        if values is not None and (not isinstance(values, list) or len(values) > 4096
                                   or not all(number(value) for value in values)):
            return False
    temperatures = system.get('temperatures')
    if temperatures is not None and (not isinstance(temperatures, list) or len(temperatures) > 4096
            or not all(isinstance(value, dict) and numeric_fields(value, 'celsius high critical')
                       for value in temperatures)):
        return False
    for mapping, name in ((system, 'swap_io'), (system, 'disk_io'), (process, 'io')):
        if mapping.get(name) is not None and not numeric_mapping(mapping[name]):
            return False
    network = system.get('network')
    if network is not None and (not isinstance(network, dict) or len(network) > 256
                                or not all(numeric_mapping(value) for value in network.values())):
        return False
    if not all(optional_dict(observer, name) for name in
               ('control', 'chassis', 'diagnostics', 'topics', 'window_peak_reported_ages')):
        return False
    topics = observer.get('topics') or {}
    if len(topics) > 64 or not all(numeric_mapping(value) for value in topics.values()):
        return False
    if not numeric_fields(observer, 'control_receipt_age_ms chassis_receipt_age_ms diagnostics_receipt_age_ms '
                          'events_total events_dropped'):
        return False
    peaks = observer.get('window_peak_reported_ages') or {}
    if not numeric_fields(peaks, 'input_age_ms feedback_age_ms'):
        return False
    control = observer.get('control') or {}
    if not numeric_fields(control, 'input_age_ms feedback_age_ms'):
        return False
    faults = control.get('faults', [])
    if not isinstance(faults, list) or len(faults) > 64 or not all(
            isinstance(fault, dict) and isinstance(fault.get('code'), str) for fault in faults):
        return False
    diagnostics = observer.get('diagnostics') or {}
    if not optional_dict(diagnostics, 'values'):
        return False
    events = observer.get('events', [])
    if not isinstance(events, list) or len(events) > 64 or not all(
            isinstance(event, dict) and timestamp(event.get('wall_time_ns'))
            and isinstance(event.get('kind'), str) and isinstance(event.get('message'), str)
            and ('monotonic_ns' not in event or counter(event['monotonic_ns'])) for event in events):
        return False
    if monitor.get('log_error') is not None and not isinstance(monitor['log_error'], str):
        return False
    return numeric_fields(monitor, 'cpu_percent rss_bytes collection_ms log_dropped')


class SampleStore:
    """Bounded dashboard history. Liveness uses this machine's monotonic clock."""
    def __init__(self, capacity=600, max_sources=16):
        self.capacity, self.max_sources = capacity, max_sources
        self.sources = {}
        self.lock = threading.Lock()

    def add(self, sample, received=None):
        if not valid_sample(sample):
            return False
        with self.lock:
            key = sample['monitor_id']
            if key not in self.sources:
                if len(self.sources) >= self.max_sources:
                    oldest = min(self.sources, key=lambda k: self.sources[k]['received'])
                    del self.sources[oldest]
                self.sources[key] = dict(samples=deque(maxlen=self.capacity), received=0.)
            source = self.sources[key]
            if source['samples'] and sample['seq'] <= source['samples'][-1]['seq']:
                return False
            source['samples'].append(sample)
            source['received'] = time.monotonic() if received is None else received
        return True

    def snapshot(self):
        with self.lock:
            return {k: dict(samples=list(v['samples']), received=v['received']) for k, v in self.sources.items()}
