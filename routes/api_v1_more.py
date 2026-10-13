"""More endpoints for the phone app (Bearer device token, same blueprint prefix as routes/api_v1.py): gear, segments, analytics rows, heatmap, road
roughness, planned routes, the ride coach and a ride's weather condition. They wrap the services the web pages already use, in the same JSON shapes the
app's own on-phone versions produce, so the one set of screens works against a server or against the phone alone.

  GET    /api/v1/gear                          bikes (+ health) and how many rides have no bike
  GET    /api/v1/gear/bikes/<id>               one bike with its parts (wear status) and service log
  POST   /api/v1/gear/bikes                    create        PATCH /gear/bikes/<id>   edit      DELETE /gear/bikes/<id>
  POST   /api/v1/gear/bikes/<id>/default | /archive
  GET/PUT/DELETE /api/v1/gear/bikes/<id>/photo   the bike's picture (PUT = raw image bytes; stored resized, EXIF removed)
  POST   /api/v1/gear/bikes/<id>/parts         fit a part    PATCH/DELETE /gear/parts/<id>
  POST   /api/v1/gear/bikes/<id>/service       log work      DELETE /gear/service/<id>
  POST   /api/v1/gear/assign                   put a bike on rides between two dates (dry_run to preview)
  POST   /api/v1/rides/ride/<id>/bike          which bike rode this ride
  GET/PUT /api/v1/food-defaults                 each food's default weight in grams (key = barcode, else n:<lowercase name>); PUT {key, grams|null}
  GET    /api/v1/segments  /  GET /segments/<id>  /  POST /segments (from a ride)  /  PATCH, DELETE /segments/<id>
  GET    /api/v1/analytics/rows                one light row per activity (with weather) for the app's analytics screens
  GET    /api/v1/heatmap?sport=&year=          route lines for the heatmap (cached)
  GET    /api/v1/roughness                     road-roughness cells
  GET/POST/DELETE /api/v1/routes               planner routes
  POST   /api/v1/rides/ride/<id>/coach         the AI ride coach (stores the text as the ride's kudos)
  POST   /api/v1/rides/ride/<id>/condition     set Clear / Cloudy / Fog / Rain / Snow / Storm by hand
"""
import json
import logging
import threading
import time
from datetime import date

from flask import Blueprint, current_app, jsonify, request

from database import get_db, query_db
from services import gear

log = logging.getLogger(__name__)
bp = Blueprint('api_v1_more', __name__, url_prefix='/api/v1')


def _rider():
    return query_db('SELECT id, name FROM Rider WHERE isDefault=1 LIMIT 1', one=True)


def _need_rider():
    r = _rider()
    if not r:
        raise _Err('no rider', 409)
    return r['id']


class _Err(Exception):
    def __init__(self, msg, code=400):
        super().__init__(msg)
        self.msg, self.code = msg, code


@bp.errorhandler(_Err)
def _on_err(e):
    return jsonify({'error': e.msg}), e.code


def _guard(fn):
    """ValueError from the services (validation) becomes a 400 with the message, like the web forms."""
    from functools import wraps

    @wraps(fn)
    def inner(*a, **k):
        try:
            return fn(*a, **k)
        except ValueError as e:
            return jsonify({'error': str(e)}), 400
    return inner


def _num(v):
    if v in (None, ''):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        raise ValueError('that is not a number')


def _int(v):
    n = _num(v)
    return None if n is None else int(n)


# ---------------------------------------------------------------- food default weights
@bp.route('/food-defaults', methods=['GET'])
def food_defaults_get():
    rid = _need_rider()
    rows = get_db().execute('SELECT foodKey, grams FROM FoodDefaultWeight WHERE riderId=?', [rid]).fetchall()
    return jsonify({'defaults': {r[0]: r[1] for r in rows}})


@bp.route('/food-defaults', methods=['PUT'])
@_guard
def food_defaults_put():
    rid = _need_rider()
    d = request.get_json(silent=True) or {}
    key = str(d.get('key') or '').strip()[:200]
    if not key:
        raise ValueError('key is required')
    g = _num(d.get('grams'))
    db = get_db()
    if g is None or g <= 0:
        db.execute('DELETE FROM FoodDefaultWeight WHERE riderId=? AND foodKey=?', [rid, key])
    else:
        db.execute("INSERT INTO FoodDefaultWeight (riderId, foodKey, grams, updatedAt) VALUES (?,?,?,datetime('now')) "
                   "ON CONFLICT(riderId, foodKey) DO UPDATE SET grams=excluded.grams, updatedAt=excluded.updatedAt", [rid, key, min(g, 10000)])
    db.commit()
    return jsonify({'ok': True})


