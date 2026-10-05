"""End-to-end smoke test: duplicate rides (phone GPX vs Garmin), the review actions, manual create/delete, and tombstones, against a THROWAWAY
instance with a brand-new DB. Never touches your data. Run:
  d=$(mktemp -d); printf "DATABASE_URL=$d/t.db\nSECRET_KEY=x\nAPP_USERNAME=t\nAPP_PASSWORD=t\n" > $d/env; HEADWIND_ENV=$d/env PYTHONPATH=. python3 scripts/smoke_duplicates_manual.py
"""
import io, os, sqlite3, tempfile
from datetime import datetime, timedelta, timezone
from app import create_app
a = create_app(); c = a.test_client()
db = a.config['DATABASE']
assert os.path.realpath(db).startswith(os.path.realpath(tempfile.gettempdir())), 'refusing to run against a non-temp database: ' + db
con = sqlite3.connect(db); con.row_factory = sqlite3.Row
con.execute("INSERT OR IGNORE INTO Settings (id) VALUES (1)"); con.execute("INSERT INTO Rider (name,isDefault) VALUES ('Tester',1)"); con.commit()
def ok(label, cond, extra=''): print(('PASS ' if cond else 'FAIL ') + label, extra); assert cond, label
from services import credentials
with c.session_transaction() as s: s['logged_in'] = True; s['av'] = credentials.auth_version()
tok = c.post('/phones/create', json={'name': 'Pixel', 'server_url': 'https://x.example.com'}).get_json()['token']
H = {'Authorization': 'Bearer ' + tok}; phone = a.test_client()

def gpx(start_utc, n=435, step=15):
    pts = ''.join(f'<trkpt lat="{54.9 + i * 0.000683:.6f}" lon="-1.38"><ele>{30 + (i % 50) * 0.4:.1f}</ele><time>{(start_utc + timedelta(seconds=step * i)).strftime("%Y-%m-%dT%H:%M:%SZ")}</time></trkpt>' for i in range(n))
    return ('<?xml version="1.0"?><gpx version="1.1" creator="t" xmlns="http://www.topografix.com/GPX/1.1"><trk><name>Ride</name><type>cycling</type><trkseg>' + pts + '</trkseg></trk></gpx>').encode()
def upload(start_utc, name='ride.gpx'):
    return phone.post('/api/v1/rides', headers=H, data={'file': (io.BytesIO(gpx(start_utc)), name)}, content_type='multipart/form-data')
live = lambda: [r[0] for r in con.execute('select id from Activity order by id')]

# 1) the real 2026-10-05 case: Garmin row (local BST time) already there, phone uploads the same ride in UTC an hour "earlier"
con.execute("INSERT INTO Activity (id,name,type,sportType,startDate,startDateLocal,distance,movingTime,elapsedTime,totalElevationGain,averageSpeed,maxSpeed,averageHeartrate,riderId,rawData,createdAt,updatedAt) "
            "VALUES ('garmin_1','County Durham Road Cycling','Ride','Ride','2026-10-05T10:30:53','2026-10-05T11:30:53',33285,6542,6600,464,5.1,9,140,1,'{}','n','n')"); con.commit()
r = upload(datetime(2026, 10, 5, 10, 29, 38, tzinfo=timezone.utc)); j = r.get_json()
ok('phone upload of the same ride -> duplicate', r.status_code == 200 and j['status'] == 'duplicate' and j['duplicate']['kept'] == 'garmin_1', j.get('duplicate'))
ok('only the Garmin ride is live; the phone copy is parked', live() == ['garmin_1'] and con.execute("select count(*) from ActivityDuplicate where status='parked'").fetchone()[0] == 1)
pid = con.execute("select id from ActivityDuplicate where status='parked'").fetchone()[0]
page = c.get('/rides/garmin_1'); ok('ride page shows the duplicate banner', page.status_code == 200 and b'also recorded by another device' in page.data)
ok('upload again is idempotent (no second parked row)', upload(datetime(2026, 10, 5, 10, 29, 38, tzinfo=timezone.utc)).status_code == 200 and con.execute('select count(*) from ActivityDuplicate').fetchone()[0] == 1)
r = c.post(f'/rides/garmin_1/duplicate/{pid}/swap'); ok('swap -> phone recording is now the ride', r.status_code == 302 and live() == [pid])
r = c.post(f'/rides/{pid}/duplicate/garmin_1/swap'); ok('swap back -> Garmin is the ride again', live() == ['garmin_1'])
r = c.post(f'/rides/garmin_1/duplicate/{pid}/keep'); ok('keep both -> two rides', sorted(live()) == sorted(['garmin_1', pid]))
ok('...and re-uploading does not merge them again', upload(datetime(2026, 10, 5, 10, 29, 38, tzinfo=timezone.utc)).get_json()['status'] in ('skipped', 'imported'))

