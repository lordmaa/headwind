import json
import os
import logging
import socket

log = logging.getLogger(__name__)

# Cache of last-published state values (uid -> value string).
# Only sensors whose value changes get re-published; if nothing changes
# the broker is not contacted at all, keeping HA recorder writes minimal.
_last_state: dict = {}

# Outage tracking so the background heartbeat backs off instead of retrying
# an unreachable broker every cycle. Event-driven pushes always attempt.
_fail_count = 0
_next_retry = 0.0
_BACKOFF_STEPS = (60, 300, 900)  # seconds

# A second Headwind instance on the same broker (e.g. a partner's) must not collide with the first: HEADWIND_MQTT_PREFIX gives
# it its own topics, unique ids, device and entity ids (`sensor.<prefix>_<uid>`). Unset = the original "bike_tracker" namespace,
# so the first instance's HA entities are untouched. HEADWIND_MQTT_NUTRITION=0 publishes only the ride sensors.
# Read lazily (functions, not module constants) so the env file loaded by config.py is always in effect.
_DEFAULT_PREFIX = 'bike_tracker'
_RIDE_ONLY_NUTRITION_UIDS = {'nutrition_ride_months', 'nutrition_ride_years',        # the Riding charts live in this list
                             'nutrition_steps_today', 'nutrition_steps_goal', 'nutrition_steps_avg_7d',   # steps (phone / watch / manual)
                             'nutrition_steps_source', 'nutrition_recovery_history'}


def _prefix():
    return os.environ.get('HEADWIND_MQTT_PREFIX') or _DEFAULT_PREFIX


def _device():
    p = _prefix()
    return {
        "identifiers": [p],
        "name": os.environ.get('HEADWIND_MQTT_NAME') or ('Headwind' if p == _DEFAULT_PREFIX else 'Headwind ' + p.replace('headwind_', '').replace('_', ' ').title()),
        "model": "bike-flask",
        "manufacturer": "Headwind",
    }


def _nutrition_sensors():
    if os.environ.get('HEADWIND_MQTT_NUTRITION', '1') == '0':
        return [s for s in NUTRITION_SENSORS if s[0] in _RIDE_ONLY_NUTRITION_UIDS]
    return NUTRITION_SENSORS

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

FIBRE_GOAL_G = 30   # UK recommendation; no fibre field on the Goals screen yet

# Meals as logged in Headwind (key -> label). Each gets calories / protein / carbs / fat / fibre sensors.
NUTRITION_MEALS = [
    ("breakfast", "Breakfast"), ("lunch", "Lunch"), ("dinner", "Dinner"),
    ("snacks", "Snacks"), ("ride_fuel", "Ride Fuel"), ("recovery", "Recovery"),
]
_MEAL_METRICS = [  # (suffix, label, icon, unit)
    ("calories", "Calories", "mdi:food",        "kcal"),
    ("protein",  "Protein",  "mdi:food-steak",  "g"),
    ("carbs",    "Carbs",    "mdi:bread-slice", "g"),
    ("fat",      "Fat",      "mdi:oil",         "g"),
    ("fibre",    "Fibre",    "mdi:grain",       "g"),
]

NUTRITION_SENSORS = [
    # (uid, friendly_name, icon, unit_of_measurement)
    # ── Day totals ───────────────────────────────────────────────
    ("nutrition_calories_eaten",     "Nutrition Calories Eaten",     "mdi:food",              "kcal"),
    ("nutrition_calories_burned",    "Nutrition Calories Burned",    "mdi:fire",              "kcal"),
    ("nutrition_calories_net",       "Nutrition Calories Net",       "mdi:scale-balance",     "kcal"),
    ("nutrition_calories_remaining", "Nutrition Calories Remaining", "mdi:target",            "kcal"),
    ("nutrition_protein_g",          "Nutrition Protein",            "mdi:food-steak",        "g"),
    ("nutrition_protein_remaining",  "Nutrition Protein Remaining",  "mdi:target",            "g"),
    ("nutrition_carbs_g",            "Nutrition Carbs",              "mdi:bread-slice",       "g"),
    ("nutrition_carbs_remaining",    "Nutrition Carbs Remaining",    "mdi:target",            "g"),
    ("nutrition_fat_g",              "Nutrition Fat",                "mdi:oil",               "g"),
    ("nutrition_fat_remaining",      "Nutrition Fat Remaining",      "mdi:target",            "g"),
    ("nutrition_fibre_g",            "Nutrition Fibre",              "mdi:grain",             "g"),
    ("nutrition_fibre_remaining",   "Nutrition Fibre Remaining",    "mdi:target",            "g"),
    ("nutrition_water_ml",           "Nutrition Water",              "mdi:water",             "ml"),
    # ── Goals (from Headwind's Goals screen) ─────────────────────
    ("nutrition_calories_goal",      "Nutrition Calories Goal",      "mdi:flag-checkered",    "kcal"),
    ("nutrition_protein_goal",       "Nutrition Protein Goal",       "mdi:flag-checkered",    "g"),
    ("nutrition_carbs_goal",         "Nutrition Carbs Goal",         "mdi:flag-checkered",    "g"),
    ("nutrition_fat_goal",           "Nutrition Fat Goal",           "mdi:flag-checkered",    "g"),
    ("nutrition_fibre_goal",         "Nutrition Fibre Goal",         "mdi:flag-checkered",    "g"),
    ("nutrition_water_goal",         "Nutrition Water Goal",         "mdi:flag-checkered",    "ml"),
    ("nutrition_garmin_last_sync",   "Nutrition Garmin Last Sync",   "mdi:watch-import",      None),
    ("nutrition_garmin_status",      "Nutrition Garmin Status",      "mdi:watch-variant",     None),
    ("nutrition_steps_today",        "Nutrition Steps Today",        "mdi:shoe-print",        "steps"),
    ("nutrition_steps_source",       "Nutrition Steps Source",       "mdi:source-branch",     None),
    ("nutrition_garmin_watch_sync",  "Nutrition Garmin Watch Sync",  "mdi:watch-import",      None),
    ("nutrition_steps_goal",         "Nutrition Steps Goal",         "mdi:flag-checkered",    "steps"),
    ("nutrition_steps_avg_7d",       "Nutrition Steps 7 Day Average", "mdi:shoe-print",       "steps"),
    ("nutrition_calorie_pct",        "Nutrition Calorie Progress",   "mdi:percent",           "%"),
    ("nutrition_water_pct",          "Nutrition Water Progress",     "mdi:water-percent",     "%"),
    # ── Chart data for the HA dashboard: state is a content hash, the series live in the attributes ──
    ("nutrition_weight_history",    "Nutrition Weight History",     "mdi:chart-line",        None),
    ("nutrition_diary_history",     "Nutrition Diary History",      "mdi:chart-bar",         None),
    ("nutrition_recovery_history",  "Nutrition Recovery History",   "mdi:heart-pulse",       None),
    ("nutrition_reality_check",     "Nutrition Reality Check",      "mdi:scale-balance",     None),
    ("nutrition_goal_projection",   "Nutrition Goal Projection",    "mdi:flag-checkered",    None),
    ("nutrition_plan",              "Nutrition Plan",               "mdi:clipboard-text",    None),
    ("nutrition_activity_weekly",   "Nutrition Activity Weekly",    "mdi:chart-timeline-variant", None),
    ("nutrition_activity_status",   "Nutrition Activity Status",    "mdi:chart-line-variant", None),
    ("nutrition_ride_months",       "Nutrition Ride Months",        "mdi:calendar-month",    None),
    ("nutrition_ride_years",        "Nutrition Ride Years",         "mdi:calendar-range",    None),
    # ── Weight (trend is Headwind's smoothed figure; the raw scale reading is already in HA) ──
    ("nutrition_weight_trend_kg",    "Nutrition Weight Trend",       "mdi:scale-bathroom",    "kg"),
    ("nutrition_weight_rate_kg_wk",  "Nutrition Weight Change per Week", "mdi:trending-down", "kg"),
] + [
    # ── Per meal ─────────────────────────────────────────────────
    (f"nutrition_{mk}_{suffix}", f"Nutrition {ml} {label}", icon, unit)
    for mk, ml in NUTRITION_MEALS for suffix, label, icon, unit in _MEAL_METRICS
]

