import hashlib
import io
import json
from math import atan2, cos, radians, sin, sqrt


def _stable_id(start_dt, distance_m):
    key = f"{start_dt.isoformat()}:{int(distance_m)}"
    return 'imp_' + hashlib.sha1(key.encode()).hexdigest()[:14]


def _haversine(lat1, lon1, lat2, lon2):
    R = 6371000
    p1, p2 = radians(lat1), radians(lat2)
    dp = radians(lat2 - lat1)
    dl = radians(lon2 - lon1)
    a = sin(dp / 2) ** 2 + cos(p1) * cos(p2) * sin(dl / 2) ** 2
    return 2 * R * atan2(sqrt(a), sqrt(1 - a))


def _ext_value(extensions, local_name):
    for ext in (extensions or []):
        try:
            for el in ext.iter():
                tag = el.tag
                if isinstance(tag, str):
                    if '}' in tag:
                        tag = tag.rsplit('}', 1)[1]
                    if tag.lower() == local_name.lower() and el.text:
                        try:
                            return float(el.text)
                        except (TypeError, ValueError):
                            pass
        except Exception:
            pass
    return None


_SPORT_MAP = {
    '1': 'Ride', 'ride': 'Ride', 'cycling': 'Ride', 'bike': 'Ride',
    '9': 'Run', 'run': 'Run', 'running': 'Run',
    'swim': 'Swim', 'swimming': 'Swim',
    'walk': 'Walk', 'walking': 'Walk',
    'hike': 'Hike', 'hiking': 'Hike',
    'vride': 'VirtualRide', 'virtual_ride': 'VirtualRide', 'virtualride': 'VirtualRide',
}


def _map_sport(s):
    return _SPORT_MAP.get(str(s).lower().strip(), 'Ride')


def parse_gpx(data: bytes):
    import gpxpy

    gpx = gpxpy.parse(io.StringIO(data.decode('utf-8', errors='replace')))

    points = []
    for track in gpx.tracks:
        for seg in track.segments:
            points.extend(seg.points)

    if not points:
        import logging; logging.getLogger(__name__).warning('parse_gpx: no track points found (route-only or empty file)')
        return None
    if points[0].time is None:
        import logging; logging.getLogger(__name__).warning('parse_gpx: no timestamps in track — cannot import route-only files')
        return None

    start = points[0].time
    name = 'Imported Ride'
    sport_type = 'Ride'
    if gpx.tracks:
        t = gpx.tracks[0]
        name = t.name or gpx.name or name
        if t.type:
            sport_type = _map_sport(t.type)

    moving_data = gpx.tracks[0].get_moving_data() if gpx.tracks else None
    distance = moving_data.moving_distance if moving_data else 0
    moving_time = moving_data.moving_time if moving_data else 0
    uphill, _ = gpx.tracks[0].get_uphill_downhill() if gpx.tracks else (0, 0)

    latlng, altitude, hr_s, cad_s, time_s, dist_s = [], [], [], [], [], []
    acc_dist = 0.0
    prev_ll = None

    for pt in points:
        has_ll = pt.latitude is not None and pt.longitude is not None
        if has_ll:
            latlng.append([pt.latitude, pt.longitude])
            if prev_ll:
                acc_dist += _haversine(prev_ll[0], prev_ll[1], pt.latitude, pt.longitude)
            dist_s.append(round(acc_dist, 1))
            prev_ll = (pt.latitude, pt.longitude)
        if pt.elevation is not None:
            altitude.append(round(pt.elevation, 1))
        if pt.time is not None:
            time_s.append(int((pt.time - start).total_seconds()))
        hr = _ext_value(pt.extensions, 'hr')
        if hr is not None:
            hr_s.append(int(hr))
        cad = _ext_value(pt.extensions, 'cad') or _ext_value(pt.extensions, 'cadence')
        if cad is not None:
            cad_s.append(int(cad))

    streams = {}
    if latlng:    streams['latlng']   = {'data': latlng}
    if altitude:  streams['altitude'] = {'data': altitude}
    if hr_s:      streams['heartrate'] = {'data': hr_s}
    if cad_s:     streams['cadence']  = {'data': cad_s}
    if time_s:    streams['time']     = {'data': time_s}
    if dist_s:    streams['distance'] = {'data': dist_s}

    avg_hr = (sum(hr_s) / len(hr_s)) if hr_s else None
    avg_speed = (distance / moving_time) if moving_time > 0 else None

    return {
        'id':                 _stable_id(start, distance),
        'name':               name,
        'sportType':          sport_type,
        'startDateLocal':     start.isoformat(),
        'distance':           round(distance, 1),
        'movingTime':         int(moving_time),
        'elapsedTime':        int(moving_time),
        'totalElevationGain': round(uphill or 0, 1),
        'averageSpeed':       round(avg_speed, 4) if avg_speed else None,
        'averageHeartrate':   round(avg_hr, 1) if avg_hr else None,
        'startLat':           latlng[0][0] if latlng else None,
        'startLng':           latlng[0][1] if latlng else None,
        'streams':            json.dumps(streams),
    }


