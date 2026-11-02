"""Plain-text exports of the food diary and of activities, built to be pasted or uploaded into an AI chat.

Everything is gathered by `food_data()` / `activity_data()` (plain dicts, easy to test) and written out by `render_text()`, which
is also what the PDF export prints (services/simple_pdf.py). Quantities are shown in both unit systems so a model never has to guess.
"""
import json
from datetime import date, datetime, timedelta

from database import query_db

MEAL_ORDER = ['breakfast', 'lunch', 'dinner', 'snacks', 'drinks', 'ride_fuel', 'recovery', 'uncategorised']
MEAL_LABEL = {'ride_fuel': 'Ride fuel', 'uncategorised': 'Other'}
MAX_DAYS = 400


# ---------------------------------------------------------------- small formatters
def fdate(d):
    d = d if isinstance(d, date) else date.fromisoformat(str(d)[:10])
    return f'{d:%a} {d.day} {d:%b %Y}'


def parse_range(start, end):
    """-> (date, date) inclusive, swapped if reversed, capped at MAX_DAYS. Raises ValueError on junk."""
    a, b = date.fromisoformat(str(start)[:10]), date.fromisoformat(str(end)[:10])
    if a > b:
        a, b = b, a
    if (b - a).days + 1 > MAX_DAYS:
        raise ValueError('Pick a range of %d days or fewer' % MAX_DAYS)
    return a, b


def n0(v):
    return f'{round(v or 0):,}'


def n1(v):
    return f'{(v or 0):,.1f}'


def hms(secs):
    secs = int(secs or 0)
    h, r = divmod(secs, 3600)
    return f'{h}:{r // 60:02d}:{r % 60:02d}'


def dist(m):
    m = m or 0
    return f'{m / 1609.344:,.1f} mi ({m / 1000:,.1f} km)'


def speed(mps):
    mps = mps or 0
    return f'{mps * 2.23694:.1f} mph ({mps * 3.6:.1f} km/h)'


def elev(m):
    m = m or 0
    return f'{round(m * 3.28084):,} ft ({round(m):,} m)'


def kg_str(kg):
    lb = kg * 2.20462
    st, rem = divmod(lb, 14)
    return f'{kg:.1f} kg ({int(st)} st {rem:.1f} lb = {lb:.1f} lb)'


def _rider_name(rider_id):
    r = query_db('SELECT name FROM Rider WHERE id=?', [rider_id], one=True)
    return (r['name'] if r else None) or 'Rider'


# ---------------------------------------------------------------- food
def _goals():
    r = query_db('SELECT nutritionCalGoal, nutritionProteinGoal, nutritionCarbGoal, nutritionFatGoal, nutritionSugarGoal, nutritionWaterGoalMl FROM Settings WHERE id=1', one=True)
    if not r:
        return {}
    return {'kcal': r['nutritionCalGoal'], 'protein': r['nutritionProteinGoal'], 'carbs': r['nutritionCarbGoal'], 'fat': r['nutritionFatGoal'],
            'sugar': r['nutritionSugarGoal'], 'water_ml': r['nutritionWaterGoalMl']}


def _steps(rider_id, day):
    try:
        from services import weighin
        return weighin._steps(rider_id, day)
    except Exception:
        return None


def _burn(rider_id, day):
    try:
        from services import goal_model
        b = goal_model.day_burn(rider_id, day)
        return b[0] if b else None
    except Exception:
        return None


def _entry(r):
    qty = r['quantity'] or 1
    grams = (r['servingG'] or 0) * qty
    return {'food': r['foodName'], 'brand': r['brand'] or '', 'meal': r['mealType'] or 'uncategorised', 'grams': round(grams) if grams else None,
            'quantity': qty, 'serving_g': r['servingG'], 'kcal': r['calories'] or 0, 'protein': r['protein'] or 0, 'carbs': r['carbs'] or 0,
            'fat': r['fat'] or 0, 'fibre': r['fibre'], 'sugar': r['sugar'], 'alcohol_units': r['alcoholUnits'] or 0, 'barcode': r['barcode'] or '', 'source': r['source'] or '', 'created': r['createdAt'] or '', 'nutriscore': (r['nutriScoreGrade'] or '').upper() if (r['nutriScoreGrade'] or '').upper() in ('A', 'B', 'C', 'D', 'E') else ''}


