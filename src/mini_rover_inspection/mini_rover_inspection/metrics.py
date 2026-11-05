"""Local measurements only: no remote-clock subtraction or control operations."""
from collections import deque
import math
from pathlib import Path
import socket
import threading
import time

import psutil


def clean(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    return value


def age(value):
    return float(value) if isinstance(value, (float, int)) and math.isfinite(value) and value >= 0 else None


def optional(call):
    try:
        return call()
    except (psutil.Error, OSError, ValueError, AttributeError, NotImplementedError):
        return None


class TopicWindow:
    """Capture bursts and gaps continuously, even between slow resource samples."""
    def __init__(self, now=None):
        self.window_at = time.monotonic() if now is None else now
        self.last_at = None
        self.count = self.total = self.unobserved = self.reordered = self.restarts = 0
        self.identity = self.seq = None
        self.gaps = deque(maxlen=2048)
        self.max_gap = 0.

    def receive(self, now, identity=None, sequence=None):
        if self.last_at is not None:
            gap = max(0., (now-self.last_at)*1000.)
            self.gaps.append(gap)
            self.max_gap = max(self.max_gap, gap)
        self.last_at = now
        self.count += 1
        self.total += 1
        if sequence is not None:
            if identity != self.identity:
                if self.identity is not None:
                    self.restarts += 1
                self.identity, self.seq = identity, sequence
            elif self.seq is not None:
                if sequence > self.seq:
                    self.unobserved += max(0, sequence-self.seq-1)
                    self.seq = sequence
                else:
                    self.reordered += 1

    def snapshot(self, now):
        gaps = sorted(self.gaps)
        since = None if self.last_at is None else max(0., (now-self.last_at)*1000.)
        result = dict(rate_hz=self.count/max(.001, now-self.window_at), count=self.count,
                      total=self.total, age_ms=since, max_gap_ms=max(self.max_gap, since) if since is not None else None,
                      p95_gap_ms=gaps[max(0, math.ceil(.95*len(gaps))-1)] if gaps else None,
                      unobserved_sequences=self.unobserved, duplicate_or_reversed=self.reordered,
                      identity_changes=self.restarts)
        self.window_at, self.count, self.max_gap = now, 0, 0.
        self.gaps.clear()
        return result


class SystemSampler:
    def __init__(self, pid, disk_path):
        self.target = psutil.Process(pid)
        self.created = self.target.create_time()
        self.disk_path = str(Path(disk_path).expanduser())
        self.previous = {}
        self.target.cpu_percent(None)
        self.self_process = psutil.Process()
        self.self_process.cpu_percent(None)
        psutil.cpu_percent(None, percpu=True)
        psutil.cpu_times_percent(None)
        self.hostname = socket.gethostname()
        self.boot_id = optional(lambda: Path('/proc/sys/kernel/random/boot_id').read_text().strip())
        self.counter_time = time.monotonic()
        self._rates(self.counter_time)
        # Hardware sensor drivers can block for tens of milliseconds. Keep
        # them away from the ROS observation thread and poll at a slower rate.
        self.sensor_reading = (None, None, [])
        self.sensor_stop = threading.Event()
        self.sensor_thread = threading.Thread(target=self._sensors, daemon=True)
        self.sensor_thread.start()

    def _sensors(self):
        while not self.sensor_stop.is_set():
            frequencies = optional(psutil.cpu_freq)
            temperatures = optional(psutil.sensors_temperatures) or {}
            temp = [dict(sensor=key, label=t.label, celsius=t.current, high=t.high, critical=t.critical)
                    for key, values in temperatures.items() for t in values]
            self.sensor_reading = (time.monotonic(), frequencies.current if frequencies else None, temp)
            self.sensor_stop.wait(5.)

    def close(self):
        self.sensor_stop.set()
        self.sensor_thread.join(timeout=.2)

    def _delta(self, key, value, elapsed):
        old = self.previous.get(key)
        self.previous[key] = value
        return None if old is None or value < old else (value-old)/elapsed

    def _rates(self, now):
        elapsed = max(.001, now-self.counter_time)
        networks = {}
        for name, counters in (optional(lambda: psutil.net_io_counters(pernic=True)) or {}).items():
            networks[name] = {field+'_per_s': self._delta('net/'+name+'/'+field, getattr(counters, field), elapsed)
                              for field in ('bytes_sent', 'bytes_recv', 'dropin', 'dropout', 'errin', 'errout')}
        disk = optional(psutil.disk_io_counters)
        disks = {field+'_per_s': self._delta('disk/'+field, getattr(disk, field), elapsed)
                 for field in ('read_bytes', 'write_bytes')} if disk else {}
        swap = optional(psutil.swap_memory)
        swaps = {field+'_per_s': self._delta('swap/'+field, getattr(swap, field), elapsed)
                 for field in ('sin', 'sout')} if swap else {}
        self.counter_time = now
        return networks, disks, swaps, elapsed

    def sample(self, now=None):
        now = time.monotonic() if now is None else now
        network, disk_io, swap_io, elapsed = self._rates(now)
        cores = psutil.cpu_percent(None, percpu=True)
        cpu_times = psutil.cpu_times_percent(None)
        memory, swap = psutil.virtual_memory(), psutil.swap_memory()
        disk = optional(lambda: psutil.disk_usage(self.disk_path))
        sensor_at, frequency, temp = self.sensor_reading
        host = dict(cpu_percent=sum(cores)/len(cores) if cores else None, core_percent=cores,
                    cpu_iowait_percent=getattr(cpu_times, 'iowait', None),
                    load_average=optional(lambda: list(psutil.getloadavg())),
                    memory_total_bytes=memory.total, memory_available_bytes=memory.available,
                    memory_percent=memory.percent, swap_used_bytes=swap.used, swap_io=swap_io,
                    disk_free_bytes=disk.free if disk else None, disk_io=disk_io, network=network,
                    cpu_frequency_mhz=frequency, temperatures=temp,
                    sensor_age_s=max(0., now-sensor_at) if sensor_at is not None else None)
        process = dict(pid=self.target.pid, created=self.created, alive=False, cpu_percent=None, rss_bytes=None)
        try:
            if self.target.is_running() and self.target.create_time() == self.created:
                with self.target.oneshot():
                    status = self.target.status()
                    mem = self.target.memory_info()
                    process.update(alive=status != psutil.STATUS_ZOMBIE, status=status,
                                   cpu_percent=self.target.cpu_percent(None), rss_bytes=mem.rss,
                                   threads=self.target.num_threads(),
                                   file_descriptors=optional(self.target.num_fds))
                    ctx = optional(self.target.num_ctx_switches)
                    io = optional(self.target.io_counters)
                    process['context_switches_per_s'] = self._delta('process/ctx', ctx.voluntary+ctx.involuntary, elapsed) if ctx else None
                    process['io'] = {f+'_per_s': self._delta('process/'+f, getattr(io, f), elapsed)
                                     for f in ('read_bytes', 'write_bytes')} if io else {}
        except psutil.NoSuchProcess:
            pass
        except psutil.AccessDenied:
            process.update(alive=True, error='Process counters unavailable: access denied')
        overhead = dict(cpu_percent=self.self_process.cpu_percent(None), rss_bytes=self.self_process.memory_info().rss)
        return clean(dict(system=host, process=process, overhead=overhead))
