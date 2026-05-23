import csv
import gzip
import io
import json
import logging
import os
import queue
import tempfile
import threading
import traceback
import zipfile
from datetime import datetime

from flask import Blueprint, Response, current_app, render_template, request, stream_with_context

from database import get_db
from services.parser import parse_fit, parse_gpx

log = logging.getLogger(__name__)

bp = Blueprint('import_rides', __name__)


@bp.route('/import')
def index():
    from database import query_db
    riders = query_db('SELECT id, name FROM Rider ORDER BY isDefault DESC, name ASC')
    return render_template('import.html', riders=riders)


@bp.route('/import/upload', methods=['POST'])
def upload():
    f = request.files.get('zipfile')
    if not f or not f.filename:
        return Response('data: {"error":"No file selected"}\n\n', mimetype='text/event-stream')

    from database import query_db as _qdb
    try:
        rider_id = int(request.form.get('riderId') or 0) or None
    except ValueError:
        rider_id = None
    if rider_id is None:
        default = _qdb('SELECT id FROM Rider WHERE isDefault=1 LIMIT 1', one=True)
        rider_id = default['id'] if default else None

    name_lower = f.filename.lower()
    is_single = name_lower.endswith('.fit') or name_lower.endswith('.gpx') \
                or name_lower.endswith('.fit.gz') or name_lower.endswith('.gpx.gz')

    suffix = '.fit' if '.fit' in name_lower else ('.gpx' if '.gpx' in name_lower else '.zip')
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    f.save(tmp)
    tmp.close()

    # Run processing in a background thread so browser disconnect can't kill the import.
    # Events are queued and the SSE generator reads from the queue until done.
    event_queue = queue.Queue(maxsize=200)
    app = current_app._get_current_object()

    def _worker():
        with app.app_context():
            try:
                if is_single:
                    for item in _process_single_file(tmp.name, f.filename, rider_id):
                        event_queue.put(item)
                else:
                    with zipfile.ZipFile(tmp.name) as zf:
                        names = set(zf.namelist())
                        log.warning('Import started — %d files in zip, has_csv=%s',
                                    len(names), 'activities.csv' in names)
                        if 'activities.csv' in names:
                            gen = _process_strava_export(zf, names, rider_id)
                        else:
                            gen = _process_generic_zip(zf, names, rider_id)
                        for item in gen:
                            event_queue.put(item)
            except zipfile.BadZipFile:
                log.error('Import failed: bad zip file')
                event_queue.put(_event({'error': 'Not a valid zip file'}))
            except Exception as e:
                log.error('Import failed: %s\n%s', e, traceback.format_exc())
                event_queue.put(_event({'error': str(e)}))
            finally:
                os.unlink(tmp.name)
                event_queue.put(None)  # sentinel — always signals end

    threading.Thread(target=_worker, daemon=True).start()

    def generate():
        while True:
            try:
                item = event_queue.get(timeout=300)
            except queue.Empty:
                break
            if item is None:
                break
            yield item

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={'X-Accel-Buffering': 'no', 'Cache-Control': 'no-cache'},
    )