# uids that can legitimately be 'unknown' — everything else is always numeric, so HA can keep long-term statistics
HISTORY_UIDS = {"nutrition_weight_history", "nutrition_diary_history", "nutrition_recovery_history", "nutrition_reality_check", "nutrition_goal_projection", "nutrition_plan", "nutrition_activity_weekly", "nutrition_ride_months", "nutrition_ride_years"}
_NON_NUMERIC_OK = HISTORY_UIDS | {"nutrition_activity_status", "nutrition_garmin_watch_sync", "nutrition_steps_source", "nutrition_garmin_last_sync", "nutrition_garmin_status", "nutrition_calories_burned", "nutrition_weight_trend_kg", "nutrition_weight_rate_kg_wk"}


def _discovery(uid, name, icon, unit):
    """(config_topic, state_topic, config_json) for one sensor."""
    p = _prefix()
    state_topic  = f"homeassistant/sensor/{p}_{uid}/state"
    config_topic = f"homeassistant/sensor/{p}_{uid}/config"
    config = {"name": name, "state_topic": state_topic,
              "unique_id": f"{p}_{uid}", "icon": icon, "device": _device()}
    if p != _DEFAULT_PREFIX:
        config["default_entity_id"] = f"sensor.{p}_{uid}"      # predictable entity ids for a second instance
    if unit:
        config["unit_of_measurement"] = unit
    if uid in ("nutrition_garmin_last_sync", "nutrition_garmin_watch_sync"):
        config["device_class"] = "timestamp"
    if uid in HISTORY_UIDS:
        config["json_attributes_topic"] = f"homeassistant/sensor/{p}_{uid}/attributes"
    elif uid.startswith("nutrition_") and uid not in _NON_NUMERIC_OK:
        config["state_class"] = "measurement"
    return config_topic, state_topic, json.dumps(config)


_GENERIC_RIDE_NAMES = {'', 'ride', 'activity', 'ride activity', 'cycling', 'new ride', 'morning ride', 'afternoon ride', 'evening ride', 'lunch ride', 'night ride'}


