"""Local metric comparisons. Command integration is a model, not ground truth."""
from bisect import bisect_left
import json
import math


def finite(value):
    if isinstance(value, bool):
        raise ValueError("Expected a finite number")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("Expected a finite number")
    return value


def ecef(lat, lon):
    lat, lon = finite(lat), finite(lon)
    if not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise ValueError("Latitude/longitude out of range")
    lat, lon = math.radians(lat), math.radians(lon)
    a, e2 = 6378137.0, 6.69437999014e-3
    n = a / math.sqrt(1 - e2 * math.sin(lat) ** 2)
    return n * math.cos(lat) * math.cos(lon), n * math.cos(lat) * math.sin(lon), n * (1 - e2) * math.sin(lat)


def local_enu(lat, lon, origin_lat, origin_lon):
    """WGS84 lat/lon to local East/North metres, zero ellipsoid height.

    Suitable for this short, horizontal bench/field comparison, not terrain or
    altitude navigation. Both sources must share this origin and orientation.
    """
    delta = [a - b for a, b in zip(ecef(lat, lon), ecef(origin_lat, origin_lon))]
    phi, lam = math.radians(float(origin_lat)), math.radians(float(origin_lon))
    x, y, z = delta
    return (-math.sin(lam) * x + math.cos(lam) * y,
            -math.sin(phi) * math.cos(lam) * x - math.sin(phi) * math.sin(lam) * y + math.cos(phi) * z)


def metrics(pairs):
    """Pairs are (time, reference_e, reference_n, gps_e, gps_n)."""
    if not pairs:
        return {"count": 0, "rmse_m": None, "latest_m": None, "bias_e_m": None, "bias_n_m": None}
    de = [p[3] - p[1] for p in pairs]
    dn = [p[4] - p[2] for p in pairs]
    return {"count": len(pairs), "rmse_m": math.sqrt(sum(x*x + y*y for x, y in zip(de, dn)) / len(pairs)),
            "latest_m": math.hypot(de[-1], dn[-1]), "bias_e_m": sum(de)/len(de), "bias_n_m": sum(dn)/len(dn)}


def compare_log(path, max_gap_s=1.0):
    """Read origin + reference + GPS JSONL. No extrapolation or auto alignment."""
    references, fixes, origins = [], [], []
    max_gap_s = finite(max_gap_s)
    if max_gap_s <= 0:
        raise ValueError("Maximum interpolation gap must be positive")
    with open(path, encoding="utf-8-sig") as stream:
        for index, line in enumerate(stream, 1):
            if index > 100000:
                raise ValueError("Log exceeds 100000 rows; split the run")
            if not line.strip():
                continue
            row = json.loads(line)
            kind = row.get("type")
            if kind == "origin":
                ecef(row["latitude"], row["longitude"])
                origins.append((row["latitude"], row["longitude"]))
            elif kind == "reference":
                references.append(tuple(finite(row[k]) for k in ("time_s", "east_m", "north_m")))
            elif kind == "gps":
                if type(row.get("valid")) is not bool:
                    raise ValueError("GPS valid must be true or false")
                if row["valid"]:
                    ecef(row["latitude"], row["longitude"])
                    fixes.append(tuple(finite(row[k]) for k in ("time_s", "latitude", "longitude")))
            else:
                raise ValueError(f"Unknown row type on line {index}")
    if len(origins) != 1 or not references:
        raise ValueError("Need exactly one origin and reference positions")
    references.sort()
    times = [r[0] for r in references]
    if len(set(times)) != len(times):
        raise ValueError("Reference timestamps must be unique")
    if len({f[0] for f in fixes}) != len(fixes):
        raise ValueError("GPS timestamps must be unique")
    pairs, skipped = [], 0
    for t, lat, lon in sorted(fixes):
        i = bisect_left(times, t)
        if i < len(times) and times[i] == t:
            _, e, n = references[i]
        elif i == 0 or i == len(times) or times[i] - times[i-1] > max_gap_s:
            skipped += 1
            continue
        else:
            a, b = references[i-1], references[i]
            weight = (t-a[0]) / (b[0]-a[0])
            e, n = a[1]+weight*(b[1]-a[1]), a[2]+weight*(b[2]-a[2])
        ge, gn = local_enu(lat, lon, *origins[0])
        pairs.append((t, e, n, ge, gn))
    return pairs, skipped


def integrate_pose(east, north, yaw, left, right, dt):
    """Toy differential drive: 1 m/s at full wheel input; track width 0.5 m.

    ENU yaw is radians CCW from East. Positive UI steering turns right.
    """
    velocity, omega = (left + right) / 2, (right - left) / .5
    mid = yaw + omega * dt / 2
    return east + math.cos(mid)*velocity*dt, north + math.sin(mid)*velocity*dt, yaw + omega*dt