def _process_single_file(path, original_name, rider_id=None):
    yield _event({'total': 1, 'source': 'files'})
    db = get_db()
    try:
        with open(path, 'rb') as fh:
            data = fh.read()
        name_lower = original_name.lower()
        if name_lower.endswith('.gz'):
            data = gzip.decompress(data)
        if 'gpx' in name_lower:
            act = parse_gpx(data)
        elif 'fit' in name_lower:
            act = parse_fit(data)
        else:
            yield _event({'error': 'Unsupported file type'})
            return

        if not act or not act.get('startDateLocal'):
            yield _event({'error': 'Could not parse activity from file'})
            return

        if db.execute('SELECT id FROM Activity WHERE id = ?', [act['id']]).fetchone():
            log.warning('Single file import: already exists id=%s', act['id'])
            yield _progress(1, 1, 0, 1, 0)
            yield _event({'complete': True, 'imported': 0, 'skipped': 1, 'errors': 0})
            return

        _insert(db, act, rider_id)
        from services.best_efforts import save_best_efforts
        save_best_efforts(db, act.get('id'), act.get('startDateLocal'), act.get('streams'))
        db.commit()
        log.warning('Single file import: imported id=%s name=%s', act['id'], act.get('name'))

        try:
            from services.segments import scan_activity_against_segments, _refresh_prs
            segments = db.execute('SELECT * FROM Segment').fetchall()
            if segments:
                act_row = db.execute('SELECT * FROM Activity WHERE id=?', [act['id']]).fetchone()
                if act_row:
                    scan_activity_against_segments(db, act_row, segments)
                    for seg in segments:
                        _refresh_prs(db, seg['id'])
                    db.commit()
        except Exception:
            pass

        try:
            from services.mqtt import push_update
            act_row = db.execute('SELECT * FROM Activity WHERE id=?', [act['id']]).fetchone()
            if act_row:
                push_update(act_row)
        except Exception:
            pass
        yield _progress(1, 1, 1, 0, 0)
        yield _event({'complete': True, 'imported': 1, 'skipped': 0, 'errors': 0})
    except Exception as e:
        log.error('Single file import failed: %s\n%s', e, traceback.format_exc())
        yield _progress(1, 1, 0, 0, 1)
        yield _event({'complete': True, 'imported': 0, 'skipped': 0, 'errors': 1})


def _process_strava_export(zf, names, rider_id=None):
    with zf.open('activities.csv') as f:
        rows = list(csv.DictReader(io.TextIOWrapper(f, 'utf-8', errors='replace')))

    total = len(rows)
    yield _event({'total': total, 'source': 'strava'})

    db = get_db()
    imported = skipped = errors = 0

    for i, row in enumerate(rows):
        act_id = str(row.get('Activity ID', '')).strip()
        if not act_id:
            skipped += 1
            yield _progress(i + 1, total, imported, skipped, errors)
            continue

        if db.execute('SELECT id FROM Activity WHERE id = ?', [act_id]).fetchone():
            skipped += 1
            yield _progress(i + 1, total, imported, skipped, errors)
            continue

        act = {}
        filename = row.get('Filename', '').strip()
        if filename and filename in names:
            try:
                data = zf.read(filename)
                if filename.endswith('.gz'):
                    data = gzip.decompress(data)
                if 'gpx' in filename.lower():
                    act = parse_gpx(data) or {}
                elif 'fit' in filename.lower():
                    act = parse_fit(data) or {}
            except Exception:
                pass

        act['id'] = act_id
        act['name'] = row.get('Activity Name', 'Imported Activity').strip() or 'Imported Activity'
        act['sportType'] = row.get('Activity Type', 'Ride').strip() or 'Ride'

        start_dt = _parse_strava_date(row.get('Activity Date', ''))
        if start_dt:
            act['startDateLocal'] = start_dt.isoformat()

        def csv_num(key):
            v = row.get(key, '').strip()
            try:
                return float(v) if v else None
            except ValueError:
                return None

        if not act.get('movingTime'):
            t = csv_num('Moving Time') or csv_num('Elapsed Time')
            if t:
                act['movingTime'] = int(t)
        if not act.get('elapsedTime'):
            t = csv_num('Elapsed Time')
            if t:
                act['elapsedTime'] = int(t)
        if act.get('totalElevationGain') is None:
            act['totalElevationGain'] = csv_num('Elevation Gain') or 0
        if act.get('averageHeartrate') is None:
            act['averageHeartrate'] = csv_num('Average Heart Rate')
        if act.get('averageWatts') is None:
            act['averageWatts'] = csv_num('Average Watts')
        if act.get('calories') is None:
            act['calories'] = csv_num('Calories')
        if not act.get('distance'):
            d = csv_num('Distance')
            if d:
                act['distance'] = d * 1000  # Strava CSV is in km

        try:
            _insert(db, act, rider_id)
            from services.best_efforts import save_best_efforts
            save_best_efforts(db, act.get('id'), act.get('startDateLocal'), act.get('streams'))
            db.commit()
            imported += 1
        except Exception as e:
            log.error('Insert failed for activity %s: %s', act.get('id'), e)
            errors += 1

        yield _progress(i + 1, total, imported, skipped, errors)

    log.warning('Strava import done — imported=%d skipped=%d errors=%d',
                imported, skipped, errors)
    yield _event({'complete': True, 'imported': imported, 'skipped': skipped, 'errors': errors})


