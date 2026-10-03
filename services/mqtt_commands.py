"""Listens for commands from Home Assistant on MQTT (e.g. a notification button that says "I drank 250 ml").

Topic  headwind/cmd/water   payload  {"ml": 250, "id": "<unique per press>"}

The id makes it safe if a message is ever delivered twice (or two workers listened): each id is applied once.
"""
import json
import logging
import threading
import time

log = logging.getLogger(__name__)
def _client_id():
    """Distinct per instance (own database) so two Headwinds on one broker don't kick each other off."""
    import hashlib
    from config import Config
    return 'headwind-commands-' + hashlib.sha1(Config.DATABASE.encode()).hexdigest()[:8]


def water_topic():
    """'headwind/cmd/water' for the first instance; '<prefix>/cmd/water' when HEADWIND_MQTT_PREFIX is set (second instance),
    so a water button only ever reaches the instance it belongs to."""
    import os
    p = os.environ.get('HEADWIND_MQTT_PREFIX')
    return f'{p}/cmd/water' if p else 'headwind/cmd/water'


WATER_TOPIC = water_topic()      # read once at import; config.py has loaded the env file by the time this module is imported


def _apply_water(app, payload):
    try:
        d = json.loads(payload)
        ml = int(d['ml'])
        cid = str(d.get('id') or '')
    except (ValueError, KeyError, TypeError):
        log.warning('MQTT water command ignored (bad payload): %r', payload[:100])
        return
    if not 50 <= ml <= 1500:
        log.warning('MQTT water command ignored (ml out of range): %s', ml)
        return
    with app.app_context():
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from database import get_db, query_db
        db = get_db()
        db.execute('CREATE TABLE IF NOT EXISTS CmdSeen (id TEXT PRIMARY KEY, seenAt TEXT DEFAULT (datetime(\'now\')))')
        if cid:
            cur = db.execute('INSERT OR IGNORE INTO CmdSeen (id) VALUES (?)', [cid])
            if cur.rowcount == 0:
                db.commit()
                return  # already applied
            db.execute("DELETE FROM CmdSeen WHERE seenAt < datetime('now', '-2 days')")
        rider = query_db('SELECT id FROM Rider WHERE isDefault=1 LIMIT 1', one=True)
        if not rider:
            return
        today = datetime.now(ZoneInfo('Europe/London')).date().isoformat()
        db.execute('INSERT INTO HydrationLog (riderId, logDate, ml) VALUES (?, ?, ?)', [rider['id'], today, ml])
        db.commit()
        log.warning('Water +%d ml logged from Home Assistant', ml)
        try:
            from services.mqtt import push_update_nutrition
            push_update_nutrition()
        except Exception:
            pass


def _make_client(app, host, port, user, pw):
    import paho.mqtt.client as mqtt
    try:
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=_client_id())
        v2 = True
    except AttributeError:
        c = mqtt.Client(client_id=_client_id())
        v2 = False
    if user:
        c.username_pw_set(user, pw or '')

    def on_connect(client, ud, flags, rc, *a):
        client.subscribe(WATER_TOPIC, qos=1)

    def on_message(client, ud, msg):
        if msg.topic == WATER_TOPIC:
            _apply_water(app, msg.payload.decode('utf-8', 'replace'))

    c.on_connect, c.on_message = on_connect, on_message
    c.reconnect_delay_set(min_delay=2, max_delay=60)
    c.connect_async(host, port, keepalive=30)
    c.loop_start()
    return c


def run(app):
    """Background thread: keep one subscriber connected, following changes to the MQTT settings."""
    time.sleep(15)
    client, current = None, None
    while True:
        try:
            with app.app_context():
                from database import query_db
                s = query_db('SELECT mqttHost, mqttPort, mqttUser, mqttPassword FROM Settings WHERE id=1', one=True)
                cfg = None
                if s and (s['mqttHost'] or '').strip():
                    cfg = ((s['mqttHost'] or '').strip(), int(s['mqttPort'] or 1883), s['mqttUser'] or '', s['mqttPassword'] or '')
            if cfg != current:
                if client:
                    client.loop_stop()
                    client.disconnect()
                    client = None
                if cfg:
                    client = _make_client(app, *cfg)
                current = cfg
        except Exception as e:
            log.warning('MQTT command listener error: %s', e)
        time.sleep(30)
