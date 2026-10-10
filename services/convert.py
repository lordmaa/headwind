"""Change what kind of activity something is: a walk that was recorded as a ride, a ride that was typed in as a run, and so on.

Rides (Ride / VirtualRide) live in `Activity`; Walk / Run / Hike live in `Workout` (kept apart on purpose: ~100 ride queries assume Activity = rides), so
changing between the two families MOVES the row and everything derived from it, it is not just a label change. Calories are re-estimated for the new
sport (a walk logged as a ride carries a cycling MET estimate, which badly over-counts). Nothing here commits: the caller does.
"""
import hashlib
import json

RIDES = ('Ride', 'VirtualRide')
WORKOUTS = ('Walk', 'Run', 'Hike')
SPORTS = RIDES + WORKOUTS
_GENERIC_NAMES = {'', 'ride', 'walk', 'run', 'hike', 'virtualride', 'garmin activity', 'ride activity'}


def _kg(rider_id):
    try:
        from services import goal_model
        return goal_model.current_weight_kg(rider_id) or 70.0
    except Exception:
        return 70.0


def _ride_to_workout(db, row, sport):
    from services import duplicates
    from services.workouts import estimate_calories
    if db.execute("SELECT 1 FROM ActivityDuplicate WHERE primaryId=? AND status='parked' LIMIT 1", [row['id']]).fetchone():
        raise ValueError('this ride has another recording set aside under it: settle that first (open the ride and choose which recording to keep)')
    wid = 'wk_' + hashlib.sha1(f"{row['riderId']}|conv|{row['id']}".encode()).hexdigest()[:16]
    dist, secs, gain = row['distance'] or 0, int(row['movingTime'] or 0), row['totalElevationGain'] or 0
    from services.calorie_adjust import pair as _kcal_pair
    kcal_raw, kcal = _kcal_pair(estimate_calories(sport, dist, secs, gain, _kg(row['riderId'])))
    if kcal is None:                                     # no estimate possible: carry the ride's figure across (raw if it has one)
        kcal_raw = row['caloriesRaw']
        kcal = row['calories']
    name = row['name'] if (row['name'] or '').strip().lower() not in _GENERIC_NAMES else None
    db.execute('''INSERT OR REPLACE INTO Workout (id, riderId, sport, name, startDate, startDateLocal, distance, movingTime, elapsedTime, totalElevationGain,
                  averageSpeed, maxSpeed, averageHeartrate, calories, startLat, startLng, streams, weatherSummary, source, caloriesRaw) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
               [wid, row['riderId'], sport, name, row['startDate'], row['startDateLocal'], dist, secs, row['elapsedTime'], gain, row['averageSpeed'], row['maxSpeed'],
                row['averageHeartrate'], kcal, row['startLat'], row['startLng'], row['streams'], row['weatherSummary'], 'converted', kcal_raw])
    duplicates.delete_activity(db, row['id'])           # derived data, bike link and a tombstone so a re-sync cannot bring it back as a ride
    db.execute('DELETE FROM RideMemory WHERE rideId=?', [row['id']])
    from services import workouts as _wk
    _wk.refresh_derived(db, wid)                         # a run gets its best times and run-segment efforts
    return {'kind': 'workout', 'id': wid, 'sport': sport}


def _workout_to_ride(db, row, sport):
    from services import duplicates, gear
    from services.parser import estimate_ride_calories
    from services.workouts import label
    aid = 'conv_' + str(row['id']).replace('wk_', '', 1)
    dist, secs = row['distance'] or 0, int(row['movingTime'] or 0)
    from services.calorie_adjust import pair as _kcal_pair
    kcal_raw, kcal = _kcal_pair(estimate_ride_calories(dist, secs, _kg(row['riderId'])))
    if kcal is None:                                     # no estimate possible: carry the workout's figure across (raw if it has one)
        kcal_raw = row['caloriesRaw']
        kcal = row['calories']
    db.execute('''INSERT OR REPLACE INTO Activity (id, name, type, sportType, startDate, startDateLocal, distance, movingTime, elapsedTime, totalElevationGain, averageSpeed, maxSpeed,
                  averageHeartrate, calories, startLat, startLng, streams, rawData, riderId, weatherSummary, caloriesRaw, createdAt, updatedAt)
                  VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))''',
               [aid, label(dict(row)) if not (row['name'] or '').strip() else row['name'], sport, sport, row['startDate'], row['startDateLocal'], dist, secs, row['elapsedTime'] or secs,
                row['totalElevationGain'] or 0, row['averageSpeed'] or 0, row['maxSpeed'] or 0, row['averageHeartrate'], kcal, row['startLat'], row['startLng'], row['streams'], '{}',
                row['riderId'], row['weatherSummary'], kcal_raw])
    from services import workouts as _wk
    _wk.forget(db, row['id'])
    db.execute('DELETE FROM Workout WHERE id=?', [row['id']])
    duplicates.rebuild_derived(db, aid)                 # best efforts + segment efforts, now that it is a ride
    gear.on_ride_added(db, aid)                         # the rider's default bike
    return {'kind': 'ride', 'id': aid, 'sport': sport}


def change_sport(db, kind, act_id, sport):
    """Returns {'kind', 'id', 'sport', 'changed'} (the id changes when the activity moves between the two families). Raises ValueError with a readable reason."""
    if sport not in SPORTS:
        raise ValueError('pick one of: ' + ', '.join(SPORTS))
    if kind == 'ride':
        row = db.execute('SELECT * FROM Activity WHERE id=?', [act_id]).fetchone()
        if not row:
            raise ValueError('that ride was not found')
        if (row['sportType'] or '') == sport:
            return {'kind': 'ride', 'id': act_id, 'sport': sport, 'changed': False}
        if sport in RIDES:                               # Ride <-> VirtualRide (or an e-bike / MTB ride becoming a plain Ride): just the label
            db.execute('UPDATE Activity SET sportType=?, type=? WHERE id=?', [sport, sport, act_id])
            return {'kind': 'ride', 'id': act_id, 'sport': sport, 'changed': True}
        return dict(_ride_to_workout(db, row, sport), changed=True)
    if kind == 'workout':
        row = db.execute('SELECT * FROM Workout WHERE id=?', [act_id]).fetchone()
        if not row:
            raise ValueError('that activity was not found')
        if row['sport'] == sport:
            return {'kind': 'workout', 'id': act_id, 'sport': sport, 'changed': False}
        if sport in WORKOUTS:
            db.execute('UPDATE Workout SET sport=? WHERE id=?', [sport, act_id])
            from services import workouts as _wk
            _wk.refresh_derived(db, act_id)            # a walk has no run segments or best times; a run needs them
            from services.workouts import estimate_calories
            from services.calorie_adjust import pair as _kcal_pair
            kcal_raw, kcal = _kcal_pair(estimate_calories(sport, row['distance'] or 0, int(row['movingTime'] or 0), row['totalElevationGain'] or 0, _kg(row['riderId'])))
            if kcal is not None and (row['source'] != 'manual' or not row['calories']):
                db.execute('UPDATE Workout SET calories=?, caloriesRaw=? WHERE id=?', [kcal, kcal_raw, act_id])
            return {'kind': 'workout', 'id': act_id, 'sport': sport, 'changed': True}
        return dict(_workout_to_ride(db, row, sport), changed=True)
    raise ValueError('kind must be ride or workout')
