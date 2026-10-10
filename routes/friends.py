import hmac
import json
import logging
import secrets
import threading
import time
import urllib.parse
import urllib.request
import urllib.error

from flask import Blueprint, Response, abort, jsonify, redirect, render_template, request, stream_with_context, url_for

log = logging.getLogger(__name__)

from database import get_db, query_db
from services.segments import scan_activity_against_segments, _refresh_prs
from services.limits import bounded_lines
from services.peer_http import open_peer

bp = Blueprint('friends', __name__)

BATCH = 50


@bp.route('/api/riders')
def riders_list():
    """Token-authenticated rider list — lets a friend's instance discover who's here."""
    token = request.headers.get('X-Feed-Token', '') or request.args.get('token', '')
    settings = query_db('SELECT feedToken FROM Settings WHERE id=1', one=True)
    if not settings or not settings['feedToken'] or token != settings['feedToken']:
        abort(403)
    riders = query_db('SELECT id, name, isDefault FROM Rider ORDER BY isDefault DESC, name ASC')
    return jsonify([dict(r) for r in riders])


def _feed_token_ok():
    token = request.headers.get('X-Feed-Token', '') or request.args.get('token', '')
    settings = query_db('SELECT feedToken FROM Settings WHERE id=1', one=True)
    return bool(settings and settings['feedToken'] and hmac.compare_digest(str(settings['feedToken']), token))


@bp.route('/api/foods-feed')
def foods_feed():
    """Token-authenticated NDJSON of this instance's own user-made foods (see services/shared_foods.py)."""
    if not _feed_token_ok():
        abort(403)
    from services import shared_foods

    def generate():
        owner = query_db('SELECT name FROM Rider WHERE isDefault=1 LIMIT 1', one=True)
        yield json.dumps({'type': 'meta', 'name': owner['name'] if owner else 'Unknown'}) + '\n'
        n = 0
        for rec in shared_foods.feed_records():
            n += 1
            yield json.dumps(rec) + '\n'
        yield json.dumps({'type': 'end', 'count': n}) + '\n'      # lets the receiver know the feed arrived whole

    return Response(stream_with_context(generate()), mimetype='application/x-ndjson')


@bp.route('/api/food-image/<ref>')
def food_image(ref):
    """A photo referenced by /api/foods-feed ('<sha1>.<ext>')."""
    if not _feed_token_ok():
        abort(403)
    from services import food_cache
    food_cache.init_path()
    hit = food_cache.local_image('local:' + ref)
    if not hit:
        abort(404)
    return Response(hit[0], mimetype=hit[1], headers={'Cache-Control': 'public, max-age=86400'})


@bp.route('/api/feed')
def feed():
    """Token-authenticated NDJSON stream — no session required.
    Each line is a JSON object with a 'type' field: meta | segment | ride.
    Accepts ?since=YYYY-MM-DD and ?rider=<name> to filter.
    """
    token = request.headers.get('X-Feed-Token', '') or request.args.get('token', '')
    settings = query_db('SELECT feedToken FROM Settings WHERE id=1', one=True)
    if not settings or not settings['feedToken'] or token != settings['feedToken']:
        abort(403)

    since = request.args.get('since')
    rider_name = request.args.get('rider')

    def generate():
        from database import get_db as _get_db
        db = _get_db()

        if rider_name:
            rider = db.execute('SELECT * FROM Rider WHERE name=? COLLATE NOCASE', [rider_name]).fetchone()
        else:
            rider = db.execute('SELECT * FROM Rider WHERE isDefault=1').fetchone()
        yield json.dumps({'type': 'meta', 'name': rider['name'] if rider else 'Unknown'}) + '\n'

        for s in db.execute('SELECT id, name, startLat, startLng, endLat, endLng, '
                            "distanceM, elevationGainM, polyline, COALESCE(sport, 'ride') AS sport FROM Segment WHERE friendId IS NULL"):
            yield json.dumps({'type': 'segment', **dict(s)}) + '\n'

        owner_id = rider['id'] if rider else None
        if owner_id:
            if since:
                cur = db.execute('''
                    SELECT id, name, type, sportType, startDate, startDateLocal, distance, movingTime,
                           elapsedTime, totalElevationGain, averageSpeed, maxSpeed,
                           averageHeartrate, maxHeartrate, averageWatts, weightedAvgWatts,
                           averageCadence, calories, startLat, startLng, streams,
                           weatherTempC, weatherWindKph, weatherGustKph, weatherWindDir,
                           weatherHumidity, weatherRainMm, weatherCode, weatherSummary, weatherWindRel
                    FROM Activity WHERE riderId=? AND startDateLocal > ? ORDER BY startDateLocal DESC
                ''', [owner_id, since])
            else:
                cur = db.execute('''
                    SELECT id, name, type, sportType, startDate, startDateLocal, distance, movingTime,
                           elapsedTime, totalElevationGain, averageSpeed, maxSpeed,
                           averageHeartrate, maxHeartrate, averageWatts, weightedAvgWatts,
                           averageCadence, calories, startLat, startLng, streams,
                           weatherTempC, weatherWindKph, weatherGustKph, weatherWindDir,
                           weatherHumidity, weatherRainMm, weatherCode, weatherSummary, weatherWindRel
                    FROM Activity WHERE riderId=? ORDER BY startDateLocal DESC
                ''', [owner_id])
            for row in cur:
                d = dict(row)
                d.pop('type', None)  # avoid collision with the NDJSON routing key
                yield json.dumps({'type': 'ride', **d}) + '\n'

    return Response(stream_with_context(generate()), mimetype='application/x-ndjson')


