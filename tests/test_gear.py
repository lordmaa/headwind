"""Gear: bikes, ride assignment (dates / ranges), computed mileage, parts, service log, once-only alerts, photos. Throwaway DB, no network."""
import io
from datetime import date, timedelta

import pytest
from flask import Flask

import database
from services import gear

MI = gear.MI


@pytest.fixture
def db(tmp_path):
    app = Flask(__name__)
    app.config['DATABASE'] = str(tmp_path / 't.db')
    with app.app_context():
        database.migrate_db()
        d = database.get_db()
        d.execute("INSERT INTO Rider(name,isDefault) VALUES ('Rob',1)")
        d.execute("INSERT INTO Rider(name,isDefault) VALUES ('Leanne',0)")
        d.commit()
        yield d


def dbpath(db):
    return db.execute('PRAGMA database_list').fetchone()[2]


def ride(db, rid, day, miles=20.0, rider=1, sport='Ride', bike=None, hh='09:00:00'):
    db.execute("INSERT INTO Activity (id,name,type,sportType,startDate,startDateLocal,distance,movingTime,elapsedTime,totalElevationGain,averageSpeed,maxSpeed,riderId,bikeId,createdAt,updatedAt)"
               " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,'n','n')", [rid, rid, sport, sport, f'{day}T{hh}', f'{day}T{hh}', miles * MI, 3600, 3700, 100, 6, 10, rider, bike])
    db.commit()


def bike_of(db, rid):
    return db.execute('SELECT bikeId FROM Activity WHERE id=?', [rid]).fetchone()[0]


def test_schema_is_idempotent_and_adds_columns(db):
    gear.ensure_schema(db); gear.ensure_schema(db)
    assert 'bikeId' in {r[1] for r in db.execute('PRAGMA table_info(Activity)')} and 'defaultBikeId' in {r[1] for r in db.execute('PRAGMA table_info(Rider)')}


def test_first_bike_becomes_default_and_archiving_clears_it(db):
    a = gear.create_bike(db, 1, 'Roubaix'); b = gear.create_bike(db, 1, 'Gravel', kind='gravel')
    assert db.execute('SELECT defaultBikeId FROM Rider WHERE id=1').fetchone()[0] == a
    gear.set_default(db, 1, b); assert db.execute('SELECT defaultBikeId FROM Rider WHERE id=1').fetchone()[0] == b
    gear.archive_bike(db, b); assert db.execute('SELECT defaultBikeId FROM Rider WHERE id=1').fetchone()[0] is None
    with pytest.raises(ValueError):
        gear.set_default(db, 1, b)                      # archived bikes cannot be the default
    with pytest.raises(ValueError):
        gear.create_bike(db, 1, '  ')


def test_new_rides_get_the_default_bike_only_if_they_are_rides_and_unset(db):
    a = gear.create_bike(db, 1, 'Roubaix'); other = gear.create_bike(db, 1, 'Spare')
    ride(db, 'r1', '2026-10-01'); ride(db, 'w1', '2026-10-01', sport='Walk'); ride(db, 'r2', '2026-10-02', bike=other)
    assert gear.stamp_new_ride(db, 'r1') == a and bike_of(db, 'r1') == a
    assert gear.stamp_new_ride(db, 'w1') is None and bike_of(db, 'w1') is None          # a walk has no bike
    assert gear.stamp_new_ride(db, 'r2') is None and bike_of(db, 'r2') == other           # never overrides a choice
    gear.set_default(db, 1, other)
    ride(db, 'r3', '2026-10-03'); gear.stamp_new_ride(db, 'r3')
    assert bike_of(db, 'r3') == other and bike_of(db, 'r1') == a                           # changing the default does not rewrite history


def test_assign_by_dates_and_range_with_preview(db):
    a = gear.create_bike(db, 1, 'Roubaix')
    for i, d in enumerate(['2026-01-05', '2026-01-06', '2026-02-01', '2026-03-10', '2026-03-10']):
        ride(db, f'r{i}', d, hh=f'0{i}:00:00')
    ride(db, 'walk', '2026-01-05', sport='Walk'); ride(db, 'other', '2026-01-05', rider=2)
    prev = gear.assign_rides(db, 1, a, dates=['2026-01-05'], dry_run=True)
    assert prev['matched'] == 1 and prev['changed'] == 1 and bike_of(db, 'r0') is None       # a preview changes nothing
    gear.assign_rides(db, 1, a, dates=['2026-01-05', '2026-03-10'])
    assert [bike_of(db, f'r{i}') for i in range(5)] == [a, None, None, a, a]
    r = gear.assign_rides(db, 1, a, date_from='2026-02-01', date_to=None)                   # "from this day to present"
    assert r['changed'] == 1 and bike_of(db, 'r2') == a                                     # r3/r4 were already on it
    assert bike_of(db, 'walk') is None and bike_of(db, 'other') is None                      # walks and other riders are never touched
    with pytest.raises(ValueError):
        gear.assign_rides(db, 1, a)                                                          # nothing selected


