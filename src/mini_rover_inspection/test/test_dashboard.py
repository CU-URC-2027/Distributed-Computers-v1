"""Replay and Tk display checks using synthetic performance records only."""
import json
from types import SimpleNamespace
import tkinter as tk

import pytest

from mini_rover_inspection import dashboard
from mini_rover_inspection.recording import SampleStore


def synthetic_sample(seq=1, role='c2', monitor_id=None, alive=True):
    """A test fixture, never measured hardware performance or motion evidence."""
    return dict(
        schema_version=1, monitor_id=monitor_id or 'synthetic-'+role,
        role=role, hostname='synthetic-'+role+'-host', namespace='/test_rover', domain='186',
        seq=seq, wall_time_ns=1_790_000_000_000_000_000+seq*1_000_000_000,
        monotonic_ns=seq*1_000_000_000, interval_s=1.,
        process=dict(pid=1234 if role == 'c2' else 5678, alive=alive,
                     cpu_percent=12.5, rss_bytes=64*2**20, status='running', threads=4),
        system=dict(cpu_percent=20., core_percent=[15., 25.],
                    memory_available_bytes=3*2**30, swap_used_bytes=0,
                    disk_free_bytes=20*2**30, temperatures=[dict(celsius=42.)]),
        observer=dict(
            control=dict(controller_state='DISABLED', faults=[]),
            chassis=dict(simulated=True), control_receipt_age_ms=20.,
            topics=dict(state=dict(rate_hz=20.)),
            window_peak_reported_ages=dict(input_age_ms=50., feedback_age_ms=100.),
            events=[dict(wall_time_ns=1_790_000_000_000_000_000+seq*1_000_000_000,
                         kind='test_event', message='Synthetic test event')]),
        monitor=dict(cpu_percent=1.2, collection_ms=2., log_error='', log_dropped=0),
    )


def test_replay_keeps_bounded_recent_history_and_skips_malformed_records(tmp_path):
    path = tmp_path/'synthetic.jsonl'
    records = [synthetic_sample(seq=seq) for seq in range(1, 9)]
    wrong_schema = synthetic_sample(seq=9)
    wrong_schema['schema_version'] = 999
    wrong_observer = synthetic_sample(seq=9)
    wrong_observer['observer'] = []
    path.write_text('\n'.join([
        *(json.dumps(record) for record in records),
        'truncated {', 'null', '[]', '{}', json.dumps(wrong_schema),
        json.dumps(wrong_observer), json.dumps(records[-1]),
    ])+'\n')
    store = SampleStore(capacity=3)
    assert dashboard.load_recordings(store, [path]) == (8, 7)
    source = store.snapshot()['synthetic-c2']
    assert [sample['seq'] for sample in source['samples']] == [6, 7, 8]
    assert source['received'] == records[-1]['wall_time_ns']/1e9


def test_replay_combines_sources_and_bounds_retained_sessions(tmp_path):
    first, second = tmp_path/'first.jsonl', tmp_path/'second.jsonl'
    first.write_text(json.dumps(synthetic_sample(monitor_id='old-c2'))+'\n')
    second.write_text('\n'.join([
        json.dumps(synthetic_sample(seq=2, monitor_id='new-c2')),
        json.dumps(synthetic_sample(seq=3, role='oc', monitor_id='new-oc')),
    ])+'\n')
    store = SampleStore(capacity=3, max_sources=2)
    assert dashboard.load_recordings(store, [first, second]) == (3, 0)
    assert set(store.snapshot()) == {'new-c2', 'new-oc'}


