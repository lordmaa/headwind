import json
import logging
import socket

log = logging.getLogger(__name__)

DEVICE = {
    "identifiers": ["bike_tracker"],
    "name": "Headwind",
    "model": "bike-flask",
    "manufacturer": "Headwind",
}

SENSORS = [
    # (uid, friendly_name, icon, unit_of_measurement)
    # ── Lifetime totals ──────────────────────────────────────────
    ("total_rides",              "Total Rides",              "mdi:bike",                None),
    ("total_distance_mi",        "Total Distance",            "mdi:map-marker-distance", "mi"),
    ("total_elevation_ft",       "Total Elevation",           "mdi:elevation-rise",      "ft"),
    ("total_calories",           "Total Calories",            "mdi:fire",                "kcal"),
    ("total_time_hours",         "Total Moving Time",         "mdi:clock-outline",       "h"),
    ("everests_climbed",         "Everests Climbed",          "mdi:mountain",            None),
    ("laps_of_earth",            "Laps of Earth",             "mdi:earth",               None),
    # ── Last ride ────────────────────────────────────────────────
    ("last_ride_name",           "Last Ride Name",            "mdi:tag-outline",         None),
    ("last_ride_date",           "Last Ride Date",            "mdi:calendar",            None),
    ("last_ride_sport",          "Last Ride Sport",           "mdi:run",                 None),
    ("last_ride_distance_mi",    "Last Ride Distance",        "mdi:map-marker-distance", "mi"),
    ("last_ride_moving_time",    "Last Ride Moving Time",     "mdi:clock-outline",       None),
    ("last_ride_elevation_ft",   "Last Ride Elevation",       "mdi:elevation-rise",      "ft"),
    ("last_ride_avg_speed_mph",  "Last Ride Avg Speed",       "mdi:speedometer",         "mph"),
    ("last_ride_avg_hr",         "Last Ride Avg Heart Rate",  "mdi:heart-pulse",         "bpm"),
    ("last_ride_avg_watts",      "Last Ride Avg Power",       "mdi:lightning-bolt",      "W"),
    ("last_ride_calories",       "Last Ride Calories",        "mdi:fire",                "kcal"),
    # ── Trophy case ──────────────────────────────────────────────
    ("badges_earned",            "Badges Earned",             "mdi:trophy",              None),
    ("badges_total",             "Total Badges",              "mdi:trophy-outline",      None),
]

GARMIN_SENSORS = [
    # (uid, friendly_name, icon, unit_of_measurement)
    ("garmin_body_battery", "Recovery Body Battery", "mdi:battery-heart-variant", "%"),
    ("garmin_resting_hr",   "Recovery Resting HR",   "mdi:heart-pulse",           "bpm"),
    ("garmin_hrv",          "Recovery HRV",           "mdi:heart-flash",           None),
    ("garmin_sleep_hours",  "Recovery Sleep",         "mdi:sleep",                 "h"),
    ("garmin_sleep_score",  "Recovery Sleep Score",   "mdi:star-circle",           None),
    ("garmin_steps",        "Recovery Daily Steps",   "mdi:walk",                  None),
    ("garmin_stress",       "Recovery Stress",        "mdi:head-dots-horizontal",  None),
]


def _fmt_duration(secs):
    secs = int(secs or 0)
    h, r = divmod(secs, 3600)
    m, s = divmod(r, 60)
    return f'{h}:{m:02d}:{s:02d}' if h else f'{m}:{s:02d}'


def _gather_garmin():
    from database import query_db
    row = query_db(
        'SELECT restingHR, hrv, sleepHours, sleepScore, bodyBattery, steps, stressScore '
        'FROM GarminDaily ORDER BY date DESC LIMIT 1',
        one=True,
    )
    return dict(row) if row else {}


def _garmin_state_values(g):
    def v(val):
        return str(val) if val is not None else 'unknown'
    return {
        "garmin_body_battery": v(g.get('bodyBattery')),
        "garmin_resting_hr":   v(g.get('restingHR')),
        "garmin_hrv":          v(g.get('hrv')),
        "garmin_sleep_hours":  v(g.get('sleepHours')),
        "garmin_sleep_score":  v(g.get('sleepScore')),
        "garmin_steps":        v(g.get('steps')),
        "garmin_stress":       v(g.get('stressScore')),
    }


