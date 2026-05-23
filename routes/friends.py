import gzip
import json
import logging
import secrets
import threading
import time
import urllib.request
import urllib.error

from flask import Blueprint, Response, abort, redirect, render_template, request, url_for

log = logging.getLogger(__name__)

from database import get_db, query_db
from services.segments import scan_activity_against_segments, _refresh_prs

bp = Blueprint('friends', __name__)


@bp.route('/api/feed')
def feed():
    """Token-authenticated public feed — no session required."""
    token = request.headers.get('X-Feed-Token', '') or request.args.get('token', '')
    settings = query_db('SELECT feedToken FROM Settings WHERE id=1', one=True)
    if not settings or not settings['feedToken'] or token != settings['feedToken']:
        abort(403)

    rider = query_db('SELECT * FROM Rider WHERE isDefault=1', one=True)
    rides = query_db('''
        SELECT id, name, type, sportType, startDate, startDateLocal, distance, movingTime,
               elapsedTime, totalElevationGain, averageSpeed, maxSpeed,
               averageHeartrate, maxHeartrate, averageWatts, weightedAvgWatts,
               averageCadence, calories, startLat, startLng, streams
        FROM Activity
        WHERE riderId = (SELECT id FROM Rider WHERE isDefault=1)
        ORDER BY startDateLocal DESC
    ''')
    # Only share locally-created segments (not ones imported from other friends)
    segments = query_db('''
        SELECT id, name, startLat, startLng, endLat, endLng,
               distanceM, elevationGainM, polyline
        FROM Segment
        WHERE friendId IS NULL
    ''')

    payload = json.dumps({
        'name':     rider['name'] if rider else 'Unknown',
        'rides':    [dict(r) for r in rides],
        'segments': [dict(s) for s in segments],
    }).encode()
    compressed = gzip.compress(payload, compresslevel=6)
    resp = Response(compressed, content_type='application/json')
    resp.headers['Content-Encoding'] = 'gzip'
    return resp


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