@bp.route('/friends')
def index():
    settings = query_db('SELECT feedToken, friendSyncInterval FROM Settings WHERE id=1', one=True)
    feed_token = settings['feedToken'] if settings else None
    sync_interval = settings['friendSyncInterval'] if settings and settings['friendSyncInterval'] is not None else 15

    if not feed_token:
        db = get_db()
        feed_token = secrets.token_urlsafe(24)
        db.execute('INSERT OR IGNORE INTO Settings (id) VALUES (1)')
        db.execute('UPDATE Settings SET feedToken=? WHERE id=1', [feed_token])
        db.commit()

    friends = query_db('''
        SELECT f.*, r.name AS rider_name, r.avatarPath AS rider_avatar
        FROM Friend f
        LEFT JOIN Rider r ON r.id = f.riderId
        ORDER BY f.name
    ''')

    return render_template('friends.html', friends=friends, feed_token=feed_token,
                           sync_interval=sync_interval)


@bp.route('/friends/sync-interval', methods=['POST'])
def set_sync_interval():
    try:
        interval = int(request.form.get('interval', 15))
    except ValueError:
        interval = 15
    db = get_db()
    db.execute('UPDATE Settings SET friendSyncInterval=? WHERE id=1', [interval])
    db.commit()
    return redirect(url_for('friends.index'))


@bp.route('/friends/regenerate-token', methods=['POST'])
def regenerate_token():
    db = get_db()
    db.execute('INSERT OR IGNORE INTO Settings (id) VALUES (1)')
    db.execute('UPDATE Settings SET feedToken=? WHERE id=1', [secrets.token_urlsafe(24)])
    db.commit()
    return redirect(url_for('friends.index'))


@bp.route('/friends/probe', methods=['POST'])
def probe():
    """Fetch the rider list from a remote instance to populate the Add Friend dropdown."""
    data  = request.get_json(silent=True) or {}
    url   = (data.get('url')   or '').strip().rstrip('/')
    token = (data.get('token') or '').strip()
    if not url:
        return jsonify({'error': 'URL required'}), 400

    probe_url = url + '/api/riders'
    headers   = {'User-Agent': 'Headwind/1.0'}
    if token:
        headers['X-Feed-Token'] = token

    try:
        resp = open_peer(probe_url, headers, timeout=10)
        riders = json.loads(resp.read())
        return jsonify({'riders': riders})
    except urllib.error.HTTPError as e:
        if e.code == 403:
            return jsonify({'error': 'Invalid token — check the feed token and try again'})
        if e.code == 404:
            # Older instance without /api/riders — return a synthetic default entry
            return jsonify({'riders': [{'id': None, 'name': 'Default rider', 'isDefault': 1}]})
        return jsonify({'error': f'HTTP {e.code} from remote instance'})
    except Exception as e:
        return jsonify({'error': f'Could not connect: {e}'})


