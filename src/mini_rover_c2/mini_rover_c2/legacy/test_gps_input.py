import unittest
from mini_rover_c2.legacy.gps_input import GPSData, nmea_sentence


def gga(utc="120000", lat="4000.0000", quality=1):
    return nmea_sentence(f"GPGGA,{utc},{lat},N,10500.0000,W,{quality},08,0.9,1600.0,M,-20.0,M,,")


class GPSTests(unittest.TestCase):
    def test_fix_coordinates_staleness_and_checksum(self):
        data = GPSData()
        row = data.consume(gga(), 10)
        self.assertEqual((row["latitude"], row["longitude"]), (40, -105))
        self.assertTrue(data.fresh(12))
        self.assertFalse(data.fresh(12.6))
        self.assertIsNone(data.consume(gga("120001")[:-2]+"ZZ", 13))
        self.assertEqual(data.last_fix, 10)
        self.assertEqual(data.bad, 1)

    def test_invalid_fix_never_becomes_zero_location(self):
        data = GPSData()
        data.consume(gga(), 1)
        row = data.consume(nmea_sentence("GPGGA,120001,,,,,0,00,99.9,,,,,,"), 2)
        self.assertFalse(row["valid"])
        self.assertIsNone(row["latitude"])
        self.assertFalse(data.fresh(2))
        self.assertEqual(len(data.samples), 1)

    def test_duplicates_backwards_and_midnight(self):
        data = GPSData()
        data.consume(gga("235959"), 1)
        self.assertIsNone(data.consume(gga("235959"), 2))
        self.assertIsNone(data.consume(gga("235958"), 3))
        self.assertEqual(data.duplicates, 2)
        self.assertIsNotNone(data.consume(gga("000000"), 4))
        self.assertEqual(data.last_fix, 4)

    def test_stationary_scatter_and_rmc_speed(self):
        data = GPSData()
        data.consume(gga(), 1)
        data.consume(gga("120001", "4000.0060"), 2)
        self.assertAlmostEqual(data.scatter()[2], 5.56, delta=.05)
        data.consume(nmea_sentence("GPRMC,120001,A,4000.0060,N,10500.0000,W,2.0,90.0,241026,,,A"), 2)
        self.assertAlmostEqual(data.speed_mps, 1.028888)
        data.consume(nmea_sentence("GPRMC,120002,V,,,,,,,241026,,,N"), 3)
        self.assertFalse(data.fresh(3))
        self.assertIsNone(data.speed_mps)

    def test_malformed_or_estimated_fixes_are_not_valid(self):
        data = GPSData()
        for latitude in ("nan", "inf", "4060.000", "9100.000", "-1"):
            self.assertIsNone(data.consume(gga(lat=latitude), 1))
        for quality in (0, 6, 7, 8):
            row = data.consume(gga(utc=f"12000{quality}", quality=quality), 2)
            self.assertFalse(row["valid"])
        self.assertEqual(len(data.samples), 0)


if __name__ == "__main__":
    unittest.main()
