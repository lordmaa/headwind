"""Changing an activity's type: a walk recorded as a ride, and back. Throwaway DB."""
import json

import pytest
from flask import Flask

import database
from services import convert, duplicates, gear

MI = 1609.344
STREAMS = json.dumps({'latlng': {'data': [[54.8 + i * 1e-4, -1.67] for i in range(300)]}, 'distance': {'data': [i * 13 for i in range(300)]}, 'time': {'data': list(range(0, 3000, 10))}})


@pytest.fixture
def db(tmp_path):
    app = Flask(__name__)
    app.config['DATABASE'] = str(tmp_path / 't.db')
    with app.app_context():
        database.migrate_db()
        d = database.get_db()
        d.execute("INSERT INTO Rider(name,isDefault) VALUES ('Rob',1)"); d.commit()
        yield d


def add_ride(db, rid='imp_x', miles=2.45, secs=2777, name='Ride', sport='Ride', calories=376):
    db.execute("INSERT INTO Activity (id,name,type,sportType,startDate,startDateLocal,distance,movingTime,elapsedTime,totalElevationGain,averageSpeed,maxSpeed,calories,startLat,startLng,streams,rawData,riderId,weatherSummary,createdAt,updatedAt)"
               " VALUES (?,?,?,?,'2026-10-05T17:40:02','2026-10-05T18:40:02',?,?,3059,42.5,1.4,3.1,?,54.8,-1.67,?,'{}',1,'Overcast, 17C','n','n')", [rid, name, sport, sport, miles * MI, secs, calories, STREAMS])
    db.commit()


def test_a_walk_recorded_as_a_ride_becomes_a_walk_with_walking_calories(db):
    add_ride(db)
    res = convert.change_sport(db, 'ride', 'imp_x', 'Walk'); db.commit()
    assert res['changed'] and res['kind'] == 'workout' and res['sport'] == 'Walk'
    w = db.execute('SELECT * FROM Workout WHERE id=?', [res['id']]).fetchone()
    assert w['sport'] == 'Walk' and w['name'] is None and abs(w['distance'] - 2.45 * MI) < 1 and w['movingTime'] == 2777 and w['weatherSummary'] == 'Overcast, 17C' and w['streams'] == STREAMS
    assert 100 < w['calories'] < 300 and w['calories'] < 376          # a walking estimate, not the cycling one
    assert db.execute("SELECT COUNT(*) FROM Activity WHERE id='imp_x'").fetchone()[0] == 0
    assert duplicates.is_deleted(db, 'imp_x')                          # a re-sync cannot bring it back as a ride


def test_ride_stats_no_longer_include_it_and_best_efforts_are_gone(db):
    add_ride(db)
    db.execute("INSERT INTO BestEffort (activityId, activityDate, distanceMi, elapsedSecs, avgSpeedMps) VALUES ('imp_x','2026-10-05',1,600,2.7)"); db.commit()
    convert.change_sport(db, 'ride', 'imp_x', 'Run'); db.commit()
    assert db.execute('SELECT COUNT(*) FROM BestEffort').fetchone()[0] == 0 and db.execute('SELECT COUNT(*) FROM Activity').fetchone()[0] == 0


def test_a_named_activity_keeps_its_name_a_generic_one_does_not(db):
    add_ride(db, 'a', name='Dog loop'); add_ride(db, 'b', name='Ride', miles=1)
    n1 = convert.change_sport(db, 'ride', 'a', 'Walk')['id']; n2 = convert.change_sport(db, 'ride', 'b', 'Walk')['id']
    assert db.execute('SELECT name FROM Workout WHERE id=?', [n1]).fetchone()[0] == 'Dog loop' and db.execute('SELECT name FROM Workout WHERE id=?', [n2]).fetchone()[0] is None


def test_a_workout_can_become_a_ride_and_gets_the_default_bike(db):
    bike = gear.create_bike(db, 1, 'Roubaix')
    add_ride(db); res = convert.change_sport(db, 'ride', 'imp_x', 'Walk'); db.commit()
    back = convert.change_sport(db, 'workout', res['id'], 'Ride'); db.commit()
    assert back['kind'] == 'ride' and back['id'].startswith('conv_')
    a = db.execute('SELECT * FROM Activity WHERE id=?', [back['id']]).fetchone()
    assert a['sportType'] == 'Ride' and a['bikeId'] == bike and abs(a['distance'] - 2.45 * MI) < 1 and a['streams'] == STREAMS
    assert db.execute('SELECT COUNT(*) FROM Workout').fetchone()[0] == 0


def test_label_only_changes_stay_where_they_are(db):
    add_ride(db)
    assert convert.change_sport(db, 'ride', 'imp_x', 'VirtualRide') == {'kind': 'ride', 'id': 'imp_x', 'sport': 'VirtualRide', 'changed': True}
    assert db.execute("SELECT sportType, type FROM Activity WHERE id='imp_x'").fetchone()[:] == ('VirtualRide', 'VirtualRide')
    db.execute("INSERT INTO Workout (id, riderId, sport, startDateLocal, distance, movingTime, source) VALUES ('wk_1',1,'Walk','2026-10-05T10:00:00',3000,1800,'android')"); db.commit()
    r = convert.change_sport(db, 'workout', 'wk_1', 'Hike'); assert r['changed'] and r['id'] == 'wk_1' and db.execute("SELECT sport FROM Workout WHERE id='wk_1'").fetchone()[0] == 'Hike'


def test_same_type_is_a_no_op_and_bad_input_is_a_clear_error(db):
    add_ride(db)
    assert convert.change_sport(db, 'ride', 'imp_x', 'Ride')['changed'] is False
    for args in (('ride', 'imp_x', 'Swim'), ('ride', 'nope', 'Walk'), ('workout', 'nope', 'Walk'), ('thing', 'imp_x', 'Walk')):
        with pytest.raises(ValueError):
            convert.change_sport(db, *args)


def test_a_ride_with_a_parked_duplicate_must_be_settled_first(db):
    add_ride(db, 'garmin_1'); add_ride(db, 'imp_phone')
    duplicates.park(db, 'imp_phone', 'garmin_1', 'test'); db.commit()
    with pytest.raises(ValueError, match='set aside'):
        convert.change_sport(db, 'ride', 'garmin_1', 'Walk')
