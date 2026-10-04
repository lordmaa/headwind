"""One-person-per-instance mode: wizard, /riders redirect, create blocked, auto multi-rider for existing multi-rider installs,
friend riders not counted, env switch. Throwaway instance only:
  d=$(mktemp -d); printf "DATABASE_URL=$d/t.db\\nSECRET_KEY=x\\nAPP_USERNAME=t\\nAPP_PASSWORD=t\\n" > $d/env; HEADWIND_ENV=$d/env PYTHONPATH=. python3 scripts/smoke_single_rider.py"""
import os
import sqlite3
from app import create_app
from services import credentials

a = create_app(); c = a.test_client()
with c.session_transaction() as s:
    s['logged_in'] = True; s['av'] = credentials.auth_version()
con = sqlite3.connect(a.config['DATABASE'])


def ok(label, cond, extra=''):
    print(('PASS ' if cond else 'FAIL ') + label, extra)
    assert cond, label


r = c.get('/setup')
ok('wizard step 1 is the single-person explainer', b'One person per Headwind' in r.data and b'Add another rider' not in r.data)
c.post('/setup', data={'riders[]': ['Alex', 'Sneaky Second']})
names = [x[0] for x in con.execute('SELECT name FROM Rider')]
ok('only one rider created by the wizard', names == ['Alex'], names)
con.execute("INSERT OR IGNORE INTO Settings (id) VALUES (1)"); con.commit()

r = c.get('/riders', follow_redirects=False)
ok('/riders redirects to own profile', r.status_code == 302 and '/riders/1' in r.headers['Location'], r.headers.get('Location'))
ok('nav says Profile', b'Profile' in c.get('/riders/1').data and b'Delete Rider' not in c.get('/riders/1').data)
ok('creating a rider is blocked', c.post('/riders/create', data={'name': 'X'}).status_code == 403)

# a friend's rider (created by friend sync) must not flip the mode
con.execute("INSERT INTO Rider (name,isDefault) VALUES ('FriendRider',0)")
rid = con.execute("SELECT id FROM Rider WHERE name='FriendRider'").fetchone()[0]
cols = [x[1] for x in con.execute('PRAGMA table_info(Friend)')]
con.execute("INSERT INTO Friend (name,url,riderId) VALUES ('F','http://x',?)", [rid]); con.commit()
ok('friend rider does not enable multi-rider', c.get('/riders', follow_redirects=False).status_code == 302)

# an existing instance with 2 real local riders keeps the multi-rider UI automatically
con.execute("INSERT INTO Rider (name,isDefault) VALUES ('Second Local',0)"); con.commit()
r = c.get('/riders')
ok('2 local riders -> multi-rider auto-on (list page, "Riders" nav)', r.status_code == 200 and b'Add Rider' in r.data)
ok('create works in multi-rider mode', c.post('/riders/create', data={'name': 'Third'}).status_code == 302)

# env switch on a fresh single-rider DB view
con.execute("DELETE FROM Rider WHERE name IN ('Second Local','Third')"); con.commit()
ok('back to single', c.get('/riders', follow_redirects=False).status_code == 302)
os.environ['HEADWIND_MULTI_RIDER'] = '1'
ok('HEADWIND_MULTI_RIDER=1 forces multi-rider', c.get('/riders', follow_redirects=False).status_code == 200)
print('ALL OK')
