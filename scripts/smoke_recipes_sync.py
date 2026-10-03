"""Smoke test for the phone <-> web recipes bridge (SavedMeal, UI-labelled "Recipes"): a recipe saved on the web appears in the
phone's /api/v1/sync/nutrition pull, and the phone's Bearer token can delete it via the existing /nutrition/api/saved-meals routes.
Runs against a THROWAWAY instance with a brand-new DB. Never touches your data. Run:
  d=$(mktemp -d); printf "DATABASE_URL=$d/t.db\nSECRET_KEY=x\nAPP_USERNAME=t\nAPP_PASSWORD=t\n" > $d/env; HEADWIND_ENV=$d/env PYTHONPATH=. python3 scripts/smoke_recipes_sync.py"""
import sqlite3
from app import create_app
a = create_app(); c = a.test_client()
db = a.config['DATABASE']
con = sqlite3.connect(db); con.execute("INSERT OR IGNORE INTO Settings (id) VALUES (1)"); con.execute("INSERT INTO Rider (name,isDefault) VALUES ('Tester',1)"); con.commit()
def ok(label, cond, extra=''): print(('PASS ' if cond else 'FAIL ') + label, extra); assert cond, label

from services import credentials
with c.session_transaction() as s: s['logged_in'] = True; s['av'] = credentials.auth_version()
tok = c.post('/phones/create', json={'name': 'Test Pixel', 'server_url': 'https://headwind.example.com'}).get_json()['token']
c2 = a.test_client()          # the phone: no session, only the Bearer token
H = {'Authorization': 'Bearer ' + tok}

# --- "Build a meal" on the web: save a recipe with two ingredients ---
body = {'name': 'zz-test-chilli', 'items': [
    {'foodName': 'Mince 500g', 'calories': 900, 'protein': 80, 'carbs': 0, 'fat': 65, 'servingG': 500},
    {'foodName': 'Kidney beans', 'calories': 280, 'protein': 18, 'carbs': 45, 'fat': 1, 'servingG': 400},
]}
r = c.post('/nutrition/api/saved-meals', json=body); j = r.get_json()
ok('create recipe (web session)', r.status_code == 201 and j['name'] == 'zz-test-chilli', j)
meal_id = j['id']
ok('list shows it with a summed total', any(m['id'] == meal_id and m['totalCal'] == 1180 and len(m['items']) == 2 for m in c.get('/nutrition/api/saved-meals').get_json()))

# --- the phone pulls it via the sync bridge, using only its Bearer token ---
sync = c2.get('/api/v1/sync/nutrition', headers=H).get_json()
ok('sync payload has saved_meals', 'saved_meals' in sync)
mirrored = next((m for m in sync['saved_meals'] if m['id'] == meal_id), None)
ok('recipe present in the phone sync pull', mirrored is not None, sync.get('saved_meals'))
ok('items + macros survive the trip', mirrored and len(mirrored['items']) == 2 and mirrored['totalCal'] == 1180, mirrored)
# --- log it: the existing /log endpoint already works with the phone's token ---
today = __import__('datetime').date.today().isoformat()
r = c2.post(f'/nutrition/api/saved-meals/{meal_id}/log', headers=H, json={'date': today, 'meal_type': 'dinner', 'scale': 1.0})
ok('phone can log the recipe with its token', r.status_code == 200 and r.get_json().get('ok'), r.get_json())
ok('two FoodLog rows created', con.execute("select count(*) from FoodLog where logDate=? and mealType='dinner'", [today]).fetchone()[0] == 2)

# --- phone deletes it (mirrors Repo.deleteSavedMeal's server_id path) ---
r = c2.delete(f'/nutrition/api/saved-meals/{meal_id}', headers=H)
ok('phone can delete the recipe with its token', r.status_code == 204)
ok('gone from the web list too', not any(m['id'] == meal_id for m in c.get('/nutrition/api/saved-meals').get_json()))
ok('gone from the next sync pull', not any(m['id'] == meal_id for m in c2.get('/api/v1/sync/nutrition', headers=H).get_json()['saved_meals']))

print('ALL CHECKS PASSED')
