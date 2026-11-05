"""Desktop viewer for live performance samples or recorded JSONL sessions."""
import argparse
from datetime import datetime
import json
import math
from pathlib import Path
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk

from .recording import SampleStore

BG, PANEL, INK, MUTED, GRID = '#101822', '#182432', '#e9f0f6', '#9aabba', '#2b3c4c'
TEAL, GOLD, RED = '#5de0c0', '#f4c677', '#ff8591'


def get(sample, path, default=None):
    value = sample
    for key in path.split('.'):
        if not isinstance(value, dict):
            return default
        value = value.get(key)
    return default if value is None else value


def fmt(value, suffix='', decimals=1, scale=1):
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        return 'N/A'
    return f'{value/scale:,.{decimals}f}{suffix}'


class Trend(tk.Canvas):
    def __init__(self, parent, title, series, unit, reference=(), height=118):
        super().__init__(parent, background=PANEL, highlightthickness=0, height=height)
        self.title, self.series, self.unit, self.reference = title, series, unit, reference
        self.bind('<Configure>', lambda event: self.draw(self.samples))
        self.samples = []

    def draw(self, samples):
        self.samples = samples
        self.delete('all')
        width, height = max(200, self.winfo_width()), max(110, self.winfo_height())
        self.create_text(12, 13, text=self.title, anchor='w', fill=INK, font=('Sans', 10, 'bold'))
        self.create_text(width-12, 13, text=self.unit, anchor='e', fill=MUTED, font=('Sans', 9))
        left, right, top, bottom = 45, width-14, 35, height-28
        end = samples[-1]['monotonic_ns']/1e9 if samples else 0.
        duration = 300.
        points = []
        for label, color, getter in self.series:
            values = [(s['monotonic_ns']/1e9, getter(s)) for s in samples
                      if s['monotonic_ns']/1e9 >= end-duration]
            points.append((label, color, values))
        maximum = max([v for _, _, values in points for _, v in values
                       if isinstance(v, (int, float)) and math.isfinite(v)]+[v for v, _ in self.reference]+[1])
        maximum *= 1.12
        for fraction in (0, .5, 1):
            y = bottom-(bottom-top)*fraction
            self.create_line(left, y, right, y, fill=GRID)
            self.create_text(left-6, y, text=f'{maximum*fraction:.0f}', anchor='e', fill=MUTED, font=('Sans', 8))
        for value, label in self.reference:
            y = bottom-(bottom-top)*value/maximum
            self.create_line(left, y, right, y, fill=GOLD, dash=(3, 5))
            self.create_text(left+5, y-6, text=label, anchor='w', fill=GOLD, font=('Sans', 8))
        legend_x = left
        for label, color, values in points:
            segment, previous_time = [], None
            for stamp, value in values:
                gap = previous_time is not None and stamp-previous_time > max(3., (samples[-1].get('interval_s', 1.)*3))
                if value is None or not isinstance(value, (int, float)) or not math.isfinite(value) or gap:
                    if len(segment) >= 4:
                        self.create_line(*segment, fill=color, width=2)
                    segment = []
                if isinstance(value, (int, float)) and math.isfinite(value):
                    x, y = right-(end-stamp)/duration*(right-left), bottom-value/maximum*(bottom-top)
                    segment.extend((x, y))
                    if len(values) == 1:
                        self.create_oval(x-2, y-2, x+2, y+2, fill=color, outline='')
                previous_time = stamp
            if len(segment) >= 4:
                self.create_line(*segment, fill=color, width=2)
            self.create_text(legend_x, height-11, text='● '+label, anchor='w', fill=color, font=('Sans', 8))
            legend_x += max(105, len(label)*6+18)
        if not samples:
            self.create_text((left+right)/2, (top+bottom)/2, text='Waiting for samples', fill=MUTED)


