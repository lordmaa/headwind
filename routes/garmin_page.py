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

    return render_template('garmin.html',
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
