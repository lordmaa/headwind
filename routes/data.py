from collections import defaultdict

from flask import Blueprint, jsonify, render_template, request

from database import get_db, query_db
from services.best_efforts import BRACKETS_MI, scan_all_best_efforts

bp = Blueprint('data', __name__)

MONTH_LABELS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

CLIMBING_BRACKETS_FT = [1000, 2000, 3000, 5000, 8000, 10000]


@bp.route('/data')
def index():
    all_riders   = query_db('SELECT * FROM Rider ORDER BY isDefault DESC, name')
    owner        = next((r for r in all_riders if r['isDefault']), all_riders[0] if all_riders else None)
    try:
        rid = int(request.args.get('rider') or owner['id'])
    except (TypeError, ValueError):
        rid = owner['id'] if owner else None
    active_rider = query_db('SELECT * FROM Rider WHERE id=?', [rid], one=True) or owner

    # ── Speed over time ──────────────────────────────────────────
    speed_rows = query_db(
        "SELECT date(startDateLocal) as d, averageSpeed "
        "FROM Activity WHERE riderId=? AND averageSpeed > 0 ORDER BY startDateLocal",
        [rid]
    )
    speed_labels  = [r['d'] for r in speed_rows]
    speed_values  = [round(float(r['averageSpeed']) * 2.23694, 1) for r in speed_rows]
    window = 20
    speed_rolling = []
    for i, v in enumerate(speed_values):
        chunk = speed_values[max(0, i - window + 1): i + 1]
        speed_rolling.append(round(sum(chunk) / len(chunk), 2))

    # ── Monthly distance ─────────────────────────────────────────
    monthly_rows = query_db(
        "SELECT strftime('%Y-%m', startDateLocal) as ym, SUM(distance) as dist "
        "FROM Activity WHERE riderId=? GROUP BY ym ORDER BY ym",
        [rid]
    )
    monthly_labels = [r['ym'] for r in monthly_rows]
    monthly_values = [round((r['dist'] or 0) / 1609.344, 1) for r in monthly_rows]

    # ── Year-on-year ─────────────────────────────────────────────
    yoy_rows = query_db(
        "SELECT strftime('%Y', startDateLocal) as yr, "
        "CAST(strftime('%m', startDateLocal) AS INTEGER) as mo, "
        "SUM(distance) as dist FROM Activity WHERE riderId=? GROUP BY yr, mo ORDER BY yr, mo",
        [rid]
    )
    year_data = defaultdict(lambda: [0] * 12)
    for r in yoy_rows:
        year_data[r['yr']][int(r['mo']) - 1] = round((r['dist'] or 0) / 1609.344, 1)
    year_on_year = {y: year_data[y] for y in sorted(year_data)}

    # ── Ride length histogram ─────────────────────────────────────
    hist_buckets = [
        ('0–5 mi',   0,      8047),
        ('5–10 mi',  8047,   16093),
        ('10–20 mi', 16093,  32187),
        ('20–30 mi', 32187,  48280),
        ('30–50 mi', 48280,  80467),
        ('50+ mi',   80467,  9_999_999),
    ]
    hist_labels, hist_values = [], []
    for label, lo, hi in hist_buckets:
        n = query_db(
            "SELECT COUNT(*) as n FROM Activity WHERE riderId=? AND distance >= ? AND distance < ?",
            [rid, lo, hi], one=True
        )['n']
        hist_labels.append(label)
        hist_values.append(n)

    # ── Day of week ───────────────────────────────────────────────
    dow_rows = query_db(
        "SELECT CAST(strftime('%w', startDateLocal) AS INTEGER) as dow, COUNT(*) as n "
        "FROM Activity WHERE riderId=? GROUP BY dow",
        [rid]
    )
    dow_map    = {r['dow']: r['n'] for r in dow_rows}
    dow_labels = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
    dow_values = [dow_map.get(i, 0) for i in range(7)]

    # ── Calories over time ────────────────────────────────────────
    cal_rows = query_db(
        "SELECT date(startDateLocal) as d, calories "
        "FROM Activity WHERE riderId=? AND calories > 0 ORDER BY startDateLocal",
        [rid]
    )
    cal_labels = [r['d'] for r in cal_rows]
    cal_values = [int(r['calories']) for r in cal_rows]
    cal_window = 20
    cal_rolling = []
    for i in range(len(cal_values)):
        chunk = cal_values[max(0, i - cal_window + 1): i + 1]
        cal_rolling.append(round(sum(chunk) / len(chunk), 1))

    # ── Heatmap (all dates with distance) ────────────────────────
    heatmap_rows = query_db(
        "SELECT date(startDateLocal) as d, SUM(distance) as dist "
        "FROM Activity WHERE riderId=? GROUP BY d",
        [rid]
    )
    heatmap = {r['d']: round((r['dist'] or 0) / 1609.344, 1) for r in heatmap_rows}

    # ── Best efforts / records ────────────────────────────────────
    effort_rows = query_db('''
        SELECT e.distanceMi, e.activityDate, e.elapsedSecs, e.avgSpeedMps,
               e.activityId, a.name as rideName
        FROM BestEffort e
        JOIN Activity a ON a.id = e.activityId
        WHERE a.riderId=?
        ORDER BY e.distanceMi, e.elapsedSecs ASC
    ''', [rid])

    records_by_year  = defaultdict(dict)
    records_by_month = defaultdict(dict)
    for e in effort_rows:
        yr  = str(e['activityDate'])[:4]
        ym  = str(e['activityDate'])[:7]
        d   = int(e['distanceMi'])
        rec = {
            'secs':   e['elapsedSecs'],
            'mph':    round(float(e['avgSpeedMps']) * 2.23694, 1) if e['avgSpeedMps'] else 0,
            'ride':   e['rideName'],
            'rideId': e['activityId'],
            'date':   e['activityDate'],
        }
        if d not in records_by_year[yr]:
            records_by_year[yr][d] = rec
        if d not in records_by_month[ym]:
            records_by_month[ym][d] = rec

    records_by_year  = {yr: {str(d): r for d, r in v.items()} for yr, v in records_by_year.items()}
    records_by_month = {ym: {str(d): r for d, r in v.items()} for ym, v in records_by_month.items()}
    has_efforts = bool(effort_rows)

    # ── Climbing records ──────────────────────────────────────────
    FEET_PER_M = 3.28084
    climbing_rows = query_db('''
        SELECT id, name, totalElevationGain, startDateLocal,
               strftime('%Y', startDateLocal) as yr,
               strftime('%Y-%m', startDateLocal) as ym
        FROM Activity
        WHERE riderId=? AND totalElevationGain > 0
        ORDER BY yr, totalElevationGain DESC
    ''', [rid])

    climbing_by_year  = defaultdict(lambda: defaultdict(lambda: None))
    climbing_by_month = defaultdict(lambda: defaultdict(lambda: None))
    for r in climbing_rows:
        ft = r['totalElevationGain'] * FEET_PER_M
        yr, ym = r['yr'], r['ym']
        for b in CLIMBING_BRACKETS_FT:
            if ft >= b:
                if climbing_by_year[yr][b] is None:
                    climbing_by_year[yr][b] = {
                        'ft':     round(ft),
                        'ride':   r['name'],
                        'rideId': r['id'],
                        'date':   str(r['startDateLocal'])[:10],
                    }
                if climbing_by_month[ym][b] is None:
                    climbing_by_month[ym][b] = {
                        'ft':     round(ft),
                        'ride':   r['name'],
                        'rideId': r['id'],
                        'date':   str(r['startDateLocal'])[:10],
                    }

    climbing_by_year  = {yr: {str(b): v for b, v in bmap.items() if v} for yr, bmap in climbing_by_year.items()}
    climbing_by_month = {ym: {str(b): v for b, v in bmap.items() if v} for ym, bmap in climbing_by_month.items()}

    # ── Weather performance ───────────────────────────────────────
    weather_scatter = query_db(
        '''SELECT id, averageSpeed, weatherTempC, weatherWindKph, weatherWindRel, weatherCode,
                  strftime('%m', startDateLocal) as mo
           FROM Activity
           WHERE riderId=? AND averageSpeed > 0
             AND weatherTempC IS NOT NULL AND weatherWindKph IS NOT NULL
           ORDER BY startDateLocal''',
        [rid]
    )

    wx_temp_x, wx_temp_y, wx_temp_season, wx_temp_ids = [], [], [], []
    wx_wind_x, wx_wind_y, wx_wind_rel, wx_wind_ids    = [], [], [], []
    wx_cond_buckets = {'Clear': [], 'Cloudy': [], 'Fog': [], 'Rain': [], 'Snow': [], 'Storm': []}

    def _wmo_bucket(code):
        if code is None: return None
        c = int(code)
        if c <= 1:  return 'Clear'
        if c <= 3:  return 'Cloudy'
        if c in (45, 48): return 'Fog'
        if c <= 82: return 'Rain'
        if c <= 77: return 'Snow'
        return 'Storm'

    def _season(mo):
        m = int(mo)
        if m in (12, 1, 2):  return 'Winter'
        if m in (3, 4, 5):   return 'Spring'
        if m in (6, 7, 8):   return 'Summer'
        return 'Autumn'

    for r in weather_scatter:
        mph = round(float(r['averageSpeed']) * 2.23694, 1)
        mo  = r['mo'] or '01'

        wx_temp_x.append(round(float(r['weatherTempC']), 1))
        wx_temp_y.append(mph)
        wx_temp_season.append(_season(mo))
        wx_temp_ids.append(r['id'])

        wx_wind_x.append(round(float(r['weatherWindKph']), 1))
        wx_wind_y.append(mph)
        wx_wind_rel.append(r['weatherWindRel'] or 'calm')
        wx_wind_ids.append(r['id'])

        bucket = _wmo_bucket(r['weatherCode'])
        if bucket:
            wx_cond_buckets[bucket].append(mph)

    wx_cond_labels = list(wx_cond_buckets.keys())
    wx_cond_avgs   = [round(sum(v) / len(v), 2) if v else 0 for v in wx_cond_buckets.values()]
    wx_cond_counts = [len(v) for v in wx_cond_buckets.values()]

    return render_template('data.html',
        speed_labels=speed_labels,
        speed_values=speed_values,
        speed_rolling=speed_rolling,
        monthly_labels=monthly_labels,
        monthly_values=monthly_values,
        year_on_year=year_on_year,
        month_labels=MONTH_LABELS,
        hist_labels=hist_labels,
        hist_values=hist_values,
        dow_labels=dow_labels,
        dow_values=dow_values,
        cal_labels=cal_labels,
        cal_values=cal_values,
        cal_rolling=cal_rolling,
        heatmap=heatmap,
        records_by_year=records_by_year,
        records_by_month=records_by_month,
        effort_brackets=BRACKETS_MI,
        has_efforts=has_efforts,
        climbing_by_year=climbing_by_year,
        climbing_by_month=climbing_by_month,
        climbing_brackets=CLIMBING_BRACKETS_FT,
        wx_temp_x=wx_temp_x,
        wx_temp_y=wx_temp_y,
        wx_temp_season=wx_temp_season,
        wx_temp_ids=wx_temp_ids,
        wx_wind_x=wx_wind_x,
        wx_wind_y=wx_wind_y,
        wx_wind_rel=wx_wind_rel,
        wx_wind_ids=wx_wind_ids,
        wx_cond_labels=wx_cond_labels,
        wx_cond_avgs=wx_cond_avgs,
        wx_cond_counts=wx_cond_counts,
        all_riders=all_riders,
        active_rider=active_rider,
    )


@bp.route('/data/scan-efforts', methods=['POST'])
def scan_efforts():
    db = get_db()
    n = scan_all_best_efforts(db)
    return jsonify({'ok': True, 'scanned': n})