@bp.route('/friends/add', methods=['POST'])
def add():
    name       = (request.form.get('name')       or '').strip()
    url        = (request.form.get('url')        or '').strip().rstrip('/')
    token      = (request.form.get('token')      or '').strip()
    rider_name = (request.form.get('riderName')  or '').strip() or None

    if not name or not url:
        return redirect(url_for('friends.index'))

    db = get_db()
    db.execute('INSERT INTO Friend (name, url, token, riderName) VALUES (?,?,?,?)',
               [name, url, token, rider_name])
    db.commit()
    fid = db.execute('SELECT last_insert_rowid()').fetchone()[0]

    # Large initial syncs can take minutes — run in background so the browser doesn't hang
    from flask import current_app
    app = current_app._get_current_object()
    threading.Thread(target=_bg_sync, args=(app, fid), daemon=True).start()
    return redirect(url_for('friends.index'))


def _bg_sync(app, friend_id):
    with app.app_context():
        _do_sync(friend_id)


@bp.route('/friends/<int:fid>/sync', methods=['POST'])
def sync(fid):
    count, err = _do_sync(fid)
    return jsonify({'ok': err is None, 'synced': count, 'error': err})


@bp.route('/friends/<int:fid>/delete', methods=['POST'])
def delete(fid):
    db = get_db()
    friend = query_db('SELECT riderId FROM Friend WHERE id=?', [fid], one=True)
    if friend and friend['riderId']:
        db.execute('DELETE FROM SegmentEffort WHERE activityId IN '
                   '(SELECT id FROM Activity WHERE riderId=?)', [friend['riderId']])
        db.execute('DELETE FROM BestEffort WHERE activityId IN (SELECT id FROM Activity WHERE riderId=?)', [friend['riderId']])
        db.execute('DELETE FROM RideMemory WHERE rideId IN (SELECT id FROM Activity WHERE riderId=?)', [friend['riderId']])
        db.execute('DELETE FROM Activity WHERE riderId=?', [friend['riderId']])
        db.execute('DELETE FROM Rider WHERE id=?', [friend['riderId']])
    # Segments imported from this friend cascade-delete their efforts via ON DELETE CASCADE
    db.execute('DELETE FROM Segment WHERE friendId=?', [fid])
    db.execute('DELETE FROM Friend WHERE id=?', [fid])
    db.commit()

    # Refresh PRs for remaining segments
    for seg in query_db('SELECT id FROM Segment'):
        _refresh_prs(db, seg['id'])
    db.commit()

    return redirect(url_for('friends.index'))


def _do_sync(friend_id):
    """Rides + segments, then the friend's shared foods. A foods problem never fails the ride sync."""
    result = _do_ride_sync(friend_id)
    try:
        from services import shared_foods
        friend = query_db('SELECT * FROM Friend WHERE id=?', [friend_id], one=True)
        if friend:
            n, err = shared_foods.sync_friend(friend)
            if err:
                log.info('Shared foods sync for "%s": %s', friend['name'], err)
    except Exception as e:
        log.warning('Shared foods sync failed for friend %s: %s', friend_id, e)
    return result


