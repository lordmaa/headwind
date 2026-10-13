"""Running stats, derived from the recorded runs themselves: best times for the standard distances, pace, totals, weekly
volume, recent runs and the latest route.

A run lives in one of two tables: Workout (sport 'Run', recorded by the phone app) or Activity (sportType 'Run', e.g. from
a Garmin). Both carry GPS+time streams, so the best-times work is the same sliding-window search the ride best-efforts use,
cached per run in RunEffort so publishing to Home Assistant stays cheap.
"""
import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from database import get_db, query_db
from services.best_efforts import _dist_from_latlng

DISTANCES = [(1000.0, '1 km'), (1609.344, '1 mile'), (3000.0, '3 km'), (5000.0, '5 km'), (10000.0, '10 km'),
             (15000.0, '15 km'), (21097.5, 'Half marathon'), (42195.0, 'Marathon')]
KM_PER_MILE = 1.609344
MIN_RUN_M, MIN_RUN_S = 400, 120          # a 9-second test recording is not a run


def _series(streams_json):
    try:
        s = json.loads(streams_json or '{}')
    except Exception:
        return None, None
    t = (s.get('time') or {}).get('data') or []
    d = (s.get('distance') or {}).get('data') or []
    if not d:
        ll = (s.get('latlng') or {}).get('data') or []
        if ll and t and len(ll) == len(t):
            d = _dist_from_latlng(ll)
    if not d or not t or len(d) != len(t) or len(d) < 2:
        return None, None
    return d, t


def compute_efforts(streams_json):
    """[(distance_m, best_elapsed_secs)] for every standard distance the run is long enough to contain."""
    d, t = _series(streams_json)
    if not d:
        return []
    total = d[-1] - d[0]
    out = []
    for target, _label in DISTANCES:
        if total < target:
            continue
        best, lo = None, 0
        for hi in range(1, len(d)):
            while lo + 1 < hi and d[hi] - d[lo + 1] >= target:
                lo += 1
            if d[hi] - d[lo] >= target:
                e = t[hi] - t[lo]
                if e > 0 and (best is None or e < best):
                    best = e
        if best:
            out.append((target, int(best)))
    return out


def index_run(db, run_id, run_date, streams_json):
    """Cache a run's best efforts (replaces any earlier rows for it). Does not commit."""
    db.execute('DELETE FROM RunEffort WHERE runId=?', [run_id])
    for dist, secs in compute_efforts(streams_json):
        db.execute('INSERT OR REPLACE INTO RunEffort (runId, runDate, distanceM, elapsedSecs) VALUES (?,?,?,?)', [run_id, str(run_date or '')[:10], dist, secs])


def _runs(rid):
    """Every run for the rider, newest first, from both tables."""
    rows = []
    for r in query_db("SELECT id, name, startDateLocal d, distance, movingTime, totalElevationGain gain, averageHeartrate hr, streams, 'workout' kind "
                      "FROM Workout WHERE riderId=? AND sport='Run' AND distance>=? AND movingTime>=?", [rid, MIN_RUN_M, MIN_RUN_S]):
        rows.append(dict(r))
    for r in query_db("SELECT id, name, startDateLocal d, distance, movingTime, totalElevationGain gain, averageHeartrate hr, streams, 'activity' kind "
                      "FROM Activity WHERE riderId=? AND sportType='Run' AND distance>=? AND movingTime>=?", [rid, MIN_RUN_M, MIN_RUN_S]):
        rows.append(dict(r))
    rows.sort(key=lambda r: r['d'] or '', reverse=True)
    return rows


def _ensure_indexed(runs):
    db = get_db()
    have = {r['runId'] for r in db.execute('SELECT DISTINCT runId FROM RunEffort').fetchall()}
    marked = {r['runId'] for r in db.execute("SELECT runId FROM RunEffort WHERE distanceM=0").fetchall()}   # 0 = looked, too short for any distance
    changed = False
    for r in runs:
        if r['id'] in have or r['id'] in marked:
            continue
        index_run(db, r['id'], r['d'], r['streams'])
        if not db.execute('SELECT 1 FROM RunEffort WHERE runId=?', [r['id']]).fetchone():
            db.execute('INSERT OR REPLACE INTO RunEffort (runId, runDate, distanceM, elapsedSecs) VALUES (?,?,0,0)', [r['id'], str(r['d'] or '')[:10]])
        changed = True
    if changed:
        db.commit()


def _thin(points, n):
    pts = [p for p in points if isinstance(p, (list, tuple)) and len(p) >= 2 and p[0] is not None and p[1] is not None]
    if len(pts) > n:
        step = (len(pts) - 1) / (n - 1)
        pts = [pts[round(i * step)] for i in range(n)]
    return [[round(float(p[0]), 5), round(float(p[1]), 5)] for p in pts]