# 2) a phone-only ride gets UTC + London-local times (BST): 10:00Z -> 11:00 local
t = datetime(2026, 8, 1, 10, 0, 0, tzinfo=timezone.utc); j = upload(t).get_json()
row = con.execute('select startDate,startDateLocal from Activity where id=?', [j['ride']['id']]).fetchone()
ok('phone ride stored as UTC instant + London local', row['startDate'] == '2026-08-01T10:00:00' and row['startDateLocal'] == '2026-08-01T11:00:00', dict(row))

# 3) manual create / delete
r = phone.post('/api/v1/activities', headers=H, json={'sport': 'Walk', 'date': '2026-10-04', 'time': '09:00', 'duration_s': 3600, 'distance_m': 4800, 'client_id': 'm1'})
ok('manual walk created', r.status_code == 201 and r.get_json()['kind'] == 'workout', r.get_json())
ok('manual walk is idempotent by client_id', phone.post('/api/v1/activities', headers=H, json={'sport': 'Walk', 'date': '2026-10-04', 'time': '09:00', 'duration_s': 3600, 'distance_m': 4800, 'client_id': 'm1'}).get_json()['status'] == 'exists')
r = phone.post('/api/v1/activities', headers=H, json={'sport': 'Ride', 'date': '2026-10-03', 'time': '17:30', 'duration_s': 2700, 'distance_m': 20000, 'elev_m': 120, 'name': 'Turbo', 'client_id': 'm2'})
mid = r.get_json()['id']; ok('manual ride created in Activity', r.status_code == 201 and mid.startswith('man_') and mid in live())
ok('manual ride page renders without a GPS track', c.get(f'/rides/{mid}').status_code == 200)
ok('bad input is a 400, not a crash', phone.post('/api/v1/activities', headers=H, json={'sport': 'Ride', 'date': '2999-01-01', 'duration_s': 60, 'distance_m': 1}).status_code == 400
   and phone.post('/api/v1/activities', headers=H, json={'sport': 'Swim', 'date': '2026-10-01', 'duration_s': 600, 'distance_m': 100}).status_code == 400)
w = con.execute("select id from Workout where source='manual'").fetchone()['id']
ok('workouts page lists it with a delete button', b'Delete' in c.get('/workouts').data and b'Add an activity' in c.get('/workouts').data)
r = c.post('/workouts/new', data={'sport': 'Run', 'date': '2026-10-02', 'time': '07:00', 'hours': '0', 'minutes': '40', 'distance': '5', 'unit': 'mi'}); ok('web form adds a run', r.status_code == 302 and con.execute("select count(*) from Workout").fetchone()[0] == 2)
ok('web form error is shown, not a 500', c.post('/workouts/new', data={'sport': 'Run', 'date': '2999-01-01', 'minutes': '40', 'distance': '5'}).status_code == 302)
r = c.post(f'/workouts/{w}/delete'); ok('web delete workout', r.status_code == 302 and con.execute('select count(*) from Workout where id=?', [w]).fetchone()[0] == 0)
r = phone.delete(f'/api/v1/rides/ride/{mid}', headers=H); ok('phone deletes a ride', r.status_code == 200 and mid not in live())
ok("...and it can't be deleted twice", phone.delete(f'/api/v1/rides/ride/{mid}', headers=H).status_code == 404)
from services import duplicates
with a.app_context():
    import database
    ok('deleted ride is remembered (Garmin re-sync will not bring it back)', duplicates.is_deleted(database.get_db(), mid))
