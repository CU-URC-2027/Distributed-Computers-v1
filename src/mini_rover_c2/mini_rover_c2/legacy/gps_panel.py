"""Explicit serial/GPS input, bounded logs and stationary test display."""
from collections import deque
import json
from pathlib import Path
from queue import Queue, Empty, Full
import threading
import time
import tkinter as tk
from tkinter import ttk
from mini_rover_c2.legacy.gps_input import GPSData


class SerialReader:
    def __init__(self, port, baud):
        self.queue = Queue(maxsize=512)
        self.done = threading.Event()
        self.dropped = 0
        self.thread = threading.Thread(target=self.run, args=(port, baud), daemon=True)
        self.thread.start()

    def emit(self, item):
        try:
            self.queue.put_nowait(item)
        except Full:
            self.dropped += 1

    def run(self, port, baud):
        try:
            import serial
            with serial.Serial(port, baud, timeout=.2, write_timeout=.2) as stream:
                self.emit(("connected", port, time.monotonic()))
                while not self.done.is_set():
                    data = stream.readline(161)
                    if data:
                        self.emit(("line", data.decode("ascii", errors="replace").strip(), time.monotonic()))
        except Exception as exc:
            self.emit(("error", f"Serial unavailable ({type(exc).__name__}); close Serial Monitor and check the port", time.monotonic()))

    def close(self):
        self.done.set()
        self.thread.join(timeout=.3)


class GPSPanel(ttk.LabelFrame):
    def __init__(self, parent, draw_track):
        super().__init__(parent, text="LIVE GPS / GP-20U7 bench test", padding=8)
        self.draw_track = draw_track
        self.reader = None
        self.source = "disconnected"
        self.error = None
        self.data = GPSData()
        self.records = deque(maxlen=20000)
        self.started = time.monotonic()
        bar = ttk.Frame(self)
        bar.pack(fill="x")
        self.port = tk.StringVar()
        self.port_menu = ttk.Combobox(bar, textvariable=self.port, width=12)
        self.port_menu.pack(side="left")
        self.baud = tk.StringVar(value="115200")
        ttk.Combobox(bar, textvariable=self.baud, values=("115200", "9600"), width=8, state="readonly").pack(side="left", padx=4)
        self.buttons = {}
        for key, label, command in (("ports", "Scan ports", self.scan), ("connect", "Connect USB GPS", self.connect),
                                    ("rover", "Use rover GPS", self.use_rover), ("disconnect", "Disconnect GPS", self.disconnect)):
            button = ttk.Button(bar, text=label, command=command)
            button.pack(side="left", padx=3)
            self.buttons[key] = button
        actions = ttk.Frame(self)
        actions.pack(fill="x", pady=4)
        for key, label, command in (("reset", "Start new GPS run", self.reset), ("save", "Save GPS run", self.save)):
            button = ttk.Button(actions, text=label, command=command)
            button.pack(side="left", padx=3)
            self.buttons[key] = button
        self.note = ttk.Label(actions, text="USB bridge: 115200 baud; direct GPS adapter: 9600", wraplength=650)
        self.note.pack(side="left", padx=8)
        self.status = ttk.Label(self, text="GPS disconnected", wraplength=1080)
        self.status.pack(anchor="w", pady=5)
        self.canvas = tk.Canvas(self, bg="#172e3f", height=160, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.scan()

    def scan(self):
        try:
            from serial.tools import list_ports
            ports = [p.device for p in list_ports.comports()]
            self.port_menu.config(values=ports)
            if not self.port.get() and ports:
                self.port.set(ports[0])
            self.note.config(text=f"{len(ports)} serial ports found. Select the Uno; close Serial Monitor before connecting.")
        except ImportError:
            self.note.config(text="USB GPS needs pyserial in this Python environment.")

    def reset(self):
        self.data = GPSData()
        self.records.clear()
        self.started = time.monotonic()
        self.note.config(text="New run. Keep antenna stationary for 5 minutes to measure scatter, not absolute accuracy.")

    def connect(self):
        self.disconnect()
        if not self.port.get().strip():
            self.note.config(text="Select the Arduino serial port first.")
            return
        self.reset()
        self.reader = SerialReader(self.port.get().strip(), int(self.baud.get()))
        self.source = "USB connecting"

    def use_rover(self):
        self.disconnect()
        self.reset()
        self.source = "Rover UDP"
        self.note.config(text="Rover GPS requires optional GPS firmware setting and documented D0/D1 rewiring.")

    def disconnect(self):
        if self.reader:
            self.reader.close()
        self.reader = None
        self.source = "disconnected"
        self.error = None
        self.data.valid = False

    def consume(self, line, now):
        parsed = self.data.consume(line, now)
        if line.startswith("$"):
            self.records.append(dict(type="gps_capture", source=self.source,
                                     receipt_monotonic_s=now, nmea=line, parsed=parsed))

    def tick(self, now, rover_lines=(), rover_ready=False):
        if self.reader:
            for _ in range(100):
                try:
                    kind, value, received = self.reader.queue.get_nowait()
                except Empty:
                    break
                if kind == "connected":
                    self.source = "USB " + value
                elif kind == "error":
                    self.error = value
                    self.data.valid = False
                elif kind == "line":
                    self.consume(value, received)
        if self.source == "Rover UDP":
            if rover_ready:
                for line in rover_lines:
                    self.consume(line, now)
            else:
                self.data.valid = False
        data = self.data
        fresh = data.fresh(now) and self.source != "disconnected" and not self.error
        location = f"Lat {data.latitude:.7f} / Lon {data.longitude:.7f}" if fresh else "Position unavailable / invalid / stale"
        age = f"{now-data.last_fix:.1f}s" if data.last_fix > 0 else "—"
        scatter = data.scatter()
        accuracy = f"Stationary scatter RMS {scatter[2]:.2f} m about sample mean" if scatter else "Need at least 2 valid GGA fixes for scatter"
        dropped = self.reader.dropped if self.reader else 0
        speed = data.speed_mps if now-getattr(data, "last_speed", -1e9) < 2.5 else None
        self.status.config(text=(self.error or f"{self.source} • {'VALID FIX' if fresh else 'NO FRESH FIX'} • {location}") +
                           f"\nFix receipt age {age} • satellites {data.satellites} • HDOP {data.hdop} • speed m/s {speed} • NMEA bad {data.bad} / duplicate {data.duplicates} / queue drops {dropped}" +
                           f"\nValid samples {len(data.samples)} • {accuracy} • elapsed {now-self.started:.0f}s")
        points = [(p[1], p[2]) for p in data.samples]
        self.draw_track(self.canvas, points, [])
        self.canvas.itemconfigure("legend", text="LIVE GPS • East / North metres • origin = first valid fix")

    def save(self):
        if not self.records:
            self.note.config(text="No GPS data to save yet.")
            return
        try:
            folder = Path(__file__).parent / "logs"
            folder.mkdir(exist_ok=True)
            path = folder / f"gps-{time.time_ns()}.jsonl"
            path.write_text("".join(json.dumps(row, allow_nan=False)+"\n" for row in self.records), encoding="utf-8")
            self.note.config(text=f"Saved {len(self.records)} recent raw/parsed NMEA records: {path.name}")
        except (OSError, ValueError) as exc:
            self.note.config(text=f"Could not save GPS log ({type(exc).__name__})")