class HostPanel(tk.Frame):
    def __init__(self, parent, role):
        super().__init__(parent, background=PANEL, padx=14, pady=12)
        self.role, self.selected, self.keys = role, None, []
        row = tk.Frame(self, background=PANEL)
        row.pack(fill='x')
        tk.Label(row, text='C2  ·  Operator computer' if role == 'c2' else 'OC  ·  Onboard computer',
                 bg=PANEL, fg=INK, font=('Sans', 14, 'bold')).pack(side='left')
        self.badge = tk.Label(row, text='WAITING', bg=PANEL, fg=MUTED, font=('Sans', 10, 'bold'))
        self.badge.pack(side='right')
        self.selector = ttk.Combobox(self, state='readonly')
        self.selector.pack(fill='x', pady=(10, 5))
        self.selector.bind('<<ComboboxSelected>>', lambda event: self.choose())
        self.resources = tk.Label(self, text='Start this computer’s performance monitor to connect.',
                                  bg=PANEL, fg=INK, justify='left', anchor='w', font=('Sans', 10))
        self.resources.pack(fill='x', pady=(4, 8))
        self.cpu = Trend(self, 'CPU usage', [('Node (100% = one core)', TEAL, lambda s: get(s, 'process.cpu_percent')),
                                             ('Host (all cores)', GOLD, lambda s: get(s, 'system.cpu_percent'))], '%')
        self.cpu.pack(fill='both', expand=True)
        self.memory = Trend(self, 'Node memory', [('Resident RAM', TEAL, lambda s: get(s, 'process.rss_bytes')/2**20 if get(s, 'process.rss_bytes') is not None else None)], 'MiB')
        self.memory.pack(fill='both', expand=True, pady=6)
        self.timing = Trend(self, 'Peak OC-reported ages per sample',
                            [('C2 input', TEAL, lambda s: get(s, 'observer.window_peak_reported_ages.input_age_ms')),
                             ('MCU feedback', GOLD, lambda s: get(s, 'observer.window_peak_reported_ages.feedback_age_ms'))],
                            'ms', [(250, 'input 250'), (350, 'feedback 350')])
        self.timing.pack(fill='both', expand=True)
        self.health = tk.Label(self, text='No controller state received', bg=PANEL, fg=MUTED,
                              justify='left', anchor='w', wraplength=540, font=('Sans', 10))
        self.health.pack(fill='x', pady=(10, 0))

    def choose(self):
        index = self.selector.current()
        if 0 <= index < len(self.keys):
            self.selected = self.keys[index]

    def update_source(self, sources, replay=False):
        candidates = sorted(((k, v) for k, v in sources.items() if v['samples'][-1]['role'] == self.role),
                            key=lambda pair: pair[1]['received'])
        keys = [k for k, _ in candidates]
        if keys != self.keys:
            old = self.keys
            self.keys = keys
            self.selector['values'] = [f"{v['samples'][-1].get('hostname', '?')} · PID {get(v['samples'][-1], 'process.pid')} · {k[:6]}"
                                       for k, v in candidates]
            if self.selected not in keys or (keys and (not old or self.selected == old[-1])):
                self.selected = keys[-1] if keys else None
            if self.selected:
                self.selector.current(keys.index(self.selected))
        if not self.selected or self.selected not in sources:
            return None
        source = sources[self.selected]
        samples, sample = source['samples'], source['samples'][-1]
        received_age = time.monotonic()-source['received']
        stale = received_age > max(3., sample.get('interval_s', 1.)*3)
        alive = get(sample, 'process.alive')
        state = 'RECORDED' if replay else 'EXITED' if not alive else 'STALE' if stale else 'LIVE'
        self.badge.configure(text=state, fg=TEAL if state == 'LIVE' else RED if state in ('STALE', 'EXITED') else GOLD)
        cores = get(sample, 'system.core_percent', [])
        temps = get(sample, 'system.temperatures', [])
        hottest = max((x['celsius'] for x in temps if isinstance(x.get('celsius'), (int, float))), default=None)
        self.resources.configure(text=
            f"Node CPU {fmt(get(sample, 'process.cpu_percent'), '%')}   ·   busiest core {fmt(max(cores) if cores else None, '%')}\n"
            f"Node RAM {fmt(get(sample, 'process.rss_bytes'), ' MiB', scale=2**20)}   ·   available {fmt(get(sample, 'system.memory_available_bytes'), ' GiB', scale=2**30)}\n"
            f"Swap {fmt(get(sample, 'system.swap_used_bytes'), ' MiB', scale=2**20)}   ·   hottest sensor {fmt(hottest, ' °C')}   ·   disk free {fmt(get(sample, 'system.disk_free_bytes'), ' GiB', scale=2**30)}")
        for chart in (self.cpu, self.memory, self.timing):
            chart.draw(samples)
        observer = sample['observer']
        control = observer.get('control') or {}
        faults = ', '.join(f['code'] for f in control.get('faults', [])) or 'none reported'
        provenance = get(sample, 'observer.chassis.simulated')
        provenance = 'simulation' if provenance is True else 'hardware' if provenance is False else 'unknown'
        receipt = get(sample, 'observer.control_receipt_age_ms')
        log_error = get(sample, 'monitor.log_error', '')
        message = (f"{provenance.upper()} · {control.get('controller_state', 'Waiting for OC')} · faults: {faults}\n"
                   f"State receipt age {fmt(receipt, ' ms')} · state rate {fmt(get(sample, 'observer.topics.state.rate_hz'), ' Hz')}\n"
                   f"Observer CPU {fmt(get(sample, 'monitor.cpu_percent'), '%')} · collection {fmt(get(sample, 'monitor.collection_ms'), ' ms')}")
        if log_error:
            message += '\nRecording error: '+log_error
        if stale and not replay:
            message += f'\nNo performance samples received for {received_age:.1f}s; values above are last known.'
        self.health.configure(text=message, fg=RED if log_error or (stale and not replay) else MUTED)
        return sample


