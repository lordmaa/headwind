import os

from flask import Blueprint, abort, redirect, render_template, request, url_for, current_app

from database import get_db, query_db
from services.best_efforts import BRACKETS_MI

bp = Blueprint('riders', __name__)

_ALLOWED = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'}


def _compute_badges(rid, totals):
    total_rides   = totals['rides'] or 0
    total_dist_m  = totals['dist']  or 0
    total_elev_m  = totals['elev']  or 0
    total_dist_mi = total_dist_m / 1609.344
    total_elev_ft = total_elev_m * 3.28084
    everests      = total_elev_m / 8849

    def _nth_ride(n):
        row = query_db(
            'SELECT id, startDateLocal FROM Activity WHERE riderId=? ORDER BY startDateLocal LIMIT 1 OFFSET ?',
            [rid, n - 1], one=True)
        return (row['startDateLocal'][:10], row['id']) if row else (None, None)

    def _dist_milestone(threshold_mi):
        row = query_db('''
            SELECT id, startDateLocal FROM (
                SELECT id, startDateLocal,
                       SUM(distance) OVER (ORDER BY startDateLocal) as running
                FROM Activity WHERE riderId=?
            ) WHERE running >= ? LIMIT 1
        ''', [rid, threshold_mi * 1609.344], one=True)
        return (row['startDateLocal'][:10], row['id']) if row else (None, None)

    def _elev_milestone(threshold_m):
        row = query_db('''
            SELECT id, startDateLocal FROM (
                SELECT id, startDateLocal,
                       SUM(totalElevationGain) OVER (ORDER BY startDateLocal) as running
                FROM Activity WHERE riderId=?
            ) WHERE running >= ? LIMIT 1
        ''', [rid, threshold_m], one=True)
        return (row['startDateLocal'][:10], row['id']) if row else (None, None)

    def _first_ride(condition_sql, params=None):
        row = query_db(
            f'SELECT id, startDateLocal FROM Activity WHERE riderId=? AND {condition_sql} ORDER BY startDateLocal LIMIT 1',
            [rid] + (params or []), one=True)
        return (row['startDateLocal'][:10], row['id']) if row else (None, None)

    # Segment stats
    seg_efforts_total = query_db('''
        SELECT COUNT(*) as n FROM SegmentEffort e
        JOIN Activity a ON a.id=e.activityId WHERE a.riderId=?
    ''', [rid], one=True)['n'] or 0

    seg_prs_held = query_db('''
        SELECT COUNT(*) as n FROM SegmentEffort e
        JOIN Activity a ON a.id=e.activityId WHERE a.riderId=? AND e.isPR=1
    ''', [rid], one=True)['n'] or 0

    total_segs_defined = query_db('SELECT COUNT(*) as n FROM Segment', one=True)['n'] or 0

    seg_max_repeats = query_db('''
        SELECT MAX(cnt) as m FROM (
            SELECT COUNT(*) as cnt FROM SegmentEffort e
            JOIN Activity a ON a.id=e.activityId
            WHERE a.riderId=? GROUP BY e.segmentId
        )
    ''', [rid], one=True)['m'] or 0

    seg_max_speed = query_db('''
        SELECT MAX(e.avgSpeedMps) as v FROM SegmentEffort e
        JOIN Activity a ON a.id=e.activityId WHERE a.riderId=?
    ''', [rid], one=True)['v'] or 0
    seg_max_speed_mph = seg_max_speed * 2.23694

    def _nth_effort(n):
        row = query_db('''
            SELECT a.id, a.startDateLocal FROM SegmentEffort e
            JOIN Activity a ON a.id=e.activityId
            WHERE a.riderId=? ORDER BY a.startDateLocal LIMIT 1 OFFSET ?
        ''', [rid, n - 1], one=True)
        return (row['startDateLocal'][:10], row['id']) if row else (None, None)

    def _first_effort_speed(threshold_mph):
        row = query_db('''
            SELECT a.id, a.startDateLocal FROM SegmentEffort e
            JOIN Activity a ON a.id=e.activityId
            WHERE a.riderId=? AND e.avgSpeedMps >= ?
            ORDER BY a.startDateLocal LIMIT 1
        ''', [rid, threshold_mph / 2.23694], one=True)
        return (row['startDateLocal'][:10], row['id']) if row else (None, None)

    def _count_cond(cond, params=None):
        return query_db(
            f'SELECT COUNT(*) as n FROM Activity WHERE riderId=? AND {cond}',
            [rid] + (params or []), one=True)['n'] or 0

    def _nth_cond(n, cond, params=None):
        row = query_db(
            f'SELECT id, startDateLocal FROM Activity WHERE riderId=? AND {cond} ORDER BY startDateLocal LIMIT 1 OFFSET ?',
            [rid] + (params or []) + [n - 1], one=True)
        return (row['startDateLocal'][:10], row['id']) if row else (None, None)

    def _wc(n, cond, params=None):
        cnt = _count_cond(cond, params)
        d, i = _nth_cond(n, cond, params) if cnt >= n else (None, None)
        return {'earned': cnt >= n, 'earned_date': d, 'ride_id': i}

    headwind_count = _count_cond("weatherWindRel='headwind'")

    def _hw(n):
        d, i = _nth_cond(n, "weatherWindRel='headwind'") if headwind_count >= n else (None, None)
        return {'earned': headwind_count >= n, 'earned_date': d, 'ride_id': i}

    def _first_repeat(n):
        row = query_db('''
            SELECT a.id, a.startDateLocal FROM SegmentEffort e
            JOIN Activity a ON a.id=e.activityId
            WHERE a.riderId=? AND e.segmentId IN (
                SELECT segmentId FROM SegmentEffort e2
                JOIN Activity a2 ON a2.id=e2.activityId
                WHERE a2.riderId=?
                GROUP BY e2.segmentId HAVING COUNT(*) >= ?
            )
            ORDER BY a.startDateLocal LIMIT 1 OFFSET ?
        ''', [rid, rid, n, n - 1], one=True)
        return (row['startDateLocal'][:10], row['id']) if row else (None, None)

    peak = query_db('''
        SELECT MAX(distance) as max_dist, MAX(totalElevationGain) as max_elev,
               MAX(averageSpeed) as max_speed, MAX(maxSpeed) as top_speed
        FROM Activity WHERE riderId=?
    ''', [rid], one=True)
    max_dist_mi   = (peak['max_dist']  or 0) / 1609.344
    max_elev_ft   = (peak['max_elev']  or 0) * 3.28084
    max_speed_mph = (peak['max_speed'] or 0) * 2.23694
    top_speed_mph = (peak['top_speed'] or 0) * 2.23694

    def _fts(mph):  # first ride hitting top speed threshold
        d, i = _first_ride('maxSpeed >= ?', [mph / 2.23694])
        return {'earned': top_speed_mph >= mph, 'earned_date': d, 'ride_id': i}

    wx = query_db('''
        SELECT MIN(weatherTempC) as min_temp FROM Activity
        WHERE riderId=? AND weatherTempC IS NOT NULL
    ''', [rid], one=True)
    min_temp = wx['min_temp'] if wx and wx['min_temp'] is not None else 999

    wx_coldest = query_db('''
        SELECT id, startDateLocal, weatherTempC FROM Activity
        WHERE riderId=? AND weatherTempC IS NOT NULL
        ORDER BY weatherTempC ASC LIMIT 1
    ''', [rid], one=True)
    wx_windiest = query_db('''
        SELECT id, startDateLocal, weatherWindKph, weatherGustKph FROM Activity
        WHERE riderId=? AND weatherWindKph IS NOT NULL
        ORDER BY COALESCE(weatherGustKph, weatherWindKph) DESC LIMIT 1
    ''', [rid], one=True)

    def _b(icon, name, desc, earned, earned_date=None, ride_id=None):
        return {'icon': icon, 'name': name, 'desc': desc,
                'earned': earned, 'earned_date': earned_date, 'ride_id': ride_id}

    def _rc(n):   # ride count helper
        d, i = _nth_ride(n) if total_rides >= n else (None, None)
        return {'earned': total_rides >= n, 'earned_date': d, 'ride_id': i}
    def _dm(mi):  # distance milestone helper
        d, i = _dist_milestone(mi) if total_dist_mi >= mi else (None, None)
        return {'earned': total_dist_mi >= mi, 'earned_date': d, 'ride_id': i}
    def _em(m):   # elevation milestone helper
        d, i = _elev_milestone(m) if everests >= m / 8849 else (None, None)
        return {'earned': everests >= m / 8849, 'earned_date': d, 'ride_id': i}
    def _fr(cond, params=None):  # first ride matching condition
        d, i = _first_ride(cond, params)
        return {'earned': bool(d), 'earned_date': d, 'ride_id': i}
    def _fe(n):   # nth segment effort helper
        d, i = _nth_effort(n) if seg_efforts_total >= n else (None, None)
        return {'earned': seg_efforts_total >= n, 'earned_date': d, 'ride_id': i}
    def _fs(mph): # first segment effort at speed
        d, i = _first_effort_speed(mph) if seg_max_speed_mph >= mph else (None, None)
        return {'earned': seg_max_speed_mph >= mph, 'earned_date': d, 'ride_id': i}
    def _rp(n):   # first repeat nth time on same segment
        d, i = _first_repeat(n) if seg_max_repeats >= n else (None, None)
        return {'earned': seg_max_repeats >= n, 'earned_date': d, 'ride_id': i}

    def _b(icon, name, desc, r):
        return {'icon': icon, 'name': name, 'desc': desc, **r}

    ride_count = [
        _b('🚴', 'First Pedal',     '1 activity',       _rc(1)),
        _b('🎯', 'Getting Started', '10 activities',    _rc(10)),
        _b('🔥', 'Regular Rider',   '50 activities',    _rc(50)),
        _b('💯', 'Centurion',       '100 activities',   _rc(100)),
        _b('⚡', 'Dedicated',       '250 activities',   _rc(250)),
        _b('🏆', 'Five Hundred',    '500 activities',   _rc(500)),
        _b('🌟', 'One Thousand',    '1,000 activities', _rc(1000)),
        _b('💫', 'Two Thousand',    '2,000 activities', _rc(2000)),
        _b('👑', 'Elite',           '3,000 activities', _rc(3000)),
        _b('🔮', 'Five Thousand',   '5,000 activities', _rc(5000)),
    ]
    distance = [
        _b('📍', 'Road Tripper',  '100 miles total',     _dm(100)),
        _b('🗺️',  'Explorer',     '500 miles total',     _dm(500)),
        _b('🌍', 'Globetrotter', '1,000 miles total',   _dm(1000)),
        _b('💫', 'Iron Legs',    '5,000 miles total',   _dm(5000)),
        _b('🌟', '10K Club',     '10,000 miles total',  _dm(10000)),
        _b('🚀', '25K Ultra',    '25,000 miles total',  _dm(25000)),
        _b('💎', '50K Legend',   '50,000 miles total',  _dm(50000)),
        _b('👑', '100K Elite',   '100,000 miles total', _dm(100000)),
    ]
    elevation = [
        _b('⛰️',  'Summit Seeker',   '1 Everest climbed',    _em(8849)),
        _b('🏔️',  'Peak Bagger',     '5 Everests climbed',   _em(44245)),
        _b('🌋', 'Altitude Junkie', '10 Everests climbed',  _em(88490)),
        _b('👑', 'Everest Master',  '25 Everests climbed',  _em(221225)),
        _b('💫', 'Cloud Walker',    '50 Everests climbed',  _em(442450)),
        _b('🌟', 'Stratosphere',    '100 Everests climbed', _em(884900)),
        _b('🚀', 'Beyond the Sky',  '150 Everests climbed', _em(1327350)),
        _b('🔮', 'Space Cowboy',    '200 Everests climbed', _em(1769800)),
    ]
    epic_rides = [
        # ── Single ride milestones ─────────────────────────────────
        _b('🌟', 'Quarter Century', '25 miles in one ride',  _fr('distance >= ?', [25  * 1609.344])),
        _b('💎', 'Half Century',    '50 miles in one ride',  _fr('distance >= ?', [50  * 1609.344])),
        _b('🔥', '75 Miler',        '75 miles in one ride',  _fr('distance >= ?', [75  * 1609.344])),
        _b('👑', 'Century Club',    '100 miles in one ride', _fr('distance >= ?', [100 * 1609.344])),
        # ── 25mi ride count ────────────────────────────────────────
        _b('🎯', '5 × 25mi',   '5 rides of 25+ miles',   _wc(5,   'distance >= ?', [25 * 1609.344])),
        _b('📍', '10 × 25mi',  '10 rides of 25+ miles',  _wc(10,  'distance >= ?', [25 * 1609.344])),
        _b('🗺️',  '25 × 25mi',  '25 rides of 25+ miles',  _wc(25,  'distance >= ?', [25 * 1609.344])),
        _b('💫', '50 × 25mi',  '50 rides of 25+ miles',  _wc(50,  'distance >= ?', [25 * 1609.344])),
        _b('🌟', '100 × 25mi', '100 rides of 25+ miles', _wc(100, 'distance >= ?', [25 * 1609.344])),
        # ── 50mi ride count ────────────────────────────────────────
        _b('⭐', '5 × 50mi',   '5 rides of 50+ miles',   _wc(5,  'distance >= ?', [50 * 1609.344])),
        _b('🔥', '10 × 50mi',  '10 rides of 50+ miles',  _wc(10, 'distance >= ?', [50 * 1609.344])),
        _b('💪', '25 × 50mi',  '25 rides of 50+ miles',  _wc(25, 'distance >= ?', [50 * 1609.344])),
        _b('👑', '50 × 50mi',  '50 rides of 50+ miles',  _wc(50, 'distance >= ?', [50 * 1609.344])),
        # ── Century count ──────────────────────────────────────────
        _b('🏆', '3 × Century',  '3 rides of 100+ miles',  _wc(3,  'distance >= ?', [100 * 1609.344])),
        _b('💎', '10 × Century', '10 rides of 100+ miles', _wc(10, 'distance >= ?', [100 * 1609.344])),
    ]
    speed = [
        _b('🐢', 'Rolling',        '8 mph avg on a ride',  _fr('averageSpeed >= ?', [8  / 2.23694])),
        _b('🚲', 'Cruising',       '10 mph avg on a ride', _fr('averageSpeed >= ?', [10 / 2.23694])),
        _b('🏃', 'Getting There',  '12 mph avg on a ride', _fr('averageSpeed >= ?', [12 / 2.23694])),
        _b('💨', 'Building Speed', '14 mph avg on a ride', _fr('averageSpeed >= ?', [14 / 2.23694])),
        _b('⚡', 'Respectable',    '16 mph avg on a ride', _fr('averageSpeed >= ?', [16 / 2.23694])),
        _b('🔥', 'Quick',          '18 mph avg on a ride', _fr('averageSpeed >= ?', [18 / 2.23694])),
        _b('🚀', 'Speed Merchant', '20 mph avg on a ride', _fr('averageSpeed >= ?', [20 / 2.23694])),
        _b('💫', 'Rocket',         '22 mph avg on a ride', _fr('averageSpeed >= ?', [22 / 2.23694])),
        _b('🌟', 'Flying',         '24 mph avg on a ride', _fr('averageSpeed >= ?', [24 / 2.23694])),
        _b('👑', 'Supersonic',     '25 mph avg on a ride', _fr('averageSpeed >= ?', [25 / 2.23694])),
        _b('💨', 'Quick Descent',  '30 mph top speed',     _fts(30)),
        _b('⚡', 'Bullet',         '40 mph top speed',     _fts(40)),
        _b('🚀', 'Missile',        '50 mph top speed',     _fts(50)),
    ]
    climbing = [
        # ── Big elevation days ────────────────────────────────────
        _b('🏔️', 'Hills Exist',   'Ride with 500ft+ elevation',    _fr('totalElevationGain >= ?', [500  / 3.28084])),
        _b('⛰️',  'Proper Climb',  'Ride with 1,000ft+ elevation',  _fr('totalElevationGain >= ?', [1000 / 3.28084])),
        _b('🌋', 'Mountain Day', 'Ride with 2,000ft+ elevation',  _fr('totalElevationGain >= ?', [2000 / 3.28084])),
        _b('💪', 'Epic Climb',   'Ride with 3,000ft+ elevation',  _fr('totalElevationGain >= ?', [3000 / 3.28084])),
        _b('🏆', 'Alpine',       'Ride with 5,000ft+ elevation',  _fr('totalElevationGain >= ?', [5000 / 3.28084])),
        _b('👑', 'Gran Fondo',   'Ride with 7,500ft+ elevation',  _fr('totalElevationGain >= ?', [7500 / 3.28084])),
        _b('🌟', 'Everest Day',  'Ride with 10,000ft+ elevation', _fr('totalElevationGain >= ?', [10000 / 3.28084])),
        # ── Hilly regulars ────────────────────────────────────────
        _b('🎯', 'Hill Regular', '10 rides with 1,000ft+ elevation',  _wc(10,  'totalElevationGain >= ?', [1000 / 3.28084])),
        _b('💦', 'Hill Addict',  '100 rides with 1,000ft+ elevation', _wc(100, 'totalElevationGain >= ?', [1000 / 3.28084])),
        _b('🦵', 'King of Hills', '500 rides with 1,000ft+ elevation', _wc(500, 'totalElevationGain >= ?', [1000 / 3.28084])),
        # ── Speed on hilly rides (≥2% avg gradient, ≥5mi) ────────
        _b('🐢', 'Up the Hill',      '8mph avg on a hilly ride (≥2% gradient)',  _fr('averageSpeed >= ? AND distance >= 8047 AND CAST(totalElevationGain AS FLOAT)/distance*100 >= 2', [8  / 2.23694])),
        _b('🚵', 'Grinder',          '10mph avg on a hilly ride (≥2% gradient)', _fr('averageSpeed >= ? AND distance >= 8047 AND CAST(totalElevationGain AS FLOAT)/distance*100 >= 2', [10 / 2.23694])),
        _b('💪', 'Hill Chaser',      '12mph avg on a hilly ride (≥2% gradient)', _fr('averageSpeed >= ? AND distance >= 8047 AND CAST(totalElevationGain AS FLOAT)/distance*100 >= 2', [12 / 2.23694])),
        _b('🔥', 'Gradient Burner',  '15mph avg on a hilly ride (≥2% gradient)', _fr('averageSpeed >= ? AND distance >= 8047 AND CAST(totalElevationGain AS FLOAT)/distance*100 >= 2', [15 / 2.23694])),
        _b('⚡', 'Gradient King',    '18mph avg on a hilly ride (≥2% gradient)', _fr('averageSpeed >= ? AND distance >= 8047 AND CAST(totalElevationGain AS FLOAT)/distance*100 >= 2', [18 / 2.23694])),
    ]
    weather = [
        # ── Winter ───────────────────────────────────────────────
        _b('❄️',  'Ice Breaker',    'Ride when temp ≤ 5°C',        _fr('weatherTempC <= 5')),
        _b('🧊', 'Cold Shoulder',  '10 rides when temp ≤ 5°C',    _wc(10,   'weatherTempC <= 5')),
        _b('🌨️',  'Winter Warrior', '50 rides when temp ≤ 5°C',    _wc(50,   'weatherTempC <= 5')),
        _b('🏔️',  'Frozen',         '100 rides when temp ≤ 5°C',   _wc(100,  'weatherTempC <= 5')),
        _b('👑', 'Polar Explorer', '500 rides when temp ≤ 5°C',   _wc(500,  'weatherTempC <= 5')),
        _b('🌟', 'Arctic',         '1,000 rides when temp ≤ 5°C', _wc(1000, 'weatherTempC <= 5')),
        _b('🥶', 'Freeze Warrior', 'Ride when temp ≤ 0°C',        _fr('weatherTempC <= 0')),
        _b('🧊', 'Sub-Zero Club',  '10 rides when temp ≤ 0°C',    _wc(10,  'weatherTempC <= 0')),
        _b('❄️',  'Ice Machine',    '50 rides when temp ≤ 0°C',    _wc(50,  'weatherTempC <= 0')),
        _b('💀', 'Hypothermia?',   '100 rides when temp ≤ 0°C',   _wc(100, 'weatherTempC <= 0')),
        _b('🌨️',  'Deep Freeze',    'Ride when temp ≤ −5°C',       _fr('weatherTempC <= -5')),
        _b('☃️',  'Record Low',
           f'Your coldest ride: {wx_coldest["weatherTempC"]:.1f}°C' if wx_coldest else 'No temp data yet',
           {'earned': bool(wx_coldest),
            'earned_date': wx_coldest['startDateLocal'][:10] if wx_coldest else None,
            'ride_id': wx_coldest['id'] if wx_coldest else None}),
        # ── Summer ───────────────────────────────────────────────
        _b('☀️',  'Sun Seeker',     'Ride when temp ≥ 25°C',       _fr('weatherTempC >= 25')),
        _b('😎', 'Summer Cyclist', '5 rides when temp ≥ 25°C',    _wc(5,  'weatherTempC >= 25')),
        _b('🌞', 'Heat Seeker',    '10 rides when temp ≥ 25°C',    _wc(10,  'weatherTempC >= 25')),
        _b('😎', 'Sun Addict',     '50 rides when temp ≥ 25°C',   _wc(50,  'weatherTempC >= 25')),
        _b('☀️',  'Heatwave Hero',  '100 rides when temp ≥ 25°C',  _wc(100, 'weatherTempC >= 25')),
        _b('🔥', 'Heatwave',       'Ride when temp ≥ 30°C',       _fr('weatherTempC >= 30')),
        _b('🌡️',  'Mad Dog',        'Ride when temp ≥ 35°C',       _fr('weatherTempC >= 35')),
        # ── Rain ─────────────────────────────────────────────────
        _b('🌧️',  'First Drops',    'Ride in the rain',            _fr('weatherCode BETWEEN 51 AND 82')),
        _b('☔', 'Rain Regular',   '10 rides in rain',            _wc(10,  'weatherCode BETWEEN 51 AND 82')),
        _b('🌊', 'Soggy Cyclist',  '50 rides in rain',            _wc(50,  'weatherCode BETWEEN 51 AND 82')),
        _b('💦', 'Waterproof',     '100 rides in rain',           _wc(100, 'weatherCode BETWEEN 51 AND 82')),
        _b('🌧️',  'British Summer', '500 rides in rain',           _wc(500, 'weatherCode BETWEEN 51 AND 82')),
        # ── Storm ────────────────────────────────────────────────
        _b('⛈️',  'Storm Chaser',   'Ride during a thunderstorm',  _fr('weatherCode >= 95')),
        _b('🌩️',  'Lightning Rod',  '5 rides in a storm',          _wc(5,  'weatherCode >= 95')),
        _b('🌪️',  'Storm Season',   '10 rides in a storm',         _wc(10, 'weatherCode >= 95')),
        # ── Wind ─────────────────────────────────────────────────
        _b('🌬️',  'Headwind Hero',  'Ride into a 30+ kph headwind', _fr("weatherWindRel='headwind' AND weatherWindKph >= 30")),
        _b('💪', 'Into the Wind',  '10 headwind rides',            _hw(10)),
        _b('😤', 'Wind Warrior',   '50 headwind rides',            _hw(50)),
        _b('⚡', 'Gale Force',     '100 headwind rides',           _hw(100)),
        _b('👑', 'Against the Elements', '1,000 headwind rides',   _hw(1000)),
        _b('💨',  'Record Gale',
           f'Your windiest ride: {round(wx_windiest["weatherGustKph"] or wx_windiest["weatherWindKph"])} kph' if wx_windiest else 'No wind data yet',
           {'earned': bool(wx_windiest),
            'earned_date': wx_windiest['startDateLocal'][:10] if wx_windiest else None,
            'ride_id': wx_windiest['id'] if wx_windiest else None}),
    ]
    segments = [
        _b('📐', 'First Effort',      '1 segment effort',    _fe(1)),
        _b('🔄', '10 Efforts',        '10 segment efforts',  _fe(10)),
        _b('💪', '50 Efforts',        '50 segment efforts',  _fe(50)),
        _b('💯', '100 Efforts',       '100 segment efforts', _fe(100)),
        _b('⚡', '250 Efforts',       '250 segment efforts', _fe(250)),
        _b('🏆', '500 Efforts',       '500 segment efforts', _fe(500)),
        _b('🥇', 'First PR',          'Set your first segment PR',
           {'earned': seg_prs_held >= 1, 'earned_date': None, 'ride_id': None}),
        _b('👑', 'King of the Road',  f'Hold every segment PR ({total_segs_defined} segments)',
           {'earned': total_segs_defined > 0 and seg_prs_held >= total_segs_defined, 'earned_date': None, 'ride_id': None}),
        _b('🔁', 'Creature of Habit', 'Ride the same segment 10+ times', _rp(10)),
        _b('😤', 'Obsessed',          'Ride the same segment 25+ times', _rp(25)),
        _b('🏠', 'Local Legend',      'Ride the same segment 50+ times', _rp(50)),
        _b('💨', 'Segment Flyer',     '20 mph avg on a segment', _fs(20)),
        _b('🚀', 'Segment Rocket',    '25 mph avg on a segment', _fs(25)),
        _b('💫', 'Segment Missile',   '30 mph avg on a segment', _fs(30)),
        _b('👑', 'Flying Machine',    '35 mph avg on a segment', _fs(35)),
    ]

    return [
        {'name': 'Ride Count',   'badges': ride_count},
        {'name': 'Distance',     'badges': distance},
        {'name': 'Elevation',    'badges': elevation},
        {'name': 'Epic Rides',   'badges': epic_rides},
        {'name': 'Speed',        'badges': speed},
        {'name': 'Climbing',     'badges': climbing},
        {'name': 'Weather',      'badges': weather},
        {'name': 'Segments',     'badges': segments},
    ]


