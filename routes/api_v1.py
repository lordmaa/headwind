"""Endpoints for the phone app (Bearer device token, see services/device_auth.py) plus the /workouts page.

  GET  /api/v1/ping        who this instance is (the app calls it right after pairing)
  GET  /api/v1/today       everything the app's home screen needs in ONE request (fast start)
  POST /api/v1/workouts    upload a recorded walk/run (multipart: file=GPX, optional sport, name, client_id, steps) — idempotent
  GET  /api/v1/workouts    recent workouts
  DELETE /api/v1/workouts/<id>
  POST /api/v1/rides       upload a recorded cycling ride (multipart: file=GPX) — lands in Activity, not Workout, via the
                            same pipeline as the web's single-file import (best efforts, segment matching, MQTT, weather)
The nutrition endpoints the app also uses (/nutrition/api/log, /water, /search, /lookup/<barcode> ...) accept the same token.
"""
import logging
import os
import tempfile
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from flask import abort, redirect, Blueprint, current_app, jsonify, render_template, request

from database import get_db, query_db

log = logging.getLogger(__name__)
bp = Blueprint('api_v1', __name__, url_prefix='/api/v1')
ui = Blueprint('workouts_ui', __name__)
MAX_GPX = 25 * 1024 * 1024


def _rider():
    return query_db('SELECT id, name FROM Rider WHERE isDefault=1 LIMIT 1', one=True)


def _today():
    return datetime.now(ZoneInfo('Europe/London')).date().isoformat()


def _public(w):
    w = dict(w)
    w.pop('streams', None)
    return w


@bp.route('/ping')
def ping():
    r = _rider()
    s = query_db('SELECT units, calorieAdjustPct FROM Settings WHERE id=1', one=True)
    return jsonify({'ok': True, 'api': 1, 'rider': r['name'] if r else None, 'units': (s['units'] if s else None) or 'imperial',
                    'calorie_adjust_pct': (s['calorieAdjustPct'] if s and s['calorieAdjustPct'] is not None else 0),
                    'server_time': datetime.now(ZoneInfo('Europe/London')).isoformat(timespec='seconds')})


def _weight_kg(rider_id):
    # the phone's live calorie estimate needs kilos regardless of the display unit
    try:
        from services import goal_model
        w = goal_model.current_weight_kg(rider_id)
        return round(w, 1) if w else None
    except Exception:
        return None


@bp.route('/today')
def today():
    from routes import nutrition as nu
    from services import workouts
    from services.mqtt import _best_steps_for
    rider = _rider()
    if not rider:
        return jsonify({'error': 'no rider'}), 409
    d = _today()
    day = nu.day_log(d).get_json()
    recent = nu.recent_foods().get_json()
    favs = nu.list_favourites().get_json()
    g = query_db('SELECT steps FROM GarminDaily WHERE date=?', [d], one=True)
    goal = (query_db('SELECT stepGoal FROM Settings WHERE id=1', one=True) or {'stepGoal': None})['stepGoal'] or 8000
    eaten = {k: round(sum((e.get(k) or 0) for e in day.get('entries', [])), 1) for k in ('calories', 'protein', 'carbs', 'fat', 'fibre')}
    goals = day.get('goals') or {}
    return jsonify({
        'date': d, 'rider': rider['name'],
        'goals': goals,
        'eaten': eaten,
        'remaining': {'calories': round((goals.get('cal') or 0) - eaten['calories']) if goals.get('cal') else None,
                      'protein': round((goals.get('protein') or 0) - eaten['protein']) if goals.get('protein') else None},
        'water_ml': day.get('water_ml', 0), 'water_goal_ml': goals.get('water'),
        'burned': day.get('burned'), 'burn_source': day.get('burn_source'),
        'steps': {'today': _best_steps_for(rider['id'], d, g['steps'] if g else None), 'goal': goal},
        'weight': day.get('weight'),
        'weight_kg': _weight_kg(rider['id']),
        'calorie_adjust_pct': __import__('services.calorie_adjust', fromlist=['get_pct']).get_pct(),   # the phone applies the same % to its own live estimate
        'entries': day.get('entries', []),
        'recent': (recent.get('items') if isinstance(recent, dict) else recent) or [],
        'favourites': favs or [],
        'workouts': [w for w in workouts.recent(rider['id'], 0)],
    })


