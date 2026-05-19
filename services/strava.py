import time
import requests
from flask import current_app
from database import get_db, query_db


def get_valid_token():
    athlete = query_db('SELECT * FROM Athlete LIMIT 1', one=True)
    if not athlete:
        raise Exception('No athlete connected')

    if time.time() < athlete['expiresAt'] - 300:
        return athlete['accessToken']

    res = requests.post('https://www.strava.com/oauth/token', json={
        'client_id': current_app.config['STRAVA_CLIENT_ID'],
        'client_secret': current_app.config['STRAVA_CLIENT_SECRET'],
        'grant_type': 'refresh_token',
        'refresh_token': athlete['refreshToken'],
    })
    data = res.json()
    db = get_db()
    db.execute(
        'UPDATE Athlete SET accessToken=?, refreshToken=?, expiresAt=? WHERE id=?',
        [data['access_token'], data['refresh_token'], data['expires_at'], athlete['id']],
    )
    db.commit()
    return data['access_token']


def strava_get(path):
    token = get_valid_token()
    res = requests.get(
        f'https://www.strava.com/api/v3{path}',
        headers={'Authorization': f'Bearer {token}'},
    )
    if not res.ok:
        raise Exception(f'Strava API error: {res.status_code} {path}')
    return res.json()


def _default_rider_id(db):
    row = db.execute('SELECT id FROM Rider WHERE isDefault=1 LIMIT 1').fetchone()
    return row[0] if row else None


def _is_ride(sport_type):
    t = (sport_type or '').lower()
    return 'ride' in t or 'cycling' in t


def sync_one_activity(activity_id):
    import json as _json
    aid = str(activity_id)
    db  = get_db()

    try:
        detail = strava_get(f'/activities/{aid}')
    except Exception as e:
        raise Exception(f'Could not fetch activity {aid}: {e}')

    try:
        keys = 'time,altitude,heartrate,watts,cadence,velocity_smooth,latlng,distance'
        streams_raw  = strava_get(f'/activities/{aid}/streams?keys={keys}&key_by_type=true')
        streams_json = _json.dumps(streams_raw)
    except Exception:
        streams_json = None

    act = detail
    rider_id = _default_rider_id(db)
    db.execute('''
        INSERT INTO Activity (
            id, name, type, sportType, startDate, startDateLocal,
            timezone, distance, movingTime, elapsedTime,
            totalElevationGain, averageSpeed, maxSpeed,
            averageHeartrate, maxHeartrate, averageWatts, maxWatts,
            weightedAvgWatts, kilojoules, averageCadence,
            calories, sufferScore, startLat, startLng,
            city, country, summaryPolyline, kudosCount,
            streams, rawData, description, riderId, createdAt, updatedAt
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))
        ON CONFLICT(id) DO UPDATE SET
            name=excluded.name, streams=excluded.streams,
            kudosCount=excluded.kudosCount, rawData=excluded.rawData,
            description=excluded.description,
            updatedAt=datetime('now')
    ''', [
        aid,
        act.get('name', ''),
        act.get('type', ''),
        act.get('sport_type') or act.get('type', ''),
        act.get('start_date', ''),
        act.get('start_date_local', ''),
        act.get('timezone'),
        act.get('distance') or 0,
        act.get('moving_time') or 0,
        act.get('elapsed_time') or 0,
        act.get('total_elevation_gain') or 0,
        act.get('average_speed') or 0,
        act.get('max_speed') or 0,
        act.get('average_heartrate'),
        act.get('max_heartrate'),
        act.get('average_watts'),
        act.get('max_watts'),
        act.get('weighted_average_watts'),
        act.get('kilojoules'),
        act.get('average_cadence'),
        act.get('calories'),
        act.get('suffer_score'),
        act['start_latlng'][0] if act.get('start_latlng') else None,
        act['start_latlng'][1] if act.get('start_latlng') else None,
        act.get('location_city'),
        act.get('location_country'),
        act.get('map', {}).get('summary_polyline') or act.get('map', {}).get('polyline'),
        act.get('kudos_count') or 0,
        streams_json,
        _json.dumps(detail),
        act.get('description') or None,
        rider_id,
    ])
    sport = act.get('sport_type') or act.get('type', '')
    if _is_ride(sport):
        from services.best_efforts import save_best_efforts
        from services.segments import scan_activity_against_segments, _refresh_prs
        save_best_efforts(db, aid, act.get('start_date_local', ''), streams_json)
        db.commit()

        segments = db.execute('SELECT * FROM Segment').fetchall()
        if segments and streams_json:
            act_row = db.execute('SELECT id, startDateLocal, streams FROM Activity WHERE id=?', [aid]).fetchone()
            scan_activity_against_segments(db, act_row, segments)
            for seg in segments:
                _refresh_prs(db, seg['id'])
            db.commit()
    else:
        db.commit()

    # Fetch weather for new/updated activity
    try:
        from services.weather import fetch_weather, save_weather
        lat = act['start_latlng'][0] if act.get('start_latlng') else None
        lng = act['start_latlng'][1] if act.get('start_latlng') else None
        w = fetch_weather(lat, lng, act.get('start_date_local'), streams_json)
        if w:
            save_weather(db, aid, w)
            db.commit()
    except Exception:
        pass

    return db.execute('SELECT * FROM Activity WHERE id=?', [aid]).fetchone()


