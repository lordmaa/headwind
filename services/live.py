"""Live ride: the phone streams position, speed and motion-sensor readings while it records, and this turns them into Home Assistant entities.

The phone never talks to MQTT itself when it is paired with a server (only one publisher per broker, or sensors overwrite each other), so it
POSTs /api/v1/live every few seconds and this module publishes: a handful of sensors (status, speed, distance, elapsed, elevation, roughness,
lean, pressure, phone battery) plus a device_tracker whose attributes carry the position, so the HA map card can follow the ride.
A final {"state": "idle"} ends it; if the stream just stops (phone died, no signal) a timer marks the ride idle after IDLE_AFTER_S.
"""
import json
import logging
import threading
import time

log = logging.getLogger(__name__)

IDLE_AFTER_S = 90
MIN_PUBLISH_GAP_S = 2.0

SENSORS = [   # (uid, name, icon, unit)
    ('live_ride_status',        'Live Ride Status',        'mdi:bike-fast',        None),
    ('live_ride_sport',         'Live Ride Sport',         'mdi:run',              None),
    ('live_ride_speed_mph',     'Live Ride Speed',         'mdi:speedometer',      'mph'),
    ('live_ride_distance_mi',   'Live Ride Distance',      'mdi:map-marker-distance', 'mi'),
    ('live_ride_elapsed',       'Live Ride Elapsed',       'mdi:timer-outline',    None),
    ('live_ride_elevation_ft',  'Live Ride Elevation',     'mdi:elevation-rise',   'ft'),
    ('live_ride_roughness',     'Live Ride Roughness',     'mdi:road-variant',     'm/s²'),
    ('live_ride_lean_deg',      'Live Ride Lean',          'mdi:angle-acute',      '°'),
    ('live_ride_pressure_hpa',  'Live Ride Pressure',      'mdi:gauge',            'hPa'),
    ('live_ride_phone_battery', 'Live Ride Phone Battery', 'mdi:cellphone',        '%'),
]
TRACKER = ('live_ride_position', 'Live Ride Position', 'mdi:bike')

_lock = threading.Lock()
_state = {'last': None, 'last_pub': 0.0, 'timer': None, 'sent_discovery': False, 'rider': None}