def ride_summary(act, rider_name, segment_prs=0, ytd=None):
    """(title, message) for the "ride added" push: a readable title and a few lines of stats. `ytd` = (rides, miles, year)."""
    dist_mi = (act.get('distance') or 0) / 1609.344
    secs = int(act.get('movingTime') or 0)
    h, r = divmod(secs, 3600)
    dur = f"{h}h {r // 60:02d}m" if h else f"{r // 60}m"
    hour = None
    try:
        hour = int(str(act.get('startDateLocal') or '')[11:13])
    except ValueError:
        pass
    part = ('Morning' if hour < 12 else 'Afternoon' if hour < 17 else 'Evening') if hour is not None else None
    name = (act.get('name') or '').strip()
    label = name if name.lower() not in _GENERIC_RIDE_NAMES else (f'{part} ride' if part else 'New ride')
    title = f"🚴 {rider_name}: {label} · {dist_mi:.1f} mi"

    lines = []
    speed = (act.get('averageSpeed') or 0) * 2.23694
    top = (act.get('maxSpeed') or 0) * 2.23694
    line = f"{dist_mi:.1f} mi · {dur}"
    if speed:
        # imported ride files store the average in the max-speed field, so only show a top speed that is clearly above the average
        line += f" · {speed:.1f} mph avg" + (f" (top {top:.0f})" if top > speed * 1.08 else '')
    lines.append(line)
    extras = []
    if act.get('totalElevationGain'):
        extras.append(f"⛰ {round(act['totalElevationGain'] * 3.28084):,} ft")
    if act.get('calories'):
        extras.append(f"🔥 {round(act['calories']):,} kcal")
    if extras:
        lines.append(' · '.join(extras))
    body = []
    if act.get('averageHeartrate'):
        body.append(f"❤️ {round(act['averageHeartrate'])} bpm")
    if act.get('averageWatts'):
        body.append(f"⚡ {round(act['averageWatts'])} W")
    if act.get('averageCadence'):
        body.append(f"🦶 {round(act['averageCadence'])} rpm")
    if body:
        lines.append(' · '.join(body))
    if act.get('weatherSummary'):
        lines.append(f"☁️ {act['weatherSummary']}")
    tail = []
    if segment_prs:
        tail.append(f"🏆 {segment_prs} segment PR{'s' if segment_prs != 1 else ''}")
    if ytd and ytd[0]:
        tail.append(f"📅 {ytd[2]}: {round(ytd[1]):,} mi in {ytd[0]} rides")
    if tail:
        lines.append(' · '.join(tail))
    return title, '\n'.join(lines)


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


def _gather_nutrition():
    from database import query_db
    from datetime import date, datetime
    from zoneinfo import ZoneInfo
    # The box's own timezone isn't UK; "today" (and so the midnight reset) must follow the diary's dates
    today_d = datetime.now(ZoneInfo('Europe/London')).date()
    today = today_d.isoformat()
    rider = query_db('SELECT id FROM Rider WHERE isDefault=1 LIMIT 1', one=True)
    if not rider:
        return {}
    rid = rider['id']
    food = query_db('''
        SELECT COALESCE(SUM(calories),0) as cal, COALESCE(SUM(protein),0) as prot,
               COALESCE(SUM(carbs),0) as carbs, COALESCE(SUM(fat),0) as fat, COALESCE(SUM(fibre),0) as fibre
        FROM FoodLog WHERE riderId=? AND logDate=?
    ''', [rid, today], one=True)
    meal_rows = query_db('''
        SELECT COALESCE(NULLIF(mealType,''),'uncategorised') as meal, COALESCE(SUM(calories),0) as cal,
               COALESCE(SUM(protein),0) as prot, COALESCE(SUM(carbs),0) as carbs,
               COALESCE(SUM(fat),0) as fat, COALESCE(SUM(fibre),0) as fibre
        FROM FoodLog WHERE riderId=? AND logDate=? GROUP BY meal
    ''', [rid, today])
    meals = {r['meal']: {'calories': r['cal'], 'protein': r['prot'], 'carbs': r['carbs'], 'fat': r['fat'], 'fibre': r['fibre']}
             for r in meal_rows}
    water = query_db(
        'SELECT COALESCE(SUM(ml),0) as ml FROM HydrationLog WHERE riderId=? AND logDate=?',
        [rid, today], one=True,
    )
    garmin = query_db('SELECT totalCalories FROM GarminDaily WHERE date=?', [today], one=True)
    rides  = query_db('''
        SELECT COALESCE(SUM(calories),0) as cal FROM Activity
        WHERE riderId=? AND date(startDateLocal)=? AND calories IS NOT NULL
    ''', [rid, today], one=True)
    from services import workouts as _wk
    wk_cal = _wk.calories_on(rid, today)                          # recorded walks / runs count toward today's burn
    settings = query_db(
        'SELECT nutritionCalGoal, nutritionProteinGoal, nutritionCarbGoal, nutritionFatGoal, nutritionWaterGoalMl, nutritionWeightEmaAlpha, nutritionBmrKcal FROM Settings WHERE id=1',
        one=True,
    )
    # Same priority as the Nutrition page (routes/nutrition.py day_log), so HA and Headwind always agree:
    #   1. manual BMR + today's ride calories   2. Garmin daily total   3. ride calories only
    ride_cal = (int(rides['cal']) if rides and rides['cal'] else 0) + wk_cal
    bmr = int(settings['nutritionBmrKcal']) if settings and settings['nutritionBmrKcal'] else 0
    from services import goal_model
    est = goal_model.day_burn(rid, today)
    if est:
        burned = est[0]
    elif bmr:
        burned = bmr + ride_cal
    elif garmin and garmin['totalCalories']:
        burned = garmin['totalCalories']
    else:
        burned = ride_cal
    eaten  = int(food['cal']) if food else 0

    gl = query_db('SELECT garminLastOk, garminFailCount, garminDeviceSync FROM Settings WHERE id=1', one=True)

    # Steps: goal from Headwind, and the average of the last 7 completed days from Garmin
    steps_goal = (query_db('SELECT stepGoal FROM Settings WHERE id=1', one=True) or {'stepGoal': None})['stepGoal'] or 8000
    from datetime import timedelta as _td

    def best_steps(day_iso):
        """Highest step count seen for a day across Garmin, the phone and the watch (Garmin only counts what the
        watch has uploaded, so it can lag or be low when the watch isn't worn). Returns (steps, source)."""
        cands = {}
        g = query_db('SELECT steps FROM GarminDaily WHERE date=?', [day_iso], one=True)
        if g and g['steps']:
            cands['Garmin'] = g['steps']
        for metric, label in (('steps_phone', 'Phone'), ('steps_watch', 'Pixel Watch'), ('steps_manual', 'Manual')):
            m = query_db('SELECT value FROM BodyMetric WHERE riderId=? AND logDate=? AND metric=?', [rid, day_iso, metric], one=True)
            if m and m['value']:
                cands[label] = m['value']
        if not cands:
            return 0, None
        src = max(cands, key=cands.get)
        return int(cands[src]), src

    steps_today, steps_source = best_steps(today)
    past = [best_steps((today_d - _td(days=i)).isoformat())[0] for i in range(1, 8)]
    past = [x for x in past if x]
    steps_avg = round(sum(past) / len(past)) if past else 0

    # Smoothed weight trend + change over the last 7 days (same maths as the weight graph)
    trend_now = rate = None
    try:
        from services.weight_trend import compute_trend
        entries = []
        for w in query_db('SELECT logDate, weightKg FROM WeightLog WHERE riderId=? ORDER BY logDate', [rid]):
            entries.append((date.fromisoformat(w['logDate']), w['weightKg']))
        if entries:
            alpha = (settings['nutritionWeightEmaAlpha'] if settings else None) or 0.1
            tm = compute_trend(entries, alpha=alpha, end_date=today_d)
            trend_now = tm.get(today_d)
            from services import insights
            rate = insights.weight_rate_kg_per_week(rid)   # regression over the raw weigh-ins, not the lagging trend
    except Exception:
        pass

    return {
        'eaten':        eaten,
        'burned':       burned,
        'net':          eaten - burned,
        'protein':      int(food['prot'])  if food else 0,
        'carbs':        int(food['carbs']) if food else 0,
        'fat':          int(food['fat'])   if food else 0,
        'fibre':        round(food['fibre'], 1) if food else 0,
        'meals':        meals,
        'water_ml':     int(water['ml'])   if water else 0,
        'cal_goal':     settings['nutritionCalGoal']     if settings else None,
        'protein_goal': settings['nutritionProteinGoal'] if settings else None,
        'carbs_goal':   settings['nutritionCarbGoal']    if settings else None,
        'fat_goal':     settings['nutritionFatGoal']     if settings else None,
        'water_goal':   settings['nutritionWaterGoalMl'] if settings else None,
        'history':      _history_payloads(),
        'steps_goal':   steps_goal,
        'garmin_last_ok': gl['garminLastOk'] if gl else None,
        'garmin_watch_sync': gl['garminDeviceSync'] if gl else None,
        'steps_today':  steps_today,
        'steps_source': steps_source,
        'garmin_status':  ('failing' if (gl and (gl['garminFailCount'] or 0) >= 1) else ('ok' if gl and gl['garminLastOk'] else 'unknown')),
        'steps_avg_7d': steps_avg,
        'weight_trend': trend_now,
        'weight_rate':  rate,
    }


