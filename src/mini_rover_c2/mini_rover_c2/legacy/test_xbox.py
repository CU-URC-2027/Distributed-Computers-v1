"""Exercise the real adapter with an emulated SDL controller API."""
import unittest
from unittest.mock import MagicMock, patch
from types import SimpleNamespace
from mini_rover_c2.legacy.xbox import Xbox, signed_axis


class XboxTests(unittest.TestCase):
    def make_adapter(self):
        pg = MagicMock()
        pg.JOYDEVICEREMOVED = 99
        pg.CONTROLLER_AXIS_LEFTX, pg.CONTROLLER_AXIS_LEFTY = 0, 1
        pg.CONTROLLER_AXIS_RIGHTX, pg.CONTROLLER_AXIS_RIGHTY = 2, 3
        pg.CONTROLLER_BUTTON_LEFTSHOULDER = 9
        pg.event.get.return_value = []
        api = MagicMock()
        api.get_count.return_value = 1
        api.is_controller.return_value = True
        device = api.Controller.return_value
        device.name = "Emulated Xbox"
        device.attached.return_value = True
        device.get_axis.side_effect = lambda i: [16384, -32768, -32768, 32767][i]
        device.get_button.return_value = True
        with patch.dict("sys.modules", {"pygame": pg, "pygame._sdl2": SimpleNamespace(controller=api)}):
            return Xbox(), pg, api, device

    def test_canonical_sticks_and_lb(self):
        adapter, pg, api, device = self.make_adapter()
        t, s, hold = adapter.read()
        self.assertEqual(t, 1)
        self.assertAlmostEqual(s, .5, places=4)
        self.assertTrue(hold)
        device.get_button.assert_called_with(9)
        adapter.stick = "Right"
        self.assertEqual(adapter.read(), (-1, -1, True))
        adapter.close()
        device.quit.assert_called_once()

    def test_missing_and_hotplug(self):
        adapter, pg, api, device = self.make_adapter()
        api.get_count.return_value = 0
        with self.assertRaisesRegex(RuntimeError, "No mapped"):
            adapter.read()
        api.get_count.return_value = 1
        adapter.read()
        device.as_joystick.return_value.get_instance_id.return_value = 12
        pg.event.get.return_value = [SimpleNamespace(type=99, instance_id=12)]
        with self.assertRaisesRegex(RuntimeError, "disconnected"):
            adapter.read()
        self.assertIsNone(adapter.device)
        pg.event.get.return_value = []
        adapter.read()
        self.assertIsNotNone(adapter.device)

    def test_normalization_endpoints(self):
        self.assertEqual(signed_axis(-32768), -1)
        self.assertEqual(signed_axis(32767), 1)
        self.assertEqual(signed_axis(0), 0)


if __name__ == "__main__":
    unittest.main()