@bp.route('/friends/add', methods=['POST'])
def add():
    name  = (request.form.get('name')  or '').strip()
    url   = (request.form.get('url')   or '').strip().rstrip('/')
    token = (request.form.get('token') or '').strip()

    if not name or not url:
        return redirect(url_for('friends.index'))

    db = get_db()
    db.execute('INSERT INTO Friend (name, url, token) VALUES (?,?,?)', [name, url, token])
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

    feed_url = friend['url'].rstrip('/') + '/api/feed'
    headers  = {'User-Agent': 'Headwind/1.0'}
    if friend['token']:
        headers['X-Feed-Token'] = friend['token']

    log.info('Friends sync: fetching feed for "%s" from %s', friend['name'], feed_url)
    try:
        req = urllib.request.Request(feed_url, headers=headers)
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read()
            if resp.headers.get('Content-Encoding') == 'gzip':
                raw = gzip.decompress(raw)
            data = json.loads(raw.decode())
    except urllib.error.HTTPError as e:
        log.warning('Friends sync: HTTP %s from %s', e.code, feed_url)
        return 0, f'HTTP {e.code} from {feed_url}'
    except Exception as e:
        log.warning('Friends sync: fetch failed for "%s": %s', friend['name'], e)
        return 0, str(e)

    rides = data.get('rides', [])
    log.info('Friends sync: received %d rides from "%s"', len(rides), data.get('name') or friend['name'])

    db   = get_db()
    name = data.get('name') or friend['name']

    if not friend['riderId']:
        db.execute('INSERT INTO Rider (name, isDefault) VALUES (?, 0)', [name])
        rider_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]
        db.execute('UPDATE Friend SET riderId=? WHERE id=?', [rider_id, friend_id])
        log.info('Friends sync: created rider "%s" (id=%s)', name, rider_id)
    else:
        rider_id = friend['riderId']
        db.execute('UPDATE Rider SET name=? WHERE id=?', [name, rider_id])

    segments = db.execute('SELECT * FROM Segment').fetchall()
    synced   = 0

    BATCH = 500  # commit every N inserts to avoid one giant transaction
    for ride in rides:
        remote_id = f"f{friend_id}_{ride['id']}"
        db.execute('''
            INSERT INTO Activity (
                id, name, type, sportType, startDate, startDateLocal, distance, movingTime,
                elapsedTime, totalElevationGain, averageSpeed, maxSpeed,
                averageHeartrate, maxHeartrate, averageWatts, weightedAvgWatts,
                averageCadence, calories, startLat, startLng, streams, riderId,
                createdAt, updatedAt
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))
            ON CONFLICT(id) DO UPDATE SET
                name    = excluded.name,
                streams = excluded.streams,
                updatedAt = datetime('now')
        ''', [
            remote_id,
            ride.get('name'),         ride.get('type') or ride.get('sportType'),
            ride.get('sportType'),
            ride.get('startDate'),    ride.get('startDateLocal'),
            ride.get('distance'),     ride.get('movingTime'),
            ride.get('elapsedTime'),  ride.get('totalElevationGain'),
            ride.get('averageSpeed'), ride.get('maxSpeed'),
            ride.get('averageHeartrate'), ride.get('maxHeartrate'),
            ride.get('averageWatts'),     ride.get('weightedAvgWatts'),
            ride.get('averageCadence'),   ride.get('calories'),
            ride.get('startLat'),     ride.get('startLng'),
            ride.get('streams'),      rider_id,
        ])
        synced += 1
        if synced % BATCH == 0:
            db.commit()
            log.info('Friends sync: upserted %d / %d rides for "%s"…', synced, len(rides), name)

    log.info('Friends sync: all %d rides upserted for "%s"', synced, name)

    # ── Import friend's segments ──────────────────────────────────
    remote_segs = data.get('segments', [])
    imported_seg_ids = []  # local IDs of upserted friend segments
    for rs in remote_segs:
        existing = db.execute(
            'SELECT id FROM Segment WHERE friendId=? AND sourceSegId=?',
            [friend_id, rs['id']]
        ).fetchone()
        if existing:
            db.execute('''
                UPDATE Segment SET name=?, startLat=?, startLng=?, endLat=?, endLng=?,
                    distanceM=?, elevationGainM=?, polyline=?
                WHERE id=?
            ''', [
                rs.get('name'), rs.get('startLat'), rs.get('startLng'),
                rs.get('endLat'), rs.get('endLng'),
                rs.get('distanceM'), rs.get('elevationGainM'), rs.get('polyline'),
                existing[0],
            ])
            imported_seg_ids.append(existing[0])
        else:
            db.execute('''
                INSERT INTO Segment (name, startLat, startLng, endLat, endLng,
                    distanceM, elevationGainM, polyline, friendId, sourceSegId)
                VALUES (?,?,?,?,?,?,?,?,?,?)
            ''', [
                rs.get('name'), rs.get('startLat'), rs.get('startLng'),
                rs.get('endLat'), rs.get('endLng'),
                rs.get('distanceM'), rs.get('elevationGainM'), rs.get('polyline'),
                friend_id, rs['id'],
            ])
            imported_seg_ids.append(db.execute('SELECT last_insert_rowid()').fetchone()[0])
    db.commit()
    if remote_segs:
        log.info('Friends sync: upserted %d segments from "%s"', len(remote_segs), name)

    # Reload full segment list now that friend's segments are in
    all_segments = db.execute('SELECT * FROM Segment').fetchall()
    affected_seg_ids = {s['id'] for s in all_segments}

    # ── Scan friend's rides against all segments ──────────────────
    if all_segments and synced:
        acts = db.execute(
            "SELECT id, startDateLocal, streams FROM Activity "
            "WHERE riderId=? AND streams IS NOT NULL AND streams NOT IN ('null', '{}')",
            [rider_id]
        ).fetchall()
        log.info('Friends sync: scanning %d friend rides against %d segments…',
                 len(acts), len(all_segments))
        for i, act in enumerate(acts, 1):
            scan_activity_against_segments(db, act, all_segments)
            if i % BATCH == 0:
                db.commit()
                log.info('Friends sync: segment scan %d / %d…', i, len(acts))
                time.sleep(0.5)  # breathe — prevents sustained 100% CPU on low-power hardware
        db.commit()

    # ── Scan owner's rides against newly imported segments ────────
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
               [friend['name'], friend_id])
    db.commit()
    log.info('Friends sync: done — %d rides, %d segments synced for "%s"',
             synced, len(remote_segs), name)
    return synced, None
