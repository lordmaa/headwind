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
            bb_data     = api.get_body_battery(d, d)
            body_battery = _parse_body_battery(bb_data)
        except Exception:
            body_battery = None

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
            INSERT INTO GarminDaily (date, restingHR, hrv, hrvBalanced, sleepHours, sleepScore, bodyBattery, steps, stressScore)
            VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT(date) DO UPDATE SET
                restingHR    = COALESCE(excluded.restingHR,    restingHR),
                hrv          = COALESCE(excluded.hrv,          hrv),
                hrvBalanced  = excluded.hrvBalanced,
                sleepHours   = COALESCE(excluded.sleepHours,   sleepHours),
                sleepScore   = COALESCE(excluded.sleepScore,   sleepScore),
                bodyBattery  = COALESCE(excluded.bodyBattery,  bodyBattery),
                steps        = COALESCE(excluded.steps,        steps),
                stressScore  = COALESCE(excluded.stressScore,  stressScore)
        ''', [d, rhr, hrv, balanced, sleep_hrs, sleep_score, body_battery, steps, stress_score])
        synced += 1

    db.commit()
    return synced
