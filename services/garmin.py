import json
from datetime import date, timedelta
from pathlib import Path

TOKEN_DIR = Path(__file__).parent.parent / '.garmin_tokens'


def _client(email, password):
    from garminconnect import Garmin

    TOKEN_DIR.mkdir(exist_ok=True)
    # login() loads tokens from tokenstore if present; saves them after password login
    api = Garmin(email=email, password=password)
    api.login(tokenstore=str(TOKEN_DIR))
    return api


def _parse_rhr(stats):
    try:
        return int(stats.get('restingHeartRate') or 0) or None
    except Exception:
        return None


def _parse_hrv(hrv_data):
    try:
        summary = hrv_data.get('hrvSummary') or {}
        last_night = summary.get('lastNight')
        status     = summary.get('status') or ''
        balanced   = 1 if 'BALANCED' in status.upper() else 0
        return (int(last_night) if last_night else None, balanced)
    except Exception:
        return (None, 0)


def _parse_sleep(sleep_data):
    try:
        dto   = sleep_data.get('dailySleepDTO') or {}
        secs  = dto.get('sleepTimeSeconds') or 0
        hours = round(secs / 3600, 1) if secs else None
        score_obj = (dto.get('sleepScores') or {}).get('overall') or {}
        score = score_obj.get('value')
        return (hours, int(score) if score is not None else None)
    except Exception:
        return (None, None)


def _parse_body_battery(bb_data):
    try:
        if not bb_data:
            return None
        charged = bb_data[-1].get('charged') if isinstance(bb_data, list) else None
        return int(charged) if charged is not None else None
    except Exception:
        return None


def _parse_steps(steps_data):
    try:
        if not steps_data:
            return None
        total = sum(x.get('steps', 0) or 0 for x in steps_data)
        return int(total) if total > 0 else None
    except Exception:
        return None


def _parse_stress(stress_data):
    try:
        val = (stress_data or {}).get('avgStressLevel')
        # Garmin returns -1 when there's no data
        if val is None or val < 0:
            return None
        return int(val)
    except Exception:
        return None


def fetch_ride_hr(api, start_utc_iso, elapsed_secs, time_stream=None):
    """
    Pull minute-by-minute HR from Garmin for the ride's time window.
    If time_stream (list of elapsed seconds from Strava) is provided, interpolates
    HR values onto it so the chart aligns with distance/speed.
    Returns {'avg': int, 'max': int, 'stream_data': [bpm, ...]} or None.
    """
    import bisect
    from datetime import datetime, timezone

    start_dt = datetime.fromisoformat(start_utc_iso.replace('Z', '+00:00'))
    start_ms = int(start_dt.timestamp() * 1000)
    end_ms   = start_ms + int(elapsed_secs) * 1000
    date_str = start_dt.strftime('%Y-%m-%d')

    hr_data = api.get_heart_rates(date_str)
    raw     = hr_data.get('heartRateValues') or []

    # Ride-only pairs for avg/max
    ride_pairs = [(ts, bpm) for ts, bpm in raw if bpm is not None and start_ms <= ts <= end_ms]
    if not ride_pairs:
        return None

    bpms = [bpm for _, bpm in ride_pairs]
    avg  = round(sum(bpms) / len(bpms))
    peak = max(bpms)

    if time_stream:
        # Include up to 5 min before start so interpolation has a proper anchor
        # at elapsed=0 rather than snapping to the first in-window Garmin point.
        buffer_ms = 5 * 60 * 1000
        interp_pairs = [(ts, bpm) for ts, bpm in raw
                        if bpm is not None and (start_ms - buffer_ms) <= ts <= end_ms]
        timestamps = [ts for ts, _ in interp_pairs]
        hr_vals    = [bpm for _, bpm in interp_pairs]
        stream_data = []
        for elapsed in time_stream:
            t   = start_ms + elapsed * 1000
            idx = bisect.bisect_left(timestamps, t)
            if idx == 0:
                stream_data.append(hr_vals[0])
            elif idx >= len(timestamps):
                stream_data.append(hr_vals[-1])
            else:
                t0, t1 = timestamps[idx - 1], timestamps[idx]
                v0, v1 = hr_vals[idx - 1], hr_vals[idx]
                frac = (t - t0) / (t1 - t0) if t1 > t0 else 0
                stream_data.append(round(v0 + frac * (v1 - v0)))
    else:
        stream_data = bpms

    return {'avg': avg, 'max': peak, 'stream_data': stream_data}


def sync_garmin(email, password, days=7):
    """Fetch the last `days` days of Garmin daily metrics and upsert into GarminDaily."""
    from database import get_db

    api = _client(email, password)
    db  = get_db()
    synced = 0

    today = date.today()
    for i in range(days):
        d = (today - timedelta(days=i)).isoformat()

        try:
            stats    = api.get_stats(d)
            rhr      = _parse_rhr(stats)
        except Exception:
            rhr = None

        try:
            hrv_data       = api.get_hrv_data(d)
            hrv, balanced  = _parse_hrv(hrv_data)
        except Exception:
            hrv, balanced = None, 0

        try:
            sleep_data         = api.get_sleep_data(d)
            sleep_hrs, sleep_score = _parse_sleep(sleep_data)
        except Exception:
            sleep_hrs, sleep_score = None, None

        try:
            bb_data      = api.get_body_battery(d, d)
            body_battery = _parse_body_battery(bb_data)
            bb_stream    = []
            if isinstance(bb_data, list):
                for item in bb_data:
                    for pair in (item.get('bodyBatteryValuesArray') or []):
                        if isinstance(pair, (list, tuple)) and len(pair) >= 2 and pair[1] is not None:
                            bb_stream.append([int(pair[0]), int(pair[1])])
            bb_stream_json = json.dumps(bb_stream) if bb_stream else None
        except Exception:
            body_battery, bb_stream_json = None, None

        try:
            hr_raw       = api.get_heart_rates(d)
            hr_stream    = [[int(ts), int(bpm)] for ts, bpm in (hr_raw.get('heartRateValues') or []) if bpm is not None]
            hr_stream_json = json.dumps(hr_stream) if hr_stream else None
        except Exception:
            hr_stream_json = None

        try:
            steps_data = api.get_steps_data(d)
            steps      = _parse_steps(steps_data)
        except Exception:
            steps = None

        try:
            stress_data  = api.get_stress_data(d)
            stress_score = _parse_stress(stress_data)
        except Exception:
            stress_score = None

        db.execute('''
            INSERT INTO GarminDaily (date, restingHR, hrv, hrvBalanced, sleepHours, sleepScore, bodyBattery, steps, stressScore, hrStream, bodyBatteryStream)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(date) DO UPDATE SET
                restingHR           = COALESCE(excluded.restingHR,          restingHR),
                hrv                 = COALESCE(excluded.hrv,                hrv),
                hrvBalanced         = excluded.hrvBalanced,
                sleepHours          = COALESCE(excluded.sleepHours,         sleepHours),
                sleepScore          = COALESCE(excluded.sleepScore,         sleepScore),
                bodyBattery         = COALESCE(excluded.bodyBattery,        bodyBattery),
                steps               = COALESCE(excluded.steps,              steps),
                stressScore         = COALESCE(excluded.stressScore,        stressScore),
                hrStream            = COALESCE(excluded.hrStream,           hrStream),
                bodyBatteryStream   = COALESCE(excluded.bodyBatteryStream,  bodyBatteryStream)
        ''', [d, rhr, hrv, balanced, sleep_hrs, sleep_score, body_battery, steps, stress_score, hr_stream_json, bb_stream_json])
        synced += 1

    db.commit()
    return synced