def _totals(entries):
    t = {k: sum((e[k] or 0) for e in entries) for k in ('kcal', 'protein', 'carbs', 'fat', 'alcohol_units')}
    t['fibre'] = sum((e['fibre'] or 0) for e in entries)
    t['sugar'] = sum((e['sugar'] or 0) for e in entries)
    return t


def food_data(rider_id, start, end):
    """Everything the food export prints. Entry numbers are the totals for the quantity eaten, exactly as the diary stores them."""
    rows = query_db('''SELECT id, logDate, mealType, foodName, brand, calories, protein, carbs, fat, fibre, sugar, alcoholUnits, quantity, servingG, nutriScoreGrade,
                              barcode, source, createdAt
                       FROM FoodLog WHERE riderId=? AND logDate BETWEEN ? AND ? ORDER BY logDate, id''', [rider_id, start.isoformat(), end.isoformat()])
    water = {r['logDate']: r['ml'] for r in query_db('SELECT logDate, SUM(ml) ml FROM HydrationLog WHERE riderId=? AND logDate BETWEEN ? AND ? GROUP BY logDate',
                                                     [rider_id, start.isoformat(), end.isoformat()])}
    weights = {r['logDate']: r['weightKg'] for r in query_db('SELECT logDate, weightKg FROM WeightLog WHERE riderId=? AND logDate BETWEEN ? AND ? ORDER BY logDate',
                                                            [rider_id, start.isoformat(), end.isoformat()])}
    try:
        from services import weighin
        ex = weighin._day_exercise(rider_id, start, end)
    except Exception:
        ex = {}
    by_day = {}
    for r in rows:
        by_day.setdefault(r['logDate'], []).append(_entry(r))
    days = []
    d = start
    while d <= end:
        iso = d.isoformat()
        ents = by_day.get(iso, [])
        if ents or iso in water or iso in weights or iso in ex:
            meals = {}
            for e in ents:
                meals.setdefault(e['meal'], []).append(e)
            days.append({'date': iso, 'entries': ents, 'meals': meals, 'totals': _totals(ents), 'water_ml': water.get(iso), 'weight_kg': weights.get(iso),
                         'steps': _steps(rider_id, iso), 'burn_kcal': _burn(rider_id, iso), 'exercise': ex.get(iso, [])})
        d += timedelta(days=1)
    logged = [x for x in days if x['entries']]
    summary = {'days_in_range': (end - start).days + 1, 'days_logged': len(logged), 'entries': len(rows)}
    if logged:
        for k in ('kcal', 'protein', 'carbs', 'fat', 'fibre', 'sugar', 'alcohol_units'):
            summary['avg_' + k] = sum(x['totals'][k] for x in logged) / len(logged)
        summary['total_alcohol_units'] = sum(x['totals']['alcohol_units'] for x in logged)
        wl = [x['water_ml'] for x in days if x['water_ml']]
        if wl:
            summary['avg_water_ml'] = sum(wl) / len(wl)
    ws = sorted(weights.items())
    if len(ws) >= 2:
        summary['weight_first'], summary['weight_last'] = ws[0], ws[-1]
    return {'rider': _rider_name(rider_id), 'start': start.isoformat(), 'end': end.isoformat(), 'goals': _goals(), 'days': days, 'summary': summary}


def _split(t):
    kcal_p, kcal_c, kcal_f = t['protein'] * 4, t['carbs'] * 4, t['fat'] * 9
    tot = kcal_p + kcal_c + kcal_f
    if tot <= 0:
        return ''
    return 'macro split by energy: protein %d%% / carbs %d%% / fat %d%%' % tuple(round(x / tot * 100) for x in (kcal_p, kcal_c, kcal_f))


SOURCE_LABEL = {'barcode': 'barcode scan', 'photo_ai': 'photo estimate (AI)', 'manual': 'typed in', 'saved_meal': 'saved meal', 'recent': 'from recent foods', 'copied': 'copied from another day',
                'screenshot': 'read from a screenshot', 'imported_total': 'imported day total', 'recovered': 'recovered entry', 'android': 'phone app'}