def _fmt_elapsed(secs):
    secs = int(max(0, secs or 0))
    return '%d:%02d:%02d' % (secs // 3600, (secs % 3600) // 60, secs % 60)


def _f(v, nd=1):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if x != x else round(x, nd)


def clean(payload):
    """The fields we trust from the phone, converted to what Home Assistant shows (miles, mph, feet)."""
    p = payload if isinstance(payload, dict) else {}
    state = 'riding' if str(p.get('state') or 'riding') != 'idle' else 'idle'
    spd = _f(p.get('speed_mps'), 3)
    dist = _f(p.get('distance_m'), 1)
    alt = _f(p.get('alt_m'), 1)
    sens = p.get('sensors') if isinstance(p.get('sensors'), dict) else {}
    lat, lng = _f(p.get('lat'), 6), _f(p.get('lng'), 6)
    if lat is not None and lng is not None and not (-90 <= lat <= 90 and -180 <= lng <= 180):
        lat = lng = None
    return {
        'state': state, 'sport': str(p.get('sport') or '')[:20], 'ts': time.time(),
        'lat': lat, 'lng': lng, 'acc': _f(p.get('accuracy_m'), 0),
        'speed_mph': None if spd is None else round(spd * 2.23694, 1),
        'distance_mi': None if dist is None else round(dist / 1609.344, 2),
        'elapsed': _fmt_elapsed(p.get('elapsed_s')),
        'elevation_ft': None if alt is None else round(alt * 3.28084),
        'roughness': _f(sens.get('rough'), 2), 'lean': _f(sens.get('tilt'), 1), 'pressure': _f(sens.get('press'), 1),
        'battery': _f(p.get('battery_pct'), 0),
    }


def states_for(c):
    """{uid: state string} for the sensors; missing readings are 'unknown'."""
    riding = c['state'] == 'riding'
    s = lambda v: 'unknown' if v is None else str(v)
    return {
        'live_ride_status': c['state'], 'live_ride_sport': c['sport'] or 'unknown',
        'live_ride_speed_mph': s(c['speed_mph'] if riding else 0), 'live_ride_distance_mi': s(c['distance_mi']),
        'live_ride_elapsed': c['elapsed'], 'live_ride_elevation_ft': s(c['elevation_ft']),
        'live_ride_roughness': s(c['roughness']), 'live_ride_lean_deg': s(c['lean']),
        'live_ride_pressure_hpa': s(c['pressure']), 'live_ride_phone_battery': s(c['battery']),
    }


def tracker_attrs(c):
    a = {'source_type': 'gps'}
    if c['lat'] is not None and c['lng'] is not None:
        a.update(latitude=c['lat'], longitude=c['lng'], gps_accuracy=c['acc'] or 10)
    return a


def build_messages(c, with_discovery):
    """The MQTT messages for one update (pure: no network)."""
    from services import mqtt
    msgs = []
    if with_discovery:
        for uid, name, icon, unit in SENSORS:
            config_topic, state_topic, config_json = mqtt._discovery(uid, name, icon, unit)
            msgs.append({'topic': config_topic, 'payload': config_json, 'retain': True, 'qos': 1})
        p = mqtt._prefix()
        uid, name, icon = TRACKER
        cfg = {'name': name, 'unique_id': f'{p}_{uid}', 'icon': icon, 'device': mqtt._device(),
               'state_topic': f'homeassistant/device_tracker/{p}_{uid}/state', 'json_attributes_topic': f'homeassistant/device_tracker/{p}_{uid}/attributes',
               'payload_home': 'home', 'payload_not_home': 'not_home', 'source_type': 'gps'}
        if p != mqtt._DEFAULT_PREFIX:
            cfg['default_entity_id'] = f'device_tracker.{p}_{uid}'
        msgs.append({'topic': f'homeassistant/device_tracker/{p}_{uid}/config', 'payload': json.dumps(cfg), 'retain': True, 'qos': 1})
    for uid, val in states_for(c).items():
        msgs.append({'topic': f'homeassistant/sensor/{mqtt._prefix()}_{uid}/state', 'payload': val, 'retain': True, 'qos': 1})
    p = mqtt._prefix()
    uid = TRACKER[0]
    msgs.append({'topic': f'homeassistant/device_tracker/{p}_{uid}/attributes', 'payload': json.dumps(tracker_attrs(c)), 'retain': True, 'qos': 1})
    msgs.append({'topic': f'homeassistant/device_tracker/{p}_{uid}/state', 'payload': 'not_home', 'retain': True, 'qos': 1})
    return msgs


def _connection():
    from database import query_db
    s = query_db('SELECT mqttHost, mqttPort, mqttUser, mqttPassword FROM Settings WHERE id=1', one=True)
    if not s or not (s['mqttHost'] or '').strip():
        return None
    auth = {'username': s['mqttUser'], 'password': s['mqttPassword'] or ''} if s['mqttUser'] else None
    return s['mqttHost'].strip(), int(s['mqttPort'] or 1883), auth


def _publish(c, sender=None, conn=None):
    conn = conn if conn is not None else _connection()
    if not conn:
        return False
    from services import mqtt
    msgs = build_messages(c, with_discovery=not _state['sent_discovery'])
    try:
        (sender or mqtt._broker_send)(msgs, *conn)
        _state['sent_discovery'] = True
        _state['last_pub'] = time.time()
        return True
    except Exception as e:
        log.warning('live ride publish failed (non-fatal): %s', e)
        return False


def update(payload, sender=None, conn=None, app=None):
    """Handle one POST from the phone. Returns the cleaned reading. Publishes at most every MIN_PUBLISH_GAP_S, but always publishes idle."""
    c = clean(payload)
    with _lock:
        prev = _state['last']
        _state['last'] = c
        due = c['state'] == 'idle' or (time.time() - _state['last_pub']) >= MIN_PUBLISH_GAP_S or prev is None or prev['state'] != c['state']
        if due:
            _publish(c, sender, conn)
        t = _state['timer']
        if t:
            t.cancel()
        if c['state'] == 'riding':
            t = threading.Timer(IDLE_AFTER_S, _idle_check, [c['ts'], sender, conn])
            t.daemon = True
            t.start()
            _state['timer'] = t
        else:
            _state['timer'] = None
    return c


def _idle_check(ts, sender=None, conn=None):
    """No update for IDLE_AFTER_S: the phone went away. Mark the ride idle (keeps the last position)."""
    with _lock:
        last = _state['last']
        if not last or last['ts'] != ts or last['state'] == 'idle':
            return
        last = dict(last, state='idle', ts=time.time())
        _state['last'] = last
        _publish(last, sender, conn)


def snapshot():
    return _state['last']
