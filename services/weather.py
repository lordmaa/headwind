import math
import requests
from datetime import datetime, timedelta


_WMO = {
    0: 'clear sky',
    1: 'mainly clear', 2: 'partly cloudy', 3: 'overcast',
    45: 'fog', 48: 'icy fog',
    51: 'light drizzle', 53: 'drizzle', 55: 'heavy drizzle',
    61: 'light rain', 63: 'rain', 65: 'heavy rain',
    71: 'light snow', 73: 'snow', 75: 'heavy snow', 77: 'snow grains',
    80: 'rain showers', 81: 'rain showers', 82: 'heavy rain showers',
    85: 'snow showers', 86: 'heavy snow showers',
    95: 'thunderstorm', 96: 'thunderstorm with hail', 99: 'thunderstorm',
}


def _bearing(a, b):
    lat1, lat2 = math.radians(a[0]), math.radians(b[0])
    dlng = math.radians(b[1] - a[1])
    x = math.sin(dlng) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlng)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def _dist_m(a, b):
    dy = (b[0] - a[0]) * 111_000
    dx = (b[1] - a[1]) * 111_000 * math.cos(math.radians((a[0] + b[0]) / 2))
    return math.hypot(dx, dy)


def _wind_exposure(streams_json, wind_dir):
    """How the wind met the rider over the WHOLE route: (mean headwind component, is_loop).

    The component per stretch is cos(angle between where the wind comes from and the direction of travel), weighted by
    distance: +1 straight into the wind, -1 straight behind, 0 across. The old approach used one first-to-last-point bearing,
    which is meaningless for a loop (start ~ finish) — a loop is half headwind, half tailwind."""
    if not streams_json or wind_dir is None:
        return None
    import json
    try:
        streams = json.loads(streams_json) if isinstance(streams_json, str) else streams_json
        pts = streams.get('latlng', {}).get('data', [])
        if len(pts) < 10:
            return None
        step = max(1, len(pts) // 120)
        total = weighted = 0.0
        for i in range(step, len(pts), step):
            a, b = pts[i - step], pts[i]
            d = _dist_m(a, b)
            if d < 5:
                continue
            weighted += math.cos(math.radians(wind_dir - _bearing(a, b))) * d
            total += d
        if total <= 0:
            return None
        return weighted / total, _dist_m(pts[0], pts[-1]) < 0.2 * total
    except Exception:
        return None


def _wind_relative(exposure, wind_kph):
    """Classify the wind relative to the route: calm / headwind / tailwind / crosswind / mixed (loops that get both)."""
    if wind_kph is None or wind_kph < 6:
        return 'calm'
    if exposure is None:
        return None
    mean, is_loop = exposure
    if mean >= 0.4:
        return 'headwind'
    if mean <= -0.4:
        return 'tailwind'
    return 'mixed' if is_loop else 'crosswind'


def fetch_weather(lat, lng, start_dt_local, streams_json=None):
    """
    Fetch hourly weather from Open-Meteo for the hour of a ride.
    Returns a dict of weather fields, or None on failure.
    Free API, no key required.
    """
    if not lat or not lng:
        return None

    try:
        dt = datetime.fromisoformat(str(start_dt_local)[:19])
    except Exception:
        return None

    date_str  = dt.strftime('%Y-%m-%d')
    days_ago  = (datetime.now() - dt).days
    hourly_fields = (
        'temperature_2m,windspeed_10m,windgusts_10m,winddirection_10m,'
        'precipitation,relativehumidity_2m,weathercode'
    )

    try:
        if days_ago >= 7:
            # Archive API — covers from 1940 to ~5 days ago
            resp = requests.get(
                'https://archive-api.open-meteo.com/v1/archive',
                params={
                    'latitude':        lat,
                    'longitude':       lng,
                    'start_date':      date_str,
                    'end_date':        date_str,
                    'hourly':          hourly_fields,
                    'timezone':        'auto',
                    'wind_speed_unit': 'kmh',
                },
                timeout=10,
            )
        else:
            # Forecast API with past_days — covers last 92 days
            resp = requests.get(
                'https://api.open-meteo.com/v1/forecast',
                params={
                    'latitude':        lat,
                    'longitude':       lng,
                    'hourly':          hourly_fields,
                    'timezone':        'auto',
                    'wind_speed_unit': 'kmh',
                    'past_days':       min(92, max(2, days_ago + 2)),
                    'forecast_days':   1,
                },
                timeout=10,
            )
        if not resp.ok:
            return None
        data = resp.json()
    except Exception:
        return None

    hourly = data.get('hourly', {})
    times  = hourly.get('time', [])
    if not times:
        return None

    # Find index matching ride's date + hour
    target = f'{date_str}T{dt.hour:02d}:00'
    idx = None
    for i, t in enumerate(times):
        if t == target:
            idx = i
            break
    if idx is None:
        # Fallback: first entry of the day
        for i, t in enumerate(times):
            if t.startswith(date_str):
                idx = i
                break
    if idx is None:
        return None

    def _val(key):
        vals = hourly.get(key, [])
        return vals[idx] if vals and idx < len(vals) else None

    temp_c   = _val('temperature_2m')
    wind_kph = _val('windspeed_10m')
    gust_kph = _val('windgusts_10m')
    wind_dir = _val('winddirection_10m')
    rain_mm  = _val('precipitation')
    humidity = _val('relativehumidity_2m')
    wmo      = _val('weathercode')

    wind_rel  = _wind_relative(_wind_exposure(streams_json, wind_dir), wind_kph)

    # Build a short human-readable summary
    parts = []
    if wmo is not None:
        parts.append(_WMO.get(int(wmo), '').capitalize())
    if temp_c is not None:
        parts.append(f'{temp_c:.0f}°C')
    if wind_kph is not None:
        wind_str = f'wind {wind_kph:.0f}kph'
        if gust_kph and gust_kph >= wind_kph * 1.25 and gust_kph > 15:
            wind_str += f' (gusts {gust_kph:.0f}kph)'
        parts.append(wind_str)
        if wind_rel and wind_rel not in ('calm', None):
            parts.append('variable wind direction' if wind_rel == 'mixed' else wind_rel)
    if rain_mm and rain_mm > 0.1:
        parts.append(f'{rain_mm:.1f}mm rain')

    summary = ', '.join(p for p in parts if p)

    return {
        'weatherTempC':    round(float(temp_c), 1)   if temp_c   is not None else None,
        'weatherWindKph':  round(float(wind_kph), 1) if wind_kph is not None else None,
        'weatherGustKph':  round(float(gust_kph), 1) if gust_kph is not None else None,
        'weatherWindDir':  round(float(wind_dir))    if wind_dir is not None else None,
        'weatherHumidity': round(float(humidity))    if humidity is not None else None,
        'weatherRainMm':   round(float(rain_mm), 1)  if rain_mm  is not None else None,
        'weatherCode':     int(wmo)                  if wmo      is not None else None,
        'weatherSummary':  summary or None,
        'weatherWindRel':  wind_rel,
    }


def save_weather(db, activity_id, weather_dict):
    """Persist weather dict to Activity row."""
    if not weather_dict:
        return
    db.execute('''
        UPDATE Activity SET
            weatherTempC=?, weatherWindKph=?, weatherGustKph=?,
            weatherWindDir=?, weatherHumidity=?, weatherRainMm=?,
            weatherCode=?, weatherSummary=?, weatherWindRel=?
        WHERE id=?
    ''', [
        weather_dict['weatherTempC'],
        weather_dict['weatherWindKph'],
        weather_dict['weatherGustKph'],
        weather_dict['weatherWindDir'],
        weather_dict['weatherHumidity'],
        weather_dict['weatherRainMm'],
        weather_dict['weatherCode'],
        weather_dict['weatherSummary'],
        weather_dict['weatherWindRel'],
        str(activity_id),
    ])