def _avatar_dir():
    return os.path.join(current_app.root_path, 'static', 'avatars')


@bp.route('/riders')
def index():
    riders = query_db('''
        SELECT r.*,
               COUNT(a.id) AS ride_count,
               SUM(a.distance) AS total_dist
        FROM Rider r
        LEFT JOIN Activity a ON a.riderId = r.id
        GROUP BY r.id
        ORDER BY r.isDefault DESC, r.name ASC
    ''')
    return render_template('riders.html', riders=riders)


@bp.route('/riders/<int:rid>')
def detail(rid):
    rider = query_db('SELECT * FROM Rider WHERE id=?', [rid], one=True)
    if not rider:
        abort(404)

    totals = query_db(
        'SELECT COUNT(*) as rides, SUM(distance) as dist, '
        'SUM(totalElevationGain) as elev, SUM(movingTime) as secs, '
        'SUM(calories) as cals FROM Activity WHERE riderId=?',
        [rid], one=True,
    )

    recent = query_db('''
        SELECT id, name, sportType, startDateLocal, distance, movingTime,
               totalElevationGain, averageSpeed
        FROM Activity WHERE riderId=? ORDER BY startDateLocal DESC LIMIT 20
    ''', [rid])

    seg_prs = query_db('''
        SELECT s.name AS seg_name, s.id AS seg_id,
               e.elapsedSecs, e.activityDate, e.avgSpeedMps
        FROM SegmentEffort e
        JOIN Activity a ON a.id = e.activityId
        JOIN Segment s ON s.id = e.segmentId
        WHERE a.riderId=? AND e.isPR=1
        ORDER BY s.name
    ''', [rid])

    effort_rows = query_db('''
        SELECT e.distanceMi, e.elapsedSecs, e.avgSpeedMps, e.activityDate,
               a.name AS rideName, e.activityId
        FROM BestEffort e
        JOIN Activity a ON a.id = e.activityId
        WHERE a.riderId=?
        ORDER BY e.distanceMi, e.elapsedSecs ASC
    ''', [rid])

    best_by_dist = {}
    for e in effort_rows:
        d = int(e['distanceMi'])
        if d not in best_by_dist:
            best_by_dist[d] = e

    badges = _compute_badges(rid, totals)

    return render_template('rider.html', rider=rider, totals=totals,
                           recent=recent, seg_prs=seg_prs,
                           best_by_dist=best_by_dist, brackets=BRACKETS_MI,
                           badges=badges)


