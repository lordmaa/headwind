#!/usr/bin/env python3
"""Build the PUBLIC DEMO database for Headwind from a real database, safely.

    python3 demo/build_demo_db.py --source /path/to/your/bike.db --out demo/out

What it does (the privacy part is the point; read it before you publish anything):
  * Finds your PRIVACY ZONES automatically: every place you frequently start or finish (home, work, the car park) is detected from the data. No coordinates are ever
    written into this script, the output, or the report.
  * Removes every GPS point inside a zone (and near each ride's own start/end), keeps the longest remaining stretch of each ride, and recomputes distance, time,
    climbing, speeds and calories from what is left so nothing contradicts the map.
  * Drops ride names, notes, descriptions, places, AI comments and raw import data. Names become "Morning Ride" etc. Weather values (no coordinates) are kept.
  * Shifts the whole timeline forward by whole weeks so the newest ride is yesterday (the demo looks alive; weekdays are preserved).
  * Thins every track to ~600 points (smaller database, faster pages).
  * Copies your segments (those clear of the zones), then re-scores every ride against them so leaderboards, PRs and efforts all work.
  * Invents the rest: a fictional rider, six months of food diary, weight, water, steps, sleep/HRV, workouts, three bikes with parts and a service log.
  * Finally AUDITS its own output and refuses to finish if any point lies inside a zone or any original ride name/id survived.
The result is out/pristine.db (+ out/bikeimg/). It is gitignored: it is derived from your real data, so do not commit or publish the file itself except as the running demo.
"""
import argparse
import json
import math
import os
import random
import shutil
import sqlite3
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
LONDON = ZoneInfo('Europe/London')
MI = 1609.344

# ------------------------------------------------------------------ geometry (no numpy needed)
def _cos(lat):
    return math.cos(math.radians(lat))


def dist_m(a, b):
    """Fast equirectangular distance between two (lat, lng): accurate to well under 1% at these ranges."""
    x = math.radians(b[1] - a[1]) * _cos((a[0] + b[0]) / 2)
    y = math.radians(b[0] - a[0])
    return 6371000.0 * math.hypot(x, y)


def detect_zones(endpoints, min_count, cell_lat=0.0036, cell_lng=0.0062):
    """Clusters of frequently used start/end points -> [(lat, lng, n)]. 8-neighbour merge of grid cells, centre = mean of members."""
    cells = defaultdict(list)
    for p in endpoints:
        cells[(round(p[0] / cell_lat), round(p[1] / cell_lng))].append(p)
    seen, zones = set(), []
    for key in sorted(cells, key=lambda k: -len(cells[k])):
        if key in seen:
            continue
        stack, members = [key], []
        while stack:
            k = stack.pop()
            if k in seen or k not in cells:
                continue
            seen.add(k)
            members += cells[k]
            stack += [(k[0] + i, k[1] + j) for i in (-1, 0, 1) for j in (-1, 0, 1)]
        if len(members) >= min_count:
            zones.append((sum(m[0] for m in members) / len(members), sum(m[1] for m in members) / len(members), len(members)))
    return zones


def inside_any_line(pts, zones, radius):
    return any(p[0] is not None and inside_any(p, zones, radius) for p in pts)


def inside_any(pt, zones, radius):
    for z in zones:
        if abs(pt[0] - z[0]) < radius / 111000.0 + 0.0005 and dist_m(pt, z) < radius:
            return True
    return False


# ------------------------------------------------------------------ rides
def parse_when(s):
    d = datetime.fromisoformat(str(s).replace('Z', '+00:00'))
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def label(local_dt):
    h = local_dt.hour
    return 'Morning Ride' if h < 11 else 'Lunch Ride' if h < 14 else 'Afternoon Ride' if h < 18 else 'Evening Ride'


def elevation_gain(alts, hysteresis=3.0):
    if len(alts) < 5:
        return 0.0
    sm = [sum(alts[max(0, i - 2): i + 3]) / len(alts[max(0, i - 2): i + 3]) for i in range(len(alts))]
    gain, ref = 0.0, sm[0]
    for v in sm[1:]:
        if v - ref >= hysteresis:
            gain += v - ref
            ref = v
        elif ref - v >= hysteresis:
            ref = v
    return gain


def thin(streams, max_points):
    n = len(streams['latlng']['data'])
    if n <= max_points:
        idx = list(range(n))
    else:
        step = math.ceil(n / max_points)
        idx = list(range(0, n, step))
        if idx[-1] != n - 1:
            idx.append(n - 1)
    out = {}
    for k, v in streams.items():
        data = v['data']
        pick = [data[i] for i in idx]
        if k == 'latlng':
            pick = [[round(a, 5), round(b, 5)] for a, b in pick]
        elif k in ('altitude', 'distance'):
            pick = [round(x, 1) for x in pick]
        elif k == 'velocity_smooth':
            pick = [round(x, 2) for x in pick]
        out[k] = dict(v, data=pick)
    return out


