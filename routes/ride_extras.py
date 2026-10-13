"""Ride page extras: rename an activity, photos (shown on the map), mechanical faults. Logic lives in services/ride_extras.py."""
import os

from flask import Blueprint, abort, current_app, jsonify, redirect, request, send_from_directory, url_for

from database import get_db
from services import ride_extras as rx

bp = Blueprint('ride_extras', __name__)
DEMO_MSG = 'Picture uploads are switched off in the demo.'


def _wants_json():
    return request.headers.get('X-Requested-With') == 'fetch' or 'application/json' in request.headers.get('Accept', '')


def _ride_url(rid, anchor=''):
    return url_for('rides.detail', rid=rid) + anchor


def _kind(db, rid):
    if db.execute('SELECT 1 FROM Activity WHERE id=?', [rid]).fetchone():
        return 'ride'
    if db.execute('SELECT 1 FROM Workout WHERE id=?', [rid]).fetchone():
        return 'workout'
    return None


def _err(rid, msg, code=400):
    if _wants_json():
        return jsonify({'ok': False, 'error': str(msg)}), code
    from urllib.parse import quote
    return redirect(_ride_url(rid) + '?error=' + quote(str(msg)))


def _num(v):
    try:
        return float(v) if v not in (None, '') else None
    except ValueError:
        return None


# ---------- rename ----------
@bp.route('/<rid>/rename', methods=['POST'])
def rename(rid):
    name = ' '.join((request.form.get('name') or '').split())[:120]
    db = get_db()
    kind = _kind(db, rid)
    if not kind:
        abort(404)
    if not name:
        return _err(rid, 'give it a name')
    db.execute(f"UPDATE {'Activity' if kind == 'ride' else 'Workout'} SET name=? WHERE id=?", [name, rid])
    db.commit()
    if _wants_json():
        return jsonify({'ok': True, 'name': name})
    return redirect(_ride_url(rid))


# ---------- photos ----------
@bp.route('/photo-file/<name>')
def photo_file(name):
    if not rx.valid_name(name):
        abort(404)
    return send_from_directory(rx.photo_dir(current_app.config['DATABASE']), name, max_age=86400)


@bp.route('/<rid>/photos', methods=['POST'])
def photos_add(rid):
    db = get_db()
    if not _kind(db, rid):
        abort(404)
    if os.environ.get('HEADWIND_DEMO') == '1':
        return _err(rid, DEMO_MSG)
    fault_id = request.form.get('fault', type=int)
    if fault_id and not db.execute('SELECT 1 FROM RideFault WHERE id=? AND rideId=?', [fault_id, rid]).fetchone():
        fault_id = None
    taken_fallback = None
    ms = request.form.get('taken_ms', type=int)             # the browser's File.lastModified: on a phone this is when the picture was taken, even though the browser strips the EXIF
    if ms and 946684800000 < ms < 4102444800000:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        taken_fallback = datetime.fromtimestamp(ms / 1000, ZoneInfo('Europe/London')).replace(tzinfo=None).isoformat(timespec='seconds')
    added, errors = [], []
    for up in request.files.getlist('photo'):
        if not up or not up.filename:
            continue
        try:
            added.append(rx.add_photo(db, rid, up.read(rx.MAX_PHOTO_BYTES + 1), current_app.config['DATABASE'], fault_id, taken_fallback=taken_fallback))
            db.commit()
        except ValueError as e:
            db.rollback()
            errors.append(f'{up.filename}: {e}')
    if _wants_json():
        return jsonify({'ok': bool(added), 'added': added, 'errors': errors}), (200 if added or not errors else 400)
    return redirect(_ride_url(rid, '#photos')) if not errors else _err(rid, '; '.join(errors))


def _photo_or_404(db, pid):
    p = db.execute('SELECT * FROM RidePhoto WHERE id=?', [pid]).fetchone()
    if not p:
        abort(404)
    return p


@bp.route('/photos/<int:pid>/delete', methods=['POST'])
def photo_delete(pid):
    db = get_db()
    p = _photo_or_404(db, pid)
    rx.delete_photo(db, pid, current_app.config['DATABASE'])
    db.commit()
    return jsonify({'ok': True}) if _wants_json() else redirect(_ride_url(p['rideId'], '#photos'))


@bp.route('/photos/<int:pid>/caption', methods=['POST'])
def photo_caption(pid):
    db = get_db()
    _photo_or_404(db, pid)
    db.execute('UPDATE RidePhoto SET caption=? WHERE id=?', [(request.form.get('caption') or '').strip()[:200] or None, pid])
    db.commit()
    return ('', 204)


