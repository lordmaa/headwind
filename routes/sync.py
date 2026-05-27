import json
import logging

from flask import Blueprint, jsonify
from database import get_db, query_db
from services.strava import sync_activities

bp = Blueprint('sync', __name__)
log = logging.getLogger(__name__)


@bp.route('/sync', methods=['POST'])
def sync():
    athlete = query_db('SELECT id FROM Athlete LIMIT 1', one=True)
    if not athlete:
        return jsonify({'error': 'Not connected'}), 401
    try:
        synced = sync_activities()
        if synced:
            from services.mqtt import push_update
            push_update()

        # Backfill Garmin HR for recent rides where Strava had no HR data
        try:
            s = query_db('SELECT garminEmail, garminPassword FROM Settings WHERE id=1', one=True)
            if s and s['garminEmail'] and s['garminPassword']:
                from services.garmin import fetch_ride_hr, _client
                missing = query_db('''
                    SELECT a.id, a.startDate, a.elapsedTime, a.streams
                    FROM Activity a
                    JOIN Rider r ON r.id = a.riderId
                    WHERE r.isDefault = 1
                      AND a.averageHeartrate IS NULL
                      AND a.startDate IS NOT NULL
                      AND a.elapsedTime IS NOT NULL
                      AND a.startDate >= datetime('now', '-7 days')
                ''')
                if missing:
                    garmin_api = _client(s['garminEmail'], s['garminPassword'])
                    db = get_db()
                    for act in missing:
                        try:
                            streams = json.loads(act['streams']) if act['streams'] else {}
                            time_stream = (streams.get('time') or {}).get('data')
                            hr = fetch_ride_hr(garmin_api, act['startDate'], act['elapsedTime'], time_stream)
                            if hr:
                                streams['heartrate'] = {
                                    'type': 'heartrate',
                                    'data': hr['stream_data'],
                                    'series_type': 'time',
                                    'original_size': len(hr['stream_data']),
                                    'resolution': 'medium' if time_stream else 'low',
                                }
                                db.execute(
                                    'UPDATE Activity SET averageHeartrate=?, maxHeartrate=?, streams=? WHERE id=?',
                                    [hr['avg'], hr['max'], json.dumps(streams), act['id']],
                                )
                                db.commit()
                                log.warning('Garmin HR backfill on sync: enriched %s — avg=%s max=%s', act['id'], hr['avg'], hr['max'])
                        except Exception as hre:
                            log.warning('Garmin HR backfill failed for %s: %s', act['id'], hre)
        except Exception as ge:
            log.warning('Garmin HR backfill (sync) failed: %s', ge)

        # Backfill weather for recent rides where it failed at sync time
        try:
            from services.weather import fetch_weather, save_weather
            missing_wx = query_db('''
                SELECT a.id, a.startLat, a.startLng, a.startDateLocal, a.streams
                FROM Activity a
                JOIN Rider r ON r.id = a.riderId
                WHERE r.isDefault = 1
                  AND a.weatherSummary IS NULL
                  AND a.startLat IS NOT NULL
                  AND a.startLng IS NOT NULL
                  AND a.startDateLocal IS NOT NULL
                  AND a.startDate >= datetime('now', '-7 days')
            ''')
            if missing_wx:
                db = get_db()
                for act in missing_wx:
                    try:
                        w = fetch_weather(act['startLat'], act['startLng'], act['startDateLocal'], act['streams'])
                        if w:
                            save_weather(db, act['id'], w)
                            db.commit()
                            log.warning('Weather backfill on sync: enriched %s', act['id'])
                    except Exception as we:
                        log.warning('Weather backfill failed for %s: %s', act['id'], we)
        except Exception as wxe:
            log.warning('Weather backfill (sync) failed: %s', wxe)

        return jsonify({'synced': synced})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
