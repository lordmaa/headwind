"""Diary summary: a short, honest verdict on one day's food choices, in the context of the exercise around it. It judges what was
eaten; it never recommends meals or says what to eat next.

Same shape as services/weighin.py: deterministic facts and highlights that always work, optional AI wording on top that is
shown only the numbers and never asked to invent any. One summary is cached per day and goes stale when the day's log
changes; it is generated on request (page view with enough entries, or the Refresh button), never in the background, so
the AI provider is only called when someone is looking.
"""
import hashlib
import json
import logging
from datetime import date, timedelta

from database import get_db, query_db
from services import weighin

log = logging.getLogger(__name__)

MIN_ENTRIES = 3                      # fewer than this and there is nothing worth summarising yet


def enabled():
    row = query_db('SELECT diarySummary FROM Settings WHERE id=1', one=True)
    return bool(row and (row['diarySummary'] is None or row['diarySummary']))


def ensure_schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS DiarySummary (
        riderId INTEGER NOT NULL, logDate TEXT NOT NULL, fingerprint TEXT, headline TEXT, good TEXT, watch TEXT, tip TEXT,
        source TEXT, createdAt TEXT DEFAULT (datetime('now')), PRIMARY KEY (riderId, logDate))''')


def _entries(rider_id, day):
    return query_db('''SELECT id, mealType, foodName, brand, calories, protein, carbs, fat, fibre, sugar, alcoholUnits,
                              quantity, servingG, nutriScoreGrade
                       FROM FoodLog WHERE riderId=? AND logDate=? ORDER BY id''', [rider_id, day])


def fingerprint(rider_id, day):
    rows = _entries(rider_id, day)
    raw = json.dumps([(r['id'], r['foodName'], round(r['calories'] or 0), r['quantity']) for r in rows])
    ex = weighin._day_exercise(rider_id, date.fromisoformat(day), date.fromisoformat(day)).get(day, [])
    raw += json.dumps([(e['kind'], e['minutes']) for e in ex])
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def build_facts(rider_id, day):
    from services import goal_model
    d = date.fromisoformat(day)
    rows = _entries(rider_id, day)
    goals = query_db('SELECT nutritionCalGoal, nutritionProteinGoal, nutritionCarbGoal, nutritionFatGoal, nutritionSugarGoal FROM Settings WHERE id=1', one=True)
    tot = lambda k: sum((r[k] or 0) for r in rows)
    kcal = tot('calories')
    burn = goal_model.day_burn(rider_id, day)
    ex = weighin._day_exercise(rider_id, d - timedelta(days=2), d)
    meals = {}
    for r in rows:
        meals.setdefault(r['mealType'] or 'other', []).append({
            'food': r['foodName'] + (f" ({r['brand']})" if r['brand'] else ''), 'kcal': round(r['calories'] or 0),
            'protein_g': round(r['protein'] or 0), 'carbs_g': round(r['carbs'] or 0), 'fat_g': round(r['fat'] or 0),
            'sugar_g': round(r['sugar'] or 0), 'alcohol_units': round(r['alcoholUnits'] or 0, 1), 'grade': r['nutriScoreGrade']})
    top = sorted(rows, key=lambda r: -(r['calories'] or 0))[:3]
    prior = [weighin._day_food(rider_id, d - timedelta(days=i), d - timedelta(days=i)).get((d - timedelta(days=i)).isoformat()) for i in range(1, 8)]
    prior_kcal = [p['kcal'] for p in prior if p and p['kcal'] and p['kcal'] >= weighin.MIN_LOGGED_KCAL]
    return {
        'date': day, 'weekday': d.strftime('%A'), 'is_today': d == date.today(), 'entries': len(rows),
        'totals': {'kcal': round(kcal), 'protein_g': round(tot('protein')), 'carbs_g': round(tot('carbs')), 'fat_g': round(tot('fat')),
                   'fibre_g': round(tot('fibre')), 'sugar_g': round(tot('sugar')), 'alcohol_units': round(tot('alcoholUnits'), 1)},
        'goals': {'kcal': goals['nutritionCalGoal'] if goals else None, 'protein_g': goals['nutritionProteinGoal'] if goals else None,
                  'carbs_g': goals['nutritionCarbGoal'] if goals else None, 'fat_g': goals['nutritionFatGoal'] if goals else None,
                  'fibre_g': 30, 'sugar_reference_g': (goals['nutritionSugarGoal'] if goals and goals['nutritionSugarGoal'] else 50)},
        'burn_kcal': burn[0] if burn else None,
        'planned_deficit_kcal': round(goal_model.profile()['loss_lb_week'] * 500),
        'seven_day_avg_kcal': round(sum(prior_kcal) / len(prior_kcal)) if prior_kcal else None,
        'meals': meals,
        'biggest_items': [{'food': r['foodName'], 'kcal': round(r['calories'] or 0), 'share_pct': round(100 * (r['calories'] or 0) / kcal) if kcal else 0} for r in top],
        'poor_grade_items': [r['foodName'] for r in rows if (r['nutriScoreGrade'] or '').lower() in ('d', 'e')][:5],
        'exercise': {x: ex.get(x, []) for x in sorted(ex)},
        'steps': weighin._steps(rider_id, day),
    }


def highlights(f):
    """Deterministic read of the day: [{'tone': 'good'|'watch', 'text': ...}]. Always available."""
    t, g = f['totals'], f['goals']
    out = []
    add = lambda tone, text: out.append({'tone': tone, 'text': text})
    sessions = [e for v in f['exercise'].values() for e in v]
    today_ex = f['exercise'].get(f['date'], [])
    mins_today = sum(e['minutes'] for e in today_ex)
    mins_2d = sum(sum(e['minutes'] for e in v) for k, v in f['exercise'].items() if k < f['date'])
    if f['burn_kcal'] and t['kcal'] and not f['is_today']:
        gap = t['kcal'] - f['burn_kcal']
        add('good' if -900 <= gap <= 150 else 'watch',
            f"{t['kcal']:,} kcal eaten against about {f['burn_kcal']:,} burned: {'a deficit of' if gap < 0 else 'a surplus of'} {abs(gap):,}.")
    elif g['kcal'] and t['kcal']:
        # over the goal only matters if it eats the deficit the day's exercise made (same rule as the phone's Fuel screen)
        line = f"{t['kcal']:,} kcal so far against a {g['kcal']:,} goal."
        over = t['kcal'] - g['kcal']
        if over > 0 and f['burn_kcal']:
            plan = max(250, f.get('planned_deficit_kcal') or 750)
            deficit = round(f['burn_kcal'] - t['kcal'])
            if deficit >= 0.6 * plan:
                add('good', f"{line} {over:,} over the goal, but exercise covers it: still {deficit:,} kcal under what you burn (your plan is {plan:,}).")
            elif deficit >= 0.25 * plan:
                add('watch', f"{line} {over:,} over the goal and eating into your deficit: {deficit:,} kcal under what you burn, against a plan of {plan:,}.")
            else:
                add('watch', line + (f" Only {deficit:,} kcal under what you burn, so the deficit is almost gone." if deficit >= 0 else f" {-deficit:,} kcal over what you burn."))
        else:
            add('good' if over <= 0 else 'watch', line)
    if g['protein_g'] and t['protein_g']:
        ok = t['protein_g'] >= 0.85 * g['protein_g']
        extra = ' It was a hard training day, so that protein matters for recovery.' if mins_today >= 60 or mins_2d >= 150 else ''
        add('good' if ok else 'watch', f"Protein {t['protein_g']} g of {g['protein_g']} g.{extra if not ok or extra else ''}")
    if mins_today >= 60 and g['carbs_g'] and t['carbs_g']:
        add('good' if t['carbs_g'] >= 0.8 * g['carbs_g'] else 'watch',
            f"You trained {mins_today} minutes today and ate {t['carbs_g']} g of carbs" + ('.' if t['carbs_g'] >= 0.8 * g['carbs_g'] else f", under the {g['carbs_g']} g aimed at: a long session wants fuel."))
    if t['fibre_g'] is not None and f['entries'] >= 4:
        add('good' if t['fibre_g'] >= 25 else 'watch', f"Fibre {t['fibre_g']} g of 30 g.")
    if t['sugar_g'] and t['sugar_g'] > 1.3 * g['sugar_reference_g']:
        add('watch', f"Total sugar {t['sugar_g']} g, over the {g['sugar_reference_g']} g reference (this counts the sugar in fruit and milk too).")
    if t['alcohol_units'] >= 3:
        add('watch', f"{t['alcohol_units']} units of alcohol.")
    if f['biggest_items'] and f['biggest_items'][0]['share_pct'] >= 30:
        b = f['biggest_items'][0]
        add('watch', f"{b['food']} was {b['share_pct']}% of the day's calories.")
    if f['poor_grade_items']:
        add('watch', 'Lower-quality choices (Nutri-Score D or E): ' + ', '.join(f['poor_grade_items'][:3]) + '.')
    if f['seven_day_avg_kcal'] and t['kcal'] and not f['is_today']:
        diff = t['kcal'] - f['seven_day_avg_kcal']
        if abs(diff) >= 350:
            add('watch' if diff > 0 else 'good', f"{abs(diff):,} kcal {'above' if diff > 0 else 'below'} your recent daily average of {f['seven_day_avg_kcal']:,}.")
    return out


_SYSTEM = (
    "You give a verdict on one day of someone's food diary, in the context of their recent exercise, like a sensible coach who has "
    "seen their numbers. JUDGE WHAT IS THERE: what the day's choices amounted to, what was good about them, what was poor or "
    "unbalanced, and how they fit the training. Do NOT recommend, suggest or plan anything: no 'try', 'consider', 'aim for', no "
    "swaps, no meal ideas, no advice about what to eat next, and nothing about what is left to eat. Use ONLY the FACTS and "
    "HIGHLIGHTS given; never invent foods, numbers or exercise; name the actual foods. Be balanced and candid, warm but not "
    "gushing, UK English, no medical advice, no moralising. If the day is still in progress (is_today) judge only what is logged so "
    "far. The sugar figure is TOTAL sugars, which includes the natural sugar in fruit, milk and veg, and its reference is guidance "
    "not a limit: describe it as information, never as dangerous. "
    "CALORIES: the goal already assumes planned_deficit_kcal. Exercise burns extra on top, so being over the goal is NOT a problem while "
    "burn_kcal minus eaten kcal is still well above half of planned_deficit_kcal; say it is covered by the exercise. Only call calories "
    "a concern when the day is eating most of that deficit or has gone over what was burned. Reply with JSON only: "
    "{\"headline\": string (max 100 chars, the verdict in a line), \"good\": [up to 3 short sentences], "
    "\"watch\": [up to 3 short sentences about what was poor, unbalanced or notable]}."
)


def _ai_wording(facts, hl):
    from services.ai import _get_client_and_model
    client, model = _get_client_and_model()
    if not client:
        return None
    prompt = 'FACTS:\n' + json.dumps(facts, default=str) + '\n\nHIGHLIGHTS:\n' + json.dumps(hl)
    resp = client.chat.completions.create(model=model, temperature=0.4, max_tokens=500, response_format={'type': 'json_object'},
                                          messages=[{'role': 'system', 'content': _SYSTEM}, {'role': 'user', 'content': prompt}], timeout=45)
    d = json.loads(resp.choices[0].message.content)
    clean = lambda xs: [str(x).strip() for x in (xs or []) if str(x).strip()][:3]
    head = str(d.get('headline') or '').strip()
    if not head:
        return None
    return {'headline': head[:140], 'good': clean(d.get('good')), 'watch': clean(d.get('watch')), 'tip': ''}


def generate(rider_id, day, use_ai=None):
    """Build and store the summary for `day`. Returns the stored dict, or None if disabled or too little logged."""
    if not enabled():
        return None
    facts = build_facts(rider_id, day)
    if facts['entries'] < MIN_ENTRIES:
        return None
    hl = highlights(facts)
    wording, source = None, 'rules'
    if (weighin.ai_wording_enabled() if use_ai is None else use_ai):
        try:
            wording = _ai_wording(facts, hl)
            source = 'ai' if wording else 'rules'
        except Exception as e:
            log.warning('diary summary AI wording failed (%s); using rule-based wording', e)
    if not wording:
        good = [h['text'] for h in hl if h['tone'] == 'good'][:3]
        watch = [h['text'] for h in hl if h['tone'] == 'watch'][:3]
        wording = {'headline': f"{facts['totals']['kcal']:,} kcal across {facts['entries']} entries" + (' so far' if facts['is_today'] else ''),
                   'good': good, 'watch': watch, 'tip': ''}
    db = get_db()
    ensure_schema(db)
    db.execute('''INSERT OR REPLACE INTO DiarySummary (riderId, logDate, fingerprint, headline, good, watch, tip, source)
                  VALUES (?,?,?,?,?,?,?,?)''', [rider_id, day, fingerprint(rider_id, day), wording['headline'], json.dumps(wording['good']),
                                                json.dumps(wording['watch']), wording['tip'], source])
    db.commit()
    return get(rider_id, day)


def get(rider_id, day):
    db = get_db()
    ensure_schema(db)
    r = db.execute('SELECT * FROM DiarySummary WHERE riderId=? AND logDate=?', [rider_id, day]).fetchone()
    if not r:
        return None
    return {'date': r['logDate'], 'headline': r['headline'], 'good': json.loads(r['good'] or '[]'), 'watch': json.loads(r['watch'] or '[]'),
            'tip': r['tip'] or '', 'source': r['source'], 'created': r['createdAt'], 'stale': r['fingerprint'] != fingerprint(rider_id, day)}
