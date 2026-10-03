import os
import sqlite3
import re
import shutil
import tempfile
import time
import uuid
from datetime import date, timedelta
from urllib.parse import urlparse

import requests
from flask import Blueprint, Response, abort, jsonify, redirect, request, render_template
from database import get_db, query_db
from services.food_lookup import name_key
from services.food_quality import nutri_score_label, nutrient_tags
from services.weight_trend import (
    MAX_SANE_KG,
    MIN_SANE_KG,
    compute_trend,
    parse_backfill_rows,
    stone_lb_to_kg,
    stone_milestones,
)

OFF_IMAGE_HOSTS = ('openfoodfacts.org', 'openfoodfacts.net')

bp = Blueprint('nutrition', __name__, url_prefix='/nutrition')

WEIGHT_PERIOD_DAYS = {'W': 7, 'M': 30, '3M': 90, '6M': 182, 'Y': 365}

MEAL_KEYS = ['breakfast', 'lunch', 'dinner', 'snacks', 'ride_fuel', 'recovery']


def _default_rider_id():
    row = query_db('SELECT id FROM Rider WHERE isDefault=1 LIMIT 1', one=True)
    return row['id'] if row else None


def _parse_serving_g(s):
    if not s:
        return None
    m = re.search(r'(\d+(?:\.\d+)?)\s*g', s, re.IGNORECASE)
    return float(m.group(1)) if m else None


def _get_goals():
    row = query_db('''
        SELECT nutritionCalGoal, nutritionProteinGoal, nutritionCarbGoal,
               nutritionFatGoal, nutritionWaterGoalMl, nutritionBmrKcal
        FROM Settings WHERE id=1
    ''', one=True)
    if not row:
        return {}
    return {
        'cal':     row['nutritionCalGoal'],
        'protein': row['nutritionProteinGoal'],
        'carbs':   row['nutritionCarbGoal'],
        'fat':     row['nutritionFatGoal'],
        'water':   row['nutritionWaterGoalMl'],
        'bmr':     row['nutritionBmrKcal'],
    }


def _apply_override(product):
    barcode = product.get('barcode')
    if not barcode:
        return product
    ov = query_db('SELECT * FROM FoodOverride WHERE barcode=?', [barcode], one=True)
    if not ov:
        from services import shared_foods
        shared = shared_foods.by_barcode(barcode)      # a friend's corrected version — only when I have not edited it myself
        return shared_foods.overlay(product, shared) if shared else product
    return {
        **product,
        'name':             ov['name'],
        'brand':            ov['brand'] or product.get('brand', ''),
        'kcal_per_100g':    ov['kcal100g'],
        'protein_per_100g': ov['protein100g'],
        'carbs_per_100g':   ov['carbs100g'],
        'fat_per_100g':     ov['fat100g'],
        'fibre_per_100g':   ov['fibre100g'] if ov['fibre100g'] is not None else product.get('fibre_per_100g'),
        'serving_g':        ov['servingG'] or product.get('serving_g'),
        'image_url':        ov['imageUrl'] or product.get('image_url'),
        'nutri_score_grade': ov['nutriScoreGrade'] or product.get('nutri_score_grade'),
        '_overridden':      True,
    }


def _with_quality(product, sugars_per_100g=None):
    """Adds nutri_score_label + nutrient_tags to a product dict, using either
    an explicit sugars value (fresh from OFF) or the one already on it."""
    product['verified'] = bool(product.get('_overridden')) or bool(product.get('_custom'))
    product['nutri_score_label'] = nutri_score_label(product.get('nutri_score_grade'))
    product['tags'] = nutrient_tags(
        kcal_per_100g=product.get('kcal_per_100g'),
        protein_per_100g=product.get('protein_per_100g'),
        fat_per_100g=product.get('fat_per_100g'),
        fibre_per_100g=product.get('fibre_per_100g'),
        sugars_per_100g=sugars_per_100g if sugars_per_100g is not None else product.get('_sugars_per_100g'),
    )
    product.pop('_sugars_per_100g', None)
    return product


def _goal_weight_settings():
    r = query_db('SELECT goalWeightKg FROM Settings WHERE id=1', one=True)
    return {'goalWeightKg': r['goalWeightKg'] if r else None}


def _weight_settings():
    row = query_db(
        'SELECT nutritionWeightUnit, nutritionWeightEmaAlpha, dietStartDate FROM Settings WHERE id=1',
        one=True,
    )
    return {
        'unit':          (row['nutritionWeightUnit'] if row else None) or 'stlb',
        'emaAlpha':      (row['nutritionWeightEmaAlpha'] if row else None) or 0.1,
        'dietStartDate': row['dietStartDate'] if row else None,
        **_goal_weight_settings(),
    }


def _all_weight_entries(rider_id):
    rows = query_db(
        'SELECT logDate, weightKg FROM WeightLog WHERE riderId=? ORDER BY logDate ASC',
        [rider_id],
    )
    entries = []
    for r in rows:
        try:
            entries.append((date.fromisoformat(r['logDate']), r['weightKg']))
        except ValueError:
            continue
    return entries


def _weight_snapshot(rider_id, date_str):
    """Raw + trend weight for one day, for the day view."""
    settings = _weight_settings()
    entries = _all_weight_entries(rider_id)
    if not entries:
        return {'raw': None, 'trend': None, 'unit': settings['unit']}
    d = date.fromisoformat(date_str)
    src = query_db('SELECT source FROM WeightLog WHERE riderId=? AND logDate=?', [rider_id, date_str], one=True)
    fat = query_db("SELECT value FROM BodyMetric WHERE riderId=? AND metric='body_fat' AND logDate<=? ORDER BY logDate DESC LIMIT 1",
                   [rider_id, date_str], one=True)
    trend_map = compute_trend(entries, alpha=settings['emaAlpha'], end_date=d)
    raw = dict(entries).get(d)
    trend = trend_map.get(d)
    return {
        'raw':   round(raw, 2) if raw is not None else None,
        'trend': round(trend, 2) if trend is not None else None,
        'unit':  settings['unit'],
        'source': src['source'] if src else None,
        'bodyFat': round(fat['value'], 1) if fat else None,
    }


def _mqtt_nutrition():
    try:
        from services.mqtt import push_update_nutrition
        push_update_nutrition()
    except Exception:
        pass


# ── Manual steps ─────────────────────────────────────────────────────
# For anyone without a Garmin (or whose phone/watch isn't feeding Headwind): log a day's steps by hand. Stored as BodyMetric
# 'steps_manual'; the app already uses the highest of Garmin / phone / watch / manual for each day.

_STEP_LABELS = (('steps_manual', 'Manual'), ('steps_phone', 'Phone'), ('steps_watch', 'Watch'))


def _london_today():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo('Europe/London')).date()


@bp.route('/steps')
def steps_page():
    return render_template('steps.html')


@bp.route('/api/steps', methods=['GET'])
def steps_list():
    rider_id = _default_rider_id()
    today = _london_today()
    goal = (query_db('SELECT stepGoal FROM Settings WHERE id=1', one=True) or {'stepGoal': None})['stepGoal'] or 8000
    from services.mqtt import _best_steps_for
    days = []
    for i in range(0, 21):
        d = (today - timedelta(days=i)).isoformat()
        rec = {'date': d}
        for metric, label in _STEP_LABELS:
            m = query_db('SELECT value FROM BodyMetric WHERE riderId=? AND logDate=? AND metric=?', [rider_id, d, metric], one=True)
            rec[label.lower()] = int(m['value']) if m and m['value'] is not None else None
        g = query_db('SELECT steps FROM GarminDaily WHERE date=?', [d], one=True)
        rec['garmin'] = int(g['steps']) if g and g['steps'] else None
        rec['best'] = _best_steps_for(rider_id, d, rec['garmin'])
        days.append(rec)
    return jsonify({'goal': goal, 'today': today.isoformat(), 'days': days})


@bp.route('/api/steps', methods=['POST'])
def steps_set():
    d = request.get_json() or {}
    try:
        steps = int(d.get('steps'))
        day = date.fromisoformat(d.get('date') or _london_today().isoformat())
    except (TypeError, ValueError):
        return jsonify({'error': 'steps must be a whole number and date YYYY-MM-DD'}), 400
    today = _london_today()
    if not 0 <= steps <= 100000:
        return jsonify({'error': 'steps must be between 0 and 100,000'}), 400
    if day > today or (today - day).days > 120:
        return jsonify({'error': 'date must be within the last 120 days (not in the future)'}), 400
    rider_id = _default_rider_id()
    # An automatic sync (currently: the phone app's Health Connect) writes to a DIFFERENT metric slot than a value someone
    # typed by hand, so neither can silently overwrite the other -- both still feed the same max() in
    # services.mqtt._best_steps_for, so the displayed total only ever goes up, never regresses because of which fired last.
    metric = 'steps_phone' if d.get('source') == 'health_connect' else 'steps_manual'
    db = get_db()
    db.execute('DELETE FROM BodyMetric WHERE riderId=? AND logDate=? AND metric=?', [rider_id, day.isoformat(), metric])
    db.execute("INSERT INTO BodyMetric (riderId, logDate, metric, value, unit, source, updatedAt) VALUES (?,?,?,?,?,?, datetime('now'))",
               [rider_id, day.isoformat(), metric, steps, 'steps', d.get('source') or 'manual'])
    db.commit()
    _mqtt_nutrition()
    return jsonify({'ok': True})


@bp.route('/api/steps/<day>', methods=['DELETE'])
def steps_delete(day):
    try:
        day = date.fromisoformat(day).isoformat()
    except ValueError:
        return jsonify({'error': 'bad date'}), 400
    db = get_db()
    db.execute("DELETE FROM BodyMetric WHERE riderId=? AND logDate=? AND metric='steps_manual'", [_default_rider_id(), day])
    db.commit()
    _mqtt_nutrition()
    return jsonify({'ok': True})