def process_ride(row, zones, zone_r, end_r, min_km, max_points, calories_fn):
    """-> (ride dict with FULL-resolution trimmed streams, reason or None)."""
    try:
        st = json.loads(row['streams'])
        ll = st['latlng']['data']
        tm = st['time']['data']
        dd = st['distance']['data']
    except Exception:
        return None, 'unreadable streams'
    n = min(len(ll), len(tm), len(dd))
    if n < 30:
        return None, 'too short'
    start_pt, end_pt = tuple(ll[0]), tuple(ll[n - 1])
    keep = [False] * n
    for i in range(n):                                   # EVERY point is tested (a GPS jump can put one point inside a zone while its neighbour is outside)
        p = ll[i]
        keep[i] = not (inside_any(p, zones, zone_r) or dist_m(p, start_pt) < end_r or dist_m(p, end_pt) < end_r)
    best, cur_start, run = (0, 0), None, None
    for i in range(n + 1):
        if i < n and keep[i]:
            if cur_start is None:
                cur_start = i
        elif cur_start is not None:
            if (i - cur_start) > (best[1] - best[0]):
                best = (cur_start, i)
            cur_start = None
    a, b = best
    if b - a < 20:
        return None, 'nothing left outside privacy zones'
    length = dd[b - 1] - dd[a]
    if length < min_km * 1000:
        return None, 'too short after trimming'
    sl = lambda arr: arr[a:b]
    streams = {'latlng': {'data': [list(p) for p in sl(ll)]}}
    t0, d0 = tm[a], dd[a]
    streams['time'] = {'data': [int(x - t0) for x in sl(tm[:n])]}
    streams['distance'] = {'data': [x - d0 for x in sl(dd[:n])]}
    for k in ('altitude', 'velocity_smooth', 'heartrate', 'cadence', 'watts', 'temp'):
        arr = (st.get(k) or {}).get('data')
        if arr and len(arr) >= n:
            streams[k] = {'data': sl(arr[:n])}
    t, d = streams['time']['data'], streams['distance']['data']
    elapsed = t[-1]
    vel = streams.get('velocity_smooth', {}).get('data')
    moving = 0
    for i in range(1, len(t)):
        dt = t[i] - t[i - 1]
        v = vel[i] if vel else (d[i] - d[i - 1]) / dt if dt > 0 else 0
        if v >= 0.8 and 0 < dt < 60:
            moving += dt
    moving = moving or elapsed
    if elapsed < 600 or moving < 600:
        return None, 'too brief'
    start_utc = parse_when(row['startDate'] or row['startDateLocal']) + timedelta(seconds=t0)
    alts = streams.get('altitude', {}).get('data')
    hr = streams.get('heartrate', {}).get('data')
    cad = streams.get('cadence', {}).get('data')
    speeds = [x for x in (vel or [(d[i] - d[i - 1]) / max(1, t[i] - t[i - 1]) for i in range(1, len(t))]) if x is not None]
    ride = {
        'start_utc': start_utc, 'distance': d[-1], 'elapsed': elapsed, 'moving': int(moving), 'gain': elevation_gain(alts) if alts else (row['totalElevationGain'] or 0) * (d[-1] / max(1, row['distance'] or d[-1])),
        'avg_speed': d[-1] / moving, 'max_speed': min(max(speeds) if speeds else 0, 25.0), 'avg_hr': round(sum(hr) / len(hr), 1) if hr else None, 'max_hr': max(hr) if hr else None,
        'avg_cad': round(sum(cad) / len(cad), 1) if cad else None, 'streams': streams, 'start_ll': tuple(streams['latlng']['data'][0]),
        'weather': {k: row[k] for k in ('weatherTempC', 'weatherWindKph', 'weatherGustKph', 'weatherWindDir', 'weatherHumidity', 'weatherRainMm', 'weatherCode', 'weatherSummary', 'weatherWindRel')},
    }
    ride['calories'] = calories_fn(ride['distance'], ride['moving'])
    return ride, None


# ------------------------------------------------------------------ fiction: food, body, recovery
# (name, kcal, protein, carbs, fat, fibre) per 100 g, typical serving g.  Plausible, not lab-exact: this is a made-up diary.
FOODS = {
    'Porridge oats': (370, 11, 60, 8, 9, 50), 'Semi-skimmed milk': (47, 3.5, 4.9, 1.7, 0, 150), 'Banana': (89, 1.1, 23, 0.3, 2.6, 110), 'Scrambled eggs (2 eggs)': (150, 10, 1.5, 11, 0, 130),
    'Wholemeal toast (2 slices)': (230, 9, 41, 3, 6, 70), 'Butter': (717, 0.6, 0.6, 81, 0, 10), 'Greek yoghurt': (97, 9, 4, 5, 0, 200), 'Granola': (450, 9, 62, 17, 7, 40),
    'Mixed berries': (45, 0.9, 10, 0.3, 4, 80), 'Bacon sandwich': (255, 14, 26, 11, 2, 190), 'Corn flakes': (375, 7, 84, 1, 3, 40), 'Peanut butter on toast': (290, 11, 30, 14, 5, 90),
    'Chicken salad wrap': (190, 13, 20, 6, 2, 230), 'Ham and cheese sandwich': (260, 14, 28, 10, 2, 190), 'Salt and vinegar crisps': (520, 6, 50, 33, 4, 30), 'Tomato soup': (45, 1, 7, 1.5, 1, 300),
    'Crusty bread roll': (270, 9, 52, 2, 3, 70), 'Jacket potato': (95, 2.5, 21, 0.2, 2.5, 280), 'Tuna and sweetcorn': (130, 15, 8, 4, 1, 120), 'Side salad': (20, 1, 3, 0.3, 1.5, 100),
    'Pasta (cooked)': (150, 5.5, 30, 1, 2, 220), 'Tomato and basil sauce': (50, 1.5, 8, 1.5, 1.5, 150), 'Chicken curry': (140, 11, 7, 7, 1.5, 300), 'Basmati rice (cooked)': (135, 3, 29, 0.4, 0.5, 200),
    'Spaghetti bolognese': (130, 7.5, 14, 4.5, 1.5, 400), 'Grilled salmon fillet': (205, 22, 0, 13, 0, 140), 'New potatoes': (75, 1.8, 16, 0.3, 1.8, 200), 'Steamed broccoli': (34, 2.8, 4, 0.4, 2.6, 120),
    'Chilli con carne': (115, 8, 11, 4.5, 3, 350), 'Margherita pizza (2 slices)': (250, 11, 30, 9, 2, 220), 'Fish and chips': (200, 9, 22, 8, 2, 450), 'Roast chicken dinner': (135, 12, 11, 5, 2, 500),
    'Egg fried noodles stir fry': (150, 6, 22, 4, 2, 380), 'Lentil dahl': (105, 6, 14, 3, 4, 300), 'Apple': (52, 0.3, 14, 0.2, 2.4, 150), 'Protein bar': (370, 33, 33, 12, 5, 55), 'Flapjack': (440, 6, 58, 21, 4, 70),
    'Latte': (45, 3, 4, 2, 0, 300), 'Chocolate bar': (520, 7, 58, 29, 2, 45), 'Mixed nuts': (610, 20, 12, 52, 7, 30), 'Rice cakes with peanut butter': (330, 12, 40, 13, 4, 60),
    'Post-ride recovery shake': (95, 16, 7, 1, 0, 400), 'Digestive biscuits (2)': (480, 7, 66, 21, 3, 30), 'Cheddar cheese': (410, 25, 0.1, 34, 0, 30), 'Energy gel': (260, 0, 64, 0, 0, 40),
    'Malt loaf slice': (285, 7, 60, 1.5, 5, 35), 'Hummus and carrot sticks': (120, 4, 9, 7, 4, 120), 'Cheese on toast': (310, 15, 27, 15, 3, 120), 'Beans on toast': (130, 6, 22, 2, 6, 280),
}
BREAKFASTS = [['Porridge oats', 'Semi-skimmed milk', 'Banana'], ['Scrambled eggs (2 eggs)', 'Wholemeal toast (2 slices)', 'Butter'], ['Greek yoghurt', 'Granola', 'Mixed berries'],
              ['Bacon sandwich', 'Latte'], ['Corn flakes', 'Semi-skimmed milk', 'Banana'], ['Peanut butter on toast', 'Banana']]
