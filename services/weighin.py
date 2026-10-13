"""Weigh-in verdict: why the scale says what it says today.

A single weigh-in is mostly water, gut contents and glycogen, not fat. This service gathers what happened around a
weigh-in (food, burn, training and when it happened, steps, sleep, alcohol, water), works out in plain arithmetic whether
the move on the scale could possibly be fat, and then writes a short verdict.

Two layers, deliberately:
  1. `build_facts` + `classify` are deterministic: the numbers, the energy-balance check and the candidate causes. They
     always work, with or without an AI key, and they are what the verdict is held to.
  2. `generate` optionally asks the configured AI provider to put that into words. It is given only the facts, must not
     contradict the classification, and any failure (no key, timeout, bad JSON) falls back to the deterministic wording.

Verdicts are cached per rider and day in `WeighInVerdict` and regenerated when that day's weigh-in changes.
Units: the display is pounds (the display unit follows the rider's setting); stored weights stay kilograms.
"""
import json
import logging
import statistics
from datetime import date, datetime, timedelta

from database import get_db, query_db

log = logging.getLogger(__name__)

LB = 2.20462
KCAL_PER_KG = 7700                 # energy in a kilogram of body fat
MIN_LOGGED_KCAL = 1200             # a day under this was not fully logged: do not read it as a deficit
LOOKBACK_DAYS = 5                  # days before the weigh-in shown to the writer
BASELINE_DAYS = 28                 # what "your usual" means

# Rough, honest sizes of the usual water effects (kg), used to say whether a cause can account for the move.
CARB_WATER_KG_PER_100G = 0.3       # glycogen holds ~3 g water per g, so +100 g carbs ~ +0.3 kg
HARD_DAY_WATER_KG = (0.3, 1.0)     # inflammation / repair water after an unusually long or hard day
ALCOHOL_NEXT_DAY_KG = (0.2, 0.8)   # dehydrated then rebounds / retains


def enabled():
    row = query_db('SELECT weighInVerdict FROM Settings WHERE id=1', one=True)
    return bool(row and (row['weighInVerdict'] is None or row['weighInVerdict']))


def ai_wording_enabled():
    row = query_db('SELECT weighInAi FROM Settings WHERE id=1', one=True)
    return bool(row and (row['weighInAi'] is None or row['weighInAi']))


def _d(s):
    return date.fromisoformat(str(s)[:10])


def _lb(kg):
    return round(kg * LB, 1) if kg is not None else None


def _sd(vals):
    return statistics.pstdev(vals) if len(vals) >= 3 else None


def _london_hour(created_utc):
    """Hour of day (Europe/London) a weigh-in was recorded, from the UTC `createdAt` stamp, or None."""
    try:
        from zoneinfo import ZoneInfo
        dt = datetime.strptime(str(created_utc)[:19], '%Y-%m-%d %H:%M:%S').replace(tzinfo=ZoneInfo('UTC'))
        return dt.astimezone(ZoneInfo('Europe/London')).hour
    except Exception:
        return None


# ── facts ────────────────────────────────────────────────────────────────────────────────────

def _day_food(rider_id, start, end):
    rows = query_db('''SELECT logDate d, SUM(calories) kcal, SUM(carbs) carbs, SUM(protein) protein, SUM(fat) fat, SUM(fibre) fibre,
                              SUM(sugar) sugar, SUM(alcoholUnits) alc, COUNT(*) n
                       FROM FoodLog WHERE riderId=? AND logDate BETWEEN ? AND ? GROUP BY logDate''',
                    [rider_id, start.isoformat(), end.isoformat()])
    return {r['d']: r for r in rows}


def _day_exercise(rider_id, start, end):
    """{date: [ {kind, name, minutes, km, kcal, hour} ]} from rides and recorded workouts."""
    out = {}
    for r in query_db('''SELECT name, sportType, movingTime, distance, calories, startDateLocal FROM Activity
                         WHERE riderId=? AND date(startDateLocal) BETWEEN ? AND ?''', [rider_id, start.isoformat(), end.isoformat()]):
        out.setdefault(str(r['startDateLocal'])[:10], []).append(
            {'kind': r['sportType'] or 'Ride', 'minutes': round((r['movingTime'] or 0) / 60), 'km': round((r['distance'] or 0) / 1000, 1),
             'kcal': int(r['calories'] or 0), 'hour': _hour(r['startDateLocal'])})
    for r in query_db('''SELECT name, sport, movingTime, distance, calories, startDateLocal FROM Workout
                         WHERE riderId=? AND date(startDateLocal) BETWEEN ? AND ?''', [rider_id, start.isoformat(), end.isoformat()]):
        out.setdefault(str(r['startDateLocal'])[:10], []).append(
            {'kind': r['sport'] or 'Walk', 'minutes': round((r['movingTime'] or 0) / 60), 'km': round((r['distance'] or 0) / 1000, 1),
             'kcal': int(r['calories'] or 0), 'hour': _hour(r['startDateLocal'])})
    return out


