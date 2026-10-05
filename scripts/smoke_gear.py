"""End-to-end smoke test for Gear on a THROWAWAY instance (brand-new DB): bikes + photo, default bike stamped on imported rides, the per-ride dropdown, calendar assignment
(preview + apply), parts / service log / alerts, backup export. Never touches your data. Run:
  d=$(mktemp -d); printf "DATABASE_URL=$d/t.db\\nSECRET_KEY=x\\nAPP_USERNAME=t\\nAPP_PASSWORD=t\\n" > $d/env; HEADWIND_ENV=$d/env PYTHONPATH=. python3 scripts/smoke_gear.py
"""
import io, os, sqlite3, zipfile
from datetime import datetime, timedelta, timezone
from app import create_app
a = create_app(); c = a.test_client()
db = a.config['DATABASE']
assert db.startswith(('/tmp', os.environ.get('TMPDIR', '/tmp'))), 'refusing to run against a non-temp database: ' + db
con = sqlite3.connect(db); con.row_factory = sqlite3.Row
con.execute("INSERT OR IGNORE INTO Settings (id) VALUES (1)"); con.execute("INSERT INTO Rider (name,isDefault) VALUES ('Tester',1)"); con.commit()
def ok(label, cond, extra=''): print(('PASS ' if cond else 'FAIL ') + label, extra); assert cond, label
from services import credentials
with c.session_transaction() as s: s['logged_in'] = True; s['av'] = credentials.auth_version()
MI = 1609.344

def gpx(start_utc, n=200, step=15):
    pts = ''.join(f'<trkpt lat="{54.9 + i * 0.0007:.6f}" lon="-1.38"><ele>30</ele><time>{(start_utc + timedelta(seconds=step * i)).strftime("%Y-%m-%dT%H:%M:%SZ")}</time></trkpt>' for i in range(n))
    return ('<?xml version="1.0"?><gpx version="1.1" creator="t" xmlns="http://www.topografix.com/GPX/1.1"><trk><name>Ride</name><type>cycling</type><trkseg>' + pts + '</trkseg></trk></gpx>').encode()
tok = c.post('/phones/create', json={'name': 'Pixel', 'server_url': 'https://x.example.com'}).get_json()['token']
phone = a.test_client()
def upload(start_utc):       # a ride arriving the way the phone app sends one: same import pipeline as a file import or a Garmin sync
    return phone.post('/api/v1/rides', headers={'Authorization': 'Bearer ' + tok}, data={'file': (io.BytesIO(gpx(start_utc)), 'ride.gpx')}, content_type='multipart/form-data')

# pages render with nothing set up
for u in ('/gear/', '/gear/bike/new', '/gear/assign'): ok(f'{u} renders empty', c.get(u).status_code == 200)

# 1) bikes, photo, default
from PIL import Image
png = io.BytesIO(); Image.new('RGB', (1600, 900), (20, 120, 200)).save(png, 'PNG')
r = c.post('/gear/bike/save', data={'name': 'Roubaix', 'kind': 'road', 'brand': 'Specialized', 'year': '2022', 'start': '1000', 'rider': '1', 'photo': (io.BytesIO(png.getvalue()), 'bike.png')}, content_type='multipart/form-data')
ok('bike saved with a photo', r.status_code == 302 and con.execute('select photo from Bike where id=1').fetchone()[0].startswith('bike_1_'))
ph = con.execute('select photo from Bike where id=1').fetchone()[0]
ok('photo is served', c.get(f'/gear/photo/{ph}').status_code == 200 and c.get('/gear/photo/../../etc/passwd').status_code == 404 and c.get('/gear/photo/bike_1_zzzzzzzz.jpg').status_code == 404)
c.post('/gear/bike/save', data={'name': 'Gravel', 'kind': 'gravel', 'rider': '1'})
ok('first bike is the default', con.execute('select defaultBikeId from Rider where id=1').fetchone()[0] == 1)
ok('bad input is a redirect with a message, not a 500', c.post('/gear/bike/save', data={'name': '', 'rider': '1'}).status_code == 302)

# 2) rides arrive: stamped with the default bike
t0 = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)
for i in range(3): upload(t0 + timedelta(days=i))
rides = [r[0] for r in con.execute('select id from Activity order by startDateLocal')]
ok('3 rides imported', len(rides) == 3)
ok('imported rides got the default bike', [r[0] for r in con.execute('select bikeId from Activity order by startDateLocal')] == [1, 1, 1])
c.post('/gear/default', data={'rider': '1', 'bike': '2'})
upload(t0 + timedelta(days=10))
last = con.execute('select id, bikeId from Activity order by startDateLocal desc limit 1').fetchone()
ok('changing the default affects only NEW rides', last['bikeId'] == 2 and con.execute('select count(*) from Activity where bikeId=1').fetchone()[0] == 3)