LUNCHES = [['Chicken salad wrap', 'Apple', 'Salt and vinegar crisps'], ['Ham and cheese sandwich', 'Salt and vinegar crisps', 'Apple'], ['Tomato soup', 'Crusty bread roll', 'Cheddar cheese'],
           ['Jacket potato', 'Tuna and sweetcorn', 'Side salad'], ['Pasta (cooked)', 'Tomato and basil sauce', 'Cheddar cheese'], ['Beans on toast', 'Apple'], ['Cheese on toast', 'Side salad']]
DINNERS = [['Chicken curry', 'Basmati rice (cooked)'], ['Spaghetti bolognese', 'Side salad'], ['Grilled salmon fillet', 'New potatoes', 'Steamed broccoli'], ['Chilli con carne', 'Basmati rice (cooked)'],
           ['Margherita pizza (2 slices)', 'Side salad'], ['Egg fried noodles stir fry'], ['Lentil dahl', 'Basmati rice (cooked)'], ['Roast chicken dinner']]
SNACKS = ['Protein bar', 'Flapjack', 'Latte', 'Chocolate bar', 'Mixed nuts', 'Rice cakes with peanut butter', 'Digestive biscuits (2)', 'Hummus and carrot sticks', 'Malt loaf slice', 'Banana', 'Apple']
RIDE_FUEL = ['Energy gel', 'Flapjack', 'Malt loaf slice', 'Banana', 'Protein bar']


def food_row(rng, name, scale=1.0):
    kcal, p, c, f, fi, serv = FOODS[name]
    g = serv * scale * rng.uniform(0.85, 1.15)
    k = g / 100.0
    return dict(foodName=name, calories=round(kcal * k), protein=round(p * k, 1), carbs=round(c * k, 1), fat=round(f * k, 1), fibre=round(fi * k, 1), servingG=round(g))


