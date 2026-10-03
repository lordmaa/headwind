"""Coaching numbers computed server-side (Headwind calculates, Home Assistant presents).

reality_check(): compares what you actually ate over the last 14 completed days with how your weight really moved,
which gives a measured daily burn — far more trustworthy than any single-day estimated deficit.
"""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from database import query_db

LB = 2.20462
KCAL_PER_KG = 7700
WINDOW = 21   # one window for intake AND weight so the two are measured over the same period


def _today():
    return datetime.now(ZoneInfo('Europe/London')).date()


def _slope(points):
    """Least-squares slope of [(day_ordinal, kg)] in kg/day, or None with fewer than 3 points."""
    if len(points) < 3:
        return None
    n = len(points)
    mx = sum(x for x, _ in points) / n
    my = sum(y for _, y in points) / n
    den = sum((x - mx) ** 2 for x, _ in points)
    return sum((x - mx) * (y - my) for x, y in points) / den if den else None


def _theil_sen(points):
    """Median of all pairwise slopes (kg/day). Unlike a least-squares line, one odd point (a holiday jump, a bad
    weigh-in) cannot drag it far, so the pace stays sensible after time off. None with fewer than 4 points."""
    if len(points) < 4:
        return None
    slopes = [(y2 - y1) / (x2 - x1) for i, (x1, y1) in enumerate(points) for x2, y2 in points[i + 1:] if x2 - x1 >= 2]
    if not slopes:
        return None
    slopes.sort()
    m = len(slopes) // 2
    return slopes[m] if len(slopes) % 2 else (slopes[m - 1] + slopes[m]) / 2


def last_weigh_in(rider_id):
    r = query_db('SELECT logDate FROM WeightLog WHERE riderId=? ORDER BY logDate DESC LIMIT 1', [rider_id], one=True)
    return date.fromisoformat(r['logDate']) if r else None


def weight_rate_kg_per_week(rider_id, days=WINDOW):
    """Weight change per week (kg, negative = losing): a robust slope through the raw weigh-ins in the 21 days up to
    your LAST weigh-in (widening to 28 if there are too few). Anchoring to the last weigh-in, not to today, means
    days you didn't weigh or track leave the estimate standing instead of wiping it out."""
    anchor = last_weigh_in(rider_id)
    if not anchor:
        return None
    for span in (days, 28):
        start = (anchor - timedelta(days=span)).isoformat()
        rows = query_db('SELECT logDate, weightKg FROM WeightLog WHERE riderId=? AND logDate>=? ORDER BY logDate', [rider_id, start])
        s = _theil_sen([(date.fromisoformat(r['logDate']).toordinal(), r['weightKg']) for r in rows])
        if s is not None:
            return s * 7
    return None


def reality_check(rider_id):
    from services import goal_model
    from services.weight_trend import compute_trend
    today = _today()
    yesterday = today - timedelta(days=1)
    # window ends at the last day you actually logged food (not necessarily yesterday), so a stretch of not tracking
    # leaves the last good 3 weeks on show ("as of ...") instead of blanking the card
    last = query_db('SELECT MAX(logDate) d FROM FoodLog WHERE riderId=? AND logDate<=? AND calories IS NOT NULL', [rider_id, yesterday.isoformat()], one=True)
    end = date.fromisoformat(last['d']) if last and last['d'] else yesterday
    start = end - timedelta(days=WINDOW - 1)

    intake = query_db('''SELECT logDate, SUM(calories) k FROM FoodLog WHERE riderId=? AND logDate BETWEEN ? AND ?
                         GROUP BY logDate HAVING SUM(calories) >= 1200''', [rider_id, start.isoformat(), end.isoformat()])   # under ~1,200 kcal = the day wasn't fully logged
    # weight movement must be measured over the SAME stretch as the food we can see, so start at the first logged day
    first_logged = min((r['logDate'] for r in intake), default=start.isoformat())
    weights = query_db('SELECT logDate, weightKg FROM WeightLog WHERE riderId=? AND logDate BETWEEN ? AND ? ORDER BY logDate',
                       [rider_id, first_logged, (end + timedelta(days=2)).isoformat()])   # +2d: the weigh-in that reflects the last day eaten
    pts = [(date.fromisoformat(r['logDate']).toordinal(), r['weightKg']) for r in weights]
    slope = _theil_sen(pts) if len(pts) >= 5 else None   # fewer than 5 weigh-ins is too few for a rate that drives a verdict
    out = {'win': WINDOW, 'days_logged': len(intake), 'weigh_ins': len(pts), 'as_of': end.isoformat(),
           'stale_days': (today - end).days}
    if len(intake) < 7 or slope is None:
        out['status'] = 'need_more_data'
        return out

    avg = sum(r['k'] for r in intake) / len(intake)
    plan = goal_model.plan(rider_id)
    target = goal_model.profile()['loss_lb_week']
    rate_lb = slope * 7 * LB

    # real weigh-ins only (no smoothing or interpolation) plus a straight line for the plan, anchored to the first real reading
    w_pts = [[r['logDate'], round(r['weightKg'] * LB, 1)] for r in weights]
    plan_line = []
    if w_pts:
        first_d, first_lb = date.fromisoformat(w_pts[0][0]), w_pts[0][1]
        last_d = date.fromisoformat(w_pts[-1][0])
        plan_line = [[first_d.isoformat(), first_lb], [last_d.isoformat(), round(first_lb - target * (last_d - first_d).days / 7, 1)]]
    out.update({
        'status': 'ok',
        'confidence': 'good' if len(pts) >= 7 else 'fair',
        'intake': round(avg),
        'rate_lb_wk': round(rate_lb, 2),
        'change_lb': round(rate_lb * WINDOW / 7, 1),
        'observed_burn': round(avg - slope * KCAL_PER_KG),
        'model_burn': plan.get('tdee') if 'error' not in plan else None,
        'target_lb_wk': target,
        'w': w_pts, 'plan': plan_line,
    })
    return out


