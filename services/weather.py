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


def _route_bearing(streams_json):
    """Approximate overall bearing of the route (first → last GPS point)."""
    if not streams_json:
        return None
    import json
    try:
        streams = json.loads(streams_json) if isinstance(streams_json, str) else streams_json
        pts = streams.get('latlng', {}).get('data', [])
        if len(pts) < 10:
            return None
        lat1r = math.radians(pts[0][0])
        lat2r = math.radians(pts[-1][0])
        dlng  = math.radians(pts[-1][1] - pts[0][1])
        x = math.sin(dlng) * math.cos(lat2r)
        y = (math.cos(lat1r) * math.sin(lat2r)
             - math.sin(lat1r) * math.cos(lat2r) * math.cos(dlng))
        return (math.degrees(math.atan2(x, y)) + 360) % 360
    except Exception:
        return None


def _wind_relative(route_bearing, wind_dir, wind_kph):
    """Classify wind relative to route direction."""
    if wind_kph is None or wind_kph < 6:
        return 'calm'
    if route_bearing is None or wind_dir is None:
        return None
    diff = ((wind_dir - route_bearing) + 360) % 360
    if diff <= 45 or diff >= 315:
        return 'headwind'
    elif 135 <= diff <= 225:
        return 'tailwind'
    return 'crosswind'


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

    bearing   = _route_bearing(streams_json)
    wind_rel  = _wind_relative(bearing, wind_dir, wind_kph)

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
            parts.append(wind_rel)
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
