"""Retried (queued-offline) food + water logs are applied once. Throwaway instance only:
  d=$(mktemp -d); printf "DATABASE_URL=$d/t.db\nSECRET_KEY=x\nAPP_USERNAME=t\nAPP_PASSWORD=t\n" > $d/env; HEADWIND_ENV=$d/env PYTHONPATH=. python3 scripts/smoke_client_ops.py"""
import sqlite3
from app import create_app
a = create_app(); c = a.test_client()
con = sqlite3.connect(a.config['DATABASE']); con.execute("INSERT OR IGNORE INTO Settings (id) VALUES (1)"); con.execute("INSERT INTO Rider (name,isDefault) VALUES ('Tester',1)"); con.commit()
from services import credentials
with c.session_transaction() as s: s['logged_in'] = True; s['av'] = credentials.auth_version()
def ok(label, cond, extra=''): print(('PASS ' if cond else 'FAIL ') + label, extra); assert cond, label
food = {'date': '2026-09-25', 'name': 'Test oats', 'calories': 200, 'meal_type': 'breakfast', 'client_id': 'cid-food-1'}
r1 = c.post('/nutrition/api/log', json=food).get_json(); r2 = c.post('/nutrition/api/log', json=food).get_json()
n = con.execute("select count(*) from FoodLog where foodName='Test oats'").fetchone()[0]
ok('food log retried with same client_id -> one row', n == 1 and r2.get('duplicate') and r1['id'] == r2['id'], (r1, r2))
c.post('/nutrition/api/log', json=dict(food, client_id='cid-food-2'))
ok('different client_id -> a second row', con.execute("select count(*) from FoodLog where foodName='Test oats'").fetchone()[0] == 2)
c.post('/nutrition/api/log', json={k: v for k, v in food.items() if k != 'client_id'})
ok('no client_id still works (web app / old app)', con.execute("select count(*) from FoodLog where foodName='Test oats'").fetchone()[0] == 3)
w = {'date': '2026-09-25', 'ml': 250, 'client_id': 'cid-water-1'}
c.post('/nutrition/api/water', json=w); c.post('/nutrition/api/water', json=w)
ok('water retried -> one row', con.execute("select count(*) from HydrationLog").fetchone()[0] == 1)
print('ALL PASS')
