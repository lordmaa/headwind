import json

from flask import Blueprint, render_template, request
from database import query_db

bp = Blueprint('garmin_page', __name__)


@bp.route('/recovery')
def index():
    days = int(request.args.get('days', 60))

    rows = query_db('''
        SELECT date, restingHR, hrv, hrvBalanced, sleepHours, sleepScore, bodyBattery, steps, stressScore
        FROM GarminDaily
        WHERE date >= date('now', ? || ' days')
        ORDER BY date ASC
    ''', [f'-{days}'])

    # Dates that have a ride (for chart markers)
    ride_dates = {
        r['d'] for r in query_db('''
            SELECT DISTINCT date(startDateLocal) as d FROM Activity
            WHERE date(startDateLocal) >= date('now', ? || ' days')
        ''', [f'-{days}'])
    }

    # Build day-by-day series covering the full range (gaps → null)
    from datetime import date, timedelta
    start = date.today() - timedelta(days=days - 1)
    date_index = {r['date']: r for r in rows}

    labels, rhr, battery, sleep, steps, stress, has_ride = [], [], [], [], [], [], []
    for i in range(days):
        d = (start + timedelta(days=i)).isoformat()
        r = date_index.get(d)
        labels.append(d)
        rhr.append(r['restingHR'] if r else None)
        battery.append(r['bodyBattery'] if r else None)
        sleep.append(r['sleepHours'] if r else None)
        steps.append(r['steps'] if r else None)
        stress.append(r['stressScore'] if r else None)
        has_ride.append(1 if d in ride_dates else None)

    table_rows = [r for r in reversed(rows) if any([
        r['restingHR'], r['bodyBattery'], r['sleepHours'], r['sleepScore'], r['steps'], r['stressScore']
    ])]

    # Most recent day with intraday streams for the "today" charts
    intraday = query_db(
        "SELECT date, hrStream, bodyBatteryStream FROM GarminDaily "
        "WHERE hrStream IS NOT NULL OR bodyBatteryStream IS NOT NULL "
        "ORDER BY date DESC LIMIT 1",
        one=True
    )
    today_hr_stream = json.loads(intraday['hrStream']) if intraday and intraday['hrStream'] else []
    today_bb_stream = json.loads(intraday['bodyBatteryStream']) if intraday and intraday['bodyBatteryStream'] else []
    today_intraday_date = intraday['date'] if intraday else None

    # headline tiles: the latest value of each measure, the period average, and which way "better" is
    hrv_series = [date_index[d]['hrv'] if d in date_index else None for d in labels]
    sleep_score = [date_index[d]['sleepScore'] if d in date_index else None for d in labels]

    def tile(key, label, series, unit, better, digits=0, fmt=None):
        vals = [v for v in series if v]
        if not vals:
            return None
        latest = vals[-1]
        avg = sum(vals) / len(vals)
        diff = latest - avg
        good = None if abs(diff) < (0.02 * avg) else ((diff > 0) == (better == 'up'))
        r = lambda x: (round(x, digits) if digits else int(round(x)))
        return {'key': key, 'label': label, 'value': r(latest), 'unit': unit, 'avg': r(avg), 'diff': r(diff), 'good': good}

    tiles = [t for t in (
        tile('rhr', 'Resting HR', rhr, 'bpm', 'down'),
        tile('hrv', 'HRV', hrv_series, 'ms', 'up'),
        tile('sleep', 'Sleep', sleep, 'h', 'up', 1),
        tile('stress', 'Stress', stress, '', 'down'),
        tile('steps', 'Steps', steps, '', 'up'),
    ) if t]
    bb_vals = [v for v in battery if v]
    body_battery = bb_vals[-1] if bb_vals else None
    latest_day = next((d for d, v in zip(reversed(labels), reversed(battery)) if v), labels[-1] if labels else None)
    ride_days_in_period = sum(1 for v in has_ride if v)

    return render_template('garmin.html',
        tiles=tiles, body_battery=body_battery, latest_day=latest_day, ride_days=ride_days_in_period,
        labels=labels,
        rhr=rhr,
        battery=battery,
        sleep=sleep,
        steps=steps,
        stress=stress,
        has_ride=has_ride,
        table_rows=table_rows,
        days=days,
        today_hr_stream=today_hr_stream,
        today_bb_stream=today_bb_stream,
        today_intraday_date=today_intraday_date,
    )