def _hour(s):
    try:
        return int(str(s)[11:13])
    except ValueError:
        return None


def _steps(rider_id, day):
    try:
        from services.mqtt import _best_steps_for
        g = query_db('SELECT steps FROM GarminDaily WHERE date=?', [day], one=True)
        return _best_steps_for(rider_id, day, g['steps'] if g else None) or None
    except Exception:
        return None


def build_facts(rider_id, day):
    """Everything known about the weigh-in on `day` (ISO string), or None if there is no weigh-in that day."""
    from services import goal_model
    from services.weight_trend import compute_trend

    today = _d(day)
    w = query_db('SELECT weightKg, createdAt, source FROM WeightLog WHERE riderId=? AND logDate=?', [rider_id, day], one=True)
    if not w:
        return None
    hist = query_db('SELECT logDate, weightKg FROM WeightLog WHERE riderId=? AND logDate<=? ORDER BY logDate', [rider_id, day])
    entries = [(_d(r['logDate']), r['weightKg']) for r in hist]
    trend = compute_trend(entries) if entries else {}
    prev = [(d, kg) for d, kg in entries if d < today]
    prior_trend = trend.get(today - timedelta(days=1)) if prev else None
    if prior_trend is None and prev:
        prior_trend = prev[-1][1]

    # day-to-day scatter around the trend: how much the scale normally jiggles for this person
    resid = []
    for d, kg in prev[-40:]:
        t = trend.get(d - timedelta(days=1))
        if t is not None:
            resid.append(kg - t)
    noise_kg = _sd(resid)

    week = [kg for d, kg in prev if (today - d).days <= 7]
    wk_mean = sum(week) / len(week) if week else None

    start = today - timedelta(days=BASELINE_DAYS)
    food = _day_food(rider_id, start, today)
    ex = _day_exercise(rider_id, start, today)

    def day_row(d):
        iso = d.isoformat()
        f = food.get(iso)
        kcal = int(f['kcal']) if f and f['kcal'] else None
        burn = goal_model.day_burn(rider_id, iso)
        g = query_db('SELECT sleepHours FROM GarminDaily WHERE date=?', [iso], one=True)
        hyd = query_db('SELECT SUM(ml) m FROM HydrationLog WHERE riderId=? AND logDate=?', [rider_id, iso], one=True)
        return {
            'date': iso, 'weekday': d.strftime('%a'),
            'kcal': kcal, 'logged': bool(kcal and kcal >= MIN_LOGGED_KCAL),
            'carbs_g': round(f['carbs']) if f and f['carbs'] else None,
            'protein_g': round(f['protein']) if f and f['protein'] else None,
            'fibre_g': round(f['fibre']) if f and f['fibre'] else None,
            'alcohol_units': round(f['alc'], 1) if f and f['alc'] else 0,
            'burn_kcal': burn[0] if burn else None,
            'net_kcal': (kcal - burn[0]) if (kcal and burn and kcal >= MIN_LOGGED_KCAL) else None,
            'steps': _steps(rider_id, iso),
            'sleep_h': round(g['sleepHours'], 1) if g and g['sleepHours'] else None,
            'water_ml': int(hyd['m']) if hyd and hyd['m'] else None,
            'exercise': ex.get(iso, []),
            'exercise_minutes': sum(e['minutes'] for e in ex.get(iso, [])),
        }

    lookback = [day_row(today - timedelta(days=i)) for i in range(LOOKBACK_DAYS, -1, -1)]   # oldest .. weigh-in day
    before = [r for r in lookback if r['date'] < day]                                          # the days that fed this weigh-in

    # what is "usual": the baseline window before the lookback
    base_days = [day_row(today - timedelta(days=i)) for i in range(LOOKBACK_DAYS + 1, BASELINE_DAYS + 1)]
    logged = [r for r in base_days if r['logged']]
    usual = {
        'kcal': round(statistics.mean(r['kcal'] for r in logged)) if logged else None,
        'carbs_g': round(statistics.mean(r['carbs_g'] for r in logged if r['carbs_g'])) if any(r['carbs_g'] for r in logged) else None,
        'steps': round(statistics.mean(r['steps'] for r in base_days if r['steps'])) if any(r['steps'] for r in base_days) else None,
        'exercise_minutes_per_day': round(sum(r['exercise_minutes'] for r in base_days) / max(1, len(base_days))),
        'longest_session_min': max((e['minutes'] for r in base_days for e in r['exercise']), default=0),
        'alcohol_units_per_day': round(sum(r['alcohol_units'] for r in base_days) / max(1, len(base_days)), 1),
    }

    # consecutive days with a ride/workout ending at the day before the weigh-in
    streak = 0
    for r in reversed(before):
        if r['exercise_minutes'] >= 20:
            streak += 1
        else:
            break

    net3 = [r['net_kcal'] for r in before[-3:] if r['net_kcal'] is not None]
    net7 = [r['net_kcal'] for r in [day_row(today - timedelta(days=i)) for i in range(7, 0, -1)] if r['net_kcal'] is not None]
    bal3 = sum(net3) if len(net3) >= 2 else None
    bal7 = sum(net7) if len(net7) >= 4 else None

    delta_prev = (w['weightKg'] - prev[-1][1]) if prev else None
    vs_trend = (w['weightKg'] - prior_trend) if prior_trend is not None else None
    facts = {
        'date': day, 'weekday': today.strftime('%a'),
        'weigh_in': {'lb': _lb(w['weightKg']), 'kg': round(w['weightKg'], 2), 'source': w['source'],
                     'hour': _london_hour(w['createdAt']),
                     'vs_previous_lb': _lb(delta_prev), 'days_since_previous': (today - prev[-1][0]).days if prev else None,
                     'vs_trend_lb': _lb(vs_trend), 'vs_7day_mean_lb': _lb(w['weightKg'] - wk_mean) if wk_mean else None,
                     'trend_lb': _lb(prior_trend), 'usual_scatter_lb': _lb(noise_kg)},
        'days': lookback,
        'usual': usual,
        'balance': {'last3_net_kcal': bal3, 'last7_net_kcal': bal7,
                    'fat_equivalent_last3_lb': _lb(bal3 / KCAL_PER_KG) if bal3 is not None else None,
                    'fat_equivalent_last7_lb': _lb(bal7 / KCAL_PER_KG) if bal7 is not None else None},
        'training_streak_days': streak,
        'weigh_ins_known': len(entries),
    }
    return facts