def _do_ride_sync(friend_id):
    friend = query_db('SELECT * FROM Friend WHERE id=?', [friend_id], one=True)
    if not friend:
        return 0, 'Friend not found'

    is_incremental = bool(friend['lastSynced'])
    feed_url = friend['url'].rstrip('/') + '/api/feed'
    params = []
    if friend['riderName']:
        params.append('rider=' + urllib.parse.quote(friend['riderName']))
    if is_incremental:
        # Trim to date only so we don't miss rides added on the same day as last sync
        since = friend['lastSynced'][:10]
        params.append(f'since={since}')
    if params:
        feed_url += '?' + '&'.join(params)

    headers  = {'User-Agent': 'Headwind/1.0'}
    if friend['token']:
        headers['X-Feed-Token'] = friend['token']

    log.info('Friends sync: streaming feed for "%s" from %s', friend['name'], feed_url)
    try:
        resp = open_peer(feed_url, headers, timeout=60)
    except urllib.error.HTTPError as e:
        log.warning('Friends sync: HTTP %s from %s', e.code, feed_url)
        return 0, f'HTTP {e.code} from {feed_url}'
    except Exception as e:
        log.warning('Friends sync: connect failed for "%s": %s', friend['name'], e)
        return 0, str(e)

    db   = get_db()
    name = friend['name']
    rider_id = friend['riderId']
    synced = 0
    remote_seg_count = 0
    imported_seg_ids = []
    new_ride_ids = []  # local IDs of rides received this sync (for incremental segment scan)

    try:
        for raw_line in bounded_lines(resp, 500 * 1024 ** 2, 2_000_000, 900):
            line = raw_line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line.decode())
            except Exception:
                continue

            t = obj.get('type')

            if t == 'meta':
                name = obj.get('name') or friend['name']
                if not rider_id:
                    db.execute('INSERT INTO Rider (name, isDefault) VALUES (?, 0)', [name])
                    rider_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]
                    db.execute('UPDATE Friend SET riderId=? WHERE id=?', [rider_id, friend_id])
                    log.info('Friends sync: created rider "%s" (id=%s)', name, rider_id)
                else:
                    db.execute('UPDATE Rider SET name=? WHERE id=?', [name, rider_id])

            elif t == 'segment':
                from services.segments import clean_polyline, valid_coord
                _sc = valid_coord(obj.get('startLat'), obj.get('startLng'))
                _ec = valid_coord(obj.get('endLat'), obj.get('endLng'))
                if not _sc or not _ec:
                    continue                       # a friend's segment with impossible coordinates is ignored
                _shape = clean_polyline(obj.get('polyline'))
                _poly = json.dumps(_shape) if _shape else None
                existing = db.execute(
                    'SELECT id FROM Segment WHERE friendId=? AND sourceSegId=?',
                    [friend_id, obj['id']]
                ).fetchone()
                if existing:
                    db.execute('''UPDATE Segment SET name=?, startLat=?, startLng=?, endLat=?, endLng=?,
                                      distanceM=?, elevationGainM=?, polyline=?, sport=? WHERE id=?''', [
                        obj.get('name'), _sc[0], _sc[1], _ec[0], _ec[1],
                        obj.get('distanceM'), obj.get('elevationGainM'), _poly, 'run' if obj.get('sport') == 'run' else 'ride',
                        existing[0],
                    ])
                    imported_seg_ids.append(existing[0])
                else:
                    db.execute('''INSERT INTO Segment (name, startLat, startLng, endLat, endLng,
                                      distanceM, elevationGainM, polyline, friendId, sourceSegId, sport)
                                  VALUES (?,?,?,?,?,?,?,?,?,?,?)''', [
                        obj.get('name'), _sc[0], _sc[1], _ec[0], _ec[1],
                        obj.get('distanceM'), obj.get('elevationGainM'), _poly,
                        friend_id, obj['id'], 'run' if obj.get('sport') == 'run' else 'ride',
                    ])
                    imported_seg_ids.append(db.execute('SELECT last_insert_rowid()').fetchone()[0])
                remote_seg_count += 1

            elif t == 'ride':
                remote_id = f"f{friend_id}_{obj['id']}"
                db.execute('''
                    INSERT INTO Activity (
                        id, name, type, sportType, startDate, startDateLocal, distance, movingTime,
                        elapsedTime, totalElevationGain, averageSpeed, maxSpeed,
                        averageHeartrate, maxHeartrate, averageWatts, weightedAvgWatts,
                        averageCadence, calories, startLat, startLng, streams, rawData, riderId,
                        weatherTempC, weatherWindKph, weatherGustKph, weatherWindDir,
                        weatherHumidity, weatherRainMm, weatherCode, weatherSummary, weatherWindRel,
                        createdAt, updatedAt
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))
                    ON CONFLICT(id) DO UPDATE SET
                        name=excluded.name, streams=excluded.streams,
                        weatherTempC=excluded.weatherTempC, weatherWindKph=excluded.weatherWindKph,
                        weatherGustKph=excluded.weatherGustKph, weatherWindDir=excluded.weatherWindDir,
                        weatherHumidity=excluded.weatherHumidity, weatherRainMm=excluded.weatherRainMm,
                        weatherCode=excluded.weatherCode, weatherSummary=excluded.weatherSummary,
                        weatherWindRel=excluded.weatherWindRel,
                        updatedAt=datetime('now')
                ''', [
                    remote_id,
                    obj.get('name'),             obj.get('sportType'),
                    obj.get('sportType'),
                    obj.get('startDate'),         obj.get('startDateLocal'),
                    obj.get('distance') or 0,     obj.get('movingTime') or 0,
                    obj.get('elapsedTime') or 0,  obj.get('totalElevationGain') or 0,
                    obj.get('averageSpeed') or 0, obj.get('maxSpeed') or 0,
                    obj.get('averageHeartrate'),  obj.get('maxHeartrate'),
                    obj.get('averageWatts'),      obj.get('weightedAvgWatts'),
                    obj.get('averageCadence'),    obj.get('calories'),
                    obj.get('startLat'),          obj.get('startLng'),
                    obj.get('streams'),           '{}', rider_id,
                    obj.get('weatherTempC'),      obj.get('weatherWindKph'),
                    obj.get('weatherGustKph'),    obj.get('weatherWindDir'),
                    obj.get('weatherHumidity'),   obj.get('weatherRainMm'),
                    obj.get('weatherCode'),       obj.get('weatherSummary'),
                    obj.get('weatherWindRel'),
                ])
                new_ride_ids.append(remote_id)
                synced += 1
                if synced % BATCH == 0:
                    db.commit()
                    log.info('Friends sync: streamed %d rides for "%s"…', synced, name)

        db.commit()
    except Exception as e:
        log.warning('Friends sync: stream error for "%s": %s', name, e)
        db.commit()  # save whatever we got
        if synced == 0:
            return 0, str(e)
    finally:
        resp.close()

    log.info('Friends sync: received %d rides, %d segments from "%s"', synced, remote_seg_count, name)

    all_segments = db.execute('SELECT * FROM Segment').fetchall()
    affected_seg_ids = {s['id'] for s in all_segments}

    # ── Scan received rides against all segments ──────────────────
    if all_segments and new_ride_ids:
        scan_ids = new_ride_ids if is_incremental else None
        if scan_ids:
            acts = db.execute(
                'SELECT id, startDateLocal, streams FROM Activity WHERE id IN ({}) '
                "AND streams IS NOT NULL AND streams NOT IN ('null', '{{}}')".format(
                    ','.join('?' * len(scan_ids))), scan_ids
            ).fetchall()
        else:
            acts = db.execute(
                "SELECT id, startDateLocal, streams FROM Activity "
                "WHERE riderId=? AND streams IS NOT NULL AND streams NOT IN ('null', '{}')",
                [rider_id]
            ).fetchall()
        log.info('Friends sync: scanning %d rides against %d segments…', len(acts), len(all_segments))
        for i, act in enumerate(acts, 1):
            scan_activity_against_segments(db, act, all_segments)
            if i % BATCH == 0:
                db.commit()
                log.info('Friends sync: segment scan %d / %d…', i, len(acts))
                time.sleep(0.5)
        db.commit()

    # ── Scan owner's rides against any new imported segments ──────
    if imported_seg_ids:
        new_segs = db.execute(
            'SELECT * FROM Segment WHERE id IN ({})'.format(','.join('?' * len(imported_seg_ids))),
            imported_seg_ids
        ).fetchall()
        owner = db.execute('SELECT id FROM Rider WHERE isDefault=1').fetchone()
        if owner:
            owner_acts = db.execute(
                "SELECT id, startDateLocal, streams FROM Activity "
                "WHERE riderId=? AND streams IS NOT NULL AND streams NOT IN ('null', '{}')",
                [owner[0]]
            ).fetchall()
            log.info('Friends sync: scanning %d owner rides against %d new segments…',
                     len(owner_acts), len(new_segs))
            for i, act in enumerate(owner_acts, 1):
                scan_activity_against_segments(db, act, new_segs)
                if i % BATCH == 0:
                    db.commit()
                    time.sleep(0.5)
            db.commit()

    log.info('Friends sync: refreshing PRs…')
    for sid in affected_seg_ids:
        _refresh_prs(db, sid)

    db.execute("UPDATE Friend SET lastSynced=datetime('now'), name=? WHERE id=?",
               [name, friend_id])
    db.commit()
    log.info('Friends sync: done — %d rides, %d segments for "%s"', synced, remote_seg_count, name)
    return synced, None
