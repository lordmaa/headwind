import json
import re
from flask import Blueprint, abort, redirect, render_template, request, url_for
from database import query_db, get_db
from routes.riders import _compute_badges
from services.ai import PERSONALITIES

bp = Blueprint('rides', __name__)

_MW_KEYS = {
    'Weather Impact™': 'impact',
    'Headwind':         'headwind',
    'Longest Headwind': 'longest_headwind',
    'Air Speed':        'air_speed',
    'Temp':             'temp',
    'Precip':           'precip',
}

def _to_mph(text):
    text = re.sub(r'(\d+\.?\d*)\s*m/s',  lambda m: f'{float(m.group(1)) * 2.23694:.1f} mph', text)
    text = re.sub(r'(\d+\.?\d*)\s*km/h', lambda m: f'{float(m.group(1)) / 1.60934:.1f} mph', text)
    return text

def _parse_mywindsock(description):
    if not description or '-- myWindsock Report --' not in description:
        return None
    data = {}
    in_block = False
    for line in description.replace('\r\n', '\n').replace('\r', '\n').split('\n'):
        line = line.strip()
        if line == '-- myWindsock Report --':
            in_block = True
            continue
        if line == '-- END --':
            break
        if in_block and ':' in line:
            raw_key, _, value = line.partition(':')
            key = _MW_KEYS.get(raw_key.strip())
            if key:
                value = value.strip()
                if key in ('headwind', 'air_speed'):
                    value = _to_mph(value)
                data[key] = value
    return data or None

_SAMPLE = 300  # max chart points


