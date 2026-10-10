import json
import logging
from math import atan2, ceil, cos, isfinite, radians, sin, sqrt

log = logging.getLogger(__name__)

TOLERANCE_M = 30  # metres — how close to a segment endpoint counts as a hit
CHECKPOINT_M = 60  # metres — tolerance for interior waypoints (wider to absorb GPS drift)
CHECKPOINT_N = 4   # number of evenly-spaced interior checkpoints to sample
MIN_SPEED_MPS = {'ride': 2.0, 'run': 1.2}   # slower than this is a false match (4.5 mph on a bike; a 13:50/mi shuffle on foot)
RUN_SPORTS = {'run', 'trailrun', 'virtualrun'}


def seg_sport(seg):
    try:
        return (seg['sport'] if 'sport' in seg.keys() else None) or 'ride'
    except AttributeError:
        return seg.get('sport') or 'ride'


def sport_family(sport_type):
    """'run' for runs, 'ride' for everything else (other activity types have always been scanned against ride segments)."""
    return 'run' if (sport_type or '').lower() in RUN_SPORTS else 'ride'


def _activity_family(db, activity):
    """'ride' / 'run' for an activity or workout row, or None for a workout that is not a run (walks and hikes have no segments)."""
    try:
        keys = activity.keys()
    except AttributeError:
        keys = []
    st = activity['sportType'] if 'sportType' in keys else (activity['sport'] if 'sport' in keys else None)
    if st is None:
        r = db.execute('SELECT sportType FROM Activity WHERE id=?', [activity['id']]).fetchone()
        if r:
            st = r[0]
        else:
            w = db.execute('SELECT sport FROM Workout WHERE id=?', [activity['id']]).fetchone()
            if not w:
                return 'ride'
            if (w[0] or '').lower() not in RUN_SPORTS:
                return None
            st = w[0]
    return sport_family(st)


def _haversine(lat1, lon1, lat2, lon2):
    R = 6_371_000
    p1, p2 = radians(lat1), radians(lat2)
    dp, dl  = radians(lat2 - lat1), radians(lon2 - lon1)
    a = sin(dp / 2) ** 2 + cos(p1) * cos(p2) * sin(dl / 2) ** 2
    return 2 * R * atan2(sqrt(a), sqrt(1 - a))


def valid_coord(lat, lng):
    """(lat, lng) as floats if they are a real position, else None. Friends can supply segments, so never trust stored values."""
    try:
        la, ln = float(lat), float(lng)
    except (TypeError, ValueError):
        return None
    if not (isfinite(la) and isfinite(ln)) or abs(la) > 90 or abs(ln) > 180:
        return None
    return [round(la, 6), round(ln, 6)]


def clean_polyline(raw, max_points=1500):
    """Parse + validate a segment shape (JSON text or list of [lat, lng]). Returns a clean list, downsampled if huge, or None if
    it is missing, malformed or contains any impossible point. Rejecting the whole shape beats drawing part of a bad one."""
    try:
        pts = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
    except ValueError:
        return None
    if not isinstance(pts, list):
        return None
    out = []
    for p in pts:
        try:
            c = valid_coord(p[0], p[1])
        except (TypeError, IndexError, KeyError):
            return None
        if c is None:
            return None
        out.append(c)
    if len(out) < 2:
        return None
    if len(out) > max_points:
        step = ceil(len(out) / max_points)
        out = out[::step] + ([out[-1]] if (len(out) - 1) % step else [])
    return out


def _path_length_m(pts):
    return sum(_haversine(a[0], a[1], b[0], b[1]) for a, b in zip(pts, pts[1:]))