@bp.route('/photos/<int:pid>/place', methods=['POST'])
def photo_place(pid):
    db = get_db()
    _photo_or_404(db, pid)
    lat, lng = _num(request.form.get('lat')), _num(request.form.get('lng'))
    if (lat is None) != (lng is None) or (lat is not None and not (-90 <= lat <= 90 and -180 <= lng <= 180)):
        return jsonify({'ok': False, 'error': 'bad location'}), 400
    rx.set_photo_place(db, pid, lat, lng)
    db.commit()
    return jsonify({'ok': True})


# ---------- faults ----------
def _fault_fields():
    f = request.form
    return dict(category=f.get('category'), outcome=f.get('outcome'), description=f.get('description'), part_id=f.get('part', type=int), minutes_lost=f.get('minutes_lost'))


@bp.route('/<rid>/faults', methods=['POST'])
def fault_add(rid):
    db = get_db()
    if not db.execute('SELECT 1 FROM Activity WHERE id=?', [rid]).fetchone():
        abort(404)
    try:
        fid = rx.add_fault(db, rid, lat=_num(request.form.get('lat')), lng=_num(request.form.get('lng')), **_fault_fields())
        if os.environ.get('HEADWIND_DEMO') != '1':
            for up in request.files.getlist('photo'):
                if up and up.filename:
                    rx.add_photo(db, rid, up.read(rx.MAX_PHOTO_BYTES + 1), current_app.config['DATABASE'], fid)
        pos = db.execute('SELECT lat, lng FROM RideFault WHERE id=?', [fid]).fetchone()
        if pos['lat'] is None:                                    # no pin was dropped: use where the fault's own photo was taken
            ph = db.execute('SELECT lat, lng FROM RidePhoto WHERE faultId=? AND lat IS NOT NULL ORDER BY id LIMIT 1', [fid]).fetchone()
            if ph:
                db.execute('UPDATE RideFault SET lat=?, lng=? WHERE id=?', [ph['lat'], ph['lng'], fid])
        db.commit()
    except ValueError as e:
        db.rollback()
        return _err(rid, e)
    return redirect(_ride_url(rid, '#faults'))


@bp.route('/faults/<int:fid>/edit', methods=['POST'])
def fault_edit(fid):
    db = get_db()
    f = db.execute('SELECT rideId FROM RideFault WHERE id=?', [fid]).fetchone()
    if not f:
        abort(404)
    try:
        rx.update_fault(db, fid, **_fault_fields())
        db.commit()
    except ValueError as e:
        db.rollback()
        return _err(f['rideId'], e)
    return redirect(_ride_url(f['rideId'], '#faults'))


@bp.route('/faults/<int:fid>/place', methods=['POST'])
def fault_place(fid):
    db = get_db()
    try:
        rx.set_fault_place(db, fid, _num(request.form.get('lat')), _num(request.form.get('lng')))
    except ValueError as e:
        return jsonify({'ok': False, 'error': str(e)}), 400
    db.commit()
    return jsonify({'ok': True})


@bp.route('/faults/<int:fid>/delete', methods=['POST'])
def fault_delete(fid):
    db = get_db()
    f = db.execute('SELECT rideId FROM RideFault WHERE id=?', [fid]).fetchone()
    if not f:
        abort(404)
    rx.delete_fault(db, fid, current_app.config['DATABASE'])
    db.commit()
    return redirect(_ride_url(f['rideId'], '#faults'))


def _back(fallback_rid):
    nxt = request.form.get('next')
    if nxt in ('gear', 'faults'):
        return redirect(url_for('gear.faults_page') if nxt == 'faults' else url_for('gear.index'))
    return redirect(_ride_url(fallback_rid, '#faults'))


@bp.route('/faults/<int:fid>/repaired', methods=['POST'])
def fault_repaired(fid):
    db = get_db()
    f = db.execute('SELECT rideId FROM RideFault WHERE id=?', [fid]).fetchone()
    if not f:
        abort(404)
    try:
        rx.set_repaired(db, fid, True, request.form.get('date') or None)
        db.commit()
    except ValueError as e:
        db.rollback()
        return _err(f['rideId'], e)
    return _back(f['rideId'])


@bp.route('/faults/<int:fid>/reopen', methods=['POST'])
def fault_reopen(fid):
    db = get_db()
    f = db.execute('SELECT rideId FROM RideFault WHERE id=?', [fid]).fetchone()
    if not f:
        abort(404)
    rx.set_repaired(db, fid, False)
    db.commit()
    return _back(f['rideId'])
