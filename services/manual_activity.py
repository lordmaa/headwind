"""Activities typed in by hand: a ride you forgot to record, a turbo session, a walk without GPS.

Walk / Run / Hike go to the Workout table and Ride / VirtualRide to Activity, the same split as everything recorded by a device. A manual ride has no
GPS track, so it has no map or best efforts, but it counts in the totals. If a real recording of the same ride arrives later (Garmin sync, phone
upload) the duplicate logic keeps the recording and parks the typed-in copy.
"""
import hashlib
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from services import duplicates

LOCAL = ZoneInfo('Europe/London')
WORKOUT_SPORTS = ('Walk', 'Run', 'Hike')
RIDE_SPORTS = ('Ride', 'VirtualRide')
SPORTS = RIDE_SPORTS + WORKOUT_SPORTS
MAX_DURATION_S, MAX_DISTANCE_M, MAX_ELEV_M = 48 * 3600, 1_500_000, 20_000


def _local_start(local_iso):
    try:
        dt = datetime.fromisoformat(str(local_iso)[:19])
    except ValueError:
        raise ValueError('that date and time are not valid')
    if dt.tzinfo:
        dt = dt.astimezone(LOCAL).replace(tzinfo=None)
    if dt > datetime.now(LOCAL).replace(tzinfo=None) + timedelta(days=1):
        raise ValueError('that is in the future')
    if dt.year < 2000:
        raise ValueError('that date is too far in the past')
    return dt


def _num(v, name, lo=0, hi=None, required=False):
    if v in (None, ''):
        if required:
            raise ValueError(f'{name} is required')
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise ValueError(f'{name} must be a number')
    if x < lo or (hi is not None and x > hi):
        raise ValueError(f'{name} is out of range')
    return x


def create(db, rider_id, sport, local_iso, duration_s, distance_m, elev_m=None, calories=None, name=None, notes=None, client_id=None):
    """Store one manual activity. Returns {'kind', 'id', 'created', 'duplicate'}; raises ValueError with a readable reason. Commits."""
    if sport not in SPORTS:
        raise ValueError('sport must be one of ' + ', '.join(SPORTS))
    start = _local_start(local_iso)
    secs = int(_num(duration_s, 'duration', 60, MAX_DURATION_S, required=True))
    dist = _num(distance_m, 'distance', 0, MAX_DISTANCE_M, required=True)
    gain = _num(elev_m, 'elevation', 0, MAX_ELEV_M) or 0
    kcal = _num(calories, 'calories', 0, 20_000)
    name = (name or '').strip()[:80] or None
    notes = (notes or '').strip()[:1000] or None
    local = start.strftime('%Y-%m-%dT%H:%M:%S')
    utc = start.replace(tzinfo=LOCAL).astimezone(ZoneInfo('UTC')).strftime('%Y-%m-%dT%H:%M:%S')
    key = client_id or f'{local}|{sport}|{round(dist)}|{secs}'
    aid = ('man_' if sport in RIDE_SPORTS else 'wk_') + hashlib.sha1(f'{rider_id}|manual|{key}'.encode()).hexdigest()[:16]
    avg = dist / secs if secs else 0
    from services import goal_model
    kg = goal_model.current_weight_kg(rider_id) or 70.0

    if sport in WORKOUT_SPORTS:
        if db.execute('SELECT 1 FROM Workout WHERE id=?', [aid]).fetchone():
            return {'kind': 'workout', 'id': aid, 'created': False, 'duplicate': None}
        from services.workouts import estimate_calories
        if kcal is None:
            kcal = estimate_calories(sport, dist, secs, gain, kg)
        db.execute('''INSERT INTO Workout (id, riderId, sport, name, startDate, startDateLocal, distance, movingTime, elapsedTime, totalElevationGain,
                                           averageSpeed, calories, source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                   [aid, rider_id, sport, name, utc, local, dist, secs, secs, gain, avg, kcal, 'manual'])
        db.commit()
        return {'kind': 'workout', 'id': aid, 'created': True, 'duplicate': None}

    if duplicates.exists_anywhere(db, aid):
        return {'kind': 'ride', 'id': aid, 'created': False, 'duplicate': None}
    if kcal is None:
        from services.parser import estimate_ride_calories
        kcal = estimate_ride_calories(dist, secs, kg)
    db.execute('''INSERT INTO Activity (id, name, type, sportType, startDate, startDateLocal, distance, movingTime, elapsedTime, totalElevationGain,
                                        averageSpeed, maxSpeed, calories, notes, rawData, riderId, createdAt, updatedAt)
                  VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))''',
               [aid, name or ('Indoor ride' if sport == 'VirtualRide' else 'Ride'), sport, sport, utc, local, dist, secs, secs, gain, avg, 0, kcal, notes, '{}', rider_id])
    dup = None
    try:
        dup = duplicates.resolve(db, aid)
    except Exception:
        db.rollback()
        raise
    db.commit()
    return {'kind': 'ride', 'id': aid, 'created': True, 'duplicate': dup}