def make_nutrition(rng, db, rider_id, end_day, days, ride_by_day):
    target = 2350
    weight0, goal = 93.6, 84.0
    rows_food, rows_water, rows_weight, rows_garmin = [], [], [], []
    w = weight0
    for i in range(days, -1, -1):
        d = end_day - timedelta(days=i)
        ds, dow = d.isoformat(), d.weekday()
        ride_min = ride_by_day.get(ds, 0)
        progress = (days - i) / days
        w = weight0 - (weight0 - 86.4) * progress + rng.gauss(0, 0.25)
        skip = rng.random() < 0.05 and i > 0
        partial = rng.random() < 0.08
        if not skip:
            meals = [('breakfast', rng.choice(BREAKFASTS)), ('lunch', rng.choice(LUNCHES))]
            if not partial or i == 0:
                dinner = ['Fish and chips'] if dow == 4 and rng.random() < 0.6 else ['Roast chicken dinner'] if dow == 6 else rng.choice(DINNERS)
                meals.append(('dinner', dinner))
            for meal, names in meals:
                for nm in names:
                    rows_food.append(dict(logDate=ds, mealType=meal, source='manual', **food_row(rng, nm)))
            for nm in rng.sample(SNACKS, rng.choice([1, 2, 2, 3])):
                rows_food.append(dict(logDate=ds, mealType='snacks', source='manual', **food_row(rng, nm, 0.9)))
            if ride_min:
                for nm in rng.sample(RIDE_FUEL, 2 if ride_min > 90 else 1):
                    rows_food.append(dict(logDate=ds, mealType='snacks', source='manual', **food_row(rng, nm)))
                if ride_min > 60:
                    rows_food.append(dict(logDate=ds, mealType='snacks', source='manual', **food_row(rng, 'Post-ride recovery shake')))
            for _ in range(rng.randint(4, 8)):
                rows_water.append(dict(logDate=ds, ml=rng.choice([250, 330, 500, 500, 750])))
        if rng.random() < 0.86:
            rows_weight.append(dict(logDate=ds, weightKg=round(w, 1), source='scale'))
        steps = int(rng.gauss(8200, 2200)) + (2500 if ride_min else 0) + (3000 if dow >= 5 else 0)
        steps = max(1800, steps)
        resting = round(61 - 6 * progress + rng.gauss(0, 1.6) + (2 if ride_min > 150 else 0))
        sleep = max(4.8, min(9.2, rng.gauss(7.1, 0.8)))
        body = max(12, min(99, int(rng.gauss(72, 16) - (10 if ride_min > 120 else 0))))
        hrs, bbs = [], []
        base_ms = int(datetime(d.year, d.month, d.day, tzinfo=LONDON).timestamp() * 1000)
        for q in range(0, 96, 2):
            t = base_ms + q * 15 * 60000
            hour = q / 4
            hrs.append([t, int(resting + (8 if 7 <= hour <= 22 else 0) + rng.gauss(0, 4) + (35 if ride_min and 9 <= hour <= 12 else 0))])
            bbs.append([t, max(5, min(100, int(body + 20 - 1.6 * max(0, hour - 6) if hour > 6 else 30 + hour * 7)))])
        rows_garmin.append(dict(date=ds, restingHR=resting, hrv=round(rng.gauss(62, 9)), hrvBalanced=1, sleepHours=round(sleep, 1), sleepScore=int(max(35, min(97, sleep * 11 + rng.gauss(0, 7)))),
                                bodyBattery=body, steps=steps, stressScore=int(max(10, min(80, rng.gauss(32, 11)))), hrStream=json.dumps(hrs), bodyBatteryStream=json.dumps(bbs),
                                totalCalories=round(2300 + steps * 0.04 + ride_min * 9), activeCalories=round(steps * 0.04 + ride_min * 9)))
    for r in rows_food:
        db.execute('INSERT INTO FoodLog (riderId, logDate, foodName, calories, protein, carbs, fat, fibre, servingG, quantity, mealType, source) VALUES (?,?,?,?,?,?,?,?,?,1,?,?)',
                   [rider_id, r['logDate'], r['foodName'], r['calories'], r['protein'], r['carbs'], r['fat'], r['fibre'], r['servingG'], r['mealType'], r['source']])
    for r in rows_water:
        db.execute('INSERT INTO HydrationLog (riderId, logDate, ml) VALUES (?,?,?)', [rider_id, r['logDate'], r['ml']])
    for r in rows_weight:
        db.execute('INSERT INTO WeightLog (riderId, logDate, weightKg, source) VALUES (?,?,?,?)', [rider_id, r['logDate'], r['weightKg'], r['source']])
    for r in rows_garmin:
        db.execute('INSERT INTO GarminDaily (date, restingHR, hrv, hrvBalanced, sleepHours, sleepScore, bodyBattery, steps, stressScore, hrStream, bodyBatteryStream, totalCalories, activeCalories) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   [r[k] for k in ('date', 'restingHR', 'hrv', 'hrvBalanced', 'sleepHours', 'sleepScore', 'bodyBattery', 'steps', 'stressScore', 'hrStream', 'bodyBatteryStream', 'totalCalories', 'activeCalories')])
    for nm, meal in (('Porridge bowl', BREAKFASTS[0]), ('Post-ride feast', ['Chilli con carne', 'Basmati rice (cooked)', 'Post-ride recovery shake']), ('Lazy lunch', LUNCHES[1])):
        mid = db.execute('INSERT INTO SavedMeal (name) VALUES (?)', [nm]).lastrowid
        for item in meal:
            f = food_row(rng, item)
            db.execute('INSERT INTO SavedMealItem (mealId, foodName, calories, protein, carbs, fat, servingG, fibre) VALUES (?,?,?,?,?,?,?,?)', [mid, item, f['calories'], f['protein'], f['carbs'], f['fat'], f['servingG'], f['fibre']])
    for item in ('Protein bar', 'Flapjack', 'Greek yoghurt', 'Banana', 'Latte'):
        f = food_row(rng, item)
        db.execute('INSERT INTO FoodFavourite (riderId, foodName, calories, protein, carbs, fat, servingG) VALUES (?,?,?,?,?,?,?)', [rider_id, item, f['calories'], f['protein'], f['carbs'], f['fat'], f['servingG']])
    return len(rows_food), len(rows_weight)


def make_workouts(rng, db, rider_id, end_day, days):
    n = 0
    for i in range(days, 0, -1):
        if rng.random() < 0.13:
            d = end_day - timedelta(days=i)
            sport = rng.choice(['Walk', 'Walk', 'Walk', 'Hike', 'Run'])
            dist = rng.uniform(2500, 9000) if sport != 'Hike' else rng.uniform(9000, 17000)
            speed = {'Walk': 1.45, 'Hike': 1.2, 'Run': 2.9}[sport] * rng.uniform(0.9, 1.1)
            secs = int(dist / speed)
            start = datetime(d.year, d.month, d.day, rng.choice([8, 9, 12, 17, 18]), rng.randint(0, 59))
            utc = start.replace(tzinfo=LONDON).astimezone(timezone.utc)
            kcal = round(dist / 1000 * (62 if sport != 'Run' else 85) * 0.9 + rng.uniform(-20, 20))
            db.execute('INSERT INTO Workout (id, riderId, sport, name, startDate, startDateLocal, distance, movingTime, elapsedTime, totalElevationGain, averageSpeed, calories, steps, source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       [f'demo_wk_{n}', rider_id, sport, None, utc.strftime('%Y-%m-%dT%H:%M:%S'), start.strftime('%Y-%m-%dT%H:%M:%S'), dist, secs, secs + rng.randint(0, 300),
                        round(dist * rng.uniform(0.004, 0.02)), dist / secs, kcal, int(dist / 0.75) if sport != 'Run' else int(dist / 1.0), 'demo'])
            n += 1
    return n