# ---------------------------------------------------------------- gear
def _bike_json(db, b, default_id):
    st = gear.bike_stats(db, b['id'])
    h = gear.bike_health(db, b['id'])
    return {'id': b['id'], 'name': b['name'], 'kind': b['kind'], 'brand': b['brand'], 'model': b['model'], 'year': b['year'], 'bought_on': b['boughtOn'],
            'start_m': b['startMeters'] or 0, 'notes': b['notes'], 'archived': b['archived'] or 0, 'is_default': 1 if default_id == b['id'] else 0,
            'photo': 1 if b['photo'] else 0, 'odometer_m': gear.odometer(db, b), 'rides': st['rides'], 'last': st['last'], 'gain_m': st['gain'], 'moving_s': st['moving'], 'level': h['level'], 'attention': h['attention']}


def _part_json(db, p):
    st = gear.part_status(db, p)
    return {'id': p['id'], 'bike_id': p['bikeId'], 'kind': p['kind'], 'name': p['name'], 'installed_on': p['installedOn'], 'retired_on': p['retiredOn'],
            'initial_m': p['initialMeters'] or 0, 'check_m': p['checkEveryM'], 'replace_m': p['replaceEveryM'], 'check_days': p['checkEveryDays'],
            'replace_days': p['replaceEveryDays'], 'notify': p['notify'], 'cost': p['cost'], 'notes': p['notes'],
            'level': st['level'], 'due': st['due'], 'meters': st['meters'], 'meters_since_check': st['meters_since_check'], 'days': st['days'],
            'days_since_check': st['days_since_check'], 'check_ratio': st['check']['ratio'], 'replace_ratio': st['replace']['ratio'], 'last_check': st['last_check']}


def _default_bike(db, rid):
    r = db.execute('SELECT defaultBikeId FROM Rider WHERE id=?', [rid]).fetchone()
    return r[0] if r else None


def _own_bike(db, rid, bike_id):
    b = gear.get_bike(db, bike_id)
    if not b or b['riderId'] != rid:
        raise _Err('bike not found', 404)
    return b


@bp.route('/gear')
def gear_overview():
    rid = _need_rider()
    db = get_db()
    d = _default_bike(db, rid)
    bikes = [_bike_json(db, b, d) for b in gear.list_bikes(db, rid, include_archived=True)]
    return jsonify({'bikes': bikes, 'unassigned': gear.ride_span(db, rid)['unassigned']})


@bp.route('/gear/bikes/<int:bike_id>')
def gear_bike(bike_id):
    rid = _need_rider()
    db = get_db()
    b = _own_bike(db, rid, bike_id)
    log_rows = [{'id': s['id'], 'bike_id': s['bikeId'], 'part_id': s['partId'], 'date': s['date'], 'action': s['action'], 'odometer_m': s['odometerM'], 'cost': s['cost'],
                 'notes': s['notes'], 'part_name': s['partName']} for s in gear.service_log(db, bike_id)]
    return jsonify({'bike': _bike_json(db, b, _default_bike(db, rid)), 'parts': [_part_json(db, p) for p in gear.part_rows(db, bike_id)], 'log': log_rows})


@bp.route('/gear/bikes', methods=['POST'])
@_guard
def gear_bike_create():
    rid = _need_rider()
    d = request.get_json(silent=True) or {}
    db = get_db()
    bid = gear.create_bike(db, rid, d.get('name'), d.get('kind') or 'road', d.get('brand'), d.get('model'), d.get('year'), d.get('bought_on'), d.get('start_m') or 0, d.get('notes'))
    db.commit()
    return jsonify({'id': bid}), 201