# ── Page ──────────────────────────────────────────────────────────────

@bp.route('/')
def index():
    return render_template('nutrition.html')


# ── Goals ─────────────────────────────────────────────────────────────

@bp.route('/api/goals', methods=['GET'])
def get_goals():
    return jsonify(_get_goals())


@bp.route('/api/goals', methods=['POST'])
def save_goals():
    d = request.get_json()
    db = get_db()
    db.execute('INSERT OR IGNORE INTO Settings (id) VALUES (1)')
    db.execute('''
        UPDATE Settings SET
            nutritionCalGoal=?, nutritionProteinGoal=?, nutritionCarbGoal=?,
            nutritionFatGoal=?, nutritionWaterGoalMl=?, nutritionBmrKcal=?
        WHERE id=1
    ''', [
        d.get('cal')     or None,
        d.get('protein') or None,
        d.get('carbs')   or None,
        d.get('fat')     or None,
        d.get('water')   or None,
        d.get('bmr')     or None,
    ])
    db.commit()
    _mqtt_nutrition()   # goals are HA sensors too
    return jsonify({'ok': True})


@bp.route('/api/estimate-meal', methods=['POST'])
def estimate_meal_photo():
    """Photo of a meal -> AI estimate (name, calories, macros, breakdown). The photo is kept locally so the logged entry can show it."""
    import io
    from PIL import Image
    from services import food_cache
    from services.food_vision import estimate_meal
    f = request.files.get('photo')
    if not f:
        return jsonify({'error': 'No photo received'}), 400
    raw = f.read()
    try:
        result = estimate_meal(raw, request.form.get('hint'))
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        return jsonify({'error': f'AI request failed: {e}'}), 502
    try:
        img = Image.open(io.BytesIO(raw)).convert('RGB')
        img.thumbnail((640, 640))
        buf = io.BytesIO()
        img.save(buf, 'JPEG', quality=85)
        food_cache.init_path()
        result['image_url'] = food_cache.save_local_image(buf.getvalue(), 'jpg')
    except Exception:
        result['image_url'] = None
    return jsonify(result)


# ── Smart (floating) goals ────────────────────────────────────────────

@bp.route('/api/goal-plan', methods=['GET'])
def goal_plan():
    from services import goal_model
    return jsonify({'plan': goal_model.plan(), 'profile': goal_model.profile(), 'current': _get_goals()})


@bp.route('/api/goal-profile', methods=['POST'])
def save_goal_profile():
    from services import goal_model
    d = request.get_json() or {}
    sex = d.get('sex') if d.get('sex') in ('male', 'female') else None
    try:
        age = int(d['age']) if d.get('age') else None
        height_cm = float(d['height_cm']) if d.get('height_cm') not in (None, '') else None
        rides = float(d['ride_kcal_week']) if d.get('ride_kcal_week') not in (None, '') else None
        loss = float(d['loss_lb_week']) if d.get('loss_lb_week') not in (None, '') else None
        steps = int(d['step_goal']) if d.get('step_goal') else None
        ride_goal = int(d['ride_goal_week']) if d.get('ride_goal_week') not in (None, '') else None
    except (TypeError, ValueError):
        return jsonify({'error': 'invalid number'}), 400
    if loss is not None and not (0 <= loss <= 2.5):
        return jsonify({'error': 'loss rate must be between 0 and 2.5 lb/week'}), 400
    if ride_goal is not None and not (1 <= ride_goal <= 14):
        return jsonify({'error': 'rides per week must be between 1 and 14'}), 400
    if height_cm is not None and not (100 <= height_cm <= 250):
        return jsonify({'error': 'height must be between 100 and 250 cm'}), 400
    db = get_db()
    db.execute('INSERT OR IGNORE INTO Settings (id) VALUES (1)')
    db.execute("UPDATE Settings SET sex=COALESCE(?, sex), birthYear=COALESCE(?, birthYear), heightCm=COALESCE(?, heightCm), rideKcalWeek=COALESCE(?, rideKcalWeek), "
               "lossLbPerWeek=COALESCE(?, lossLbPerWeek), stepGoal=COALESCE(?, stepGoal), rideGoalWeek=COALESCE(?, rideGoalWeek), goalAuto=? WHERE id=1",
               [sex, (goal_model._today().year - age) if age else None, height_cm, rides, loss, steps, ride_goal, 1 if d.get('auto') else 0])
    db.commit()
    _mqtt_nutrition()
    return jsonify({'plan': goal_model.plan(), 'profile': goal_model.profile()})


@bp.route('/api/goal-plan/apply', methods=['POST'])
def apply_goal_plan():
    from services import goal_model
    pl = goal_model.plan()
    if 'error' in pl:
        return jsonify({'error': pl['error']}), 400
    goal_model.apply(pl)
    _mqtt_nutrition()
    return jsonify({'ok': True, 'plan': pl})


# ── OFFs search + lookup ──────────────────────────────────────────────

OFF_FIELDS = (
    'code,product_name,product_name_en,brands,nutriments,serving_size,'
    'nutriscore_grade,image_front_small_url,image_front_url,image_url'
)


def _off_product_to_dict(p):
    name = (p.get('product_name_en') or p.get('product_name') or '').strip()
    if not name:
        return None
    n = p.get('nutriments', {})
    kcal = n.get('energy-kcal_100g')
    if kcal is None:  # 0 is a real, legitimate value (water, black coffee) — only missing data means "not found"
        return None
    # search.openfoodfacts.org returns `brands` as a list; the v2 product API
    # (used by lookup()) returns it as a comma-separated string — handle both.
    brands = p.get('brands') or ''
    brand = (brands[0] if brands else '').strip() if isinstance(brands, list) else brands.split(',')[0].strip()
    return {
        'barcode':           p.get('code', ''),
        'name':              name,
        'brand':             brand,
        'kcal_per_100g':     kcal,
        'protein_per_100g':  n.get('proteins_100g'),
        'carbs_per_100g':    n.get('carbohydrates_100g'),
        'fat_per_100g':      n.get('fat_100g'),
        'fibre_per_100g':    n.get('fiber_100g'),
        'serving_g':         _parse_serving_g(p.get('serving_size', '')),
        'image_url':         p.get('image_front_small_url') or p.get('image_front_url') or p.get('image_url'),
        'nutri_score_grade': p.get('nutriscore_grade'),
        '_sugars_per_100g':  n.get('sugars_100g'),
    }


@bp.route('/api/search')
def search_food():
    from services import food_cache
    q = request.args.get('q', '').strip()
    if not q:
        return jsonify([])
    food_cache.init_path()
    off_ok, products = False, []
    try:
        # world.openfoodfacts.org/cgi/search.pl (legacy) is prone to 503s under
        # light load — search.openfoodfacts.org is OFF's current search index.
        resp = requests.get(
            'https://search.openfoodfacts.org/search',
            params={
                'q':                 q,
                'page_size':         12,
                'fields':            OFF_FIELDS,
                'countries_tags_en': 'United Kingdom',
            },
            timeout=4,
            headers={'User-Agent': 'Headwind-Nutrition/1.0'},
        )
        if resp.ok:
            off_ok = True
            for hit in resp.json().get('hits', []):
                p = _off_product_to_dict(hit)
                if p:
                    products.append(p)
    except Exception:
        pass

    if products:
        food_cache.store(products)  # every product we see is kept for when the API is down
    else:
        # API down, or nothing found: fall back to the local copy (flagged so the UI can say so)
        products = food_cache.search(q)
        for p in products:
            p['offline'] = not off_ok

    results = []
    saved_images = _saved_images()
    for p in products:
        sugars = p.pop('_sugars_per_100g', None)
        if not p.get('image_url'):
            p['image_url'] = saved_images.get(name_key(p.get('name') or ''))
        results.append(_with_quality(_apply_override(p), sugars))
    from services import shared_foods
    have = {(r.get('name') or '').lower() for r in results}
    mine = [_with_quality(sf) for sf in shared_foods.search(q) if sf['name'].lower() not in have]
    return jsonify(mine + results)


@bp.route('/api/lookup/<barcode>')
def lookup(barcode):
    ov = query_db('SELECT * FROM FoodOverride WHERE barcode=?', [barcode], one=True)
    if ov:
        return jsonify(_with_quality({
            'barcode':           barcode,
            'name':              ov['name'],
            'brand':             ov['brand'] or '',
            'kcal_per_100g':     ov['kcal100g'],
            'protein_per_100g':  ov['protein100g'],
            'carbs_per_100g':    ov['carbs100g'],
            'fat_per_100g':      ov['fat100g'],
            'fibre_per_100g':    ov['fibre100g'],
            'serving_g':         ov['servingG'],
            'image_url':         ov['imageUrl'],
            'nutri_score_grade': ov['nutriScoreGrade'],
            '_overridden':       True,
        }))
    from services import shared_foods
    shared = shared_foods.by_barcode(barcode)
    if shared:
        return jsonify(_with_quality(shared))
    from services import food_cache
    food_cache.init_path()

    def from_cache(product):
        sugars = product.pop('_sugars_per_100g', None)
        return jsonify(_with_quality(_apply_override(product), sugars))

    fresh = food_cache.get_by_barcode(barcode, max_age_days=food_cache.FRESH_DAYS)
    if fresh:
        return from_cache(fresh)
    url = f'https://world.openfoodfacts.org/api/v2/product/{barcode}.json'
    try:
        resp = requests.get(
            url, params={'fields': OFF_FIELDS}, timeout=5,
            headers={'User-Agent': 'Headwind-Nutrition/1.0'},
        )
        data = resp.json() if resp.ok else {}
        product = _off_product_to_dict(data['product']) if data.get('status') == 1 else None
        if product:
            product['barcode'] = barcode
            food_cache.store([product])
            sugars = product.pop('_sugars_per_100g', None)
            return jsonify(_with_quality(_apply_override(product), sugars))
    except Exception:
        pass
    # API down or product missing there: a stale local copy beats nothing
    stale = food_cache.get_by_barcode(barcode)
    if stale:
        stale['offline'] = True
        return from_cache(stale)
    return jsonify({'error': 'not_found'}), 404