# ------------------------------------------------------------------ gear (three bikes, parts, service log)
def bike_photo(path, base, accent):
    from PIL import Image, ImageDraw, ImageFilter
    W, H = 1400, 875
    im = Image.new('RGB', (W, H))
    d = ImageDraw.Draw(im)
    for y in range(H):
        t = y / H
        d.line([(0, y), (W, y)], fill=tuple(int(base[i] * (1 - t) + base[i] * 0.45 * t) for i in range(3)))
    d.ellipse([200, 690, 1200, 760], fill=(0, 0, 0))
    im = im.filter(ImageFilter.GaussianBlur(2))
    d = ImageDraw.Draw(im)

    def wheel(cx, cy, r):
        d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(235, 235, 235), width=14)
        d.ellipse([cx - r + 20, cy - r + 20, cx + r - 20, cy + r - 20], outline=(120, 120, 130), width=3)
        for k in range(16):
            ang = k * math.pi / 8
            d.line([(cx, cy), (cx + (r - 14) * math.cos(ang), cy + (r - 14) * math.sin(ang))], fill=(160, 160, 170), width=2)
        d.ellipse([cx - 14, cy - 14, cx + 14, cy + 14], fill=(200, 200, 205))
    wheel(430, 640, 235)
    wheel(980, 640, 235)
    bb, seat, head, w = (640, 640), (560, 330), (900, 340), 22
    d.line([(430, 640), bb, seat, (430, 640)], fill=accent, width=w, joint='curve')
    d.line([bb, head, seat], fill=accent, width=w, joint='curve')
    d.line([head, (980, 640)], fill=(60, 60, 70), width=w - 4)
    d.line([seat, (545, 255)], fill=(30, 30, 35), width=14)
    d.rounded_rectangle([490, 235, 640, 262], 12, fill=(25, 25, 28))
    d.line([head, (930, 270), (985, 285)], fill=(30, 30, 35), width=14)
    d.ellipse([580, 580, 700, 700], outline=(210, 210, 215), width=10)
    im.save(path, 'JPEG', quality=88)


