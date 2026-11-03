"""Opt-in guarded UDP transport for Matthew's motor wiring. No auto-enable."""
from collections import OrderedDict
import ipaddress
import json
import math
import secrets
import socket
import time


OPS = {"HELLO", "PING", "ARM", "DRIVE", "DISARM", "STOP", "RESET"}


def packet(session, seq, epoch, op, throttle=0, steering=0):
    if len(session) != 16 or any(c not in "0123456789abcdef" for c in session):
        raise ValueError("Invalid session")
    if op not in OPS or type(seq) is not int or not 0 < seq <= 0xffffffff or type(epoch) is not int or not 0 <= epoch <= 0xffffffff:
        raise ValueError("Invalid command identity")
    if any(type(n) is not int for n in (throttle, steering)) or max(abs(throttle+steering), abs(throttle-steering)) > 64:
        raise ValueError("Commands exceed the 25% bench wheel limit")
    if op != "DRIVE" and (throttle or steering):
        raise ValueError("Only DRIVE may contain motion")
    return f"C2V1,{session},{seq},{epoch},{op},{throttle},{steering}\n".encode("ascii")


class RoverLink:
    """Nonblocking socket serviced by Tk. Only the GUI tick sends drive heartbeats.

    If Tk stalls, transmission stops and the receiver's local watchdog must stop
    the motors. A background thread must NOT repeat the last drive command.
    """
    def __init__(self):
        self.sock = None
        self.target = None
        self.session = ""
        self.seq = 0
        self.state = None
        self.last_rx = -math.inf
        self.started = 0
        self.last_tx = -math.inf
        self.last_state_id = -1
        self.sent = OrderedDict()
        self.fault = None
        self.pending_arm = None
        self.disarm_pending = False
        self.rtt_ms = None
        self.rejected = 0
        self.gps_lines = []
        self.description = "SIMULATION • motor transport disconnected"

    @property
    def connected(self):
        return self.sock is not None

    def connect(self, host, port=4210, now=None):
        self.close()
        ip = ipaddress.IPv4Address(host.strip())
        if ip.is_multicast or ip.is_unspecified or str(ip) == "255.255.255.255":
            raise ValueError("Enter the rover's unicast IPv4 address")
        port = int(port)
        if not 1 <= port <= 65535:
            raise ValueError("Port must be between 1 and 65535")
        self.__init__()
        self.session = secrets.token_hex(8)
        self.target = (str(ip), port)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setblocking(False)
        self.sock.bind(("0.0.0.0", 0))
        self.started = time.monotonic() if now is None else now
        self.description = "VERIFYING • waiting for guarded firmware acknowledgment"
        self.send("HELLO", now=self.started)

    def send(self, op, throttle=0, steering=0, now=None):
        if self.sock is None:
            return None
        now = time.monotonic() if now is None else now
        self.seq += 1
        epoch = self.state["epoch"] if self.state else 0
        data = packet(self.session, self.seq, epoch, op, throttle, steering)
        self.sock.sendto(data, self.target)
        self.sent[self.seq] = now
        while len(self.sent) > 256:
            self.sent.popitem(last=False)
        self.last_tx = now
        return self.seq

    def ready(self, now):
        if not self.connected or self.fault or self.state is None:
            return False
        acknowledged_at = self.sent.get(self.state["seq"], -math.inf)
        return now-self.last_rx < .35 and now-acknowledged_at < .35

    def request_enable(self, now):
        if not self.ready(now):
            raise ValueError("No fresh rover acknowledgment; connect/check firmware first")
        if self.state["stopped"]:
            raise ValueError("Rover STOP is latched; Reset STOP and wait for confirmation")
        if self.state["enabled"] or self.disarm_pending:
            raise ValueError("Wait for rover to confirm disabled, then enable again")
        self.pending_arm = (self.send("ARM", now=now), now)

    def disable(self, latch=False):
        self.pending_arm = None
        if self.sock:
            try:
                self.send("STOP" if latch else "DISARM")
                self.disarm_pending = True
            except (OSError, ValueError) as exc:
                self.fault = f"Stop transmission failed ({type(exc).__name__})"

    def reset_stop(self):
        if not self.ready(time.monotonic()) or self.state["enabled"]:
            raise ValueError("Need fresh disabled rover state before resetting STOP")
        self.send("RESET")

    def receive(self, now):
        for _ in range(32):
            try:
                data, address = self.sock.recvfrom(2048)
            except BlockingIOError:
                return
            if address != self.target:
                self.rejected += 1
                continue
            try:
                row = json.loads(data)
                if not isinstance(row, dict) or row.get("v") != 1 or row.get("session") != self.session:
                    raise ValueError("Wrong protocol/session")
                if row.get("type") == "gps_nmea":
                    line = row.get("nmea")
                    if isinstance(line, str) and len(line) <= 120 and line.startswith("$"):
                        self.gps_lines.append(line)
                        self.gps_lines = self.gps_lines[-64:]
                    continue  # GPS is never a motor acknowledgment.
                if row.get("type") != "state" or row.get("fw") != "c2-guard-v1" or row.get("watchdog_ms") != 250:
                    raise ValueError("Expected guarded rover firmware")
                for field in ("seq", "epoch", "state_id", "throttle", "steering"):
                    if type(row.get(field)) is not int:
                        raise ValueError("Invalid numeric field")
                if row["seq"] not in self.sent or row["state_id"] <= self.last_state_id or not 0 < row["epoch"] <= 0xffffffff:
                    raise ValueError("Old/unknown acknowledgment")
                if self.state and row["seq"] < self.state["seq"]:
                    raise ValueError("Regressed command acknowledgment")
                if any(type(row.get(f)) is not bool for f in ("enabled", "stopped")) or not isinstance(row.get("reason"), str):
                    raise ValueError("Invalid state")
                if max(abs(row["throttle"]+row["steering"]), abs(row["throttle"]-row["steering"])) > 64:
                    raise ValueError("Applied drive out of bounds")
                if not row["enabled"] and (row["throttle"] or row["steering"]):
                    raise ValueError("Disabled but nonzero drive")
                if row["enabled"] and row["stopped"]:
                    raise ValueError("Stopped but enabled")
                enc = row.get("encoders")
                if enc is not None and (not isinstance(enc, list) or len(enc) != 4 or any(v is not None and (type(v) is not int or not 0 <= v <= 0xffffffff) for v in enc)):
                    raise ValueError("Invalid encoder counts")
                self.state = row
                self.last_state_id = row["state_id"]
                self.last_rx = now
                self.rtt_ms = max(0, (now-self.sent[row["seq"]])*1000)
                if not row["enabled"]:
                    self.disarm_pending = False
                if self.pending_arm and row["seq"] >= self.pending_arm[0] and row["enabled"]:
                    self.pending_arm = None
            except (ValueError, TypeError, KeyError, UnicodeError):
                self.rejected += 1

    def tick(self, result, now=None):
        now = time.monotonic() if now is None else now
        if not self.sock:
            return
        try:
            self.receive(now)
            if self.state is None:
                if now-self.started > 3:
                    self.fault = "No guarded firmware reply; stock v6 has no acknowledgment. Install the companion sketch."
                elif now-self.last_tx >= .25:
                    self.send("HELLO", now=now)
                return
            if not self.ready(now):
                self.fault = self.fault or "Rover acknowledgment stale; disconnect/reconnect and enable deliberately"
            if self.pending_arm and now-self.pending_arm[1] >= .25:
                self.fault = "Rover did not confirm enable; reconnect before retrying"
            if self.fault:
                if now-self.last_tx >= .05:
                    self.send("STOP", now=now)
                self.pending_arm = None
                return
            if not result["enabled"]:
                if self.pending_arm or self.state["enabled"]:
                    self.pending_arm = None
                    self.disarm_pending = True
                    self.send("STOP" if result["stop_latched"] else "DISARM", now=now)
                elif result["stop_latched"] and not self.state["stopped"]:
                    self.send("STOP", now=now)
                elif now-self.last_tx >= .05:
                    self.send("PING", now=now)
            elif self.pending_arm:
                pass  # No movement until ARM has been acknowledged.
            elif self.state["enabled"] and not self.disarm_pending:
                if now-self.last_tx >= .045:
                    t, s = map(int, result["legacy_drive_preview"].strip().split(","))
                    self.send("DRIVE", t, s, now)
            else:
                self.fault = "Rover disabled motion; re-establish connection before enabling"
        except (OSError, ValueError) as exc:
            self.fault = f"Rover link error: {type(exc).__name__}"

    def summary(self, now):
        if not self.connected:
            return self.description
        if self.fault:
            return "FAULT • " + self.fault
        if self.state is None:
            return self.description
        state = self.state
        counts = state.get("encoders")
        encoders = " / ".join("N/A" if v is None else str(v) for v in counts) if counts else "unavailable"
        return (f"ROVER {'ENABLED' if state['enabled'] else 'DISABLED'} • {state['reason']} • "
                f"ack #{state['seq']} • age {(now-self.last_rx)*1000:.0f} ms • "
                f"applied T {state['throttle']} / S {state['steering']} PWM\n"
                f"Encoders FL/RL/FR/RR: {encoders} • counts, not signed speed")

    def close(self):
        if self.sock:
            try:
                for _ in range(3):
                    self.send("STOP")
            except (OSError, ValueError):
                pass
            finally:
                self.sock.close()
                self.sock = None
        self.description = "SIMULATION • motor transport disconnected"
        self.pending_arm = None