@bp.route('/riders/<int:rid>/avatar', methods=['POST'])
def upload_avatar(rid):
    import logging
    log = logging.getLogger(__name__)

    rider = query_db('SELECT * FROM Rider WHERE id=?', [rid], one=True)
    if not rider:
        abort(404)

    f = request.files.get('avatar')
    log.warning('Avatar upload rid=%s filename=%r size=%s',
                rid, f.filename if f else None,
                f.content_length if f else None)

    if not f or not f.filename:
        log.warning('Avatar upload aborted: no file or empty filename')
        return redirect(url_for('riders.detail', rid=rid))

    data = f.read()
    if not data:
        log.warning('Avatar upload aborted: file is empty filename=%r mime=%r', f.filename, f.mimetype)
        return redirect(url_for('riders.detail', rid=rid))

    # Detect type from magic bytes — works regardless of extension or MIME type
    if data[:8] == b'\x89PNG\r\n\x1a\n':
        ext = 'png'
    elif data[:3] == b'\xff\xd8\xff':
        ext = 'jpg'
    elif data[:6] in (b'GIF87a', b'GIF89a'):
        ext = 'gif'
    elif data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        ext = 'webp'
    elif b'<svg' in data[:256] or b'<?xml' in data[:64]:
        ext = 'svg'
    else:
        # Fall back to filename extension, then MIME type
        ext = f.filename.rsplit('.', 1)[-1].lower() if '.' in f.filename else ''
        if ext not in _ALLOWED:
            _mime_map = {'image/jpeg': 'jpg', 'image/jpg': 'jpg', 'image/png': 'png',
                         'image/gif': 'gif', 'image/webp': 'webp'}
            ext = _mime_map.get(f.mimetype or '', 'jpg')

    avatar_dir = _avatar_dir()
    os.makedirs(avatar_dir, exist_ok=True)

    # Resize to max 256x256 and save as JPEG regardless of source format
    try:
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(data)).convert('RGB')
        img.thumbnail((256, 256), Image.LANCZOS)
        filename = f'rider_{rid}.jpg'
        save_path = os.path.join(avatar_dir, filename)
        img.save(save_path, 'JPEG', quality=88)
    except Exception as e:
        log.warning('PIL resize failed (%s), saving raw', e)
        filename = f'rider_{rid}.{ext}'
        save_path = os.path.join(avatar_dir, filename)
        with open(save_path, 'wb') as fh:
            fh.write(data)

    log.warning('Avatar saved to %s', save_path)

    db = get_db()
    db.execute('UPDATE Rider SET avatarPath=? WHERE id=?', [filename, rid])
    db.commit()
    log.warning('Avatar DB updated: rid=%s avatarPath=%s', rid, filename)
    return redirect(url_for('riders.detail', rid=rid))


@bp.route('/riders/<int:rid>/edit', methods=['POST'])
def edit(rid):
    name      = (request.form.get('name')     or '').strip()
    ha_device = (request.form.get('haDevice') or '').strip()
    db = get_db()
    if name:
        db.execute('UPDATE Rider SET name=?, haDevice=? WHERE id=?', [name, ha_device or None, rid])
    else:
        db.execute('UPDATE Rider SET haDevice=? WHERE id=?', [ha_device or None, rid])
    db.commit()
    return redirect(url_for('riders.detail', rid=rid))


@bp.route('/riders/create', methods=['POST'])
def create():
    name = (request.form.get('name') or '').strip() or 'New Rider'
    db = get_db()
    db.execute('INSERT INTO Rider (name, isDefault) VALUES (?, 0)', [name])
    db.commit()
    new_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]
    return redirect(url_for('riders.detail', rid=new_id))
