import json
import logging
from math import atan2, cos, radians, sin, sqrt

log = logging.getLogger(__name__)

BRACKETS_MI = [5, 10, 20, 30, 50, 75, 100]
BRACKETS_M  = [mi * 1609.344 for mi in BRACKETS_MI]


def _haversine(lat1, lon1, lat2, lon2):
    R = 6_371_000
    p1, p2 = radians(lat1), radians(lat2)
    dp, dl  = radians(lat2 - lat1), radians(lon2 - lon1)
    a = sin(dp / 2) ** 2 + cos(p1) * cos(p2) * sin(dl / 2) ** 2
    return 2 * R * atan2(sqrt(a), sqrt(1 - a))


def _dist_from_latlng(latlng):
    """Build cumulative distance array (metres) from latlng stream data."""
    out = [0.0]
    for i in range(1, len(latlng)):
        out.append(out[-1] + _haversine(
            latlng[i-1][0], latlng[i-1][1],
            latlng[i][0],   latlng[i][1],
        ))
    return out


def compute_best_efforts(streams_json):
    """
    Returns list of {distanceMi, elapsedSecs, avgSpeedMps} for each
    distance bracket that the activity is long enough to contain.

    Uses a two-pointer sliding window over the distance+time streams to find
    the minimum elapsed time for any continuous stretch of exactly that distance.
    Falls back to computing distance from latlng if no distance stream exists.
    """
    try:
        streams = json.loads(streams_json or '{}')
    except Exception:
        return []

    dist_data = (streams.get('distance') or {}).get('data') or []
    time_data  = (streams.get('time')    or {}).get('data') or []

    # Fallback: build distance from latlng
    if not dist_data:
        latlng = (streams.get('latlng') or {}).get('data') or []
        if latlng and time_data and len(latlng) == len(time_data):
            dist_data = _dist_from_latlng(latlng)

    if not dist_data or not time_data or len(dist_data) != len(time_data) or len(dist_data) < 2:
        return []

    total_dist = dist_data[-1] - dist_data[0]
    n = len(dist_data)
    results = []

    for dist_mi, target_m in zip(BRACKETS_MI, BRACKETS_M):
        if total_dist < target_m:
            continue

        best_secs = None
        lo = 0
        for hi in range(1, n):
            # Advance lo: keep it as high as possible while still covering target_m
            while lo + 1 < hi and dist_data[hi] - dist_data[lo + 1] >= target_m:
                lo += 1
            if dist_data[hi] - dist_data[lo] >= target_m:
                elapsed = time_data[hi] - time_data[lo]
                if elapsed > 0 and (best_secs is None or elapsed < best_secs):
                    best_secs = elapsed

        if best_secs:
            results.append({
                'distanceMi': dist_mi,
                'elapsedSecs': best_secs,
                'avgSpeedMps': target_m / best_secs,
            })

    return results


def save_best_efforts(db, activity_id, activity_date, streams_json):
    """Compute and upsert best efforts for one activity. Does not commit."""
    efforts = compute_best_efforts(streams_json)
    date_str = str(activity_date or '')[:10]
    for e in efforts:
        db.execute('''
            INSERT INTO BestEffort (activityId, activityDate, distanceMi, elapsedSecs, avgSpeedMps)
            VALUES (?,?,?,?,?)
            ON CONFLICT(activityId, distanceMi) DO UPDATE SET
                elapsedSecs=excluded.elapsedSecs,
                avgSpeedMps=excluded.avgSpeedMps,
                activityDate=excluded.activityDate
        ''', [activity_id, date_str, e['distanceMi'], e['elapsedSecs'], e['avgSpeedMps']])
    return len(efforts)


def scan_all_best_efforts(db):
    """Backfill every activity. Commits at the end."""
    activities = db.execute(
        "SELECT id, startDateLocal, streams FROM Activity "
        "WHERE streams IS NOT NULL AND streams NOT IN ('null', '{}')"
    ).fetchall()
    total = 0
    for act in activities:
        total += save_best_efforts(db, act['id'], act['startDateLocal'], act['streams'])
    db.commit()
    log.warning('Best efforts scan — %d efforts across %d activities', total, len(activities))
    return len(activities)