def _attr_topic(uid):
    return f"homeassistant/sensor/{_prefix()}_{uid}/attributes"


_hist_cache = {'t': 0.0, 'v': {}}


def _hist_now():
    """History payloads, computed once per publish burst (they are used for both hash and attributes)."""
    import time
    if time.time() - _hist_cache['t'] > 5:
        _hist_cache['v'] = _history_payloads()
        _hist_cache['t'] = time.time()
    return _hist_cache['v']


STEP_METRICS = ('steps_phone', 'steps_watch', 'steps_manual')


def _step_days(rid, start_iso):
    """Days (ISO) with step data from anywhere: Garmin rows plus phone / watch / manually logged entries, so a rider
    without a Garmin still gets a steps history."""
    from database import query_db
    rows = query_db(
        'SELECT date AS d FROM GarminDaily WHERE date>=? UNION '
        'SELECT logDate FROM BodyMetric WHERE riderId=? AND metric IN (?,?,?) AND logDate>=? ORDER BY 1',
        [start_iso, rid, *STEP_METRICS, start_iso])
    return [r['d'] for r in rows]


def _best_steps_for(rid, day_iso, garmin_steps):
    from database import query_db
    vals = [garmin_steps or 0]
    for metric in STEP_METRICS:
        m = query_db('SELECT value FROM BodyMetric WHERE riderId=? AND logDate=? AND metric=?', [rid, day_iso, metric], one=True)
        if m and m['value']:
            vals.append(m['value'])
    return int(max(vals))