def test_only_unassigned_protects_existing_choices_but_overwrite_replaces_them(db):
    a = gear.create_bike(db, 1, 'A'); b = gear.create_bike(db, 1, 'B')
    ride(db, 'r1', '2026-05-01', bike=a); ride(db, 'r2', '2026-05-02')
    assert gear.assign_rides(db, 1, b, date_from='2026-05-01', date_to='2026-05-31', only_unassigned=True)['changed'] == 1
    assert bike_of(db, 'r1') == a and bike_of(db, 'r2') == b
    assert gear.assign_rides(db, 1, b, date_from='2026-05-01', date_to='2026-05-31', only_unassigned=False)['changed'] == 1 and bike_of(db, 'r1') == b
    gear.assign_rides(db, 1, None, dates=['2026-05-01'], only_unassigned=False); assert bike_of(db, 'r1') is None      # clear


def test_cannot_assign_to_someone_elses_bike(db):
    theirs = gear.create_bike(db, 2, 'Hers')
    ride(db, 'r1', '2026-05-01')
    with pytest.raises(ValueError):
        gear.assign_rides(db, 1, theirs, dates=['2026-05-01'])
    with pytest.raises(ValueError):
        gear.set_ride_bike(db, 'r1', theirs)


def test_calendar_month_counts(db):
    a = gear.create_bike(db, 1, 'A')
    ride(db, 'r1', '2026-02-27', bike=a); ride(db, 'r2', '2026-02-27', hh='15:00:00'); ride(db, 'r3', '2026-03-01')
    m = gear.calendar_month(db, 1, 2026, 2)
    assert m == {'2026-02-27': {'n': 2, 'unassigned': 1, 'bikes': {str(a): 1}}}
    assert gear.calendar_month(db, 1, 2026, 12) == {} and gear.ride_span(db, 1) == {'first': '2026-02-27', 'last': '2026-03-01', 'unassigned': 2, 'total': 3, 'first_unassigned': '2026-02-27'}


def test_odometer_and_part_mileage_follow_assignments(db):
    a = gear.create_bike(db, 1, 'A', start_meters=1000 * MI)
    ride(db, 'r1', '2026-03-01', miles=50, bike=a); ride(db, 'r2', '2026-04-01', miles=30, bike=a); ride(db, 'r3', '2026-04-02', miles=10)
    bike = gear.get_bike(db, a)
    assert round(gear.odometer(db, bike) / MI) == 1080
    pid = gear.add_part(db, a, 'chain', installed_on='2026-03-15', replace_m=2500 * MI)
    part = db.execute('SELECT * FROM Part WHERE id=?', [pid]).fetchone()
    assert round(gear.part_status(db, part)['meters'] / MI) == 30                              # only the ride after it was fitted
    gear.set_ride_bike(db, 'r3', a)
    assert round(gear.part_status(db, part)['meters'] / MI) == 40                              # re-assigning a ride corrects it, nothing stored to fix
    same_day = gear.add_part(db, a, 'cassette', installed_on='2026-04-01'); p2 = db.execute('SELECT * FROM Part WHERE id=?', [same_day]).fetchone()
    assert round(gear.part_status(db, p2)['meters'] / MI) == 40                                # a ride on the fitting day counts


def test_part_levels_by_distance_and_by_days(db):
    a = gear.create_bike(db, 1, 'A')
    ride(db, 'r1', '2026-06-01', miles=1000, bike=a)
    pid = gear.add_part(db, a, 'chain', installed_on='2026-05-01', check_m=1000 * MI, replace_m=2500 * MI)
    p = lambda: db.execute('SELECT * FROM Part WHERE id=?', [pid]).fetchone()
    on = date(2026, 6, 2)
    st = gear.part_status(db, p(), on)
    assert st['check']['level'] == 'due' and st['replace']['level'] == 'ok' and st['due'] == 'check'
    ride(db, 'r2', '2026-06-10', miles=1000, bike=a); ride(db, 'r3', '2026-06-20', miles=300, bike=a)
    assert gear.part_status(db, p(), date(2026, 6, 21))['replace']['level'] == 'soon' and gear.part_status(db, p(), date(2026, 6, 21))['due'] == 'check'
    ride(db, 'r4', '2026-06-25', miles=300, bike=a)
    assert gear.part_status(db, p(), date(2026, 6, 26))['replace']['level'] == 'due'
    sealant = gear.add_part(db, a, 'sealant', installed_on='2026-01-01', replace_days=120)
    s = db.execute('SELECT * FROM Part WHERE id=?', [sealant]).fetchone()
    assert gear.part_status(db, s, date(2026, 2, 15))['level'] == 'ok' and gear.part_status(db, s, date(2026, 5, 5))['level'] == 'due' and gear.part_status(db, s, date(2026, 6, 15))['level'] == 'overdue'