def details(sample):
    if not sample:
        return {}
    result = {
        'Host / process': f"{sample.get('hostname')} / {get(sample, 'process.pid')}",
        'ROS namespace / domain': f"{sample.get('namespace')} / {sample.get('domain')}",
        'Process status': get(sample, 'process.status', 'N/A'),
        'Node threads': get(sample, 'process.threads'),
        'Node file descriptors': get(sample, 'process.file_descriptors'),
        'Node context switches / s': fmt(get(sample, 'process.context_switches_per_s')),
        'Host CPU I/O wait': fmt(get(sample, 'system.cpu_iowait_percent'), '%'),
        'CPU frequency': fmt(get(sample, 'system.cpu_frequency_mhz'), ' MHz'),
        'Temperature/frequency sensor age': fmt(get(sample, 'system.sensor_age_s'), ' s'),
        'Host swap in / out': fmt(get(sample, 'system.swap_io.sin_per_s'), ' KiB/s', scale=1024)+' / '+fmt(get(sample, 'system.swap_io.sout_per_s'), ' KiB/s', scale=1024),
        'Host disk read / write': fmt(get(sample, 'system.disk_io.read_bytes_per_s'), ' KiB/s', scale=1024)+' / '+fmt(get(sample, 'system.disk_io.write_bytes_per_s'), ' KiB/s', scale=1024),
        'Serial port open (OC reports)': get(sample, 'observer.control.arduino_connected'),
        'Serial error (last diagnostic)': get(sample, 'observer.diagnostics.values.serial_error', 'none received'),
        'Diagnostic receipt age': fmt(get(sample, 'observer.diagnostics_receipt_age_ms'), ' ms'),
        'Diagnostic message': get(sample, 'observer.diagnostics.message', 'N/A'),
        'OC input age (last reported)': fmt(get(sample, 'observer.control.input_age_ms'), ' ms'),
        'MCU feedback age (last reported)': fmt(get(sample, 'observer.control.feedback_age_ms'), ' ms'),
        'Latest distinct state receipt age': fmt(get(sample, 'observer.control_receipt_age_ms'), ' ms'),
        'Latest distinct chassis receipt age': fmt(get(sample, 'observer.chassis_receipt_age_ms'), ' ms'),
        'OC boot ID': get(sample, 'observer.control.boot_id'),
        'MCU boot ID': get(sample, 'observer.control.arduino_boot_id'),
        'OC authority generation': get(sample, 'observer.control.generation'),
        'Monitor sample duration': fmt(get(sample, 'monitor.collection_ms'), ' ms'),
        'Monitor RAM': fmt(get(sample, 'monitor.rss_bytes'), ' MiB', scale=2**20),
        'Local log': get(sample, 'monitor.log_path'),
        'Log records dropped': get(sample, 'monitor.log_dropped'),
        'Log error': get(sample, 'monitor.log_error', ''),
        'Events omitted (bounded queue)': get(sample, 'observer.events_dropped'),
    }
    for topic, metrics in get(sample, 'observer.topics', {}).items():
        for field, unit in [('rate_hz', ' Hz'), ('age_ms', ' ms'), ('max_gap_ms', ' ms'), ('p95_gap_ms', ' ms'),
                            ('unobserved_sequences', ''), ('duplicate_or_reversed', ''), ('identity_changes', '')]:
            result[f'{topic} / {field}'] = fmt(metrics.get(field), unit)
    for name, values in get(sample, 'system.network', {}).items():
        result[f'Host NIC {name} / RX / TX'] = fmt(values.get('bytes_recv_per_s'), ' KiB/s', scale=1024)+' / '+fmt(values.get('bytes_sent_per_s'), ' KiB/s', scale=1024)
        result[f'Host NIC {name} / drop in / out'] = fmt(values.get('dropin_per_s'), '/s')+' / '+fmt(values.get('dropout_per_s'), '/s')
        result[f'Host NIC {name} / errors in / out'] = fmt(values.get('errin_per_s'), '/s')+' / '+fmt(values.get('errout_per_s'), '/s')
    return result


