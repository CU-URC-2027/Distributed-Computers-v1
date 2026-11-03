"""C2 bench station: simulated motion or explicit guarded rover UDP connection."""
import argparse
from collections import deque
import json
import math
import random
from pathlib import Path
import time
import tkinter as tk
from tkinter import ttk, filedialog
from uuid import uuid4

from mini_rover_c2.legacy.core import Control, JOINTS
from mini_rover_c2.legacy.cameras import CameraPanel
from mini_rover_c2.legacy.navigation import integrate_pose, compare_log, metrics
from mini_rover_c2.legacy.rover_link import RoverLink
from mini_rover_c2.legacy.gps_panel import GPSPanel


class Station:
    def __init__(self, root, args):
        self.root = root
        self._closing = False
        self._focus_after = None
        self._loop_after = None
        self.buttons = {}
        self.cameras = []
        self.control = Control()
        self.rover = RoverLink()
        self.mock_rover = None
        self.xbox = None
        self.require_xbox = args.xbox
        self.controller_args = tuple(getattr(args, key, None) for key in ("throttle_axis", "steering_axis", "hold_button"))
        self.controller_values = None
        self.drive_hold = False
        self.keyboard_hold = False
        self.jog_hold = 0.0
        self.events = deque(maxlen=100)
        self.records = deque(maxlen=1200)
        self.path = deque(maxlen=500)
        self.x = self.y = 0.0
        self.heading = math.pi / 2
        self.session_id = str(uuid4())
        self.gps_pairs = deque(maxlen=600)
        self.gps_last = -math.inf
        self.gps_random = random.Random(42)
        self.gps_loaded = False
        self.last = time.monotonic()
        self.sequence = 0
        self.previous_reason = None
        root.title("URC C2 | Prototype 0.4 — rover link and live GPS")
        root.geometry("1220x900")
        root.minsize(1060, 850)
        root.configure(bg="#10202d")
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TFrame", background="#10202d")
        style.configure("TLabel", background="#10202d", foreground="#e3edf5", font=("Segoe UI", 11))
        style.configure("TLabelframe", background="#10202d")
        style.configure("TLabelframe.Label", background="#10202d", foreground="#6edac6")
        style.configure("TButton", padding=4)
        outer = ttk.Frame(root, padding=18)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="URC / COMMAND STATION", font=("Segoe UI", 22, "bold")).pack(anchor="w")
        self.connection_banner = ttk.Label(outer, text="SIMULATION • chassis disconnected • arm preview • GPS/cameras connect only on request")
        self.connection_banner.pack(anchor="w", pady=(3, 10))
        bar = ttk.Frame(outer)
        bar.pack(fill="x")
        self.source = tk.StringVar(value="Xbox controller" if args.xbox else "Sliders (demo)")
        menu = ttk.Combobox(bar, textvariable=self.source, values=("Sliders (demo)", "Xbox controller", "RC (demo)"), state="readonly", width=18)
        menu.pack(side="left")
        menu.bind("<<ComboboxSelected>>", self.source_changed)
        self.source_menu = menu
        self.button(bar, "enable", "Enable controls", self.enable).pack(side="left", padx=8)
        self.button(bar, "disarm", "Disarm", self.disarm).pack(side="left")
        self.button(bar, "reset", "Reset STOP", self.reset_stop).pack(side="left", padx=8)
        self.buttons["stop"] = tk.Button(bar, text="STOP  /  Esc", bg="#ca3e49", fg="white", font=("Segoe UI", 14, "bold"), command=self.stop)
        self.buttons["stop"].pack(side="right")
        self.button(bar, "input_mode", "Reconnect Xbox", self.reconnect_xbox).pack(side="left", padx=8)
        self.buttons["input_mode"].config(state="disabled")
        self.stick = tk.StringVar(value="Left")
        ttk.Label(bar, text="Drive stick:").pack(side="left")
        self.stick_menu = ttk.Combobox(bar, textvariable=self.stick, values=("Left", "Right"), state="readonly", width=6)
        self.stick_menu.pack(side="left", padx=5)
        self.stick_menu.bind("<<ComboboxSelected>>", self.stick_changed)
        if self.controller_args[0] is not None:
            self.stick.set("Raw")
            self.stick_menu.config(state="disabled")
        self.status = ttk.Label(outer, text="Disarmed", font=("Segoe UI", 12, "bold"))
        self.status.pack(anchor="w", pady=8)
        self.hint = ttk.Label(outer, text="Select Xbox controller for real input. Yellow input arrow moves before enabling. See Help for steps.", wraplength=1100)
        self.hint.pack(anchor="w", pady=(0, 8))
        self.notebook = ttk.Notebook(outer)
        self.notebook.pack(fill="both", expand=True)
        main = ttk.Frame(self.notebook, padding=10)
        camera_tab = ttk.Frame(self.notebook, padding=10)
        telemetry_tab = ttk.Frame(self.notebook, padding=10)
        help_tab = ttk.Frame(self.notebook, padding=10)
        hardware_tab = ttk.Frame(self.notebook, padding=10)
        for tab, title in ((main, "Drive & Arm"), (camera_tab, "Cameras"), (telemetry_tab, "Telemetry & Logs"), (help_tab, "Help / Next Features"), (hardware_tab, "Connections & Live GPS")):
            self.notebook.add(tab, text=title)
        self.active_tab = self.notebook.select()
        self.notebook.bind("<<NotebookTabChanged>>", self.tab_changed)
        main.columnconfigure(0, weight=1)
        main.columnconfigure(1, weight=1)
        drive = ttk.LabelFrame(main, text="CHASSIS / 25% wheel limit • check connection banner", padding=8)
        drive.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        self.throttle, self.steering = tk.DoubleVar(), tk.DoubleVar()
        self.sliders = []
        for label, var in (("Throttle: reverse / forward", self.throttle), ("Steering: left / right", self.steering)):
            ttk.Label(drive, text=label).pack(anchor="w")
            slider = ttk.Scale(drive, from_=-1, to=1, variable=var)
            slider.pack(fill="x", pady=4)
            self.sliders.append(slider)
        self.button(drive, "center", "Center sticks", self.center).pack(fill="x")
        hold = self.button(drive, "drive_hold", "Hold mouse here OR hold Space / Xbox LB", lambda: None)
        hold.pack(fill="x", pady=4)
        hold.bind("<ButtonPress-1>", lambda e: setattr(self, "drive_hold", True))
        hold.bind("<Leave>", lambda e: setattr(self, "drive_hold", False))
        self.drop = tk.BooleanVar()
        self.drop_button = ttk.Checkbutton(drive, text="Simulate input/link loss", variable=self.drop)
        self.drop_button.pack(anchor="w")
        self.drive_status = ttk.Label(drive, text="Left 0% / Right 0%")
        self.drive_status.pack(anchor="w", pady=4)
        self.input_status = ttk.Label(drive, text="SLIDERS • no physical controller input", wraplength=410)
        self.input_status.pack(anchor="w", pady=4)
        arm = ttk.LabelFrame(main, text="ARM / request to onboard computer • preview", padding=8)
        arm.grid(row=0, column=1, sticky="nsew")
        ttk.Label(arm, text="Nucleo-F446RE • DRV8825 / NEMA 17 • MG995\nJoint assignments pending; nothing sent to the arm.", wraplength=500).pack(anchor="w")
        self.joint = tk.StringVar(value=JOINTS[0])
        joint_box = ttk.Combobox(arm, textvariable=self.joint, values=JOINTS, state="readonly")
        joint_box.pack(fill="x", pady=8)
        joint_box.bind("<<ComboboxSelected>>", lambda e: self.release())
        self.joint_menu = joint_box
        self.button(arm, "arm_mode", "Toggle arm jogging / rover drive", self.toggle_arm).pack(fill="x")
        for label, rate in (("Hold: jog negative / close", -1), ("Hold: jog positive / open", 1)):
            button = self.button(arm, "jog_negative" if rate < 0 else "jog_positive", label, lambda: None)
            button.pack(fill="x", pady=6)
            button.bind("<ButtonPress-1>", lambda e, r=rate: setattr(self, "jog_hold", r))
            button.bind("<Leave>", lambda e: setattr(self, "jog_hold", 0.0))
        self.arm_status = ttk.Label(arm, text="Arm disabled")
        self.arm_status.pack(anchor="w", pady=8)
        movement = ttk.LabelFrame(main, text="LIVE INPUT & SIMULATED MOTION / metres • not measured position", padding=6)
        movement.grid(row=1, column=0, columnspan=2, sticky="nsew", pady=6)
        movement.columnconfigure(0, weight=1)
        movement.columnconfigure(1, weight=2)
        movement.rowconfigure(0, weight=1)
        self.vector = tk.Canvas(movement, width=250, height=155, bg="#172e3f", highlightthickness=0)
        self.vector.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        self.canvas = tk.Canvas(movement, width=600, height=155, bg="#172e3f", highlightthickness=0)
        self.canvas.grid(row=0, column=1, sticky="nsew")
        self.motion_status = ttk.Label(movement, text="")
        self.motion_status.grid(row=1, column=0, columnspan=2, sticky="w")
        telemetry = ttk.LabelFrame(telemetry_tab, text="GPS COMPARISON / no GPS receiver connected", padding=12)
        telemetry_tab.columnconfigure(0, weight=1)
        telemetry_tab.rowconfigure(1, weight=1)
        self.telemetry_summary = ttk.Label(telemetry_tab, text="Chassis: disconnected • battery: UNKNOWN • STM32: not connected", wraplength=1050)
        self.telemetry_summary.grid(row=0, column=0, sticky="w", pady=8)
        telemetry.grid(row=1, column=0, sticky="nsew", padx=(0, 10), pady=12)
        gps_bar = ttk.Frame(telemetry)
        gps_bar.pack(fill="x")
        self.gps_demo = tk.BooleanVar()
        self.gps_demo_button = ttk.Checkbutton(gps_bar, text="Generate synthetic GPS (1 Hz)", variable=self.gps_demo, command=self.change_gps)
        self.gps_demo_button.pack(side="left")
        self.button(gps_bar, "load_gps", "Load comparison JSONL", self.load_gps).pack(side="left", padx=8)
        self.button(gps_bar, "clear_gps", "Clear comparison", self.clear_gps).pack(side="left")
        self.gps_status = ttk.Label(telemetry, text="No comparison data. Enable the demo, return to Drive, then drive a path.", wraplength=1040)
        self.gps_status.pack(anchor="w", pady=8)
        self.gps_canvas = tk.Canvas(telemetry, width=700, height=260, bg="#172e3f", highlightthickness=0)
        self.gps_canvas.pack(fill="both", expand=True)
        ttk.Label(telemetry, text="Cyan: reference path • Orange: GPS • Lines: time-matched differences\nA command-integrated path is not ground truth; wheel slip, timing and GPS noise all contribute.", wraplength=1000).pack(anchor="w", pady=6)
        output = ttk.LabelFrame(main, text="CHASSIS TRANSPORT / arm remains preview • model is not measured motion", padding=6)
        output.grid(row=2, column=0, columnspan=2, sticky="nsew", pady=4)
        main.rowconfigure(1, weight=2, minsize=180)
        main.rowconfigure(2, weight=1, minsize=100)
        self.preview = tk.Text(output, height=5, width=45, bg="#172e3f", fg="#e3edf5", relief="flat", font=("Consolas", 10))
        preview_scroll = ttk.Scrollbar(output, command=self.preview.yview)
        preview_scroll.pack(side="right", fill="y")
        self.preview.config(yscrollcommand=preview_scroll.set)
        self.preview.pack(fill="both", expand=True)
        self.button(telemetry_tab, "save", "Save recent commands + rover state (up to ~60 seconds)", self.save).grid(row=2, column=0, sticky="w", pady=8)
        self.build_connections(hardware_tab)
        ttk.Label(camera_tab, text="Optional camera input. Snapshot polling is for bench checks; low-latency driving video comes later.", wraplength=950).pack(anchor="w", pady=6)
        views = ttk.Frame(camera_tab)
        views.pack(fill="both", expand=True)
        for col, title in enumerate(("FRONT / DRIVING CAMERA", "ARM / GRIPPER CAMERA")):
            views.columnconfigure(col, weight=1, uniform="camera")
            view = CameraPanel(views, title)
            view.grid(row=0, column=col, sticky="nsew", padx=4)
            self.cameras.append(view)
        views.rowconfigure(0, weight=1)
        help_text = tk.Text(help_tab, wrap="word", bg="#172e3f", fg="#e3edf5", font=("Segoe UI", 11), padx=12, pady=12)
        scrollbar = ttk.Scrollbar(help_tab, command=help_text.yview)
        scrollbar.pack(side="right", fill="y")
        help_text.config(yscrollcommand=scrollbar.set)
        help_text.pack(fill="both", expand=True)
        help_text.insert("1.0", (Path(__file__).parent / "operator_guide.txt").read_text(encoding="utf-8"))
        help_text.config(state="disabled")
        root.bind_all("<ButtonRelease-1>", lambda e: self.release_mouse())
        # Intercept before widget class bindings: Space must not also click a
        # focused Enable/STOP button or move a slider while used as the deadman.
        root.bind_class("C2DriveKeys", "<KeyPress-space>", self.space_down)
        root.bind_class("C2DriveKeys", "<KeyRelease-space>", self.space_up)
        def add_drive_keys(widget):
            widget.bindtags(("C2DriveKeys",) + widget.bindtags())
            for child in widget.winfo_children():
                add_drive_keys(child)
        add_drive_keys(root)
        root.bind("<Escape>", lambda e: self.stop())
        # Tcl-created dropdowns use their own bindtags, outside the root binding.
        root.bind_class("ComboboxListbox", "<Escape>", lambda e: self.stop(), add="+")
        root.bind("<FocusOut>", self.focus_out)
        root.protocol("WM_DELETE_WINDOW", self.close)
        if args.xbox:
            self.connect_xbox()
        self.loop()

    def connect_xbox(self):
        self.disarm()
        if self.xbox is not None:
            self.xbox.close()
        self.xbox = None
        self.controller_values = None
        self.require_xbox = True
        self.buttons["input_mode"].config(state="normal")
        for slider in self.sliders:
            slider.config(state="disabled")
        try:
            from mini_rover_c2.legacy.xbox import Xbox
            self.xbox = Xbox(*self.controller_args)
            self.xbox.stick = self.stick.get()
            self.hint.config(text="Xbox: move the selected stick to check the yellow arrow. Center, release LB, enable, then hold LB to drive.")
        except Exception as exc:
            self.xbox = None
            self.hint.config(text=f"Xbox unavailable: {exc}. Install pygame in this Python environment, then Reconnect Xbox.")

    def reconnect_xbox(self):
        if self.source.get() == "Xbox controller":
            self.connect_xbox()

    def stick_changed(self, event=None):
        self.disarm()
        if self.xbox is not None:
            self.xbox.stick = self.stick.get()
        self.controller_values = None

    def poll_controller(self):
        self.controller_values = None
        if not self.require_xbox:
            self.input_status.config(text="SLIDERS • Set sliders, then hold Space to drive.\nRC demo does not read a physical receiver.")
            return
        try:
            if self.xbox is None:
                raise RuntimeError("Controller library unavailable; choose sliders or retry Xbox")
            self.controller_values = self.xbox.read()
            throttle, steering, held = self.controller_values
            self.throttle.set(throttle)
            self.steering.set(steering)
            axes = " ".join(f"{i}:{v:+.2f}" for i, v in enumerate(self.xbox.axes))
            hold_name = "LB" if self.controller_args[0] is None else f"Button {self.controller_args[2]}"
            self.input_status.config(text=f"CONNECTED: {self.xbox.name}\n{self.stick.get()} input T {throttle:+.2f} / S {steering:+.2f} • {hold_name} {'ON' if held else 'OFF'}\nAxes {axes}")
        except Exception as exc:
            self.input_status.config(text=f"XBOX UNAVAILABLE: {exc}")
            self.throttle.set(0)
            self.steering.set(0)
            self.release()
            self.control.disarm("Xbox input unavailable")

    def button(self, parent, key, label, command):
        widget = ttk.Button(parent, text=label, command=command)
        self.buttons[key] = widget
        return widget

    def build_connections(self, parent):
        link = ttk.LabelFrame(parent, text="MATTHEW'S CHASSIS / Uno R4 WiFi • guarded firmware required", padding=8)
        link.pack(fill="x", pady=(0, 6))
        row = ttk.Frame(link)
        row.pack(fill="x")
        self.rover_host, self.rover_port = tk.StringVar(), tk.StringVar(value="4210")
        ttk.Label(row, text="Rover IPv4:").pack(side="left")
        ttk.Entry(row, textvariable=self.rover_host, width=18).pack(side="left", padx=4)
        ttk.Label(row, text="UDP port:").pack(side="left")
        ttk.Entry(row, textvariable=self.rover_port, width=7).pack(side="left", padx=4)
        for key, label, callback in (("rover_connect", "Connect rover", self.connect_rover),
                                      ("rover_disconnect", "Disconnect / simulation", self.disconnect_rover),
                                      ("rover_mock", "Start local test", self.start_mock)):
            self.button(row, key, label, callback).pack(side="left", padx=3)
        self.link_status = ttk.Label(link, text=self.rover.description, wraplength=1080)
        self.link_status.pack(anchor="w", pady=6)
        ttk.Label(link, text="Connect only verifies communication. Return to Drive, neutral sticks → Enable → hold LB.\nLocal test opens a loopback mock with no motors. Stock v6 cannot pass the guarded handshake.", wraplength=1080).pack(anchor="w")
        self.gps_live = GPSPanel(parent, self.draw_track)
        self.gps_live.pack(fill="both", expand=True)

    def connect_rover(self):
        self.disconnect_rover()
        try:
            self.rover.connect(self.rover_host.get(), self.rover_port.get())
            self.hint.config(text="Waiting for guarded rover reply. No enable or motion is sent by Connect.")
        except (ValueError, OSError) as exc:
            self.rover.close()
            self.hint.config(text=f"Cannot connect rover: {exc}")

    def disconnect_rover(self):
        self.disarm()
        self.rover.close()
        self.hint.config(text="Chassis disconnected. Inputs now drive the model only; arm output remains a preview.")
        if self.mock_rover:
            self.mock_rover.close()
            self.mock_rover = None

    def start_mock(self):
        self.disconnect_rover()
        try:
            from mini_rover_c2.legacy.mock_rover import MockRover
            self.mock_rover = MockRover()
            self.rover_host.set("127.0.0.1")
            self.rover_port.set(str(self.mock_rover.port))
            self.rover.connect("127.0.0.1", self.mock_rover.port)
            self.hint.config(text="LOCAL TEST • no motors. Wait for acknowledgment, then return to Drive and enable.")
        except OSError as exc:
            self.disconnect_rover()
            self.hint.config(text=f"Local UDP test unavailable: {exc}")

    def tab_changed(self, event=None):
        selected = self.notebook.select()
        if selected != self.active_tab:
            self.release()
            self.control.disarm("Tab changed; enable again in Drive & Arm")
            self.active_tab = selected

    def disarm(self):
        self.release()
        self.control.disarm()
        self.rover.disable()

    def reset_stop(self):
        self.release()
        if self.rover.connected:
            try:
                self.rover.reset_stop()
            except (OSError, ValueError) as exc:
                self.hint.config(text=str(exc))
                return
        self.control.reset_stop()

    def center(self):
        self.throttle.set(0)
        self.steering.set(0)
        self.release()

    def release(self):
        self.keyboard_hold = False
        self.release_mouse()

    def release_mouse(self):
        self.drive_hold = False
        self.jog_hold = 0.0
        self.control.jog = 0.0

    def space_down(self, event=None):
        if self.notebook.index(self.notebook.select()) == 0:
            self.keyboard_hold = True
            return "break"

    def space_up(self, event=None):
        self.keyboard_hold = False
        if self.notebook.index(self.notebook.select()) == 0:
            return "break"

    def source_changed(self, event=None):
        self.center()
        self.control.select("RC" if self.source.get() == "RC (demo)" else "Xbox")
        if self.source.get() == "Xbox controller":
            self.connect_xbox()
        else:
            if self.xbox is not None:
                self.xbox.close()
            self.xbox = None
            self.controller_values = None
            self.require_xbox = False
            self.buttons["input_mode"].config(state="disabled")
            for slider in self.sliders:
                slider.config(state="normal")
            self.hint.config(text="Demo: center sliders, enable, then hold Space while adjusting throttle and steering.")

    def stop(self):
        self.release()
        self.control.stop()
        self.rover.disable(latch=True)

    def focus_out(self, event):
        if not self._closing and self._focus_after is None:
            self._focus_after = self.root.after_idle(self.check_focus)

    def check_focus(self):
        self._focus_after = None
        if self._closing:
            return
        # A ttk dropdown's Tcl-created .popdown window has no Python Widget
        # object. focus_displayof() tries to convert it and raises KeyError.
        # Only the presence of focus is needed here; keep the raw Tcl path.
        focused = self.root.tk.call("focus", "-displayof", self.root._w)
        if not focused:
            self.release()
            self.control.disarm("Window lost focus")

    def enable(self):
        if self.notebook.index(self.notebook.select()) != 0:
            self.disarm()
            self.hint.config(text="Open Drive & Arm before enabling controls.")
            return
        if self.rover.connected and self.control.source == "RC":
            self.hint.config(text="RC is an input demo only. Choose Xbox or sliders for the bench chassis.")
            return
        allowed, reason = self.control.enable(time.monotonic())
        if allowed and self.rover.connected:
            try:
                self.rover.request_enable(time.monotonic())
                reason = "Enable requested; waiting for rover confirmation. Hold LB only after confirmation."
            except (OSError, ValueError) as exc:
                self.disarm()
                reason = str(exc)
        self.events.append(reason)
        self.hint.config(text=reason)

    def toggle_arm(self):
        if self.rover.connected:
            self.disarm()
            self.hint.config(text="Arm is preview-only. Disconnect chassis to explore arm jogging; no STM32 firmware link exists yet.")
            return
        self.release()
        self.control.arm_enabled = self.control.armed and not self.control.arm_enabled
        self.hint.config(text=("Arm mode: hold a joint jog button; drive is inhibited." if self.control.arm_enabled else "Drive mode: hold the drive button to move." if self.control.armed else "Enable controls before selecting arm mode."))

    def save(self):
        destination = Path(__file__).parent / "logs"
        try:
            destination.mkdir(exist_ok=True)
            path = destination / f"commands-{time.time_ns()}.jsonl"
            path.write_text("".join(json.dumps(row) + "\n" for row in self.records), encoding="utf-8")
            self.hint.config(text=f"Saved commands and received rover state: {path.name}")
        except OSError as exc:
            self.hint.config(text=f"Could not save log ({type(exc).__name__}); check folder permissions.")

    def draw_motion(self, result):
        self.vector.delete("all")
        w, h = max(150, self.vector.winfo_width()), max(60, self.vector.winfo_height())
        cx, cy, radius = w/2, h/2+8, max(1, min(w/2-30, h/2-30))
        self.vector.create_text(10, 12, anchor="w", text="INPUT PREVIEW • works while disarmed", fill="#e8b963")
        self.vector.create_line(cx-radius, cy, cx+radius, cy, fill="#688297")
        self.vector.create_line(cx, cy-radius, cx, cy+radius, fill="#688297")
        self.vector.create_oval(cx-radius, cy-radius, cx+radius, cy+radius, outline="#688297")
        self.vector.create_line(cx, cy, cx+self.steering.get()*radius, cy-self.throttle.get()*radius,
                                fill="#e8b963", width=4, arrow="last", tags="input_arrow")
        self.vector.create_oval(cx-3, cy-3, cx+3, cy+3, fill="#e8b963")
        self.draw_track(self.canvas, list(self.path), [], self.heading)
        if not result["enabled"]:
            why = result["reason"]
        elif self.control.arm_enabled:
            why = "Arm mode; chassis held stationary"
        elif not self.control.samples[self.control.source].deadman:
            why = "Hold LB, Space or mouse to permit drive"
        else:
            why = "Drive permitted; stick sets speed"
        self.motion_status.config(text=f"MODEL E {self.x:+.2f} m / N {self.y:+.2f} m • {why}")

    @staticmethod
    def draw_track(canvas, reference, gps, heading=None):
        canvas.delete("all")
        w, h = max(150, canvas.winfo_width()), max(60, canvas.winfo_height())
        all_points = reference + gps + [(-2, -2), (2, 2)]
        min_e, max_e = min(p[0] for p in all_points), max(p[0] for p in all_points)
        min_n, max_n = min(p[1] for p in all_points), max(p[1] for p in all_points)
        scale = min((w-50)/(max_e-min_e), (h-50)/(max_n-min_n))
        def project(p):
            return w/2+(p[0]-(min_e+max_e)/2)*scale, h/2-(p[1]-(min_n+max_n)/2)*scale
        ox, oy = project((0, 0))
        canvas.create_line(ox, 22, ox, h-12, fill="#294557")
        canvas.create_line(12, oy, w-12, oy, fill="#294557")
        canvas.create_text(8, 12, anchor="w", text="N ↑   E →  |  Cyan model/reference, orange GPS", fill="#c1d7e6", tags="legend")
        if len(reference) > 1:
            canvas.create_line(*[v for p in reference for v in project(p)], fill="#6edac6", width=2, tags="track")
        for ref, fix in zip(reference, gps):
            canvas.create_line(*project(ref), *project(fix), fill="#6c6755")
            gx, gy = project(fix)
            canvas.create_oval(gx-3, gy-3, gx+3, gy+3, outline="#e8b963")
        if reference:
            x, y = project(reference[-1])
            canvas.create_oval(x-6, y-6, x+6, y+6, fill="#eef6ff", tags="rover_body")
            if heading is not None:
                canvas.create_line(x, y, x+22*math.cos(heading), y-22*math.sin(heading), fill="#6edac6", width=3, arrow="last")
        canvas.create_line(w-90, h-12, w-90+scale, h-12, fill="#c1d7e6", width=2)
        canvas.create_text(w-90, h-24, anchor="w", text="1 m", fill="#c1d7e6")

    def clear_gps(self):
        self.gps_demo.set(False)
        self.gps_loaded = False
        self.gps_pairs.clear()
        self.gps_last = -math.inf
        self.gps_status.config(text="No GPS comparison data. Physical receiver: NOT CONNECTED.")
        self.draw_track(self.gps_canvas, [], [])

    def change_gps(self):
        enabled = self.gps_demo.get()
        self.clear_gps()
        self.gps_demo.set(enabled)

    def load_gps(self):
        self.disarm()
        path = filedialog.askopenfilename(parent=self.root, title="Load time-aligned GPS/reference JSONL", filetypes=[("JSON Lines", "*.jsonl")])
        if not path:
            return
        try:
            pairs, skipped = compare_log(path)
            self.clear_gps()
            self.gps_loaded = True
            self.gps_pairs.extend(pairs)
            self.render_gps(f"OFFLINE • {Path(path).name} • {skipped} unmatched valid fixes • latest {len(self.gps_pairs)} pairs")
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            self.hint.config(text=f"GPS comparison could not load: {exc}")

    def update_gps(self, now):
        if self.gps_demo.get() and now-self.gps_last >= 1:
            self.gps_last = now
            # Deliberately biased, noisy synthetic readings, not real GPS data.
            ge = self.x+.7+self.gps_random.gauss(0, .3)
            gn = self.y-.4+self.gps_random.gauss(0, .3)
            self.gps_pairs.append((now, self.x, self.y, ge, gn))
        if self.gps_demo.get():
            self.render_gps("SYNTHETIC GPS • bias E +0.7 m / N -0.4 m, noise σ 0.3 m • no receiver")
        elif self.gps_loaded:
            self.draw_track(self.gps_canvas, [(p[1], p[2]) for p in self.gps_pairs], [(p[3], p[4]) for p in self.gps_pairs])

    def render_gps(self, label):
        values = metrics(self.gps_pairs)
        if values['count']:
            text = (f"{label}\nPairs {values['count']} • latest difference {values['latest_m']:.2f} m • "
                    f"horizontal RMSE {values['rmse_m']:.2f} m • mean ΔE {values['bias_e_m']:+.2f} / ΔN {values['bias_n_m']:+.2f} m")
        else:
            text = f"{label}\nNo time-matched valid pairs; check shared timestamps, origin and log format."
        self.gps_status.config(text=text)
        self.draw_track(self.gps_canvas, [(p[1], p[2]) for p in self.gps_pairs], [(p[3], p[4]) for p in self.gps_pairs])

    def loop(self):
        self._loop_after = None
        if self._closing:
            return
        now = time.monotonic()
        self.poll_controller()
        self.control.tick(now)
        if not self.drop.get():
            values = (self.throttle.get(), self.steering.get(), self.drive_hold or self.keyboard_hold)
            try:
                if self.require_xbox:
                    if self.controller_values is None:
                        raise RuntimeError("Xbox input unavailable")
                    throttle, steering, held = self.controller_values
                    values = (throttle, steering, held or self.drive_hold or self.keyboard_hold)
                self.control.receive(self.control.source, *values, now)
            except Exception as exc:
                self.control.disarm(str(exc))
            if self.control.armed:
                self.control.receive_jog(self.joint.get(), self.jog_hold, now)
        result = self.control.output(now)
        self.rover.tick(result, now)
        if self.rover.fault and self.rover.connected:
            self.release()
            self.control.disarm(self.rover.fault)
            result = self.control.output(now)
        connection = self.rover.summary(now)
        self.link_status.config(text=connection)
        self.telemetry_summary.config(text=connection+"\nBattery: UNKNOWN • STM32/joint feedback: not connected. Live GPS is in Connections & Live GPS.")
        mode = "LOCAL UDP TEST • no motors" if self.mock_rover else "LIVE CHASSIS UDP" if self.rover.connected else "SIMULATION • chassis disconnected"
        self.connection_banner.config(text=mode+" • arm preview • path remains a model, not measured rover position")
        gps_lines, self.rover.gps_lines = self.rover.gps_lines, []
        self.gps_live.tick(now, gps_lines, self.rover.ready(now))
        self.status.config(text=f"{'STOP LATCHED' if result['stop_latched'] else 'ENABLED' if result['enabled'] else 'DISARMED'} | {self.source.get()} | {result['reason']}")
        self.drive_status.config(text=f"Left {result['left']:+.0%} / Right {result['right']:+.0%}")
        self.arm_status.config(text=f"{'Arm mode (drive inhibited)' if self.control.arm_enabled else 'Drive mode'} | Jog {result['arm_preview']['rate']:+.0%}")
        dt = min(now - self.last, 0.1)
        self.last = now
        self.x, self.y, self.heading = integrate_pose(self.x, self.y, self.heading, result['left'], result['right'], dt)
        self.path.append((self.x, self.y))
        self.draw_motion(result)
        self.update_gps(now)
        if result['reason'] != self.previous_reason:
            self.events.append(result['reason'])
            self.previous_reason = result['reason']
            if not result['enabled'] and str(self.hint.cget("text")).startswith("Enabled;"):
                self.hint.config(text=result['reason'])
        self.sequence += 1
        request = {"session_id": self.session_id, "command_id": self.sequence,
                   "enable_requested": result['enabled'], "stop_requested": result['stop_latched'],
                   "forward_mps": round((result['left']+result['right'])/2, 4),
                   "turn_radps": round((result['right']-result['left'])/.5, 4)}
        result.update(sequence=self.sequence, monotonic_seconds=now, simulation=not self.rover.connected or self.mock_rover is not None,
                      motor_transport=mode, rover_state=dict(self.rover.state) if self.rover.state else None,
                      input_mode=self.source.get(), input_throttle=self.throttle.get(), input_steering=self.steering.get(),
                      pose_model={"east_m": self.x, "north_m": self.y, "yaw_rad": self.heading}, onboard_request=request)
        self.records.append(result)
        self.preview.config(state="normal")
        self.preview.delete("1.0", "end")
        self.preview.insert("end", connection + "\nChassis drive values: " + repr(result['legacy_drive_preview']) +
                            " • framed with session / sequence / watchdog protocol when connected\nArm request (NOT SENT): " + json.dumps(result['arm_preview']) +
                            "\nFuture OC velocity request (NOT SENT): " + json.dumps(request))
        self.preview.config(state="disabled")
        for camera in self.cameras:
            camera.tick(now)
        self._loop_after = self.root.after(50, self.loop)

    def close(self):
        if self._closing:
            return
        self._closing = True
        for callback in (self._focus_after, self._loop_after):
            if callback is not None:
                self.root.after_cancel(callback)
        self._focus_after = self._loop_after = None
        self.control.stop("Application closed")
        self.rover.close()
        if self.mock_rover:
            self.mock_rover.close()
        self.gps_live.disconnect()
        for camera in self.cameras:
            camera.disconnect()
        if self.xbox:
            self.xbox.close()
        self.root.destroy()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--xbox", action="store_true", help="Read a gamepad; motor connection still requires an explicit Connect action")
    parser.add_argument("--throttle-axis", type=int, help="Optional raw mapping; supply all three indices")
    parser.add_argument("--steering-axis", type=int)
    parser.add_argument("--hold-button", type=int)
    args = parser.parse_args()
    indices = (args.throttle_axis, args.steering_axis, args.hold_button)
    if any(i is not None for i in indices) and (any(i is None for i in indices) or any(i < 0 for i in indices if i is not None)):
        parser.error("Raw mapping requires three nonnegative indices")
    Station(tk.Tk(), args).root.mainloop()