# 3) ride page dropdown
page = c.get(f"/rides/{rides[0]}")
ok('ride page shows the bike dropdown', page.status_code == 200 and b'Which bike did you ride?' in page.data and b'Roubaix' in page.data)
r = c.post(f'/gear/ride/{rides[0]}/bike', data={'bike': '2'}); ok('change the bike on a ride', r.status_code == 302 and con.execute('select bikeId from Activity where id=?', [rides[0]]).fetchone()[0] == 2)
c.post(f'/gear/ride/{rides[0]}/bike', data={'bike': '1'})

# 4) calendar assignment: preview, apply, clear
j = c.get('/gear/api/calendar?year=2026&month=9').get_json()['days']
ok('calendar shows ride days with their bikes', '2026-09-01' in j and j['2026-09-01']['bikes'] == {'1': 1})
con.execute('update Activity set bikeId=NULL'); con.commit()
pv = c.post('/gear/api/assign', json={'bike': 1, 'from': '2026-09-02', 'to': None, 'dry_run': True}).get_json()
ok('preview: "from 2 Sep to present" matches 3 rides, changes nothing', pv['matched'] == 3 and pv['changed'] == 3 and con.execute('select count(*) from Activity where bikeId is not null').fetchone()[0] == 0, pv)
ap = c.post('/gear/api/assign', json={'bike': 1, 'dates': ['2026-09-01'], 'from': '2026-09-02', 'to': None}).get_json()
ok('apply: one picked day + a to-present range', ap['changed'] == 4 and con.execute('select count(*) from Activity where bikeId=1').fetchone()[0] == 4, ap)
ok('assign errors are clean', c.post('/gear/api/assign', json={'bike': 1}).status_code == 400 and c.post('/gear/api/assign', json={'bike': 99, 'dates': ['2026-09-01']}).status_code == 400)

# 5) parts, service, alerts
r = c.post('/gear/bike/1/part', data={'kind': 'chain', 'installedOn': '2026-09-01', 'replaceEvery': '20', 'checkEvery': '10', 'notify': '1'})
ok('part added', r.status_code == 302 and con.execute('select count(*) from Part').fetchone()[0] == 1)
ok('alert was remembered once (not repeated on every page view)', con.execute('select count(*) from PartAlert').fetchone()[0] >= 1)
before = con.execute('select count(*) from PartAlert').fetchone()[0]; c.get('/gear/bike/1'); c.get('/gear/')
ok('viewing pages does not create alerts', con.execute('select count(*) from PartAlert').fetchone()[0] == before)
bikepage = c.get('/gear/bike/1'); ok('bike page shows the part and its status', bikepage.status_code == 200 and b'Chain' in bikepage.data and b'Service log' in bikepage.data)
ok('due items on the overview', c.get('/gear/').status_code == 200 and b'Needs attention' in c.get('/gear/').data)
ok('dashboard shows a gear card when something is due', b'Gear needs attention' in c.get('/dashboard').data)
ok('rider page has the default-bike picker', b'Default bike for new rides' in c.get('/riders/1').data)
r = c.post('/gear/bike/1/service', data={'action': 'replaced', 'part': '1', 'date': '2026-09-20', 'cost': '35', 'fit_new': '1', 'new_name': 'New chain'})
ok('replacing a part retires it and fits a new one', r.status_code == 302 and con.execute("select count(*) from Part where retiredOn is not null").fetchone()[0] == 1 and con.execute("select count(*) from Part where retiredOn is null").fetchone()[0] == 1)
ok('service log lists it', b'Replaced' in c.get('/gear/bike/1').data)

# 6) backup export carries the photo
r = c.get('/settings/backup/export'); z = zipfile.ZipFile(io.BytesIO(r.data)) if r.status_code == 200 else None
ok('backup export contains the bike picture', z is not None and any(n.startswith('bikeimg/bike_1_') for n in z.namelist()), r.status_code)

# 7) deleting a bike unassigns its rides
r = c.post('/gear/bike/2/delete'); ok('delete a bike: rides stay, photo/parts go', r.status_code == 302 and con.execute('select count(*) from Activity').fetchone()[0] == 4)
print('ALL PASS')
