import hashlib
import io
import json
from math import atan2, cos, radians, sin, sqrt


def _stable_id(start_dt, distance_m):
    key = f"{start_dt.isoformat()}:{int(distance_m)}"
    return 'imp_' + hashlib.sha1(key.encode()).hexdigest()[:14]


def estimate_ride_calories(distance_m, moving_s, weight_kg):
    """Fallback for a GPX ride with no device-recorded calories (e.g. a phone recording — GPX has no calorie
    field at all, unlike FIT). Compendium-of-Physical-Activities MET bands by average speed — same table as
    the Android app's live in-ride estimate (Live.kt Calories.rideKcal), kept identical so a ride shows the
    same number while recording and after it lands on the server. Not gradient-aware (a hilly ride burns more
    than a flat one at the same average speed) — good enough as a fallback, real device data is always used
    when a source (Garmin/FIT/a Strava export) actually provides it."""
    if not weight_kg or weight_kg <= 0 or not distance_m or not moving_s or moving_s <= 0:
        return None
    minutes = moving_s / 60.0
    kmh = (distance_m / 1000.0) / (moving_s / 3600.0)
    met = 4.0 if kmh < 16 else 6.8 if kmh < 19 else 8.0 if kmh < 22 else 10.0 if kmh < 26 else 12.0 if kmh < 30 else 15.8
    return round(met * 3.5 * weight_kg / 200.0 * minutes)


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


_MAX_PLAUSIBLE = {'speed': 30.0, 'hr': 250, 'watts': 2500, 'cadence': 250}   # m/s (108 km/h), bpm, W, rpm — beyond this is sensor/GPS noise


def _windowed_max_speed(streams, window_s=10):
    """Max speed (m/s) for files with no speed stream (GPX): distance over >= window_s seconds, so a single GPS
    jump can't create a 90 mph "max". Distance comes from the stream or, failing that, from the GPS trace."""
    t = (streams.get('time') or {}).get('data') or []
    d = (streams.get('distance') or {}).get('data') or []
    if len(d) != len(t):
        ll = (streams.get('latlng') or {}).get('data') or []
        if len(ll) == len(t) and len(ll) > 1:
            d, acc = [0.0], 0.0
            for i in range(1, len(ll)):
                acc += _haversine(ll[i - 1][0], ll[i - 1][1], ll[i][0], ll[i][1])
                d.append(acc)
    if len(t) < 3 or len(d) != len(t):
        return None
    best, lo = 0.0, 0
    for hi in range(1, len(t)):
        while lo < hi - 1 and t[hi] - t[lo + 1] >= window_s:
            lo += 1
        dt = t[hi] - t[lo]
        if dt >= window_s:
            v = (d[hi] - d[lo]) / dt
            if v <= _MAX_PLAUSIBLE['speed']:      # a window containing a GPS jump is skipped, not allowed to set the max
                best = max(best, v)
    return best if best > 0 else None


