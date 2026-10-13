"""Phone-app endpoints (Bearer device token) for the ride extras: renaming an activity, ride photos and mechanical faults. Logic lives in services/ride_extras.py.

  POST   /api/v1/rides/<ride|workout>/<id>/name        {name}
  GET    /api/v1/rides/<kind>/<id>/photos              POST (multipart: photo, optional fault, lat, lng, taken_at, caption, client_id; retried client_id returns the first copy)
  GET    /api/v1/ride-photos/<file>                     the picture (or its _t thumbnail)
  PATCH  /api/v1/ride-photos/<id> {caption, lat, lng}   (lat+lng null = no location)        DELETE /api/v1/ride-photos/<id>
  GET    /api/v1/faults/meta                            categories + outcomes (labels the app shows)
  GET    /api/v1/faults?pending=1&ride=<id>             this rider's faults, newest first
  GET    /api/v1/faults/stats?year=                     totals + by type / season / bike / month
  POST   /api/v1/rides/ride/<id>/faults                 {category, outcome, description, part_id, minutes_lost, lat, lng, client_id}
  PATCH  /api/v1/faults/<id>                            same fields          DELETE /api/v1/faults/<id>
  POST   /api/v1/faults/<id>/repaired {date?}           POST /api/v1/faults/<id>/reopen
Faults go to the bike's service log exactly as they do from the web page.
"""
import os

from flask import Blueprint, current_app, jsonify, request, send_from_directory

from database import get_db, query_db
from services import ride_extras as rx

bp = Blueprint('api_v1_extras', __name__, url_prefix='/api/v1')


def _rider():
    return query_db('SELECT id FROM Rider WHERE isDefault=1 LIMIT 1', one=True)


def _bad(msg, code=400):
    return jsonify({'error': str(msg)}), code


def _owned_ride(db, kind, rid):
    """(table, row) when the ride/workout belongs to the owner, else None."""
    rider = _rider()
    if not rider or kind not in ('ride', 'workout'):
        return None
    table = 'Activity' if kind == 'ride' else 'Workout'
    row = db.execute(f'SELECT id, riderId FROM {table} WHERE id=? AND riderId=?', [rid, rider['id']]).fetchone()
    return (table, row) if row else None


def _num(v):
    try:
        return float(v) if v not in (None, '') else None
    except (TypeError, ValueError):
        return None


def _photo(db, pid):
    rider = _rider()
    p = db.execute('SELECT * FROM RidePhoto WHERE id=?', [pid]).fetchone()
    if not p or not rider:
        return None
    ok = db.execute('SELECT 1 FROM Activity WHERE id=? AND riderId=? UNION SELECT 1 FROM Workout WHERE id=? AND riderId=?', [p['rideId'], rider['id'], p['rideId'], rider['id']]).fetchone()
    return p if ok else None


def _fault(db, fid):
    rider = _rider()
    f = db.execute('SELECT * FROM RideFault WHERE id=?', [fid]).fetchone()
    return f if f and rider and f['riderId'] == rider['id'] else None


def _fault_out(db, fid):
    r = db.execute('SELECT f.*, b.name AS bikeName, p.name AS partName FROM RideFault f LEFT JOIN Bike b ON b.id=f.bikeId LEFT JOIN Part p ON p.id=f.partId WHERE f.id=?', [fid]).fetchone()
    return rx.fault_json(dict(r, categoryLabel=rx.category_label(r['category'])))


# ---------- rename ----------
@bp.route('/rides/<kind>/<rid>/name', methods=['POST'])
def rename(kind, rid):
    db = get_db()
    hit = _owned_ride(db, kind, rid)
    if not hit:
        return _bad('not found', 404)
    name = ' '.join(str((request.get_json(silent=True) or request.form).get('name') or '').split())[:120]
    if not name:
        return _bad('name is required')
    db.execute(f'UPDATE {hit[0]} SET name=? WHERE id=?', [name, rid])
    db.commit()
    return jsonify({'ok': True, 'name': name})


# ---------- photos ----------
@bp.route('/rides/<kind>/<rid>/photos', methods=['GET', 'POST'])
def photos(kind, rid):
    db = get_db()
    if not _owned_ride(db, kind, rid):
        return _bad('not found', 404)
    if request.method == 'GET':
        return jsonify({'photos': [rx.photo_json(p) for p in rx.photos_for(db, rid)]})
    up = request.files.get('photo')
    if not up:
        return _bad('photo is required')
    fault_id = request.form.get('fault', type=int)
    if fault_id and not db.execute('SELECT 1 FROM RideFault WHERE id=? AND rideId=?', [fault_id, rid]).fetchone():
        fault_id = None
    try:
        res = rx.add_photo(db, rid, up.read(rx.MAX_PHOTO_BYTES + 1), current_app.config['DATABASE'], fault_id, request.form.get('caption'),
                           _num(request.form.get('lat')), _num(request.form.get('lng')), (request.form.get('taken_at') or '')[:19] or None, (request.form.get('client_id') or '')[:64] or None)
        db.commit()
    except ValueError as e:
        db.rollback()
        return _bad(e, 422)
    p = db.execute('SELECT * FROM RidePhoto WHERE id=?', [res['id']]).fetchone()
    return jsonify({'photo': rx.photo_json(p)}), (200 if res.get('duplicate') else 201)


@bp.route('/ride-photos/<name>')
def photo_file(name):
    if not rx.valid_name(name):
        return _bad('not found', 404)
    return send_from_directory(rx.photo_dir(current_app.config['DATABASE']), name, max_age=86400)