@pytest.mark.parametrize('path,value', [
    ('hostname', None),
    ('wall_time_ns', 'invalid timestamp'),
    ('wall_time_ns', 10**30),
    ('monotonic_ns', -1),
    ('interval_s', 0),
    ('process.rss_bytes', '64 MiB'),
    ('process.cpu_percent', 10**500),
    ('system.core_percent', [10., None]),
    ('system.temperatures', ['invalid sensor']),
    ('system.temperatures', [dict(celsius='warm')]),
    ('system.network', dict(eth0=[])),
    ('observer.control', []),
    ('observer.control.faults', [dict(code=42)]),
    ('observer.topics', dict(state=None)),
    ('observer.events', [dict(kind='fault', message='missing timestamp')]),
    ('observer.events', [dict(wall_time_ns=10**30, kind='fault', message='invalid date')]),
    ('monitor.log_error', 42),
])
def test_malformed_nested_display_values_are_skipped_before_rendering(tmp_path, path, value):
    sample = synthetic_sample()
    target = sample
    parts = path.split('.')
    for part in parts[:-1]:
        target = target[part]
    target[parts[-1]] = value
    file = tmp_path/'malformed.jsonl'
    file.write_text(json.dumps(sample)+'\n')
    store = SampleStore()
    assert dashboard.load_recordings(store, [file]) == (0, 1)
    assert store.snapshot() == {}


@pytest.fixture
def tk_root():
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip('Tk graphical display unavailable: '+str(exc))
    root.withdraw()
    yield root
    for callback in root.tk.call('after', 'info'):
        root.after_cancel(callback)
    root.destroy()


@pytest.mark.parametrize('alive,received,replay,expected', [
    (True, 99., False, 'LIVE'),
    (True, 90., False, 'STALE'),
    (False, 99., False, 'EXITED'),
    (True, 90., True, 'RECORDED'),
])
def test_host_status_distinguishes_live_stale_exit_and_recorded(
        tk_root, monkeypatch, alive, received, replay, expected):
    monkeypatch.setattr(dashboard, 'time', SimpleNamespace(monotonic=lambda: 100.))
    sample = synthetic_sample(alive=alive)
    store = SampleStore()
    assert store.add(sample, received=received)
    panel = dashboard.HostPanel(tk_root, 'c2')
    panel.pack(fill='both', expand=True)
    assert panel.update_source(store.snapshot(), replay=replay) == sample
    tk_root.update()
    assert panel.badge.cget('text') == expected
    assert '64.0 MiB' in panel.resources.cget('text')
    assert 'SIMULATION' in panel.health.cget('text')
    assert bool(panel.cpu.find_all())
    assert ('last known' in panel.health.cget('text')) == (expected == 'STALE')


def test_full_dashboard_renders_both_hosts_details_and_events(tk_root, monkeypatch):
    monkeypatch.setattr(dashboard, 'time', SimpleNamespace(monotonic=lambda: 100.))
    store = SampleStore()
    for role in ('c2', 'oc'):
        store.add(synthetic_sample(role=role), received=99.)
    viewer = dashboard.Dashboard(tk_root, store, '/test_rover')
    tk_root.update()
    assert all(panel.badge.cget('text') == 'LIVE' for panel in viewer.panels.values())
    measurements = {viewer.table.item(item, 'values')[0]: viewer.table.item(item, 'values')[1:]
                    for item in viewer.table.get_children()}
    assert measurements['Host / process'] == ('synthetic-c2-host / 1234', 'synthetic-oc-host / 5678')
    events = [viewer.events.item(item, 'values') for item in viewer.events.get_children()]
    assert len(events) == 2
    assert {event[1] for event in events} == {'synthetic-c2-host / C2', 'synthetic-oc-host / OC'}
    assert all(event[2:] == ('test_event', 'Synthetic test event') for event in events)


def test_missing_optional_metrics_show_na_and_recording_error_is_visible(tk_root, monkeypatch):
    monkeypatch.setattr(dashboard, 'time', SimpleNamespace(monotonic=lambda: 100.))
    sample = synthetic_sample()
    sample['system'] = {}
    sample['process'] = dict(pid=1234, alive=True)
    sample['observer'] = {}
    sample['monitor'] = dict(log_error='Synthetic disk-full test')
    store = SampleStore()
    store.add(sample, received=99.)
    panel = dashboard.HostPanel(tk_root, 'c2')
    panel.pack()
    panel.update_source(store.snapshot())
    tk_root.update()
    assert 'Node CPU N/A' in panel.resources.cget('text')
    assert 'Node RAM N/A' in panel.resources.cget('text')
    assert 'UNKNOWN' in panel.health.cget('text')
    assert 'Recording error: Synthetic disk-full test' in panel.health.cget('text')
    assert panel.health.cget('fg') == dashboard.RED
