"""One newest-first list of rides (Activity) and phone/hand-recorded walks, runs and hikes (Workout), with a tiny route thumbnail.
Shared by the Activities page and the Home page's latest-activity card (the phone app's /api/v1/rides does the same on its side)."""
import json
import math

from database import query_db

PAGE = 30
SPORTS = [('', 'All'), ('ride', 'Rides'), ('run', 'Runs'), ('walk', 'Walks'), ('hike', 'Hikes')]


def _downsample(seq, n):
    if not seq:
        return []
    step = max(1, -(-len(seq) // n))
    out = seq[::step]
    if seq[-1] is not out[-1]:
        out.append(seq[-1])
    return out


def route_path(points, size=100, pad=9):
    """SVG path (in a size x size box) for a list of [lat, lng]; '' when there is no usable track."""
    pts = [p for p in points if p and len(p) >= 2 and p[0] is not None and p[1] is not None]
    if len(pts) < 2:
        return ''
    lat0 = sum(p[0] for p in pts) / len(pts)
    k = math.cos(math.radians(lat0))
    xs = [p[1] * k for p in pts]
    ys = [-p[0] for p in pts]
    w, h = (max(xs) - min(xs)) or 1e-9, (max(ys) - min(ys)) or 1e-9
    s = (size - 2 * pad) / max(w, h)
    ox = (size - w * s) / 2 - min(xs) * s
    oy = (size - h * s) / 2 - min(ys) * s
    return 'M' + ' L'.join(f'{x * s + ox:.1f} {y * s + oy:.1f}' for x, y in zip(xs, ys))


def _points(kind, rid):
    try:
        if kind == 'ride':
            row = query_db('SELECT streams, summaryPolyline FROM Activity WHERE id=?', [rid], one=True)
        else:
            row = query_db('SELECT streams, NULL summaryPolyline FROM Workout WHERE id=?', [rid], one=True)
        pts = ((json.loads(row['streams']) if row and row['streams'] else {}).get('latlng') or {}).get('data') or []
        if not pts and row and row['summaryPolyline']:
            import polyline
            pts = polyline.decode(row['summaryPolyline'])
        return _downsample(pts, 40)
    except Exception:
        return []


_SRC = ("SELECT 'ride' kind, CAST(id AS TEXT) id, name, sportType sport, startDateLocal, distance, movingTime, totalElevationGain elev, averageSpeed spd, "
        "averageHeartrate hr, calories, city FROM Activity WHERE riderId=? "
        "UNION ALL SELECT 'workout', id, name, sport, startDateLocal, distance, movingTime, totalElevationGain, averageSpeed, averageHeartrate, calories, NULL "
        "FROM Workout WHERE riderId=?")


def page(rider_id, sport='', q='', offset=0, limit=PAGE, previews=True):
    where, args = [], [rider_id, rider_id]
    sport = (sport or '').strip().lower()
    if sport == 'ride':
        where.append("lower(sport) LIKE '%ride%' OR lower(sport) LIKE '%cycl%'".join(['(', ')']))
    elif sport:
        where.append('lower(sport) LIKE ?')
        args.append(f'%{sport}%')
    q = (q or '').strip()
    if q:
        where.append('(name LIKE ? OR city LIKE ?)')
        args += [f'%{q}%', f'%{q}%']
    w = (' WHERE ' + ' AND '.join(where)) if where else ''
    total = query_db(f'SELECT COUNT(*) n FROM ({_SRC}){w}', args, one=True)['n']
    rows = query_db(f'SELECT * FROM ({_SRC}){w} ORDER BY startDateLocal DESC LIMIT ? OFFSET ?', args + [limit, offset])
    from database import get_db
    from services import ride_extras
    faulty = ride_extras.fault_ride_ids(get_db(), [r['id'] for r in rows if r['kind'] == 'ride'])
    items = []
    for r in rows:
        d = dict(r)
        d['fault'] = r['id'] in faulty
        d['path'] = route_path(_points(r['kind'], r['id'])) if previews else ''
        items.append(d)
    return {'total': total, 'items': items}
