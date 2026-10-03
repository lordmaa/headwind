"""Pull health readings (e.g. a BLE scale seen through an ESPHome bluetooth proxy) out of Home Assistant's REST API.

Auth is a long-lived access token. Each mapping is {entity_id, target}; 'weight' lands in WeightLog (so it feeds the
trend graph), everything else in BodyMetric (latest reading per day).
"""
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

from database import get_db, query_db
from services.weight_trend import MIN_SANE_KG, MAX_SANE_KG

log = logging.getLogger(__name__)
LOCAL_TZ = ZoneInfo('Europe/London')
FIRST_SYNC_DAYS = 10  # HA's recorder keeps 10 days by default
# Same sanity range as the manual/backfill weight paths (services/weight_trend.py) — these had drifted apart
# (this file used to have its own 20-350), which let a bogus HA-imported reading past a bound that the rest
# of the app enforces more tightly, corrupting the trend graph and goal_model's BMR calc with no warning.
MIN_KG, MAX_KG = MIN_SANE_KG, MAX_SANE_KG

TARGETS = {
    'weight':          ('Weight', 'kg'),
    'body_fat':        ('Body fat', '%'),
    'muscle_mass':     ('Muscle mass', 'kg'),
    'body_water':      ('Body water', '%'),
    'bone_mass':       ('Bone mass', 'kg'),
    'bmi':             ('BMI', ''),
    'visceral_fat':    ('Visceral fat', ''),
    'basal_metabolism': ('Basal metabolism', 'kcal'),
    'metabolic_age':   ('Metabolic age', 'yrs'),
    'resting_hr':      ('Resting heart rate', 'bpm'),
    # No generic 'steps' target: services/mqtt.py's STEP_METRICS only ever reads steps_phone/steps_watch/steps_manual —
    # a BodyMetric row written as plain 'steps' was silently invisible everywhere. Pick one of these two instead.
    'steps_phone':     ('Steps (phone)', ''),
    'steps_watch':     ('Steps (watch)', ''),
}
_TO_KG = {'kg': 1.0, 'g': 0.001, 'lb': 0.45359237, 'lbs': 0.45359237, 'st': 6.35029318, 'oz': 0.0283495231}
_HINTS = ('weight', 'scale', 'body', 'fat', 'muscle', 'bmi', 'bone', 'water', 'visceral', 'metabolic', 'heart', 'steps', 'mass', 'impedance')


class HAError(Exception):
    pass


def _headers(token):
    return {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}


def _get(url, token, path, params=None, timeout=15):
    try:
        r = requests.get(url.rstrip('/') + path, headers=_headers(token), params=params, timeout=timeout)
    except requests.exceptions.RequestException as e:
        raise HAError(f'Could not reach Home Assistant at {url} ({e.__class__.__name__})')
    if r.status_code == 401:
        raise HAError('Home Assistant rejected the token (401) — create a new long-lived access token.')
    if not r.ok:
        raise HAError(f'Home Assistant returned {r.status_code}')
    return r.json()


def test(url, token):
    cfg = _get(url, token, '/api/config')
    return {'name': cfg.get('location_name'), 'version': cfg.get('version')}


def list_entities(url, token):
    """Sensor-like entities (likely health ones first) plus any `weather.*` entities — one HA call
    serves both the health-metric picker and the weather-entity picker (see routes/homeassistant.py
    and the setup wizard's Home Assistant step, which split the result by `domain` client-side)."""
    out = []
    for s in _get(url, token, '/api/states', timeout=30):
        eid = s['entity_id']
        domain = eid.split('.')[0]
        if domain not in ('sensor', 'input_number', 'number', 'weather'):
            continue
        a = s.get('attributes') or {}
        name = a.get('friendly_name') or eid
        blob = f'{eid} {name}'.lower()
        out.append({'entity_id': eid, 'name': name, 'state': s.get('state'), 'domain': domain,
                    'unit': a.get('unit_of_measurement') or '',
                    'likely': a.get('device_class') == 'weight' or any(h in blob for h in _HINTS)})
    out.sort(key=lambda e: (not e['likely'], e['name'].lower()))
    return out