def _history_payloads():
    """{uid: compact JSON} — the series behind the dashboard charts (weight, diary, recovery)."""
    from database import query_db
    from datetime import date, datetime, timedelta
    from zoneinfo import ZoneInfo
    today = datetime.now(ZoneInfo('Europe/London')).date()
    rider = query_db('SELECT id FROM Rider WHERE isDefault=1 LIMIT 1', one=True)
    if not rider:
        return {}
    rid = rider['id']
    out = {}

    # weight: every weigh-in (last 180) with its smoothed trend and that day's body fat
    try:
        from services.weight_trend import compute_trend
        rows = query_db('SELECT logDate, weightKg FROM WeightLog WHERE riderId=? ORDER BY logDate', [rid])
        entries = [(date.fromisoformat(r['logDate']), r['weightKg']) for r in rows]
        settings = query_db('SELECT nutritionWeightEmaAlpha, heightCm FROM Settings WHERE id=1', one=True)
        alpha = (settings['nutritionWeightEmaAlpha'] if settings else None) or 0.1
        height_m = ((settings['heightCm'] if settings else None) or 0) / 100
        trend = compute_trend(entries, alpha=alpha, end_date=today) if entries else {}
        fat = {r['logDate']: r['value'] for r in query_db("SELECT logDate, value FROM BodyMetric WHERE riderId=? AND metric='body_fat'", [rid])}
        entries = entries[-180:]
        out['nutrition_weight_history'] = {
            'd': [d.isoformat() for d, _ in entries],
            'w': [round(w, 2) for _, w in entries],
            't': [round(trend[d], 2) if d in trend else None for d, _ in entries],
            'bf': [fat.get(d.isoformat()) for d, _ in entries],
            'bmi': [round(w / height_m ** 2, 1) if height_m else None for _, w in entries],
            'src': [(query_db('SELECT source FROM WeightLog WHERE riderId=? AND logDate=?', [rid, d.isoformat()], one=True) or {'source': ''})['source'] for d, _ in entries],
        }
    except Exception:
        out['nutrition_weight_history'] = {'d': [], 'w': [], 't': [], 'bf': [], 'src': []}

    # diary: last 30 days of daily totals (missing days = 0) + the goals to draw against
    start = today - timedelta(days=29)
    by_day = {r['logDate']: r for r in query_db('''
        SELECT logDate, SUM(calories) k, SUM(protein) p, SUM(carbs) c, SUM(fat) f, SUM(fibre) fi
        FROM FoodLog WHERE riderId=? AND logDate>=? GROUP BY logDate''', [rid, start.isoformat()])}
    days = [start + timedelta(days=i) for i in range(30)]
    g = query_db('SELECT nutritionCalGoal, nutritionProteinGoal, nutritionCarbGoal, nutritionFatGoal FROM Settings WHERE id=1', one=True)
    out['nutrition_diary_history'] = {
        'd': [d.isoformat() for d in days],
        'k': [round((by_day.get(d.isoformat()) or {'k': 0})['k'] or 0) for d in days],
        'p': [round((by_day.get(d.isoformat()) or {'p': 0})['p'] or 0) for d in days],
        'c': [round((by_day.get(d.isoformat()) or {'c': 0})['c'] or 0) for d in days],
        'f': [round((by_day.get(d.isoformat()) or {'f': 0})['f'] or 0) for d in days],
        'fi': [round((by_day.get(d.isoformat()) or {'fi': 0})['fi'] or 0, 1) for d in days],
        'goal': {'k': g['nutritionCalGoal'] if g else None, 'p': g['nutritionProteinGoal'] if g else None,
                 'c': g['nutritionCarbGoal'] if g else None, 'f': g['nutritionFatGoal'] if g else None},
    }

    # recovery: last 30 days from Garmin
    start30 = (today - timedelta(days=29)).isoformat()
    gr = {r['date']: dict(r) for r in query_db('''SELECT date, restingHR, hrv, sleepHours, bodyBattery, steps, stressScore FROM GarminDaily
                     WHERE date>=? ORDER BY date''', [start30])}
    days30 = sorted(set(gr) | set(_step_days(rid, start30)))
    out['nutrition_recovery_history'] = {
        'd': days30, 'hr': [gr.get(d, {}).get('restingHR') for d in days30], 'hrv': [gr.get(d, {}).get('hrv') for d in days30],
        'sl': [gr.get(d, {}).get('sleepHours') for d in days30], 'bb': [gr.get(d, {}).get('bodyBattery') for d in days30],
        'st': [_best_steps_for(rid, d, gr.get(d, {}).get('steps')) for d in days30], 'sx': [gr.get(d, {}).get('stressScore') for d in days30],
    }
    try:
        from services import insights
        out['nutrition_reality_check'] = insights.reality_check(rid)
    except Exception as e:
        log.warning('reality check failed: %s', e)
        out['nutrition_reality_check'] = {'status': 'error'}
    # cycling: this year month by month (the all-time totals stay as sensors; this is the part that helps day to day)
    try:
        yr = today.year
        rows = {r['m']: r for r in query_db('''
            SELECT CAST(strftime('%m', startDateLocal) AS INTEGER) m, COUNT(*) n, SUM(distance) d, SUM(movingTime) t,
                   SUM(totalElevationGain) e, SUM(calories) k
            FROM Activity WHERE riderId=? AND strftime('%Y', startDateLocal)=? GROUP BY m''', [rid, str(yr)])}
        months = list(range(1, today.month + 1))
        pick = lambda key, f: [f((rows.get(m) or {key: 0})[key] or 0) for m in months]
        payload = {
            'year': yr, 'd': [f'{yr}-{m:02d}-01' for m in months],
            'rides': [int((rows.get(m) or {'n': 0})['n']) for m in months],
            'mi': pick('d', lambda v: round(v / 1609.344, 1)), 'h': pick('t', lambda v: round(v / 3600, 1)),
            'ft': pick('e', lambda v: round(v * 3.28084)), 'kcal': pick('k', lambda v: round(v)),
        }
        payload['ytd'] = {'rides': sum(payload['rides']), 'mi': round(sum(payload['mi']), 1), 'h': round(sum(payload['h']), 1),
                          'ft': sum(payload['ft']), 'kcal': sum(payload['kcal'])}
        out['nutrition_ride_months'] = payload
    except Exception as e:
        log.warning('ride months failed: %s', e)
        out['nutrition_ride_months'] = {'year': today.year, 'd': []}
    # cycling: every year, plus how far each year had got by today's date (a like-for-like comparison with this year)
    try:
        md = today.strftime('%m-%d')
        yrs = query_db('''
            SELECT strftime('%Y', startDateLocal) y, COUNT(*) n, SUM(distance) d, SUM(movingTime) t, SUM(totalElevationGain) e,
                   SUM(CASE WHEN strftime('%m-%d', startDateLocal) <= ? THEN distance ELSE 0 END) d2d
            FROM Activity WHERE riderId=? GROUP BY y HAVING n >= 5 ORDER BY y''', [md, rid])   # >=5 rides drops stray mis-dated rides
        out['nutrition_ride_years'] = {
            'd': [f"{r['y']}-01-01" for r in yrs], 'rides': [int(r['n']) for r in yrs],
            'mi': [round((r['d'] or 0) / 1609.344) for r in yrs], 'h': [round((r['t'] or 0) / 3600) for r in yrs],
            'ft': [round((r['e'] or 0) * 3.28084) for r in yrs], 'mi_to_date': [round((r['d2d'] or 0) / 1609.344) for r in yrs],
            'as_of': today.strftime('%-d %b'), 'this_year': today.year,
        }
    except Exception as e:
        log.warning('ride years failed: %s', e)
        out['nutrition_ride_years'] = {'d': []}
    try:
        from services import insights
        out['nutrition_goal_projection'] = insights.projection(rid)
    except Exception as e:
        log.warning('goal projection failed: %s', e)
        out['nutrition_goal_projection'] = {'status': 'error'}
    # activity trend: last 26 weeks (Mon-Sun) of riding and average daily steps, so the card can show when it flattens
    try:
        this_mon = today - timedelta(days=today.weekday())
        mons = [this_mon - timedelta(weeks=k) for k in range(25, -1, -1)]
        first = mons[0].isoformat()
        rides = {r['w']: r for r in query_db('''
            SELECT date(startDateLocal, '-' || ((CAST(strftime('%w', startDateLocal) AS INTEGER) + 6) % 7) || ' days') w,
                   COUNT(*) n, SUM(distance) d, SUM(movingTime) t
            FROM Activity WHERE riderId=? AND date(startDateLocal)>=? GROUP BY w''', [rid, first])}
        steps_by_week = {}
        g_steps = {r['date']: r['steps'] for r in query_db('SELECT date, steps FROM GarminDaily WHERE date>=? AND date<=?', [first, today.isoformat()])}
        for day_iso in sorted(set(g_steps) | {x for x in _step_days(rid, first) if x <= today.isoformat()}):
            d = date.fromisoformat(day_iso)
            v = _best_steps_for(rid, day_iso, g_steps.get(day_iso))
            if v and v > 0:
                steps_by_week.setdefault((d - timedelta(days=d.weekday())).isoformat(), []).append(v)
        out['nutrition_activity_weekly'] = {
            'd': [m.isoformat() for m in mons],
            'rides': [int((rides.get(m.isoformat()) or {'n': 0})['n']) for m in mons],
            'mi': [round(((rides.get(m.isoformat()) or {'d': 0})['d'] or 0) / 1609.344, 1) for m in mons],
            'h': [round(((rides.get(m.isoformat()) or {'t': 0})['t'] or 0) / 3600, 1) for m in mons],
            'steps': [round(sum(steps_by_week[m.isoformat()]) / len(steps_by_week[m.isoformat()])) if m.isoformat() in steps_by_week else None for m in mons],
            'step_days': [len(steps_by_week.get(m.isoformat(), [])) for m in mons],
            'ride_goal': __import__('services.goal_model', fromlist=['x']).profile()['ride_goal_week'],
            'step_goal': (query_db('SELECT stepGoal FROM Settings WHERE id=1', one=True) or {'stepGoal': None})['stepGoal'] or 8000,
        }
    except Exception as e:
        log.warning('activity weekly failed: %s', e)
        out['nutrition_activity_weekly'] = {'d': []}
    try:
        from services import goal_model
        out['nutrition_plan'] = goal_model.plan_status(rid)
    except Exception as e:
        log.warning('plan status failed: %s', e)
        out['nutrition_plan'] = {'status': 'error'}
    return {uid: json.dumps(v, separators=(',', ':')) for uid, v in out.items()}


