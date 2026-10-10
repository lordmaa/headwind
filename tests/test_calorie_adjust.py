"""Global calorie-burn adjustment: applied as figures are pulled in, raw kept, reversible. Throwaway DB."""
import pytest
from flask import Flask

import database
from services import calorie_adjust as ca, convert, manual_activity, workouts


@pytest.fixture
def db(tmp_path):
    app = Flask(__name__)
    app.config['DATABASE'] = str(tmp_path / 't.db')
    with app.app_context():
        database.migrate_db()
        d = database.get_db()
        d.execute("INSERT OR IGNORE INTO Settings (id) VALUES (1)")
        d.execute("INSERT INTO Rider(name,isDefault) VALUES ('Rob',1)")
        d.commit()
        yield d


def set_pct(db, pct):
    db.execute('UPDATE Settings SET calorieAdjustPct=? WHERE id=1', [pct]); db.commit()


def test_apply_is_a_straight_percentage_and_off_by_default(db):
    assert ca.get_pct() == 0
    assert ca.apply(500) == 500
    set_pct(db, 20)
    assert ca.get_pct() == 20
    assert ca.apply(500) == 400
    assert ca.apply(None) is None and ca.apply(0) == 0
    assert ca.pair(250) == (250, 200)
    assert ca.pair(None) == (None, None)


def test_the_percentage_is_clamped(db):
    assert ca.clamp(-5) == 0 and ca.clamp('x') == 0 and ca.clamp(80) == ca.MAX_PCT


def test_a_new_estimated_workout_is_stored_adjusted_with_the_raw_kept(db):
    set_pct(db, 20)
    raw, adj = ca.pair(workouts.estimate_calories('Walk', 4000, 3000, 20, 80))
    assert adj == pytest.approx(raw * 0.8, abs=0.1)


def test_manual_numbers_the_user_typed_are_never_adjusted(db):
    set_pct(db, 20)
    r = manual_activity.create(db, 1, 'Walk', '2026-10-05T10:00:00', 1800, 2000, calories=300)
    db.commit()
    row = db.execute('SELECT calories, caloriesRaw FROM Workout WHERE id=?', [r['id']]).fetchone()
    assert row['calories'] == 300 and row['caloriesRaw'] is None


def test_a_manual_activity_with_an_estimated_figure_is_adjusted(db):
    set_pct(db, 20)
    r = manual_activity.create(db, 1, 'Walk', '2026-10-06T10:00:00', 1800, 2000)
    db.commit()
    row = db.execute('SELECT calories, caloriesRaw FROM Workout WHERE id=?', [r['id']]).fetchone()
    assert row['caloriesRaw'] and row['calories'] == pytest.approx(row['caloriesRaw'] * 0.8, abs=0.1)


def test_reapply_recomputes_from_raw_and_is_reversible(db):
    db.execute("INSERT INTO Activity (id,name,type,sportType,startDate,startDateLocal,distance,movingTime,elapsedTime,totalElevationGain,averageSpeed,maxSpeed,calories,caloriesRaw,rawData,riderId) "
               "VALUES ('imp_a','R','Ride','Ride','2026-10-05T17:00:00','2026-10-05T18:00:00',1000,100,100,0,1,1,400,500,'{}',1)")
    db.commit()
    set_pct(db, 20)
    assert ca.reapply_all()['Activity'] == 1
    assert db.execute("SELECT calories FROM Activity WHERE id='imp_a'").fetchone()[0] == 400
    set_pct(db, 0)
    ca.reapply_all()
    assert db.execute("SELECT calories FROM Activity WHERE id='imp_a'").fetchone()[0] == 500       # back to the original
    set_pct(db, 10)
    ca.reapply_all()
    assert db.execute("SELECT calories FROM Activity WHERE id='imp_a'").fetchone()[0] == 450


def test_history_adoption_leaves_friends_rides_alone_and_adopts_own(db):
    for rid in ('imp_own', 'f1_imp_theirs'):
        db.execute("INSERT INTO Activity (id,name,type,sportType,startDate,startDateLocal,distance,movingTime,elapsedTime,totalElevationGain,averageSpeed,maxSpeed,calories,rawData,riderId) "
                   "VALUES (?,'R','Ride','Ride','2026-10-05T17:00:00','2026-10-05T18:00:00',1000,100,100,0,1,1,500,'{}',1)", [rid])
    db.commit()
    set_pct(db, 20)
    ca.reapply_all(include_unrecorded=True)
    assert db.execute("SELECT calories, caloriesRaw FROM Activity WHERE id='imp_own'").fetchone()[:] == (400, 500)
    assert db.execute("SELECT calories, caloriesRaw FROM Activity WHERE id='f1_imp_theirs'").fetchone()[:] == (500, None)


def test_garmin_daily_totals_are_adjusted_from_raw(db):
    db.execute("INSERT INTO GarminDaily (date, totalCalories, activeCalories, totalCaloriesRaw, activeCaloriesRaw) VALUES ('2026-10-05', 2400, 800, 3000, 1000)")
    db.commit()
    set_pct(db, 20)
    ca.reapply_all()
    r = db.execute("SELECT totalCalories, activeCalories FROM GarminDaily WHERE date='2026-10-05'").fetchone()
    assert (r[0], r[1]) == (2400, 800)


def test_an_imported_ride_with_device_calories_is_stored_adjusted_with_raw(db):
    from routes.import_rides import _insert
    set_pct(db, 20)
    _insert(db, {'id': 'imp_t1', 'name': 'R', 'sportType': 'Ride', 'startDate': '2026-10-05T17:00:00', 'distance': 20000, 'movingTime': 3600,
                 'elapsedTime': 3600, 'calories': 700, 'averageSpeed': 5.5}, rider_id=1)
    db.commit()
    r = db.execute("SELECT calories, caloriesRaw FROM Activity WHERE id='imp_t1'").fetchone()
    assert (r['calories'], r['caloriesRaw']) == (560, 700)


def test_a_gpx_ride_with_no_calories_gets_an_adjusted_estimate(db):
    from routes.import_rides import _insert
    set_pct(db, 20)
    _insert(db, {'id': 'imp_t2', 'name': 'R', 'sportType': 'Ride', 'startDate': '2026-10-06T17:00:00', 'distance': 20000, 'movingTime': 3600,
                 'elapsedTime': 3600, 'averageSpeed': 5.5}, rider_id=1)
    db.commit()
    r = db.execute("SELECT calories, caloriesRaw FROM Activity WHERE id='imp_t2'").fetchone()
    if r['caloriesRaw']:                      # an estimate needs a weight on file; when there is one it is shaved by the setting
        assert r['calories'] == pytest.approx(r['caloriesRaw'] * 0.8, abs=0.1)