# HA's built-in weather conditions (https://www.home-assistant.io/integrations/weather/) — not every
# integration uses every value, but this covers the standard set. Falls back to a generic cloud if a
# custom integration reports something outside this list, rather than showing nothing.
WEATHER_ICONS = {
    'clear-night': '🌙', 'cloudy': '☁️', 'exceptional': '⚠️', 'fog': '🌫️',
    'hail': '🌨️', 'lightning': '⛈️', 'lightning-rainy': '⛈️', 'partlycloudy': '⛅',
    'pouring': '🌧️', 'rainy': '🌧️', 'snowy': '❄️', 'snowy-rainy': '🌨️',
    'sunny': '☀️', 'windy': '💨', 'windy-variant': '💨',
}


def weather_icon(condition):
    return WEATHER_ICONS.get((condition or '').lower(), '☁️')


def sync_weather():
    """Fetch the current state of the chosen weather.* entity and cache it on Settings — a live read,
    not a history import, so this is deliberately separate from sync()/the body-metric mappings above."""
    cfg = _cfg()
    if not cfg:
        return
    s = query_db('SELECT haWeatherEntity FROM Settings WHERE id=1', one=True)
    eid = ((s['haWeatherEntity'] if s else None) or '').strip()
    if not eid:
        return
    row = _get(cfg['url'], cfg['token'], f'/api/states/{eid}', timeout=8)
    attrs = row.get('attributes') or {}
    condition = row.get('state')
    temp_c = attrs.get('temperature')
    db = get_db()
    db.execute(
        'UPDATE Settings SET haWeatherCondition=?, haWeatherTempC=?, haWeatherUpdatedAt=? WHERE id=1',
        [condition, temp_c, datetime.now(timezone.utc).isoformat(timespec='seconds')],
    )
    db.commit()


_last_weather_attempt = 0.0


def sync_weather_if_due():
    """Called by the background heartbeat (every 5 min, same cadence as sync_if_due) — independent of
    the body-metric mappings/interval, so weather works even if no health entities are configured."""
    global _last_weather_attempt
    if time.time() - _last_weather_attempt < 240:
        return
    _last_weather_attempt = time.time()
    try:
        sync_weather()
    except HAError as e:
        log.warning('Home Assistant weather fetch failed: %s', e)
    except Exception as e:
        log.warning('Home Assistant weather error: %s', e)


def _cfg():
    s = query_db('SELECT haUrl, haToken, haEntityMap, haSyncMinutes, haLastSync FROM Settings WHERE id=1', one=True)
    if not s or not (s['haUrl'] or '').strip() or not (s['haToken'] or '').strip():
        return None
    try:
        mappings = json.loads(s['haEntityMap'] or '[]')
    except ValueError:
        mappings = []
    return {'url': s['haUrl'].strip(), 'token': s['haToken'].strip(), 'mappings': mappings,
            'minutes': s['haSyncMinutes'] or 15, 'last': s['haLastSync']}


def _to_float(state):
    try:
        return float(state)
    except (TypeError, ValueError):
        return None  # 'unknown', 'unavailable', ''


def _history_rows(url, token, entity_id, since):
    """[(utc datetime, float value)] for every real state change since `since` (numeric states only)."""
    # HA defaults end_time to start + 1 day, so it must be given or anything older than a day is silently dropped.
    # skip_initial_state: otherwise HA prepends the value held at the window start, stamped with the START time —
    # which looked like a fresh reading on that day (it invented a weigh-in on days you didn't weigh).
    hist = _get(url, token, '/api/history/period/' + since.astimezone(timezone.utc).isoformat(timespec='seconds'),
                {'filter_entity_id': entity_id, 'minimal_response': '1', 'no_attributes': '1',
                 'end_time': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'skip_initial_state': '1'}, timeout=60)
    out = []
    for row in (hist[0] if hist else []):
        val = _to_float(row.get('state'))
        ts = row.get('last_changed') or row.get('last_updated')
        if val is not None and ts:
            out.append((datetime.fromisoformat(ts.replace('Z', '+00:00')), val))
    return out


def _unit_of(url, token, entity_id):
    return (_get(url, token, f'/api/states/{entity_id}').get('attributes') or {}).get('unit_of_measurement') or ''


def _daily_last(url, token, entity_id, since):
    """{local_date: (value, unit)} — the last valid reading each day since `since`."""
    unit = _unit_of(url, token, entity_id)
    days = {}
    for ts, val in _history_rows(url, token, entity_id, since):   # chronological, so the last one sticks
        days[ts.astimezone(LOCAL_TZ).date().isoformat()] = (val, unit)
    return days


