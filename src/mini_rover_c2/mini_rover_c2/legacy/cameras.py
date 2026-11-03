"""Optional HTTP still-image input; workers never touch Tk or motor controls."""
from io import BytesIO
from queue import Queue, Empty
import threading
import time
from urllib.parse import urlsplit
from urllib.request import urlopen
import tkinter as tk
from tkinter import ttk

MAX_BYTES = 4 * 1024 * 1024


def fetch_snapshot(url):
    """Read one bounded JPEG/PNG response, not a video/MJPEG stream."""
    if urlsplit(url).scheme not in ("http", "https"):
        raise ValueError("Use an http:// or https:// JPEG/PNG snapshot URL")
    with urlopen(url, timeout=2) as response:
        if urlsplit(response.geturl()).scheme not in ("http", "https"):
            raise ValueError("Unsupported redirect")
        content_type = response.headers.get_content_type()
        if content_type not in ("image/jpeg", "image/png"):
            raise ValueError("Endpoint must return image/jpeg or image/png, not a video stream or web page")
        data = response.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("Camera image exceeds 4 MB")
    return data


class SnapshotReader:
    """One daemon worker per connection; one pending frame, cancellation by token."""
    def __init__(self, fetch=fetch_snapshot):
        self.fetch = fetch
        self.stop_event = threading.Event()
        self.results = Queue(maxsize=1)

    def start(self, url):
        def work():
            while not self.stop_event.is_set():
                started = time.monotonic()
                try:
                    data = self.fetch(url)
                    item = (data, time.monotonic(), time.monotonic() - started, None)
                except Exception as exc:
                    # Do not display URLs, which could contain camera credentials.
                    item = (None, time.monotonic(), 0, f"Snapshot failed ({type(exc).__name__}); check address and camera")
                if self.stop_event.is_set():
                    return
                try:
                    self.results.get_nowait()
                except Empty:
                    pass
                self.results.put_nowait(item)
                if self.stop_event.wait(.5):
                    return
        threading.Thread(target=work, daemon=True).start()

    def stop(self):
        self.stop_event.set()


class CameraPanel(ttk.LabelFrame):
    def __init__(self, parent, title):
        super().__init__(parent, text=title, padding=12)
        self.reader = None
        self.mode = "disconnected"
        self.last_frame = None
        self.photo = None
        self.last_poll = 0
        self.url = tk.StringVar()
        self.entry = ttk.Entry(self, textvariable=self.url)
        self.entry.pack(fill="x")
        ttk.Label(self, text="HTTP JPEG/PNG snapshot URL (not RTSP/MJPEG)").pack(anchor="w", pady=4)
        buttons = ttk.Frame(self)
        buttons.pack(fill="x", pady=6)
        self.connect_button = ttk.Button(buttons, text="Connect snapshot", command=self.connect)
        self.connect_button.pack(side="left")
        self.demo_button = ttk.Button(buttons, text="Demo view", command=self.demo)
        self.demo_button.pack(side="left", padx=4)
        self.disconnect_button = ttk.Button(buttons, text="Disconnect", command=self.disconnect)
        self.disconnect_button.pack(side="left")
        self.canvas = tk.Canvas(self, height=220, bg="#172e3f", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.status = ttk.Label(self, text="DISCONNECTED • no camera image", wraplength=420)
        self.status.pack(anchor="w", pady=6)
        self.message("NO CAMERA CONNECTED")

    def message(self, text):
        self.canvas.delete("all")
        self.canvas.create_text(18, 30, text=text, fill="#e3edf5", anchor="nw", width=380, font=("Segoe UI", 13, "bold"))

    def disconnect(self):
        if self.reader is not None:
            self.reader.stop()
        self.reader = None
        self.mode = "disconnected"
        self.last_frame = None
        self.photo = None
        self.message("NO CAMERA CONNECTED")
        self.status.config(text="DISCONNECTED • no camera image")

    def demo(self):
        self.disconnect()
        self.mode = "demo"
        self.tick(time.monotonic())

    def connect(self):
        self.disconnect()
        url = self.url.get().strip()
        try:
            parsed = urlsplit(url)
        except ValueError:
            self.status.config(text="Invalid camera URL. Check the address.")
            return
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            self.status.config(text="Enter a complete HTTP/HTTPS snapshot URL. No connection attempted.")
            return
        try:
            from PIL import Image, ImageTk
        except ImportError:
            self.status.config(text="Camera preview needs Pillow. Demo view still works.")
            return
        self.mode = "snapshot"
        self.reader = SnapshotReader()
        self.reader.start(url)
        self.status.config(text="CONNECTING • waiting for first image")

    def tick(self, now):
        if self.mode == "demo":
            self.canvas.delete("all")
            width, height = max(300, self.canvas.winfo_width()), max(180, self.canvas.winfo_height())
            self.canvas.create_rectangle(0, height/2, width, height, fill="#294638", outline="")
            for fraction in (.1, .3, .5, .7, .9):
                self.canvas.create_line(width/2, height/2, width*fraction, height, fill="#6edac6")
            self.canvas.create_text(16, 24, text="DEMO — GENERATED TEST VIEW", anchor="w", fill="#e8b963", font=("Segoe UI", 12, "bold"))
            self.canvas.create_text(16, 50, text=f"Animation clock: {now:.1f}", anchor="w", fill="white")
            self.status.config(text="DEMO ONLY • not rover footage • no connection")
        elif self.mode == "snapshot" and self.reader is not None:
            try:
                data, received, elapsed, error = self.reader.results.get_nowait()
            except Empty:
                data = None
                error = None
            if error:
                self.message("CAMERA UNAVAILABLE\n" + error)
                self.status.config(text=error)
                self.last_frame = None
            elif data is not None:
                try:
                    from PIL import Image, ImageTk
                    with Image.open(BytesIO(data)) as source:
                        if source.width * source.height > 16_000_000:
                            raise ValueError("Image dimensions too large")
                        source.thumbnail((max(300, self.canvas.winfo_width()), max(180, self.canvas.winfo_height())))
                        self.photo = ImageTk.PhotoImage(source.copy())
                    self.canvas.delete("all")
                    self.canvas.create_image(0, 0, image=self.photo, anchor="nw")
                    self.last_frame = received
                    self.last_poll = elapsed
                except Exception:
                    self.last_frame = None
                    self.photo = None
                    self.message("IMAGE DECODE FAILED")
                    self.status.config(text="Invalid/unsupported image returned by camera")
            if self.last_frame is not None:
                age = now - self.last_frame
                if age > 3:
                    self.message("STALE IMAGE — DO NOT USE FOR DRIVING")
                self.status.config(text=f"{'STALE' if age > 3 else 'SNAPSHOT RECEIVED'} • received {age:.1f}s ago • request {self.last_poll:.2f}s\nReceipt age is not camera capture latency.")