@bp.route('/api/stock-image')
def stock_image():
    """Best-effort generic product photo for a food name with no barcode
    (manual entries, My Foods) — reuses the OFF search index as a free
    stock-photo source. Silent-fails like search_food/lookup."""
    name = request.args.get('name', '').strip()
    if not name:
        return jsonify({'image_url': None})
    try:
        resp = requests.get(
            'https://search.openfoodfacts.org/search',
            params={
                'q':          name,
                'page_size':  5,
                'fields':     'product_name,product_name_en,image_front_small_url,image_front_url,image_url',
            },
            timeout=6,
            headers={'User-Agent': 'Headwind-Nutrition/1.0'},
        )
        if resp.ok:
            for p in resp.json().get('hits', []):
                img = p.get('image_front_small_url') or p.get('image_front_url') or p.get('image_url')
                if img:
                    return jsonify({'image_url': img})
    except Exception:
        pass
    return jsonify({'image_url': None})


@bp.route('/api/image')
def image_proxy():
    """Product photo. Served from the local copy when we have one (works offline); otherwise fetched and kept."""
    from services import food_cache
    url = request.args.get('url', '')
    food_cache.init_path()
    if food_cache.is_local(url):
        hit = food_cache.local_image(url)
        if not hit:
            abort(404)
        return Response(hit[0], mimetype=hit[1], headers={'Cache-Control': 'public, max-age=604800'})
    if not food_cache.image_allowed(url):
        abort(400)
    hit = food_cache.fetch_image(url)
    if not hit:
        abort(502)
    return Response(hit[0], mimetype=hit[1], headers={'Cache-Control': 'public, max-age=604800'})


@bp.route('/api/food-nutrition', methods=['POST'])
def save_food_nutrition():
    """Store corrected per-100g values (incl. fibre) for a food so every future lookup of it is complete.
    Foods with a barcode become an override on that barcode (and refresh the local cache); others become a My Food."""
    from services import food_cache
    d = request.get_json() or {}
    name = (d.get('name') or '').strip()
    barcode = (d.get('barcode') or '').strip()
    if not name:
        return jsonify({'error': 'name required'}), 400
    vals = [d.get('kcal_per_100g'), d.get('protein_per_100g'), d.get('carbs_per_100g'),
            d.get('fat_per_100g'), d.get('fibre_per_100g')]
    if d.get('kcal_per_100g') is None:
        return jsonify({'error': 'calories required'}), 400
    db = get_db()
    if barcode:
        db.execute("""
            INSERT INTO FoodOverride (barcode, name, brand, kcal100g, protein100g, carbs100g, fat100g, fibre100g,
                                      servingG, imageUrl, nutriScoreGrade, updatedAt)
            VALUES (?,?,?,?,?,?,?,?,?,?,?, datetime('now'))
            ON CONFLICT(barcode) DO UPDATE SET
                name=excluded.name, brand=excluded.brand, kcal100g=excluded.kcal100g, protein100g=excluded.protein100g,
                carbs100g=excluded.carbs100g, fat100g=excluded.fat100g, fibre100g=excluded.fibre100g,
                servingG=COALESCE(excluded.servingG, servingG), imageUrl=COALESCE(excluded.imageUrl, imageUrl),
                nutriScoreGrade=COALESCE(excluded.nutriScoreGrade, nutriScoreGrade), updatedAt=datetime('now')
        """, [barcode, name, d.get('brand'), *vals, d.get('serving_g'), d.get('image_url'), d.get('nutri_score_grade')])
        db.execute("""UPDATE FoodCache SET kcal100g=?, protein100g=?, carbs100g=?, fat100g=?, fibre100g=?
                      WHERE key=?""", [*vals, barcode])
    else:
        rider_id = _default_rider_id()
        existing = query_db('SELECT id FROM CustomFood WHERE riderId=? AND lower(name)=lower(?)', [rider_id, name], one=True)
        if existing:
            db.execute("""UPDATE CustomFood SET brand=COALESCE(?, brand), calories=?, protein=?, carbs=?, fat=?, fibre100g=?,
                          servingG=COALESCE(?, servingG), imageUrl=COALESCE(?, imageUrl) WHERE id=?""",
                       [d.get('brand'), *vals, d.get('serving_g'), d.get('image_url'), existing['id']])
        else:
            db.execute("""INSERT INTO CustomFood (riderId, name, brand, calories, protein, carbs, fat, fibre100g, servingG, imageUrl)
                          VALUES (?,?,?,?,?,?,?,?,?,?)""",
                       [rider_id, name, d.get('brand'), *vals, d.get('serving_g'), d.get('image_url')])
    db.commit()
    return jsonify({'ok': True, 'saved_as': 'override' if barcode else 'my_food'})


# ── Photo picker ──────────────────────────────────────────────────────

def _image_queries(name, brand):
    """Search terms, most specific first: full name, brand + name, then the distinctive words."""
    import re as _re
    no_sizes = _re.sub(r'\d+(?:\.\d+)?\s*(?:x\s*\d+\s*)?(?:kg|g|ml|l)\b', ' ', name, flags=_re.I)
    base = ' '.join(_re.sub(r'\([^)]*\)', ' ', no_sizes).split())
    words = _re.findall(r"[A-Za-z']+", base)
    # The brand/supermarket (the coloured line above the name) is the most identifying term, so it leads.
    branded = f'{brand} {base}' if brand and brand.lower() not in base.lower() else base
    qs = [branded, base]
    if len(words) > 2:
        first = ' '.join(words[:2])
        qs.append(first if brand.lower() in first.lower() else f'{brand} {first}'.strip())  # brand + the first couple of words
        qs.append(' '.join(words[-2:]))
    seen, out = set(), []
    for q in qs:
        if q and q.lower() not in seen:
            seen.add(q.lower())
            out.append(q)
    return out[:4]


def _off_images(q):
    try:
        resp = requests.get('https://search.openfoodfacts.org/search', timeout=5,
                            params={'q': q, 'page_size': 8, 'fields': OFF_FIELDS},
                            headers={'User-Agent': 'Headwind-Nutrition/1.0'})
        hits = resp.json().get('hits', []) if resp.ok else []
    except Exception:
        return []
    out = []
    for h in hits:
        p = _off_product_to_dict(h)
        url = (p or {}).get('image_url') or h.get('image_front_url') or h.get('image_url')
        if url:
            title = f"{p.get('brand', '')} {p.get('name', '')}".strip() if p else ''
            out.append({'url': url, 'title': title, 'source': 'Open Food Facts'})
    return out


def _wiki_images(q):
    try:
        resp = requests.get('https://commons.wikimedia.org/w/api.php', timeout=6,
                            headers={'User-Agent': 'Headwind-Nutrition/1.0'},
                            params={'action': 'query', 'format': 'json', 'generator': 'search', 'gsrnamespace': 6,
                                    'gsrsearch': f'{q} filetype:bitmap', 'gsrlimit': 8, 'prop': 'imageinfo',
                                    'iiprop': 'url|mime', 'iiurlwidth': 400})
        pages = (resp.json().get('query') or {}).get('pages', {}) if resp.ok else {}
    except Exception:
        return []
    out = []
    for pg in pages.values():
        info = (pg.get('imageinfo') or [{}])[0]
        if info.get('mime') in ('image/jpeg', 'image/png') and info.get('thumburl'):
            out.append({'url': info['thumburl'], 'title': pg.get('title', '').replace('File:', ''), 'source': 'Wikimedia'})
    return out


@bp.route('/api/image-search')
def image_search():
    """Candidate photos for a food: saved/packaged-product photos first, then generic ones."""
    from concurrent.futures import ThreadPoolExecutor
    from services import food_cache
    name = request.args.get('name', '').strip()
    brand = request.args.get('brand', '').strip()
    custom = request.args.get('q', '').strip()
    if not name and not custom:
        return jsonify([])
    food_cache.init_path()
    queries = [custom] if custom else _image_queries(name, brand)

    candidates = []
    for q in queries:  # what we already have locally works offline
        for p in food_cache.search(q, limit=6):
            if p.get('image_url'):
                candidates.append({'url': p['image_url'], 'title': f"{p.get('brand', '')} {p['name']}".strip(), 'source': 'Saved'})
    with ThreadPoolExecutor(max_workers=6) as pool:
        jobs = [pool.submit(_off_images, q) for q in queries] + [pool.submit(_wiki_images, q) for q in queries[:2]]
        for j in jobs:
            try:
                candidates.extend(j.result())
            except Exception:
                pass
    seen, out = set(), []
    for c in candidates:
        if c['url'] not in seen and food_cache.image_allowed(c['url']):
            seen.add(c['url'])
            out.append(c)
    return jsonify(out[:30])


def _apply_food_image(name, brand, barcode, url):
    """Make `url` the photo for this food everywhere it is stored, and remember it for similar names."""
    db = get_db()
    db.execute('UPDATE FoodLog SET imageUrl=? WHERE lower(foodName)=lower(?) OR (barcode IS NOT NULL AND barcode=?)', [url, name, barcode or ''])
    db.execute('UPDATE CustomFood SET imageUrl=? WHERE lower(name)=lower(?)', [url, name])
    db.execute('UPDATE FoodFavourite SET imageUrl=? WHERE lower(foodName)=lower(?) OR (barcode IS NOT NULL AND barcode=?)', [url, name, barcode or ''])
    if barcode:
        db.execute('UPDATE FoodOverride SET imageUrl=? WHERE barcode=?', [url, barcode])
        db.execute('UPDATE FoodCache SET imageUrl=? WHERE key=?', [url, barcode])
    db.execute('UPDATE FoodCache SET imageUrl=? WHERE lower(name)=lower(?)', [url, name])
    db.execute("INSERT INTO FoodImage (nameKey, name, imageUrl) VALUES (?,?,?) "
               "ON CONFLICT(nameKey) DO UPDATE SET name=excluded.name, imageUrl=excluded.imageUrl, updatedAt=datetime('now')",
               [name_key(name), name, url])
    db.commit()


