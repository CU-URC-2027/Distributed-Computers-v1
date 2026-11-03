"""Hidden-window regression checks. Run: python gui_regression.py"""
import argparse
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch, MagicMock
import time
import tempfile
import json
from pathlib import Path
from io import BytesIO
from queue import Queue

from mini_rover_c2.legacy.app import Station


class FocusRegression(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = Station(self.root, argparse.Namespace(xbox=False))
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(args)
        # Return an actual Tk widget path deterministically, without taking
        # desktop focus from the user's running program.
        self.root.tk.eval('rename focus original_focus')
        self.root.tk.eval('proc focus {args} {return $::test_focus}')
        self.set_focus("")
        self.root.update_idletasks()

    def set_focus(self, path):
        self.root.tk.setvar("test_focus", path)

    def tearDown(self):
        if not self.app._closing:
            self.app.close()
        self.assertFalse(self.errors, "Unexpected Tk callback error")

    def test_dropdown_does_not_raise_or_disarm(self):
        box = ttk.Combobox(self.root, values=("Xbox", "RC"))
        popup = self.root.tk.call("ttk::combobox::PopdownWindow", str(box))
        self.set_focus(str(popup) + ".f.l")
        # Reproduce the exact traceback from the old implementation.
        with self.assertRaisesRegex(KeyError, "popdown"):
            self.root.focus_displayof()
        self.app.control.armed = True
        for _ in range(10):
            self.app.focus_out(None)
        self.root.update_idletasks()
        self.assertTrue(self.app.control.armed)
        self.assertFalse(self.errors)

    def test_external_focus_loss_releases_both_controls(self):
        self.app.control.armed = True
        self.app.drive_hold = True
        self.app.jog_hold = 1.0
        self.set_focus("")
        self.app.check_focus()
        self.assertFalse(self.app.control.armed)
        self.assertFalse(self.app.drive_hold)
        self.assertEqual(self.app.jog_hold, 0)

    def test_normal_internal_focus_keeps_controls_enabled(self):
        self.set_focus(str(self.app.preview))
        self.app.control.armed = True
        self.app.check_focus()
        self.assertTrue(self.app.control.armed)

    def test_close_cancels_pending_callbacks(self):
        self.app.focus_out(None)
        pending = (self.app._focus_after, self.app._loop_after)
        self.app.close()
        scheduled = self.root.tk.call("after", "info")
        for callback in pending:
            self.assertNotIn(callback, scheduled)
        self.app.check_focus()
        self.app.loop()
        self.app.close()
        self.assertFalse(self.errors)

    def step(self):
        if self.app._loop_after is not None:
            self.root.after_cancel(self.app._loop_after)
        self.app.loop()

    def arm(self):
        self.app.notebook.select(0)
        self.app.tab_changed()
        self.set_focus(str(self.app.preview))
        self.app.buttons["center"].invoke()
        self.step()
        self.app.buttons["enable"].invoke()
        self.assertTrue(self.app.control.armed)

    def test_all_drive_buttons_and_hold_events(self):
        self.arm()
        self.app.throttle.set(.5)
        hold = self.app.buttons["drive_hold"]
        hold.event_generate("<ButtonPress-1>")
        self.step()
        self.assertGreater(self.app.records[-1]["left"], 0)
        hold.event_generate("<ButtonRelease-1>")
        self.step()
        self.assertEqual(self.app.records[-1]["left"], 0)
        hold.event_generate("<ButtonPress-1>")
        hold.event_generate("<Leave>")
        self.assertFalse(self.app.drive_hold)
        self.app.buttons["center"].invoke()
        self.assertEqual(self.app.throttle.get(), 0)
        self.app.buttons["disarm"].invoke()
        self.assertFalse(self.app.control.armed)
        self.arm()
        self.app.buttons["stop"].invoke()
        self.app.buttons["enable"].invoke()
        self.assertFalse(self.app.control.armed)
        self.assertTrue(self.app.control.stopped)
        self.app.buttons["reset"].invoke()
        self.assertFalse(self.app.control.stopped)
        self.assertFalse(self.app.control.armed)

    def test_both_jog_buttons_and_joint_dropdown(self):
        self.app.buttons["arm_mode"].invoke()
        self.assertFalse(self.app.control.arm_enabled)
        self.arm()
        self.app.buttons["arm_mode"].invoke()
        self.assertTrue(self.app.control.arm_enabled)
        self.app.joint_menu.set("elbow")
        self.app.joint_menu.event_generate("<<ComboboxSelected>>")
        for key, rate in (("jog_negative", -.2), ("jog_positive", .2)):
            button = self.app.buttons[key]
            button.event_generate("<ButtonPress-1>")
            self.step()
            self.assertEqual(self.app.records[-1]["arm_preview"], {"type": "arm_jog", "joint": "elbow", "rate": rate})
            self.assertEqual(self.app.records[-1]["left"], 0)
            button.event_generate("<ButtonRelease-1>")
            self.step()
            self.assertEqual(self.app.records[-1]["arm_preview"]["rate"], 0)
        self.app.buttons["arm_mode"].invoke()
        self.assertFalse(self.app.control.arm_enabled)

    def test_source_dropdown_and_link_loss_checkbox(self):
        self.arm()
        self.app.source_menu.set("RC (demo)")
        self.app.source_menu.event_generate("<<ComboboxSelected>>")
        self.assertEqual(self.app.control.source, "RC")
        self.assertFalse(self.app.control.armed)
        self.arm()
        self.app.drop_button.invoke()
        self.assertTrue(self.app.drop.get())
        self.app.control.samples["RC"].received = time.monotonic() - 1
        self.step()
        self.assertFalse(self.app.control.armed)
        self.app.drop_button.invoke()
        self.step()
        self.assertFalse(self.app.control.armed)

    def test_save_button_success_and_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch("mini_rover_c2.legacy.app.__file__", str(Path(folder) / "mini_rover_c2.legacy.app.py")):
                self.app.buttons["save"].invoke()
            paths = list((Path(folder) / "logs").glob("*.jsonl"))
            self.assertEqual(len(paths), 1)
            self.assertTrue(json.loads(paths[0].read_text().splitlines()[0])["simulation"])
        with patch("mini_rover_c2.legacy.app.Path.mkdir", side_effect=PermissionError):
            self.app.buttons["save"].invoke()
        self.assertIn("Could not save", self.app.hint.cget("text"))

    def test_tab_switch_disarms_and_all_tabs_exist(self):
        self.assertEqual(len(self.app.notebook.tabs()), 5)
        for index in range(1, 5):
            self.arm()
            self.app.notebook.select(index)
            self.root.update_idletasks()
            self.app.tab_changed()
            self.assertFalse(self.app.control.armed)
            self.app.buttons["enable"].invoke()
            self.assertFalse(self.app.control.armed)
            self.assertIn("Open Drive", self.app.hint.cget("text"))

    def test_every_camera_button_and_error_state(self):
        for camera in self.app.cameras:
            camera.demo_button.invoke()
            self.assertEqual(camera.mode, "demo")
            self.assertIn("DEMO", camera.status.cget("text"))
            camera.disconnect_button.invoke()
            self.assertEqual(camera.mode, "disconnected")
            for url in ("", "rtsp://camera/live", "http://[bad"):
                camera.url.set(url)
                camera.connect_button.invoke()
                self.assertIsNone(camera.reader)
            camera.url.set("http://camera.test/image.jpg")
            with patch("mini_rover_c2.legacy.cameras.SnapshotReader") as reader_class:
                reader = reader_class.return_value
                reader.results = Queue()
                camera.connect_button.invoke()
                reader.start.assert_called_once_with("http://camera.test/image.jpg")
                reader.results.put((None, time.monotonic(), 0, "Camera unavailable"))
                camera.tick(time.monotonic())
                self.assertIn("unavailable", camera.status.cget("text"))
                from PIL import Image
                data = BytesIO()
                Image.new("RGB", (32, 32), "green").save(data, "PNG")
                now = time.monotonic()
                reader.results.put((data.getvalue(), now, .1, None))
                camera.tick(now)
                self.assertIsNotNone(camera.photo)
                self.assertIn("SNAPSHOT RECEIVED", camera.status.cget("text"))
                camera.tick(now + 4)
                self.assertIn("STALE", camera.status.cget("text"))
                reader.results.put((b"not an image", now, 0, None))
                camera.tick(now)
                self.assertIn("Invalid", camera.status.cget("text"))
                camera.disconnect_button.invoke()
                reader.stop.assert_called_once()
                self.assertIsNone(camera.photo)
        self.assertFalse(self.errors)

    def test_xbox_detection_mouse_hold_and_disconnect(self):
        controller = MagicMock()
        controller.name = "Test Xbox"
        controller.axes = (0, 0, 0, 0)
        controller.read.return_value = (0, 0, False)
        with patch("mini_rover_c2.legacy.xbox.Xbox", return_value=controller):
            self.app.source_menu.set("Xbox controller")
            self.app.source_menu.event_generate("<<ComboboxSelected>>")
        self.step()
        self.assertFalse(self.app.control.armed)
        self.assertIn("CONNECTED: Test Xbox", self.app.input_status.cget("text"))
        self.arm()
        controller.read.return_value = (.5, 0, False)
        old_arrow = self.app.vector.coords("input_arrow")
        self.step()
        self.assertEqual(self.app.records[-1]["left"], 0)
        self.assertNotEqual(old_arrow, self.app.vector.coords("input_arrow"))
        self.app.buttons["drive_hold"].event_generate("<ButtonPress-1>")
        self.step()
        self.assertGreater(self.app.records[-1]["left"], 0)
        self.app.buttons["drive_hold"].event_generate("<ButtonRelease-1>")
        self.step()
        self.assertEqual(self.app.records[-1]["left"], 0)
        controller.read.return_value = (.5, 0, True)
        self.step()
        self.assertGreater(self.app.records[-1]["left"], 0)
        controller.read.side_effect = RuntimeError("Xbox disconnected")
        self.step()
        self.assertFalse(self.app.control.armed)
        self.assertIn("UNAVAILABLE", self.app.input_status.cget("text"))
        controller.read.side_effect = None
        controller.read.return_value = (0, 0, False)
        self.step()
        self.assertFalse(self.app.control.armed)
        self.app.source_menu.set("Sliders (demo)")
        self.app.source_menu.event_generate("<<ComboboxSelected>>")
        self.assertFalse(self.app.require_xbox)
        controller.close.assert_called_once()

    def test_missing_xbox_library_reports_before_enable(self):
        with patch("mini_rover_c2.legacy.xbox.Xbox", side_effect=ImportError("No pygame")):
            self.app.source_menu.set("Xbox controller")
            self.app.source_menu.event_generate("<<ComboboxSelected>>")
        self.step()
        self.assertIn("UNAVAILABLE", self.app.input_status.cget("text"))
        self.assertFalse(self.app.control.armed)

    def test_keyboard_hold_and_visual_rover_moves(self):
        self.arm()
        start = (self.app.x, self.app.y)
        self.app.throttle.set(.8)
        self.app.space_down()
        # Mouse release while adjusting a slider must NOT release Space.
        self.app.release_mouse()
        self.assertTrue(self.app.keyboard_hold)
        self.app.last -= .1
        self.step()
        self.assertNotEqual(start, (self.app.x, self.app.y))
        self.assertEqual(self.app.canvas.master, self.app.vector.master)
        self.assertTrue(self.app.canvas.find_withtag("rover_body"))
        self.app.release()
        self.step()
        self.assertEqual(self.app.records[-1]["left"], 0)
        self.assertEqual(self.app.records[-1]["onboard_request"]["forward_mps"], 0)

    def test_space_intercepts_buttons_and_releases(self):
        for button in self.app.buttons.values():
            self.assertEqual(button.bindtags()[0], "C2DriveKeys")
        self.assertEqual(self.app.space_down(), "break")
        self.assertTrue(self.app.keyboard_hold)
        self.assertEqual(self.app.space_up(), "break")
        self.assertFalse(self.app.keyboard_hold)
        self.app.notebook.select(1)
        self.assertIsNone(self.app.space_down())
        self.assertFalse(self.app.keyboard_hold)

    def test_xbox_stick_selector_reconnect_and_no_auto_arm(self):
        controller = MagicMock(name="Gamepad")
        controller.name, controller.axes = "Test Xbox", (0, 0, 0, 0)
        controller.read.return_value = (0, 0, False)
        with patch("mini_rover_c2.legacy.xbox.Xbox", return_value=controller):
            self.app.source_menu.set("Xbox controller")
            self.app.source_changed()
            self.step()
            self.arm()
            self.app.stick_menu.set("Right")
            self.app.stick_menu.event_generate("<<ComboboxSelected>>")
            self.assertFalse(self.app.control.armed)
            self.assertEqual(controller.stick, "Right")
            self.app.buttons["input_mode"].invoke()
            controller.close.assert_called_once()
            self.assertFalse(self.app.control.armed)

    def test_gps_demo_import_cancel_errors_and_clear(self):
        self.app.gps_demo_button.invoke()
        self.step()
        self.assertIn("SYNTHETIC GPS", self.app.gps_status.cget("text"))
        self.assertEqual(len(self.app.gps_pairs), 1)
        self.assertTrue(self.app.gps_canvas.find_all())
        self.app.buttons["clear_gps"].invoke()
        self.assertFalse(self.app.gps_demo.get())
        self.assertFalse(self.app.gps_pairs)
        with patch("mini_rover_c2.legacy.app.filedialog.askopenfilename", return_value=""):
            self.app.buttons["load_gps"].invoke()
        with patch("mini_rover_c2.legacy.app.filedialog.askopenfilename", return_value="missing.jsonl"):
            self.app.buttons["load_gps"].invoke()
        self.assertIn("could not load", self.app.hint.cget("text"))
        path = str(Path(__file__).parent / "examples" / "gps_comparison.jsonl")
        with patch("mini_rover_c2.legacy.app.filedialog.askopenfilename", return_value=path):
            self.app.buttons["load_gps"].invoke()
        self.assertTrue(self.app.gps_loaded)
        self.assertIn("OFFLINE", self.app.gps_status.cget("text"))
        self.app.gps_demo_button.invoke()
        self.assertFalse(self.app.gps_loaded)

    def test_onboard_turn_convention_and_request_identity(self):
        self.arm()
        self.app.steering.set(.5)
        self.app.drive_hold = True
        self.step()
        request = self.app.records[-1]["onboard_request"]
        self.assertLess(request["turn_radps"], 0)  # UI right -> ROS clockwise
        self.assertTrue(request["enable_requested"])
        self.step()
        newer = self.app.records[-1]["onboard_request"]
        self.assertGreater(newer["command_id"], request["command_id"])
        self.assertEqual(newer["session_id"], request["session_id"])

    def test_rover_connect_mock_drive_and_disconnect_buttons(self):
        self.assertIsNone(self.app.rover.sock)
        self.app.rover_host.set("invalid")
        self.app.buttons["rover_connect"].invoke()
        self.assertIn("Cannot connect", self.app.hint.cget("text"))
        self.app.buttons["rover_mock"].invoke()
        def pump():
            for _ in range(15):
                self.step()
                time.sleep(.015)
        pump()
        self.assertTrue(self.app.rover.ready(time.monotonic()))
        self.arm()
        self.app.throttle.set(.6)
        self.app.drive_hold = True
        pump()
        self.assertGreater(self.app.mock_rover.throttle, 0)
        self.app.buttons["arm_mode"].invoke()
        pump()
        self.assertFalse(self.app.mock_rover.enabled)
        self.app.buttons["stop"].invoke()
        pump()
        self.assertTrue(self.app.mock_rover.stopped)
        self.app.buttons["reset"].invoke()
        pump()
        self.assertFalse(self.app.mock_rover.stopped)
        self.app.buttons["rover_disconnect"].invoke()
        self.assertIsNone(self.app.rover.sock)
        self.assertIsNone(self.app.mock_rover)

    def test_live_gps_buttons_serial_input_and_export(self):
        from mini_rover_c2.legacy.test_gps_input import gga
        gps = self.app.gps_live
        self.assertIsNone(gps.reader)
        gps.buttons["ports"].invoke()
        gps.port.set("")
        gps.buttons["connect"].invoke()
        self.assertIn("Select", gps.note.cget("text"))
        gps.buttons["save"].invoke()
        self.assertIn("No GPS", gps.note.cget("text"))
        gps.port.set("COM_TEST")
        with patch("mini_rover_c2.legacy.gps_panel.SerialReader") as reader_class:
            reader = reader_class.return_value
            reader.queue, reader.dropped = Queue(), 0
            gps.buttons["connect"].invoke()
            now = time.monotonic()
            reader.queue.put(("connected", "COM_TEST", now))
            reader.queue.put(("line", gga(), now))
            gps.tick(now)
            self.assertIn("VALID FIX", gps.status.cget("text"))
            self.assertTrue(gps.canvas.find_all())
            with tempfile.TemporaryDirectory() as folder:
                with patch("mini_rover_c2.legacy.gps_panel.__file__", str(Path(folder)/"mini_rover_c2.legacy.gps_panel.py")):
                    gps.buttons["save"].invoke()
                self.assertEqual(len(list((Path(folder)/"logs").glob("gps-*.jsonl"))), 1)
            gps.tick(now+3)
            self.assertIn("NO FRESH FIX", gps.status.cget("text"))
            gps.buttons["disconnect"].invoke()
            reader.close.assert_called_once()
        gps.buttons["rover"].invoke()
        gps.tick(time.monotonic(), [gga()], rover_ready=True)
        self.assertTrue(gps.data.valid)
        gps.tick(time.monotonic(), rover_ready=False)
        self.assertFalse(gps.data.valid)
        gps.buttons["reset"].invoke()
        self.assertFalse(gps.records)


if __name__ == "__main__":
    unittest.main(verbosity=2)