def _sample(data, n=_SAMPLE):
    if not data:
        return []
    step = max(1, len(data) // n)
    return data[::step]


def _stream_series(streams, key, dist_key='distance', transform=None):
    raw  = (streams.get(key) or {}).get('data') or []
    dist = (streams.get(dist_key) or {}).get('data') or []
    if not raw:
        return None
    step = max(1, len(raw) // _SAMPLE)
    values = []
    labels = []
    for i in range(0, len(raw), step):
        v = raw[i]
        values.append(round(transform(v) if transform else v, 2))
        labels.append(round(dist[i] / 1609.344, 2) if i < len(dist) else i)
    return {'values': values, 'labels': labels}


@bp.route('/<rid>')
def detail(rid):
    activity = query_db('''
        SELECT a.*, r.name AS riderName, r.avatarPath AS riderAvatar, r.isDefault AS riderIsDefault
        FROM Activity a
        LEFT JOIN Rider r ON r.id = a.riderId
        WHERE a.id=?
    ''', [rid], one=True)
    if not activity:
        abort(404)

    streams = {}
    if activity['streams']:
        try:
            streams = json.loads(activity['streams'])
        except Exception:
            pass

    # Prefer the raw latlng GPS stream — it's the same source the segment
    # matching algorithm uses, so segment snap coordinates are always consistent.
    # Fall back to the summary polyline for older activities without a stream.
    coords = (streams.get('latlng') or {}).get('data') or []
    if not coords and activity['summaryPolyline']:
        try:
            import polyline as pl
            coords = pl.decode(activity['summaryPolyline'])
        except Exception:
            pass

    charts = {
        'elevation': _stream_series(streams, 'altitude', transform=lambda v: round(v * 3.28084)),
        'heartrate': _stream_series(streams, 'heartrate'),
        'power':     _stream_series(streams, 'watts'),
        'speed':     _stream_series(streams, 'velocity_smooth', transform=lambda v: round(v * 2.23694, 1)),
        'cadence':   _stream_series(streams, 'cadence'),
    }

    memory_count = get_db().execute(
        'SELECT COUNT(*) FROM RideMemory WHERE rideId != ?', [rid]
    ).fetchone()[0]

    # Other riders who rode on the same calendar day
    co_riders = query_db('''
        SELECT DISTINCT r.id, r.name, r.avatarPath
        FROM Activity a
        JOIN Rider r ON r.id = a.riderId
        WHERE date(a.startDateLocal) = date(?)
          AND a.riderId != ?
          AND a.riderId IS NOT NULL
    ''', [activity['startDateLocal'], activity['riderId'] or -1])

    seg_efforts = query_db('''
        SELECT e.*, s.name AS seg_name, s.id AS seg_id, s.distanceM
        FROM SegmentEffort e
        JOIN Segment s ON s.id = e.segmentId
        WHERE e.activityId = ?
        ORDER BY e.elapsedSecs ASC
    ''', [rid])

    # For each segment on this ride, get other riders' efforts on the SAME DAY
    seg_rivals = {}
    if seg_efforts:
        seg_ids = [e['seg_id'] for e in seg_efforts]
        placeholders = ','.join('?' * len(seg_ids))
        rivals = query_db(f'''
            SELECT e.segmentId, e.elapsedSecs, e.avgSpeedMps, e.isPR,
                   r.name AS rider_name, r.avatarPath AS rider_avatar, r.id AS rider_id
            FROM SegmentEffort e
            JOIN Activity a ON a.id = e.activityId
            JOIN Rider r ON r.id = a.riderId
            WHERE e.segmentId IN ({placeholders})
              AND date(a.startDateLocal) = date(?)
              AND a.riderId != ?
              AND a.riderId IS NOT NULL
        ''', seg_ids + [activity['startDateLocal'], activity['riderId'] or -1])
        for r in rivals:
            seg_rivals.setdefault(r['segmentId'], []).append(r)

    seg_trends = {}
    for e in seg_efforts:
        prev = query_db('''
            SELECT e2.elapsedSecs FROM SegmentEffort e2
            JOIN Activity a2 ON a2.id = e2.activityId
            WHERE e2.segmentId=? AND a2.riderId IS ? AND a2.startDateLocal < ? AND e2.activityId != ?
            ORDER BY a2.startDateLocal DESC LIMIT 1
        ''', [e['seg_id'], activity['riderId'], activity['startDateLocal'], rid], one=True)
        if prev:
            delta = e['elapsedSecs'] - prev['elapsedSecs']
            seg_trends[e['seg_id']] = {'dir': 'up' if delta < 0 else ('down' if delta > 0 else 'flat'), 'delta': abs(delta)}

    # Trends for rival riders: compare each rival's today time vs their previous effort
    rival_trends = {}  # {rider_id: {seg_id: {dir, delta}}}
    for seg_id, rivals_list in seg_rivals.items():
        for rival in rivals_list:
            prev = query_db('''
                SELECT e2.elapsedSecs FROM SegmentEffort e2
                JOIN Activity a2 ON a2.id = e2.activityId
                WHERE e2.segmentId=? AND a2.riderId=? AND a2.startDateLocal < ?
                ORDER BY a2.startDateLocal DESC LIMIT 1
            ''', [seg_id, rival['rider_id'], activity['startDateLocal']], one=True)
            if prev:
                delta = rival['elapsedSecs'] - prev['elapsedSecs']
                rival_trends.setdefault(rival['rider_id'], {})[seg_id] = {
                    'dir': 'up' if delta < 0 else ('down' if delta > 0 else 'flat'),
                    'delta': abs(delta),
                }

    prev_ride = query_db('''
        SELECT id, name FROM Activity
        WHERE startDateLocal < ? AND riderId IS ?
        ORDER BY startDateLocal DESC LIMIT 1
    ''', [activity['startDateLocal'], activity['riderId']], one=True)

    next_ride = query_db('''
        SELECT id, name FROM Activity
        WHERE startDateLocal > ? AND riderId IS ?
        ORDER BY startDateLocal ASC LIMIT 1
    ''', [activity['startDateLocal'], activity['riderId']], one=True)

    alt_raw = (streams.get('altitude') or {}).get('data') or []

    elev_loss_ft = 0
    for i in range(1, len(alt_raw)):
        d = alt_raw[i] - alt_raw[i - 1]
        if d < 0:
            elev_loss_ft += -d * 3.28084

    sport = (activity['sportType'] or '').lower()
    is_ride = 'ride' in sport or 'cycling' in sport

    # Badges earned on this specific ride
    ride_badges = []
    if activity['riderId']:
        rtotals = query_db(
            'SELECT COUNT(*) as rides, SUM(distance) as dist, SUM(totalElevationGain) as elev '
            'FROM Activity WHERE riderId=?', [activity['riderId']], one=True)
        if rtotals:
            for cat in _compute_badges(activity['riderId'], rtotals):
                for b in cat['badges']:
                    if b.get('ride_id') and str(b['ride_id']) == str(rid):
                        ride_badges.append(b)

    return render_template('ride.html', activity=activity, coords=coords,
                           charts=charts, has_memory=memory_count > 0,
                           seg_efforts=seg_efforts, alt_raw=alt_raw,
                           co_riders=co_riders, seg_rivals=seg_rivals,
                           seg_trends=seg_trends, rival_trends=rival_trends,
                           elev_loss_ft=int(elev_loss_ft),
                           prev_ride=prev_ride, next_ride=next_ride,
                           is_ride=is_ride,
                           ride_badges=ride_badges,
                           personalities=PERSONALITIES,
                           current_personality=query_db('SELECT coachPersonality FROM Settings WHERE id=1', one=True) or {},
                           mywindsock=_parse_mywindsock(activity['description']))


@bp.route('/<rid>/scan', methods=['POST'])
def scan_segments(rid):
    from services.segments import scan_activity_against_segments, _refresh_prs
    db = get_db()
    activity = db.execute('SELECT id, startDateLocal, streams FROM Activity WHERE id=?', [rid]).fetchone()
    if not activity:
        return ('Not found', 404)
    segments = db.execute('SELECT * FROM Segment').fetchall()
    if not segments:
        return ('{"matched":0}', 200, {'Content-Type': 'application/json'})
    matched = scan_activity_against_segments(db, activity, segments)
    for seg in segments:
        _refresh_prs(db, seg['id'])
    db.commit()
    from flask import jsonify
    return jsonify(matched=matched)


@bp.route('/<rid>/notes', methods=['POST'])
def save_notes(rid):
    notes = (request.form.get('notes') or '').strip() or None
    db = get_db()
    db.execute('UPDATE Activity SET notes=? WHERE id=?', [notes, rid])
    db.commit()
    return ('', 204)


@bp.route('/<rid>/gpx')
def export_gpx(rid):
    from datetime import datetime, timedelta
    from flask import Response

    activity = query_db('SELECT * FROM Activity WHERE id=?', [rid], one=True)
    if not activity:
        abort(404)

    streams = {}
    if activity['streams']:
        try:
            streams = json.loads(activity['streams'])
        except Exception:
            pass

    latlng     = (streams.get('latlng')    or {}).get('data') or []
    altitude   = (streams.get('altitude')  or {}).get('data') or []
    time_data  = (streams.get('time')      or {}).get('data') or []

    if not latlng:
        abort(404)

    try:
        start_dt = datetime.fromisoformat(str(activity['startDateLocal'])[:19])
    except Exception:
        start_dt = datetime.utcnow()

    name_escaped = (activity['name'] or 'Ride').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<gpx version="1.1" creator="BikeTracker" xmlns="http://www.topografix.com/GPX/1/1">',
        '  <trk>',
        f'    <name>{name_escaped}</name>',
        '    <trkseg>',
    ]

    for i, (lat, lng) in enumerate(latlng):
        ele = altitude[i]  if i < len(altitude)  else None
        t   = time_data[i] if i < len(time_data) else None
        lines.append(f'      <trkpt lat="{lat}" lon="{lng}">')
        if ele is not None:
            lines.append(f'        <ele>{ele:.1f}</ele>')
        if t is not None:
            lines.append(f'        <time>{(start_dt + timedelta(seconds=t)).strftime("%Y-%m-%dT%H:%M:%SZ")}</time>')
        lines.append('      </trkpt>')

    lines += ['    </trkseg>', '  </trk>', '</gpx>']

    safe = re.sub(r'[^\w\s-]', '', activity['name'] or 'ride').strip().replace(' ', '_')
    filename = f"{str(activity['startDateLocal'])[:10]}_{safe}.gpx"

    return Response(
        '\n'.join(lines),
        mimetype='application/gpx+xml',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'},
    )


@bp.route('/<rid>/delete', methods=['POST'])
def delete(rid):
    db = get_db()
    db.execute('DELETE FROM Activity WHERE id=?', [rid])
    db.execute('DELETE FROM RideMemory WHERE rideId=?', [rid])
    db.commit()
    return redirect(url_for('dashboard.dashboard'))