@bp.route('/api/food-image', methods=['POST'])
def set_food_image():
    from services import food_cache
    d = request.get_json() or {}
    name, url = (d.get('name') or '').strip(), (d.get('url') or '').strip()
    if not name or not url:
        return jsonify({'error': 'name and url required'}), 400
    food_cache.init_path()
    if not food_cache.is_local(url):
        if not food_cache.image_allowed(url) or not food_cache.fetch_image(url):  # download + keep a local copy now
            return jsonify({'error': 'Could not fetch that photo'}), 502
    _apply_food_image(name, d.get('brand') or '', d.get('barcode') or '', url)
    return jsonify({'ok': True, 'image_url': url})


@bp.route('/api/food-image/upload', methods=['POST'])
def upload_food_image():
    import io
    from PIL import Image
    from services import food_cache
    f = request.files.get('photo')
    name = (request.form.get('name') or '').strip()
    if not f or not name:
        return jsonify({'error': 'photo and name required'}), 400
    try:
        img = Image.open(f.stream).convert('RGB')
        img.thumbnail((640, 640))
        buf = io.BytesIO()
        img.save(buf, 'JPEG', quality=85)
    except Exception:
        return jsonify({'error': 'That file is not a readable image'}), 400
    food_cache.init_path()
    url = food_cache.save_local_image(buf.getvalue(), 'jpg')
    _apply_food_image(name, request.form.get('brand') or '', request.form.get('barcode') or '', url)
    return jsonify({'ok': True, 'image_url': url})


# ── Log entries ───────────────────────────────────────────────────────

def _saved_fibre(barcode, name, grams, calories):
    """Fibre we already know for this food, so a value the user corrected/added sticks however it is added
    again (search, Recent, Go-Tos…). Order: their per-barcode correction, My Foods, an earlier log of it, the cached API value."""
    barcode = (barcode or '').strip()
    if barcode:
        ov = query_db('SELECT fibre100g FROM FoodOverride WHERE barcode=?', [barcode], one=True)
        if ov and ov['fibre100g'] is not None and grams:
            return round(ov['fibre100g'] * grams / 100, 2)
    cf = query_db('SELECT fibre100g FROM CustomFood WHERE lower(name)=lower(?) AND fibre100g IS NOT NULL LIMIT 1', [name or ''], one=True)
    if cf and grams:
        return round(cf['fibre100g'] * grams / 100, 2)
    prev = query_db('''
        SELECT fibre, calories FROM FoodLog
        WHERE fibre IS NOT NULL AND calories > 0 AND ((? != '' AND barcode=?) OR lower(foodName)=lower(?))
        ORDER BY id DESC LIMIT 1
    ''', [barcode, barcode, name or ''], one=True)
    if prev and calories:
        return round(prev['fibre'] * calories / prev['calories'], 2)
    if barcode and grams:
        fc = query_db('SELECT fibre100g FROM FoodCache WHERE key=? AND fibre100g IS NOT NULL', [barcode], one=True)
        if fc:
            return round(fc['fibre100g'] * grams / 100, 2)
    return None


def _valid_date(v):
    """True for a real YYYY-MM-DD calendar date (not just a non-empty string)."""
    try:
        return bool(v) and date.fromisoformat(str(v)) is not None and len(str(v)) == 10
    except ValueError:
        return False


def _bad_numbers(d, fields, hi=100_000):
    """Name of the first of `fields` that is present but not a finite number in [0, hi], else None."""
    import math
    for f in fields:
        v = d.get(f)
        if v is None or v == '':
            continue
        try:
            x = float(v)
        except (TypeError, ValueError):
            return f
        if not math.isfinite(x) or x < 0 or x > hi:
            return f
    return None



def _claim_client_op(kind, rider_id, cid):
    """Idempotency for queued offline writes. Takes the write lock *before* checking, so two concurrent retries of
    the same op can't both pass the check; keyed by kind+rider so reused client ids can't suppress a different
    operation. Returns (stored key, previous ClientOp row or None); a hit releases the lock."""
    db = get_db()
    try:
        db.execute('BEGIN IMMEDIATE')
    except sqlite3.OperationalError:
        pass                                   # a transaction is already open on this connection
    key = f'{kind}:{rider_id}:{cid}'
    prev = db.execute('SELECT entryId FROM ClientOp WHERE clientId=? OR (clientId=? AND kind=?)',
                      [key, cid, kind]).fetchone()   # second form: rows written before keys were scoped
    if prev:
        db.rollback()
    return key, prev