# ── the deterministic verdict ───────────────────────────────────────────────────────────────────

def classify(f):
    """Verdict from the numbers alone: {category, headline, reasons[], confidence, direction}. Never raises on thin data."""
    wi = f['weigh_in']
    move = wi['vs_trend_lb'] if wi['vs_trend_lb'] is not None else wi['vs_previous_lb']
    if move is None:
        return {'category': 'first', 'direction': 0, 'confidence': 'low', 'move_lb': None,
                'headline': 'First weigh-in: nothing to compare against yet.', 'reasons': []}
    scatter = wi['usual_scatter_lb'] or 1.2
    big = max(1.0, scatter * 1.2)                          # bigger than the usual jiggle
    direction = 1 if move > 0 else -1 if move < 0 else 0
    if abs(move) < max(0.6, scatter * 0.6):
        return {'category': 'steady', 'direction': direction, 'confidence': 'good', 'move_lb': move,
                'headline': 'Right on your trend: nothing to read into.', 'reasons': []}

    days = f['days']
    before = [r for r in days if r['date'] < f['date']]
    last = before[-1] if before else None
    usual = f['usual']
    bal3, fat3 = f['balance']['last3_net_kcal'], f['balance']['fat_equivalent_last3_lb']
    reasons = []                                           # (weight in lb, sentence), biggest first
    texts_extra = []                                       # context lines that do not size a cause
    expl = 0.0                                             # lb of the move that causes can account for

    def add(lb, text):
        nonlocal expl
        reasons.append((lb, text))
        expl += lb

    # carbs vs usual on the days that fed it
    if usual['carbs_g'] and last and last['carbs_g']:
        extra = last['carbs_g'] - usual['carbs_g']
        if extra >= 60:
            lb = round(extra / 100 * CARB_WATER_KG_PER_100G * LB, 1)
            add(lb, f"Yesterday's carbs were {last['carbs_g']} g against your usual {usual['carbs_g']} g. Each extra 100 g stores about 0.3 kg of water, so around {lb} lb of this is glycogen water.")
        elif extra <= -60:
            lb = round(-extra / 100 * CARB_WATER_KG_PER_100G * LB, 1)
            add(-lb, f"Yesterday's carbs were {last['carbs_g']} g against your usual {usual['carbs_g']} g, so you are carrying less glycogen water (about {lb} lb).")
    # training
    ex_days = [r for r in before[-2:] if r['exercise_minutes']]
    long_min = max((e['minutes'] for r in before[-2:] for e in r['exercise']), default=0)
    if long_min and usual['longest_session_min'] and long_min >= 1.25 * usual['longest_session_min'] and long_min >= 60:
        add(round(sum(HARD_DAY_WATER_KG) / 2 * LB, 1), f"A {long_min}-minute session is longer than your usual longest of {usual['longest_session_min']} minutes, and muscles hold extra water for a day or two while they repair.")
    elif f['training_streak_days'] >= 3:
        add(round(0.5 * LB, 1), f"That is {f['training_streak_days']} training days in a row: muscle repair and glycogen refill both hold water.")
    elif ex_days and sum(r['exercise_minutes'] for r in ex_days) >= 90:
        add(round(0.4 * LB, 1), f"You trained {sum(r['exercise_minutes'] for r in ex_days)} minutes over the last two days, and muscles retain water while they recover.")
    # a session earlier on the weigh-in day: sweat loss makes the scale read LOW, so a rise is, if anything, understated
    today_row = days[-1] if days else None
    if direction > 0 and today_row and wi['hour'] is not None:
        done = [e for e in today_row['exercise'] if e['hour'] is not None and e['hour'] < wi['hour'] and e['minutes'] >= 45]
        if done:
            mins = sum(e['minutes'] for e in done)
            texts_extra.append(f"You had already trained {mins} minutes today before weighing in, and sweat loss normally pulls the scale down, so this rise is if anything understated.")
    # alcohol
    alc = last['alcohol_units'] if last else 0
    if alc >= 3 and alc > 2 * (usual['alcohol_units_per_day'] or 0):
        add(round(sum(ALCOHOL_NEXT_DAY_KG) / 2 * LB, 1), f"{alc} units of alcohol yesterday: the body holds fluid the day after.")
    # sleep
    sl = days[-1]['sleep_h'] if days else None
    if sl is not None and sl < 6:
        add(round(0.3 * LB, 1), f"Only {sl} h of sleep last night, which raises cortisol and water retention.")
    # fewer logged calories + deficit: a drop that real fat can partly explain
    real_note = None
    if fat3 is not None:
        if move > 0:
            if fat3 <= 0.3:
                gain = max(0, fat3)
                real_note = (f"Your last 3 days were {abs(bal3):,.0f} kcal {'under' if bal3 < 0 else 'around'} what you burn, so "
                             + ("no fat gain is possible and none of this rise can be fat." if gain < 0.1
                                else f"at most {gain:.1f} lb of fat gain is possible; the rest of the rise cannot be fat."))
            else:
                real_note = f"Your last 3 days were {bal3:,.0f} kcal over what you burn, worth about {fat3:.1f} lb of fat at most."
        else:
            if fat3 <= -0.3:
                real_note = f"Your last 3 days were {abs(bal3):,.0f} kcal under what you burn, worth about {abs(fat3):.1f} lb of fat: a real contribution."
            else:
                real_note = f"Your last 3 days were roughly at maintenance ({bal3:+,.0f} kcal), so fat loss cannot explain much of this drop."

    reasons.sort(key=lambda r: -abs(r[0]))
    texts = [t for _, t in reasons] + ([real_note] if real_note else []) + texts_extra   # the energy-balance line must survive the cut below

    if direction > 0:
        fat_possible = max(0.0, fat3 or 0.0)
        explained = expl + fat_possible
        fat7 = f['balance']['fat_equivalent_last7_lb'] or 0
        if fat3 is not None and fat3 > 0.3 and fat7 >= 0.5 * abs(move):      # a real surplus, sustained over the week, can account for it
            cat, head = 'real_gain', f"Up {abs(move):.1f} lb and your calories back it up: this one is probably real."
            conf = 'fair'
        elif explained >= 0.6 * abs(move) or (fat3 is not None and fat3 <= 0.3 and (reasons or abs(move) >= big)):
            cat, head = 'water_up', f"Up {abs(move):.1f} lb, but it is water, not fat."
            conf = 'good' if explained >= 0.6 * abs(move) else 'fair'
        else:
            cat, head = 'noise_up', f"Up {abs(move):.1f} lb: well within normal day-to-day swings. Wait for the next one before reading anything into it."
            conf = 'fair'
    else:
        fat_possible = max(0.0, -(fat3 or 0.0))
        water_down = -sum(lb for lb, _ in reasons if lb < 0)
        fat7 = f['balance']['fat_equivalent_last7_lb'] or 0
        if fat3 is not None and fat3 < -0.3 and -fat7 >= 0.5 * abs(move):    # a real deficit, sustained over the week, can account for it
            cat, head = 'real_loss', f"Down {abs(move):.1f} lb, and your deficit backs it up: genuine progress."
            conf = 'good'
        elif water_down >= 0.5 * abs(move):
            cat, head = 'water_down', f"Down {abs(move):.1f} lb, mostly water rather than fat."
            conf = 'fair'
        else:
            cat, head = 'noise_down', f"Down {abs(move):.1f} lb: a normal swing, not a trend yet."
            conf = 'fair'
    if wi['days_since_previous'] and wi['days_since_previous'] > 3:
        conf = 'low'
        texts.append(f"There is a {wi['days_since_previous']}-day gap since the last weigh-in, so the comparison is looser.")
    if wi['hour'] is not None and wi['hour'] >= 17:
        texts.append('This weigh-in was in the evening; weight is typically 1-2 lb heavier later in the day than first thing.')
    if sum(1 for r in before if r['logged']) < 2:
        conf = 'low'
        texts.append('Little food was logged over the last few days, so this verdict is a guess.')
    return {'category': cat, 'direction': direction, 'confidence': conf, 'move_lb': move, 'headline': head, 'reasons': texts[:4]}


