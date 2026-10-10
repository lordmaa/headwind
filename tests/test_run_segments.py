"""Run segments: runs only match run segments, rides only ride segments; efforts/PRs span Workout and Activity. Throwaway DB."""
import json

import pytest
from flask import Flask

import database
from services import run_stats, segments, workouts


def line(secs, speed_mps=3.0, lat0=51.5, lng0=-0.12):
    """A straight northbound GPS track at a steady speed, one fix per second."""
    d_lat = speed_mps / 111_320.0
    ll = [[lat0 + i * d_lat, lng0] for i in range(secs + 1)]
    return json.dumps({'latlng': {'data': ll}, 'time': {'data': list(range(secs + 1))}, 'distance': {'data': [i * speed_mps for i in range(secs + 1)]}})


@pytest.fixture
def db(tmp_path):
    app = Flask(__name__)
    app.config['DATABASE'] = str(tmp_path / 't.db')
    with app.app_context():
        database.migrate_db()
        d = database.get_db()
        d.execute("INSERT OR IGNORE INTO Settings (id) VALUES (1)")
        d.execute("INSERT INTO Rider(name,isDefault) VALUES ('Leanne',1)")
        d.commit()
        yield d


def add_run(db, wid, secs=1200, speed=3.0, day='2026-10-07', sport='Run'):
    st = line(secs, speed)
    db.execute("INSERT INTO Workout (id,riderId,sport,startDateLocal,distance,movingTime,streams,source) VALUES (?,1,?,?,?,?,?,'android')", [wid, sport, f'{day}T10:00:00', secs * speed, secs, st])
    db.commit()
    workouts.refresh_derived(db, wid)
    db.commit()


def add_ride(db, aid, secs=1200, speed=6.0, day='2026-10-07'):
    st = line(secs, speed)
    db.execute("INSERT INTO Activity (id,name,type,sportType,startDate,startDateLocal,distance,movingTime,elapsedTime,rawData,riderId,streams) VALUES (?,'R','Ride','Ride',?,?,?,?,?,'{}',1,?)",
               [aid, f'{day}T10:00:00', f'{day}T10:00:00', secs * speed, secs, secs, st])
    db.commit()


def add_seg(db, sport, name='Seg', start_s=100, end_s=700, speed=3.0):
    lat0 = 51.5
    d = speed / 111_320.0
    sl, el = lat0 + start_s * d, lat0 + end_s * d
    db.execute("INSERT INTO Segment (name,startLat,startLng,endLat,endLng,distanceM,sport) VALUES (?,?,?,?,?,?,?)", [name, sl, -0.12, el, -0.12, (end_s - start_s) * speed, sport])
    db.commit()
    return db.execute('SELECT last_insert_rowid()').fetchone()[0]


def test_a_run_matches_a_run_segment_and_gets_a_pr(db):
    sid = add_seg(db, 'run')
    add_run(db, 'wk_a')
    row = db.execute('SELECT elapsedSecs, isPR FROM SegmentEffort WHERE segmentId=? AND activityId=?', [sid, 'wk_a']).fetchone()
    assert row and abs(row['elapsedSecs'] - 600) <= 2 and row['isPR'] == 1


def test_a_run_never_matches_a_ride_segment(db):
    sid = add_seg(db, 'ride')
    add_run(db, 'wk_a')
    assert db.execute('SELECT COUNT(*) FROM SegmentEffort WHERE segmentId=?', [sid]).fetchone()[0] == 0


def test_a_ride_never_matches_a_run_segment(db):
    sid = add_seg(db, 'run', speed=3.0)
    add_ride(db, 'imp_r')
    segments.scan_all_activities(db)
    assert db.execute('SELECT COUNT(*) FROM SegmentEffort WHERE segmentId=?', [sid]).fetchone()[0] == 0


def test_a_walk_has_no_segments(db):
    sid = add_seg(db, 'run')
    add_run(db, 'wk_w', sport='Walk')
    assert db.execute('SELECT COUNT(*) FROM SegmentEffort WHERE segmentId=?', [sid]).fetchone()[0] == 0


def test_the_faster_run_takes_the_pr_and_deleting_it_hands_it_back(db):
    sid = add_seg(db, 'run')
    add_run(db, 'wk_slow', speed=2.5, secs=1500, day='2026-10-01')
    add_run(db, 'wk_fast', speed=3.4, secs=1200, day='2026-10-07')
    pr = db.execute('SELECT activityId FROM SegmentEffort WHERE segmentId=? AND isPR=1', [sid]).fetchone()
    assert pr['activityId'] == 'wk_fast'
    workouts.forget(db, 'wk_fast'); db.execute("DELETE FROM Workout WHERE id='wk_fast'"); db.commit()
    pr = db.execute('SELECT activityId FROM SegmentEffort WHERE segmentId=? AND isPR=1', [sid]).fetchone()
    assert pr['activityId'] == 'wk_slow'


def test_a_new_segment_scan_picks_up_existing_runs(db):
    add_run(db, 'wk_a')
    sid = add_seg(db, 'run')
    segments.scan_all_activities(db, segment_ids=[sid])
    assert db.execute('SELECT COUNT(*) FROM SegmentEffort WHERE segmentId=?', [sid]).fetchone()[0] == 1


def test_changing_a_run_to_a_walk_removes_its_efforts(db):
    from services import convert
    sid = add_seg(db, 'run')
    add_run(db, 'wk_a')
    convert.change_sport(db, 'workout', 'wk_a', 'Walk'); db.commit()
    assert db.execute('SELECT COUNT(*) FROM SegmentEffort WHERE segmentId=?', [sid]).fetchone()[0] == 0
    assert db.execute('SELECT COUNT(*) FROM RunEffort WHERE runId=?', ['wk_a']).fetchone()[0] == 0


def test_run_stats_gives_best_times_and_ignores_test_recordings(db):
    add_run(db, 'wk_a', secs=1800, speed=3.0)           # 5.4 km
    db.execute("INSERT INTO Workout (id,riderId,sport,startDateLocal,distance,movingTime,streams,source) VALUES ('wk_tiny',1,'Run','2026-10-08T10:00:00',16,9,?, 'android')", [line(9)]); db.commit()
    out = run_stats.payloads(1)['nutrition_run_stats']
    assert out['runs'] == 1
    five_k = [b for b in out['bests'] if b['label'] == '5 km'][0]
    assert abs(five_k['secs'] - 1667) <= 3                # 5 km at 3 m/s
    assert not [b for b in out['bests'] if b['label'] == '10 km']