@bp.route('/api/log', methods=['POST'])
def log_food():
    d = request.get_json(silent=True) or {}
    if not _valid_date(d.get('date')) or not str(d.get('name') or '').strip():
        return jsonify({'error': 'a valid date (YYYY-MM-DD) and food name are required'}), 400
    bad = _bad_numbers(d, ('calories', 'protein', 'carbs', 'fat', 'fibre', 'serving_g', 'quantity'))
    if bad:
        return jsonify({'error': f'{bad} must be a number between 0 and 100000'}), 400
    rider_id = _default_rider_id()
    cid = d.get('client_id') if not d.get('id') else None
    op_key = None
    if cid:                                    # a queued offline log being retried: apply once
        op_key, prev = _claim_client_op('food', rider_id, cid)
        if prev:
            return jsonify({'ok': True, 'id': prev['entryId'], 'duplicate': True})
    if d.get('fibre') is None:
        d['fibre'] = _saved_fibre(d.get('barcode'), d.get('name'), d.get('serving_g'), d.get('calories'))
    db = get_db()
    values = [
        d['date'], d.get('barcode'), d['name'],
        d.get('calories'), d.get('protein'), d.get('carbs'), d.get('fat'),
        d.get('serving_g'), d.get('quantity', 1),
        d.get('meal_type', 'uncategorised'),
        d.get('source', 'manual'),
        d.get('brand'), d.get('image_url'), d.get('nutri_score_grade'), d.get('fibre'),
    ]
    if d.get('id'):
        db.execute('''
            UPDATE FoodLog SET
                logDate=?, barcode=?, foodName=?, calories=?, protein=?, carbs=?, fat=?,
                servingG=?, quantity=?, mealType=?, source=?, brand=?, imageUrl=?, nutriScoreGrade=?, fibre=?
            WHERE id=?
        ''', values + [d['id']])
        entry_id = d['id']
    else:
        db.execute('''
            INSERT INTO FoodLog
                (logDate, barcode, foodName, calories, protein, carbs, fat,
                 servingG, quantity, mealType, source, brand, imageUrl, nutriScoreGrade, fibre, riderId)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', values + [rider_id])
        entry_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]
    if cid:
        db.execute("INSERT OR IGNORE INTO ClientOp (clientId, kind, entryId) VALUES (?, 'food', ?)", [op_key, entry_id])
    db.commit()
    if d.get('barcode') and not d.get('id'):
        from services import food_cache
        food_cache.init_path()
        food_cache.bump_use(d['barcode'])
    _mqtt_nutrition()
    return jsonify({'ok': True, 'id': entry_id})


@bp.route('/api/log-batch', methods=['POST'])
def log_food_batch():
    """Insert several entries in one go (screenshot import) and push to HA once."""
    d = request.get_json() or {}
    items = d.get('items') or []
    if not items:
        return jsonify({'error': 'Nothing to log'}), 400
    rider_id = _default_rider_id()
    db = get_db()
    for it in items:
        db.execute('''
            INSERT INTO FoodLog
                (logDate, foodName, calories, protein, carbs, fat, fibre, quantity, mealType, source, riderId)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
        ''', [d['date'], it['name'], it.get('calories'), it.get('protein'), it.get('carbs'),
              it.get('fat'), it.get('fibre'), it.get('meal_type') or 'uncategorised',
              'screenshot_est' if it.get('est') else 'screenshot', rider_id])
    db.commit()
    _mqtt_nutrition()
    return jsonify({'ok': True, 'count': len(items)})


# ── Recipe book (recipes imported from the source app) ────────────────

@bp.route('/api/recipes', methods=['GET'])
def list_recipes():
    rows = query_db('SELECT id, name, calories, protein, carbs, fat FROM RecipeBook WHERE riderId=? ORDER BY name',
                    [_default_rider_id()])
    return jsonify([dict(r) for r in rows])


@bp.route('/api/recipes', methods=['POST'])
def save_recipes():
    from services.food_lookup import name_key
    d = request.get_json() or {}
    rows = [r for r in (d.get('recipes') or []) if r.get('name') and r.get('calories')]
    if not rows:
        return jsonify({'error': 'Nothing to save'}), 400
    rider_id = _default_rider_id()
    db = get_db()
    for rec in rows:
        db.execute('''
            INSERT INTO RecipeBook (riderId, name, nameKey, calories, protein, carbs, fat)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(riderId, nameKey) DO UPDATE SET
                name=excluded.name, calories=excluded.calories, protein=excluded.protein,
                carbs=excluded.carbs, fat=excluded.fat, updatedAt=datetime('now')
        ''', [rider_id, rec['name'].strip(), name_key(rec['name']), rec['calories'],
              rec.get('protein'), rec.get('carbs'), rec.get('fat')])
    db.commit()
    return jsonify({'ok': True, 'count': len(rows)})


@bp.route('/api/recipes/<int:recipe_id>', methods=['DELETE'])
def delete_recipe(recipe_id):
    db = get_db()
    db.execute('DELETE FROM RecipeBook WHERE id=? AND riderId=?', [recipe_id, _default_rider_id()])
    db.commit()
    return jsonify({'ok': True})


# ── Screenshot import ─────────────────────────────────────────────────

SHARE_DIR = os.path.join(tempfile.gettempdir(), 'headwind_shares')
MAX_SCREENSHOTS = 6


def _purge_old_shares():
    if not os.path.isdir(SHARE_DIR):
        return
    for name in os.listdir(SHARE_DIR):
        path = os.path.join(SHARE_DIR, name)
        if time.time() - os.path.getmtime(path) > 3600:
            shutil.rmtree(path, ignore_errors=True)


@bp.route('/share', methods=['POST'])
def share_target():
    """Android share-sheet target (see nutrition-manifest.json): stash images, open the import modal."""
    _purge_old_shares()
    files = sorted(request.files.getlist('screenshots')[:MAX_SCREENSHOTS], key=lambda f: f.filename or '')
    if not files:
        return redirect('/nutrition/')
    token = uuid.uuid4().hex
    folder = os.path.join(SHARE_DIR, token)
    os.makedirs(folder, exist_ok=True)
    for i, f in enumerate(files):
        f.save(os.path.join(folder, f'{i}.img'))
    return redirect(f'/nutrition/?import={token}')


@bp.route('/api/import-screenshot', methods=['POST'])
def import_screenshot():
    """Streams newline-delimited JSON: {type:'progress',stage,done,total} … then {type:'result',…} or {type:'error'}."""
    import json
    import queue
    import threading
    from flask import current_app, stream_with_context
    from services.food_vision import extract_foods, fill_from_day_totals
    from services.food_lookup import enrich_items

    # Screenshot filenames are timestamped, so name order == the order they were taken == scroll order
    uploads = sorted(request.files.getlist('screenshots')[:MAX_SCREENSHOTS], key=lambda f: f.filename or '')
    images = [f.read() for f in uploads]
    token = request.form.get('share_token', '')
    if token:
        if not re.fullmatch(r'[0-9a-f]{32}', token):
            return jsonify({'error': 'Bad share token'}), 400
        folder = os.path.join(SHARE_DIR, token)
        if os.path.isdir(folder):
            for name in sorted(os.listdir(folder)):
                with open(os.path.join(folder, name), 'rb') as fh:
                    images.append(fh.read())
    if not images:
        return jsonify({'error': 'No screenshots received'}), 400

    app = current_app._get_current_object()
    events = queue.Queue()

    def emit(stage, done, total):
        events.put({'type': 'progress', 'stage': stage, 'done': done, 'total': total})

    recipe_mode = request.form.get('mode') == 'recipes'

    def worker():
        with app.app_context():
            try:
                emit('read', 0, len(images))
                if recipe_mode:
                    from services.food_vision import extract_recipes
                    from services.food_lookup import name_key
                    result = extract_recipes(images, progress=emit)
                    have = {x['nameKey'] for x in query_db(
                        'SELECT nameKey FROM RecipeBook WHERE riderId=?', [_default_rider_id()])}
                    for rec in result['recipes']:
                        rec['exists'] = name_key(rec['name']) in have
                    events.put({'type': 'result', **result})
                    events.put(None)
                    return
                result = extract_foods(images, progress=emit)
                emit('lookup', 0, len(result['items']))
                enrich_items(result['items'], progress=emit)
                emit('estimate', 0, 1)
                fill_from_day_totals(result)
                if token:
                    shutil.rmtree(os.path.join(SHARE_DIR, token), ignore_errors=True)
                events.put({'type': 'result', **result})
            except ValueError as e:
                events.put({'type': 'error', 'error': str(e)})
            except Exception as e:
                events.put({'type': 'error', 'error': f'AI request failed: {e}'})
            events.put(None)

    threading.Thread(target=worker, daemon=True).start()

    def stream():
        while True:
            ev = events.get()
            if ev is None:
                return
            yield json.dumps(ev) + '\n'

    return Response(stream_with_context(stream()), mimetype='application/x-ndjson',
                    headers={'X-Accel-Buffering': 'no', 'Cache-Control': 'no-cache'})


@bp.route('/api/log/<int:entry_id>', methods=['DELETE'])
def delete_log(entry_id):
    db = get_db()
    db.execute('DELETE FROM FoodLog WHERE id=?', [entry_id])
    db.commit()
    _mqtt_nutrition()
    return jsonify({'ok': True})


# ── Water ─────────────────────────────────────────────────────────────

@bp.route('/api/water', methods=['POST'])
def add_water():
    d = request.get_json(silent=True) or {}
    if not _valid_date(d.get('date')) or _bad_numbers(d, ('ml',), hi=10_000) or not d.get('ml'):
        return jsonify({'error': 'a valid date and ml (1-10000) are required'}), 400
    rider_id = _default_rider_id()
    cid = d.get('client_id')
    op_key = None
    if cid:
        op_key, prev = _claim_client_op('water', rider_id, cid)
        if prev:
            return jsonify({'ok': True, 'id': prev['entryId'], 'duplicate': True})
    db = get_db()
    db.execute(
        'INSERT INTO HydrationLog (riderId, logDate, ml) VALUES (?, ?, ?)',
        [rider_id, d['date'], int(d['ml'])],
    )
    entry_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]
    if cid:
        db.execute("INSERT OR IGNORE INTO ClientOp (clientId, kind, entryId) VALUES (?, 'water', ?)", [op_key, entry_id])
    db.commit()
    _mqtt_nutrition()
    return jsonify({'ok': True, 'id': entry_id})


@bp.route('/api/water/<int:entry_id>', methods=['DELETE'])
def delete_water(entry_id):
    db = get_db()
    db.execute('DELETE FROM HydrationLog WHERE id=?', [entry_id])
    db.commit()
    _mqtt_nutrition()
    return jsonify({'ok': True})


# ── Day summary ───────────────────────────────────────────────────────

@bp.route('/api/day/<date_str>')
def day_log(date_str):
    rider_id = _default_rider_id()
    entries = query_db('''
        SELECT id, foodName, calories, protein, carbs, fat, fibre, servingG, quantity,
               barcode, mealType, source, brand, imageUrl, nutriScoreGrade
        FROM FoodLog WHERE riderId=? AND logDate=? ORDER BY createdAt ASC
    ''', [rider_id, date_str])
    water_row = query_db(
        'SELECT COALESCE(SUM(ml),0) as total, MAX(id) as lastId FROM HydrationLog WHERE riderId=? AND logDate=?',
        [rider_id, date_str], one=True,
    )
    water_ml   = int(water_row['total'])  if water_row else 0
    last_water = water_row['lastId']      if water_row else None
    garmin_row = query_db(
        'SELECT totalCalories, activeCalories FROM GarminDaily WHERE date=?',
        [date_str], one=True,
    )
    garmin_total  = garmin_row['totalCalories']  if garmin_row else None
    garmin_active = garmin_row['activeCalories'] if garmin_row else None
    ride_row = query_db('''
        SELECT COALESCE(SUM(calories), 0) as total FROM Activity
        WHERE riderId=? AND date(startDateLocal)=? AND calories IS NOT NULL
    ''', [rider_id, date_str], one=True)
    ride_cal = int(ride_row['total']) if ride_row else 0
    from services import workouts as _wk
    ride_cal += _wk.calories_on(rider_id, date_str)               # recorded walks / runs

    goals = _get_goals()
    bmr   = goals.get('bmr')

    # Burn calculation priority:
    #   1. Manual BMR + today's ride calories (always current, no sync lag)
    #   2. Garmin daily total (comprehensive but up to 2h stale)
    #   3. Ride calories only
    from services import goal_model
    est = goal_model.day_burn(rider_id, date_str)
    if est:
        burned, burn_source = est
    elif bmr:
        burned      = int(bmr) + ride_cal
        burn_source = 'BMR + rides' if ride_cal else 'BMR (resting)'
    elif garmin_total:
        burned      = garmin_total
        burn_source = 'Garmin total'
    elif ride_cal:
        burned      = ride_cal
        burn_source = 'rides'
    else:
        burned      = None
        burn_source = None

    return jsonify({
        'entries':       _annotate_food_rows(entries),
        'water_ml':      water_ml,
        'last_water_id': last_water,
        'ride_calories': ride_cal,
        'garmin_total':  garmin_total,
        'garmin_active': garmin_active,
        'burned':        burned,
        'burn_source':   burn_source,
        'goals':         goals,
        'weight':        _weight_snapshot(rider_id, date_str),
    })


# ── Recent foods ──────────────────────────────────────────────────────

def _per_100g(calories, protein, carbs, fat, serving_g, quantity):
    total_g = (serving_g or 0) * (quantity or 1)
    if not total_g:
        return {}
    scale = 100 / total_g
    return {
        'kcal_per_100g':    calories * scale if calories is not None else None,
        'protein_per_100g': protein  * scale if protein  is not None else None,
        'carbs_per_100g':   carbs    * scale if carbs    is not None else None,
        'fat_per_100g':     fat      * scale if fat      is not None else None,
    }


def _saved_images():
    """{nameKey: imageUrl} for every photo the user has chosen — applies to similar names in future."""
    return {x['nameKey']: x['imageUrl'] for x in query_db('SELECT nameKey, imageUrl FROM FoodImage')}


def _annotate_food_rows(rows):
    """Adds verified/nutri_score_label/tags to raw FoodLog rows — shared by
    the day view and /api/recent so both render the same badges/thumbnails."""
    overridden_barcodes = {
        r['barcode'] for r in query_db('SELECT barcode FROM FoodOverride')
    }
    results = []
    saved_images = _saved_images()
    for r in rows:
        item = dict(r)
        item.pop('createdAt', None)
        if not item.get('imageUrl'):
            item['imageUrl'] = saved_images.get(name_key(item.get('foodName') or ''))
        if item.get('fibre') is None:   # show the fibre we already know for it, so the card matches what will be logged
            item['fibre'] = _saved_fibre(item.get('barcode'), item.get('foodName'), (item.get('servingG') or 0) * (item.get('quantity') or 1), item.get('calories'))
        item['verified'] = (item.get('barcode') in overridden_barcodes) or item.get('source') in ('manual', 'custom_food', 'favourite')
        per100 = _per_100g(item.get('calories'), item.get('protein'), item.get('carbs'), item.get('fat'),
                            item.get('servingG'), item.get('quantity', 1))
        item['nutri_score_label'] = nutri_score_label(item.get('nutriScoreGrade'))
        item['tags'] = nutrient_tags(
            kcal_per_100g=per100.get('kcal_per_100g'),
            protein_per_100g=per100.get('protein_per_100g'),
            fat_per_100g=per100.get('fat_per_100g'),
        )
        results.append(item)
    return results


@bp.route('/api/recent')
def recent_foods():
    rider_id  = _default_rider_id()
    meal_type = request.args.get('meal_type')
    offset    = int(request.args.get('offset', 0))
    limit     = int(request.args.get('limit', 20))

    rows = query_db('''
        SELECT foodName, barcode, brand, calories, protein, carbs, fat, fibre, servingG,
               quantity, mealType, imageUrl, nutriScoreGrade, source, createdAt
        FROM FoodLog
        WHERE riderId=? AND calories IS NOT NULL AND COALESCE(source,'') != 'imported_total'
        ORDER BY createdAt ASC
    ''', [rider_id])

    # Per meal: dinner shows what you've had for dinner, breakfast what you've had for breakfast, etc.
    # A meal with no history yet falls back to everything so the list is never empty.
    fallback = False
    if meal_type:
        in_meal = [r for r in rows if (r['mealType'] or 'uncategorised') == meal_type]
        if in_meal:
            rows = in_meal
        else:
            fallback = True

    groups = {}
    for r in rows:
        key = (r['foodName'] or '').strip().lower()
        if not key:
            continue
        g = groups.setdefault(key, {'freq': 0, 'mealCounts': {}, 'latest': None})
        g['freq'] += 1
        mt = r['mealType'] or 'uncategorised'
        g['mealCounts'][mt] = g['mealCounts'].get(mt, 0) + 1
        g['latest'] = r  # rows are ASC, so the last write wins == most recent

    now = date.today()
    scored = []
    for key, g in groups.items():
        latest = g['latest']
        try:
            days_since = max(0, (now - date.fromisoformat(latest['createdAt'][:10])).days)
        except (ValueError, TypeError):
            days_since = 999
        recency_score = 5.0 / (days_since + 1)
        top_meal = max(g['mealCounts'].items(), key=lambda kv: kv[1])[0]
        meal_boost = 3.0 if meal_type and top_meal == meal_type else 0.0
        score = g['freq'] + recency_score + meal_boost
        scored.append((score, latest))

    scored.sort(key=lambda x: x[0], reverse=True)
    page = scored[offset:offset + limit]
    results = _annotate_food_rows([r for _, r in page])

    return jsonify({'items': results, 'hasMore': offset + limit < len(scored), 'fallback': fallback})


# ── Favourites (Go-Tos) ─────────────────────────────────────────────────

def _favourite_dict(row):
    item = dict(row)
    per100 = _per_100g(row['calories'], row['protein'], row['carbs'], row['fat'], row['servingG'], 1)
    item['verified'] = True
    item['nutri_score_label'] = None
    item['tags'] = nutrient_tags(
        kcal_per_100g=per100.get('kcal_per_100g'),
        protein_per_100g=per100.get('protein_per_100g'),
        fat_per_100g=per100.get('fat_per_100g'),
    )
    return item


@bp.route('/api/favourites', methods=['GET'])
def list_favourites():
    rider_id = _default_rider_id()
    rows = query_db(
        'SELECT * FROM FoodFavourite WHERE riderId=? ORDER BY foodName COLLATE NOCASE ASC',
        [rider_id],
    )
    return jsonify([_favourite_dict(r) for r in rows])


@bp.route('/api/favourites', methods=['POST'])
def add_favourite():
    d = request.get_json()
    rider_id = _default_rider_id()
    name = (d.get('foodName') or '').strip()
    if not name:
        return jsonify({'error': 'foodName required'}), 400

    existing = query_db(
        'SELECT id FROM FoodFavourite WHERE riderId=? AND ('
        '  (barcode IS NOT NULL AND barcode=?) OR LOWER(TRIM(foodName))=?'
        ')',
        [rider_id, d.get('barcode'), name.lower()], one=True,
    )
    if existing:
        return jsonify({'ok': True, 'id': existing['id'], 'alreadyExists': True})

    db = get_db()
    cur = db.execute('''
        INSERT INTO FoodFavourite (riderId, barcode, foodName, brand, calories, protein, carbs, fat, servingG, imageUrl)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', [
        rider_id, d.get('barcode'), name, d.get('brand'),
        d.get('calories'), d.get('protein'), d.get('carbs'), d.get('fat'),
        d.get('servingG'), d.get('imageUrl'),
    ])
    db.commit()
    return jsonify({'ok': True, 'id': cur.lastrowid}), 201