def _logged_at(created):
    """Entries are stamped in UTC when saved; show London time. This is when it was LOGGED, which for backfills is not when it was eaten."""
    try:
        from zoneinfo import ZoneInfo
        from datetime import timezone
        return datetime.strptime(created[:19], '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc).astimezone(ZoneInfo('Europe/London')).strftime('%H:%M')
    except Exception:
        return ''


def _food_extra(e):
    bits = []
    if e['grams']:
        f = 100.0 / e['grams']
        bits.append(f"per 100 g: {n0(e['kcal'] * f)} kcal, P {n1(e['protein'] * f)}, C {n1(e['carbs'] * f)}, F {n1(e['fat'] * f)}" + (f", fibre {n1(e['fibre'] * f)}" if e['fibre'] is not None else '') + (f", sugar {n1(e['sugar'] * f)}" if e['sugar'] is not None else ''))
    if e['barcode']:
        bits.append('barcode ' + e['barcode'])
    if e['source']:
        bits.append('logged by ' + SOURCE_LABEL.get(e['source'], e['source']))
    t = _logged_at(e['created'])
    if t:
        bits.append('logged at ' + t)
    return ('      ' + ' | '.join(bits)) if bits else ''


def _food_line(e):
    name = e['food'] + (f" [{e['brand']}]" if e['brand'] else '')
    qty = ''
    if e['grams']:
        qty = f"{e['grams']} g"
        if (e['quantity'] or 1) != 1:
            qty += f" ({e['quantity']:g} x {round(e['serving_g'])} g)"
    elif (e['quantity'] or 1) != 1:
        qty = f"x{e['quantity']:g}"
    bits = [f"{n0(e['kcal'])} kcal", f"P {n1(e['protein'])}", f"C {n1(e['carbs'])}", f"F {n1(e['fat'])}"]
    if e['fibre'] is not None:
        bits.append(f"fibre {n1(e['fibre'])}")
    if e['sugar'] is not None:
        bits.append(f"sugar {n1(e['sugar'])}")
    if e['alcohol_units']:
        bits.append(f"alcohol {e['alcohol_units']:.1f} u")
    if e['nutriscore']:
        bits.append(f"Nutri-Score {e['nutriscore']}")
    return f"  - {name}{' | ' + qty if qty else ''} | " + ', '.join(bits)


def food_text(data, heading=True, full=False):
    g, s = data['goals'] or {}, data['summary']
    out = []
    if heading:
        out += ['\x01FOOD DIARY EXPORT', f"Rider: {data['rider']}", f"Period: {fdate(data['start'])} to {fdate(data['end'])} ({s['days_in_range']} days, {s['days_logged']} with food logged, {s['entries']} entries)", '']
    out += ['HOW TO READ THIS: each entry is the total for the amount eaten (not per 100 g).',
            'Energy in kcal; protein (P), carbohydrate (C), fat (F), fibre and TOTAL sugars in grams;',
            'alcohol in UK units (1 unit = 8 g ethanol). "Estimated burn" is an app estimate, not a measurement.', '']
    if s['days_logged']:
        out.append('\x01PERIOD SUMMARY (days with food logged)')
        out.append(f"  Average per day: {n0(s['avg_kcal'])} kcal | protein {n0(s['avg_protein'])} g | carbs {n0(s['avg_carbs'])} g | fat {n0(s['avg_fat'])} g | fibre {n0(s['avg_fibre'])} g | sugar {n0(s['avg_sugar'])} g")
        avg = {'protein': s['avg_protein'], 'carbs': s['avg_carbs'], 'fat': s['avg_fat']}
        sp = _split(avg)
        if sp:
            out.append('  Average ' + sp)
        if s.get('total_alcohol_units'):
            out.append(f"  Alcohol: {s['total_alcohol_units']:.1f} units in total ({s['avg_alcohol_units']:.1f} per logged day)")
        if s.get('avg_water_ml'):
            out.append(f"  Water logged: {n0(s['avg_water_ml'])} ml per day on average")
        goals = [f'{n0(g[k])} {u}' for k, u in (('kcal', 'kcal'), ('protein', 'g protein'), ('carbs', 'g carbs'), ('fat', 'g fat')) if g.get(k)]
        if goals:
            out.append('  Daily targets: ' + ' | '.join(goals))
        if 'weight_first' in s:
            (d1, w1), (d2, w2) = s['weight_first'], s['weight_last']
            out.append(f"  Weight: {kg_str(w1)} on {fdate(d1)} -> {kg_str(w2)} on {fdate(d2)} (change {w2 - w1:+.1f} kg)")
        out.append('')
    for day in data['days']:
        t = day['totals']
        out.append('\x01=== ' + fdate(day['date']) + ' ===')
        if day['entries']:
            line = f"Totals: {n0(t['kcal'])} kcal | P {n0(t['protein'])} g | C {n0(t['carbs'])} g | F {n0(t['fat'])} g | fibre {n0(t['fibre'])} g | sugar {n0(t['sugar'])} g"
            if t['alcohol_units']:
                line += f" | alcohol {t['alcohol_units']:.1f} u"
            out.append(line)
            sp = _split(t)
            if sp:
                out.append(sp)
            if g.get('kcal'):
                diff = t['kcal'] - g['kcal']
                out.append(f"Target {n0(g['kcal'])} kcal: {'over' if diff > 0 else 'under'} by {n0(abs(diff))}")
        else:
            out.append('No food logged.')
        ctx = []
        if day['water_ml']:
            ctx.append(f"water {n0(day['water_ml'])} ml")
        if day['weight_kg']:
            ctx.append('weight ' + kg_str(day['weight_kg']))
        if day['steps']:
            ctx.append(f"steps {n0(day['steps'])}")
        if day['burn_kcal']:
            ctx.append(f"estimated burn {n0(day['burn_kcal'])} kcal")
        if ctx:
            out.append('Day: ' + ' | '.join(ctx))
        for x in day['exercise']:
            out.append(f"Exercise: {x['kind']} {x['minutes']} min, {x['km']} km ({x['km'] / 1.609344:.1f} mi), {x['kcal']} kcal")
        for meal in sorted(day['meals'], key=lambda m: MEAL_ORDER.index(m) if m in MEAL_ORDER else 99):
            ents = day['meals'][meal]
            mt = _totals(ents)
            out.append(f"{MEAL_LABEL.get(meal, meal.capitalize())} ({n0(mt['kcal'])} kcal, P {n0(mt['protein'])} C {n0(mt['carbs'])} F {n0(mt['fat'])})")
            for e in ents:
                out.append(_food_line(e))
                if full and _food_extra(e):
                    out.append(_food_extra(e))
        out.append('')
    if not data['days']:
        out.append('Nothing was logged in this period.')
    return out


# ---------------------------------------------------------------- activities
def _bike_name(bike_id):
    if not bike_id:
        return ''
    try:
        r = query_db('SELECT name FROM Bike WHERE id=?', [bike_id], one=True)
        return r['name'] if r else ''
    except Exception:
        return ''


def activity_data(rider_id, start, end):
    s, e = start.isoformat(), end.isoformat()
    acts = []
    for r in query_db('''SELECT id, name, sportType, startDateLocal, distance, movingTime, elapsedTime, totalElevationGain, averageSpeed, maxSpeed, averageHeartrate,
                                maxHeartrate, averageWatts, maxWatts, weightedAvgWatts, kilojoules, averageCadence, calories, sufferScore, city, country,
                                weatherSummary, weatherTempC, weatherWindKph, weatherGustKph, weatherWindRel, weatherRainMm, notes, description, bikeId
                         FROM Activity WHERE riderId=? AND date(startDateLocal) BETWEEN ? AND ? ORDER BY startDateLocal''', [rider_id, s, e]):
        a = dict(r)
        a['kind'] = a.pop('sportType') or 'Ride'
        a['bike'] = _bike_name(a.pop('bikeId'))
        acts.append(a)
    for r in query_db('''SELECT id, name, sport, startDateLocal, distance, movingTime, elapsedTime, totalElevationGain, averageSpeed, maxSpeed, averageHeartrate,
                                calories, steps, weatherSummary FROM Workout WHERE riderId=? AND date(startDateLocal) BETWEEN ? AND ? ORDER BY startDateLocal''', [rider_id, s, e]):
        a = dict(r)
        a['kind'] = a.pop('sport') or 'Walk'
        acts.append(a)
    acts.sort(key=lambda a: str(a['startDateLocal']))
    by_kind = {}
    for a in acts:
        k = by_kind.setdefault(a['kind'], {'n': 0, 'dist': 0, 'time': 0, 'elev': 0, 'kcal': 0})
        k['n'] += 1
        k['dist'] += a.get('distance') or 0
        k['time'] += a.get('movingTime') or 0
        k['elev'] += a.get('totalElevationGain') or 0
        k['kcal'] += a.get('calories') or 0
    return {'rider': _rider_name(rider_id), 'start': s, 'end': e, 'activities': acts, 'by_kind': by_kind}


def activity_text(data, heading=True):
    acts = data['activities']
    out = []
    if heading:
        out += ['\x01ACTIVITY EXPORT', f"Rider: {data['rider']}", f"Period: {fdate(data['start'])} to {fdate(data['end'])} ({len(acts)} {'activity' if len(acts) == 1 else 'activities'})", '']
    out += ['HOW TO READ THIS: distances and speeds are given in miles and km. Calories are the device figure or',
            "the app's estimate (a percentage may have been taken off estimates). Heart rate in bpm, power in watts.", '']
    if not acts:
        return out + ['No activities in this period.']
    tot_d = sum(a.get('distance') or 0 for a in acts)
    tot_t = sum(a.get('movingTime') or 0 for a in acts)
    tot_e = sum(a.get('totalElevationGain') or 0 for a in acts)
    tot_k = sum(a.get('calories') or 0 for a in acts)
    out.append('\x01PERIOD SUMMARY')
    out.append(f"  {len(acts)} {'activity' if len(acts) == 1 else 'activities'} | {dist(tot_d)} | moving time {hms(tot_t)} | climbed {elev(tot_e)} | {n0(tot_k)} kcal")
    for k, v in sorted(data['by_kind'].items(), key=lambda kv: -kv[1]['dist']):
        out.append(f"  {k}: {v['n']} | {dist(v['dist'])} | {hms(v['time'])} | climbed {elev(v['elev'])} | {n0(v['kcal'])} kcal")
    out.append('')
    for a in acts:
        t = str(a['startDateLocal'])
        out.append('\x01=== ' + fdate(t) + ' ' + t[11:16] + '  ' + a['kind'] + (f'  "{a["name"]}"' if a.get('name') else '') + ' ===')
        l1 = [f"Distance {dist(a.get('distance'))}", f"moving {hms(a.get('movingTime'))}"]
        if a.get('elapsedTime') and a.get('elapsedTime') != a.get('movingTime'):
            l1.append(f"elapsed {hms(a['elapsedTime'])}")
        out.append('  ' + ' | '.join(l1))
        l2 = [f"avg speed {speed(a.get('averageSpeed'))}"]
        if a.get('maxSpeed'):
            l2.append(f"max {speed(a['maxSpeed'])}")
        if a.get('totalElevationGain'):
            l2.append(f"climbed {elev(a['totalElevationGain'])}")
        out.append('  ' + ' | '.join(l2))
        l3 = []
        if a.get('averageHeartrate'):
            l3.append(f"HR avg {round(a['averageHeartrate'])}" + (f" max {round(a['maxHeartrate'])}" if a.get('maxHeartrate') else ''))
        if a.get('averageWatts'):
            l3.append(f"power avg {round(a['averageWatts'])} W" + (f", weighted {round(a['weightedAvgWatts'])} W" if a.get('weightedAvgWatts') else '') + (f", max {round(a['maxWatts'])} W" if a.get('maxWatts') else ''))
        if a.get('kilojoules'):
            l3.append(f"work {round(a['kilojoules'])} kJ")
        if a.get('averageCadence'):
            l3.append(f"cadence {round(a['averageCadence'])} rpm")
        if a.get('steps'):
            l3.append(f"steps {n0(a['steps'])}")
        if a.get('calories'):
            l3.append(f"{n0(a['calories'])} kcal")
        if a.get('sufferScore'):
            l3.append(f"effort score {round(a['sufferScore'])}")
        if l3:
            out.append('  ' + ' | '.join(l3))
        w = []
        if a.get('weatherSummary'):                                    # already reads like "Cloudy, 9C, wind 18kph (gusts 35kph), headwind"
            w.append(a['weatherSummary'])
        elif a.get('weatherTempC') is not None:
            w.append(f"{a['weatherTempC']:.0f} C ({a['weatherTempC'] * 9 / 5 + 32:.0f} F)")
            if a.get('weatherWindKph'):
                w.append(f"wind {a['weatherWindKph'] / 1.609:.0f} mph ({a['weatherWindKph']:.0f} km/h)" + (f", gusts {a['weatherGustKph'] / 1.609:.0f} mph" if a.get('weatherGustKph') else '') + (f", {a['weatherWindRel']}" if a.get('weatherWindRel') else ''))
        if a.get('weatherRainMm'):
            w.append(f"rain {a['weatherRainMm']:.1f} mm")
        if w:
            out.append('  Weather: ' + ', '.join(w))
        meta = [x for x in (a.get('bike') and 'Bike: ' + a['bike'], a.get('city') and 'Near ' + a['city']) if x]
        if meta:
            out.append('  ' + ' | '.join(meta))
        for k, label in (('notes', 'Notes'), ('description', 'Description')):
            if a.get(k):
                out.append(f"  {label}: {' '.join(str(a[k]).split())[:600]}")
        out.append('')
    return out


# ---------------------------------------------------------------- body + recovery readings
METRIC_LABEL = {'body_fat': 'body fat', 'metabolic_age': 'metabolic age', 'muscle_mass': 'muscle mass', 'body_water': 'body water', 'bone_mass': 'bone mass',
                'visceral_fat': 'visceral fat', 'bmi': 'BMI', 'bmr': 'BMR'}


def body_data(rider_id, start, end):
    """One row per day that has any reading: weight, every body metric the scale/HA import brought in (body fat, metabolic age...), and the Garmin
    recovery numbers (resting HR, HRV, sleep, body battery, stress, steps). Step counts use the same best-source rule as the rest of the app."""
    s, e = start.isoformat(), end.isoformat()
    rows = {}
    for r in query_db('SELECT logDate, weightKg FROM WeightLog WHERE riderId=? AND logDate BETWEEN ? AND ? ORDER BY logDate', [rider_id, s, e]):
        rows.setdefault(r['logDate'], {})['weight_kg'] = r['weightKg']
    metrics = []
    for r in query_db("SELECT logDate, metric, value, unit FROM BodyMetric WHERE riderId=? AND logDate BETWEEN ? AND ? AND metric NOT LIKE 'steps%' ORDER BY logDate", [rider_id, s, e]):
        rows.setdefault(r['logDate'], {}).setdefault('metrics', {})[r['metric']] = (r['value'], r['unit'] or '')
        if r['metric'] not in metrics:
            metrics.append(r['metric'])
    try:
        for r in query_db('SELECT date, restingHR, hrv, sleepHours, sleepScore, bodyBattery, stressScore FROM GarminDaily WHERE date BETWEEN ? AND ? ORDER BY date', [s, e]):
            g = {k: r[k] for k in ('restingHR', 'hrv', 'sleepHours', 'sleepScore', 'bodyBattery', 'stressScore') if r[k] is not None}
            if g:
                rows.setdefault(r['date'], {})['garmin'] = g
    except Exception:
        pass
    d = start
    while d <= end:
        iso = d.isoformat()
        if iso in rows:
            st = _steps(rider_id, iso)
            if st:
                rows[iso]['steps'] = st
        d += timedelta(days=1)
    return {'rider': _rider_name(rider_id), 'start': s, 'end': e, 'rows': dict(sorted(rows.items())), 'metrics': metrics}


def _avg(vals):
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


def body_text(data, heading=True):
    rows = data['rows']
    out = []
    if heading:
        out += ['\x01BODY AND RECOVERY EXPORT', f"Rider: {data['rider']}", f"Period: {fdate(data['start'])} to {fdate(data['end'])} ({len(rows)} days with readings)", '']
    out += ['HOW TO READ THIS: weight comes from the scale or manual weigh-ins; body composition from the scale; resting HR, HRV, sleep, body battery and stress',
            'from Garmin (when connected). A missing value means no reading that day, not zero.', '']
    if not rows:
        return out + ['No readings in this period.']
    out.append('\x01PERIOD SUMMARY')
    ws = [(d, r['weight_kg']) for d, r in rows.items() if r.get('weight_kg')]
    if len(ws) >= 2:
        out.append(f"  Weight: {kg_str(ws[0][1])} on {fdate(ws[0][0])} -> {kg_str(ws[-1][1])} on {fdate(ws[-1][0])} (change {ws[-1][1] - ws[0][1]:+.1f} kg over {len(ws)} weigh-ins)")
    elif ws:
        out.append(f"  Weight: {kg_str(ws[0][1])} on {fdate(ws[0][0])}")
    for m in data['metrics']:
        vs = [(d, r['metrics'][m]) for d, r in rows.items() if m in r.get('metrics', {})]
        u = vs[0][1][1]
        lab = METRIC_LABEL.get(m, m.replace('_', ' '))
        out.append(f"  {lab}: {vs[0][1][0]:g}{u} -> {vs[-1][1][0]:g}{u} (average {sum(v[1][0] for v in vs) / len(vs):.1f}{u}, {len(vs)} readings)")
    gl = [r['garmin'] for r in rows.values() if r.get('garmin')]
    for key, lab, fm in (('restingHR', 'resting heart rate', '{:.0f} bpm'), ('hrv', 'HRV', '{:.0f} ms'), ('sleepHours', 'sleep', '{:.1f} h'), ('sleepScore', 'sleep score', '{:.0f}'),
                         ('bodyBattery', 'body battery', '{:.0f}'), ('stressScore', 'stress', '{:.0f}')):
        a = _avg([g.get(key) for g in gl])
        if a is not None:
            out.append(f"  Average {lab}: " + fm.format(a))
    out += ['', '\x01DAILY READINGS']
    for d, r in rows.items():
        bits = []
        if r.get('weight_kg'):
            bits.append('weight ' + kg_str(r['weight_kg']))
        for m, (v, u) in (r.get('metrics') or {}).items():
            bits.append(f"{METRIC_LABEL.get(m, m.replace('_', ' '))} {v:g}{u}")
        g = r.get('garmin') or {}
        for key, lab, fm in (('restingHR', 'resting HR', '{:.0f} bpm'), ('hrv', 'HRV', '{:.0f} ms'), ('sleepHours', 'sleep', '{:.1f} h'), ('sleepScore', 'sleep score', '{:.0f}'),
                             ('bodyBattery', 'body battery', '{:.0f}'), ('stressScore', 'stress', '{:.0f}')):
            if g.get(key) is not None:
                bits.append(f"{lab} " + fm.format(g[key]))
        if r.get('steps'):
            bits.append(f"steps {n0(r['steps'])}")
        out.append(f"{fdate(d)}: " + ' | '.join(bits))
    return out


# ---------------------------------------------------------------- public entry
def render_text(rider_id, start, end, what=('food', 'activities'), full=False):
    """-> the export as one string. `what` is any of 'food', 'activities', 'body'."""
    what = [w for w in ('food', 'activities', 'body') if w in what] or ['food']
    names = {'food': 'FOOD DIARY', 'activities': 'ACTIVITIES', 'body': 'BODY AND RECOVERY'}
    head = ['\x01HEADWIND EXPORT: ' + ' + '.join(names[w] for w in what), f'Rider: {_rider_name(rider_id)}', f'Period: {fdate(start)} to {fdate(end)}',
            f'Generated: {datetime.now():%Y-%m-%d %H:%M}', '']
    out = list(head)
    if 'food' in what:
        out += food_text(food_data(rider_id, start, end), heading=False, full=full)
    if 'activities' in what:
        if 'food' in what:
            out += ['-' * 72, '']
        out += ['\x01ACTIVITIES'] + activity_text(activity_data(rider_id, start, end), heading=False)
    if 'body' in what:
        if 'food' in what or 'activities' in what:
            out += ['', '-' * 72, '']
        out += ['\x01BODY AND RECOVERY'] + body_text(body_data(rider_id, start, end), heading=False)
    return '\n'.join(out).rstrip() + '\n'


def day_counts(rider_id, first, last):
    """{iso date: {'food': n entries, 'act': n activities}} for the calendar's dots."""
    out = {}
    for r in query_db('SELECT logDate d, COUNT(*) n FROM FoodLog WHERE riderId=? AND logDate BETWEEN ? AND ? GROUP BY logDate', [rider_id, first.isoformat(), last.isoformat()]):
        out.setdefault(r['d'], {})['food'] = r['n']
    for tbl in ('Activity', 'Workout'):
        for r in query_db(f'SELECT date(startDateLocal) d, COUNT(*) n FROM {tbl} WHERE riderId=? AND date(startDateLocal) BETWEEN ? AND ? GROUP BY date(startDateLocal)',
                          [rider_id, first.isoformat(), last.isoformat()]):
            out.setdefault(r['d'], {})['act'] = out.get(r['d'], {}).get('act', 0) + r['n']
    return out
