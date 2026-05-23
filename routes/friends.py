import json
import secrets
import urllib.request
import urllib.error

from flask import Blueprint, abort, jsonify, redirect, render_template, request, url_for

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
        SELECT id, name, sportType, startDate, startDateLocal, distance, movingTime,
               elapsedTime, totalElevationGain, averageSpeed, maxSpeed,
               averageHeartrate, maxHeartrate, averageWatts, weightedAvgWatts,
               averageCadence, calories, startLat, startLng, streams
        FROM Activity
        WHERE riderId = (SELECT id FROM Rider WHERE isDefault=1)
        ORDER BY startDateLocal DESC
        LIMIT 100
    ''')

    return jsonify({
        'name':  rider['name'] if rider else 'Unknown',
        'rides': [dict(r) for r in rides],
    })


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

    _do_sync(fid)
    return redirect(url_for('friends.index'))


@bp.route('/friends/<int:fid>/sync', methods=['POST'])
def sync(fid):
    count, err = _do_sync(fid)
    return jsonify({'ok': err is None, 'synced': count, 'error': err})


@bp.route('/friends/<int:fid>/delete', methods=['POST'])
def delete(fid):
    db = get_db()
    friend = query_db('SELECT riderId FROM Friend WHERE id=?', [fid], one=True)
    if friend and friend['riderId']:
        db.execute('DELETE FROM SegmentEffort WHERE segmentId IN '
                   '(SELECT id FROM SegmentEffort e JOIN Activity a ON a.id=e.activityId '
                   ' WHERE a.riderId=?)', [friend['riderId']])
        db.execute('DELETE FROM Activity WHERE riderId=?', [friend['riderId']])
        db.execute('DELETE FROM Rider WHERE id=?', [friend['riderId']])
    db.execute('DELETE FROM Friend WHERE id=?', [fid])
    db.commit()

    # Re-run PRs now that their efforts are gone
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

    try:
        req = urllib.request.Request(feed_url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return 0, f'HTTP {e.code} from {feed_url}'
    except Exception as e:
        return 0, str(e)

    db   = get_db()
    name = data.get('name') or friend['name']

    if not friend['riderId']:
        db.execute('INSERT INTO Rider (name, isDefault) VALUES (?, 0)', [name])
        rider_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]
        db.execute('UPDATE Friend SET riderId=? WHERE id=?', [rider_id, friend_id])
    else:
        rider_id = friend['riderId']
        db.execute('UPDATE Rider SET name=? WHERE id=?', [name, rider_id])

    segments = db.execute('SELECT * FROM Segment').fetchall()
    synced   = 0

    for ride in data.get('rides', []):
        remote_id = f"f{friend_id}_{ride['id']}"
        db.execute('''
            INSERT INTO Activity (
                id, name, sportType, startDate, startDateLocal, distance, movingTime,
                elapsedTime, totalElevationGain, averageSpeed, maxSpeed,
                averageHeartrate, maxHeartrate, averageWatts, weightedAvgWatts,
                averageCadence, calories, startLat, startLng, streams, riderId,
                createdAt, updatedAt
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))
            ON CONFLICT(id) DO UPDATE SET
                name    = excluded.name,
                streams = excluded.streams,
                updatedAt = datetime('now')
        ''', [
            remote_id,
            ride.get('name'),         ride.get('sportType'),
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

    if segments and synced:
        acts = db.execute(
            "SELECT id, startDateLocal, streams FROM Activity "
            "WHERE riderId=? AND streams IS NOT NULL AND streams NOT IN ('null', '{}')",
            [rider_id]
        ).fetchall()
        for act in acts:
            scan_activity_against_segments(db, act, segments)
        for seg in segments:
            _refresh_prs(db, seg['id'])

    db.execute("UPDATE Friend SET lastSynced=datetime('now'), name=? WHERE id=?",
               [friend['name'], friend_id])
    db.commit()
    return synced, None