@bp.route('/api/favourites/<int:fav_id>', methods=['DELETE'])
def delete_favourite(fav_id):
    db = get_db()
    db.execute('DELETE FROM FoodFavourite WHERE id=?', [fav_id])
    db.commit()
    return jsonify({'ok': True})


# ── My Foods (custom, barcode-less) ──────────────────────────────────────
# Stored as per-100g values, same shape as an OFF product, so they slot into
# the same search/serving-picker flow as scanned/searched foods.

def _custom_food_dict(row):
    item = dict(row)
    item['barcode'] = None
    item['kcal_per_100g']    = row['calories']
    item['protein_per_100g'] = row['protein']
    item['carbs_per_100g']   = row['carbs']
    item['fat_per_100g']     = row['fat']
    item['fibre_per_100g']   = row['fibre100g']
    item['serving_g']        = row['servingG']
    item['image_url']        = row['imageUrl']
    item['verified'] = True
    item['nutri_score_label'] = None
    item['tags'] = nutrient_tags(
        kcal_per_100g=row['calories'], protein_per_100g=row['protein'], fat_per_100g=row['fat'],
    )
    return item


@bp.route('/api/my-foods', methods=['GET'])
def list_custom_foods():
    rider_id = _default_rider_id()
    rows = query_db(
        'SELECT * FROM CustomFood WHERE riderId=? ORDER BY name COLLATE NOCASE ASC',
        [rider_id],
    )
    return jsonify([_custom_food_dict(r) for r in rows])


@bp.route('/api/my-foods', methods=['POST'])
def create_custom_food():
    d = request.get_json()
    rider_id = _default_rider_id()
    name = (d.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'name required'}), 400
    db = get_db()
    cur = db.execute('''
        INSERT INTO CustomFood (riderId, name, brand, calories, protein, carbs, fat, fibre100g, servingG, imageUrl)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', [
        rider_id, name, d.get('brand'),
        d.get('kcal_per_100g'), d.get('protein_per_100g'), d.get('carbs_per_100g'), d.get('fat_per_100g'),
        d.get('fibre_per_100g'), d.get('serving_g'), d.get('image_url'),
    ])
    db.commit()
    return jsonify({'ok': True, 'id': cur.lastrowid}), 201


@bp.route('/api/my-foods/<int:food_id>', methods=['PUT'])
def update_custom_food(food_id):
    d = request.get_json()
    name = (d.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'name required'}), 400
    db = get_db()
    db.execute('''
        UPDATE CustomFood SET name=?, brand=?, calories=?, protein=?, carbs=?, fat=?, fibre100g=?, servingG=?, imageUrl=?
        WHERE id=?
    ''', [
        name, d.get('brand'),
        d.get('kcal_per_100g'), d.get('protein_per_100g'), d.get('carbs_per_100g'), d.get('fat_per_100g'),
        d.get('fibre_per_100g'), d.get('serving_g'), d.get('image_url'), food_id,
    ])
    db.commit()
    return jsonify({'ok': True})


@bp.route('/api/my-foods/<int:food_id>', methods=['DELETE'])
def delete_custom_food(food_id):
    db = get_db()
    db.execute('DELETE FROM CustomFood WHERE id=?', [food_id])
    db.commit()
    return jsonify({'ok': True})


# ── Saved meals ───────────────────────────────────────────────────────

@bp.route('/api/saved-meals', methods=['GET'])
def list_saved_meals():
    meals = query_db('SELECT id, name, createdAt FROM SavedMeal ORDER BY name ASC')
    result = []
    for m in meals:
        items = query_db(
            'SELECT id, foodName, calories, protein, carbs, fat, fibre, servingG, barcode '
            'FROM SavedMealItem WHERE mealId=? ORDER BY id',
            [m['id']],
        )
        result.append({
            'id':        m['id'],
            'name':      m['name'],
            'createdAt': m['createdAt'],
            'items':     [dict(i) for i in items],
            'totalCal':  round(sum(i['calories'] or 0 for i in items)),
        })
    return jsonify(result)


@bp.route('/api/saved-meals', methods=['POST'])
def create_saved_meal():
    d = request.get_json()
    name  = (d.get('name') or 'Saved Meal').strip()[:100]
    items = d.get('items') or []
    if not items:
        return jsonify({'error': 'No items provided'}), 400
    db = get_db()
    cur = db.execute('INSERT INTO SavedMeal (name) VALUES (?)', [name])
    meal_id = cur.lastrowid
    for item in items:
        db.execute('''
            INSERT INTO SavedMealItem (mealId, foodName, calories, protein, carbs, fat, fibre, servingG, barcode)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', [
            meal_id, item['foodName'],
            item.get('calories'), item.get('protein'), item.get('carbs'),
            item.get('fat'), item.get('fibre'), item.get('servingG'), item.get('barcode'),
        ])
    db.commit()
    return jsonify({'id': meal_id, 'name': name}), 201


