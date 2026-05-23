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

bp = Blueprint('friends', __name__)

BATCH = 500


@bp.route('/api/riders')
def riders_list():
    """Token-authenticated rider list — lets a friend's instance discover who's here."""
    token = request.headers.get('X-Feed-Token', '') or request.args.get('token', '')
    settings = query_db('SELECT feedToken FROM Settings WHERE id=1', one=True)
    if not settings or not settings['feedToken'] or token != settings['feedToken']:
        abort(403)
    riders = query_db('SELECT id, name, isDefault FROM Rider ORDER BY isDefault DESC, name ASC')
    return jsonify([dict(r) for r in riders])


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
                            'distanceM, elevationGainM, polyline FROM Segment WHERE friendId IS NULL'):
            yield json.dumps({'type': 'segment', **dict(s)}) + '\n'

        owner_id = rider['id'] if rider else None
        if owner_id:
            if since:
                cur = db.execute('''
                    SELECT id, name, type, sportType, startDate, startDateLocal, distance, movingTime,
                           elapsedTime, totalElevationGain, averageSpeed, maxSpeed,
                           averageHeartrate, maxHeartrate, averageWatts, weightedAvgWatts,
                           averageCadence, calories, startLat, startLng, streams
                    FROM Activity WHERE riderId=? AND startDateLocal > ? ORDER BY startDateLocal DESC
                ''', [owner_id, since])
            else:
                cur = db.execute('''
                    SELECT id, name, type, sportType, startDate, startDateLocal, distance, movingTime,
                           elapsedTime, totalElevationGain, averageSpeed, maxSpeed,
                           averageHeartrate, maxHeartrate, averageWatts, weightedAvgWatts,
                           averageCadence, calories, startLat, startLng, streams
                    FROM Activity WHERE riderId=? ORDER BY startDateLocal DESC
                ''', [owner_id])
            for row in cur:
                d = dict(row)
                d.pop('type', None)  # avoid collision with the NDJSON routing key
                yield json.dumps({'type': 'ride', **d}) + '\n'

    return Response(stream_with_context(generate()), mimetype='application/x-ndjson')


@bp.route('/friends')
def index():
    settings = query_db('SELECT feedToken FROM Settings WHERE id=1', one=True)
    feed_token = settings['feedToken'] if settings else None

    if not feed_token:
        db = get_db()
        feed_token = secrets.token_urlsafe(24)
        db.execute('UPDATE Settings SET feedToken=? WHERE id=1', [feed_token])
        db.commit()

    friends = query_db('''
        SELECT f.*, r.name AS rider_name, r.avatarPath AS rider_avatar
        FROM Friend f
        LEFT JOIN Rider r ON r.id = f.riderId
        ORDER BY f.name
    ''')

    return render_template('friends.html', friends=friends, feed_token=feed_token)


@bp.route('/friends/regenerate-token', methods=['POST'])
def regenerate_token():
    db = get_db()
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
        req  = urllib.request.Request(probe_url, headers=headers)
        resp = urllib.request.urlopen(req, timeout=10)
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
        req = urllib.request.Request(feed_url, headers=headers)
        resp = urllib.request.urlopen(req, timeout=60)
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
        for raw_line in resp:
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
                existing = db.execute(
                    'SELECT id FROM Segment WHERE friendId=? AND sourceSegId=?',
                    [friend_id, obj['id']]
                ).fetchone()
                if existing:
                    db.execute('''UPDATE Segment SET name=?, startLat=?, startLng=?, endLat=?, endLng=?,
                                      distanceM=?, elevationGainM=?, polyline=? WHERE id=?''', [
                        obj.get('name'), obj.get('startLat'), obj.get('startLng'),
                        obj.get('endLat'), obj.get('endLng'),
                        obj.get('distanceM'), obj.get('elevationGainM'), obj.get('polyline'),
                        existing[0],
                    ])
                    imported_seg_ids.append(existing[0])
                else:
                    db.execute('''INSERT INTO Segment (name, startLat, startLng, endLat, endLng,
                                      distanceM, elevationGainM, polyline, friendId, sourceSegId)
                                  VALUES (?,?,?,?,?,?,?,?,?,?)''', [
                        obj.get('name'), obj.get('startLat'), obj.get('startLng'),
                        obj.get('endLat'), obj.get('endLng'),
                        obj.get('distanceM'), obj.get('elevationGainM'), obj.get('polyline'),
                        friend_id, obj['id'],
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
                        averageCadence, calories, startLat, startLng, streams, riderId,
                        createdAt, updatedAt
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))
                    ON CONFLICT(id) DO UPDATE SET
                        name=excluded.name, streams=excluded.streams, updatedAt=datetime('now')
                ''', [
                    remote_id,
                    obj.get('name'),          obj.get('sportType'),
                    obj.get('sportType'),
                    obj.get('startDate'),     obj.get('startDateLocal'),
                    obj.get('distance'),      obj.get('movingTime'),
                    obj.get('elapsedTime'),   obj.get('totalElevationGain'),
                    obj.get('averageSpeed'),  obj.get('maxSpeed'),
                    obj.get('averageHeartrate'), obj.get('maxHeartrate'),
                    obj.get('averageWatts'),     obj.get('weightedAvgWatts'),
                    obj.get('averageCadence'),   obj.get('calories'),
                    obj.get('startLat'),      obj.get('startLng'),
                    obj.get('streams'),       rider_id,
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