def extremes_from_streams(streams):
    """Max speed / heart rate / power / cadence from the streams, ignoring impossible spikes. Missing -> absent keys."""
    out = {}
    def mx(key, cap):
        vals = [v for v in ((streams.get(key) or {}).get('data') or []) if v is not None and 0 < v <= cap]
        return max(vals) if vals else None
    spd = mx('velocity_smooth', _MAX_PLAUSIBLE['speed'])
    if spd is None:
        spd = _windowed_max_speed(streams)
    for name, val in (('maxSpeed', spd), ('maxHeartrate', mx('heartrate', _MAX_PLAUSIBLE['hr'])),
                      ('maxWatts', mx('watts', _MAX_PLAUSIBLE['watts'])), ('maxCadence', mx('cadence', _MAX_PLAUSIBLE['cadence']))):
        if val is not None:
            out[name] = round(float(val), 3) if name == 'maxSpeed' else round(float(val), 1)
    return out


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

    latlng, altitude, hr_s, cad_s, time_s, dist_s, pwr_s = [], [], [], [], [], [], []
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
        pwr = _ext_value(pt.extensions, 'power')
        if pwr is None:
            pwr = _ext_value(pt.extensions, 'powerinwatts')
        if pwr is None:
            pwr = _ext_value(pt.extensions, 'watts')
        if pwr is not None:
            pwr_s.append(int(pwr))

    streams = {}
    if latlng:    streams['latlng']   = {'data': latlng}
    if altitude:  streams['altitude'] = {'data': altitude}
    if hr_s:      streams['heartrate'] = {'data': hr_s}
    if cad_s:     streams['cadence']  = {'data': cad_s}
    if pwr_s:     streams['watts']    = {'data': pwr_s}
    if time_s:    streams['time']     = {'data': time_s}
    if dist_s:    streams['distance'] = {'data': dist_s}

    avg_hr = (sum(hr_s) / len(hr_s)) if hr_s else None
    avg_pwr = (sum(pwr_s) / len(pwr_s)) if pwr_s else None
    extremes = extremes_from_streams(streams)
    avg_speed = (distance / moving_time) if moving_time > 0 else None

    timed = [p.time for p in points if p.time is not None]
    elapsed_s = (timed[-1] - timed[0]).total_seconds() if len(timed) > 1 else moving_time   # wall-clock, not moving time

    return {
        'id':                 _stable_id(start, distance),
        'name':               name,
        'sportType':          sport_type,
        'startDateLocal':     start.isoformat(),
        'distance':           round(distance, 1),
        'movingTime':         int(moving_time),
        'elapsedTime':        int(elapsed_s),
        'totalElevationGain': round(uphill or 0, 1),
        'averageSpeed':       round(avg_speed, 4) if avg_speed else None,
        'averageHeartrate':   round(avg_hr, 1) if avg_hr else None,
        'averageWatts':       round(avg_pwr, 1) if avg_pwr else None,
        **extremes,
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
    # Some older devices report distance in mm. Only rescale when the per-record distance stream agrees it's off by 1000x —
    # a legitimate ultra-distance ride (>500 km) must not be silently shrunk.
    if distance > 500000:
        rec_dists = [r['distance'] for r in records if r.get('distance') is not None]
        last = float(rec_dists[-1]) if rec_dists else 0
        if last > 0 and abs(last - distance / 1000) / (distance / 1000) < 0.2:
            distance = distance / 1000
    moving_time = int(s.get('total_moving_time') or s.get('total_timer_time') or 0)
    elev_gain  = float(s.get('total_ascent') or 0)
    avg_hr     = s.get('avg_heart_rate')
    avg_watts  = s.get('avg_power')
    avg_speed  = s.get('avg_speed') if s.get('avg_speed') is not None else s.get('enhanced_avg_speed')
    avg_cad    = s.get('avg_cadence')
    calories   = s.get('total_calories')
    sport_type = _map_sport(s.get('sport', 'cycling'))

    latlng, alt_s, hr_s, pwr_s, cad_s, spd_s, time_s, dist_s = [], [], [], [], [], [], [], []

    for r in records:
        lat = r.get('position_lat')
        lng = r.get('position_long')
        if lat is not None and lng is not None:
            latlng.append([lat * 180 / 2 ** 31, lng * 180 / 2 ** 31])
        alt = r.get('altitude') if r.get('altitude') is not None else r.get('enhanced_altitude')   # newer devices write only enhanced_*
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
        spd = r.get('speed') if r.get('speed') is not None else r.get('enhanced_speed')
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

    # Maxima: prefer what the device recorded for the whole session, else derive from the streams.
    extremes = extremes_from_streams(streams)
    dev_max = {'maxSpeed': s.get('enhanced_max_speed') if s.get('enhanced_max_speed') is not None else s.get('max_speed'),
               'maxHeartrate': s.get('max_heart_rate'), 'maxWatts': s.get('max_power'), 'maxCadence': s.get('max_cadence')}
    for k, v in dev_max.items():
        try:
            v = float(v)
        except (TypeError, ValueError):
            continue
        if 0 < v <= {'maxSpeed': 30.0, 'maxHeartrate': 250, 'maxWatts': 2500, 'maxCadence': 250}[k]:
            extremes[k] = round(v, 3) if k == 'maxSpeed' else round(v, 1)

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
        **extremes,
        'calories':           round(float(calories), 1) if calories else None,
        'startLat':           latlng[0][0] if latlng else None,
        'startLng':           latlng[0][1] if latlng else None,
        'streams':            json.dumps(streams),
    }