def sync_activities():
    db = get_db()
    rider_id = _default_rider_id(db)
    segments = db.execute('SELECT * FROM Segment').fetchall()
    synced = 0
    page = 1

    while True:
        activities = strava_get(f'/athlete/activities?per_page=50&page={page}')
        if not activities:
            break

        stop = False
        for act in activities:
            aid = str(act['id'])
            exists = db.execute('SELECT id FROM Activity WHERE id=?', [aid]).fetchone()
            if exists:
                stop = True
                break

            try:
                detail = strava_get(f'/activities/{aid}')
            except Exception:
                detail = act

            try:
                keys = 'time,altitude,heartrate,watts,cadence,velocity_smooth,latlng,distance'
                streams_raw = strava_get(f'/activities/{aid}/streams?keys={keys}&key_by_type=true')
                import json
                streams_json = json.dumps(streams_raw)
            except Exception:
                streams_json = None

            db.execute('''
                INSERT INTO Activity (
                    id, name, type, sportType, startDate, startDateLocal,
                    timezone, distance, movingTime, elapsedTime,
                    totalElevationGain, averageSpeed, maxSpeed,
                    averageHeartrate, maxHeartrate, averageWatts, maxWatts,
                    weightedAvgWatts, kilojoules, averageCadence,
                    calories, sufferScore, startLat, startLng,
                    city, country, summaryPolyline, kudosCount,
                    streams, rawData, riderId, description, createdAt, updatedAt
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))
            ''', [
                aid,
                act.get('name', ''),
                act.get('type', ''),
                act.get('sport_type') or act.get('type', ''),
                act.get('start_date', ''),
                act.get('start_date_local', ''),
                act.get('timezone'),
                act.get('distance') or 0,
                act.get('moving_time') or 0,
                act.get('elapsed_time') or 0,
                act.get('total_elevation_gain') or 0,
                act.get('average_speed') or 0,
                act.get('max_speed') or 0,
                act.get('average_heartrate'),
                act.get('max_heartrate'),
                detail.get('average_watts') or act.get('average_watts'),
                detail.get('max_watts'),
                detail.get('weighted_average_watts'),
                detail.get('kilojoules'),
                act.get('average_cadence'),
                detail.get('calories'),
                act.get('suffer_score'),
                act['start_latlng'][0] if act.get('start_latlng') else None,
                act['start_latlng'][1] if act.get('start_latlng') else None,
                act.get('location_city'),
                act.get('location_country'),
                act.get('map', {}).get('summary_polyline') or detail.get('map', {}).get('polyline'),
                act.get('kudos_count') or 0,
                streams_json,
                __import__('json').dumps(detail),
                rider_id,
                detail.get('description') or None,
            ])
            db.commit()
            bulk_sport = act.get('sport_type') or act.get('type', '')
            if _is_ride(bulk_sport):
                from services.best_efforts import save_best_efforts
                from services.segments import scan_activity_against_segments, _refresh_prs
                save_best_efforts(db, aid, act.get('start_date_local', ''), streams_json)
                db.commit()
                if segments and streams_json:
                    act_row = db.execute('SELECT id, startDateLocal, streams FROM Activity WHERE id=?', [aid]).fetchone()
                    scan_activity_against_segments(db, act_row, segments)
                    for seg in segments:
                        _refresh_prs(db, seg['id'])
                    db.commit()
            try:
                from services.weather import fetch_weather, save_weather
                lat = act['start_latlng'][0] if act.get('start_latlng') else None
                lng = act['start_latlng'][1] if act.get('start_latlng') else None
                w = fetch_weather(lat, lng, act.get('start_date_local'), streams_json)
                if w:
                    save_weather(db, aid, w)
                    db.commit()
            except Exception:
                pass

            synced += 1

        if stop:
            break
        page += 1

    return synced