# 4) the phone-facing duplicate API
con.execute("UPDATE Activity SET id='garmin_9' WHERE id='garmin_1'"); con.commit()
j = upload(datetime(2026, 9, 1, 8, 0, 0, tzinfo=timezone.utc)).get_json()      # a fresh phone ride...
con.execute("INSERT INTO Activity (id,name,type,sportType,startDate,startDateLocal,distance,movingTime,elapsedTime,totalElevationGain,averageSpeed,maxSpeed,averageHeartrate,riderId,rawData,createdAt,updatedAt) "
            "VALUES ('garmin_77','G','Ride','Ride','2026-09-01T08:00:30','2026-09-01T09:00:30',33300,6500,6540,10,5.1,9,150,1,'{}','n','n')"); con.commit()
with a.app_context():
    import database
    duplicates.resolve(database.get_db(), 'garmin_77'); database.get_db().commit()
d = phone.get('/api/v1/rides/ride/garmin_77', headers=H).get_json()
ok('ride detail lists the parked duplicate', len(d['duplicates']) == 1 and d['duplicates'][0]['source'] == 'Phone or file', d['duplicates'])
did = d['duplicates'][0]['id']
r = phone.post(f'/api/v1/rides/ride/garmin_77/duplicate/{did}/swap', headers=H); ok('phone can swap', r.status_code == 200 and r.get_json()['ride_id'] == did)
ok('wrong action -> 400, wrong ride -> 404', phone.post(f'/api/v1/rides/ride/{did}/duplicate/garmin_77/nope', headers=H).status_code == 400 and phone.post('/api/v1/rides/ride/zzz/duplicate/garmin_77/swap', headers=H).status_code == 404)
# 5) a walk recorded as a ride can be changed to a walk (API, ride page, workouts page)
slow = datetime(2026, 8, 20, 17, 0, 0, tzinfo=timezone.utc)
j = upload(slow).get_json(); rid = j['ride']['id']
r = phone.post(f'/api/v1/rides/ride/{rid}/sport', headers=H, json={'sport': 'Walk'}); body = r.get_json()
ok('phone: change a ride to a Walk', r.status_code == 200 and body['kind'] == 'workout' and body['changed'] and rid not in live(), body)
w = con.execute('select sport, calories from Workout where id=?', [body['id']]).fetchone()
ok('...it is now a workout with a walking calorie estimate', w['sport'] == 'Walk' and w['calories'] is not None)
ok('wrong type / unknown id / other kind are clean errors', phone.post(f"/api/v1/rides/workout/{body['id']}/sport", headers=H, json={'sport': 'Swim'}).status_code == 400
   and phone.post('/api/v1/rides/ride/nope/sport', headers=H, json={'sport': 'Walk'}).status_code == 404)
back = phone.post(f"/api/v1/rides/workout/{body['id']}/sport", headers=H, json={'sport': 'Ride'}).get_json()
ok('...and back to a ride', back['kind'] == 'ride' and back['id'] in live(), back)
r = c.post(f"/rides/{back['id']}/sport", data={'sport': 'Run'}); ok('web: ride page changes it to a Run (lands on Workouts)', r.status_code == 302 and '/workouts' in r.headers['Location'])
wid = con.execute("select id from Workout where sport='Run' and source='converted'").fetchone()[0]
r = c.post(f'/workouts/{wid}/sport', data={'sport': 'Walk'}); ok('web: workouts page changes it to a Walk', r.status_code == 302 and con.execute('select sport from Workout where id=?', [wid]).fetchone()[0] == 'Walk')
ok('the ride page shows the Type control', b'What kind of activity was this?' in c.get(f"/rides/{[x for x in live() if x.startswith('garmin') or x.startswith('imp_')][0]}").data)
print('ALL PASS')
