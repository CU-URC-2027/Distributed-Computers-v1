import json
import math
from pathlib import Path
import tempfile
import unittest
from mini_rover_c2.legacy.navigation import local_enu, metrics, compare_log, integrate_pose


class NavigationTests(unittest.TestCase):
    def test_equator_metres_and_origin(self):
        self.assertEqual(local_enu(40, -105, 40, -105), (0, 0))
        e, n = local_enu(0, math.degrees(1/6378137), 0, 0)
        self.assertAlmostEqual(e, 1, places=7)
        self.assertAlmostEqual(n, 0)
        for lat, lon in ((91, 0), (0, 181), (float('nan'), 0)):
            with self.assertRaises(ValueError):
                local_enu(lat, lon, 0, 0)

    def test_metrics_and_example(self):
        pairs, skipped = compare_log(Path(__file__).parent / "examples" / "gps_comparison.jsonl")
        self.assertEqual(len(pairs), 3)
        self.assertEqual(skipped, 0)
        self.assertAlmostEqual(metrics(pairs)["rmse_m"], math.sqrt(2), places=4)
        self.assertIsNone(metrics([])["rmse_m"])

    def compare(self, rows):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "test.jsonl"
            path.write_text('\n'.join(json.dumps(r) for r in rows))
            return compare_log(path)

    def test_no_extrapolation_or_large_gap(self):
        rows = [{"type":"origin", "latitude":0,"longitude":0},
                {"type":"reference", "time_s":0,"east_m":0,"north_m":0},
                {"type":"reference", "time_s":3,"east_m":3,"north_m":0}]
        for t in (-1, 1, 4):
            rows.append({"type":"gps","time_s":t,"latitude":0,"longitude":0,"valid":True})
        self.assertEqual(self.compare(rows), ([], 3))
        rows[-1]['valid'] = 'false'
        with self.assertRaises(ValueError):
            self.compare(rows)

    def test_pose_straight_and_turn(self):
        e, n, yaw = integrate_pose(0, 0, math.pi/2, .25, .25, 1)
        self.assertAlmostEqual(e, 0)
        self.assertAlmostEqual(n, .25)
        e, n, yaw = integrate_pose(0, 0, 0, .25, -.25, 1)
        self.assertEqual((e, n), (0, 0))
        self.assertLess(yaw, 0)
