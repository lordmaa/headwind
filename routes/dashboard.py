from calendar import month_name
from datetime import date, datetime, timedelta

from flask import Blueprint, redirect, render_template, request

from database import query_db

bp = Blueprint('dashboard', __name__)


def _weekly_data(rider_id):
    today  = date.today()
    monday = today - timedelta(days=today.weekday())
    weeks  = []
    for i in range(11, -1, -1):
        ws = monday - timedelta(weeks=i)
        we = ws + timedelta(days=7)
        row = query_db(
            "SELECT SUM(distance) as total, SUM(totalElevationGain) as elev, SUM(calories) as cals FROM Activity "
            "WHERE riderId=? AND date(startDateLocal) >= ? AND date(startDateLocal) < ?",
            [rider_id, ws.isoformat(), we.isoformat()], one=True,
        )
        total_mi = (row['total'] or 0) / 1609.344
        total_ft = (row['elev'] or 0) * 3.28084
        weeks.append({'label': ws.strftime('%-d %b'), 'mi': round(total_mi, 1), 'ft': round(total_ft), 'cals': round(row['cals'] or 0), 'current': i == 0})
    return weeks


def _group_activities(activities):
    today = date.today()
    tree = {}

    for a in activities:
        raw = str(a['startDateLocal'])[:19]
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError:
            continue
        y, m, d = dt.year, dt.month, dt.day
        tree.setdefault(y, {}).setdefault(m, {}).setdefault(d, []).append(a)

    result = []
    for year in sorted(tree, reverse=True):
        yr_rides = yr_dist = yr_elev = 0
        months = []
        for mo in sorted(tree[year], reverse=True):
            mo_rides = mo_dist = mo_elev = 0
            days = []
            for dy in sorted(tree[year][mo], reverse=True):
                acts = tree[year][mo][dy]
                d_dist = sum(a['distance'] or 0 for a in acts)
                d_elev = sum(a['totalElevationGain'] or 0 for a in acts)
                dt_day = datetime(year, mo, dy)
                days.append({
                    'label':      dt_day.strftime('%-d %b'),
                    'activities': acts,
                    'count':      len(acts),
                    'dist':       d_dist,
                    'elev':       d_elev,
                })
                mo_rides += len(acts)
                mo_dist  += d_dist
                mo_elev  += d_elev
            months.append({
                'name':       month_name[mo],
                'num':        mo,
                'days':       days,
                'count':      mo_rides,
                'dist':       mo_dist,
                'elev':       mo_elev,
                'is_current': year == today.year and mo == today.month,
            })
            yr_rides += mo_rides
            yr_dist  += mo_dist
            yr_elev  += mo_elev
        result.append({
            'year':       year,
            'months':     months,
            'count':      yr_rides,
            'dist':       yr_dist,
            'elev':       yr_elev,
            'is_current': year == today.year,
        })
    return result


@bp.route('/')
def index():
    return redirect('/dashboard')


@bp.route('/dashboard')
def dashboard():
    athlete = query_db('SELECT * FROM Athlete LIMIT 1', one=True)

    all_riders = query_db('SELECT * FROM Rider ORDER BY isDefault DESC, name')
    owner      = next((r for r in all_riders if r['isDefault']), all_riders[0] if all_riders else None)

    try:
        rid = int(request.args.get('rider') or owner['id'])
    except (TypeError, ValueError):
        rid = owner['id'] if owner else None

    active_rider = query_db('SELECT * FROM Rider WHERE id=?', [rid], one=True) or owner

    totals = query_db(
        'SELECT COUNT(*) as rides, SUM(distance) as dist, '
        'SUM(totalElevationGain) as elev, SUM(movingTime) as secs, '
        'SUM(calories) as cals FROM Activity WHERE riderId=?',
        [rid], one=True,
    )

    q         = request.args.get('q', '').strip()
    date_from = request.args.get('from', '').strip()
    date_to   = request.args.get('to', '').strip()
    min_mi    = request.args.get('min_mi', '').strip()
    max_mi    = request.args.get('max_mi', '').strip()
    sport     = request.args.get('sport', '').strip()
    is_filtered = any([q, date_from, date_to, min_mi, max_mi, sport])

    where  = ['a.riderId=?']
    params = [rid]
    if q:
        where.append('a.name LIKE ?');  params.append(f'%{q}%')
    if date_from:
        where.append('date(a.startDateLocal) >= ?'); params.append(date_from)
    if date_to:
        where.append('date(a.startDateLocal) <= ?'); params.append(date_to)
    if min_mi:
        try: where.append('a.distance >= ?'); params.append(float(min_mi) * 1609.344)
        except ValueError: pass
    if max_mi:
        try: where.append('a.distance <= ?'); params.append(float(max_mi) * 1609.344)
        except ValueError: pass
    if sport:
        where.append('lower(a.sportType) LIKE ?'); params.append(f'%{sport.lower()}%')

    activities = query_db(f'''
        SELECT a.id, a.name, a.sportType, a.startDateLocal, a.distance, a.movingTime,
               a.totalElevationGain, a.averageSpeed, a.averageWatts, a.averageHeartrate,
               a.city, a.country, a.aiKudos,
               r.name AS riderName, r.avatarPath AS riderAvatar, r.isDefault AS riderIsDefault,
               r.id AS riderId
        FROM Activity a
        LEFT JOIN Rider r ON r.id = a.riderId
        WHERE {' AND '.join(where)}
        ORDER BY a.startDateLocal DESC
    ''', params)

    year_speed_rows = query_db(
        "SELECT date(startDateLocal) as d, averageSpeed, name "
        "FROM Activity "
        "WHERE riderId=? AND strftime('%Y', startDateLocal) = strftime('%Y', 'now') "
        "AND averageSpeed > 0 ORDER BY startDateLocal",
        [rid],
    )
    year_speed = [
        {'d': r['d'], 'mph': round(float(r['averageSpeed']) * 2.23694, 1), 'name': r['name']}
        for r in year_speed_rows
    ]

    sport_types = query_db(
        'SELECT DISTINCT sportType FROM Activity WHERE riderId=? AND sportType IS NOT NULL ORDER BY sportType',
        [rid]
    )

    recovery = None
    if active_rider and active_rider['isDefault']:
        recovery = query_db(
            'SELECT date, restingHR, hrv, hrvBalanced, sleepHours, sleepScore, bodyBattery, steps, stressScore '
            'FROM GarminDaily ORDER BY date DESC LIMIT 1',
            one=True,
        )

    return render_template(
        'dashboard.html',
        athlete=athlete,
        totals=totals,
        grouped=_group_activities(activities) if not is_filtered else [],
        search_results=activities if is_filtered else [],
        is_filtered=is_filtered,
        weekly=_weekly_data(rid),
        year_speed=year_speed,
        now=date.today(),
        q=q, date_from=date_from, date_to=date_to,
        min_mi=min_mi, max_mi=max_mi, sport=sport,
        sport_types=sport_types,
        all_riders=all_riders,
        active_rider=active_rider,
        recovery=recovery,
    )
