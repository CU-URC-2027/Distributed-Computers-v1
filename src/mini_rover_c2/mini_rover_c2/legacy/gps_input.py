"""Checksum-validated GGA/RMC input and local stationary-scatter statistics."""
from collections import deque
from datetime import datetime, timezone
import math
from mini_rover_c2.legacy.navigation import local_enu


def nmea_sentence(body):
    checksum = 0
    for char in body:
        checksum ^= ord(char)
    return f"${body}*{checksum:02X}"


def coordinate(raw, direction, latitude):
    value = float(raw)
    degrees = int(value // 100)
    minutes = value - degrees * 100
    limit = 90 if latitude else 180
    if not math.isfinite(value) or value < 0 or not 0 <= minutes < 60 or degrees > limit or (degrees == limit and minutes):
        raise ValueError("Coordinate range")
    allowed = ("N", "S") if latitude else ("E", "W")
    if direction not in allowed:
        raise ValueError("Coordinate hemisphere")
    return (degrees+minutes/60) * (-1 if direction in ("S", "W") else 1)


def utc_seconds(raw):
    if len(raw) < 6:
        raise ValueError("UTC missing")
    h, m, s = int(raw[:2]), int(raw[2:4]), float(raw[4:])
    if not 0 <= h < 24 or not 0 <= m < 60 or not 0 <= s < 61:
        raise ValueError("UTC invalid")
    return h*3600+m*60+s


class GPSData:
    def __init__(self):
        self.lines = self.bad = self.duplicates = 0
        self.last_rx = self.last_fix = -math.inf
        self.valid = False
        self.latitude = self.longitude = None
        self.satellites = self.hdop = self.altitude_m = None
        self.speed_mps = self.course_deg = None
        self.last_speed = -math.inf
        self.utc = None
        self.quality = 0
        self.seen_times = {}
        self.samples = deque(maxlen=3600)
        self.origin = None

    def consume(self, line, now):
        if not line.startswith("$"):
            return None
        self.lines += 1
        try:
            text = line.strip()
            if len(text) > 120 or text.count("*") != 1:
                raise ValueError("NMEA size/checksum")
            body, checksum = text[1:].split("*")
            if len(checksum) != 2 or nmea_sentence(body)[-2:] != checksum.upper():
                raise ValueError("NMEA checksum")
            fields = body.split(",")
            kind = fields[0][-3:]
            if kind not in ("GGA", "RMC"):
                return None
            stamp = utc_seconds(fields[1])
            previous = self.seen_times.get(kind)
            if previous is not None and (stamp == previous or (stamp < previous and previous-stamp < 43200)):
                self.duplicates += 1
                return None
            if kind == "GGA":
                if len(fields) < 15:
                    raise ValueError("Truncated GGA")
                quality = int(fields[6])
                if not 0 <= quality <= 8:
                    raise ValueError("Fix quality")
                valid = quality in (1, 2, 4, 5)  # exclude estimated/manual/simulation fixes
                lat = coordinate(fields[2], fields[3], True) if valid else None
                lon = coordinate(fields[4], fields[5], False) if valid else None
                satellites = int(fields[7]) if fields[7] else None
                hdop = float(fields[8]) if fields[8] else None
                altitude = float(fields[9]) if fields[9] else None
                if satellites is not None and not 0 <= satellites <= 99:
                    raise ValueError("Satellite count")
                if hdop is not None and (not math.isfinite(hdop) or hdop < 0):
                    raise ValueError("HDOP")
                if altitude is not None and (not math.isfinite(altitude) or fields[10] != "M"):
                    raise ValueError("Altitude units")
                self.valid, self.quality = valid, quality
                self.latitude, self.longitude = lat, lon
                self.satellites, self.hdop, self.altitude_m = satellites, hdop, altitude
                if valid:
                    self.last_fix = now
                    self.origin = self.origin or (lat, lon)
                    e, n = local_enu(lat, lon, *self.origin)
                    self.samples.append((now, e, n, lat, lon))
            else:
                if len(fields) < 10 or fields[2] not in ("A", "V"):
                    raise ValueError("RMC status")
                if fields[2] == "V":
                    self.valid = False
                    self.speed_mps = self.course_deg = None
                else:
                    speed = float(fields[7])*.514444 if fields[7] else None
                    course = float(fields[8]) if fields[8] else None
                    if speed is not None and (not math.isfinite(speed) or speed < 0):
                        raise ValueError("Speed")
                    if course is not None and (not math.isfinite(course) or not 0 <= course <= 360):
                        raise ValueError("Course")
                    self.speed_mps, self.course_deg = speed, course
                    self.last_speed = now
                # Position authority stays GGA. RMC does not revive a stale GGA.
            self.last_rx = now
            self.utc = fields[1]
            self.seen_times[kind] = stamp
            return dict(type="gps", nmea_type=kind, receipt_monotonic_s=now,
                        receipt_utc=datetime.now(timezone.utc).isoformat(), nmea_utc=self.utc,
                        valid=self.valid and now-self.last_fix < 2.5,
                        latitude=self.latitude if self.valid else None,
                        longitude=self.longitude if self.valid else None,
                        satellites=self.satellites, hdop=self.hdop,
                        speed_mps=self.speed_mps, quality=self.quality)
        except (ValueError, IndexError, OverflowError):
            self.bad += 1
            return None

    def fresh(self, now):
        return self.valid and now-self.last_fix < 2.5

    def scatter(self):
        if len(self.samples) < 2:
            return None
        e = sum(p[1] for p in self.samples)/len(self.samples)
        n = sum(p[2] for p in self.samples)/len(self.samples)
        rms = math.sqrt(sum((p[1]-e)**2+(p[2]-n)**2 for p in self.samples)/len(self.samples))
        return e, n, rms
