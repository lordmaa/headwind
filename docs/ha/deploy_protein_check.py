"""Evening protein check: one quiet notification at 20:00 if there's a meaningful protein gap AND room in the calories."""
import sys, requests, hawss
PHONE = 'notify.mobile_app_pixel_10'
P_LEFT, K_LEFT, P_GOAL = 'sensor.headwind_nutrition_protein_remaining', 'sensor.headwind_nutrition_calories_remaining', 'sensor.headwind_nutrition_protein_goal'
url, tok = hawss.creds()
H = {'Authorization': f'Bearer {tok}', 'Content-Type': 'application/json'}
url = url.rstrip('/')
body = {
    'alias': 'Headwind: evening protein check',
    'description': 'At 20:00: if 25 g+ of protein is still to go and there are 150 kcal+ left, send one heads-up. Silent otherwise.',
    'mode': 'single',
    'triggers': [{'trigger': 'time', 'at': '20:00:00'}],
    'conditions': [
        {'condition': 'template', 'value_template': "{{ states('%s')|float(0) >= 25 and states('%s')|float(0) >= 150 }}" % (P_LEFT, K_LEFT)},
        # only if a normal day has been logged — otherwise the gap is forgotten logging, not low protein
        {'condition': 'template', 'value_template': "{{ states('sensor.headwind_nutrition_calories_eaten')|float(0) >= 1200 }}"},
    ],
    'actions': [{'action': PHONE, 'data': {
        'title': '🍗 Protein check',
        'message': "{{ states('%s')|int(0) }} g of protein to go, with {{ states('%s')|int(0) }} kcal still available today." % (P_LEFT, K_LEFT),
        'data': {'tag': 'headwind-protein', 'channel': 'Coaching', 'color': '#facc15'}}}],
}
r = requests.post(url + '/api/config/automation/config/headwind_protein_check', headers=H, json=body, timeout=30)
if not r.ok: sys.exit(f'{r.status_code} {r.text[:200]}')
requests.post(url + '/api/services/automation/reload', headers=H, json={}, timeout=30)
print('automation deployed')