def _activity_status():
    """'ok' / 'stagnant' / 'unknown' from the weekly activity payload (drives the 'looking stagnant' push in HA)."""
    try:
        from services import insights
        return insights.activity_status(json.loads(_hist_now().get('nutrition_activity_weekly') or '{}'))
    except Exception as e:
        log.warning('activity status failed: %s', e)
        return 'unknown'


def _nutrition_state_values(n):
    def v(val):
        return str(val) if val is not None else 'unknown'
    cal_pct   = round(n['eaten']    / n['cal_goal']   * 100) if n.get('cal_goal')   and n['eaten']    else 0
    water_pct = round(n['water_ml'] / n['water_goal'] * 100) if n.get('water_goal') and n['water_ml'] else 0
    out = {
        'nutrition_calories_eaten':     v(n['eaten']),
        'nutrition_calories_burned':    v(n['burned']) if n.get('burned') else 'unknown',
        'nutrition_calories_net':       v(n['net']),
        'nutrition_calories_remaining': v(max(0, n['cal_goal'] - n['eaten']) if n.get('cal_goal') else 0),
        'nutrition_protein_g':          v(n['protein']),
        'nutrition_protein_remaining':  v(max(0, n['protein_goal'] - n['protein']) if n.get('protein_goal') else 0),
        'nutrition_carbs_g':            v(n['carbs']),
        'nutrition_fat_g':              v(n['fat']),
        'nutrition_fibre_g':            v(n.get('fibre', 0)),
        'nutrition_carbs_remaining':    v(max(0, n['carbs_goal'] - n['carbs']) if n.get('carbs_goal') else 0),
        'nutrition_fat_remaining':      v(max(0, n['fat_goal'] - n['fat']) if n.get('fat_goal') else 0),
        'nutrition_fibre_remaining':    v(round(max(0, FIBRE_GOAL_G - (n.get('fibre') or 0)), 1)),
        'nutrition_calories_goal':      v(n.get('cal_goal') or 0),
        'nutrition_protein_goal':       v(n.get('protein_goal') or 0),
        'nutrition_carbs_goal':         v(n.get('carbs_goal') or 0),
        'nutrition_fat_goal':           v(n.get('fat_goal') or 0),
        'nutrition_fibre_goal':         v(FIBRE_GOAL_G),
        'nutrition_water_goal':         v(n.get('water_goal') or 0),
        'nutrition_activity_status':    _activity_status(),
        'nutrition_steps_goal':         v(n.get('steps_goal') or 0),
        'nutrition_steps_today':        v(n.get('steps_today') or 0),
        'nutrition_steps_source':       n.get('steps_source') or 'unknown',
        'nutrition_garmin_watch_sync':  n.get('garmin_watch_sync') or 'unknown',
        'nutrition_garmin_last_sync':   n.get('garmin_last_ok') or 'unknown',
        'nutrition_garmin_status':      n.get('garmin_status') or 'unknown',
        'nutrition_steps_avg_7d':       v(n.get('steps_avg_7d') or 0),
        'nutrition_water_ml':           v(n['water_ml']),
        'nutrition_calorie_pct':        v(cal_pct),
        'nutrition_water_pct':          v(water_pct),
        'nutrition_weight_trend_kg':    str(round(n['weight_trend'], 2)) if n.get('weight_trend') is not None else 'unknown',
        'nutrition_weight_rate_kg_wk':  str(round(n['weight_rate'], 2) + 0.0) if n.get('weight_rate') is not None else 'unknown',
    }
    import hashlib
    for uid, payload in (n.get('history') or {}).items():
        out[uid] = hashlib.sha1(payload.encode()).hexdigest()[:10]   # changes whenever the series change
    meals = n.get('meals') or {}
    for mk, _ in NUTRITION_MEALS:
        m = meals.get(mk) or {}
        for suffix, _label, _icon, _unit in _MEAL_METRICS:
            val = m.get(suffix) or 0
            out[f'nutrition_{mk}_{suffix}'] = str(int(round(val)) if suffix == 'calories' else round(val, 1))
    return out


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
    # The HA sensors describe the owner only — other riders on this instance (a partner's rides, friends' synced rides)
    # must not inflate the totals or become "last ride".
    owner_row = query_db('SELECT id FROM Rider WHERE isDefault=1 LIMIT 1', one=True)
    who = ' WHERE riderId=%d' % owner_row['id'] if owner_row else ''
    totals = query_db(
        'SELECT COUNT(*) as rides, SUM(distance) as dist, SUM(totalElevationGain) as elev, '
        'SUM(movingTime) as secs, SUM(calories) as cals FROM Activity' + who,
        one=True,
    )
    last = query_db(
        'SELECT name, sportType, startDateLocal, distance, movingTime, '
        'totalElevationGain, averageSpeed, averageHeartrate, averageWatts, calories '
        'FROM Activity' + who + ' ORDER BY startDateLocal DESC LIMIT 1',
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
    """Force-publish all HA sensors. Called by the manual Settings button.
    Resets the change-detection cache so the next heartbeat sees a clean baseline."""
    global _last_state
    host = (settings.get('mqttHost') or '').strip()
    if not host:
        raise ValueError("MQTT host not configured")

    port = int(settings.get('mqttPort') or 1883)
    auth = None
    if settings.get('mqttUser'):
        auth = {'username': settings['mqttUser'],
                'password': settings.get('mqttPassword') or ''}

    states = _state_values(stats)
    garmin_states    = _garmin_state_values(_gather_garmin())
    nutrition_states = _nutrition_state_values(_gather_nutrition())
    msgs   = []

    for sensor_list, state_map in [
        (SENSORS,           states),
        (GARMIN_SENSORS,    garmin_states),
        (_nutrition_sensors(), nutrition_states),
    ]:
        for uid, name, icon, unit in sensor_list:
            config_topic, state_topic, config_json = _discovery(uid, name, icon, unit)
            msgs.append({'topic': config_topic, 'payload': config_json, 'retain': True, 'qos': 1})
            msgs.append({'topic': state_topic,  'payload': state_map[uid], 'retain': True, 'qos': 1})
            if uid in HISTORY_UIDS:
                msgs.append({'topic': _attr_topic(uid), 'payload': _hist_now().get(uid, '{}'), 'retain': True, 'qos': 1})
            log.info("MQTT queued %s = %s", uid, state_map[uid])

    _broker_send(msgs, host, port, auth)
    # Reset cache so next heartbeat sees the freshly published values as the baseline
    all_states = {}
    all_states.update(states)
    all_states.update(garmin_states)
    all_states.update(nutrition_states)
    _last_state.update(all_states)
    log.warning("MQTT publish complete — %d sensors", len(SENSORS) + len(GARMIN_SENSORS) + len(_nutrition_sensors()))


def push_update(activity=None, background=False):
    """Auto-update sensors and optionally send a ride notification.
    Safe to call from any route — silently no-ops if MQTT not configured.
    Only publishes sensors whose value has changed since the last push."""
    global _last_state, _fail_count, _next_retry
    import time
    from database import query_db
    if background and time.time() < _next_retry:
        return
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

        stats = _gather_stats()
        all_states = {}
        all_states.update(_state_values(stats))
        all_states.update(_garmin_state_values(_gather_garmin()))
        all_states.update(_nutrition_state_values(_gather_nutrition()))

        sensor_lookup = {uid: (name, icon, unit)
                         for uid, name, icon, unit in SENSORS + GARMIN_SENSORS + _nutrition_sensors()}
        all_states = {uid: val for uid, val in all_states.items() if uid in sensor_lookup}   # ride-only instances skip nutrition
        changed = {uid for uid, val in all_states.items() if _last_state.get(uid) != val}

        msgs = []
        if changed:
            for uid in changed:
                name, icon, unit = sensor_lookup[uid]
                config_topic, state_topic, config_json = _discovery(uid, name, icon, unit)
                msgs.append({'topic': config_topic, 'payload': config_json, 'retain': True, 'qos': 1})
                msgs.append({'topic': state_topic,  'payload': all_states[uid], 'retain': True, 'qos': 1})
                if uid in HISTORY_UIDS:
                    msgs.append({'topic': _attr_topic(uid), 'payload': _hist_now().get(uid, '{}'), 'retain': True, 'qos': 1})

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
            prs = query_db('SELECT COUNT(*) AS n FROM SegmentEffort WHERE activityId=? AND isPR=1', [act.get('id')], one=True)
            ytd = None
            if act.get('riderId') and act.get('startDateLocal'):
                ytd = query_db("SELECT COUNT(*) AS n, SUM(distance) AS d FROM Activity WHERE riderId=? AND strftime('%Y', startDateLocal)=?",
                               [act['riderId'], act['startDateLocal'][:4]], one=True)
            title, message = ride_summary(act, rider_name, prs['n'] if prs else 0,
                                          (ytd['n'], (ytd['d'] or 0) / 1609.344, act['startDateLocal'][:4]) if ytd else None)

            payload = {'title': title, 'message': message, 'url': url, 'rider': rider_name, 'ride_id': act.get('id')}
            if ha_device:
                payload['ha_device'] = ha_device

            msgs.append({
                'topic':   f'{_prefix()}/new_ride',
                'payload': json.dumps(payload),
                'qos': 1,
            })

        if msgs:
            _broker_send(msgs, host, port, auth)
            _last_state.update({uid: all_states[uid] for uid in changed})
            log.warning("MQTT push_update — %d sensor(s) changed%s",
                        len(changed), ", ride notification sent" if activity else "")
        elif activity:
            _broker_send(msgs, host, port, auth)
            log.warning("MQTT push_update — ride notification sent (no sensor changes)")

        if _fail_count:
            log.warning("MQTT broker reachable again after %d failed attempt(s)", _fail_count)
        _fail_count = 0
        _next_retry = 0.0

    except Exception as e:
        _fail_count += 1
        _next_retry = time.time() + _BACKOFF_STEPS[min(_fail_count, len(_BACKOFF_STEPS)) - 1]
        # Log the first failure of an outage, then every 10th, to avoid flooding the journal
        if _fail_count == 1 or _fail_count % 10 == 0:
            log.warning("MQTT push_update failed (non-fatal, attempt %d, backing off): %s", _fail_count, e)


def push_workout(w, rider_name, url):
    """"Workout saved" push: same topic and shape as the ride-added message, so the one HA automation sends it to both phones."""
    from database import query_db
    from services import workouts
    s = query_db('SELECT mqttHost, mqttPort, mqttUser, mqttPassword FROM Settings WHERE id=1', one=True)
    if not s or not (s['mqttHost'] or '').strip():
        return
    title, message = workouts.summary(w, rider_name)
    auth = {'username': s['mqttUser'], 'password': s['mqttPassword'] or ''} if s['mqttUser'] else None
    payload = {'title': title, 'message': message, 'url': url, 'rider': rider_name, 'ride_id': w['id'], 'kind': 'workout'}
    _broker_send([{'topic': f'{_prefix()}/new_ride', 'payload': json.dumps(payload), 'qos': 1}], s['mqttHost'].strip(), int(s['mqttPort'] or 1883), auth)


def push_gear_alert(alerts):
    """"Something on a bike needs attention" push: same topic and shape as the ride-added message so the one HA automation delivers it to the right phone."""
    from database import query_db
    s = query_db('SELECT mqttHost, mqttPort, mqttUser, mqttPassword FROM Settings WHERE id=1', one=True)
    if not s or not (s['mqttHost'] or '').strip() or not alerts:
        return
    auth = {'username': s['mqttUser'], 'password': s['mqttPassword'] or ''} if s['mqttUser'] else None
    msgs = []
    for a in alerts:
        r = query_db('SELECT name, haDevice FROM Rider WHERE id=?', [a.get('riderId')], one=True)
        payload = {'title': 'Gear: ' + ('needs attention' if len(alerts) == 1 else f'{len(alerts)} things need attention'), 'message': a['text'], 'url': '/gear', 'rider': r['name'] if r else None,
                   'kind': 'gear', 'ride_id': None}
        if r and r['haDevice']:
            payload['ha_device'] = r['haDevice']
        msgs.append({'topic': f'{_prefix()}/new_ride', 'payload': json.dumps(payload), 'qos': 1})
    _broker_send(msgs, s['mqttHost'].strip(), int(s['mqttPort'] or 1883), auth)


def push_update_nutrition():
    """Publish nutrition sensors that have changed — called after every food/water log action."""
    global _last_state
    try:
        from database import query_db
        settings = query_db('SELECT * FROM Settings WHERE id=1', one=True)
        if not settings or not (settings['mqttHost'] or '').strip():
            return

        settings = dict(settings)   # sqlite3.Row has no .get()
        host = settings['mqttHost'].strip()
        port = int(settings.get('mqttPort') or 1883)
        auth = None
        if settings.get('mqttUser'):
            auth = {'username': settings['mqttUser'], 'password': settings.get('mqttPassword') or ''}

        nutrition_states = _nutrition_state_values(_gather_nutrition())
        changed = {uid for uid, val in nutrition_states.items() if _last_state.get(uid) != val}
        if not changed:
            return

        msgs = []
        for uid, name, icon, unit in _nutrition_sensors():
            if uid not in changed:
                continue
            config_topic, state_topic, config_json = _discovery(uid, name, icon, unit)
            msgs.append({'topic': config_topic, 'payload': config_json, 'retain': True, 'qos': 1})
            msgs.append({'topic': state_topic,  'payload': nutrition_states[uid], 'retain': True, 'qos': 1})
            if uid in HISTORY_UIDS:
                msgs.append({'topic': _attr_topic(uid), 'payload': _hist_now().get(uid, '{}'), 'retain': True, 'qos': 1})

        _broker_send(msgs, host, port, auth)
        _last_state.update({uid: nutrition_states[uid] for uid in changed})
        log.warning("MQTT nutrition push — %d sensor(s) changed", len(changed))
    except Exception as e:
        log.warning("MQTT push_update_nutrition failed (non-fatal): %s", e)
