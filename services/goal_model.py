"""Floating calorie/macro goals: a target that moves only as your WEIGHT does.

  BMR (Mifflin-St Jeor, from current weight/height/age/sex)
    x activity factor (derived from the daily step goal — 8k steps is ~1.35)
    + a FIXED allowance for rides (typical weekly ride burn / 7 — a setting, not what you rode this week)
  = estimated daily burn (TDEE)
    - deficit for the chosen loss rate (1 lb/week = 500 kcal/day)
  = calorie goal, held at 1.1 x BMR at the lowest so you are never asked to eat at resting burn.

A heavy or missed ride day never changes the goal; as weight falls, BMR falls and the goal steps down slowly.

Macros follow: protein by body weight, fat as a share of calories, carbs take the remainder.
"""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from database import get_db, query_db

DEFAULT_RIDE_KCAL_WEEK = 3500   # net calories from a typical week of riding (about 2 rides)
FLOOR_X_BMR = 1.1
RIDE_GOAL_WEEK = 2               # default minimum rides per Mon-Sun week (Settings.rideGoalWeek overrides) for the dashboard's Today's goals card


def activity_from_steps(steps):
    """Everyday-movement multiplier from the daily step goal: 5k ~ 1.30, 8k ~ 1.35, 12k ~ 1.43."""
    return round(min(1.6, max(1.2, 1.2 + 0.019 * (steps or 8000) / 1000)), 3)
PROTEIN_G_PER_KG = 1.55
FAT_SHARE = 0.25
KCAL_PER_LB_WEEK = 500


def _today():
    return datetime.now(ZoneInfo('Europe/London')).date()


def _or_default(v, default):
    """None means unset; an explicit 0 (maintenance, no-ride allowance) is a real value, not a default."""
    return default if v is None else v


def profile():
    s = query_db('SELECT sex, birthYear, heightCm, lossLbPerWeek, stepGoal, goalAuto, rideKcalWeek, rideGoalWeek FROM Settings WHERE id=1', one=True)
    s = dict(s) if s else {}
    by = s.get('birthYear')
    return {
        'sex': s.get('sex') or '',
        'age': (_today().year - by) if by else None,
        'height_cm': s.get('heightCm'),
        'activity': activity_from_steps(s.get('stepGoal') or 8000),
        'loss_lb_week': _or_default(s.get('lossLbPerWeek'), 1.5),
        'step_goal': s.get('stepGoal') or 8000,
        'ride_kcal_week': _or_default(s.get('rideKcalWeek'), DEFAULT_RIDE_KCAL_WEEK),
        'auto': bool(s.get('goalAuto')),
        'ride_goal_week': s.get('rideGoalWeek') or RIDE_GOAL_WEEK,
    }


def current_weight_kg(rider_id):
    """Average of the last 7 days of weigh-ins (falls back to the latest one) — responsive, unlike the smoothed trend."""
    rows = query_db('SELECT logDate, weightKg FROM WeightLog WHERE riderId=? ORDER BY logDate DESC LIMIT 12', [rider_id])
    if not rows:
        return None
    cutoff = (_today() - timedelta(days=7)).isoformat()
    recent = [r['weightKg'] for r in rows if r['logDate'] >= cutoff]
    return sum(recent) / len(recent) if recent else rows[0]['weightKg']


def bmr_mifflin(kg, cm, age, sex):
    return 10 * kg + 6.25 * cm - 5 * age + (5 if sex == 'male' else -161)


def _working(kg, p):
    """The calorie maths for one weight: (bmr, neat, ride/day, tdee, deficit, floor, goal)."""
    bmr = bmr_mifflin(kg, p['height_cm'], p['age'], p['sex'])
    neat = bmr * p['activity']
    ride_per_day = p['ride_kcal_week'] / 7   # fixed allowance — riding more or less this week does not move the goal
    tdee = neat + ride_per_day
    deficit = p['loss_lb_week'] * KCAL_PER_LB_WEEK
    floor = bmr * FLOOR_X_BMR
    goal = int(round(max(floor, tdee - deficit) / 10.0) * 10)
    return bmr, neat, ride_per_day, tdee, deficit, floor, goal


def plan(rider_id=None):
    """Returns the working, or {'error': ...} when a profile field is missing."""
    if rider_id is None:
        r = query_db('SELECT id FROM Rider WHERE isDefault=1 LIMIT 1', one=True)
        rider_id = r['id'] if r else None
    p = profile()
    kg = current_weight_kg(rider_id)
    missing = [k for k, v in (('sex', p['sex']), ('age', p['age']), ('height', p['height_cm']), ('a weigh-in', kg)) if not v]
    if missing:
        return {'error': 'Need ' + ', '.join(missing), 'profile': p}

    bmr, neat, ride_per_day, tdee, deficit, floor, goal = _working(kg, p)

    protein = int(round(PROTEIN_G_PER_KG * kg / 5.0) * 5)
    fat = int(round(goal * FAT_SHARE / 9))
    carbs = max(0, int(round((goal - protein * 4 - fat * 9) / 4)))
    return {
        'weight_kg': round(kg, 1), 'bmr': round(bmr), 'neat': round(neat), 'rides_per_day': round(ride_per_day), 'rides_week': p['ride_kcal_week'], 'floor': round(floor),
        'tdee': round(tdee), 'deficit': round(deficit), 'goal': goal, 'floored': tdee - deficit < floor,
        'protein': protein, 'carbs': carbs, 'fat': fat, 'profile': p,
    }