def parse_fit(data: bytes):
    from fitparse import FitFile

    ff = FitFile(io.BytesIO(data))
    records = []
    session_msg = None

    for msg in ff.get_messages():
        if msg.name == 'record':
            records.append({f.name: f.value for f in msg})
        elif msg.name == 'session':
            session_msg = {f.name: f.value for f in msg}

    start = None
    if session_msg:
        start = session_msg.get('start_time')
    if not start and records:
        start = records[0].get('timestamp')
    if not start:
        return None

    s = session_msg or {}
    distance   = float(s.get('total_distance') or 0)
    moving_time = int(s.get('total_moving_time') or s.get('total_timer_time') or 0)
    elev_gain  = float(s.get('total_ascent') or 0)
    avg_hr     = s.get('avg_heart_rate')
    avg_watts  = s.get('avg_power')
    avg_speed  = s.get('avg_speed')
    avg_cad    = s.get('avg_cadence')
    calories   = s.get('total_calories')
    sport_type = _map_sport(s.get('sport', 'cycling'))

    latlng, alt_s, hr_s, pwr_s, cad_s, spd_s, time_s, dist_s = [], [], [], [], [], [], [], []

    for r in records:
        lat = r.get('position_lat')
        lng = r.get('position_long')
        if lat is not None and lng is not None:
            latlng.append([lat * 180 / 2 ** 31, lng * 180 / 2 ** 31])
        alt = r.get('altitude')
        if alt is not None:
            alt_s.append(round(float(alt), 1))
        hr = r.get('heart_rate')
        if hr is not None:
            hr_s.append(int(hr))
        pwr = r.get('power')
        if pwr is not None:
            pwr_s.append(int(pwr))
        cad = r.get('cadence')
        if cad is not None:
            cad_s.append(int(cad))
        spd = r.get('speed')
        if spd is not None:
            spd_s.append(round(float(spd), 3))
        ts = r.get('timestamp')
        if ts is not None:
            time_s.append(int((ts - start).total_seconds()))
        d = r.get('distance')
        if d is not None:
            dist_s.append(round(float(d), 1))

    streams = {}
    if latlng:  streams['latlng']          = {'data': latlng}
    if alt_s:   streams['altitude']        = {'data': alt_s}
    if hr_s:    streams['heartrate']       = {'data': hr_s}
    if pwr_s:   streams['watts']           = {'data': pwr_s}
    if cad_s:   streams['cadence']         = {'data': cad_s}
    if spd_s:   streams['velocity_smooth'] = {'data': spd_s}
    if time_s:  streams['time']            = {'data': time_s}
    if dist_s:  streams['distance']        = {'data': dist_s}

    if avg_hr is None and hr_s:
        avg_hr = sum(hr_s) / len(hr_s)
    if avg_speed is None and distance and moving_time:
        avg_speed = distance / moving_time

    return {
        'id':                 _stable_id(start, distance),
        'name':               sport_type + ' Activity',
        'sportType':          sport_type,
        'startDateLocal':     start.isoformat(),
        'distance':           round(distance, 1),
        'movingTime':         moving_time,
        'elapsedTime':        int(s.get('total_elapsed_time') or moving_time),
        'totalElevationGain': round(elev_gain, 1),
        'averageSpeed':       round(float(avg_speed), 4) if avg_speed else None,
        'averageHeartrate':   round(float(avg_hr), 1) if avg_hr else None,
        'averageWatts':       round(float(avg_watts), 1) if avg_watts else None,
        'averageCadence':     round(float(avg_cad), 1) if avg_cad else None,
        'calories':           round(float(calories), 1) if calories else None,
        'startLat':           latlng[0][0] if latlng else None,
        'startLng':           latlng[0][1] if latlng else None,
        'streams':            json.dumps(streams),
    }
