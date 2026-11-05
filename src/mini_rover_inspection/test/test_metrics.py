"""Measurement semantics and bounded retention, independent of ROS/hardware."""
import json
import os
from pathlib import Path
import threading
from types import SimpleNamespace as NS

import pytest

from mini_rover_inspection.metrics import TopicWindow, SystemSampler, age, clean
from mini_rover_inspection.observer import Observer
from mini_rover_inspection.recording import Recorder, SampleStore


def test_gaps_capture_short_stall_and_silence_between_resource_samples():
    window = TopicWindow(now=0)
    window.receive(.01, 'boot', 1)
    window.receive(.06, 'boot', 2)
    window.receive(.46, 'boot', 5)
    first = window.snapshot(.5)
    assert first['max_gap_ms'] == pytest.approx(400)
    assert first['unobserved_sequences'] == 2
    assert first['rate_hz'] == 6
    silent = window.snapshot(1.)
    assert silent['rate_hz'] == 0
    assert silent['max_gap_ms'] == pytest.approx(540)
    assert silent['p95_gap_ms'] is None


def test_sequence_baseline_resets_only_on_identity_change():
    window = TopicWindow(now=0)
    for stamp, identity, seq in [(0, 'a', 4), (.1, 'a', 4), (.2, 'a', 3), (.3, 'b', 1), (.4, 'b', 2)]:
        window.receive(stamp, identity, seq)
    result = window.snapshot(.5)
    assert result['duplicate_or_reversed'] == 2
    assert result['identity_changes'] == 1
    assert result['unobserved_sequences'] == 0


def test_never_seen_ages_and_nonfinite_values_are_not_zero():
    assert age(-1000) is None
    assert age(float('inf')) is None
    assert age(0) == 0
    assert clean({'a': [float('nan'), 2]}) == {'a': [None, 2]}


def test_unrelated_diagnostics_do_not_renew_oc_freshness():
    observer = Observer()
    observer.diagnostics_message(NS(status=[NS(name='unrelated')]))
    assert observer.topics['diagnostics'].total == 0
    assert observer.diagnostics_at is None
    observer.diagnostics_message(NS(status=[NS(name='mini_rover/oc', level=0,
        message='okay', values=[NS(key='serial_error', value='')])]))
    assert observer.topics['diagnostics'].total == 1
    assert observer.diagnostics_at is not None


def test_system_sampler_exact_pid_and_reuse_protection(tmp_path, monkeypatch):
    sampler = SystemSampler(os.getpid(), tmp_path)
    sample = sampler.sample()
    assert sample['process']['pid'] == os.getpid()
    assert sample['process']['rss_bytes'] > 0
    assert sample['system']['memory_total_bytes'] > 0
    monkeypatch.setattr(sampler.target, 'is_running', lambda: False)
    assert sampler.sample()['process']['alive'] is False
    sampler.close()


def test_slow_sensor_read_does_not_block_resource_sampling(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    def slow_temperature():
        entered.set()
        release.wait(timeout=5)
        return {}
    monkeypatch.setattr('psutil.sensors_temperatures', slow_temperature)
    sampler = SystemSampler(os.getpid(), tmp_path)
    try:
        assert entered.wait(timeout=2)
        # The sensor call is still waiting. A resource sample must complete
        # with unknown sensor age while it waits, without joining that call.
        assert sampler.sample()['system']['sensor_age_s'] is None
        assert not release.is_set()
    finally:
        release.set()
        sampler.close()


def test_recording_rotates_and_remains_valid_jsonl(tmp_path):
    recorder = Recorder(tmp_path, 'test', max_bytes=180, backups=2)
    for index in range(20):
        recorder.write({'seq': index, 'value': 'x'*40})
    recorder.close()
    assert not recorder.error
    files = list(tmp_path.iterdir())
    assert len(files) == 3
    assert all(path.stat().st_size <= 180 for path in files)
    records = [json.loads(line) for path in files for line in path.read_text().splitlines()]
    assert max(r['seq'] for r in records) == 19
    assert len(records) < 20


def test_recorder_errors_are_visible(tmp_path):
    recorder = Recorder(tmp_path, 'failure')
    recorder.handler.stream.close()
    recorder.write({'value': 1})
    recorder.close()
    assert recorder.error
    recorder.write({'value': 2})
    assert recorder.dropped == 1


def sample(seq=1, identity='a'):
    return dict(schema_version=1, monitor_id=identity, role='oc', seq=seq, monotonic_ns=seq,
                hostname='test-host', wall_time_ns=1700000000000000000,
                system={}, process={}, observer={}, monitor={})


def test_store_bounds_sources_history_and_rejects_reordered_samples():
    store = SampleStore(capacity=2, max_sources=2)
    for seq in range(1, 5):
        assert store.add(sample(seq), received=float(seq))
    assert not store.add(sample(3), received=99.)
    assert [s['seq'] for s in store.snapshot()['a']['samples']] == [3, 4]
    assert store.snapshot()['a']['received'] == 4
    store.add(sample(identity='b'), received=5.)
    store.add(sample(identity='c'), received=6.)
    assert set(store.snapshot()) == {'b', 'c'}
    assert not store.add({'schema_version': 99})