def payloads(rid):
    """{'nutrition_run_stats': {...}, 'nutrition_run_route': {...}} for the rider (empty-but-valid when they have no runs)."""
    runs = _runs(rid)
    s = query_db('SELECT units FROM Settings WHERE id=1', one=True)
    units = (s['units'] if s and s['units'] else 'imperial')
    if not runs:
        return {'nutrition_run_stats': {'runs': 0, 'units': units}, 'nutrition_run_route': {'pts': []}, 'nutrition_run_segments': {'efforts': []}}
    _ensure_indexed(runs)
    today = datetime.now(ZoneInfo('Europe/London')).date()
    mon = today - timedelta(days=today.weekday())
    month0, year0 = today.replace(day=1), today.replace(month=1, day=1)

    def tot(since):
        sel = [r for r in runs if since is None or (r['d'] or '')[:10] >= since.isoformat()]
        return {'n': len(sel), 'm': round(sum(r['distance'] or 0 for r in sel)), 's': int(sum(r['movingTime'] or 0 for r in sel)),
                'gain': round(sum(r['gain'] or 0 for r in sel))}

    weeks = []
    for k in range(11, -1, -1):
        w0 = mon - timedelta(weeks=k)
        w1 = w0 + timedelta(days=7)
        sel = [r for r in runs if w0.isoformat() <= (r['d'] or '')[:10] < w1.isoformat()]
        weeks.append({'d': w0.isoformat(), 'm': round(sum(r['distance'] or 0 for r in sel)), 'n': len(sel)})

    bests = []
    for dist, label in DISTANCES:
        b = query_db('SELECT e.runId, e.runDate, e.elapsedSecs FROM RunEffort e WHERE e.distanceM=? AND e.runId IN (%s) ORDER BY e.elapsedSecs ASC LIMIT 1'
                     % ','.join('?' * len(runs)), [dist] + [r['id'] for r in runs], one=True)
        if b:
            prior = query_db('SELECT MIN(elapsedSecs) m FROM RunEffort WHERE distanceM=? AND runId IN (%s) AND runId<>?' % ','.join('?' * len(runs)),
                             [dist] + [r['id'] for r in runs] + [b['runId']], one=True)
            bests.append({'label': label, 'm': dist, 'secs': b['elapsedSecs'], 'date': b['runDate'], 'run': b['runId'],
                          'next': prior['m'] if prior and prior['m'] else None})

    def card(r):
        return {'id': r['id'], 'name': r['name'] or 'Run', 'date': (r['d'] or '')[:10], 'm': round(r['distance'] or 0), 's': int(r['movingTime'] or 0),
                'gain': round(r['gain'] or 0), 'hr': round(r['hr']) if r['hr'] else None}

    latest = runs[0]
    longest = max(runs, key=lambda r: r['distance'] or 0)
    fastest = max((r for r in runs if (r['distance'] or 0) >= 1000 and r['movingTime']), key=lambda r: (r['distance'] or 0) / r['movingTime'], default=None)
    try:
        ll = (json.loads(latest['streams'] or '{}').get('latlng') or {}).get('data') or []
    except Exception:
        ll = []
    stats = {'runs': len(runs), 'units': units, 'week': tot(mon), 'month': tot(month0), 'year': tot(year0), 'all': tot(None), 'weeks': weeks, 'bests': bests,
             'recent': [card(r) for r in runs[:10]], 'longest': card(longest), 'fastest': card(fastest) if fastest else None}
    efforts, segs = _segment_efforts(rid, latest)
    route = {'id': latest['id'], 'name': latest['name'] or 'Run', 'date': (latest['d'] or '')[:10], 'm': round(latest['distance'] or 0), 'pts': _thin(ll, 140), 'segs': segs}
    seg_payload = {'id': latest['id'], 'name': latest['name'] or 'Run', 'date': (latest['d'] or '')[:10], 'sport': 'run', 'efforts': efforts}
    return {'nutrition_run_stats': stats, 'nutrition_run_route': route, 'nutrition_run_segments': seg_payload}


def _segment_efforts(rid, run):
    """The run segments a run crossed, with time, rank and gap to the runner's best, plus each segment's shape for the map overlay."""
    rows = query_db("""SELECT e.segmentId, e.elapsedSecs, e.isPR, s.name, s.distanceM, s.polyline
                        FROM SegmentEffort e JOIN Segment s ON s.id=e.segmentId WHERE e.activityId=? AND COALESCE(s.sport,'ride')='run' ORDER BY s.name""", [run['id']])
    efforts, segs = [], []
    for e in rows:
        own = query_db("""SELECT e2.elapsedSecs secs, a2.startDateLocal d FROM SegmentEffort e2 JOIN SegActivity a2 ON a2.id=e2.activityId
                          WHERE e2.segmentId=? AND a2.riderId=? ORDER BY a2.startDateLocal""", [e['segmentId'], rid])
        times = [o['secs'] for o in own]
        before = [o['secs'] for o in own if o['d'] < (run['d'] or '')]
        best = min(times) if times else e['elapsedSecs']
        efforts.append({'n': e['name'], 'secs': e['elapsedSecs'], 'm': round(e['distanceM'] or 0), 'pr': bool(e['isPR']), 'best': best,
                        'prev_best': min(before) if before else None, 'last': before[-1] if before else None,
                        'rank': sorted(times).index(e['elapsedSecs']) + 1, 'tries': len(times)})
        try:
            sp = json.loads(e['polyline'] or '[]')
        except Exception:
            sp = []
        if sp:
            segs.append({'n': e['name'], 'pr': bool(e['isPR']), 'pts': _thin(sp, 22)})
    return efforts, segs
