"""Separate-process PTY Arduino. Fault controls use a local JSON file, never ROS."""
import argparse
from collections import deque
import errno
import json
import os
from pathlib import Path
import pty
import signal
import time
import tty
from mini_rover_control.arduino import ArduinoController
from mini_rover_control.contracts import Config, WHEELS
from mini_rover_control.protocol import Decoder, encode


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port-link', default='/tmp/mini-rover-arduino')
    parser.add_argument('--fault-file', default='')
    parser.add_argument('--response-time', type=float, default=.2)
    parser.add_argument('--pwm-limit', type=int, default=63)
    args = parser.parse_args(argv)
    link = Path(args.port_link).absolute()
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.exists() or link.is_symlink():
        if not link.is_symlink() or not str(link.readlink()).startswith('/dev/pts/'):
            parser.error(f'Refusing to replace a non-PTY path: {link}')
        # An extant target could belong to another running simulator.
        if link.exists():
            parser.error(f'PTY link already active: {link}')
        link.unlink()
    master, slave = pty.openpty()
    tty.setraw(slave)
    os.set_blocking(master, False)
    device = os.ttyname(slave)
    link.symlink_to(device)
    controller = ArduinoController(Config(response_time=args.response_time, pwm_limit=args.pwm_limit))
    decoder = Decoder()
    running = True
    def stop(*_):
        nonlocal running
        running = False
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    pending, pending_at = b'', 0.
    next_sample = time.monotonic()
    next_fault = 0.
    faults = {}
    delayed = deque(maxlen=32)
    print(f'SIMULATED Arduino: {link} -> {device}; no physical motors', flush=True)
    try:
        while running:
            now = time.monotonic()
            if args.fault_file and now >= next_fault:
                next_fault = now + .1
                try:
                    value = json.loads(Path(args.fault_file).read_text())
                    if not isinstance(value, dict):
                        raise ValueError('Fault file must be an object')
                    for key in ('missing', 'stalled'):
                        if not isinstance(value.get(key, []), list) or not set(value.get(key, [])) <= set(WHEELS):
                            raise ValueError('Unknown wheel in fault file')
                    delay = value.get('delay_ms', 0)
                    if type(delay) not in (int, float) or not 0 <= delay <= 2000:
                        raise ValueError('delay_ms must be between 0 and 2000')
                    faults = value
                except FileNotFoundError:
                    faults = {}
                except (OSError, ValueError, TypeError) as exc:
                    print(f'Fault file ignored: {exc}', flush=True)
            controller.missing = set(faults.get('missing', []))
            controller.model.stalled = set(faults.get('stalled', []))
            controller.tick()
            try:
                data = os.read(master, 4096)
            except BlockingIOError:
                data = b''
            except OSError as exc:
                if exc.errno != errno.EIO:
                    raise
                data = b''
            for packet in decoder.feed(data):
                if not faults.get('drop_commands', False):
                    if controller.handle(packet):
                        next_sample = min(next_sample, now)
            if pending:
                try:
                    n = os.write(master, pending)
                    pending = pending[n:]
                except BlockingIOError:
                    pass
                if now - pending_at > .1:
                    # Finish a truncated line before a subsequent record, never append to it.
                    pending = b'\n'
                    pending_at = now
            if now >= next_sample:
                next_sample = now + .1
                if not faults.get('silence', False) and len(delayed) < delayed.maxlen:
                    delayed.append((now + faults.get('delay_ms', 0)/1000, encode(controller.sample())))
                elif faults.get('silence', False):
                    delayed.clear()
            if not pending and delayed and delayed[0][0] <= now:
                _, pending = delayed.popleft()
                pending_at = now
            time.sleep(.005)
    finally:
        controller.disable('SHUTDOWN')
        os.close(master)
        os.close(slave)
        if link.is_symlink() and str(link.readlink()) == device:
            link.unlink()


if __name__ == '__main__':
    main()