def _period_start(kind, today):
    if kind == 'week':
        return today - timedelta(days=today.weekday())
    if kind == 'month':
        return today.replace(day=1)
    return today.replace(month=1, day=1)


@bp.route('/overview')
def overview():
    """The web Dashboard's numbers, shaped for the phone: lifetime + week/month/year ride totals, 12-week chart, walks/runs, latest recovery."""
    from routes.dashboard import _weekly_data
    rider = _rider()
    if not rider:
        return jsonify({'error': 'no rider'}), 409
    rid = rider['id']
    today = datetime.now(ZoneInfo('Europe/London')).date()

    def tot(since=None):
        q = 'SELECT COUNT(*) n, SUM(distance) d, SUM(totalElevationGain) e, SUM(movingTime) t, SUM(calories) c FROM Activity WHERE riderId=?'
        a = [rid]
        if since:
            q += ' AND date(startDateLocal)>=?'; a.append(since.isoformat())
        r = query_db(q, a, one=True)
        return {'rides': r['n'] or 0, 'distance_m': round(r['d'] or 0), 'elev_m': round(r['e'] or 0), 'moving_s': int(r['t'] or 0), 'calories': round(r['c'] or 0)}

    def wk(since):
        r = query_db('SELECT COUNT(*) n, SUM(distance) d, SUM(calories) c FROM Workout WHERE riderId=? AND date(startDateLocal)>=?', [rid, since.isoformat()], one=True)
        return {'count': r['n'] or 0, 'distance_m': round(r['d'] or 0), 'calories': round(r['c'] or 0)}

    weekly = [{'label': w['label'], 'distance_m': round(w['mi'] * 1609.344), 'elev_m': round(w['ft'] / 3.28084), 'calories': w['cals'], 'current': w['current']} for w in _weekly_data(rid)]
    rec = query_db('SELECT date, restingHR, hrv, sleepHours, sleepScore, bodyBattery, steps FROM GarminDaily ORDER BY date DESC LIMIT 1', one=True)
    sports = [r['sportType'] for r in query_db('SELECT DISTINCT sportType FROM Activity WHERE riderId=? AND sportType IS NOT NULL ORDER BY sportType', [rid])]
    return jsonify({
        'rider': rider['name'], 'all': tot(),
        'week': tot(_period_start('week', today)), 'month': tot(_period_start('month', today)), 'year': tot(_period_start('year', today)),
        'workouts': {k: wk(_period_start(k, today)) for k in ('week', 'month', 'year')},
        'weekly': weekly, 'sports': sports, 'recovery': dict(rec) if rec else None,
    })


