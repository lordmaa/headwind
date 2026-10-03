"""The 6-step /setup wizard (riders -> units -> Garmin -> Home Assistant -> telemetry -> done), /garmin/connect +
/garmin/connect/mfa, and the one-time telemetry ping/opt-out. Throwaway instance only:
  d=$(mktemp -d); printf "DATABASE_URL=$d/t.db\nSECRET_KEY=x\nAPP_USERNAME=t\nAPP_PASSWORD=t\n" > $d/env; HEADWIND_ENV=$d/env PYTHONPATH=. python3 scripts/smoke_setup_wizard.py"""
import sqlite3
from app import create_app
a = create_app(); c = a.test_client()
db_path = a.config['DATABASE']
from services import credentials
with c.session_transaction() as s: s['logged_in'] = True; s['av'] = credentials.auth_version()
def ok(label, cond, extra=''): print(('PASS ' if cond else 'FAIL ') + label, extra); assert cond, label

r = c.get('/setup')
ok('GET /setup (no riders) renders step 1', r.status_code == 200 and b'Your name' in r.data)

r = c.post('/setup', data={'riders[]': ['Alex']}, follow_redirects=False)
ok('POST /setup creates rider -> redirect step 2', r.status_code == 302 and 'step=2' in r.headers['Location'])

r = c.get('/setup', follow_redirects=False)
ok('bare /setup (rider exists) redirects to dashboard', r.status_code == 302 and '/dashboard' in r.headers['Location'])
r = c.post('/setup', data={'riders[]': ['Intruder']}, follow_redirects=False)
ok('POST /setup (rider exists) also redirects, no second rider created', r.status_code == 302 and '/dashboard' in r.headers['Location'])
con = sqlite3.connect(db_path)
ok('still exactly one rider', con.execute('SELECT COUNT(*) FROM Rider').fetchone()[0] == 1)

r = c.get('/setup?step=2')
ok('step 2 still reachable after step 1', r.status_code == 200 and b'Miles' in r.data)
r = c.post('/setup?step=2', data={'units': 'metric'}, follow_redirects=False)
ok('POST step 2 saves units -> redirect step 3', r.status_code == 302 and 'step=3' in r.headers['Location'])
con2 = sqlite3.connect(db_path)
ok('units persisted', con2.execute('SELECT units FROM Settings WHERE id=1').fetchone() == ('metric',))

r = c.get('/setup?step=3')
ok('step 3 renders Garmin form', r.status_code == 200 and b'garminConnect' in r.data)

r = c.get('/setup?step=4')
ok('step 4 renders Home Assistant form', r.status_code == 200 and b'haUrl' in r.data and b'weather entity' in r.data.lower())

# --- HA entity listing includes weather-domain entities (for the weather picker), not just sensors ---
import services.homeassistant as ha
def fake_states(url, token, path, params=None, timeout=15):
    assert path == '/api/states'
    return [
        {'entity_id': 'sensor.scale_weight', 'state': '78.2', 'attributes': {'friendly_name': 'Scale Weight', 'unit_of_measurement': 'kg'}},
        {'entity_id': 'weather.home', 'state': 'cloudy', 'attributes': {'friendly_name': 'Home', 'temperature': 14.2}},
        {'entity_id': 'light.kitchen', 'state': 'on', 'attributes': {}},
    ]
ha._get = fake_states
ents = ha.list_entities('http://fake', 'tok')
ok('list_entities includes the weather entity', any(e['entity_id'] == 'weather.home' and e['domain'] == 'weather' for e in ents))
ok('list_entities excludes unrelated domains (light)', not any(e['entity_id'].startswith('light.') for e in ents))

r = c.get('/setup?step=5')
ok('step 5 renders telemetry disclosure', r.status_code == 200 and b'one-time' in r.data.lower() and b'Opt out forever' in r.data)

# --- opt out: no ping ever sent, flag recorded, ping_once() becomes a permanent no-op ---
import services.telemetry as tel
pinged = {'n': 0}
def fake_post(url, json=None, timeout=None):
    pinged['n'] += 1
    class R: ok = True
    return R()
tel.requests.post = fake_post
r = c.post('/setup?step=5', data={'telemetry': 'off'}, follow_redirects=False)
ok('POST step 5 (opt out) -> redirect step 6', r.status_code == 302 and 'step=6' in r.headers['Location'])
con3 = sqlite3.connect(db_path)
ok('telemetryOptOut recorded', con3.execute('SELECT telemetryOptOut FROM Settings WHERE id=1').fetchone() == (1,))
ok('no ping was sent', pinged['n'] == 0)
with a.app_context():
    tel.ping_once()  # even called directly, opt-out must block it forever
ok('ping_once() is a permanent no-op after opt-out', pinged['n'] == 0)

r = c.get('/setup?step=6')
ok('step 6 renders done screen', r.status_code == 200 and b'dashboard' in r.data.lower())

print('ALL PASS')
