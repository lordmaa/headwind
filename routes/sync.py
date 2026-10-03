import json
import logging

from flask import Blueprint, jsonify
from database import get_db, query_db

bp = Blueprint('sync', __name__)
log = logging.getLogger(__name__)


@bp.route('/sync', methods=['POST'])
def sync():
    """Pull recent activities from Garmin Connect (the only live sync source — Strava's public API was
    locked down by Strava and is no longer usable for this kind of third-party sync). Rides also arrive
    via the phone app's own recording (POST /api/v1/rides) or manual .fit/.gpx/Strava-export-zip import
    (routes/import_rides.py) — neither of those needs this endpoint."""
    s = query_db('SELECT garminEmail, garminPassword, garminSyncMode FROM Settings WHERE id=1', one=True)
    if not (s and s['garminEmail'] and s['garminPassword']):
        return jsonify({'error': 'Garmin is not configured — connect it on the Settings page'}), 400

    rider = query_db('SELECT id FROM Rider WHERE isDefault=1', one=True)
    if not rider:
        return jsonify({'error': 'No default rider found'}), 400

    imported, skipped = 0, 0
    try:
        if s['garminSyncMode'] == 'full':
            from services.garmin import sync_garmin_activities
            for status in sync_garmin_activities(s['garminEmail'], s['garminPassword'], rider['id']):
                if 'imported' in status:
                    imported = status['imported']
                    skipped = status['skipped']
        else:
            from services.garmin import sync_garmin
            sync_garmin(s['garminEmail'], s['garminPassword'], days=14)

        if imported > 0:
            from services.mqtt import push_update
            push_update()
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    # Backfill HR/weather for the owner's recent rides that are missing it, whatever their source —
    # a file import or a phone-recorded ride can land without HR (GPX has none) or without weather
    # (the API can be briefly down at insert time).
    try:
        if s['garminEmail'] and s['garminPassword']:
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

    return jsonify({'synced': imported, 'skipped': skipped})
