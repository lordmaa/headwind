"""Road roughness map: the phone's shake readings (see services/sensors.py) laid onto the ground.

Each sidecar row that has a GPS position, and was moving, says how hard the phone was being shaken at that spot. Vibration grows with speed, so
every reading is eased towards what it would be at REF_SPEED (a steady 18 mph, scaled by the square root of the speed ratio: a real ride showed shake grows far
slower than linearly) and readings taken on steep climbs are dropped (pedalling and rocking the bike swamp the road there) before being averaged into ~20 m cells. Several rides over the same road
average out, so a pothole you hit once matters less than a surface that is rough every time.
"""
import json
import math

CELL_M = 20.0
REF_SPEED = 8.0          # m/s (about 18 mph): roughness readings are scaled to this speed
MIN_SPEED = 2.5          # slower than this, shake is mostly the rider, not the road
MAX_CLIMB = 6.0          # % gradient; steeper uphill samples are rider effort, not road
MIN_SAMPLES = 3          # seconds of data a cell needs before it is shown
M_PER_DEG = 111_320.0
FIELDS = ['t', 'rough', 'peak', 'gyro', 'tilt', 'press', 'lat', 'lng', 'spd', 'alt']


def norm(rough, speed):
    """Roughness as it would read at the reference speed."""
    if rough is None or speed is None or speed < MIN_SPEED:
        return None
    return rough * math.sqrt(REF_SPEED / max(speed, 3.0))


def _alt(press, p0):
    return 44330.0 * (1 - (press / p0) ** 0.1903)


def _grades(rows, ix):
    """Gradient (%) at each row from the barometer over +-5 s of travel; None where it cannot be told."""
    p = [r[ix['press']] for r in rows]
    v = [r[ix['spd']] or 0 for r in rows]
    p0 = next((x for x in p if x), None)
    out = [None] * len(rows)
    if not p0:
        return out
    cum = [0.0]
    for x in v:
        cum.append(cum[-1] + x)
    for i in range(5, len(rows) - 5):
        dist = cum[i + 5] - cum[i - 5]
        if dist >= 20 and p[i + 5] and p[i - 5]:
            out[i] = 100 * (_alt(p[i + 5], p0) - _alt(p[i - 5], p0)) / dist
    return out


def _cell(lat, lng):
    band = round(lat, 1)
    dlat = CELL_M / M_PER_DEG
    dlng = CELL_M / (M_PER_DEG * max(0.1, math.cos(math.radians(band))))
    return int(math.floor(lat / dlat)), int(math.floor(lng / dlng)), dlat, dlng


def rows_with_position(rows):
    """[(lat, lng, roughness_at_ref_speed)] for the rows of one sidecar that have a position and were moving."""
    ix = {f: i for i, f in enumerate(FIELDS)}
    out = []
    grades = _grades(rows, ix)
    for r, g in zip(rows, grades):
        lat, lng, n = r[ix['lat']], r[ix['lng']], norm(r[ix['rough']], r[ix['spd']])
        if g is not None and g > MAX_CLIMB:
            continue
        if lat is not None and lng is not None and n is not None:
            out.append((lat, lng, n))
    return out


def cells(db, rider_id, since=None, include_pocket=False, min_samples=MIN_SAMPLES):
    """Aggregate every sidecar of the rider into cells: [{'lat', 'lng', 'r', 'n', 'rides'}]. `since` is 'YYYY-MM-DD'. Pocket-carried data is left out unless asked for
    (a phone in a pocket mostly measures the rider)."""
    q = 'SELECT id, mount, data FROM RideSensor WHERE riderId=?'
    args = [rider_id]
    if since:
        q += ' AND startUtc >= ?'
        args.append(since)
    if not include_pocket:
        q += " AND COALESCE(mount, 'other') <> 'pocket'"
    acc = {}
    for s in db.execute(q, args).fetchall():
        try:
            rows = json.loads(s['data'])
        except ValueError:
            continue
        seen = set()
        for lat, lng, n in rows_with_position(rows):
            iy, ix_, dlat, dlng = _cell(lat, lng)
            c = acc.setdefault((iy, ix_), [0.0, 0, set(), dlat, dlng])
            c[0] += n
            c[1] += 1
            c[2].add(s['id'])
            seen.add((iy, ix_))
    out = []
    for (iy, ix_), (tot, n, rides, dlat, dlng) in acc.items():
        if n >= min_samples:
            out.append({'lat': round((iy + 0.5) * dlat, 6), 'lng': round((ix_ + 0.5) * dlng, 6), 'r': round(tot / n, 2), 'n': n, 'rides': len(rides)})
    return out


def summary(cs):
    """Headline numbers and colour breakpoints (percentiles of what this rider's roads actually read) for a set of cells."""
    if not cs:
        return {'cells': 0, 'km': 0, 'breaks': [], 'worst': []}
    vals = sorted(c['r'] for c in cs)
    pick = lambda p: round(vals[int(p * (len(vals) - 1))], 2)
    worst = sorted(cs, key=lambda c: (-c['r'], -c['n']))[:10]
    return {'cells': len(cs), 'km': round(len(cs) * CELL_M / 1000, 1), 'median': pick(0.5), 'breaks': [pick(0.25), pick(0.5), pick(0.8), pick(0.95)],
            'worst': worst, 'max': vals[-1]}


def track(rows, max_points=700):
    """A sidecar's own route coloured by roughness: [[lat, lng, r], ...] thinned for the ride page."""
    pts = rows_with_position(rows)
    if len(pts) > max_points:
        step = len(pts) / max_points
        pts = [pts[int(i * step)] for i in range(max_points)]
    return [[round(a, 6), round(b, 6), round(c, 2)] for a, b, c in pts]
