import json, re, sys
import hawss, build_dashboard
cfg = build_dashboard.build()
blob = json.dumps(cfg)
used = sorted(set(re.findall(r"\b(?:sensor|binary_sensor)\.[a-z0-9_]+", blob)))
(states,) = hawss.call({'type': 'get_states'})
have = {s['entity_id'] for s in states['result']}
missing = [e for e in used if e not in have]
print(f'{len(used)} entities referenced, missing: {missing or "none"}')
if missing: sys.exit(1)
res, = hawss.call({'type': 'lovelace/dashboards/list'})
exists = any(d['url_path'] == 'rob-health' for d in res['result'])
cmds = []
if not exists:
    cmds.append({'type': 'lovelace/dashboards/create', 'url_path': 'rob-health', 'mode': 'storage', 'title': 'Rob',
                 'icon': 'mdi:heart-pulse', 'show_in_sidebar': True, 'require_admin': False})
cmds.append({'type': 'lovelace/config/save', 'url_path': 'rob-health', 'config': cfg})
for r in hawss.call(*cmds): print(r.get('success'), r.get('error') or '')