@bp.route('/api/saved-meals/<int:meal_id>', methods=['DELETE'])
def delete_saved_meal(meal_id):
    db = get_db()
    db.execute('DELETE FROM SavedMealItem WHERE mealId=?', [meal_id])
    db.execute('DELETE FROM SavedMeal WHERE id=?', [meal_id])
    db.commit()
    return '', 204


@bp.route('/api/saved-meals/<int:meal_id>/log', methods=['POST'])
def log_saved_meal(meal_id):
    d         = request.get_json()
    rider_id  = _default_rider_id()
    date_str  = d.get('date')
    meal_type = d.get('meal_type', 'uncategorised')
    scale     = float(d.get('scale', 1.0))
    items = query_db(
        'SELECT * FROM SavedMealItem WHERE mealId=?', [meal_id]
    )
    if not items:
        return jsonify({'error': 'Meal not found or empty'}), 404
    db = get_db()
    for item in items:
        db.execute('''
            INSERT INTO FoodLog
                (riderId, logDate, barcode, foodName, calories, protein, carbs, fat, fibre,
                 servingG, quantity, mealType, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, 'saved_meal')
        ''', [
            rider_id, date_str, item['barcode'], item['foodName'],
            (item['calories'] * scale) if item['calories'] else None,
            (item['protein']  * scale) if item['protein']  else None,
            (item['carbs']    * scale) if item['carbs']    else None,
            (item['fat']      * scale) if item['fat']      else None,
            (item['fibre']    * scale) if item['fibre']    else None,
            (item['servingG'] * scale) if item['servingG'] else None,
            meal_type,
        ])
    db.commit()
    _mqtt_nutrition()
    return jsonify({'ok': True})


# ── Copy day ──────────────────────────────────────────────────────────

@bp.route('/api/copy-day', methods=['POST'])
def copy_day():
    d         = request.get_json()
    from_date = d.get('from_date')
    to_date   = d.get('to_date')
    meal_type = d.get('meal_type')
    rider_id  = _default_rider_id()
    if meal_type:
        entries = query_db(
            'SELECT * FROM FoodLog WHERE riderId=? AND logDate=? AND mealType=?',
            [rider_id, from_date, meal_type],
        )
    else:
        entries = query_db(
            'SELECT * FROM FoodLog WHERE riderId=? AND logDate=?',
            [rider_id, from_date],
        )
    if not entries:
        return jsonify({'count': 0})
    db = get_db()
    for e in entries:
        db.execute('''
            INSERT INTO FoodLog
                (riderId, logDate, barcode, foodName, calories, protein, carbs, fat, fibre,
                 servingG, quantity, mealType, source, brand, imageUrl, nutriScoreGrade)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'copied', ?, ?, ?)
        ''', [
            rider_id, to_date, e['barcode'], e['foodName'],
            e['calories'], e['protein'], e['carbs'], e['fat'], e['fibre'],
            e['servingG'], e['quantity'],
            e['mealType'] if e['mealType'] else 'uncategorised',
            e['brand'], e['imageUrl'], e['nutriScoreGrade'],
        ])
    db.commit()
    _mqtt_nutrition()
    return jsonify({'ok': True, 'count': len(entries)})


# ── Food overrides ────────────────────────────────────────────────────

@bp.route('/api/override', methods=['POST'])
def save_override():
    d       = request.get_json()
    barcode = (d.get('barcode') or '').strip()
    if not barcode:
        return jsonify({'error': 'barcode required'}), 400
    db = get_db()
    db.execute('''
        INSERT INTO FoodOverride
            (barcode, name, brand, kcal100g, protein100g, carbs100g, fat100g, fibre100g, servingG,
             imageUrl, nutriScoreGrade, updatedAt)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(barcode) DO UPDATE SET
            name=excluded.name, brand=excluded.brand,
            kcal100g=excluded.kcal100g, protein100g=excluded.protein100g,
            carbs100g=excluded.carbs100g, fat100g=excluded.fat100g, fibre100g=excluded.fibre100g,
            servingG=excluded.servingG, imageUrl=excluded.imageUrl,
            nutriScoreGrade=excluded.nutriScoreGrade, updatedAt=datetime('now')
    ''', [
        barcode, d.get('name'), d.get('brand'),
        d.get('kcal_per_100g'), d.get('protein_per_100g'),
        d.get('carbs_per_100g'), d.get('fat_per_100g'), d.get('fibre_per_100g'), d.get('serving_g'),
        d.get('image_url'), d.get('nutri_score_grade'),
    ])
    db.commit()
    return jsonify({'ok': True})


# ── Weekly summary ────────────────────────────────────────

@bp.route('/week')
@bp.route('/week/<date_str>')
def week_index(date_str=None):
    return render_template('nutrition_week.html')


@bp.route('/api/week/<date_str>')
def week_log(date_str):
    from datetime import datetime, timedelta
    rider_id = _default_rider_id()

    # Parse date and find Monday of that week
    d = datetime.fromisoformat(date_str)
    monday = d - timedelta(days=d.weekday())

    days = []
    goals = _get_goals()
    bmr = goals.get('bmr')

    for i in range(7):
        date = monday + timedelta(days=i)
        date_iso = date.strftime('%Y-%m-%d')
        dow_name = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'][i]

        # Food
        food = query_db(
            'SELECT COALESCE(SUM(calories),0) as cal, COALESCE(SUM(protein),0) as p, '
            'COALESCE(SUM(carbs),0) as c, COALESCE(SUM(fat),0) as f '
            'FROM FoodLog WHERE riderId=? AND logDate=?',
            [rider_id, date_iso], one=True
        )
        eaten = int(food['cal']) if food else 0
        protein = float(food['p'] or 0) if food else 0
        carbs = float(food['c'] or 0) if food else 0
        fat = float(food['f'] or 0) if food else 0

        # Water
        water = query_db(
            'SELECT COALESCE(SUM(ml),0) as total FROM HydrationLog WHERE riderId=? AND logDate=?',
            [rider_id, date_iso], one=True
        )
        water_ml = int(water['total']) if water else 0

        # Burn
        ride_cal = query_db(
            'SELECT COALESCE(SUM(calories),0) as total FROM Activity WHERE riderId=? AND date(startDateLocal)=?',
            [rider_id, date_iso], one=True
        )
        ride_cal = int(ride_cal['total']) if ride_cal else 0
        from services import workouts as _wk
        ride_cal += _wk.calories_on(rider_id, date_iso)           # recorded walks / runs

        garmin = query_db(
            'SELECT totalCalories FROM GarminDaily WHERE date=?',
            [date_iso], one=True
        )
        garmin_total = garmin['totalCalories'] if garmin else None

        from services import goal_model
        est = goal_model.day_burn(rider_id, date_iso)
        if est:
            burned = est[0]
        elif bmr:
            burned = int(bmr) + ride_cal
        elif garmin_total:
            burned = garmin_total
        elif ride_cal:
            burned = ride_cal
        else:
            burned = None

        net = (eaten - burned) if burned else None

        days.append({
            'date': date_iso,
            'dow': dow_name,
            'eaten': eaten,
            'burned': burned,
            'net': net,
            'goal': goals.get('cal'),
            'vs_goal': (eaten - goals['cal']) if goals.get('cal') and eaten else None,
            'protein': round(protein, 1),
            'carbs': round(carbs, 1),
            'fat': round(fat, 1),
            'water_ml': water_ml,
            'has_data': eaten > 0 or water_ml > 0 or (burned and burned > int(bmr or 0)),
            'has_activity': ride_cal > 0
        })

    # Weekly totals — only include days up to and including today with actual logged data
    from datetime import date as date_class
    today = date_class.today()
    past_days = [d for d in days if d['date'] <= today.isoformat() and d['has_data']]

    total_eaten = sum(d['eaten'] for d in past_days)
    total_burned = sum(d['burned'] or 0 for d in past_days)
    total_net = total_eaten - total_burned if total_burned else None

    return jsonify({
        'days': days,
        'goals': goals,
        'week_start': monday.strftime('%Y-%m-%d'),
        'totals': {
            'eaten': total_eaten,
            'burned': total_burned,
            'net': total_net,
            'goal': (goals.get('cal') or 0) * len(past_days) or None,
            'vs_goal': (total_eaten - goals['cal'] * len(past_days)) if goals.get('cal') and past_days else None,
            'protein': round(sum(d['protein'] for d in past_days), 1),
            'carbs': round(sum(d['carbs'] for d in past_days), 1),
            'fat': round(sum(d['fat'] for d in past_days), 1),
            'water_ml': sum(d['water_ml'] for d in past_days)
        }
    })


# ── Weight tracking ──────────────────────────────────────────────────

@bp.route('/weight')
def weight_graph_page():
    return render_template('nutrition_weight_graph.html')


@bp.route('/weight/backfill')
def weight_backfill_page():
    return render_template('nutrition_weight_backfill.html')


@bp.route('/api/weight/settings', methods=['GET'])
def get_weight_settings():
    return jsonify(_weight_settings())