@bp.route('/ride-photos/<int:pid>', methods=['PATCH', 'DELETE'])
def photo_one(pid):
    db = get_db()
    p = _photo(db, pid)
    if not p:
        return _bad('not found', 404)
    if request.method == 'DELETE':
        rx.delete_photo(db, pid, current_app.config['DATABASE'])
        db.commit()
        return jsonify({'ok': True})
    d = request.get_json(silent=True) or {}
    if 'caption' in d:
        db.execute('UPDATE RidePhoto SET caption=? WHERE id=?', [(d.get('caption') or '').strip()[:200] or None, pid])
    if 'lat' in d or 'lng' in d:
        lat, lng = _num(d.get('lat')), _num(d.get('lng'))
        if (lat is None) != (lng is None) or (lat is not None and not (-90 <= lat <= 90 and -180 <= lng <= 180)):
            return _bad('bad location')
        rx.set_photo_place(db, pid, lat, lng)
    db.commit()
    return jsonify({'photo': rx.photo_json(db.execute('SELECT * FROM RidePhoto WHERE id=?', [pid]).fetchone())})


# ---------- faults ----------
@bp.route('/faults/meta')
def fault_meta():
    return jsonify({'categories': [{'key': k, 'label': l} for k, l in rx.CATEGORIES], 'outcomes': [{'key': k, 'label': l} for k, l in rx.OUTCOMES]})


@bp.route('/faults')
def fault_list():
    rider = _rider()
    if not rider:
        return _bad('no rider', 409)
    q, a = ('SELECT f.id FROM RideFault f WHERE f.riderId=?'), [rider['id']]
    if request.args.get('pending') in ('1', 'true'):
        q += ' AND f.repairedOn IS NULL'
    if request.args.get('ride'):
        q += ' AND f.rideId=?'
        a.append(request.args['ride'])
    db = get_db()
    ids = [r[0] for r in db.execute(q + ' ORDER BY f.date DESC, f.id DESC LIMIT 500', a).fetchall()]
    return jsonify({'faults': [_fault_out(db, i) for i in ids]})


@bp.route('/faults/stats')
def fault_stats():
    rider = _rider()
    if not rider:
        return _bad('no rider', 409)
    st = rx.stats(get_db(), rider['id'], request.args.get('year', type=int))
    return jsonify({'total': st['total'], 'pending': st['pending'], 'rides_with_fault': st['rides_with'], 'rides': st['rides'], 'distance_m': st['metres'], 'minutes_lost': st['minutes_lost'],
                    'by_category': [{'key': k, 'label': l, 'count': n} for k, l, n in st['by_category']],
                    'by_season': [{'season': s, 'count': n, 'rides': r} for s, n, r in st['by_season']], 'by_bike': [{'bike': b, 'count': n} for b, n in st['by_bike']],
                    'by_month': [{'month': m, 'count': n} for m, n in st['by_month']], 'years': st['years']})


def _fields(d):
    return dict(category=d.get('category'), outcome=d.get('outcome'), description=d.get('description'), part_id=d.get('part_id'), minutes_lost=d.get('minutes_lost'))


@bp.route('/rides/ride/<rid>/faults', methods=['POST'])
def fault_add(rid):
    db = get_db()
    if not _owned_ride(db, 'ride', rid):
        return _bad('not found', 404)
    d = request.get_json(silent=True) or {}
    try:
        fid = rx.add_fault(db, rid, lat=_num(d.get('lat')), lng=_num(d.get('lng')), client_id=(str(d.get('client_id') or '')[:64] or None), **_fields(d))
        db.commit()
    except ValueError as e:
        db.rollback()
        return _bad(e, 422)
    return jsonify({'fault': _fault_out(db, fid)}), 201


@bp.route('/faults/<int:fid>', methods=['PATCH', 'DELETE'])
def fault_one(fid):
    db = get_db()
    f = _fault(db, fid)
    if not f:
        return _bad('not found', 404)
    if request.method == 'DELETE':
        rx.delete_fault(db, fid, current_app.config['DATABASE'])
        db.commit()
        return jsonify({'ok': True})
    d = request.get_json(silent=True) or {}
    cur = {'category': f['category'], 'outcome': f['outcome'], 'description': f['description'], 'part_id': f['partId'], 'minutes_lost': f['minutesLost']}
    cur.update({k: v for k, v in _fields(d).items() if k in d})
    try:
        rx.update_fault(db, fid, **cur)
        if 'lat' in d or 'lng' in d:
            rx.set_fault_place(db, fid, _num(d.get('lat')), _num(d.get('lng')))
        db.commit()
    except ValueError as e:
        db.rollback()
        return _bad(e, 422)
    return jsonify({'fault': _fault_out(db, fid)})


@bp.route('/faults/<int:fid>/repaired', methods=['POST'])
def fault_repaired(fid):
    db = get_db()
    if not _fault(db, fid):
        return _bad('not found', 404)
    try:
        rx.set_repaired(db, fid, True, (request.get_json(silent=True) or {}).get('date'))
        db.commit()
    except ValueError as e:
        db.rollback()
        return _bad(e, 422)
    return jsonify({'fault': _fault_out(db, fid)})


@bp.route('/faults/<int:fid>/reopen', methods=['POST'])
def fault_reopen(fid):
    db = get_db()
    if not _fault(db, fid):
        return _bad('not found', 404)
    rx.set_repaired(db, fid, False)
    db.commit()
    return jsonify({'fault': _fault_out(db, fid)})
