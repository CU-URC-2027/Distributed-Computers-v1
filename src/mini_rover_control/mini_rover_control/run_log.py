"""Bounded, asynchronous whole-run recorder; logging never blocks control timers."""
import json
import math
from pathlib import Path
from queue import Queue, Full
import threading
import time


def json_safe(value):
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    return value


class RunLog:
    def __init__(self, directory):
        directory = Path(directory).expanduser()
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / f'run-{time.time_ns()}.jsonl'
        self.stream = self.path.open('x', buffering=1)
        self.queue = Queue(maxsize=4096)
        self.error = ''
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def write(self, kind, value):
        if self.error:
            return
        try:
            self.queue.put_nowait(dict(kind=kind, receipt_wall_ns=time.time_ns(),
                                       receipt_monotonic_ns=time.monotonic_ns(), data=json_safe(value)))
        except Full:
            self.error = 'Log queue full'

    def _run(self):
        try:
            while True:
                row = self.queue.get()
                if row is None:
                    break
                self.stream.write(json.dumps(row, allow_nan=False, separators=(',', ':')) + '\n')
        except (OSError, ValueError) as exc:
            self.error = str(exc)
        finally:
            self.stream.close()

    def close(self):
        if self.thread.is_alive():
            try:
                self.queue.put(None, timeout=1)
            except Full:
                self.error = 'Log shutdown queue full'
            self.thread.join(timeout=2)