def test_checking_a_part_resets_the_check_clock_not_the_replace_clock(db):
    a = gear.create_bike(db, 1, 'A')
    ride(db, 'r1', '2026-06-01', miles=1200, bike=a)
    pid = gear.add_part(db, a, 'chain', installed_on='2026-05-01', check_m=1000 * MI, replace_m=2500 * MI)
    gear.log_service(db, a, 'inspected', on='2026-06-02', part_id=pid)
    st = gear.part_status(db, db.execute('SELECT * FROM Part WHERE id=?', [pid]).fetchone(), date(2026, 6, 3))
    assert st['check']['level'] == 'ok' and round(st['meters'] / MI) == 1200


def test_replacing_a_part_retires_it_and_fits_a_fresh_one_with_the_same_intervals(db):
    a = gear.create_bike(db, 1, 'A')
    ride(db, 'r1', '2026-06-01', miles=2600, bike=a)
    pid = gear.add_part(db, a, 'chain', name='KMC X11', installed_on='2026-05-01', replace_m=2500 * MI)
    res = gear.log_service(db, a, 'replaced', on='2026-06-05', part_id=pid, cost=35, new_part={'name': 'KMC X11 (new)'})
    old = db.execute('SELECT * FROM Part WHERE id=?', [pid]).fetchone(); new = db.execute('SELECT * FROM Part WHERE id=?', [res['new_part']]).fetchone()
    assert old['retiredOn'] == '2026-06-05' and new['installedOn'] == '2026-06-05' and new['replaceEveryM'] == 2500 * MI and new['name'] == 'KMC X11 (new)'
    assert [x['id'] for x in gear.part_rows(db, a)] == [res['new_part']] and len(gear.part_rows(db, a, include_retired=True)) == 2
    assert round(gear.part_status(db, new, date(2026, 6, 6))['meters']) == 0
    assert gear.service_log(db, a)[0]['partName'] == 'KMC X11'
    assert gear.log_service(db, a, 'replaced', on='2026-06-06', part_id=res['new_part'], new_part=False)['new_part'] is None


def test_alerts_fire_once_per_level_and_reset_after_a_check(db):
    a = gear.create_bike(db, 1, 'Roubaix')
    pid = gear.add_part(db, a, 'chain', installed_on='2026-01-01', check_m=1000 * MI, replace_m=None)
    ride(db, 'r1', '2026-02-01', miles=920, bike=a)                       # 92% -> soon
    n1 = gear.evaluate_alerts(db, send=False)
    assert [(x['what'], x['level']) for x in n1] == [('check', 'soon')] and 'Roubaix' in n1[0]['text'] and 'check' in n1[0]['text']
    assert gear.evaluate_alerts(db, send=False) == []                     # nothing new: no repeat
    ride(db, 'r2', '2026-02-10', miles=100, bike=a)                       # 102% -> due
    assert [(x['what'], x['level']) for x in gear.evaluate_alerts(db, send=False)] == [('check', 'due')]
    assert gear.evaluate_alerts(db, send=False) == []
    ride(db, 'r3', '2026-03-01', miles=300, bike=a)                       # 132% -> overdue
    assert [x['level'] for x in gear.evaluate_alerts(db, send=False)] == ['overdue']
    gear.log_service(db, a, 'inspected', on='2026-03-02', part_id=pid)     # looked at it: a new cycle starts
    assert gear.evaluate_alerts(db, send=False) == []
    ride(db, 'r4', '2026-04-01', miles=1000, bike=a)
    assert [x['level'] for x in gear.evaluate_alerts(db, send=False)] == ['due']       # the next cycle alerts again


def test_alert_jumps_straight_to_the_current_level_in_one_message(db):
    a = gear.create_bike(db, 1, 'A'); gear.add_part(db, a, 'chain', installed_on='2026-01-01', replace_m=1000 * MI)
    ride(db, 'r1', '2026-02-01', miles=1500, bike=a)
    got = gear.evaluate_alerts(db, send=False)
    assert len(got) == 1 and got[0]['level'] == 'overdue'


def test_muted_and_archived_parts_stay_quiet(db):
    a = gear.create_bike(db, 1, 'A'); gear.add_part(db, a, 'chain', installed_on='2026-01-01', replace_m=100 * MI, notify=False)
    ride(db, 'r1', '2026-02-01', miles=500, bike=a)
    assert gear.evaluate_alerts(db, send=False) == []
    gear.update_part(db, 1, notify=True); gear.archive_bike(db, a)
    assert gear.evaluate_alerts(db, send=False) == []