def _state_values(stats):
    dist_m  = stats.get('dist')  or 0
    elev_m  = stats.get('elev')  or 0
    secs    = stats.get('secs')  or 0
    cals    = stats.get('cals')  or 0
    rides   = stats.get('rides') or 0

    last_dist  = stats.get('last_dist')  or 0
    last_elev  = stats.get('last_elev')  or 0
    last_time  = stats.get('last_time')  or 0
    last_speed = stats.get('last_speed') or 0
    last_hr    = stats.get('last_hr')
    last_watts = stats.get('last_watts')
    last_cals  = stats.get('last_cals')

    return {
        # totals
        "total_rides":             str(rides),
        "total_distance_mi":       str(round(dist_m / 1609.344, 1)),
        "total_elevation_ft":      str(round(elev_m * 3.28084)),
        "total_calories":          str(int(cals)),
        "total_time_hours":        str(round(secs / 3600, 1)),
        "everests_climbed":        str(round(elev_m / 8849, 1)),
        "laps_of_earth":           str(round(dist_m / 40_075_016.7, 2)),
        # last ride
        "last_ride_name":          str(stats.get('last_name')  or 'Unknown'),
        "last_ride_date":          str((stats.get('last_date') or '')[:10]),
        "last_ride_sport":         str(stats.get('last_sport') or 'Ride'),
        "last_ride_distance_mi":   str(round(last_dist / 1609.344, 1)),
        "last_ride_moving_time":   _fmt_duration(last_time),
        "last_ride_elevation_ft":  str(round(last_elev * 3.28084)),
        "last_ride_avg_speed_mph": str(round(last_speed * 2.23694, 1)),
        "last_ride_avg_hr":        str(round(last_hr))    if last_hr    else 'unknown',
        "last_ride_avg_watts":     str(round(last_watts)) if last_watts else 'unknown',
        "last_ride_calories":      str(int(last_cals))    if last_cals  else 'unknown',
        "badges_earned":           str(stats.get('badges_earned') or 0),
        "badges_total":            str(stats.get('badges_total')  or 0),
    }


def _gather_stats():
    from database import query_db
    from routes.riders import _compute_badges
    totals = query_db(
        'SELECT COUNT(*) as rides, SUM(distance) as dist, SUM(totalElevationGain) as elev, '
        'SUM(movingTime) as secs, SUM(calories) as cals FROM Activity',
        one=True,
    )
    last = query_db(
        'SELECT name, sportType, startDateLocal, distance, movingTime, '
        'totalElevationGain, averageSpeed, averageHeartrate, averageWatts, calories '
        'FROM Activity ORDER BY startDateLocal DESC LIMIT 1',
        one=True,
    )
    stats = dict(totals)
    if last:
        stats['last_name']  = last['name']
        stats['last_date']  = last['startDateLocal']
        stats['last_sport'] = last['sportType']
        stats['last_dist']  = last['distance']
        stats['last_time']  = last['movingTime']
        stats['last_elev']  = last['totalElevationGain']
        stats['last_speed'] = last['averageSpeed']
        stats['last_hr']    = last['averageHeartrate']
        stats['last_watts'] = last['averageWatts']
        stats['last_cals']  = last['calories']
    # Badge counts for default rider
    owner = query_db('SELECT id FROM Rider WHERE isDefault=1 LIMIT 1', one=True)
    if owner:
        rtotals = query_db(
            'SELECT COUNT(*) as rides, SUM(distance) as dist, SUM(totalElevationGain) as elev '
            'FROM Activity WHERE riderId=?', [owner['id']], one=True)
        if rtotals:
            cats = _compute_badges(owner['id'], rtotals)
            stats['badges_earned'] = sum(1 for cat in cats for b in cat['badges'] if b['earned'])
            stats['badges_total']  = sum(len(cat['badges']) for cat in cats)
    return stats


def _broker_send(msgs, host, port, auth):
    import os
    import paho.mqtt.publish as mqtt_publish
    old = socket.getdefaulttimeout()
    socket.setdefaulttimeout(5)
    try:
        mqtt_publish.multiple(msgs, hostname=host, port=port, auth=auth,
                              client_id=f'bike-tracker-{os.getpid()}')
    finally:
        socket.setdefaulttimeout(old)


