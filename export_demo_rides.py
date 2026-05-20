"""
export_demo_rides.py

Selects a representative cross-section of rides, strips GPS points within
PRIVACY_M metres of home at both ends, and writes clean GPX files.

Usage:
    python3 export_demo_rides.py [--radius 500] [--count 1000] [--seed 42] [--out ./demo_gpx]
"""

import argparse
import json
import math
import os
import random
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

HOME_LAT  =  54.73262
HOME_LNG  = -1.73997

BANDS = [
    (0,   20,  "short"),
    (20,  40,  "medium"),
    (40,  70,  "long"),
    (70,  999, "epic"),
]

# ── helpers ───────────────────────────────────────────────────────────────────
def haversine_m(lat1, lng1, lat2, lng2):
    R = 6_371_000
    φ1, φ2 = math.radians(lat1), math.radians(lat2)
    dφ = math.radians(lat2 - lat1)
    dλ = math.radians(lng2 - lng1)
    a = math.sin(dφ/2)**2 + math.cos(φ1)*math.cos(φ2)*math.sin(dλ/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

def near_home(lat, lng, radius):
    return haversine_m(lat, lng, HOME_LAT, HOME_LNG) < radius

def strip_privacy(latlng, radius):
    if not latlng:
        return [], 0
    start = 0
    for i, (lat, lng) in enumerate(latlng):
        if not near_home(lat, lng, radius):
            start = i
            break
    end = len(latlng)
    for i in range(len(latlng) - 1, start, -1):
        if not near_home(latlng[i][0], latlng[i][1], radius):
            end = i + 1
            break
    return latlng[start:end], start

def _esc(s):
    return (s or '').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

def to_gpx(name, start_dt, latlng, altitude=None, time_offsets=None):
    """
    Build a valid GPX string. Each trackpoint gets a <time> element so
    gpxpy can parse it. time_offsets is a list of elapsed seconds; if
    absent, evenly spaces points over a 1 m/s assumed pace.
    """
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<gpx version="1.1" creator="Headwind" xmlns="http://www.topografix.com/GPX/1/1">',
        f'  <metadata><name>{_esc(name)}</name></metadata>',
        '  <trk>',
        f'    <name>{_esc(name)}</name>',
        '    <trkseg>',
    ]
    n = len(latlng)
    for i, (lat, lng) in enumerate(latlng):
        if time_offsets and i < len(time_offsets):
            secs = time_offsets[i]
        else:
            secs = i  # fallback: 1 point per second

        pt_time = (start_dt + timedelta(seconds=secs)).strftime('%Y-%m-%dT%H:%M:%SZ')
        lines.append(f'      <trkpt lat="{lat:.6f}" lon="{lng:.6f}">')
        if altitude and i < len(altitude):
            lines.append(f'        <ele>{altitude[i]:.1f}</ele>')
        lines.append(f'        <time>{pt_time}</time>')
        lines.append(f'      </trkpt>')

    lines += ['    </trkseg>', '  </trk>', '</gpx>']
    return '\n'.join(lines)

def dist_mi(metres):
    return (metres or 0) / 1609.344

# ── selection ─────────────────────────────────────────────────────────────────
def select_rides(rides, target, seed):
    random.seed(seed)
    buckets = defaultdict(list)
    for r in rides:
        dm = dist_mi(r['distance'])
        band = next((b[2] for b in BANDS if b[0] <= dm < b[1]), 'short')
        year = (r['startDateLocal'] or '')[:4]
        buckets[(year, band)].append(r)

    for v in buckets.values():
        random.shuffle(v)

    keys = sorted(buckets.keys())
    selected = []
    exhausted = set()
    iters = {k: iter(v) for k, v in buckets.items()}
    while len(selected) < target and len(exhausted) < len(keys):
        for k in keys:
            if k in exhausted:
                continue
            try:
                selected.append(next(iters[k]))
            except StopIteration:
                exhausted.add(k)
            if len(selected) >= target:
                break
    return selected

# ── main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--radius', type=int, default=500)
    parser.add_argument('--count',  type=int, default=1000)
    parser.add_argument('--seed',   type=int, default=42)
    parser.add_argument('--out',    default='./demo_gpx')
    args = parser.parse_args()

    os.makedirs(args.out, exist_ok=True)

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from app import create_app
    app = create_app()

    with app.app_context():
        from database import query_db
        rides = query_db('''
            SELECT id, name, startDateLocal, startDate, distance,
                   totalElevationGain, movingTime, streams
            FROM Activity
            WHERE riderId = (SELECT id FROM Rider WHERE isDefault=1)
              AND streams IS NOT NULL
              AND distance > 5000
            ORDER BY startDateLocal ASC
        ''')
        print(f"Found {len(rides)} rides with GPS data")

    selected = select_rides(rides, args.count, args.seed)
    print(f"Selected {len(selected)} rides, stripping {args.radius}m privacy zone…")

    exported = skipped = 0
    for i, r in enumerate(selected):
        try:
            streams  = json.loads(r['streams'])
            latlng   = (streams.get('latlng')    or {}).get('data') or []
            altitude = (streams.get('altitude')  or {}).get('data') or []
            time_s   = (streams.get('time')      or {}).get('data') or []

            stripped, offset = strip_privacy(latlng, args.radius)
            if len(stripped) < 20:
                skipped += 1
                continue

            alt_stripped  = altitude[offset: offset + len(stripped)] if altitude else []
            time_stripped = time_s[offset:   offset + len(stripped)] if time_s   else []
            # Re-zero time offsets so they start from 0
            if time_stripped:
                t0 = time_stripped[0]
                time_stripped = [t - t0 for t in time_stripped]

            date_str = r['startDateLocal'] or r['startDate'] or ''
            try:
                start_dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
            except Exception:
                start_dt = datetime.now(timezone.utc)

            name = r['name'] or f"Ride {i+1}"
            gpx  = to_gpx(name, start_dt, stripped, alt_stripped, time_stripped)

            safe = f"{date_str[:10]}_{name[:40]}".replace('/', '-').replace(' ', '_')
            with open(os.path.join(args.out, f"{safe}.gpx"), 'w', encoding='utf-8') as fh:
                fh.write(gpx)
            exported += 1
        except Exception as e:
            print(f"  ✗ {r['id']}: {e}")
            skipped += 1

    print(f"\nDone: {exported} exported, {skipped} skipped")
    print(f"Files in: {os.path.abspath(args.out)}")
    years = defaultdict(int)
    for r in selected:
        years[(r['startDateLocal'] or '')[:4]] += 1
    print("\nYear distribution:")
    for y, c in sorted(years.items()):
        print(f"  {y}: {c} rides")

if __name__ == '__main__':
    main()