@bp.route('/gear/bikes/<int:bike_id>', methods=['PATCH'])
@_guard
def gear_bike_update(bike_id):
    rid = _need_rider()
    db = get_db()
    _own_bike(db, rid, bike_id)
    d = request.get_json(silent=True) or {}
    f = {}
    for src, dst in (('name', 'name'), ('kind', 'kind'), ('brand', 'brand'), ('model', 'model'), ('year', 'year'), ('start_m', 'startMeters'), ('notes', 'notes'), ('bought_on', 'boughtOn')):
        if src in d:
            f[dst] = d[src]
    gear.update_bike(db, bike_id, **f)
    db.commit()
    return jsonify({'ok': True})


@bp.route('/gear/bikes/<int:bike_id>', methods=['DELETE'])
def gear_bike_delete(bike_id):
    rid = _need_rider()
    db = get_db()
    _own_bike(db, rid, bike_id)
    gear.delete_bike(db, bike_id)
    db.commit()
    return jsonify({'ok': True})


@bp.route('/gear/bikes/<int:bike_id>/photo', methods=['GET'])
def gear_bike_photo_get(bike_id):
    from flask import send_from_directory
    rid = _need_rider()
    db = get_db()
    b = _own_bike(db, rid, bike_id)
    if not b['photo'] or not gear.valid_photo_name(b['photo']):
        return jsonify({'error': 'no photo'}), 404
    return send_from_directory(gear.photo_dir(current_app.config['DATABASE']), b['photo'], max_age=0)


@bp.route('/gear/bikes/<int:bike_id>/photo', methods=['PUT'])
@_guard
def gear_bike_photo_put(bike_id):
    rid = _need_rider()
    db = get_db()
    _own_bike(db, rid, bike_id)
    gear.save_photo(db, bike_id, request.get_data(cache=False)[:gear.MAX_PHOTO_BYTES + 1], current_app.config['DATABASE'])
    db.commit()
    return jsonify({'ok': True})


@bp.route('/gear/bikes/<int:bike_id>/photo', methods=['DELETE'])
def gear_bike_photo_delete(bike_id):
    rid = _need_rider()
    db = get_db()
    _own_bike(db, rid, bike_id)
    gear.remove_photo(db, bike_id, current_app.config['DATABASE'])
    db.commit()
    return jsonify({'ok': True})


@bp.route('/gear/bikes/<int:bike_id>/default', methods=['POST'])
@_guard
def gear_bike_default(bike_id):
    rid = _need_rider()
    db = get_db()
    gear.set_default(db, rid, bike_id)
    db.commit()
    return jsonify({'ok': True})


@bp.route('/gear/bikes/<int:bike_id>/archive', methods=['POST'])
def gear_bike_archive(bike_id):
    rid = _need_rider()
    db = get_db()
    _own_bike(db, rid, bike_id)
    gear.archive_bike(db, bike_id, bool((request.get_json(silent=True) or {}).get('archived', True)))
    db.commit()
    return jsonify({'ok': True})


@bp.route('/gear/bikes/<int:bike_id>/parts', methods=['POST'])
@_guard
def gear_part_add(bike_id):
    rid = _need_rider()
    db = get_db()
    _own_bike(db, rid, bike_id)
    d = request.get_json(silent=True) or {}
    pid = gear.add_part(db, bike_id, d.get('kind') or 'custom', d.get('name'), d.get('installed_on'), d.get('initial_m') or 0, d.get('check_m'), d.get('replace_m'),
                        d.get('check_days'), d.get('replace_days'), d.get('notify', True), d.get('cost'), d.get('notes'))
    db.commit()
    return jsonify({'id': pid}), 201


def _own_part(db, rid, part_id):
    p = db.execute('SELECT p.*, b.riderId FROM Part p JOIN Bike b ON b.id=p.bikeId WHERE p.id=?', [part_id]).fetchone()
    if not p or p['riderId'] != rid:
        raise _Err('part not found', 404)
    return p


@bp.route('/gear/parts/<int:part_id>', methods=['PATCH'])
@_guard
def gear_part_update(part_id):
    rid = _need_rider()
    db = get_db()
    _own_part(db, rid, part_id)
    d = request.get_json(silent=True) or {}
    f = {}
    for src, dst in (('name', 'name'), ('installed_on', 'installedOn'), ('initial_m', 'initialMeters'), ('check_m', 'checkEveryM'), ('replace_m', 'replaceEveryM'),
                     ('check_days', 'checkEveryDays'), ('replace_days', 'replaceEveryDays'), ('notify', 'notify')):
        if src in d:
            f[dst] = d[src]
    gear.update_part(db, part_id, **f)
    db.commit()
    return jsonify({'ok': True})


