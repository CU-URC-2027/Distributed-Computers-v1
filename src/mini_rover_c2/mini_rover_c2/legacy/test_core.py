import math
import unittest
from mini_rover_c2.legacy.core import Control, legacy_drive


class ControlTests(unittest.TestCase):
    def ready(self):
        c = Control()
        c.receive("Xbox", 0, 0, False, 0)
        self.assertTrue(c.enable(0)[0])
        return c

    def test_startup_blocks_motion(self):
        c = Control()
        c.receive("Xbox", 1, 0, True, 0)
        self.assertEqual(c.output(0)["left"], 0)

    def test_neutral_and_fresh_input_required(self):
        c = Control()
        self.assertFalse(c.enable(0)[0])
        for throttle, steering, hold in ((1, 0, False), (0, 1, False), (0, 0, True)):
            c.receive("Xbox", throttle, steering, hold, 0)
            self.assertFalse(c.enable(0)[0])

    def test_deadman_and_bounded_mixing(self):
        c = self.ready()
        c.receive("Xbox", 1, 1, False, .05)
        self.assertEqual(c.output(.05)["left"], 0)
        c.receive("Xbox", 1, 1, True, .1)
        out = c.output(.1)
        self.assertEqual(out["left"], .25)
        self.assertEqual(out["right"], 0)

    def test_stop_latches_until_reset_and_enable(self):
        c = self.ready()
        c.stop()
        c.receive("Xbox", 1, 0, True, .05)
        self.assertEqual(c.output(.05)["left"], 0)
        self.assertFalse(c.enable(.05)[0])
        c.reset_stop()
        self.assertFalse(c.armed)
        self.assertFalse(c.enable(.05)[0])

    def test_source_change_disarms_and_ignores_other_source(self):
        c = self.ready()
        c.select("RC")
        c.receive("RC", 0, 0, False, .05)
        self.assertFalse(c.armed)
        self.assertTrue(c.enable(.05)[0])
        c.receive("Xbox", 1, 1, True, .1)
        self.assertEqual(c.output(.1)["left"], 0)

    def test_timeout_and_reconnection_do_not_rearm(self):
        c = self.ready()
        c.receive("Xbox", 1, 0, True, .05)
        self.assertGreater(c.output(.05)["left"], 0)
        c.receive("Xbox", 1, 0, True, .4)
        self.assertFalse(c.armed)
        self.assertEqual(c.output(.4)["left"], 0)

    def test_invalid_numbers_disarm(self):
        for value in (math.nan, math.inf, -math.inf, 1.1):
            c = self.ready()
            with self.assertRaises(ValueError):
                c.receive("Xbox", value, 0, True, .05)
            self.assertFalse(c.armed)

    def test_arm_mode_inhibits_drive_and_times_out(self):
        c = self.ready()
        c.arm_enabled = True
        c.receive("Xbox", 1, 0, True, .05)
        c.receive_jog("elbow", 1, .05)
        out = c.output(.05)
        self.assertEqual(out["left"], 0)
        self.assertEqual(out["arm_preview"]["rate"], .2)
        c.receive("Xbox", 0, 0, False, .2)
        self.assertEqual(c.output(.31)["arm_preview"]["rate"], 0)

    def test_stop_clears_arm(self):
        c = self.ready()
        c.arm_enabled = True
        c.receive_jog("gripper", -1, .05)
        c.stop()
        self.assertEqual(c.output(.1)["arm_preview"]["rate"], 0)

    def test_legacy_protocol(self):
        self.assertEqual(legacy_drive(1, -1), "255,-255\n")
        self.assertEqual(legacy_drive(.01, -.01), "3,-3\n")

    def test_small_command_is_not_dead_zoned_twice(self):
        c = self.ready()
        c.receive("Xbox", .2, 0, True, .05)
        out = c.output(.05)
        self.assertAlmostEqual(out["left"], .05)
        self.assertEqual(out["legacy_drive_preview"], "13,0\n")


if __name__ == "__main__":
    unittest.main()