# ── Goal weight and the date projection ─────────────────────────────

def _settings_row():
    return query_db('SELECT goalWeightKg, dietStartDate FROM Settings WHERE id=1', one=True)


def projection(rider_id):
    """When you reach the goal weight at the pace your recent weigh-ins show. The pace is a robust slope anchored to your
    last weigh-in, so time away neither wipes it out nor lets one jump distort it; the card says how old the data is."""
    from services.weight_trend import compute_trend
    s = _settings_row()
    goal_kg = s['goalWeightKg'] if s else None
    if not goal_kg:
        return {'status': 'no_goal'}
    today = _today()
    rows = query_db('SELECT logDate, weightKg FROM WeightLog WHERE riderId=? ORDER BY logDate', [rider_id])
    if len(rows) < 2:
        return {'status': 'need_more_data', 'goal_lb': round(goal_kg * LB, 1)}

    # current weight: the latest week of weigh-ins (or the last reading), not the lagging trend
    latest = date.fromisoformat(rows[-1]['logDate'])
    recent = [r['weightKg'] for r in rows if r['logDate'] >= (latest - timedelta(days=7)).isoformat()]
    current_kg = sum(recent) / len(recent) if recent else rows[-1]['weightKg']
    start_kg = rows[0]['weightKg']
    if s['dietStartDate']:
        after = [r for r in rows if r['logDate'] >= s['dietStartDate']]
        start_kg = after[0]['weightKg'] if after else start_kg

    rate = weight_rate_kg_per_week(rider_id)                      # kg/week, negative when losing
    loss_lb_wk = -rate * LB if rate is not None else None
    from services import goal_model
    target = goal_model.profile()['loss_lb_week']
    remaining_lb = (current_kg - goal_kg) * LB
    out = {'status': 'ok', 'goal_lb': round(goal_kg * LB, 1), 'current_lb': round(current_kg * LB, 1),
           'start_lb': round(start_kg * LB, 1), 'remaining_lb': round(remaining_lb, 1),
           'rate_lb_wk': round(loss_lb_wk, 2) if loss_lb_wk is not None else None, 'target_lb_wk': target,
           'last_weigh_in': latest.isoformat(), 'days_since': (today - latest).days, 'stale': (today - latest).days > 10,
           'progress': round(max(0.0, min(1.0, (start_kg - current_kg) / (start_kg - goal_kg))), 3) if start_kg > goal_kg else 1.0}
    if remaining_lb <= 0:
        out['status'] = 'reached'
        return out

    def eta(rate_lb):
        return (today + timedelta(days=round(remaining_lb / rate_lb * 7))).isoformat() if rate_lb and rate_lb > 0.05 else None

    out['eta_now'] = eta(loss_lb_wk)                               # at the pace you're actually losing
    out['eta_slow'] = eta(loss_lb_wk * 0.7) if loss_lb_wk and loss_lb_wk > 0 else None   # losses usually slow down
    out['eta_plan'] = eta(target)
    out['weeks_now'] = round(remaining_lb / loss_lb_wk, 1) if loss_lb_wk and loss_lb_wk > 0.05 else None
    # a milestone every 10 lb on the way down, ending at the goal itself
    ms = []
    cur_lb, goal_lb = current_kg * LB, goal_kg * LB
    step = int(cur_lb // 10) * 10
    marks = []
    while step > goal_lb + 0.5 and len(marks) < 5:
        if step < cur_lb - 0.5:
            marks.append(float(step))
        step -= 10
    marks.append(round(goal_lb, 1))
    for lb in marks:
        rem = cur_lb - lb
        ms.append({'label': f'{lb:g} lb', 'lb': lb,
                   'eta': (today + timedelta(days=round(rem / loss_lb_wk * 7))).isoformat() if loss_lb_wk and loss_lb_wk > 0.05 else None})
    out['milestones'] = ms
    return out


def activity_status(w):
    """'stagnant' when neither weekly riding miles nor daily steps are up 5%+ (last 4 completed weeks vs the 4 before),
    'ok' when at least one is rising, 'unknown' with too little data. `w` is the nutrition_activity_weekly payload; its
    last week is still in progress and is left out. Same rule the Activity trend card draws."""
    def pct(v):
        done = [x for x in (v or [])[:-1] if x is not None]
        if len(done) < 8:
            return None
        a, b = sum(done[-4:]) / 4, sum(done[-8:-4]) / 4
        return (a - b) / b * 100 if b else None
    p = [x for x in (pct(w.get('mi')), pct(w.get('steps'))) if x is not None]
    if not p:
        return 'unknown'
    return 'stagnant' if all(x < 5 for x in p) else 'ok'