@bp.route('/gear/parts/<int:part_id>', methods=['DELETE'])
def gear_part_delete(part_id):
    rid = _need_rider()
    db = get_db()
    _own_part(db, rid, part_id)
    db.execute('DELETE FROM PartAlert WHERE partId=?', [part_id])
    db.execute('DELETE FROM ServiceLog WHERE partId=?', [part_id])
    db.execute('DELETE FROM Part WHERE id=?', [part_id])
    db.commit()
    return jsonify({'ok': True})


@bp.route('/gear/bikes/<int:bike_id>/service', methods=['POST'])
@_guard
def gear_service(bike_id):
    rid = _need_rider()
    db = get_db()
    _own_bike(db, rid, bike_id)
    d = request.get_json(silent=True) or {}
    res = gear.log_service(db, bike_id, d.get('action'), d.get('date'), d.get('part_id'), d.get('cost'), d.get('notes'), new_part=({'name': d['new_name']} if d.get('new_name') else None))
    db.commit()
    return jsonify(res), 201


@bp.route('/gear/service/<int:log_id>', methods=['DELETE'])
def gear_service_delete(log_id):
    rid = _need_rider()
    db = get_db()
    s = db.execute('SELECT s.id, b.riderId FROM ServiceLog s JOIN Bike b ON b.id=s.bikeId WHERE s.id=?', [log_id]).fetchone()
    if not s or s['riderId'] != rid:
        raise _Err('not found', 404)
    gear.delete_log(db, log_id)
    db.commit()
    return jsonify({'ok': True})


@bp.route('/gear/assign', methods=['POST'])
@_guard
def gear_assign():
    rid = _need_rider()
    d = request.get_json(silent=True) or {}
    db = get_db()
    res = gear.assign_rides(db, rid, int(d['bike_id']) if d.get('bike_id') is not None else None, date_from=d.get('from'), date_to=d.get('to'),
                            only_unassigned=bool(d.get('only_unassigned', True)), dry_run=bool(d.get('dry_run')))
    if not d.get('dry_run'):
        db.commit()
    return jsonify(res)


@bp.route('/rides/ride/<rid_>/bike', methods=['POST'])
@_guard
def ride_bike(rid_):
    _need_rider()
    d = request.get_json(silent=True) or {}
    db = get_db()
    gear.set_ride_bike(db, rid_, int(d['bike_id']) if d.get('bike_id') is not None else None)
    from services import ride_extras
    ride_extras.resync_ride_bike(db, rid_)
    db.commit()
    return jsonify({'ok': True})


# ---------------------------------------------------------------- segments
def _owner_efforts_sql():
    return ('FROM SegmentEffort e JOIN SegActivity a ON a.id = e.activityId JOIN Rider r ON r.id = a.riderId WHERE r.isDefault = 1')


@bp.route('/segments')
def segments_list():
    _need_rider()
    sport = request.args.get('sport')
    q = 'SELECT id, name, COALESCE(sport, \'ride\') sport, distanceM, elevationGainM FROM Segment'
    args = []
    if sport in ('ride', 'run'):
        q += " WHERE COALESCE(sport,'ride')=?"
        args.append(sport)
    out = []
    for s in query_db(q + ' ORDER BY name COLLATE NOCASE', args):
        n = query_db(f'SELECT COUNT(*) n, MIN(CASE WHEN COALESCE(e.approx,0)=0 THEN e.elapsedSecs END) best {_owner_efforts_sql()} AND e.segmentId=?', [s['id']], one=True)
        if not n['n'] and False:
            continue
        out.append({'id': s['id'], 'name': s['name'], 'sport': s['sport'], 'distance_m': s['distanceM'], 'elev_m': s['elevationGainM'], 'efforts': n['n'], 'best_secs': n['best']})
    return jsonify({'segments': out})


