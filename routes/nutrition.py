import re
import requests
from flask import Blueprint, jsonify, request, render_template
from database import get_db, query_db

bp = Blueprint('nutrition', __name__, url_prefix='/nutrition')


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
               nutritionFatGoal, nutritionWaterGoalMl
        FROM Settings WHERE id=1
    ''', one=True)
    if not row:
        return {}
    return {
        'cal':   row['nutritionCalGoal'],
        'protein': row['nutritionProteinGoal'],
        'carbs': row['nutritionCarbGoal'],
        'fat':   row['nutritionFatGoal'],
        'water': row['nutritionWaterGoalMl'],
    }


@bp.route('/')
def index():
    return render_template('nutrition.html')


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
            nutritionFatGoal=?, nutritionWaterGoalMl=?
        WHERE id=1
    ''', [
        d.get('cal')  or None,
        d.get('protein') or None,
        d.get('carbs') or None,
        d.get('fat')   or None,
        d.get('water') or None,
    ])
    db.commit()
    return jsonify({'ok': True})


@bp.route('/api/search')
def search_food():
    q = request.args.get('q', '').strip()
    if not q:
        return jsonify([])
    try:
        resp = requests.get(
            'https://world.openfoodfacts.org/cgi/search.pl',
            params={
                'search_terms':   q,
                'search_simple':  1,
                'action':         'process',
                'json':           1,
                'page_size':      12,
                'fields':         'code,product_name,product_name_en,brands,nutriments,serving_size',
                'countries_tags': 'en:united-kingdom',
            },
            timeout=8,
            headers={'User-Agent': 'Headwind-Nutrition/1.0'},
        )
        if not resp.ok:
            return jsonify([])
        products = resp.json().get('products', [])
        results = []
        for p in products:
            name = (p.get('product_name_en') or p.get('product_name') or '').strip()
            if not name:
                continue
            n = p.get('nutriments', {})
            kcal = n.get('energy-kcal_100g')
            if not kcal:
                continue  # skip products with no calorie data
            results.append({
                'barcode':          p.get('code', ''),
                'name':             name,
                'brand':            (p.get('brands') or '').split(',')[0].strip(),
                'kcal_per_100g':    kcal,
                'protein_per_100g': n.get('proteins_100g'),
                'carbs_per_100g':   n.get('carbohydrates_100g'),
                'fat_per_100g':     n.get('fat_100g'),
                'serving_g':        _parse_serving_g(p.get('serving_size', '')),
            })
        return jsonify(results)
    except Exception as e:
        return jsonify([])


@bp.route('/api/lookup/<barcode>')
def lookup(barcode):
    url = f'https://world.openfoodfacts.org/api/v2/product/{barcode}.json'
    try:
        resp = requests.get(url, timeout=8, headers={'User-Agent': 'Headwind-Nutrition/1.0'})
        if not resp.ok:
            return jsonify({'error': 'not_found'}), 404
        data = resp.json()
        if data.get('status') != 1:
            return jsonify({'error': 'not_found'}), 404
        p = data['product']
        n = p.get('nutriments', {})
        return jsonify({
            'name':             (p.get('product_name_en') or p.get('product_name') or '').strip() or 'Unknown product',
            'brand':            p.get('brands', '').split(',')[0].strip(),
            'kcal_per_100g':    n.get('energy-kcal_100g'),
            'protein_per_100g': n.get('proteins_100g'),
            'carbs_per_100g':   n.get('carbohydrates_100g'),
            'fat_per_100g':     n.get('fat_100g'),
            'serving_g':        _parse_serving_g(p.get('serving_size', '')),
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


def _mqtt_nutrition():
    try:
        from services.mqtt import push_update_nutrition
        push_update_nutrition()
    except Exception:
        pass


@bp.route('/api/log', methods=['POST'])
def log_food():
    d = request.get_json()
    rider_id = _default_rider_id()
    db = get_db()
    db.execute('''
        INSERT INTO FoodLog (riderId, logDate, barcode, foodName, calories, protein, carbs, fat, servingG, quantity)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', [
        rider_id, d['date'], d.get('barcode'), d['name'],
        d.get('calories'), d.get('protein'), d.get('carbs'), d.get('fat'),
        d.get('serving_g'), d.get('quantity', 1),
    ])
    db.commit()
    entry_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]
    _mqtt_nutrition()
    return jsonify({'ok': True, 'id': entry_id})


@bp.route('/api/log/<int:entry_id>', methods=['DELETE'])
def delete_log(entry_id):
    db = get_db()
    db.execute('DELETE FROM FoodLog WHERE id=?', [entry_id])
    db.commit()
    _mqtt_nutrition()
    return jsonify({'ok': True})


@bp.route('/api/water', methods=['POST'])
def add_water():
    d = request.get_json()
    rider_id = _default_rider_id()
    db = get_db()
    db.execute(
        'INSERT INTO HydrationLog (riderId, logDate, ml) VALUES (?, ?, ?)',
        [rider_id, d['date'], int(d['ml'])],
    )
    db.commit()
    entry_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]
    _mqtt_nutrition()
    return jsonify({'ok': True, 'id': entry_id})


@bp.route('/api/water/<int:entry_id>', methods=['DELETE'])
def delete_water(entry_id):
    db = get_db()
    db.execute('DELETE FROM HydrationLog WHERE id=?', [entry_id])
    db.commit()
    _mqtt_nutrition()
    return jsonify({'ok': True})


@bp.route('/api/day/<date_str>')
def day_log(date_str):
    rider_id = _default_rider_id()

    entries = query_db('''
        SELECT id, foodName, calories, protein, carbs, fat, servingG, quantity, barcode
        FROM FoodLog WHERE riderId=? AND logDate=? ORDER BY createdAt ASC
    ''', [rider_id, date_str])

    # Water total
    water_row = query_db(
        'SELECT COALESCE(SUM(ml),0) as total FROM HydrationLog WHERE riderId=? AND logDate=?',
        [rider_id, date_str], one=True,
    )
    water_ml = int(water_row['total']) if water_row else 0

    # Calories burned: prefer Garmin total (includes BMR+active), fall back to ride sum
    garmin_row = query_db(
        'SELECT totalCalories, activeCalories FROM GarminDaily WHERE date=?',
        [date_str], one=True,
    )
    garmin_total  = garmin_row['totalCalories']  if garmin_row else None
    garmin_active = garmin_row['activeCalories'] if garmin_row else None

    ride_row = query_db('''
        SELECT COALESCE(SUM(calories), 0) as total
        FROM Activity
        WHERE riderId=? AND date(startDateLocal)=? AND calories IS NOT NULL
    ''', [rider_id, date_str], one=True)
    ride_cal = int(ride_row['total']) if ride_row else 0

    goals = _get_goals()

    return jsonify({
        'entries':        [dict(e) for e in entries],
        'water_ml':       water_ml,
        'ride_calories':  ride_cal,
        'garmin_total':   garmin_total,
        'garmin_active':  garmin_active,
        'goals':          goals,
    })
