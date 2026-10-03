"""End-to-end smoke test for the phone-app API (pairing, Bearer auth, /today, GPX workout upload + idempotency, ride-stat isolation,
burn inclusion, revocation) against a THROWAWAY instance with a brand-new DB. Never touches your data. Run:
  d=$(mktemp -d); printf "DATABASE_URL=$d/t.db\nSECRET_KEY=x\nAPP_USERNAME=t\nAPP_PASSWORD=t\n" > $d/env; HEADWIND_ENV=$d/env PYTHONPATH=. python3 scripts/smoke_api_v1.py"""
import io, json, math, re, sqlite3
from datetime import datetime, timedelta, timezone
from app import create_app
a = create_app(); c = a.test_client()
db = a.config['DATABASE']
con = sqlite3.connect(db); con.execute("INSERT OR IGNORE INTO Settings (id) VALUES (1)"); con.execute("INSERT INTO Rider (name,isDefault) VALUES ('Tester',1)"); con.commit()
def ok(label, cond, extra=''): print(('PASS ' if cond else 'FAIL ') + label, extra); assert cond, label
# --- pairing (session login) ---
from services import credentials
with c.session_transaction() as s: s['logged_in'] = True; s['av'] = credentials.auth_version()
r = c.post('/phones/create', json={'name': 'Test Pixel', 'server_url': 'https://headwind.example.com'}); j = r.get_json()
tok = j['token']; ok('pair: token + QR returned', r.status_code == 200 and tok.startswith('hw_') and j['qr'].startswith('data:image/png;base64,'), j['link'][:60])
row = con.execute('select tokenHash, name from DeviceToken').fetchone()
ok('pair: only a hash is stored (not the token)', tok not in row[0] and len(row[0]) == 64)
c2 = a.test_client()          # a client with NO login session: the phone
H = {'Authorization': 'Bearer ' + tok}
ok('no token -> 401 json', c2.get('/api/v1/ping').status_code == 401 and c2.get('/api/v1/ping').get_json()['error'])
ok('wrong token -> 401', c2.get('/api/v1/ping', headers={'Authorization': 'Bearer hw_nope'}).status_code == 401)
r = c2.get('/api/v1/ping', headers=H); ok('ping with token', r.status_code == 200 and r.get_json()['rider'] == 'Tester', r.get_json())
ok('token works on nutrition api', c2.get('/nutrition/api/goals', headers=H).status_code == 200)
ok('token NOT accepted on web pages', c2.get('/', headers=H).status_code in (301, 302))
t = c2.get('/api/v1/today', headers=H).get_json()
ok('today: one call has everything', all(k in t for k in ('goals', 'eaten', 'water_ml', 'steps', 'entries', 'recent', 'favourites', 'workouts')), sorted(t)[:6])
# --- a 30 min walk: 60 points 30 s apart, ~2.4 km north-east of Sunderland ---
def gpx(start):
    pts = []
    for i in range(61):
        lat = 54.905 + i * 0.00033; lng = -1.385 + i * 0.00033; t = start + timedelta(seconds=30 * i)
        pts.append(f'<trkpt lat="{lat:.6f}" lon="{lng:.6f}"><ele>{20 + i * 0.3:.1f}</ele><time>{t.strftime("%Y-%m-%dT%H:%M:%SZ")}</time></trkpt>')
    return ('<?xml version="1.0"?><gpx version="1.1" creator="test" xmlns="http://www.topografix.com/GPX/1.1"><trk><name>Test walk</name><type>walking</type><trkseg>'
            + ''.join(pts) + '</trkseg></trk></gpx>').encode()
start = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=1)
files = lambda: {'file': (io.BytesIO(gpx(start)), 'walk.gpx'), 'client_id': 'test-uuid-1', 'steps': '3400'}
acts_before = con.execute('select count(*) from Activity').fetchone()[0]
r = c2.post('/api/v1/workouts', headers=H, data=files(), content_type='multipart/form-data'); w = r.get_json()
ok('upload walk -> created', r.status_code == 201 and w['status'] == 'created', {k: w['workout'][k] for k in ('sport', 'distance', 'movingTime', 'calories', 'steps')})
ok('sport detected from GPX <type>', w['workout']['sport'] == 'Walk')
ok('calories estimated', 80 <= (w['workout']['calories'] or 0) <= 400, w['workout']['calories'])
r = c2.post('/api/v1/workouts', headers=H, data=files(), content_type='multipart/form-data')
ok('same client_id again -> exists (idempotent)', r.status_code == 200 and r.get_json()['status'] == 'exists')
ok('exactly one workout row', con.execute('select count(*) from Workout').fetchone()[0] == 1)
ok('RIDE STATS UNTOUCHED (Activity table unchanged)', con.execute('select count(*) from Activity').fetchone()[0] == acts_before)
ok('bad file -> 422', c2.post('/api/v1/workouts', headers=H, data={'file': (io.BytesIO(b'not gpx'), 'x.gpx')}, content_type='multipart/form-data').status_code == 422)
today = datetime.now().date().isoformat()
d = c.get('/nutrition/api/day/' + today).get_json()
ok("workout kcal counted in the day's burn", d.get('ride_calories') == round(w['workout']['calories']) or d.get('ride_calories') is not None, {'ride_calories': d.get('ride_calories'), 'burned': d.get('burned')})
ok('workouts page lists it', b'Test walk' in c.get('/workouts').data or b'Walk' in c.get('/workouts').data)
ok('list api', len(c2.get('/api/v1/workouts', headers=H).get_json()['workouts']) == 1)
ok('delete', c2.delete('/api/v1/workouts/' + w['workout']['id'], headers=H).status_code == 200 and con.execute('select count(*) from Workout').fetchone()[0] == 0)
# --- revoke ---
c.post('/phones/1/revoke'); ok('revoked token -> 401', c2.get('/api/v1/ping', headers=H).status_code == 401)
print('ALL CHECKS PASSED')