@bp.route('/segments/<int:sid>')
def segment_detail(sid):
    _need_rider()
    s = query_db('SELECT * FROM Segment WHERE id=?', [sid], one=True)
    if not s:
        raise _Err('not found', 404)
    rows = query_db(f'''SELECT e.id, e.elapsedSecs secs, e.avgSpeedMps avg_speed, COALESCE(e.approx,0) approx, e.activityDate date, e.activityId activity_id, e.isPR is_pr,
                              a.name name {_owner_efforts_sql()} AND e.segmentId=? ORDER BY e.activityDate DESC, e.elapsedSecs''', [sid])
    best = min([r['secs'] for r in rows if not r['approx']] or [None], key=lambda x: (x is None, x))
    efforts = []
    seen_best = False
    for r in rows:
        d = dict(r)
        d['is_pr'] = bool(not r['approx'] and r['secs'] == best and not seen_best)
        seen_best = seen_best or d['is_pr']
        efforts.append(d)
    return jsonify({'id': s['id'], 'name': s['name'], 'sport': s['sport'] or 'ride', 'start_lat': s['startLat'], 'start_lng': s['startLng'], 'end_lat': s['endLat'], 'end_lng': s['endLng'],
                    'distance_m': s['distanceM'], 'elev_m': s['elevationGainM'], 'polyline': s['polyline'] or '[]', 'efforts': efforts})


def _scan_in_background(seg_id):
    app = current_app._get_current_object()

    def run():
        try:
            with app.app_context():
                from services.segments import scan_all_activities
                db = get_db()
                scan_all_activities(db, segment_ids=[seg_id])
                db.commit()
        except Exception as e:
            log.warning('segment scan for %s failed: %s', seg_id, e)
    threading.Thread(target=run, daemon=True).start()


@bp.route('/segments', methods=['POST'])
@_guard
def segment_create():
    """Create a segment from a stretch of one of this rider's rides: {name, ride_id, from_frac, to_frac} (fractions of the ride's GPS points). The scan of
    every other ride runs in the background (it can take minutes on a big history); the app polls the segment until its efforts appear."""
    from services.segments import clean_polyline, _path_length_m
    rid = _need_rider()
    d = request.get_json(silent=True) or {}
    ride_id = str(d.get('ride_id') or '')
    row = query_db('SELECT streams, sportType FROM Activity WHERE id=? AND riderId=?', [ride_id, rid], one=True)
    sport = 'ride'
    kind_workout = False
    if not row:
        row = query_db('SELECT streams, sport AS sportType FROM Workout WHERE id=? AND riderId=?', [ride_id, rid], one=True)
        kind_workout = True
    if not row or not row['streams']:
        raise ValueError('that ride has no GPS')
    if kind_workout or (row['sportType'] or '').lower() in ('run', 'trailrun', 'virtualrun'):
        sport = 'run' if (row['sportType'] or '').lower() in ('run', 'trailrun', 'virtualrun') else 'ride'
        if kind_workout and sport != 'run':
            raise ValueError('walks and hikes have no segments')
    st = json.loads(row['streams'])
    ll = (st.get('latlng') or {}).get('data') or []
    alt = (st.get('altitude') or {}).get('data') or []
    if len(ll) < 10:
        raise ValueError('that ride has too little GPS')
    a, b = float(d.get('from_frac', 0.2)), float(d.get('to_frac', 0.6))
    i0, i1 = int(a * (len(ll) - 1)), int(b * (len(ll) - 1))
    if i1 - i0 < 3:
        raise ValueError('that stretch is too short')
    piece = ll[i0:i1 + 1]
    dist = _path_length_m(piece)
    if dist < 100:
        raise ValueError('make the segment at least 100 m long')
    shape = clean_polyline(piece, max_points=400)
    gain = None
    if alt and len(alt) >= i1 + 1:
        g = 0.0
        ref = alt[i0]
        for v in alt[i0:i1 + 1]:
            if v > ref + 3:
                g += v - ref
                ref = v
            elif v < ref - 3:
                ref = v
        gain = round(g, 1)
    name = (d.get('name') or '').strip()[:100] or 'My Segment'
    db = get_db()
    db.execute('INSERT INTO Segment (name, startLat, startLng, endLat, endLng, distanceM, sourceActivityId, polyline, elevationGainM, sport) VALUES (?,?,?,?,?,?,?,?,?,?)',
               [name, piece[0][0], piece[0][1], piece[-1][0], piece[-1][1], dist, ride_id, json.dumps(shape), gain, sport])
    sid = db.execute('SELECT last_insert_rowid()').fetchone()[0]
    db.commit()
    _scan_in_background(sid)
    return jsonify({'id': sid, 'scanning': True}), 201