@bp.route('/perform')
def perform():
    """PRs for the Perform tab: all-time fastest at each distance bracket, biggest climbs, and segment PRs.
    Deliberately all-time-only (not the web /data page's year/month grid, which doesn't suit a phone screen) —
    reuses the same BestEffort/Segment/SegmentEffort tables the web page reads, no separate computation."""
    from services.best_efforts import BRACKETS_MI
    rider = _rider()
    if not rider:
        return jsonify({'error': 'no rider'}), 409
    rid = rider['id']

    best_efforts = []
    for dist_mi in BRACKETS_MI:
        e = query_db('''
            SELECT e.elapsedSecs, e.avgSpeedMps, e.activityId, e.activityDate, a.name as rideName
            FROM BestEffort e JOIN Activity a ON a.id = e.activityId
            WHERE a.riderId=? AND e.distanceMi=?
            ORDER BY e.elapsedSecs ASC LIMIT 1
        ''', [rid, dist_mi], one=True)
        if e:
            best_efforts.append({
                'distanceMi': dist_mi, 'secs': e['elapsedSecs'], 'avgSpeedMps': e['avgSpeedMps'] or 0,
                'rideId': e['activityId'], 'rideName': e['rideName'], 'date': e['activityDate'],
            })

    climbs = query_db('''
        SELECT id, name, totalElevationGain, date(startDateLocal) as d
        FROM Activity WHERE riderId=? AND totalElevationGain > 0
        ORDER BY totalElevationGain DESC LIMIT 5
    ''', [rid])
    biggest_climbs = [{'rideId': c['id'], 'rideName': c['name'], 'elevM': round(c['totalElevationGain']), 'date': c['d']} for c in climbs]

    segs = query_db('''
        SELECT s.id, s.name, s.distanceM, s.elevationGainM,
               COUNT(e.id) AS effort_count, MIN(e.elapsedSecs) AS best_secs,
               MAX(CASE WHEN e.isPR=1 THEN e.activityDate END) AS pr_date
        FROM Segment s
        JOIN SegmentEffort e ON e.segmentId = s.id
        JOIN Activity a ON a.id = e.activityId AND a.riderId=?
        GROUP BY s.id
        HAVING best_secs IS NOT NULL
        ORDER BY pr_date DESC
    ''', [rid])
    segment_list = []
    for s in segs:
        kom = query_db('SELECT MIN(e.elapsedSecs) t FROM SegmentEffort e WHERE e.segmentId=?', [s['id']], one=True)
        segment_list.append({
            'id': s['id'], 'name': s['name'], 'distanceM': s['distanceM'], 'elevationGainM': s['elevationGainM'],
            'effortCount': s['effort_count'], 'bestSecs': s['best_secs'], 'prDate': s['pr_date'],
            # global MIN(elapsedSecs) across every rider is always <= the owner's own MIN (their efforts are a subset)
            # — equality means the owner is fastest (or tied), i.e. holds the KOM.
            'isKom': bool(kom and kom['t'] is not None and s['best_secs'] is not None and kom['t'] == s['best_secs']),
        })

    return jsonify({'best_efforts': best_efforts, 'biggest_climbs': biggest_climbs, 'segments': segment_list})


@bp.route('/rides', methods=['POST'])
def upload_ride():
    """Upload a phone-recorded cycling ride as a real Activity (not a Workout) — full treatment: parsed the same
    way as any single-file GPX import (services/parser.parse_gpx), best-efforts scan, segment matching + PR
    refresh, weather, MQTT push. Multipart: file=GPX. Idempotent for free: parse_gpx's activity id is content-
    addressed (sha1 of start time + distance) and _process_single_file also guards near-duplicates (same rider,
    start within 90s, distance within 3%) — no client_id needed, unlike /workouts."""
    from routes.import_rides import _process_single_file
    import json as _json

    rider = _rider()
    if not rider:
        return jsonify({'error': 'no rider'}), 409
    f = request.files.get('file')
    if not f or not f.filename:
        return jsonify({'error': 'file (GPX) is required'}), 400
    data = f.read(MAX_GPX + 1)
    if len(data) > MAX_GPX:
        return jsonify({'error': 'file too large'}), 413

    with tempfile.NamedTemporaryFile(delete=False, suffix='.gpx') as tmp:
        tmp.write(data)
    try:
        result = {'imported': 0, 'skipped': 0, 'errors': 0}
        ride = None
        duplicate = None
        for ev in _process_single_file(tmp.name, f.filename, rider['id']):
            payload = _json.loads(ev[len('data: '):]) if ev.startswith('data: ') else {}
            if payload.get('complete'):
                result = {k: payload.get(k, 0) for k in ('imported', 'skipped', 'errors')}
                ride = payload.get('activity')
                duplicate = payload.get('duplicate')
            elif payload.get('error'):
                return jsonify({'error': payload['error']}), 422
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass

    if result['errors']:
        return jsonify({'error': 'could not parse that recording', **result}), 500

    # imported=0/skipped=1 means it was already uploaded (retry after a dropped connection) — not an error,
    # ride/activity still identifies which one so the app can show "Uploaded X mi" either way.
    return jsonify({'status': 'duplicate' if duplicate else ('imported' if result['imported'] else 'skipped'), **result, 'ride': ride, 'duplicate': duplicate})


