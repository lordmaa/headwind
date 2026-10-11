"""Phone sensor sidecars (attach by time overlap, summary) and the live-ride publisher (pure message building, idle timer). Throwaway DB."""
import json

import pytest
from flask import Flask

import database
from services import live, sensors

T0 = 1_790_000_000_000      # an arbitrary epoch ms


def make_rows(n=120, step_ms=1000, start=T0, speed=8.0, rough=0.4):
    rows = []
    for i in range(n):
        rows.append([start + i * step_ms, rough + (i % 10) * 0.05, 1.0 + (i % 7) * 0.2, 0.05, 3.0 + (i % 5), 1013.0 - i * 0.02, 51.5 + i * 1e-4, -0.12, speed, 50.0])
    return rows


def payload(cid='c1', **kw):
    d = {'client_id': cid, 'fields': sensors.FIELDS, 'rows': make_rows(**kw), 'mount': 'bars', 'device': 'Pixel 10', 'sensors_only': True}
    return d


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


def add_ride(db, rid='garmin_1', start_ms=T0 - 30_000, secs=150, kind='Activity'):
    from datetime import datetime, timezone
    iso = datetime.fromtimestamp(start_ms / 1000, timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
    if kind == 'Activity':
        db.execute("INSERT INTO Activity (id,name,type,sportType,startDate,startDateLocal,distance,movingTime,elapsedTime,rawData,riderId) VALUES (?,?,?,?,?,?,?,?,?,'{}',1)",
                   [rid, 'R', 'Ride', 'Ride', iso, iso, 1000, secs, secs])
    else:
        db.execute("INSERT INTO Workout (id,riderId,sport,startDate,startDateLocal,distance,movingTime,elapsedTime,source) VALUES (?,1,'Run',?,?,1000,?,?,'android')", [rid, iso, iso, secs, secs])
    db.commit()


def test_a_sidecar_is_validated_and_summarised(db):
    res, err = sensors.store(db, 1, payload())
    assert err is None and res['id']
    row = db.execute('SELECT summary, mount, sensorsOnly FROM RideSensor').fetchone()
    s = json.loads(row['summary'])
    assert s['samples'] == 120 and s['duration_s'] == 119 and s['rough_max'] > s['rough_mean'] > 0
    assert row['mount'] == 'bars' and row['sensorsOnly'] == 1
    assert 'baro_climb_m' in s and s['press_start'] > s['press_end']


def test_junk_is_rejected(db):
    assert sensors.store(db, 1, {'client_id': 'x', 'fields': ['t'], 'rows': [[1]]})[1]
    assert sensors.store(db, 1, {'fields': sensors.FIELDS, 'rows': make_rows()})[1] == 'client_id is required'
    assert sensors.store(db, 1, 'nope')[1]


def test_upload_is_idempotent_per_client_id(db):
    sensors.store(db, 1, payload('same'))
    sensors.store(db, 1, payload('same'))
    assert db.execute('SELECT COUNT(*) FROM RideSensor').fetchone()[0] == 1


def test_sidecar_attaches_to_a_ride_that_is_already_there(db):
    add_ride(db)
    res, _ = sensors.store(db, 1, payload())
    assert res['attached'] == {'kind': 'activity', 'id': 'garmin_1'}


def test_sidecar_waits_for_a_ride_that_arrives_later(db):
    res, _ = sensors.store(db, 1, payload())
    assert res['attached'] is None
    add_ride(db, 'garmin_late')
    sensors.attach_for_ride(db, 'garmin_late')
    assert db.execute('SELECT rideId FROM RideSensor').fetchone()[0] == 'garmin_late'


def test_a_ride_that_does_not_overlap_gets_nothing(db):
    add_ride(db, 'elsewhere', start_ms=T0 + 3_600_000)
    res, _ = sensors.store(db, 1, payload())
    assert res['attached'] is None


def test_a_phone_recorded_run_can_carry_its_own_sidecar(db):
    add_ride(db, 'wk_run', kind='Workout')
    res, _ = sensors.store(db, 1, payload())
    assert res['attached'] == {'kind': 'workout', 'id': 'wk_run'}


def test_for_ride_returns_time_aligned_series(db):
    add_ride(db)
    sensors.store(db, 1, payload())
    out = sensors.for_ride(db, 'garmin_1')
    assert out['mount'] == 'bars' and out['sensors_only']
    s = out['series']
    assert abs(s['min'][0] - 0.5) < 0.01          # the sidecar started 30 s after the ride did
    assert len(s['rough']) == len(s['min']) == len(s['alt'])


def test_deleting_the_ride_keeps_the_sidecar(db):
    from services import duplicates
    add_ride(db)
    sensors.store(db, 1, payload())
    duplicates.delete_activity(db, 'garmin_1')
    r = db.execute('SELECT rideId FROM RideSensor').fetchone()
    assert r['rideId'] is None


def test_hard_braking_is_counted(db):
    rows = make_rows(n=60, speed=9.0)
    for i in range(30, 33):
        rows[i][8] = 9.0 - (i - 29) * 3.0            # 9 -> 6 -> 3 -> 0 m/s in three seconds
    s = sensors.summarise(rows)
    assert s['hard_brakes'] >= 1 and s['max_decel'] >= 2.5


# ---- live ride ----

def test_live_payload_is_converted_to_ha_units():
    c = live.clean({'state': 'riding', 'sport': 'Ride', 'lat': 51.5, 'lng': -0.12, 'speed_mps': 8.94, 'distance_m': 16093.44, 'elapsed_s': 3725, 'alt_m': 100,
                    'sensors': {'rough': 0.8, 'tilt': 12.4, 'press': 1008.2}, 'battery_pct': 71})
    st = live.states_for(c)
    assert st['live_ride_status'] == 'riding' and st['live_ride_speed_mph'] == '20.0' and st['live_ride_distance_mi'] == '10.0'
    assert st['live_ride_elapsed'] == '1:02:05' and st['live_ride_elevation_ft'] == '328' and st['live_ride_lean_deg'] == '12.4'


def test_live_bad_coordinates_are_dropped():
    c = live.clean({'lat': 999, 'lng': 5})
    assert live.tracker_attrs(c) == {'source_type': 'gps'}


def test_live_messages_include_discovery_once_and_a_tracker():
    c = live.clean({'state': 'riding', 'lat': 51.5, 'lng': -0.12, 'speed_mps': 5})
    first = live.build_messages(c, True)
    later = live.build_messages(c, False)
    assert len(first) > len(later)
    topics = [m['topic'] for m in first]
    assert any('/device_tracker/' in t and t.endswith('/config') for t in topics) and any(t.endswith('/live_ride_speed_mph/state') or 'live_ride_speed_mph' in t for t in topics)
    attrs = [json.loads(m['payload']) for m in later if m['topic'].endswith('/attributes')][0]
    assert attrs['latitude'] == 51.5 and attrs['longitude'] == -0.12


def test_live_update_publishes_and_idle_always_publishes():
    sent = []
    live._state.update(last=None, last_pub=0.0, timer=None, sent_discovery=False)
    live.update({'state': 'riding', 'speed_mps': 5}, sender=lambda m, *a: sent.append(m), conn=('h', 1883, None))
    n1 = len(sent)
    live.update({'state': 'riding', 'speed_mps': 6}, sender=lambda m, *a: sent.append(m), conn=('h', 1883, None))   # inside the 2 s gap: not published
    assert len(sent) == n1
    live.update({'state': 'idle'}, sender=lambda m, *a: sent.append(m), conn=('h', 1883, None))
    assert len(sent) > n1
    status = [m['payload'] for m in sent[-1] if m['topic'].endswith('live_ride_status/state')]
    assert status == ['idle']
    t = live._state['timer']
    assert t is None


def test_live_goes_idle_if_the_phone_stops_talking():
    sent = []
    live._state.update(last=None, last_pub=0.0, timer=None, sent_discovery=False)
    c = live.update({'state': 'riding', 'speed_mps': 5}, sender=lambda m, *a: sent.append(m), conn=('h', 1883, None))
    live._state['timer'].cancel()
    live._idle_check(c['ts'], sender=lambda m, *a: sent.append(m), conn=('h', 1883, None))
    status = [m['payload'] for m in sent[-1] if m['topic'].endswith('live_ride_status/state')]
    assert status == ['idle']