@bp.route('/segments/<int:sid>', methods=['PATCH'])
def segment_rename(sid):
    _need_rider()
    name = ((request.get_json(silent=True) or {}).get('name') or '').strip()[:100]
    if not name:
        raise _Err('give it a name')
    db = get_db()
    db.execute('UPDATE Segment SET name=? WHERE id=?', [name, sid])
    db.commit()
    return jsonify({'ok': True})


@bp.route('/segments/<int:sid>', methods=['DELETE'])
def segment_delete(sid):
    _need_rider()
    db = get_db()
    db.execute('DELETE FROM SegmentEffort WHERE segmentId=?', [sid])
    db.execute('DELETE FROM Segment WHERE id=?', [sid])
    db.commit()
    return jsonify({'ok': True})


def segments_for_activity(db, act_id):
    """The segments one activity rode, for its page: [{segment_id, name, secs, approx, distance_m, is_pr}]."""
    out = []
    for r in db.execute('''SELECT e.segmentId, e.elapsedSecs secs, COALESCE(e.approx,0) approx, e.isPR, s.name, s.distanceM FROM SegmentEffort e JOIN Segment s ON s.id=e.segmentId
                           WHERE e.activityId=? ORDER BY s.name''', [str(act_id)]).fetchall():
        out.append({'segment_id': r['segmentId'], 'name': r['name'], 'secs': r['secs'], 'approx': r['approx'], 'distance_m': r['distanceM'], 'is_pr': bool(r['isPR'] and not r['approx'])})
    return out


# ---------------------------------------------------------------- analytics, heatmap, roughness
@bp.route('/analytics/rows')
def analytics_rows():
    rid = _need_rider()
    rows = []
    for r in query_db('''SELECT id, sportType, startDateLocal, distance, movingTime, totalElevationGain, averageSpeed, calories, weatherTempC, weatherWindKph, weatherCode, weatherWindRel
                         FROM Activity WHERE riderId=? ORDER BY startDateLocal''', [rid]):
        w = None
        if r['weatherTempC'] is not None or r['weatherWindKph'] is not None or r['weatherCode'] is not None:
            w = json.dumps({'temp_c': r['weatherTempC'], 'wind_kph': r['weatherWindKph'], 'code': r['weatherCode'], 'wind_rel': r['weatherWindRel']})
        rows.append({'id': str(r['id']), 'kind': 'ride', 'sport': r['sportType'], 'date': str(r['startDateLocal'])[:19], 'distance_m': r['distance'], 'moving_s': r['movingTime'],
                     'elev_m': r['totalElevationGain'], 'avg_speed': r['averageSpeed'], 'calories': r['calories'], 'weather': w})
    for r in query_db('SELECT id, sport, startDateLocal, distance, movingTime, calories FROM Workout WHERE riderId=? ORDER BY startDateLocal', [rid]):
        rows.append({'id': str(r['id']), 'kind': 'workout', 'sport': r['sport'], 'date': str(r['startDateLocal'])[:19], 'distance_m': r['distance'], 'moving_s': r['movingTime'],
                     'elev_m': None, 'avg_speed': (r['distance'] / r['movingTime']) if r['distance'] and r['movingTime'] else None, 'calories': r['calories'], 'weather': None})
    return jsonify({'rows': rows})


_HEAT = {}
_HEAT_LOCK = threading.Lock()


