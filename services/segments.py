import json
import logging
from math import atan2, cos, radians, sin, sqrt

log = logging.getLogger(__name__)

TOLERANCE_M = 30  # metres — how close to a segment endpoint counts as a hit
CHECKPOINT_M = 60  # metres — tolerance for interior waypoints (wider to absorb GPS drift)
CHECKPOINT_N = 4   # number of evenly-spaced interior checkpoints to sample


def _haversine(lat1, lon1, lat2, lon2):
    R = 6_371_000
    p1, p2 = radians(lat1), radians(lat2)
    dp, dl  = radians(lat2 - lat1), radians(lon2 - lon1)
    a = sin(dp / 2) ** 2 + cos(p1) * cos(p2) * sin(dl / 2) ** 2
    return 2 * R * atan2(sqrt(a), sqrt(1 - a))


def _sample_checkpoints(polyline_json, n=CHECKPOINT_N):
    """Return n evenly-spaced interior points from a segment polyline, skipping endpoints."""
    try:
        pts = json.loads(polyline_json) if polyline_json else []
    except Exception:
        pts = []
    if len(pts) < 4:
        return []
    interior = pts[1:-1]
    step = max(1, len(interior) // (n + 1))
    return [interior[min(i * step, len(interior) - 1)] for i in range(1, n + 1)]


def match_segment(activity_streams_json, seg):
    """
    Returns elapsed_secs (int) for the fastest valid effort on this segment,
    or None if the activity never traverses it.

    Finds every entry into the start-zone (outside→inside TOLERANCE_M
    transition), tries each as a candidate start, then looks for the closest
    approach to the end *after* that candidate.  Taking the minimum elapsed
    time across all candidates means:
      - Loop rides (up + back down past start) are handled correctly
      - Direction is enforced (end must follow start in the GPS stream)
      - Multiple laps of the same segment return the fastest lap
    """
    try:
        streams = json.loads(activity_streams_json or '{}')
    except Exception:
        return None

    latlng = (streams.get('latlng') or {}).get('data') or []
    times  = (streams.get('time')   or {}).get('data') or []

    if not latlng or not times:
        return None
    n = min(len(latlng), len(times))
    latlng = latlng[:n]
    times  = times[:n]

    dist_m      = seg['distanceM'] or 0
    checkpoints = _sample_checkpoints(seg['polyline']) if seg['polyline'] else []
    best_elapsed = None

    def _try_start(start_idx):
        nonlocal best_elapsed
        # Find the first entry into the end zone (mirrors start-zone logic).
        # Committing to the first crossing prevents a later near-miss on the
        # return leg from inflating the elapsed time.
        in_end_zone = False
        end_idx, end_dist = None, float('inf')
        for j in range(start_idx + 1, len(latlng)):
            d = _haversine(latlng[j][0], latlng[j][1], seg['endLat'], seg['endLng'])
            if d < TOLERANCE_M:
                in_end_zone = True
                if d < end_dist:
                    end_dist = d
                    end_idx  = j
            elif in_end_zone:
                break  # exited end zone — use best point from this visit
        if end_idx is None:
            return
        elapsed = times[end_idx] - times[start_idx]
        if elapsed <= 0:
            return
        if dist_m and (dist_m / elapsed) < 2.0:
            return  # slower than 4.5 mph — false match
        if dist_m:
            # Reject shortcut routes: actual GPS distance must be ≥70% of
            # stored segment distance so riders who take a shorter road between
            # the same start/end coordinates don't appear on the leaderboard.
            actual_dist = sum(
                _haversine(latlng[j][0], latlng[j][1], latlng[j + 1][0], latlng[j + 1][1])
                for j in range(start_idx, end_idx)
            )
            if actual_dist < dist_m * 0.7:
                return
        # For segments with a polyline, require the activity to pass near each
        # sampled interior checkpoint in order. This catches rides that hit the
        # start and end zones via a completely different route (especially loops
        # where start ≈ end, so the endpoint check alone is not enough).
        if checkpoints:
            cp_cursor = start_idx
            for cp_lat, cp_lng in checkpoints:
                hit = False
                for j in range(cp_cursor + 1, end_idx):
                    if _haversine(latlng[j][0], latlng[j][1], cp_lat, cp_lng) < CHECKPOINT_M:
                        cp_cursor = j
                        hit = True
                        break
                if not hit:
                    return
        if best_elapsed is None or elapsed < best_elapsed:
            best_elapsed = elapsed

    # Walk the stream, detecting each entry into the start zone and tracking
    # the closest approach within that visit.
    in_zone      = False
    zone_best_i  = None
    zone_best_d  = float('inf')

    for i, (lat, lng) in enumerate(latlng):
        d = _haversine(lat, lng, seg['startLat'], seg['startLng'])
        if d < TOLERANCE_M:
            if not in_zone:
                in_zone = True
            if d < zone_best_d:
                zone_best_d = d
                zone_best_i = i
        else:
            if in_zone:
                _try_start(zone_best_i)
                in_zone = False
                zone_best_i = None
                zone_best_d = float('inf')

    # Ride ends while still inside the start zone
    if in_zone and zone_best_i is not None:
        _try_start(zone_best_i)

    return best_elapsed


def _refresh_prs(db, segment_id):
    db.execute('UPDATE SegmentEffort SET isPR=0 WHERE segmentId=?', [segment_id])
    # Mark each rider's personal best
    riders = db.execute('''
        SELECT DISTINCT a.riderId
        FROM SegmentEffort e
        JOIN Activity a ON a.id = e.activityId
        WHERE e.segmentId=? AND a.riderId IS NOT NULL
    ''', [segment_id]).fetchall()
    for row in riders:
        best = db.execute('''
            SELECT e.id FROM SegmentEffort e
            JOIN Activity a ON a.id = e.activityId
            WHERE e.segmentId=? AND a.riderId=?
            ORDER BY e.elapsedSecs ASC LIMIT 1
        ''', [segment_id, row[0]]).fetchone()
        if best:
            db.execute('UPDATE SegmentEffort SET isPR=1 WHERE id=?', [best[0]])


def scan_activity_against_segments(db, activity, segments):
    """Scan one activity against a list of segments. Commits nothing."""
    matched = 0
    for seg in segments:
        elapsed = match_segment(activity['streams'], seg)
        if elapsed is None:
            continue
        dist_m     = seg['distanceM'] or 0
        avg_speed  = dist_m / elapsed if dist_m and elapsed else None
        db.execute('''
            INSERT INTO SegmentEffort
              (segmentId, activityId, activityDate, elapsedSecs, avgSpeedMps)
            VALUES (?,?,?,?,?)
            ON CONFLICT(segmentId, activityId) DO UPDATE SET
              elapsedSecs=excluded.elapsedSecs,
              avgSpeedMps=excluded.avgSpeedMps
        ''', [
            seg['id'],
            activity['id'],
            str(activity['startDateLocal'] or '')[:10],
            elapsed,
            avg_speed,
        ])
        matched += 1
    return matched


def scan_all_activities(db, segment_ids=None):
    """
    Scan every activity against all (or specific) segments.
    Returns number of activities scanned.
    """
    if segment_ids:
        placeholders = ','.join('?' * len(segment_ids))
        segments = db.execute(
            f'SELECT * FROM Segment WHERE id IN ({placeholders})', segment_ids
        ).fetchall()
    else:
        segments = db.execute('SELECT * FROM Segment').fetchall()

    if not segments:
        return 0

    # Clear existing efforts for these segments so stale rows (e.g. from
    # previously looser matching) don't survive a rescan.
    seg_ids = [s['id'] for s in segments]
    placeholders = ','.join('?' * len(seg_ids))
    db.execute(f'DELETE FROM SegmentEffort WHERE segmentId IN ({placeholders})', seg_ids)

    activities = db.execute(
        "SELECT id, startDateLocal, streams FROM Activity "
        "WHERE streams IS NOT NULL AND streams NOT IN ('null', '{}')"
    ).fetchall()

    for act in activities:
        scan_activity_against_segments(db, act, segments)

    for seg in segments:
        _refresh_prs(db, seg['id'])

    db.commit()
    log.warning('Segment scan complete — %d activities, %d segments', len(activities), len(segments))
    return len(activities)