def apply(pl):
    """Write the plan's goals into Settings (BMR too, for the calorie-burn sensors)."""
    db = get_db()
    db.execute('UPDATE Settings SET nutritionCalGoal=?, nutritionProteinGoal=?, nutritionCarbGoal=?, nutritionFatGoal=?, nutritionBmrKcal=? WHERE id=1',
               [pl['goal'], pl['protein'], pl['carbs'], pl['fat'], pl['bmr']])
    db.commit()


def auto_update():
    """Daily: if 'adjust automatically' is on and the plan has drifted 20+ kcal from the saved goal, apply it."""
    s = query_db('SELECT goalAuto, goalAutoLast, nutritionCalGoal FROM Settings WHERE id=1', one=True)
    if not s or not s['goalAuto']:
        return None
    today = _today().isoformat()
    if s['goalAutoLast'] == today:
        return None
    db = get_db()
    db.execute('UPDATE Settings SET goalAutoLast=? WHERE id=1', [today])
    db.commit()
    pl = plan()
    if 'error' in pl:
        return None
    if abs(pl['goal'] - (s['nutritionCalGoal'] or 0)) >= 20:
        apply(pl)
        try:
            from services.mqtt import push_update_nutrition
            push_update_nutrition()
        except Exception:
            pass
        return pl
    return None


def day_burn(rider_id, date_iso):
    """Estimated calories burned on a day: everyday movement (BMR x activity) plus that day's detected activities
    (Garmin/imported rides), net of the resting burn during them. Returns (kcal, source_label) or None if the
    profile isn't filled in yet (callers then fall back to the old BMR/Garmin rules)."""
    p = profile()
    kg = current_weight_kg(rider_id)
    if not (p['sex'] and p['age'] and p['height_cm'] and kg):
        return None
    bmr = bmr_mifflin(kg, p['height_cm'], p['age'], p['sex'])
    rides = query_db('SELECT calories, movingTime FROM Activity WHERE riderId=? AND date(startDateLocal)=? AND calories IS NOT NULL',
                     [rider_id, date_iso])
    net = sum(max(0.0, (r['calories'] or 0) - bmr * (r['movingTime'] or 0) / 86400) for r in rides)
    from services import workouts
    wnet = workouts.net_calories_on(rider_id, date_iso, bmr)          # recorded walks / runs count too
    return int(round(bmr * p['activity'] + net + wnet)), ('daily burn + activity' if (rides or wnet) else 'est. daily burn')


APPLY_DRIFT = 20   # kcal — auto_update only rewrites the saved goal when the plan has moved this far


def plan_status(rider_id=None):
    """Everything the 'My plan' and 'Next calorie change' dashboard cards show: the saved goals, why they are what they are,
    and at which (7-day average) weights the goal will next step down — or up, since weight is the only trigger."""
    if rider_id is None:
        r = query_db('SELECT id FROM Rider WHERE isDefault=1 LIMIT 1', one=True)
        rider_id = r['id'] if r else None
    pl = plan(rider_id)
    if 'error' in pl:
        return {'status': 'error', 'error': pl['error']}
    p = pl['profile']
    s = dict(query_db('SELECT nutritionCalGoal, nutritionProteinGoal, nutritionCarbGoal, nutritionFatGoal, goalAutoLast FROM Settings WHERE id=1', one=True) or {})
    saved = s.get('nutritionCalGoal') or pl['goal']
    kg = pl['weight_kg']
    LB = 2.20462

    def goal_at(w):
        return _working(w, p)[6]

    def walk(direction, limit=3):
        """Successive weights (lb) at which the plan moves >= APPLY_DRIFT from the goal in force, stepping 0.05 kg at a time."""
        out, base, w = [], saved, kg
        for _ in range(int(60 / 0.05)):
            w += direction * 0.05
            if w < 40 or len(out) >= limit:
                break
            g = goal_at(w)
            if (g - base) * direction >= APPLY_DRIFT:
                out.append({'lb': round(w * LB, 1), 'away_lb': round(abs(w - kg) * LB, 1), 'goal': g})
                base = g
        return out

    downs = walk(-1)
    ups = walk(+1, 1)
    rate = None
    try:
        from services import insights
        rate = insights.weight_rate_kg_per_week(rider_id)
    except Exception:
        pass
    loss_lb_wk = round(-rate * LB, 2) if rate is not None else None
    for d in downs:
        d['days'] = round(d['away_lb'] / loss_lb_wk * 7) if loss_lb_wk and loss_lb_wk > 0.05 else None
    return {
        'status': 'ok', 'auto': p['auto'], 'saved_goal': saved, 'plan_goal': pl['goal'],
        'protein': s.get('nutritionProteinGoal'), 'carbs': s.get('nutritionCarbGoal'), 'fat': s.get('nutritionFatGoal'),
        'weight_lb': round(kg * LB, 1), 'bmr': pl['bmr'], 'neat': pl['neat'], 'rides_per_day': pl['rides_per_day'], 'rides_week': pl['rides_week'],
        'tdee': pl['tdee'], 'deficit': pl['deficit'], 'floor': pl['floor'], 'floored': pl['floored'],
        'step_goal': p['step_goal'], 'activity': p['activity'], 'loss_lb_week': p['loss_lb_week'],
        'last_check': s.get('goalAutoLast'), 'rate_lb_wk': loss_lb_wk, 'drift': APPLY_DRIFT,
        'next_down': downs, 'next_up': ups[0] if ups else None,
    }