def _slice_between(streams_json, seg, tol_m=150, dist_tol=0.35):
    """The part of a ride's GPS trace that runs from the segment's start to its finish, or None if this ride doesn't clearly
    follow it: both ends must be near, in the right order, and the path length must agree with the segment distance."""
    try:
        ll = ((json.loads(streams_json or '{}').get('latlng') or {}).get('data')) or []
    except ValueError:
        return None
    pts = [valid_coord(p[0], p[1]) for p in ll if isinstance(p, (list, tuple)) and len(p) >= 2]
    pts = [p for p in pts if p]
    start, end = valid_coord(seg['startLat'], seg['startLng']), valid_coord(seg['endLat'], seg['endLng'])
    if len(pts) < 2 or not start or not end:
        return None
    si = min(range(len(pts)), key=lambda i: _haversine(start[0], start[1], pts[i][0], pts[i][1]))
    if _haversine(start[0], start[1], pts[si][0], pts[si][1]) > tol_m:
        return None
    after = range(si + 1, len(pts))
    if not after:
        return None
    ei = min(after, key=lambda i: _haversine(end[0], end[1], pts[i][0], pts[i][1]))
    if _haversine(end[0], end[1], pts[ei][0], pts[ei][1]) > tol_m:
        return None
    piece = pts[si:ei + 1]
    if len(piece) < 2:
        return None
    dist = seg['distanceM']
    if dist:
        length = _path_length_m(piece)
        if abs(length - float(dist)) / float(dist) > dist_tol:
            return None
    return clean_polyline(piece, max_points=400)


def recover_polyline(db, seg):
    """Rebuild a missing segment shape from the ride it was drawn on, else from the fastest matching efforts. None if no ride
    verifiably follows the segment — callers should then say the shape is unavailable rather than draw a straight line."""
    tried = set()
    ids = [seg['sourceActivityId']] if seg['sourceActivityId'] else []
    ids += [r[0] for r in db.execute(
        'SELECT activityId FROM SegmentEffort WHERE segmentId=? ORDER BY elapsedSecs ASC LIMIT 6', [seg['id']])]
    for aid in ids:
        if not aid or aid in tried:
            continue
        tried.add(aid)
        row = db.execute('SELECT streams FROM Activity WHERE id=?', [aid]).fetchone() or db.execute('SELECT streams FROM Workout WHERE id=?', [aid]).fetchone()
        if row and row[0]:
            shape = _slice_between(row[0], seg)
            if shape:
                return shape
    return None


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
        if dist_m and (dist_m / elapsed) < MIN_SPEED_MPS[seg_sport(seg)]:
            return  # too slow to be a real effort — false match
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
        JOIN SegActivity a ON a.id = e.activityId
        WHERE e.segmentId=? AND a.riderId IS NOT NULL
    ''', [segment_id]).fetchall()
    for row in riders:
        best = db.execute('''
            SELECT e.id FROM SegmentEffort e
            JOIN SegActivity a ON a.id = e.activityId
            WHERE e.segmentId=? AND a.riderId=?
            ORDER BY e.elapsedSecs ASC LIMIT 1
        ''', [segment_id, row[0]]).fetchone()
        if best:
            db.execute('UPDATE SegmentEffort SET isPR=1 WHERE id=?', [best[0]])


def scan_activity_against_segments(db, activity, segments):
    """Scan one activity against a list of segments. Commits nothing."""
    matched = 0
    fam = _activity_family(db, activity)
    for seg in segments:
        if fam is None or seg_sport(seg) != fam:        # runs only match run segments, rides only ride segments
            continue
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
        "SELECT id, startDateLocal, streams, sportType FROM Activity "
        "WHERE streams IS NOT NULL AND streams NOT IN ('null', '{}')"
    ).fetchall()
    if any(seg_sport(s) == 'run' for s in segments):     # phone-recorded runs live in Workout
        activities = list(activities) + db.execute(
            "SELECT id, startDateLocal, streams, sport AS sportType FROM Workout "
            "WHERE sport='Run' AND streams IS NOT NULL AND streams NOT IN ('null', '{}')").fetchall()

    for act in activities:
        scan_activity_against_segments(db, act, segments)

    for seg in segments:
        _refresh_prs(db, seg['id'])

    db.commit()
    log.warning('Segment scan complete — %d activities, %d segments', len(activities), len(segments))
    return len(activities)