class Dashboard:
    def __init__(self, root, store, namespace, replay=False, note=''):
        self.root, self.store, self.replay = root, store, replay
        root.title('Mini Rover · Performance inspection')
        root.geometry('1440x900')
        root.minsize(1100, 760)
        root.configure(background=BG)
        style = ttk.Style(root)
        style.theme_use('clam')
        style.configure('TNotebook', background=BG, borderwidth=0)
        style.configure('TNotebook.Tab', background=PANEL, foreground=INK, padding=(16, 9))
        style.map('TNotebook.Tab', background=[('selected', GRID)])
        style.configure('Treeview', background=PANEL, fieldbackground=PANEL, foreground=INK, rowheight=27)
        style.configure('Treeview.Heading', background=GRID, foreground=INK)
        header = tk.Frame(root, bg=BG, padx=22, pady=14)
        header.pack(fill='x')
        tk.Label(header, text='Performance inspection', bg=BG, fg=INK, font=('Sans', 23, 'bold')).pack(anchor='w')
        tk.Label(header, text=f'{"Recorded session" if replay else "Live observation"}  ·  {namespace}  ·  Up to five-minute trends per source  {note}',
                 bg=BG, fg=MUTED, font=('Sans', 11)).pack(anchor='w', pady=(5, 0))
        notebook = ttk.Notebook(root)
        notebook.pack(fill='both', expand=True, padx=18)
        overview = tk.Frame(notebook, bg=BG)
        notebook.add(overview, text='Overview')
        overview.grid_columnconfigure((0, 1), weight=1, uniform='hosts')
        overview.grid_rowconfigure(0, weight=1)
        self.panels = {role: HostPanel(overview, role) for role in ('c2', 'oc')}
        for col, panel in enumerate(self.panels.values()):
            panel.grid(row=0, column=col, sticky='nsew', padx=(0, 8) if col == 0 else (8, 0), pady=12)
        metric_frame = ttk.Frame(notebook)
        notebook.add(metric_frame, text='All measurements')
        self.table = self.tree(metric_frame, ('Measurement', 'C2', 'OC'), (420, 440, 440))
        event_frame = ttk.Frame(notebook)
        notebook.add(event_frame, text='Faults & events')
        self.events = self.tree(event_frame, ('Time on source', 'Host / role', 'Event', 'Details'), (180, 220, 170, 650))
        footer = ('Read-only observers · host NIC traffic includes other applications · sequence gaps are unobserved values, not measured packet loss.\n'
                  'Reported ages use OC’s clock; receipt ages use each monitor’s clock. Unsynchronized network transit and callback execution time are not measured.')
        tk.Label(root, text=footer, bg=BG, fg=MUTED, justify='left', anchor='w', font=('Sans', 9), padx=22, pady=10).pack(fill='x')
        self.last_event_keys = None
        self.refresh()

    @staticmethod
    def tree(parent, columns, widths):
        tree = ttk.Treeview(parent, columns=columns, show='headings')
        for column, width in zip(columns, widths):
            tree.heading(column, text=column)
            tree.column(column, width=width, minwidth=90, anchor='w')
        scroll = ttk.Scrollbar(parent, orient='vertical', command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        tree.pack(fill='both', expand=True)
        return tree

    def refresh(self):
        sources = self.store.snapshot()
        latest = {role: panel.update_source(sources, self.replay) for role, panel in self.panels.items()}
        values = {role: details(sample) for role, sample in latest.items()}
        keys = list(dict.fromkeys([*values['c2'], *values['oc']]))
        current = self.table.get_children()
        if len(current) != len(keys):
            self.table.delete(*current)
            for index in range(len(keys)):
                self.table.insert('', 'end', iid=str(index))
        for index, key in enumerate(keys):
            self.table.item(str(index), values=(key, *[str(values[r].get(key, 'N/A')) if values[r].get(key) is not None else 'N/A' for r in ('c2', 'oc')]))
        event_rows = []
        for source in sources.values():
            for sample in source['samples']:
                for event in get(sample, 'observer.events', []):
                    event_rows.append((event['wall_time_ns'], sample['hostname']+' / '+sample['role'].upper(), event['kind'], event['message']))
        event_rows = sorted(event_rows)[-500:]
        if event_rows != self.last_event_keys:
            self.events.delete(*self.events.get_children())
            for stamp, host, kind, message in reversed(event_rows):
                self.events.insert('', 'end', values=(datetime.fromtimestamp(stamp/1e9).strftime('%H:%M:%S.%f')[:-3], host, kind, message))
            self.last_event_keys = event_rows
        self.root.after(1000, self.refresh)


def load_recordings(store, paths):
    count = skipped = 0
    for path in paths:
        with path.expanduser().open() as stream:
            for line in stream:
                try:
                    sample = json.loads(line)
                    accepted = store.add(sample, received=sample.get('wall_time_ns', 0)/1e9)
                    count += int(accepted)
                    skipped += int(not accepted)
                except (ValueError, TypeError, AttributeError):
                    skipped += 1
    return count, skipped


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--namespace', default='/rover1')
    parser.add_argument('--replay', type=Path, nargs='+', help='Read JSONL files, oldest rotation first; no ROS connection')
    # ROS launch appends remapping arguments. Keep them for rclpy, remove for argparse.
    arguments = sys.argv[1:] if argv is None else argv
    cli = arguments[:arguments.index('--ros-args')] if '--ros-args' in arguments else arguments
    args = parser.parse_args(cli)
    store = SampleStore()
    node = executor = worker = None
    note = ''
    if args.replay:
        try:
            count, skipped = load_recordings(store, args.replay)
        except OSError as exc:
            parser.error(str(exc))
        if not count:
            parser.error('No supported performance samples found')
        note = f'· {count} samples loaded; {skipped} skipped'
    else:
        import rclpy
        from rclpy.executors import SingleThreadedExecutor
        from rclpy.node import Node
        from std_msgs.msg import String
        from .monitor import qos
        rclpy.init(args=arguments)
        node = Node('performance_dashboard', namespace='/'+args.namespace.strip('/'))
        def receive(message):
            if len(message.data) > 262144:
                return
            try:
                store.add(json.loads(message.data))
            except (ValueError, TypeError):
                pass
        node.create_subscription(String, 'inspection/metrics', receive, qos())
        executor = SingleThreadedExecutor()
        executor.add_node(node)
        def spin():
            try:
                executor.spin()
            except rclpy.executors.ExternalShutdownException:
                pass
        worker = threading.Thread(target=spin, daemon=True)
        worker.start()
    try:
        root = tk.Tk()
        Dashboard(root, store, args.namespace, replay=bool(args.replay), note=note)
        root.mainloop()
    except tk.TclError as exc:
        print(f'Dashboard needs a graphical desktop: {exc}', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        pass
    finally:
        if executor:
            executor.shutdown(timeout_sec=2)
        if worker:
            worker.join(timeout=2)
        if node:
            node.destroy_node()
            if rclpy.ok():
                rclpy.shutdown()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
