"""Walks, runs and hikes recorded by the phone app (the Workout table — kept apart from Activity, which is rides only).

A workout arrives as a GPX file (the app records the track); we reuse the GPX parser, store our own row, estimate calories when the
phone gave none, and stay idempotent: the same `client_id` (a UUID the app makes per recording) can be re-sent any number of times.
"""
import hashlib
import json
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from database import get_db, query_db

log = logging.getLogger(__name__)
SPORTS = ('Walk', 'Run', 'Hike')
_ICON = {'Walk': '🚶', 'Run': '🏃', 'Hike': '🥾'}
MI = 1609.344


def estimate_calories(sport, distance_m, moving_s, gain_m, kg):
    """Gross kcal from the ACSM walking / running equations (the same "gross" meaning as a watch's activity calories, i.e. it
    includes resting burn; day_burn subtracts that). Returns None when there isn't enough to go on."""
    if not (distance_m and moving_s and kg and moving_s > 60):
        return None
    minutes = moving_s / 60.0
    speed = distance_m / minutes                       # metres per minute
    grade = min(0.15, max(0.0, (gain_m or 0) / distance_m))
    if sport == 'Run' and speed >= 134:                # running equation from ~5 mph
        vo2 = 0.2 * speed + 0.9 * speed * grade + 3.5
    else:                                              # walking (also slow jogging / hiking)
        vo2 = 0.1 * speed + 1.8 * speed * grade + 3.5
    return round(vo2 * kg / 1000.0 * 5.0 * minutes)


def _local(iso):
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        return dt.strftime('%Y-%m-%dT%H:%M:%S'), iso
    return dt.astimezone(ZoneInfo('Europe/London')).strftime('%Y-%m-%dT%H:%M:%S'), dt.astimezone(ZoneInfo('UTC')).strftime('%Y-%m-%dT%H:%M:%SZ')


def save_from_gpx(rider_id, data, sport=None, name=None, client_id=None, steps=None, source='android'):
    """Parse a GPX recording and store it. Returns (workout dict, created: bool) or raises ValueError with a readable reason."""
    from services.parser import parse_gpx
    try:
        act = parse_gpx(data)
    except Exception as e:                             # not XML / not GPX / truncated upload
        raise ValueError(f'that is not a readable GPX file ({type(e).__name__})')
    if not act or not act.get('startDateLocal'):
        raise ValueError('could not read a timed track from that GPX file')
    sport = sport if sport in SPORTS else (act.get('sportType') if act.get('sportType') in SPORTS else 'Walk')
    local, utc = _local(act['startDateLocal'])
    key = client_id or f"{local}|{round(act.get('distance') or 0)}"
    wid = 'wk_' + hashlib.sha1(f'{rider_id}|{key}'.encode()).hexdigest()[:16]
    existing = query_db('SELECT * FROM Workout WHERE id=?', [wid], one=True)
    if existing:
        return dict(existing), False
    from services import goal_model
    kg = goal_model.current_weight_kg(rider_id) or 70.0
    dist, secs, gain = act.get('distance') or 0, int(act.get('movingTime') or 0), act.get('totalElevationGain') or 0
    kcal = estimate_calories(sport, dist, secs, gain, kg)
    weather = None
    if act.get('startLat') is not None:
        try:
            from services.weather import fetch_weather
            w = fetch_weather(act['startLat'], act['startLng'], local, act.get('streams'))
            weather = (w or {}).get('summary') or (w or {}).get('weatherSummary')
        except Exception:
            pass
    db = get_db()
    db.execute('''INSERT INTO Workout (id, riderId, sport, name, startDate, startDateLocal, distance, movingTime, elapsedTime,
                                       totalElevationGain, averageSpeed, averageHeartrate, calories, steps, startLat, startLng,
                                       streams, weatherSummary, source)
                  VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
               [wid, rider_id, sport, (name or '').strip() or None, utc, local, dist, secs, int(act.get('elapsedTime') or secs), gain,
                act.get('averageSpeed'), act.get('averageHeartrate'), kcal, steps, act.get('startLat'), act.get('startLng'),
                act.get('streams') if isinstance(act.get('streams'), str) else json.dumps(act.get('streams') or {}), weather, source])
    db.commit()
    return dict(query_db('SELECT * FROM Workout WHERE id=?', [wid], one=True)), True


def label(w):
    """'Morning walk' style label for a workout without a name of its own."""
    hour = int(str(w.get('startDateLocal') or 'T12')[11:13] or 12)
    return (w.get('name') or '').strip() or f"{'Morning' if hour < 12 else 'Afternoon' if hour < 17 else 'Evening'} {w['sport'].lower()}"


def summary(w, rider_name):
    """(title, message) for the push notification when a workout is saved."""
    mi = (w.get('distance') or 0) / MI
    secs = int(w.get('movingTime') or 0)
    h, r = divmod(secs, 3600)
    dur = f"{h}h {r // 60:02d}m" if h else f"{r // 60}m"
    title = f"{_ICON.get(w['sport'], '🏃')} {rider_name}: {label(w)} · {mi:.1f} mi"
    line1 = f"{mi:.1f} mi · {dur}"
    if secs and w.get('distance'):
        pace = secs / (w['distance'] / MI)                        # seconds per mile
        line1 += f" · {int(pace // 60)}:{int(pace % 60):02d} /mi"
    lines = [line1]
    extras = []
    if w.get('totalElevationGain'):
        extras.append(f"⛰ {round(w['totalElevationGain'] * 3.28084):,} ft")
    if w.get('calories'):
        extras.append(f"🔥 {round(w['calories']):,} kcal")
    if w.get('steps'):
        extras.append(f"👣 {w['steps']:,} steps")
    if extras:
        lines.append(' · '.join(extras))
    if w.get('averageHeartrate'):
        lines.append(f"❤️ {round(w['averageHeartrate'])} bpm")
    if w.get('weatherSummary'):
        lines.append(f"☁️ {w['weatherSummary']}")
    return title, '\n'.join(lines)


def calories_on(rider_id, date_iso):
    """Gross workout kcal on a local date (added to the daily burn wherever activity calories are summed)."""
    r = query_db('SELECT COALESCE(SUM(calories), 0) AS t FROM Workout WHERE riderId=? AND date(startDateLocal)=? AND calories IS NOT NULL',
                 [rider_id, date_iso], one=True)
    return int(r['t']) if r else 0


def net_calories_on(rider_id, date_iso, bmr):
    """Workout kcal above the resting burn during the workout — the same "net" basis goal_model.day_burn uses for rides."""
    rows = query_db('SELECT calories, movingTime FROM Workout WHERE riderId=? AND date(startDateLocal)=? AND calories IS NOT NULL', [rider_id, date_iso])
    return sum(max(0.0, (r['calories'] or 0) - bmr * (r['movingTime'] or 0) / 86400) for r in rows)


def recent(rider_id, days=30):
    since = (datetime.now(ZoneInfo('Europe/London')).date() - timedelta(days=days)).isoformat()
    rows = query_db('''SELECT id, sport, name, startDateLocal, distance, movingTime, totalElevationGain, calories, steps, weatherSummary, source
                       FROM Workout WHERE riderId=? AND date(startDateLocal)>=? ORDER BY startDateLocal DESC''', [rider_id, since])
    return [dict(r) for r in rows]