@bp.route('/heatmap')
def heatmap():
    """Route lines for the app's heatmap: ~30 points per activity, cached for an hour (building it reads every stream, which is slow on a big history)."""
    _need_rider()
    sport = (request.args.get('sport') or '').strip()
    year = (request.args.get('year') or '').strip()
    key = (sport, year)
    hit = _HEAT.get(key)
    if hit and time.time() - hit[0] < 3600:
        return jsonify({'tracks': hit[1], 'count': len(hit[1])})
    where, args = "WHERE streams IS NOT NULL AND streams != ''", []
    if sport:
        where += ' AND lower(sportType) LIKE ?'
        args.append(f'%{sport.lower()}%')
    if year:
        where += ' AND substr(startDateLocal,1,4)=?'
        args.append(year)
    with _HEAT_LOCK:
        tracks = []
        for row in query_db(f'SELECT streams FROM Activity {where}', args):
            try:
                ll = ((json.loads(row['streams']).get('latlng') or {}).get('data')) or []
                if len(ll) < 2:
                    continue
                step = max(1, len(ll) // 30)
                pts = [[round(p[0], 4), round(p[1], 4)] for p in ll[::step]]
                if len(pts) >= 2:
                    tracks.append(pts)
            except Exception:
                continue
        _HEAT[key] = (time.time(), tracks)
    return jsonify({'tracks': tracks, 'count': len(tracks)})


@bp.route('/roughness')
def roughness():
    from services import roughness as rough
    rid = _need_rider()
    cells = rough.cells(get_db(), rid)
    return jsonify({'cells': [[c['lat'], c['lng'], c['r']] for c in cells]})


# ---------------------------------------------------------------- planner routes
def _route_points(w):
    return [[p['lat'], p['lng']] if isinstance(p, dict) else p for p in w]


@bp.route('/routes', methods=['GET'])
def routes_list():
    _need_rider()
    from routes.route_builder import _total_mi
    out = []
    for r in query_db('SELECT id, name, waypoints, createdAt FROM PlannedRoute ORDER BY createdAt DESC'):
        try:
            w = json.loads(r['waypoints'] or '[]')
            out.append({'id': r['id'], 'name': r['name'], 'distance_m': round(_total_mi(w) * 1609.344), 'points': _route_points(w)})
        except (ValueError, TypeError, KeyError):
            continue
    return jsonify({'routes': out})


@bp.route('/routes', methods=['POST'])
def routes_save():
    _need_rider()
    d = request.get_json(silent=True) or {}
    pts = d.get('points') or []
    if len(pts) < 2:
        raise _Err('need at least 2 points')
    db = get_db()
    cur = db.execute('INSERT INTO PlannedRoute (name, waypoints) VALUES (?, ?)', [(d.get('name') or 'Route').strip()[:100], json.dumps([{'lat': p[0], 'lng': p[1]} for p in pts])])
    db.commit()
    return jsonify({'id': cur.lastrowid}), 201


@bp.route('/routes/<int:route_id>', methods=['DELETE'])
def routes_delete(route_id):
    _need_rider()
    db = get_db()
    db.execute('DELETE FROM PlannedRoute WHERE id=?', [route_id])
    db.commit()
    return jsonify({'ok': True})


# ---------------------------------------------------------------- coach and condition
@bp.route('/rides/ride/<rid_>/coach', methods=['POST'])
def ride_coach(rid_):
    rid = _need_rider()
    activity = query_db('SELECT * FROM Activity WHERE id=? AND riderId=?', [rid_, rid], one=True)
    if not activity:
        raise _Err('not found', 404)
    try:
        from routes.kudos import _ensure_weather
        from services.ai import generate_analysis, AIUnavailable
        from services.context import save_ride_memory
        db = get_db()
        activity = _ensure_weather(activity, db)
        text = generate_analysis(activity)
        db.execute("UPDATE Activity SET aiKudos=?, aiKudosAt=datetime('now') WHERE id=?", [text, rid_])
        db.commit()
        save_ride_memory(activity, text)
        return jsonify({'kudos': text})
    except Exception as e:                                  # AIUnavailable (no key) and provider errors both come back as a readable message
        return jsonify({'error': str(e)}), 503 if e.__class__.__name__ == 'AIUnavailable' else 500


@bp.route('/rides/ride/<rid_>/condition', methods=['POST'])
def ride_condition(rid_):
    from services import weather
    rid = _need_rider()
    label = ((request.get_json(silent=True) or {}).get('label') or '').strip()
    if label not in weather.CATEGORIES:
        raise _Err('pick one of ' + ', '.join(weather.CATEGORIES))
    if not query_db('SELECT 1 FROM Activity WHERE id=? AND riderId=?', [rid_, rid], one=True):
        raise _Err('not found', 404)
    db = get_db()
    weather.set_condition(db, rid_, label)
    db.commit()
    return jsonify({'ok': True})