@bp.route('/rides')
def rides():
    """Rides (Activity) and phone-recorded walks/runs (Workout) as one newest-first list, paged: ?offset=0&limit=30&sport=Ride&q=text"""
    rider = _rider()
    if not rider:
        return jsonify({'error': 'no rider'}), 409
    rid = rider['id']
    try:
        offset = max(0, int(request.args.get('offset', 0))); limit = min(100, max(1, int(request.args.get('limit', 30))))
    except ValueError:
        return jsonify({'error': 'bad paging'}), 400
    sport = (request.args.get('sport') or '').strip().lower()
    q = (request.args.get('q') or '').strip()
    src = (
        "SELECT 'ride' kind, CAST(id AS TEXT) id, name, sportType sport, startDateLocal, distance, movingTime, totalElevationGain elev, averageSpeed spd, "
        "averageHeartrate hr, calories, city FROM Activity WHERE riderId=? "
        "UNION ALL SELECT 'workout', id, name, sport, startDateLocal, distance, movingTime, totalElevationGain, averageSpeed, averageHeartrate, calories, NULL "
        "FROM Workout WHERE riderId=?")
    where, args = [], [rid, rid]
    if sport:
        where.append('lower(sport)=?'); args.append(sport)
    if q:
        where.append('(name LIKE ? OR city LIKE ?)'); args += [f'%{q}%', f'%{q}%']
    w = (' WHERE ' + ' AND '.join(where)) if where else ''
    total = query_db(f'SELECT COUNT(*) n FROM ({src}){w}', args, one=True)['n']
    rows = query_db(f'SELECT * FROM ({src}){w} ORDER BY startDateLocal DESC LIMIT ? OFFSET ?', args + [limit, offset])
    def preview(kind, rid_):
        # ~28-point route outline for the list thumbnail (stream if there is one, else the summary polyline)
        import json as _json
        try:
            row = query_db('SELECT streams, summaryPolyline FROM Activity WHERE id=?' if kind == 'ride' else 'SELECT streams, NULL summaryPolyline FROM Workout WHERE id=?', [rid_], one=True)
            pts = ((_json.loads(row['streams']) if row and row['streams'] else {}).get('latlng') or {}).get('data') or []
            if not pts and row and row['summaryPolyline']:
                import polyline as pl
                pts = pl.decode(row['summaryPolyline'])
            return [[round(p[0], 5), round(p[1], 5)] for p in _downsample(pts, 28)]
        except Exception:
            return []

    items = [{'kind': r['kind'], 'id': r['id'], 'preview': preview(r['kind'], r['id']), 'name': r['name'], 'sport': r['sport'], 'date': str(r['startDateLocal'])[:19],
              'distance_m': round(r['distance'] or 0), 'moving_s': int(r['movingTime'] or 0), 'elev_m': round(r['elev'] or 0),
              'avg_speed_mps': round(r['spd'] or 0, 2), 'avg_hr': round(r['hr']) if r['hr'] else None,
              'calories': round(r['calories']) if r['calories'] else None, 'city': r['city']} for r in rows]
    return jsonify({'total': total, 'offset': offset, 'items': items})