def publish(settings, stats):
    """Update all HA sensors. Called by the manual Settings button."""
    host = (settings.get('mqttHost') or '').strip()
    if not host:
        raise ValueError("MQTT host not configured")

    port = int(settings.get('mqttPort') or 1883)
    auth = None
    if settings.get('mqttUser'):
        auth = {'username': settings['mqttUser'],
                'password': settings.get('mqttPassword') or ''}

    states = _state_values(stats)
    garmin_states = _garmin_state_values(_gather_garmin())
    msgs   = []

    for uid, name, icon, unit in SENSORS:
        state_topic  = f"homeassistant/sensor/bike_tracker_{uid}/state"
        config_topic = f"homeassistant/sensor/bike_tracker_{uid}/config"

        config = {
            "name":        name,
            "state_topic": state_topic,
            "unique_id":   f"bike_tracker_{uid}",
            "icon":        icon,
            "device":      DEVICE,
        }
        if unit:
            config["unit_of_measurement"] = unit

        msgs.append({'topic': config_topic, 'payload': json.dumps(config), 'retain': True, 'qos': 1})
        msgs.append({'topic': state_topic,  'payload': states[uid],        'retain': True, 'qos': 1})
        log.info("MQTT queued %s = %s", uid, states[uid])

    for uid, name, icon, unit in GARMIN_SENSORS:
        state_topic  = f"homeassistant/sensor/bike_tracker_{uid}/state"
        config_topic = f"homeassistant/sensor/bike_tracker_{uid}/config"
        config = {"name": name, "state_topic": state_topic,
                  "unique_id": f"bike_tracker_{uid}", "icon": icon, "device": DEVICE}
        if unit:
            config["unit_of_measurement"] = unit
        msgs.append({'topic': config_topic, 'payload': json.dumps(config), 'retain': True, 'qos': 1})
        msgs.append({'topic': state_topic,  'payload': garmin_states[uid], 'retain': True, 'qos': 1})
        log.info("MQTT queued %s = %s", uid, garmin_states[uid])

    _broker_send(msgs, host, port, auth)
    log.warning("MQTT publish complete — %d sensors", len(SENSORS) + len(GARMIN_SENSORS))


def push_update(activity=None):
    """Auto-update sensors and optionally send a ride notification.
    Safe to call from any route — silently no-ops if MQTT not configured."""
    from database import query_db
    try:
        settings = query_db('SELECT * FROM Settings WHERE id=1', one=True)
        if not settings or not (settings['mqttHost'] or '').strip():
            return

        s    = dict(settings)
        host = s['mqttHost'].strip()
        port = int(s.get('mqttPort') or 1883)
        auth = None
        if s.get('mqttUser'):
            auth = {'username': s['mqttUser'], 'password': s.get('mqttPassword') or ''}

        stats  = _gather_stats()
        states = _state_values(stats)
        garmin_states = _garmin_state_values(_gather_garmin())
        msgs   = []

        for uid, name, icon, unit in SENSORS:
            state_topic  = f"homeassistant/sensor/bike_tracker_{uid}/state"
            config_topic = f"homeassistant/sensor/bike_tracker_{uid}/config"
            config = {"name": name, "state_topic": state_topic,
                      "unique_id": f"bike_tracker_{uid}", "icon": icon, "device": DEVICE}
            if unit:
                config["unit_of_measurement"] = unit
            msgs.append({'topic': config_topic, 'payload': json.dumps(config), 'retain': True, 'qos': 1})
            msgs.append({'topic': state_topic,  'payload': states[uid],        'retain': True, 'qos': 1})

        for uid, name, icon, unit in GARMIN_SENSORS:
            state_topic  = f"homeassistant/sensor/bike_tracker_{uid}/state"
            config_topic = f"homeassistant/sensor/bike_tracker_{uid}/config"
            config = {"name": name, "state_topic": state_topic,
                      "unique_id": f"bike_tracker_{uid}", "icon": icon, "device": DEVICE}
            if unit:
                config["unit_of_measurement"] = unit
            msgs.append({'topic': config_topic, 'payload': json.dumps(config), 'retain': True, 'qos': 1})
            msgs.append({'topic': state_topic,  'payload': garmin_states[uid], 'retain': True, 'qos': 1})

        if activity:
            from flask import current_app
            act = dict(activity)

            dist_mi   = round((act.get('distance') or 0) / 1609.344, 1)
            secs      = int(act.get('movingTime') or 0)
            h, r      = divmod(secs, 3600)
            time_str  = f"{h}h {r//60:02d}m" if h else f"{r//60}m"
            speed_mph = round((act.get('averageSpeed') or 0) * 2.23694, 1)
            wx        = act.get('weatherSummary') or ''

            rider_name = 'Rider'
            ha_device  = None
            if act.get('riderId'):
                rider = query_db('SELECT name, haDevice FROM Rider WHERE id=?', [act['riderId']], one=True)
                if rider:
                    rider_name = rider['name']
                    ha_device  = rider['haDevice']

            app_url = current_app.config.get('APP_URL', '').rstrip('/')
            url     = f"{app_url}/rides/{act.get('id', '')}"
            title   = f"🚴 {rider_name}: {act.get('name') or 'New Ride'}"
            message = f"{dist_mi} mi · {time_str} · {speed_mph} mph"
            if wx:
                message += f" · {wx}"

            payload = {'title': title, 'message': message, 'url': url}
            if ha_device:
                payload['ha_device'] = ha_device

            msgs.append({
                'topic':   'bike_tracker/new_ride',
                'payload': json.dumps(payload),
                'qos': 1,
            })

        _broker_send(msgs, host, port, auth)
        log.warning("MQTT push_update complete — sensors updated%s",
                    ", ride notification sent" if activity else "")

    except Exception as e:
        log.warning("MQTT push_update failed (non-fatal): %s", e)