def spoken(v, name=None):
    """A short sentence suited to text-to-speech: no symbols, under ~250 characters."""
    lead = (name + ', ' if name else '')
    text = lead + v['headline'].replace(':', ',')
    first = next((r for r in v.get('reasons', []) if r), '')
    if first and len(text) + len(first) < 240:
        text += ' ' + first
    text = text.replace('~', 'about ').replace(' g ', ' grams ').replace(' h ', ' hours ')
    return text[:250]


# ── wording (optional AI) ────────────────────────────────────────────────────────────────────────

_SYSTEM = (
    "You write a short weigh-in verdict for a person who tracks food and exercise. You are given FACTS and a CLASSIFICATION that "
    "was worked out by arithmetic. Explain the classification in plain, friendly UK English in pounds. Rules: use ONLY the numbers "
    "in FACTS, never invent data; never contradict the classification category; name the specific causes (what they ate, trained, "
    "drank or slept) with their numbers; say honestly when you cannot tell; no medical advice, no moralising, no hype. "
    "Reply with JSON only: {\"headline\": string (max 90 chars), \"reasons\": [up to 4 strings, each one sentence], "
    "\"spoken\": string (under 230 chars, no symbols, suitable for a voice assistant)}."
)


def _ai_wording(facts, cls):
    from openai import OpenAI  # noqa: F401  (imported lazily: Ollama and no-key setups must still work)
    from services.ai import _get_client_and_model
    client, model = _get_client_and_model()
    if not client:
        return None
    slim = {k: facts[k] for k in ('date', 'weekday', 'weigh_in', 'days', 'usual', 'balance', 'training_streak_days')}
    prompt = ("FACTS:\n" + json.dumps(slim, default=str) + "\n\nCLASSIFICATION:\n" +
              json.dumps({k: cls[k] for k in ('category', 'direction', 'confidence', 'move_lb', 'headline', 'reasons')}))
    resp = client.chat.completions.create(model=model, temperature=0.3, max_tokens=500,
                                          response_format={'type': 'json_object'},
                                          messages=[{'role': 'system', 'content': _SYSTEM}, {'role': 'user', 'content': prompt}],
                                          timeout=45)
    data = json.loads(resp.choices[0].message.content)
    reasons = [str(x).strip() for x in (data.get('reasons') or []) if str(x).strip()][:4]
    head = str(data.get('headline') or '').strip()
    if not head or not reasons:
        return None
    return {'headline': head[:140], 'reasons': reasons, 'spoken': str(data.get('spoken') or '')[:250]}


