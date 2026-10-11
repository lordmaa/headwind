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


def _workout_as_activity(wid):
    """A Workout row shaped like an Activity row, so ride.html (map, elevation, segments) can show it. None if there is no such workout."""
    w = query_db('''SELECT w.*, r.name AS riderName, r.avatarPath AS riderAvatar, r.isDefault AS riderIsDefault
                    FROM Workout w LEFT JOIN Rider r ON r.id = w.riderId WHERE w.id=?''', [wid], one=True)
    if not w:
        return None
    cols = [c[1] for c in get_db().execute('PRAGMA table_info(Activity)').fetchall()]
    a = {c: None for c in cols}
    a.update(dict(w))
    a.update({'sportType': w['sport'], 'type': w['sport'], 'maxSpeed': w['maxSpeed'] if 'maxSpeed' in w.keys() else None,
              'riderName': w['riderName'], 'riderAvatar': w['riderAvatar'], 'riderIsDefault': w['riderIsDefault'], 'name': w['name'] or w['sport']})
    return a


@bp.route('/<rid>')
def detail(rid):
    activity = query_db('''
        SELECT a.*, r.name AS riderName, r.avatarPath AS riderAvatar, r.isDefault AS riderIsDefault
        FROM Activity a
        LEFT JOIN Rider r ON r.id = a.riderId
        WHERE a.id=?
    ''', [rid], one=True)
    is_workout = False
    if not activity:
        activity = _workout_as_activity(rid)          # phone-recorded runs/walks live in Workout; show them on the same page
        if not activity:
            abort(404)
        is_workout = True

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
    is_run = sport in ('run', 'trailrun', 'virtualrun')

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

    from services import duplicates, gear as gear_svc
    gear_bikes = gear_svc.list_bikes(get_db(), activity['riderId']) if activity['riderId'] and is_ride else []
    dups = []
    for d in duplicates.parked_for(get_db(), rid):
        try:
            r = json.loads(d['rowJson'])
        except ValueError:
            continue
        dups.append({'id': d['id'], 'source': d['source'], 'reason': d['reason'], 'start': r.get('startDateLocal'), 'distance': r.get('distance'),
                     'moving': r.get('movingTime'), 'hr': r.get('averageHeartrate'), 'watts': r.get('averageWatts')})

    from services import sensors as _sensors
    phone_sensors = _sensors.for_ride(get_db(), str(activity['id']))
    return render_template('ride.html', phone_sensors=phone_sensors, activity=activity, coords=coords, dups=dups, gear_bikes=gear_bikes, gear_error=request.args.get('error'), sports=['Ride', 'VirtualRide', 'Walk', 'Run', 'Hike'],
                           charts=charts, has_memory=memory_count > 0,
                           seg_efforts=seg_efforts, alt_raw=alt_raw,
                           co_riders=co_riders, seg_rivals=seg_rivals,
                           seg_trends=seg_trends, rival_trends=rival_trends,
                           elev_loss_ft=int(elev_loss_ft),
                           prev_ride=prev_ride, next_ride=next_ride,
                           is_ride=is_ride, is_run=is_run, is_workout=is_workout,
                           ride_badges=ride_badges,
                           personalities=PERSONALITIES,
                           current_personality=query_db('SELECT coachPersonality FROM Settings WHERE id=1', one=True) or {},
                           mywindsock=_parse_mywindsock(activity['description']))


@bp.route('/<rid>/scan', methods=['POST'])
def scan_segments(rid):
    from services.segments import scan_activity_against_segments, _refresh_prs
    db = get_db()
    activity = (db.execute('SELECT id, startDateLocal, streams, sportType FROM Activity WHERE id=?', [rid]).fetchone()
                or db.execute('SELECT id, startDateLocal, streams, sport AS sportType FROM Workout WHERE id=?', [rid]).fetchone())
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
        '<gpx version="1.1" creator="Headwind" xmlns="http://www.topografix.com/GPX/1/1">',
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
    from services import duplicates
    db = get_db()
    duplicates.delete_activity(db, rid)
    db.execute('DELETE FROM RideMemory WHERE rideId=?', [rid])
    db.commit()
    return redirect(url_for('dashboard.dashboard'))


@bp.route('/<rid>/sport', methods=['POST'])
def change_sport(rid):
    """Change what this activity is (e.g. a walk that was recorded as a ride). A walk/run/hike leaves the rides list and appears under Workouts."""
    from services import convert
    db = get_db()
    try:
        res = convert.change_sport(db, 'ride', rid, request.form.get('sport'))
        db.commit()
    except ValueError as e:
        db.rollback()
        from urllib.parse import quote
        return redirect(url_for('rides.detail', rid=rid) + '?error=' + quote(str(e)))
    return redirect(url_for('workouts_ui.workouts_page') if res['kind'] == 'workout' else url_for('rides.detail', rid=res['id']))


@bp.route('/<rid>/duplicate/<dup_id>/<action>', methods=['POST'])
def resolve_duplicate(rid, dup_id, action):
    """The same ride recorded twice: use the other recording, keep both as separate rides, or discard the other one."""
    from services import duplicates
    db = get_db()
    d = db.execute("SELECT primaryId FROM ActivityDuplicate WHERE id=? AND status='parked'", [dup_id]).fetchone()
    if not d or str(d['primaryId']) != str(rid):
        abort(404)
    new_main = rid
    if action == 'swap':
        new_main = duplicates.swap(db, dup_id) or rid
    elif action == 'keep':
        duplicates.keep_both(db, dup_id)
    elif action == 'discard':
        duplicates.discard(db, dup_id)
    else:
        abort(400)
    db.commit()
    return redirect(url_for('rides.detail', rid=new_main))