@bp.route('/api/weight/settings', methods=['POST'])
def save_weight_settings():
    d = request.get_json(silent=True) or {}
    try:
        alpha = float(d.get('emaAlpha') or 0.1)
    except (TypeError, ValueError):
        alpha = -1
    if not (0 < alpha <= 1) or (d.get('dietStartDate') and not _valid_date(d.get('dietStartDate'))) \
            or _bad_numbers(d, ('goalWeightKg',), hi=MAX_SANE_KG):
        return jsonify({'error': 'emaAlpha must be in (0,1], dietStartDate a real date, goalWeightKg a sane weight'}), 400
    db = get_db()
    db.execute('INSERT OR IGNORE INTO Settings (id) VALUES (1)')
    db.execute('''
        UPDATE Settings SET nutritionWeightUnit=?, nutritionWeightEmaAlpha=?, dietStartDate=?
        WHERE id=1
    ''', [
        d.get('unit', 'stlb'),
        float(d.get('emaAlpha') or 0.1),
        d.get('dietStartDate') or None,
    ])
    if 'goalWeightKg' in d:
        db.execute('UPDATE Settings SET goalWeightKg=? WHERE id=1', [float(d['goalWeightKg']) if d.get('goalWeightKg') else None])
    db.commit()
    _mqtt_nutrition()
    return jsonify({'ok': True})


@bp.route('/api/weight', methods=['POST'])
def log_weight():
    d = request.get_json()
    rider_id = _default_rider_id()
    date_str = d.get('date')
    if not _valid_date(date_str):
        return jsonify({'error': 'a valid date (YYYY-MM-DD) is required'}), 400

    try:
        if d.get('unit') == 'kg':
            weight_kg = float(d['kg'])
        else:
            weight_kg = stone_lb_to_kg(d.get('stone') or 0, d.get('lb') or 0)
    except (KeyError, TypeError, ValueError):
        return jsonify({'error': 'invalid weight'}), 400

    if not (MIN_SANE_KG <= weight_kg <= MAX_SANE_KG):
        return jsonify({'error': f'weight must be between {MIN_SANE_KG} and {MAX_SANE_KG} kg'}), 400

    existing = query_db(
        'SELECT id, weightKg, source FROM WeightLog WHERE riderId=? AND logDate=?',
        [rider_id, date_str], one=True,
    )
    if existing and not d.get('force'):
        # id + source included so an automatic caller (the phone app) can decide whether to defer to what's already here
        # rather than push over it -- see routes/nutrition.py CLAUDE.md note and headwind-android's Repo.push().
        return jsonify({'exists': True, 'existingKg': round(existing['weightKg'], 2), 'id': existing['id'], 'source': existing['source']})

    db = get_db()
    if existing:
        db.execute('UPDATE WeightLog SET weightKg=?, source=? WHERE id=?',
                   [weight_kg, d.get('source', 'manual'), existing['id']])
        entry_id = existing['id']
    else:
        cur = db.execute(
            'INSERT INTO WeightLog (riderId, logDate, weightKg, source) VALUES (?, ?, ?, ?)',
            [rider_id, date_str, weight_kg, d.get('source', 'manual')],
        )
        entry_id = cur.lastrowid
    db.commit()
    _mqtt_nutrition()
    return jsonify({'ok': True, 'id': entry_id, 'weightKg': round(weight_kg, 2)})


@bp.route('/api/weight/<int:entry_id>', methods=['DELETE'])
def delete_weight(entry_id):
    db = get_db()
    db.execute('DELETE FROM WeightLog WHERE id=?', [entry_id])
    db.commit()
    _mqtt_nutrition()
    return jsonify({'ok': True})


@bp.route('/api/weight/refresh', methods=['POST'])
def weight_refresh():
    """Pull the latest scale readings from Home Assistant (throttled) — called when the app is opened."""
    from services import homeassistant
    return jsonify({'changed': homeassistant.refresh()})


@bp.route('/api/metrics')
def body_metrics():
    """Body-composition series (body fat, etc.) from Home Assistant for the same window as the weight graph."""
    from services.homeassistant import TARGETS
    rider_id = _default_rider_id()
    period = request.args.get('period', 'M').upper()
    end_date = date.fromisoformat(request.args['end']) if request.args.get('end') else date.today()
    if period == 'ALL':
        start_date = date(2000, 1, 1)
    else:
        start_date = end_date - timedelta(days=WEIGHT_PERIOD_DAYS.get(period, 30))
    rows = query_db('''
        SELECT logDate, metric, value, unit FROM BodyMetric
        WHERE riderId=? AND logDate BETWEEN ? AND ? ORDER BY logDate ASC
    ''', [rider_id, start_date.isoformat(), end_date.isoformat()])
    out = {}
    for row in rows:
        m = out.setdefault(row['metric'], {'metric': row['metric'], 'label': TARGETS.get(row['metric'], (row['metric'],))[0],
                                           'unit': row['unit'] or '', 'points': []})
        m['points'].append({'date': row['logDate'], 'value': row['value']})
    return jsonify(list(out.values()))


@bp.route('/api/weight/graph')
def weight_graph_data():
    rider_id = _default_rider_id()
    period = request.args.get('period', 'M').upper()
    end_param = request.args.get('end')
    entries = _all_weight_entries(rider_id)
    settings = _weight_settings()
    unit = settings['unit']
    diet_start = settings['dietStartDate']

    if not entries:
        return jsonify({
            'raw': [], 'trend': [], 'unit': unit, 'dietStartDate': diet_start,
            'periodStart': None, 'periodEnd': None, 'summary': None, 'stoneLines': [],
        })

    today = date.today()
    end_date = date.fromisoformat(end_param) if end_param else today
    if period == 'ALL':
        start_date = entries[0][0]
    else:
        start_date = end_date - timedelta(days=WEIGHT_PERIOD_DAYS.get(period, 30))

    trend_end = max(end_date, entries[-1][0], today)
    trend_map = compute_trend(entries, alpha=settings['emaAlpha'], end_date=trend_end)

    sources = {x['logDate']: x['source'] for x in query_db('SELECT logDate, source FROM WeightLog WHERE riderId=?', [rider_id])}
    raw_points = [
        {'date': d.isoformat(), 'kg': round(w, 2), 'source': sources.get(d.isoformat())}
        for d, w in entries if start_date <= d <= end_date
    ]
    trend_points = [
        {'date': d.isoformat(), 'kg': round(t, 2)}
        for d, t in sorted(trend_map.items()) if start_date <= d <= end_date
    ]

    def trend_on_or_after(target):
        candidates = [(d, t) for d, t in trend_map.items() if d >= target]
        return min(candidates, key=lambda x: x[0])[1] if candidates else None

    def trend_on_or_before(target):
        candidates = [(d, t) for d, t in trend_map.items() if d <= target]
        return max(candidates, key=lambda x: x[0])[1] if candidates else None

    period_start_trend = trend_on_or_after(start_date)
    period_end_trend = trend_on_or_before(end_date)

    diet_start_date, diet_start_trend = None, None
    if diet_start:
        try:
            diet_start_date = date.fromisoformat(diet_start)
            diet_start_trend = trend_on_or_after(diet_start_date)
        except ValueError:
            pass

    summary = None
    if period_start_trend is not None and period_end_trend is not None:
        change_period = period_end_trend - period_start_trend
        days_span = max(1, (end_date - start_date).days)
        summary = {
            'changeKg':     round(change_period, 2),
            'periodDays':   days_span,
            'weeklyRateKg': round(change_period / days_span * 7, 3),
        }
        if diet_start_trend is not None:
            change_diet = period_end_trend - diet_start_trend
            diet_days = max(1, (end_date - diet_start_date).days)
            summary['changeSinceDietStartKg'] = round(change_diet, 2)
            summary['dietWeeklyRateKg']       = round(change_diet / diet_days * 7, 3)

    all_weights = [w for _, w in entries]
    stone_lines = [
        round(k, 2) for k in
        stone_milestones(diet_start_trend or max(all_weights), min(all_weights))
    ]

    return jsonify({
        'raw':           raw_points,
        'trend':         trend_points,
        'unit':          unit,
        'dietStartDate': diet_start,
        'periodStart':   start_date.isoformat(),
        'periodEnd':     end_date.isoformat(),
        'summary':       summary,
        'stoneLines':    stone_lines,
    })


@bp.route('/api/weight/backfill/preview', methods=['POST'])
def weight_backfill_preview():
    rider_id = _default_rider_id()
    if 'file' in request.files:
        text = request.files['file'].read().decode('utf-8', errors='replace')
    else:
        text = (request.get_json(silent=True) or {}).get('text', '')

    rows = parse_backfill_rows(text)
    existing_dates = {
        r['logDate'] for r in query_db('SELECT logDate FROM WeightLog WHERE riderId=?', [rider_id])
    }
    for row in rows:
        row['duplicate'] = bool(row.get('date')) and not row.get('error') and row['date'] in existing_dates
    return jsonify({'rows': rows})


@bp.route('/api/weight/backfill/confirm', methods=['POST'])
def weight_backfill_confirm():
    d = request.get_json()
    rider_id = _default_rider_id()
    rows = d.get('rows', [])
    db = get_db()
    imported, skipped = 0, 0
    for row in rows:
        date_str  = row.get('date')
        weight_kg = row.get('weightKg')
        if not date_str or weight_kg is None:
            continue
        existing = query_db(
            'SELECT id FROM WeightLog WHERE riderId=? AND logDate=?',
            [rider_id, date_str], one=True,
        )
        if existing:
            if not row.get('overwrite'):
                skipped += 1
                continue
            db.execute('UPDATE WeightLog SET weightKg=?, source=? WHERE id=?',
                       [weight_kg, 'backfill', existing['id']])
        else:
            db.execute(
                'INSERT INTO WeightLog (riderId, logDate, weightKg, source) VALUES (?, ?, ?, ?)',
                [rider_id, date_str, weight_kg, 'backfill'],
            )
        imported += 1
    db.commit()
    _mqtt_nutrition()
    return jsonify({'ok': True, 'imported': imported, 'skipped': skipped})
