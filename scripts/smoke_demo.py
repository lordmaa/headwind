"""End-to-end check of PUBLIC DEMO MODE on a throwaway instance (new empty DB). Proves the published login works, the dangerous things are refused, the harmless things work, the
counter counts browsers and not bots, and rate limiting kicks in. Run:
  d=$(mktemp -d); printf "DATABASE_URL=$d/t.db\\nSECRET_KEY=x\\nAPP_USERNAME=test\\nAPP_PASSWORD=test\\n" > $d/env; HEADWIND_DEMO=1 HEADWIND_ENV=$d/env DEMO_COUNTER_DB=$d/c.db DEMO_RATE_PER_MIN=200 PYTHONPATH=. python3 scripts/smoke_demo.py
"""
import io, os, sqlite3, tempfile
from app import create_app
assert os.environ.get('HEADWIND_DEMO') == '1'
a = create_app(); c = a.test_client()
db = a.config['DATABASE']
assert os.path.realpath(db).startswith(os.path.realpath(tempfile.gettempdir())), 'refusing to run against a non-temp database: ' + db
con = sqlite3.connect(db); con.execute("INSERT OR IGNORE INTO Settings (id) VALUES (1)"); con.execute("INSERT INTO Rider (name,isDefault) VALUES ('Alex',1)"); con.commit()
def ok(label, cond, extra=''): print(('PASS ' if cond else 'FAIL ') + label, extra); assert cond, label
CHROME = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36'}
H = {'Host': 'localhost', **CHROME}

r = c.get('/login', headers=H); ok('login page shows the demo notice, prefilled test/test and the counter', r.status_code == 200 and b'value="test"' in r.data and b'/demo/counter.gif' in r.data and b'visitor number' in r.data)
r = c.post('/login', data={'username': 'test', 'password': 'test'}, headers=H); ok('the published login works', r.status_code == 302)
ok('wrong password does not', a.test_client().post('/login', data={'username': 'test', 'password': 'nope'}, headers=H).status_code == 200)
ok('the demo banner is on every page', b'LIVE DEMO' in c.get('/dashboard', headers=H).data)

# refused
for label, method, url, kw in [
    ('change the password', 'post', '/settings/password', {'data': {'current': 'test', 'new': 'hacked', 'confirm': 'hacked'}}),
    ('restore a backup (file upload)', 'post', '/settings/backup/import', {'data': {'file': (io.BytesIO(b'x'), 'x.db')}, 'content_type': 'multipart/form-data'}),
    ('download the whole database', 'get', '/settings/backup/export', {}),
    ('save settings (keys, MQTT, URLs)', 'post', '/settings/', {'data': {'mqttHost': 'evil.example.com'}}),
    ('add a friend (makes the server fetch a URL)', 'post', '/friends/add', {'data': {'url': 'http://169.254.169.254/'}}),
    ('probe a URL', 'post', '/friends/probe', {'json': {'url': 'http://127.0.0.1:22/'}}),
    ('import a ride file', 'post', '/import/upload', {'data': {'file': (io.BytesIO(b'x'), 'x.gpx')}, 'content_type': 'multipart/form-data'}),
    ('connect Garmin', 'post', '/garmin/connect', {'json': {}}),
    ('create a phone token', 'post', '/phones/create', {'json': {'name': 'x'}}),
    ('heavy segment scan', 'post', '/segments/scan', {}),
    ('delete a rider', 'post', '/riders/1/delete', {}),
    ('proxy third-party map tiles', 'get', '/heatmap/tile/5/1/1', {}),
    ('run the setup wizard', 'post', '/setup', {'data': {'riders[]': 'x'}}),
    ('the AI estimate (outbound)', 'post', '/nutrition/api/estimate-meal', {'json': {}}),
    ('phone API', 'post', '/api/v1/activities', {'json': {}}),
]:
    r = getattr(c, method)(url, headers=H, **kw); ok(f'REFUSED: {label}', r.status_code == 403, r.status_code)
ok('the password did not change', sqlite3.connect(db).execute('select count(*) from Settings').fetchone()[0] == 1)

# allowed
today = __import__('datetime').date.today().isoformat()
r = c.post('/nutrition/api/log', json={'date': today, 'name': 'Demo toast', 'calories': 120, 'meal_type': 'breakfast'}, headers=H); ok('ALLOWED: log a food', r.status_code in (200, 201), r.status_code)
ok('ALLOWED: add water', c.post('/nutrition/api/water', json={'date': today, 'ml': 250}, headers=H).status_code in (200, 201))
ok('ALLOWED: add a bike', c.post('/gear/bike/save', data={'name': 'Demo bike', 'rider': '1'}, headers=H).status_code == 302)
ok('bike picture uploads are ignored', c.post('/gear/bike/1/photo', data={'photo': (io.BytesIO(b'x'), 'x.png')}, headers=H, content_type='multipart/form-data').status_code == 302)
for u in ('/dashboard', '/gear/', '/nutrition/', '/segments', '/heatmap', '/data', '/workouts', '/settings/'):
    ok(f'page renders: {u}', c.get(u, headers=H).status_code == 200)

# counter: browsers count (once), bots and refreshes do not
def count(): return sqlite3.connect(os.environ['DEMO_COUNTER_DB']).execute('select n from counter').fetchone()[0]
c0 = count() if os.path.exists(os.environ['DEMO_COUNTER_DB']) else 0
g = c.get('/demo/counter.gif', headers=H); ok('counter image is a GIF', g.status_code == 200 and g.data[:3] == b'GIF' and 'no-store' in g.headers['Cache-Control'])
ok('a browser was counted once', count() == c0 + 1)
c.get('/demo/counter.gif', headers=H); ok('a refresh is not counted again', count() == c0 + 1)
c.get('/demo/counter.gif', headers={'Host': 'localhost', 'User-Agent': 'Googlebot/2.1'}); c.get('/demo/counter.gif', headers={'Host': 'localhost', 'User-Agent': 'curl/8'})
ok('bots are not counted', count() == c0 + 1)
ok('the counter is public (no login)', a.test_client().get('/demo/counter.gif', headers=H).status_code == 200 and a.test_client().get('/demo/health', headers=H).get_json()['ok'])
ok('no login = no pages', a.test_client().get('/dashboard', headers=H).status_code == 302)

# rate limit
cc = a.test_client(); cc.post('/login', data={'username': 'test', 'password': 'test'}, headers=H)
codes = [cc.get('/gear/', headers={**H, 'CF-Connecting-IP': '203.0.113.9'}).status_code for _ in range(260)]
ok('rate limiting kicks in for a hammering client', 429 in codes and codes[0] == 200, codes.count(429))
ok("...but not for a different client", cc.get('/gear/', headers={**H, 'CF-Connecting-IP': '198.51.100.7'}).status_code == 200)
print('ALL PASS')
