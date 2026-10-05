"""Duplicate rides: matching on the real instant, picking the better recording, parking (never deleting), and undoing the choice."""
import json

import pytest
from flask import Flask

import database
from services import duplicates as dup

GPS = json.dumps({'latlng': {'data': [[54.0 + i * 1e-4, -1.5] for i in range(3000)]}})


def _row(**kw):
    base = {'id': 'x', 'startDate': None, 'startDateLocal': '2026-10-05T11:30:53', 'elapsedTime': 6600, 'movingTime': 6500, 'distance': 33000.0,
            'averageHeartrate': None, 'averageWatts': None, 'averageCadence': None, 'streams': None, 'riderId': 1}
    base.update(kw)
    return base


# ---- pure matching: the exact pair that was missed on 2026-10-05 ----
def test_phone_utc_and_garmin_local_are_the_same_ride_in_bst():
    garmin = _row(id='garmin_1', startDateLocal='2026-10-05T11:30:53', movingTime=6542, elapsedTime=6600, distance=33285.0)
    phone = _row(id='imp_1', startDateLocal='2026-10-05T10:29:38.782000+00:00', startDate='2026-10-05T10:29:38.782000+00:00', movingTime=6517, elapsedTime=6620, distance=33290.1)
    assert dup.same_ride(garmin, phone) and dup.same_ride(phone, garmin)


def test_second_commute_same_day_is_a_different_ride():
    am, pm = _row(startDateLocal='2026-10-05T08:00:00'), _row(startDateLocal='2026-10-05T17:30:00')
    assert not dup.same_ride(am, pm)


def test_different_distance_is_a_different_ride():
    assert not dup.same_ride(_row(distance=33000), _row(distance=20000))


def test_zero_distance_activity_does_not_swallow_a_real_ride():
    real, empty = _row(), _row(distance=0, startDateLocal='2026-10-05T12:00:00', elapsedTime=300, movingTime=300)
    assert not dup.same_ride(real, empty) and not dup.same_ride(empty, real)
    assert dup.same_ride(_row(distance=0), _row(distance=0, startDateLocal='2026-10-05T11:31:30'))      # two identical indoor sessions are one


def test_phone_started_a_few_minutes_late_still_matches():
    assert dup.same_ride(_row(startDateLocal='2026-10-05T11:30:00'), _row(startDateLocal='2026-10-05T11:34:30', elapsedTime=6300, movingTime=6200, distance=32400))


def test_garmin_recording_beats_a_bare_phone_gpx():
    garmin = _row(id='garmin_1', averageHeartrate=140, streams=GPS)
    phone = _row(id='imp_1', streams=GPS)
    winner, loser, reason = dup.pick_winner(phone, garmin)           # existing = phone, new = garmin
    assert winner['id'] == 'garmin_1' and loser['id'] == 'imp_1' and 'Garmin' in reason


def test_tie_keeps_what_is_already_saved():
    a, b = _row(id='imp_a', streams=GPS), _row(id='imp_b', streams=GPS)
    assert dup.pick_winner(a, b)[0]['id'] == 'imp_a'


def test_manual_entry_loses_to_any_real_recording():
    assert dup.pick_winner(_row(id='man_1'), _row(id='imp_2', streams=GPS))[0]['id'] == 'imp_2'


# ---- database behaviour ----
@pytest.fixture
def db(tmp_path):
    app = Flask(__name__)
    app.config['DATABASE'] = str(tmp_path / 't.db')
    with app.app_context():
        database.migrate_db()
        d = database.get_db()
        d.execute("INSERT INTO Rider(name) VALUES ('r')")
        d.commit()
        yield d