def _downsample(seq, n):
    if not seq:
        return []
    step = max(1, -(-len(seq) // n))
    out = seq[::step]
    if seq[-1] is not out[-1]:
        out.append(seq[-1])
    return out


@bp.route('/sensors', methods=['POST'])
def post_sensors():
    """A phone's motion-sensor recording (JSON: client_id, fields, rows, mount, device, sensors_only): kept as a sidecar and attached to the ride that overlaps it in time.
    Idempotent per client_id. See services/sensors.py."""
    from services import sensors
    rider = _rider()
    if not rider:
        return jsonify({'error': 'no rider'}), 409
    payload = request.get_json(silent=True)
    db = get_db()
    res, err = sensors.store(db, rider['id'], payload)
    if err:
        return jsonify({'error': err}), 400
    db.commit()
    return jsonify(res)


@bp.route('/live', methods=['POST', 'GET'])
def live():
    """POST: the phone's live position/speed/sensors while it records ({state: riding|idle, lat, lng, speed_mps, distance_m, elapsed_s, alt_m, accuracy_m, sport, battery_pct, sensors: {rough, tilt, press}}).
    Published to Home Assistant by services/live.py. GET: the last reading (for debugging)."""
    from services import live as live_svc
    if request.method == 'GET':
        return jsonify(live_svc.snapshot() or {})
    c = live_svc.update(request.get_json(silent=True))
    return jsonify({'ok': True, 'state': c['state']})


@bp.route('/rides/<kind>/<rid>')
def ride_detail(kind, rid):
    """One ride (Activity) or phone workout: all the stats, a route for the map and small elevation/speed/heart-rate series."""
    import json as _json
    rider = _rider()
    if not rider:
        return jsonify({'error': 'no rider'}), 409
    if kind == 'ride':
        row = query_db('SELECT * FROM Activity WHERE id=? AND riderId=?', [rid, rider['id']], one=True)
    elif kind == 'workout':
        row = query_db('SELECT * FROM Workout WHERE id=? AND riderId=?', [rid, rider['id']], one=True)
    else:
        return jsonify({'error': 'bad kind'}), 400
    if not row:
        return jsonify({'error': 'not found'}), 404
    r = dict(row)
    try:
        streams = _json.loads(r.get('streams') or '{}')
    except Exception:
        streams = {}
    coords = (streams.get('latlng') or {}).get('data') or []
    if not coords and r.get('summaryPolyline'):
        try:
            import polyline as pl
            coords = pl.decode(r['summaryPolyline'])
        except Exception:
            coords = []
    dist = (streams.get('distance') or {}).get('data') or []

    def series(key):
        vals = (streams.get(key) or {}).get('data') or []
        if len(vals) < 5:
            return None
        idx = list(range(len(vals)))
        keep = _downsample(idx, 150)
        return [{'d': round(dist[i]) if i < len(dist) else i, 'v': round(vals[i], 2)} for i in keep]

    def num(k, nd=None):
        v = r.get(k)
        return None if v is None else (round(v, nd) if nd is not None else v)

    dups = []
    if kind == 'ride':
        from services import duplicates
        for d in duplicates.parked_for(get_db(), str(r['id'])):
            try:
                o = _json.loads(d['rowJson'])
            except ValueError:
                continue
            dups.append({'id': d['id'], 'source': d['source'], 'reason': d['reason'], 'date': str(o.get('startDateLocal'))[:19], 'distance_m': round(o.get('distance') or 0),
                         'moving_s': o.get('movingTime'), 'avg_hr': o.get('averageHeartrate'), 'avg_watts': o.get('averageWatts')})

    return jsonify({
        'duplicates': dups,
        'kind': kind, 'id': str(r['id']), 'name': r.get('name'), 'sport': r.get('sportType') or r.get('sport'),
        'date': str(r.get('startDateLocal'))[:19], 'city': r.get('city'), 'source': r.get('source'),
        'distance_m': num('distance', 0), 'moving_s': r.get('movingTime'), 'elapsed_s': r.get('elapsedTime'), 'elev_m': num('totalElevationGain', 0),
        'avg_speed_mps': num('averageSpeed', 2), 'max_speed_mps': num('maxSpeed', 2),
        'avg_hr': num('averageHeartrate', 0), 'max_hr': num('maxHeartrate', 0),
        'avg_watts': num('averageWatts', 0), 'max_watts': num('maxWatts', 0), 'kj': num('kilojoules', 0), 'cadence': num('averageCadence', 0),
        'calories': num('calories', 0), 'steps': r.get('steps'), 'suffer': num('sufferScore', 0),
        'weather': {'summary': r.get('weatherSummary'), 'temp_c': num('weatherTempC', 1), 'wind_kph': num('weatherWindKph', 0), 'rain_mm': num('weatherRainMm', 1)},
        'description': r.get('description'), 'notes': r.get('notes'), 'kudos': r.get('aiKudos'),
        'track': [[round(c[0], 6), round(c[1], 6)] for c in _downsample(coords, 700)],
        'series': {'elevation': series('altitude'), 'speed': series('velocity_smooth'), 'heartrate': series('heartrate'), 'watts': series('watts')},
        'sensors': __import__('services.sensors', fromlist=['for_ride']).for_ride(get_db(), str(r['id'])),
    })


@bp.route('/sync/nutrition')
def sync_nutrition():
    """Everything the phone's local database needs to mirror this rider's nutrition: food + water logs for the last ?days=120,
    per-day burn for the last fortnight, goals, today's steps and weight. The phone merges it (its own unsent edits win)."""
    from routes import nutrition as nu
    from services import goal_model, workouts
    from services.mqtt import _best_steps_for
    rider = _rider()
    if not rider:
        return jsonify({'error': 'no rider'}), 409
    rid = rider['id']
    try:
        days = min(730, max(1, int(request.args.get('days', 120))))
    except ValueError:
        return jsonify({'error': 'bad days'}), 400
    today = datetime.now(ZoneInfo('Europe/London')).date()
    since = (today - timedelta(days=days)).isoformat()
    rows = [dict(r) for r in query_db(
        'SELECT id, logDate, foodName, calories, protein, carbs, fat, fibre, servingG, quantity, barcode, mealType, source, brand, imageUrl, '
        'nutriScoreGrade, createdAt FROM FoodLog WHERE riderId=? AND logDate>=? ORDER BY createdAt ASC, id ASC', [rid, since])]
    created = {r['id']: r['createdAt'] for r in rows}
    entries = nu._annotate_food_rows([dict(r) for r in rows])
    for e in entries:
        e['createdAt'] = created.get(e['id'])
    water = [dict(r) for r in query_db('SELECT id, logDate, ml FROM HydrationLog WHERE riderId=? AND logDate>=? ORDER BY id', [rid, since])]
    meta = {}
    for i in range(0, 15):
        d = (today - timedelta(days=i)).isoformat()
        est = goal_model.day_burn(rid, d)
        if est:
            meta[d] = {'burned': est[0], 'burn_source': est[1]}
    g = query_db('SELECT steps FROM GarminDaily WHERE date=?', [today.isoformat()], one=True)
    goal = (query_db('SELECT stepGoal FROM Settings WHERE id=1', one=True) or {'stepGoal': None})['stepGoal'] or 8000
    return jsonify({
        'server_time': datetime.now(ZoneInfo('Europe/London')).isoformat(timespec='seconds'), 'since': since, 'today': today.isoformat(),
        'entries': entries, 'water': water, 'days': meta, 'goals': nu._get_goals(),
        'steps': {'today': _best_steps_for(rid, today.isoformat(), g['steps'] if g else None), 'goal': goal},
        'weight_kg': _weight_kg(rid), 'workouts': workouts.recent(rid, 30),
        'weights': [{'id': r['id'], 'date': r['logDate'], 'kg': r['weightKg'], 'source': r['source']}
                    for r in query_db('SELECT id, logDate, weightKg, source FROM WeightLog WHERE riderId=? ORDER BY logDate', [rid])],
        'weight_settings': nu._weight_settings(),
        'profile': dict(query_db('SELECT sex, birthYear, heightCm, lossLbPerWeek, stepGoal, goalAuto, rideKcalWeek, rideGoalWeek FROM Settings WHERE id=1', one=True) or {}),
        'steps_days': [{'date': d['date'], 'best': d['best'], 'manual': d['manual']} for d in nu.steps_list().get_json()['days']],
        'saved_meals': nu.list_saved_meals().get_json(),
    })


@bp.route('/workouts', methods=['POST'])
def post_workout():
    from services import workouts
    f = request.files.get('file')
    if not f:
        return jsonify({'error': 'file (GPX) is required'}), 400
    data = f.read(MAX_GPX + 1)
    if len(data) > MAX_GPX:
        return jsonify({'error': 'file too large'}), 413
    rider = _rider()
    if not rider:
        return jsonify({'error': 'no rider'}), 409
    steps = request.form.get('steps')
    try:
        w, created = workouts.save_from_gpx(rider['id'], data, sport=request.form.get('sport'), name=request.form.get('name'),
                                            client_id=(request.form.get('client_id') or '').strip() or None,
                                            steps=int(steps) if steps and steps.isdigit() else None)
    except ValueError as e:
        return jsonify({'error': str(e)}), 422
    if created:
        try:
            from services.mqtt import push_workout, push_update_nutrition
            push_workout(w, rider['name'], (current_app.config.get('APP_URL') or '').rstrip('/') + '/workouts')
            push_update_nutrition()                      # the day's burn changed
        except Exception as e:
            log.warning('workout side effects failed (non-fatal): %s', e)
    return jsonify({'status': 'created' if created else 'exists', 'workout': _public(w)}), (201 if created else 200)


@bp.route('/workouts', methods=['GET'])
def list_workouts():
    rider = _rider()
    from services import workouts
    return jsonify({'workouts': workouts.recent(rider['id'], min(int(request.args.get('days', 30)), 400)) if rider else []})


@bp.route('/workouts/<wid>', methods=['DELETE'])
def delete_workout(wid):
    rider = _rider()
    db = get_db()
    from services import workouts as _wk
    _wk.forget(db, wid)
    n = db.execute('DELETE FROM Workout WHERE id=? AND riderId=?', [wid, rider['id'] if rider else -1]).rowcount
    db.commit()
    if n:
        try:
            from services.mqtt import push_update_nutrition
            push_update_nutrition()
        except Exception:
            pass
    return jsonify({'deleted': bool(n)}), (200 if n else 404)


@bp.route('/activities', methods=['POST'])
def post_activity():
    """Add an activity by hand (JSON): {sport, date 'YYYY-MM-DD', time 'HH:MM', duration_s, distance_m, elev_m?, calories?, name?, notes?, client_id?}.
    Walk/Run/Hike -> Workout, Ride/VirtualRide -> Activity. Idempotent per client_id."""
    from services import manual_activity
    rider = _rider()
    if not rider:
        return jsonify({'error': 'no rider'}), 409
    b = request.get_json(silent=True) or {}
    try:
        res = manual_activity.create(get_db(), rider['id'], b.get('sport'), f"{b.get('date')}T{(b.get('time') or '12:00')[:5]}:00", b.get('duration_s'),
                                     b.get('distance_m'), b.get('elev_m'), b.get('calories'), b.get('name'), b.get('notes'), b.get('client_id'))
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    _refresh_after_change(res['kind'])
    return jsonify({'status': 'created' if res['created'] else 'exists', **res}), (201 if res['created'] else 200)


def _refresh_after_change(kind):
    """Re-publish the Home Assistant sensors after an activity was added or removed (never fatal)."""
    try:
        if kind == 'workout':
            from services.mqtt import push_update_nutrition
            push_update_nutrition()
        else:
            from services.mqtt import push_update
            push_update()
    except Exception as e:
        log.warning('sensor refresh failed (non-fatal): %s', e)


@bp.route('/rides/<kind>/<rid>/sport', methods=['POST'])
def change_sport(kind, rid):
    """Change what an activity is, e.g. a walk that was recorded as a ride: JSON {sport: Ride|VirtualRide|Walk|Run|Hike}. Moves it between the ride and
    workout tables when needed, so the id can change: the response carries the new kind and id."""
    from services import convert
    rider = _rider()
    db = get_db()
    tbl = {'ride': 'Activity', 'workout': 'Workout'}.get(kind)
    row = db.execute(f'SELECT riderId FROM {tbl} WHERE id=?', [rid]).fetchone() if tbl else None
    if not rider or not row or row['riderId'] != rider['id']:
        return jsonify({'error': 'not found'}), 404
    try:
        res = convert.change_sport(db, kind, rid, (request.get_json(silent=True) or {}).get('sport'))
        db.commit()
    except ValueError as e:
        db.rollback()
        return jsonify({'error': str(e)}), 400
    if res['changed']:
        _refresh_after_change('ride'); _refresh_after_change('workout')
    return jsonify(res)


@bp.route('/rides/ride/<rid>/duplicate/<dup_id>/<action>', methods=['POST'])
def resolve_duplicate(rid, dup_id, action):
    """The same ride recorded twice: action = swap (use the other recording) | keep (both are real rides) | discard (delete the other one)."""
    from services import duplicates
    rider = _rider()
    db = get_db()
    d = db.execute("SELECT primaryId, riderId FROM ActivityDuplicate WHERE id=? AND status='parked'", [dup_id]).fetchone()
    if not rider or not d or str(d['primaryId']) != str(rid) or d['riderId'] != rider['id']:
        return jsonify({'error': 'not found'}), 404
    new_main = rid
    if action == 'swap':
        new_main = duplicates.swap(db, dup_id) or rid
    elif action == 'keep':
        duplicates.keep_both(db, dup_id)
    elif action == 'discard':
        duplicates.discard(db, dup_id)
    else:
        return jsonify({'error': 'action must be swap, keep or discard'}), 400
    db.commit()
    _refresh_after_change('ride')
    return jsonify({'ok': True, 'ride_id': new_main})


@bp.route('/rides/<kind>/<rid>', methods=['DELETE'])
def delete_ride(kind, rid):
    """Delete a ride (Activity) or a workout from the phone. A deleted ride is remembered so a Garmin re-sync does not bring it back."""
    from services import duplicates
    rider = _rider()
    db = get_db()
    if kind == 'workout':
        from services import workouts as _wk
        _wk.forget(db, rid)
        n = db.execute('DELETE FROM Workout WHERE id=? AND riderId=?', [rid, rider['id'] if rider else -1]).rowcount
    elif kind == 'ride':
        row = db.execute('SELECT riderId FROM Activity WHERE id=?', [rid]).fetchone()
        n = 1 if (row and rider and row['riderId'] == rider['id'] and duplicates.delete_activity(db, rid)) else 0
        if n:
            db.execute('DELETE FROM RideMemory WHERE rideId=?', [rid])
    else:
        return jsonify({'error': 'kind must be ride or workout'}), 400
    db.commit()
    if n:
        _refresh_after_change(kind)
    return jsonify({'deleted': bool(n)}), (200 if n else 404)


@ui.route('/workouts')
def workouts_page():
    from services import workouts
    rider = _rider()
    rows = workouts.recent(rider['id'], 90) if rider else []
    st = query_db('SELECT units FROM Settings WHERE id=1', one=True)
    return render_template('workouts.html', workouts=rows, units=(st['units'] if st and st['units'] else 'imperial'), today=_today(),
                           now=datetime.now(ZoneInfo('Europe/London')).strftime('%H:%M'), error=request.args.get('error'))


@ui.route('/workouts/new', methods=['POST'])
def workouts_new():
    from services import manual_activity
    rider = _rider()
    f = request.form
    km = f.get('unit') == 'km'
    try:
        dur = (float(f.get('hours') or 0) * 3600) + (float(f.get('minutes') or 0) * 60)
        dist = float(f.get('distance') or 0) * (1000 if km else 1609.344)
        elev = float(f['elevation']) * (1 if km else 0.3048) if f.get('elevation') else None
        manual_activity.create(get_db(), rider['id'], f.get('sport'), f"{f.get('date')}T{(f.get('time') or '12:00')[:5]}:00", dur, dist, elev,
                               f.get('calories') or None, f.get('name'), f.get('notes'))
    except (ValueError, TypeError) as e:
        from urllib.parse import quote
        return redirect('/workouts?error=' + quote(str(e)))
    _refresh_after_change('workout' if f.get('sport') in ('Walk', 'Run', 'Hike') else 'ride')
    return redirect('/workouts')


@ui.route('/workouts/<wid>/sport', methods=['POST'])
def workouts_sport(wid):
    from services import convert
    rider = _rider()
    db = get_db()
    row = db.execute('SELECT riderId FROM Workout WHERE id=?', [wid]).fetchone()
    if not rider or not row or row['riderId'] != rider['id']:
        abort(404)
    try:
        res = convert.change_sport(db, 'workout', wid, request.form.get('sport'))
        db.commit()
    except ValueError as e:
        db.rollback()
        from urllib.parse import quote
        return redirect('/workouts?error=' + quote(str(e)))
    _refresh_after_change('ride'); _refresh_after_change('workout')
    return redirect(url_for('rides.detail', rid=res['id']) if res['kind'] == 'ride' else '/workouts')


@ui.route('/workouts/<wid>/delete', methods=['POST'])
def workouts_delete(wid):
    rider = _rider()
    db = get_db()
    from services import workouts as _wk
    _wk.forget(db, wid)
    db.execute('DELETE FROM Workout WHERE id=? AND riderId=?', [wid, rider['id'] if rider else -1])
    db.commit()
    _refresh_after_change('workout')
    return redirect('/workouts')
