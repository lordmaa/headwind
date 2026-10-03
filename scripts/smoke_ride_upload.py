"""POST /api/v1/rides (phone-recorded cycling ride -> real Activity, not Workout) end to end. Throwaway instance only:
  d=$(mktemp -d); printf "DATABASE_URL=$d/t.db\nSECRET_KEY=x\nAPP_USERNAME=t\nAPP_PASSWORD=t\n" > $d/env; HEADWIND_ENV=$d/env PYTHONPATH=. python3 scripts/smoke_ride_upload.py"""
import sqlite3
import time
from app import create_app

a = create_app()
c = a.test_client()
db_path = a.config['DATABASE']
con = sqlite3.connect(db_path)
con.execute("INSERT OR IGNORE INTO Settings (id) VALUES (1)")
con.execute("INSERT INTO Rider (name,isDefault) VALUES ('Tester',1)")
con.commit()
rider_id = con.execute("SELECT id FROM Rider").fetchone()[0]
con.execute("INSERT INTO WeightLog (riderId, logDate, weightKg) VALUES (?, '2026-09-28', 80.0)", [rider_id])
con.commit()

from services import device_auth
with a.app_context():
    token, device_id = device_auth.create('smoke-test-device')
auth = {'Authorization': f'Bearer {token}'}


def ok(label, cond, extra=''):
    print(('PASS ' if cond else 'FAIL ') + label, extra)
    assert cond, label


# --- a GPX shaped EXACTLY like Gpx.kt's writer: plain trkpts, no extensions, <type>cycling</type> ---
def make_gpx(start_iso, n_points, lat0=54.87, lng0=-1.68, step=0.0006, name='Ride'):
    import datetime
    start = datetime.datetime.fromisoformat(start_iso)
    pts = []
    for i in range(n_points):
        t = (start + datetime.timedelta(seconds=i * 5)).isoformat().replace('+00:00', 'Z')
        pts.append(f'<trkpt lat="{lat0 + i * step:.6f}" lon="{lng0 + i * step:.6f}"><ele>{100 + i:.1f}</ele><time>{t}</time></trkpt>')
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<gpx version="1.1" creator="Headwind Android" xmlns="http://www.topografix.com/GPX/1.1">\n'
            f'<trk><name>{name}</name><type>cycling</type><trkseg>\n' + '\n'.join(pts) + '\n</trkseg></trk></gpx>\n').encode()


gpx1 = make_gpx('2026-09-29T08:00:00', 400, step=0.00025)  # comfortably over 8047 m — clears the 5-mile BestEffort bracket

r = c.post('/api/v1/rides', headers=auth, data={'file': (__import__('io').BytesIO(gpx1), 'ride.gpx')}, content_type='multipart/form-data')
j = r.get_json()
ok('upload succeeds', r.status_code == 200, (r.status_code, j))
ok('status=imported', j.get('status') == 'imported', j)
ok('imported=1', j.get('imported') == 1, j)
ride1 = j.get('ride')
ok('ride payload present with id/name/distance', ride1 and ride1.get('id') and ride1.get('name') == 'Ride' and ride1.get('distance', 0) > 0, ride1)

row = con.execute("SELECT sportType, riderId, distance, calories FROM Activity WHERE id=?", [ride1['id']]).fetchone()
ok('landed in Activity (not Workout) with sportType=Ride', row is not None and row[0] == 'Ride' and row[1] == rider_id, row)
ok('calories estimated (GPX has none of its own, but the rider has a weigh-in on file)', row[3] is not None and row[3] > 0, row[3])

# --- day_burn() must actually see this ride, not just the Activity row existing — this was the whole point of
# estimating calories at all (day_burn filters WHERE calories IS NOT NULL, silently dropping un-estimated rides) ---
from services.goal_model import day_burn
con.execute("UPDATE Settings SET sex='male', birthYear=1990, heightCm=180 WHERE id=1")
con.commit()
with a.app_context():
    burn = day_burn(rider_id, '2026-09-29')
ok('day_burn sees the ride (not silently dropped)', burn is not None and burn[1] == 'daily burn + activity', burn)
n_workouts = con.execute("SELECT COUNT(*) FROM Workout").fetchone()[0]
ok('Workout table untouched', n_workouts == 0, n_workouts)

# --- retry the exact same file (network drop before the app deleted its local copy) -> idempotent, not duplicated ---
r2 = c.post('/api/v1/rides', headers=auth, data={'file': (__import__('io').BytesIO(gpx1), 'ride.gpx')}, content_type='multipart/form-data')
j2 = r2.get_json()
ok('retry -> status=skipped', j2.get('status') == 'skipped', j2)
ok('retry -> same ride id reported back', j2.get('ride', {}).get('id') == ride1['id'], j2)
n_activities = con.execute("SELECT COUNT(*) FROM Activity").fetchone()[0]
ok('still only one Activity row', n_activities == 1, n_activities)

# --- a different ride (different start time) -> a genuinely new Activity ---
gpx2 = make_gpx('2026-09-29T14:00:00', 30, name='Evening spin')
r3 = c.post('/api/v1/rides', headers=auth, data={'file': (__import__('io').BytesIO(gpx2), 'ride2.gpx')}, content_type='multipart/form-data')
j3 = r3.get_json()
ok('second distinct ride -> imported', j3.get('status') == 'imported' and j3.get('ride', {}).get('id') != ride1['id'], j3)
ok('now two Activity rows', con.execute("SELECT COUNT(*) FROM Activity").fetchone()[0] == 2)

# --- best efforts got computed (proves the full pipeline ran, not just an Activity insert) ---
n_efforts = con.execute("SELECT COUNT(*) FROM BestEffort").fetchone()[0]
ok('BestEffort rows were computed for the upload', n_efforts > 0, n_efforts)

# --- garbage upload (no file) -> clean 400, not a 500 ---
r4 = c.post('/api/v1/rides', headers=auth, content_type='multipart/form-data')
ok('missing file -> 400', r4.status_code == 400, r4.get_json())

# --- revoked token -> 401, never touches the DB ---
with a.app_context():
    device_auth.revoke(device_id)
r5 = c.post('/api/v1/rides', headers=auth, data={'file': (__import__('io').BytesIO(gpx1), 'ride.gpx')}, content_type='multipart/form-data')
ok('revoked token -> 401', r5.status_code == 401, r5.status_code)

print('ALL PASS')