def _process_generic_zip(zf, names, rider_id=None):
    activity_files = [
        n for n in names
        if n.lower().endswith(('.gpx', '.fit', '.gpx.gz', '.fit.gz'))
    ]
    total = len(activity_files)
    yield _event({'total': total, 'source': 'files'})

    db = get_db()
    imported = skipped = errors = 0

    for i, name in enumerate(activity_files):
        try:
            data = zf.read(name)
            if name.lower().endswith('.gz'):
                data = gzip.decompress(data)
            act = parse_gpx(data) if 'gpx' in name.lower() else parse_fit(data)
            if act and act.get('startDateLocal'):
                if db.execute('SELECT id FROM Activity WHERE id = ?', [act['id']]).fetchone():
                    log.info('Import skip (duplicate): %s', name)
                    skipped += 1
                else:
                    _insert(db, act, rider_id)
                    from services.best_efforts import save_best_efforts
                    save_best_efforts(db, act.get('id'), act.get('startDateLocal'), act.get('streams'))
                    db.commit()
                    imported += 1
            else:
                log.warning('Import skip (no parseable activity): %s', name)
                skipped += 1
        except Exception as e:
            log.error('Import error for %s: %s\n%s', name, e, traceback.format_exc())
            errors += 1

        yield _progress(i + 1, total, imported, skipped, errors)

    log.warning('Generic import done — imported=%d skipped=%d errors=%d',
                imported, skipped, errors)
    yield _event({'complete': True, 'imported': imported, 'skipped': skipped, 'errors': errors})


def _insert(db, act, rider_id=None):
    sport = act.get('sportType') or 'Ride'
    avg_speed = act.get('averageSpeed') or 0
    result = db.execute('''
        INSERT OR IGNORE INTO Activity
          (id, name, type, sportType, startDate, startDateLocal,
           distance, movingTime, elapsedTime,
           totalElevationGain, averageSpeed, maxSpeed,
           averageHeartrate, averageWatts,
           averageCadence, calories, startLat, startLng, streams,
           rawData, riderId, createdAt, updatedAt)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))
    ''', [
        act.get('id'),            act.get('name'),          sport,
        sport,                    act.get('startDateLocal'), act.get('startDateLocal'),
        act.get('distance') or 0, act.get('movingTime') or 0, act.get('elapsedTime') or 0,
        act.get('totalElevationGain') or 0, avg_speed, avg_speed,
        act.get('averageHeartrate'), act.get('averageWatts'),
        act.get('averageCadence'), act.get('calories'),
        act.get('startLat'),      act.get('startLng'),
        act.get('streams'),       '{}',
        rider_id,
    ])
    if result.rowcount and act.get('startLat') and act.get('startLng'):
        try:
            from services.weather import fetch_weather, save_weather
            w = fetch_weather(act['startLat'], act['startLng'],
                              act.get('startDateLocal'), act.get('streams'))
            if w:
                save_weather(db, act['id'], w)
        except Exception:
            pass


def _event(obj):
    return f'data: {json.dumps(obj)}\n\n'


def _progress(done, total, imported, skipped, errors):
    return _event({'done': done, 'total': total,
                   'imported': imported, 'skipped': skipped, 'errors': errors})


def _parse_strava_date(s):
    for fmt in ('%b %d, %Y, %I:%M:%S %p', '%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S'):
        try:
            return datetime.strptime(s.strip(), fmt)
        except ValueError:
            continue
    return None