def test_due_items_and_bike_health(db):
    a = gear.create_bike(db, 1, 'A'); gear.add_part(db, a, 'chain', installed_on='2026-01-01', replace_m=100 * MI); gear.add_part(db, a, 'cassette', installed_on='2026-01-01', replace_m=100000 * MI)
    ride(db, 'r1', '2026-02-01', miles=500, bike=a)
    items = gear.due_items(db, 1)
    assert [i['part']['kind'] for i in items] == ['chain'] and gear.bike_health(db, a) == {'level': 'overdue', 'attention': 1, 'soon': 0}


def test_delete_bike_unassigns_rides_and_removes_its_parts(db):
    a = gear.create_bike(db, 1, 'A'); ride(db, 'r1', '2026-02-01', bike=a); gear.add_part(db, a, 'chain'); gear.log_service(db, a, 'cleaned')
    gear.delete_bike(db, a)
    assert bike_of(db, 'r1') is None and db.execute('SELECT COUNT(*) FROM Part').fetchone()[0] == 0 and db.execute('SELECT COUNT(*) FROM ServiceLog').fetchone()[0] == 0
    assert db.execute('SELECT defaultBikeId FROM Rider WHERE id=1').fetchone()[0] is None


def test_bike_stats(db):
    a = gear.create_bike(db, 1, 'A')
    ride(db, 'r1', '2025-12-31', miles=10, bike=a); ride(db, 'r2', '2026-01-02', miles=30, bike=a); ride(db, 'r3', '2026-01-03', miles=20)
    s = gear.bike_stats(db, a)
    assert s['rides'] == 2 and round(s['distance'] / MI) == 40 and s['first'] == '2025-12-31' and s['last'] == '2026-01-02'
    assert [(y['year'], y['rides']) for y in s['by_year']] == [('2025', 1), ('2026', 1)] and s['avg_speed'] == pytest.approx(40 * MI / 7200)


def test_photo_is_resized_to_jpeg_old_one_removed_and_junk_rejected(db):
    from PIL import Image
    a = gear.create_bike(db, 1, 'A')
    buf = io.BytesIO(); Image.new('RGB', (3000, 2000), (10, 200, 30)).save(buf, 'PNG')
    n1 = gear.save_photo(db, a, buf.getvalue(), dbpath(db))
    assert gear.valid_photo_name(n1)
    with Image.open(__import__('os').path.join(gear.photo_dir(dbpath(db)), n1)) as im:       # closed again: Windows cannot delete a file that is still open
        assert max(im.size) <= 1400 and im.format == 'JPEG'
    n2 = gear.save_photo(db, a, buf.getvalue(), dbpath(db))
    assert n2 != n1 and not __import__('os').path.exists(__import__('os').path.join(gear.photo_dir(dbpath(db)), n1))
    with pytest.raises(ValueError):
        gear.save_photo(db, a, b'not an image', dbpath(db))
    gear.remove_photo(db, a, dbpath(db)); assert db.execute('SELECT photo FROM Bike WHERE id=?', [a]).fetchone()[0] is None
    assert not gear.valid_photo_name('../../etc/passwd') and not gear.valid_photo_name('bike_1_zzzzzzzz.jpg')


def test_bike_photos_are_part_of_backup_and_restore(db, tmp_path):
    """A backup zip carries bikeimg/ and restore stages it (and ignores look-alike names that could escape the folder)."""
    import sqlite3
    import zipfile
    from PIL import Image
    from services import backup
    a = gear.create_bike(db, 1, 'A'); db.commit()
    buf = io.BytesIO(); Image.new('RGB', (200, 100), (1, 2, 3)).save(buf, 'PNG')
    name = gear.save_photo(db, a, buf.getvalue(), dbpath(db)); db.commit()
    snap = str(tmp_path / 'snap.db'); sqlite3.connect(dbpath(db)).backup(sqlite3.connect(snap))
    z = str(tmp_path / 'b.zip')
    with zipfile.ZipFile(z, 'w') as zf:
        zf.write(snap, 'headwind.db'); zf.write(__import__('os').path.join(gear.photo_dir(dbpath(db)), name), f'bikeimg/{name}')
        zf.writestr('bikeimg/../../evil.jpg', b'x'); zf.writestr('bikeimg/notabike.jpg', b'x')

    class FS:
        def save(self, path): __import__('shutil').copy(z, path)
    tmp, staged, assets = backup.stage_upload(FS())
    try:
        assert sorted(__import__('os').listdir(assets['bikeimg'])) == [name]
    finally:
        __import__('shutil').rmtree(tmp, ignore_errors=True)