# ── storage + entry points ───────────────────────────────────────────────────────────────────────

def ensure_schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS WeighInVerdict (
        riderId INTEGER NOT NULL, logDate TEXT NOT NULL, weightKg REAL NOT NULL, category TEXT, direction INTEGER,
        confidence TEXT, headline TEXT, reasons TEXT, spoken TEXT, source TEXT, createdAt TEXT DEFAULT (datetime('now')),
        PRIMARY KEY (riderId, logDate))''')


def generate(rider_id, day, use_ai=None):
    """Build and store the verdict for the weigh-in on `day`. Returns the stored dict, or None (disabled / no weigh-in)."""
    if not enabled():
        return None
    facts = build_facts(rider_id, day)
    if not facts:
        return None
    cls = classify(facts)
    if cls['category'] == 'first':
        # nothing to say yet and nothing worth sending to an AI provider
        use_ai = False
    wording, source = None, 'rules'
    if (ai_wording_enabled() if use_ai is None else use_ai):
        try:
            wording = _ai_wording(facts, cls)
            source = 'ai' if wording else 'rules'
        except Exception as e:                      # no key, offline, bad JSON: the rule-based text still stands
            log.warning('weigh-in AI wording failed (%s); using rule-based wording', e)
    head = wording['headline'] if wording else cls['headline']
    reasons = wording['reasons'] if wording else cls['reasons']
    say = (wording and wording['spoken']) or spoken({'headline': head, 'reasons': reasons})
    db = get_db()
    ensure_schema(db)
    db.execute('''INSERT OR REPLACE INTO WeighInVerdict (riderId, logDate, weightKg, category, direction, confidence, headline, reasons, spoken, source)
                  VALUES (?,?,?,?,?,?,?,?,?,?)''',
               [rider_id, day, facts['weigh_in']['kg'], cls['category'], cls['direction'], cls['confidence'], head,
                json.dumps(reasons), say, source])
    db.commit()
    return get(rider_id, day)


def get(rider_id, day=None):
    """The stored verdict for `day` (default: latest), as a dict, or None."""
    db = get_db()
    ensure_schema(db)
    if day:
        r = db.execute('SELECT * FROM WeighInVerdict WHERE riderId=? AND logDate=?', [rider_id, day]).fetchone()
    else:
        r = db.execute('SELECT * FROM WeighInVerdict WHERE riderId=? ORDER BY logDate DESC LIMIT 1', [rider_id]).fetchone()
    if not r:
        return None
    wl = db.execute('SELECT weightKg FROM WeightLog WHERE riderId=? AND logDate=?', [rider_id, r['logDate']]).fetchone()
    return {'date': r['logDate'], 'weight_lb': _lb(r['weightKg']), 'category': r['category'], 'direction': r['direction'],
            'confidence': r['confidence'], 'headline': r['headline'], 'reasons': json.loads(r['reasons'] or '[]'),
            'spoken': r['spoken'], 'source': r['source'], 'created': r['createdAt'],
            'stale': bool(wl and abs(wl['weightKg'] - r['weightKg']) > 0.005)}


def on_weigh_in(rider_id, day):
    """Hook for every path that records a weigh-in. Runs in a background thread: AI can take seconds and callers must not wait.
    Only the newest weigh-in gets a verdict (a backfilled old day would be stale news)."""
    import threading
    from flask import current_app
    try:
        latest = query_db('SELECT MAX(logDate) d FROM WeightLog WHERE riderId=?', [rider_id], one=True)
        if latest and latest['d'] and latest['d'] > day:
            return
        app = current_app._get_current_object()
        have = get(rider_id, day)
        if have and not have['stale']:        # same weigh-in already judged: the HA poll runs often, do not re-ask the AI each time
            return
    except RuntimeError:
        return

    def _run():
        try:
            with app.app_context():
                v = generate(rider_id, day)
                if v:
                    from services import mqtt
                    mqtt.push_update_nutrition()
        except Exception as e:
            log.warning('weigh-in verdict failed: %s', e)
    threading.Thread(target=_run, daemon=True, name='weighin-verdict').start()
