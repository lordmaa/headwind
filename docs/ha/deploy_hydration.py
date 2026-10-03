"""Creates the hydration helpers, scripts and automations in Home Assistant (idempotent)."""
import json, sys
import requests
import hawss

PHONE = 'notify.mobile_app_pixel_10'
TOPIC = 'headwind/cmd/water'
ID_TPL = '{{ now().timestamp() }}-{{ range(10000, 99999) | random }}'
WATER, GOAL = 'sensor.headwind_nutrition_water', 'sensor.headwind_nutrition_water_goal'
SNOOZE, LASTP = 'input_datetime.headwind_water_snooze', 'input_datetime.headwind_water_last_prompt'

url, tok = hawss.creds()
H = {'Authorization': f'Bearer {tok}', 'Content-Type': 'application/json'}
url = url.rstrip('/')


def post(path, body):
    r = requests.post(url + path, headers=H, json=body, timeout=30)
    if not r.ok:
        sys.exit(f'{path} -> {r.status_code} {r.text[:300]}')
    return r.json() if r.text else {}


# ── helpers (state only; created once) ──
(st,) = hawss.call({'type': 'get_states'})
have = {s['entity_id'] for s in st['result']}
for name in ('Headwind water snooze', 'Headwind water last prompt'):
    eid = 'input_datetime.' + name.lower().replace(' ', '_')
    if eid not in have:
        r, = hawss.call({'type': 'input_datetime/create', 'name': name, 'has_date': True, 'has_time': True, 'icon': 'mdi:cup-water'})
        print('helper', eid, r.get('success'), r.get('error') or '')

# ── one-tap scripts (Android home-screen widget buttons + dashboard buttons) ──
for ml in (150, 250, 500):
    post(f'/api/config/script/config/headwind_add_water_{ml}', {
        'alias': f'Headwind: drank {ml} ml', 'icon': 'mdi:cup-water', 'mode': 'queued',
        'sequence': [{'action': 'mqtt.publish', 'data': {'topic': TOPIC, 'payload': '{"ml": %d, "id": "%s"}' % (ml, ID_TPL)}}],
    })

# ── the reminder ──
behind = ("{% set goal = states('" + GOAL + "')|float(0) %}{% set have = states('" + WATER + "')|float(0) %}"
          "{% set h = now().hour + now().minute / 60 %}{% set frac = [0, [(h - 8) / 13.5, 1]|min]|max %}"
          "{{ goal > 0 and have < goal and have < goal * frac - 200 }}")
snooze_over = ("{% set s = states('" + SNOOZE + "') %}{{ s in ['unknown', 'unavailable', ''] or as_timestamp(s, 0) < as_timestamp(now()) }}")
gap_ok = ("{{ as_timestamp(now()) - as_timestamp(states('" + LASTP + "'), 0) > 3300 }}")
amount = ("{% set goal = states('" + GOAL + "')|float(0) %}{% set have = states('" + WATER + "')|float(0) %}"
          "{% set h = now().hour + now().minute / 60 %}{% set frac = [0, [(h - 8) / 13.5, 1]|min]|max %}"
          "{% set need = goal * frac - have %}{{ [500, [250, ((need / 50)|round(0)) * 50]|max]|min|int }}")

post('/api/config/automation/config/headwind_hydration_prompt', {
    'alias': 'Headwind: hydration prompt',
    'description': 'Every 10 min between 08:00 and 21:30: if you are behind the day\'s water pace, ask you to drink now. Snooze and 1-hour spacing are respected.',
    'mode': 'single',
    'triggers': [{'trigger': 'time_pattern', 'minutes': '/10'}],
    'conditions': [
        {'condition': 'time', 'after': '08:00:00', 'before': '21:30:00'},
        {'condition': 'template', 'value_template': behind},
        {'condition': 'template', 'value_template': snooze_over},
        {'condition': 'template', 'value_template': gap_ok},
    ],
    'actions': [
        {'variables': {'amt': amount}},
        {'action': 'input_datetime.set_datetime', 'target': {'entity_id': LASTP}, 'data': {'timestamp': '{{ as_timestamp(now()) }}'}},
        {'action': PHONE, 'data': {
            'title': '💧 Time for some water',
            'message': "Drink {{ amt }} ml now — you're at {{ states('" + WATER + "')|int(0) }} of {{ states('" + GOAL + "')|int(0) }} ml.",
            'data': {'tag': 'headwind-water', 'channel': 'Hydration', 'importance': 'high', 'color': '#60a5fa', 'ttl': 0, 'priority': 'high',
                     'actions': [{'action': 'HW_WATER_DONE_{{ amt }}', 'title': 'Done ✓ +{{ amt }} ml'},
                                 {'action': 'HW_WATER_SNOOZE', 'title': 'Remind me in 30 min'}]}}},
    ],
})

# ── the button handler ──
post('/api/config/automation/config/headwind_hydration_action', {
    'alias': 'Headwind: hydration notification buttons',
    'description': 'Done adds the water to Headwind; Remind me in 30 min snoozes the prompts.',
    'mode': 'queued',
    'triggers': [{'trigger': 'event', 'event_type': 'mobile_app_notification_action'}],
    'conditions': [{'condition': 'template', 'value_template': "{{ trigger.event.data.action.startswith('HW_WATER') }}"}],
    'actions': [{'choose': [
        {'conditions': [{'condition': 'template', 'value_template': "{{ trigger.event.data.action.startswith('HW_WATER_DONE_') }}"}],
         'sequence': [
             {'variables': {'ml': "{{ trigger.event.data.action.split('_')[-1] | int(250) }}"}},
             {'action': 'mqtt.publish', 'data': {'topic': TOPIC, 'payload': '{"ml": {{ ml }}, "id": "{{ trigger.event.context.id | default(now().timestamp(), true) }}"}'}},
             {'action': PHONE, 'data': {'message': "Logged +{{ ml }} ml ✓  ({{ states('" + WATER + "')|int(0) + ml }} / {{ states('" + GOAL + "')|int(0) }} ml)",
                                        'data': {'tag': 'headwind-water', 'timeout': 8, 'channel': 'Hydration'}}},
         ]},
        {'conditions': [{'condition': 'template', 'value_template': "{{ trigger.event.data.action == 'HW_WATER_SNOOZE' }}"}],
         'sequence': [
             {'action': 'input_datetime.set_datetime', 'target': {'entity_id': SNOOZE}, 'data': {'timestamp': '{{ as_timestamp(now()) + 1800 }}'}},
         ]},
    ]}],
})

for dom in ('script', 'automation'):
    post(f'/api/services/{dom}/reload', {})
print('deployed: 3 scripts, 2 automations, helpers ok')