def _add(db, rid, local, dist=33000, hr=None, notes=None, streams=None):
    db.execute("INSERT INTO Activity (id,name,type,sportType,startDate,startDateLocal,distance,movingTime,elapsedTime,totalElevationGain,averageSpeed,maxSpeed,"
               "averageHeartrate,notes,streams,riderId,createdAt,updatedAt) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,'n','n')",
               [rid, rid, 'Ride', 'Ride', local, local, dist, 6500, 6600, 0, 5, 9, hr, notes, streams])
    db.commit()


def _live(db):
    return sorted(r[0] for r in db.execute('SELECT id FROM Activity'))


def test_resolve_parks_the_loser_and_keeps_notes(db):
    _add(db, 'imp_phone', '2026-10-05T10:29:38+00:00', notes='felt great')
    _add(db, 'garmin_1', '2026-10-05T11:30:53', hr=140)
    res = dup.resolve(db, 'garmin_1'); db.commit()
    assert res['kept'] == 'garmin_1' and res['parked'] == 'imp_phone'
    assert _live(db) == ['garmin_1']
    assert db.execute("SELECT notes FROM Activity WHERE id='garmin_1'").fetchone()[0] == 'felt great'      # nothing the user typed is lost
    assert dup.exists_anywhere(db, 'imp_phone') and not dup.find_duplicates(db, db.execute("SELECT * FROM Activity WHERE id='garmin_1'").fetchone())


def test_a_later_worse_copy_is_parked_instead_of_added(db):
    _add(db, 'garmin_1', '2026-10-05T11:30:53', hr=140)
    _add(db, 'imp_phone', '2026-10-05T10:29:38+00:00')
    res = dup.resolve(db, 'imp_phone'); db.commit()
    assert res['kept'] == 'garmin_1' and res['parked'] == 'imp_phone' and _live(db) == ['garmin_1']


def test_swap_restores_the_other_recording_and_is_reversible(db):
    _add(db, 'imp_phone', '2026-10-05T10:29:38+00:00')
    _add(db, 'garmin_1', '2026-10-05T11:30:53', hr=140)
    dup.resolve(db, 'garmin_1'); db.commit()
    assert dup.swap(db, 'imp_phone') == 'imp_phone'; db.commit()
    assert _live(db) == ['imp_phone'] and dup.parked_for(db, 'imp_phone')[0]['id'] == 'garmin_1'
    assert dup.swap(db, 'garmin_1') == 'garmin_1'; db.commit()                 # and back again
    assert _live(db) == ['garmin_1']


def test_keep_both_restores_it_and_is_never_merged_again(db):
    _add(db, 'imp_phone', '2026-10-05T10:29:38+00:00')
    _add(db, 'garmin_1', '2026-10-05T11:30:53', hr=140)
    dup.resolve(db, 'garmin_1'); db.commit()
    dup.keep_both(db, 'imp_phone'); db.commit()
    assert _live(db) == ['garmin_1', 'imp_phone']
    assert dup.resolve(db, 'garmin_1') is None                                  # the pair is remembered as "different"


def test_discard_keeps_the_id_remembered_but_drops_the_data(db):
    _add(db, 'imp_phone', '2026-10-05T10:29:38+00:00')
    _add(db, 'garmin_1', '2026-10-05T11:30:53', hr=140)
    dup.resolve(db, 'garmin_1'); db.commit()
    assert dup.discard(db, 'imp_phone'); db.commit()
    assert dup.exists_anywhere(db, 'imp_phone') and dup.parked_for(db, 'garmin_1') == []
    assert dup.swap(db, 'imp_phone') is None


def test_other_riders_rides_are_never_merged(db):
    db.execute("INSERT INTO Rider(name) VALUES ('other')"); db.commit()
    _add(db, 'garmin_1', '2026-10-05T11:30:53', hr=140)
    _add(db, 'imp_other', '2026-10-05T10:29:38+00:00')
    db.execute("UPDATE Activity SET riderId=2 WHERE id='imp_other'"); db.commit()
    assert dup.resolve(db, 'imp_other') is None and _live(db) == ['garmin_1', 'imp_other']