def make_gear(db, db_path, rider_id, end_day, rides_first_day, rng):
    from services import gear
    MIU = gear.MI
    rb = gear.create_bike(db, rider_id, 'Roubaix', 'road', 'Specialized', 'Roubaix Comp', 2022, None, 2400 * MIU, 'The everyday road bike.')
    gv = gear.create_bike(db, rider_id, 'Gravel', 'gravel', 'Cube', 'Nuroad Pro', 2021, None, 900 * MIU, 'Canal paths and mud.')
    wt = gear.create_bike(db, rider_id, 'Winter hack', 'commuter', 'Trek', 'FX 3', 2018, None, 0, 'Mudguards, lights, no feelings.')
    gear.set_default(db, rider_id, rb)
    for bid, base, acc in ((rb, (22, 70, 105), (252, 76, 2)), (gv, (45, 85, 62), (250, 190, 40)), (wt, (60, 66, 90), (110, 170, 240))):
        tmp = tempfile.mktemp(suffix='.jpg')
        bike_photo(tmp, base, acc)
        gear.save_photo(db, bid, open(tmp, 'rb').read(), db_path)
        os.remove(tmp)
    rides = db.execute("SELECT id, substr(startDateLocal,1,10) d FROM Activity WHERE riderId=? ORDER BY startDateLocal", [rider_id]).fetchall()
    cut_old = (end_day - timedelta(days=365 * 5)).isoformat()
    for i, r in enumerate(rides):
        d = date.fromisoformat(r['d'])
        winter = d.month in (12, 1, 2)
        if r['d'] < cut_old or (winter and rng.random() < 0.7):
            b = wt
        elif d.weekday() == 6 and rng.random() < 0.55:
            b = gv
        else:
            b = rb
        db.execute('UPDATE Activity SET bikeId=? WHERE id=?', [b, r['id']])
    ago = lambda days: (end_day - timedelta(days=days)).isoformat()
    P = gear.add_part
    P(db, rb, 'chain', 'KMC X11 chain', ago(70), 0, 450 * MIU, 2500 * MIU)
    P(db, rb, 'cassette', 'Shimano 105 11-30', ago(520), 0, 5000 * MIU, 8000 * MIU)
    P(db, rb, 'chainrings', 'Shimano 105 50/34', ago(980), 1500 * MIU, 6000 * MIU, 12000 * MIU)
    P(db, rb, 'tyre_front', 'Continental GP5000 28mm', ago(140), 0, 1000 * MIU, 4000 * MIU)
    P(db, rb, 'tyre_rear', 'Continental GP5000 28mm', ago(140), 0, 1000 * MIU, 2500 * MIU)
    P(db, rb, 'sealant', 'Stans Race sealant', ago(140), 0, None, None, None, 150)
    P(db, rb, 'bar_tape', 'Supacaz Super Sticky', ago(610), 0, None, 4000 * MIU, None, 540)
    P(db, rb, 'brake_pads', 'Shimano L03A resin', ago(210), 0, 900 * MIU, 2500 * MIU)
    P(db, rb, 'headset', 'Specialized headset bearings', ago(980), 0, 3000 * MIU, 12000 * MIU)
    P(db, gv, 'chain', 'SRAM GX chain', ago(180), 0, 1000 * MIU, 2500 * MIU)
    P(db, gv, 'tyre_rear', 'Schwalbe G-One 40mm', ago(330), 0, 1000 * MIU, 2500 * MIU)
    P(db, gv, 'sealant', 'Orange Seal', ago(215), 0, None, None, None, 120)
    P(db, wt, 'chain', 'KMC Z8 chain', ago(230), 0, 1000 * MIU, 2500 * MIU)
    chain = db.execute("select id from Part where bikeId=? and kind='chain'", [rb]).fetchone()[0]
    gear.log_service(db, rb, 'cleaned', ago(8), None, None, 'Full strip and degrease')
    gear.log_service(db, rb, 'inspected', ago(55), chain, None, 'Chain checker: 0.25%')
    gear.log_service(db, gv, 'serviced', ago(80), None, 45, 'Hubs and headset re-greased')
    wchain = db.execute("select id from Part where bikeId=? and kind='chain'", [wt]).fetchone()[0]
    gear.log_service(db, wt, 'replaced', ago(230), wchain, 24.99, 'Worn at 0.75%', new_part={'name': 'KMC Z8 chain'})
    return rb, gv, wt


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--source', required=True, help='your real Headwind database (opened read-only)')
    ap.add_argument('--out', default=os.path.join(ROOT, 'demo', 'out'))
    ap.add_argument('--from-year', type=int, default=2013)
    ap.add_argument('--to-year', type=int, default=2100)
    ap.add_argument('--shift-until-year', type=int, default=2022, help='rides up to this year (your busy years) are moved forward by whole weeks so the newest of them is yesterday; later rides keep their real dates')
    ap.add_argument('--max-rides', type=int, default=5000)
    ap.add_argument('--redact', action='append', default=[], metavar='WORD', help='also remove this word from segment names (rider names from the source database are removed automatically)')
    ap.add_argument('--zone-radius', type=int, default=1500, help='metres cleared around every detected privacy zone')
    ap.add_argument('--endpoint-radius', type=int, default=1000, help='metres cleared around each ride\'s own start and end')
    ap.add_argument('--zone-min', type=int, default=6, help='a place used as start/end at least this many times is a privacy zone')
    ap.add_argument('--extra-zone', action='append', default=[], metavar='LAT,LNG', help='additional private place (never saved)')
    ap.add_argument('--allow-segment', action='append', type=int, default=[], metavar='ID', help='publish this segment even though it is inside a privacy zone (YOUR decision: it reveals that place)')
    ap.add_argument('--min-km', type=float, default=3.0)
    ap.add_argument('--points', type=int, default=600)
    ap.add_argument('--food-days', type=int, default=180)
    ap.add_argument('--seed', type=int, default=7)
    args = ap.parse_args()
    rng = random.Random(args.seed)
    os.makedirs(args.out, exist_ok=True)
    out_db = os.path.join(args.out, 'pristine.db')
    for f in (out_db, out_db + '-wal', out_db + '-shm'):
        if os.path.exists(f):
            os.remove(f)
    shutil.rmtree(os.path.join(args.out, 'bikeimg'), ignore_errors=True)

    src = sqlite3.connect(f'file:{args.source}?mode=ro', uri=True, timeout=60)
    src.row_factory = sqlite3.Row
    owner = src.execute('SELECT id FROM Rider WHERE isDefault=1 ORDER BY id LIMIT 1').fetchone()[0]
    rows = src.execute("SELECT * FROM Activity WHERE riderId=? AND streams IS NOT NULL AND length(streams)>200 AND sportType LIKE '%Ride%' "
                       "AND substr(startDateLocal,1,4) BETWEEN ? AND ? ORDER BY startDateLocal", [owner, str(args.from_year), str(args.to_year)]).fetchall()
    print(f'source rides in range: {len(rows)}')

    # 1) privacy zones from where rides start and finish
    ends = []
    for r in rows:
        try:
            ll = json.loads(r['streams'])['latlng']['data']
            ends += [tuple(ll[0]), tuple(ll[-1])]
        except Exception:
            pass
    zones = detect_zones(ends, args.zone_min)
    for z in args.extra_zone:
        la, lo = (float(x) for x in z.split(','))
        zones.append((la, lo, 0))
    print(f'privacy zones detected: {len(zones)} (each cleared to {args.zone_radius} m); ride endpoints cleared to {args.endpoint_radius} m')

    # 2) trim + recompute
    from services.parser import estimate_ride_calories
    cal = lambda dist, moving: estimate_ride_calories(dist, moving, 82.0)
    kept, dropped = [], Counter()
    for i, r in enumerate(rows):
        ride, why = process_ride(r, zones, args.zone_radius, args.endpoint_radius, args.min_km, args.points, cal)
        if ride:
            kept.append(ride)
        else:
            dropped[why] += 1
        if (i + 1) % 500 == 0:
            print(f'  processed {i + 1}/{len(rows)} (kept {len(kept)})')
    print(f'kept {len(kept)} rides; dropped: {dict(dropped)}')
    if len(kept) > args.max_rides:
        kept = sorted(rng.sample(kept, args.max_rides), key=lambda x: x['start_utc'])
        print(f'sampled down to {len(kept)}')

    # 3) shift the busy years forward by whole weeks (weekdays are preserved) so the demo looks alive; later rides keep their real dates. A ride that would then overlap
    #    another in time is dropped (two bikes at once looks silly).
    today = datetime.now(LONDON).date()
    old = [k for k in kept if k['start_utc'].astimezone(LONDON).year <= args.shift_until_year]
    newest = max(k['start_utc'] for k in old).astimezone(LONDON).date()
    shift = ((today - timedelta(days=1) - newest).days // 7) * 7
    print(f'timeline shift for rides up to {args.shift_until_year}: +{shift} days (their newest lands {(newest + timedelta(days=shift)).isoformat()})')
    for k in kept:
        k['shifted'] = timedelta(days=shift) if k['start_utc'].astimezone(LONDON).year <= args.shift_until_year else timedelta(0)
        k['start_final'] = k['start_utc'] + k['shifted']
    kept.sort(key=lambda k: k['start_final'])
    clean, last_end = [], None
    for k in kept:
        if last_end is not None and k['start_final'] < last_end:
            dropped['overlaps another ride after the shift'] += 1
            continue
        clean.append(k)
        last_end = k['start_final'] + timedelta(seconds=k['elapsed'])
    kept = clean
    print(f'rides after de-overlap: {len(kept)}')

    # 4) build the new database with the app's own schema code
    from flask import Flask
    import database
    from services import gear as gear_svc
    from services.best_efforts import save_best_efforts
    from services.segments import scan_activity_against_segments, _refresh_prs
    app = Flask(__name__)
    app.config['DATABASE'] = out_db
    with app.app_context():
        database.migrate_db()
        db = database.get_db()
        db.execute("INSERT INTO Rider (name, avatarPath, isDefault) VALUES ('Alex', 'custard_cream.svg', 1)")
        rider = db.execute('SELECT id FROM Rider').fetchone()[0]
        db.execute("""INSERT OR REPLACE INTO Settings (id, units, telemetryOptOut, nutritionCalGoal, nutritionProteinGoal, nutritionCarbGoal, nutritionFatGoal, nutritionWaterGoalMl,
                      nutritionWeightUnit, heightCm, sex, birthYear, stepGoal, goalWeightKg, rideGoalWeek, goalAuto) VALUES (1,'imperial',1,2400,160,260,75,2500,'kg',181,'male',1985,10000,84,200,0)""")
        # segments: only those clear of every privacy zone, judged on EVERY point of the line (segment lines are stored as JSON point lists)
        from services.segments import clean_polyline
        import re
        words = {w for r in src.execute('SELECT name FROM Rider').fetchall() for w in re.findall(r"[A-Za-z]{3,}", r['name'] or '')} | {w for x in args.redact for w in x.split()}
        redact = re.compile(r"\b(?:" + '|'.join(re.escape(w) for w in sorted(words, key=len, reverse=True)) + r")(?:'?s)?\b", re.I) if words else None
        clean_name = lambda n: (re.sub(r'\s{2,}', ' ', redact.sub('', n)).strip(" -:'") if redact else n).strip()
        segs = src.execute('SELECT * FROM Segment WHERE friendId IS NULL').fetchall()
        seg_keep, seg_drop, seg_report = [], 0, []
        for s in segs:
            line = clean_polyline(s['polyline']) if s['polyline'] else None
            pts = [(s['startLat'], s['startLng']), (s['endLat'], s['endLng'])] + [tuple(p) for p in (line or [])]
            if line is None or any(p[0] is None for p in pts):
                seg_drop += 1
                seg_report.append((s['name'], 'dropped: no usable line'))
                continue
            near = min(((dist_m(p, z), z[2]) for p in pts for z in zones), default=(1e9, 0))
            note = f'{clean_name(s["name"])[:30]:32} closest to a privacy zone: {int(round(near[0] / 100) * 100):>6} m (a place used {near[1]}x as start/finish)'
            if inside_any_line(pts, zones, args.zone_radius) and s['id'] not in args.allow_segment:
                seg_drop += 1
                seg_report.append((s['name'], 'DROPPED ' + note))
                continue
            seg_report.append((s['name'], ('KEPT (allowed by you) ' if s['id'] in args.allow_segment else 'kept ') + note))
            seg_keep.append(s)
        for s in seg_keep:
            db.execute('INSERT INTO Segment (id, name, startLat, startLng, endLat, endLng, distanceM, elevationGainM, sourceActivityId, polyline) VALUES (?,?,?,?,?,?,?,?,NULL,?)',
                       [s['id'], (lambda n: n[:1].upper() + n[1:])(clean_name(s['name'])), s['startLat'], s['startLng'], s['endLat'], s['endLng'], s['distanceM'], s['elevationGainM'], s['polyline']])
        segments = db.execute('SELECT * FROM Segment').fetchall()
        print(f'segments copied: {len(segments)} (left out for being near a privacy zone: {seg_drop})')

        ride_minutes = defaultdict(int)
        eff_total = 0
        for n, k in enumerate(kept):
            start_local = k['start_final'].astimezone(LONDON)
            start_naive_utc = k['start_final'].replace(tzinfo=None)
            aid = f'demo_{n:05d}'
            full = json.dumps(k['streams'])
            w = k['weather']
            db.execute('''INSERT INTO Activity (id, name, type, sportType, startDate, startDateLocal, distance, movingTime, elapsedTime, totalElevationGain, averageSpeed, maxSpeed,
                          averageHeartrate, maxHeartrate, averageCadence, calories, startLat, startLng, streams, rawData, riderId, weatherTempC, weatherWindKph, weatherGustKph,
                          weatherWindDir, weatherHumidity, weatherRainMm, weatherCode, weatherSummary, weatherWindRel, createdAt, updatedAt)
                          VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))''',
                       [aid, label(start_local), 'Ride', 'Ride', start_naive_utc.strftime('%Y-%m-%dT%H:%M:%S'), start_local.strftime('%Y-%m-%dT%H:%M:%S'), k['distance'], k['moving'], k['elapsed'],
                        k['gain'], k['avg_speed'], k['max_speed'], k['avg_hr'], k['max_hr'], k['avg_cad'], k['calories'], k['start_ll'][0], k['start_ll'][1], full, '{}', rider,
                        w['weatherTempC'], w['weatherWindKph'], w['weatherGustKph'], w['weatherWindDir'], w['weatherHumidity'], w['weatherRainMm'], w['weatherCode'], w['weatherSummary'], w['weatherWindRel']])
            row = {'id': aid, 'startDateLocal': start_local.strftime('%Y-%m-%dT%H:%M:%S'), 'streams': full}
            save_best_efforts(db, aid, row['startDateLocal'], full)                       # full resolution, before thinning
            # cheap bounding-box pre-check, then the app's own matcher on the full-resolution track
            la = [p[0] for p in k['streams']['latlng']['data']]
            lo = [p[1] for p in k['streams']['latlng']['data']]
            box = (min(la) - 0.001, max(la) + 0.001, min(lo) - 0.002, max(lo) + 0.002)
            cand = [s for s in segments if box[0] <= s['startLat'] <= box[1] and box[2] <= s['startLng'] <= box[3] and box[0] <= s['endLat'] <= box[1] and box[2] <= s['endLng'] <= box[3]]
            if cand:
                eff_total += scan_activity_against_segments(db, row, cand)
            db.execute('UPDATE Activity SET streams=? WHERE id=?', [json.dumps(thin(k['streams'], args.points)), aid])
            ride_minutes[start_local.date().isoformat()] += k['moving'] // 60
            if (n + 1) % 500 == 0:
                db.commit()
                print(f'  inserted {n + 1}/{len(kept)}')
        for s in segments:
            _refresh_prs(db, s['id'])
        db.commit()
        print(f'segment efforts found: {eff_total}')

        end_day = today - timedelta(days=1)
        nf, nw = make_nutrition(rng, db, rider, end_day, args.food_days, ride_minutes)
        nwk = make_workouts(rng, db, rider, end_day, args.food_days)
        make_gear(db, out_db, rider, end_day, None, rng)
        db.execute('CREATE TABLE IF NOT EXISTS DemoMeta (key TEXT PRIMARY KEY, value TEXT)')
        db.execute("INSERT OR REPLACE INTO DemoMeta (key, value) VALUES ('built_on', ?)", [today.isoformat()])
        db.commit()
        print(f'fiction: {nf} food entries, {nw} weigh-ins, {nwk} walks/runs/hikes, 3 bikes')
        gear_svc.evaluate_alerts(db, send=False)
        db.commit()
        db.execute('PRAGMA wal_checkpoint(TRUNCATE)')

    # 5) AUDIT: refuse to finish if anything private survived
    print('auditing...')
    chk = sqlite3.connect(out_db)
    chk.row_factory = sqlite3.Row
    src_ids = {r['id'] for r in rows}
    src_names = {r['name'] for r in rows if r['name']} - {'Morning Ride', 'Lunch Ride', 'Afternoon Ride', 'Evening Ride'}
    bad = []
    for r in chk.execute('SELECT id, name, streams, startLat, startLng, rawData, description, notes, city, aiKudos FROM Activity'):
        if r['id'] in src_ids:
            bad.append('original id survived')
        if r['name'] in src_names:
            bad.append('original name survived')
        if any(r[c] not in (None, '') for c in ('description', 'notes', 'city', 'aiKudos')) or r['rawData'] not in ('{}', None):
            bad.append('text metadata survived')
        for p in json.loads(r['streams'])['latlng']['data']:
            if inside_any(p, zones, args.zone_radius - 5):
                bad.append('a track point is inside a privacy zone')
                break
        if inside_any((r['startLat'], r['startLng']), zones, args.zone_radius - 5):
            bad.append('a ride start is inside a privacy zone')
    from services.segments import clean_polyline as _cp
    for s in chk.execute('SELECT * FROM Segment'):
        if s['id'] in args.allow_segment:
            continue
        if inside_any_line([(s['startLat'], s['startLng']), (s['endLat'], s['endLng'])] + [tuple(p) for p in (_cp(s['polyline']) or [])], zones, args.zone_radius - 5):
            bad.append('a segment line is inside a privacy zone')
    if chk.execute('SELECT COUNT(*) FROM Friend').fetchone()[0] or chk.execute('SELECT COUNT(*) FROM DeviceToken').fetchone()[0]:
        bad.append('friends/tokens present')
    st = chk.execute('SELECT * FROM Settings').fetchone()
    for col in ('garminEmail', 'garminPassword', 'mqttHost', 'mqttPassword', 'aiApiKey', 'haToken', 'ntfyUrl'):
        if col in st.keys() and st[col]:
            bad.append(f'secret in Settings.{col}')
    if bad:
        for b in sorted(set(bad)):
            print('AUDIT FAILED:', b)
        os.remove(out_db)
        sys.exit(2)
    counts = {t: chk.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0] for t in ('Activity', 'BestEffort', 'Segment', 'SegmentEffort', 'FoodLog', 'WeightLog', 'GarminDaily', 'Workout', 'Bike', 'Part')}
    size = os.path.getsize(out_db) / 1e6
    with open(os.path.join(args.out, 'REPORT.txt'), 'w') as f:
        f.write(f'built {datetime.now().isoformat(timespec="seconds")}\nprivacy zones: {len(zones)} (radius {args.zone_radius} m)\nrides kept {len(kept)}; dropped {dict(dropped)}\n'
                f'segments copied {len(segments)}, left out {seg_drop}\n{json.dumps(counts)}\ndatabase {size:.0f} MB\nAUDIT PASSED\n')
    print(f'AUDIT PASSED. {json.dumps(counts)}  ({size:.0f} MB) -> {out_db}')
    print('\nSEGMENTS (names and places become public: review):')
    for n, line in sorted(seg_report):
        print('  ' + line)


if __name__ == '__main__':
    main()
