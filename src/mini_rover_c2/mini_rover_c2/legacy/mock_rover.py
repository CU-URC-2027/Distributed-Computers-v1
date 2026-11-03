"""Loopback-only rover test endpoint. No GPIO, serial or motor access."""
import json
import socket
import threading
import time


class MockRover:
    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(.02)
        self.port = self.sock.getsockname()[1]
        self.stop_event = threading.Event()
        self.silent = False
        self.peer = None
        self.session = ""
        self.seq = 0
        self.epoch = 1
        self.state_id = 0
        self.enabled = self.stopped = False
        self.throttle = self.steering = 0
        self.reason = "STARTUP"
        self.last_drive = self.last_owner = 0
        self.received = []
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def disable(self, reason, latch=False):
        if self.enabled or (latch and not self.stopped):
            self.epoch += 1
        self.enabled = False
        self.stopped |= latch
        self.throttle = self.steering = 0
        self.reason = reason

    def handle(self, data, address, now):
        from mini_rover_c2.legacy.rover_link import packet
        try:
            marker, session, seq, epoch, op, t, s = data.decode("ascii").strip().split(",")
            seq, epoch, t, s = map(int, (seq, epoch, t, s))
            if marker != "C2V1":
                return
            packet(session, seq, epoch, op, t, s)  # same envelope limits
        except (ValueError, UnicodeError):
            return
        if op == "HELLO" and session != self.session:
            if self.enabled or (self.session and now-self.last_owner < 1):
                return
            self.session, self.seq = session, 0
            self.epoch += 1
            self.peer = address
        if session != self.session or address != self.peer or seq <= self.seq:
            return
        if op == "ARM":
            if self.stopped or self.enabled or epoch != self.epoch:
                return
            self.enabled, self.last_drive, self.reason = True, now, "ENABLED"
        elif op == "DRIVE":
            if not self.enabled or self.stopped or epoch != self.epoch:
                return
            self.throttle, self.steering, self.last_drive = t, s, now
            self.reason = "DRIVING"
        elif op == "STOP":
            self.disable("OPERATOR_STOP", True)
        elif op == "DISARM":
            self.disable("DISARMED")
        elif op == "RESET":
            if self.enabled or epoch != self.epoch:
                return
            self.stopped = False
            self.epoch += 1
            self.reason = "RESET_DISABLED"
        self.seq, self.last_owner = seq, now
        self.received.append((op, t, s))
        self.received = self.received[-200:]

    def run(self):
        last_status = 0
        while not self.stop_event.is_set():
            now = time.monotonic()
            if self.enabled and now-self.last_drive >= .25:
                self.disable("COMMAND_TIMEOUT")
            try:
                data, address = self.sock.recvfrom(512)
                # Check expiry BEFORE an arriving packet can refresh the timer.
                now = time.monotonic()
                if self.enabled and now-self.last_drive >= .25:
                    self.disable("COMMAND_TIMEOUT")
                self.handle(data, address, now)
            except socket.timeout:
                pass
            except OSError:
                break
            if self.peer and not self.silent and now-last_status >= .02:
                self.state_id += 1
                row = dict(v=1, type="state", fw="c2-guard-v1", session=self.session,
                           seq=self.seq, epoch=self.epoch, state_id=self.state_id,
                           enabled=self.enabled, stopped=self.stopped, throttle=self.throttle,
                           steering=self.steering, encoders=None, reason=self.reason,
                           watchdog_ms=250, mock=True)
                try:
                    self.sock.sendto(json.dumps(row).encode(), self.peer)
                except OSError:
                    break
                last_status = now

    def close(self):
        self.stop_event.set()
        self.sock.close()
        self.thread.join(timeout=.2)