def sync(days=None):
    """Import everything new. Returns a summary dict; raises HAError on connection problems."""
    cfg = _cfg()
    if not cfg:
        raise HAError('Home Assistant is not configured yet.')
    if not cfg['mappings']:
        raise HAError('No entities selected yet.')
    if days:
        since = datetime.now(timezone.utc) - timedelta(days=days)
    elif cfg['last']:
        since = datetime.fromisoformat(cfg['last']) - timedelta(hours=2)
    else:
        since = datetime.now(timezone.utc) - timedelta(days=FIRST_SYNC_DAYS)

    rider = query_db('SELECT id FROM Rider WHERE isDefault=1 LIMIT 1', one=True)
    rider_id = rider['id'] if rider else None
    db = get_db()
    summary = {'weights': 0, 'metrics': 0, 'replaced_manual': 0, 'errors': []}
    for m in cfg['mappings']:
        eid, target = m.get('entity_id'), m.get('target')
        if target not in TARGETS or not eid:
            continue
        try:
            readings = _daily_last(cfg['url'], cfg['token'], eid, since)   # the last value of each day — no averaging or estimating
        except HAError as e:
            summary['errors'].append(f'{eid}: {e}')
            continue
        for day, (val, unit) in readings.items():
            if target == 'weight':
                kg = val * _TO_KG.get(unit.lower(), 1.0)
                if not (MIN_KG <= kg <= MAX_KG):
                    continue
                existing = query_db('SELECT id, source FROM WeightLog WHERE riderId=? AND logDate=?',
                                    [rider_id, day], one=True)
                if existing:
                    if existing['source'] == 'manual':
                        summary['replaced_manual'] += 1  # the scale is the source of truth
                    db.execute("UPDATE WeightLog SET weightKg=?, source='homeassistant' WHERE id=?", [round(kg, 2), existing['id']])
                    summary['weights'] += 1
                else:
                    db.execute("INSERT INTO WeightLog (riderId, logDate, weightKg, source) VALUES (?,?,?,'homeassistant')",
                               [rider_id, day, round(kg, 2)])
                    summary['weights'] += 1
            else:
                db.execute('''
                    INSERT INTO BodyMetric (riderId, logDate, metric, value, unit, source)
                    VALUES (?,?,?,?,?,'homeassistant')
                    ON CONFLICT(riderId, logDate, metric) DO UPDATE SET
                        value=excluded.value, unit=excluded.unit, updatedAt=datetime('now')
                ''', [rider_id, day, target, val, unit or TARGETS[target][1]])
                summary['metrics'] += 1
    now = datetime.now(timezone.utc).isoformat(timespec='seconds')
    msg = f"{summary['weights']} weigh-in(s), {summary['metrics']} other reading(s)"
    if summary['replaced_manual']:
        msg += f", {summary['replaced_manual']} hand-typed weigh-in(s) replaced by the scale"
    if summary['errors']:
        msg += ' — errors: ' + '; '.join(summary['errors'])
    db.execute('UPDATE Settings SET haLastSync=?, haLastResult=? WHERE id=1', [now if not summary['errors'] else cfg['last'], msg])
    db.commit()
    if summary['weights']:
        try:
            from services.mqtt import push_update_nutrition
            push_update_nutrition()   # the weight-trend sensors changed
        except Exception:
            pass
    return summary


_last_attempt = 0.0


def sync_if_due():
    """Called by the background heartbeat; honours the configured interval and never raises."""
    global _last_attempt
    cfg = _cfg()
    if not cfg or not cfg['mappings'] or time.time() - _last_attempt < cfg['minutes'] * 60:
        return
    _last_attempt = time.time()
    try:
        sync()
    except HAError as e:
        log.warning('Home Assistant sync failed: %s', e)
        db = get_db()
        db.execute('UPDATE Settings SET haLastResult=? WHERE id=1', [f'Failed: {e}'])
        db.commit()
    except Exception as e:
        log.warning('Home Assistant sync error: %s', e)


_last_refresh = 0.0


def refresh(min_gap=45):
    """Quick pull of the last couple of days, for when the app is opened. Throttled; returns True if anything was written."""
    global _last_refresh
    cfg = _cfg()
    if not cfg or not cfg['mappings'] or time.time() - _last_refresh < min_gap:
        return False
    _last_refresh = time.time()
    try:
        r = sync(days=2)
        return bool(r['weights'] or r['metrics'])
    except Exception as e:
        log.warning('Home Assistant refresh failed: %s', e)
        return False
