import json
import time
import unittest
from mini_rover_c2.legacy.mock_rover import MockRover
from mini_rover_c2.legacy.rover_link import RoverLink, packet


def output(enabled=False, t=0, s=0, stop=False):
    return dict(enabled=enabled, stop_latched=stop, legacy_drive_preview=f"{t},{s}\n")


class RoverTests(unittest.TestCase):
    def setUp(self):
        self.mock = MockRover()
        self.link = RoverLink()
        self.link.connect("127.0.0.1", self.mock.port)
        self.pump(.12)
        self.assertTrue(self.link.ready(time.monotonic()))

    def tearDown(self):
        self.link.close()
        self.mock.close()

    def pump(self, seconds, result=None):
        end = time.monotonic()+seconds
        while time.monotonic() < end:
            self.link.tick(result or output())
            time.sleep(.01)

    def drive(self):
        self.link.request_enable(time.monotonic())
        self.pump(.15, output(True, 30, 10))
        self.assertIsNone(self.link.fault)
        self.assertTrue(self.link.state["enabled"])
        self.assertEqual(self.link.state["throttle"], 30)

    def test_handshake_drive_release_and_stop_reset(self):
        self.assertFalse(self.mock.enabled)
        self.assertFalse(any(op == "ARM" for op, _, _ in self.mock.received))
        self.drive()
        self.pump(.1, output(True))
        self.assertEqual(self.mock.throttle, 0)
        self.link.disable(latch=True)
        self.pump(.1, output(stop=True))
        self.assertTrue(self.mock.stopped)
        self.assertFalse(self.mock.enabled)
        with self.assertRaises(ValueError):
            self.link.request_enable(time.monotonic())
        self.link.reset_stop()
        self.pump(.1)
        self.assertFalse(self.mock.stopped)
        self.assertFalse(self.mock.enabled)
        self.drive()
        self.link.disable()
        self.pump(.1)
        self.assertFalse(self.mock.enabled)

    def test_pc_freeze_stops_and_late_drive_does_not_restart(self):
        self.drive()
        epoch = self.link.state["epoch"]
        time.sleep(.32)  # endpoint runs independently of the frozen client
        self.assertFalse(self.mock.enabled)
        self.assertEqual(self.mock.reason, "COMMAND_TIMEOUT")
        self.assertGreater(self.mock.epoch, epoch)
        self.pump(.15, output(True, 30))
        self.assertIsNotNone(self.link.fault)
        self.assertFalse(self.mock.enabled)

    def test_ack_loss_latches_fault_and_recovery_never_rearms(self):
        self.drive()
        self.mock.silent = True
        self.pump(.55, output(True, 30))
        self.assertIsNotNone(self.link.fault)
        self.assertFalse(self.mock.enabled)
        self.mock.silent = False
        self.pump(.15, output(True, 30))
        self.assertFalse(self.mock.enabled)
        self.assertFalse(self.link.ready(time.monotonic()))

    def test_close_stops_receiver(self):
        self.drive()
        self.link.close()
        time.sleep(.08)
        self.assertFalse(self.mock.enabled)
        self.assertTrue(self.mock.stopped)

    def test_invalid_and_foreign_packets_cannot_enable(self):
        for data in (b"MODE:TELEOP", b"255,255\n", b"garbage", packet("b"*16, 1, 1, "ARM")):
            self.link.sock.sendto(data, self.link.target)
        self.pump(.1)
        self.assertFalse(self.mock.enabled)
        self.assertIsNone(self.link.fault)

    def test_bad_telemetry_is_rejected(self):
        self.mock.silent = True
        time.sleep(.04)
        self.link.receive(time.monotonic())
        old_state = self.link.state.copy()
        invalid = dict(old_state, state_id=old_state["state_id"]+100, enabled=False, throttle=1)
        self.mock.sock.sendto(json.dumps(invalid).encode(), ("127.0.0.1", self.link.sock.getsockname()[1]))
        time.sleep(.02)
        self.link.receive(time.monotonic())
        self.assertEqual(self.link.state, old_state)
        self.assertGreater(self.link.rejected, 0)

    def test_envelope_limits(self):
        for args in ((65, 0), (50, 50), (float("nan"), 0), (True, 0)):
            with self.assertRaises(ValueError):
                packet("a"*16, 1, 1, "DRIVE", *args)
        self.assertIn(b"DRIVE,32,32", packet("a"*16, 1, 1, "DRIVE", 32, 32))


if __name__ == "__main__":
    unittest.main()
