"""One-click Home Assistant dashboard for Headwind's sensors, built from HA's own cards only (no HACS needed).

Flow (see routes/homeassistant.py `/settings/home-assistant/dashboard`): read HA's entity registry, map each Headwind
sensor (`unique_id` = "<mqtt prefix>_<uid>") to whatever entity id HA actually gave it, build a Lovelace config from the
modules that exist, then create/update a dashboard over HA's websocket API.

`build()` is pure (entity resolver in, dict out) so it is unit-tested without a Home Assistant. This is the portable
cousin of docs/ha/build_dashboard.py, which stays the author's own bespoke, HACS-card dashboard.
"""
import asyncio
import json
import re
from urllib.parse import urlparse

DEFAULT_PATH = 'headwind-health'
OWNED_PREFIX = 'headwind-'          # we only ever create/replace dashboards under this prefix

# module -> the sensor uid that proves it exists
MODULES = {
    'nutrition': 'nutrition_calories_eaten',
    'body':      'nutrition_weight_trend_kg',
    'steps':     'nutrition_steps_today',
    'riding':    'total_rides',
    'recovery':  'garmin_body_battery',
    'live':      'live_ride_status',
}
MODULE_LABELS = {
    'nutrition': 'Food, water and calories', 'body': 'Weight trend', 'steps': 'Steps',
    'riding': 'Ride totals and last ride', 'recovery': 'Garmin recovery', 'live': 'Live ride',
}


class DashboardError(Exception):
    pass


# ── building (pure) ────────────────────────────────────────────────────────────────────────────

def detect(R):
    return [m for m, uid in MODULES.items() if R(uid)]


def _tile(R, uid, name=None, icon=None):
    e = R(uid)
    if not e:
        return None
    c = {'type': 'tile', 'entity': e}
    if name:
        c['name'] = name
    if icon:
        c['icon'] = icon
    return c


def _tiles(R, items):
    return [c for c in (_tile(R, *i) for i in items) if c]


def _heading(text, icon=None):
    c = {'type': 'heading', 'heading': text}
    if icon:
        c['icon'] = icon
    return c


def _gauge(R, uid, name, mx=100, severity=None):
    e = R(uid)
    if not e:
        return None
    return {'type': 'gauge', 'entity': e, 'name': name, 'min': 0, 'max': mx, 'needle': False,
            'severity': severity or {'green': 0, 'yellow': 100, 'red': 120}, 'grid_options': {'columns': 6}}


def _graph(R, uids, hours=168, names=None):
    ents = []
    for uid in uids:
        e = R(uid)
        if e:
            ents.append({'entity': e, 'name': (names or {}).get(uid) or e})
    if not ents:
        return None
    return {'type': 'history-graph', 'entities': ents, 'hours_to_show': hours, 'grid_options': {'columns': 12}}


def _section(*cards):
    cards = [c for c in cards if c]
    return {'type': 'grid', 'cards': cards} if len(cards) > 1 else None


def _macro_table(R):
    rows = []
    for label, eaten, goal, unit in (('Calories', 'nutrition_calories_eaten', 'nutrition_calories_goal', 'kcal'),
                                     ('Protein', 'nutrition_protein_g', 'nutrition_protein_goal', 'g'),
                                     ('Carbs', 'nutrition_carbs_g', 'nutrition_carbs_goal', 'g'),
                                     ('Fat', 'nutrition_fat_g', 'nutrition_fat_goal', 'g'),
                                     ('Fibre', 'nutrition_fibre_g', 'nutrition_fibre_goal', 'g'),
                                     ('Sugar (total)', 'nutrition_sugar_g', 'nutrition_sugar_goal', 'g')):
        e, g = R(eaten), R(goal)
        if e:
            goal_txt = f" / {{{{ states('{g}') | int(0) }}}}" if g else ''
            rows.append(f"| {label} | {{{{ states('{e}') | float(0) | round(0) | int }}}}{goal_txt} {unit} |")
    if not rows:
        return None
    return {'type': 'markdown', 'title': 'Today against your goals',
            'content': '| | eaten / goal |\n|---|---|\n' + '\n'.join(rows), 'grid_options': {'columns': 12}}


def _verdict_card(R):
    e = R('nutrition_weighin_verdict')
    if not e:
        return None
    tpl = (f"**{{{{ state_attr('{e}', 'headline') or 'No weigh-in verdict yet' }}}}**\n\n"
           f"{{% for r in (state_attr('{e}', 'reasons') or []) %}}- {{{{ r }}}}\n{{% endfor %}}")
    return {'type': 'markdown', 'title': 'Latest weigh-in', 'content': tpl, 'grid_options': {'columns': 12}}


def _meals_card(R):
    rows = []
    for key, label in (('breakfast', 'Breakfast'), ('lunch', 'Lunch'), ('dinner', 'Dinner'), ('snacks', 'Snacks'),
                       ('ride_fuel', 'Ride fuel'), ('recovery', 'Recovery'), ('drinks', 'Drinks')):
        e = R(f'nutrition_{key}_calories')
        if e:
            rows.append({'entity': e, 'name': label})
    return {'type': 'entities', 'title': 'Calories by meal', 'entities': rows, 'grid_options': {'columns': 12}} if rows else None


def _weeks_card(R):
    """The last eight weeks of riding as a small table with text bars (built-in markdown card; no charting add-on needed)."""
    e = R('nutrition_activity_weekly')
    if not e:
        return None
    tpl = (f"{{% set d = state_attr('{e}', 'd') or [] %}}{{% set mi = state_attr('{e}', 'mi') or [] %}}{{% set n = state_attr('{e}', 'rides') or [] %}}"
           "{% set top = ((mi[-8:] | max) if mi else 1) or 1 %}"
           "| Week of | Rides | Miles | |\n|---|---|---|---|\n"
           "{% for i in range([d | length - 8, 0] | max, d | length) | reverse %}"
           "| {{ as_timestamp(d[i]) | timestamp_custom('%d %b') }} | {{ n[i] }} | {{ mi[i] }} | {{ '█' * ((mi[i] / top * 12) | round(0) | int) }} |\n{% endfor %}")
    return {'type': 'markdown', 'title': 'Last eight weeks', 'content': tpl, 'grid_options': {'columns': 12}}


def view_today(R, mods):
    secs = []
    if 'nutrition' in mods:
        secs.append(_section(
            _heading('Food and water', 'mdi:food-apple'),
            _gauge(R, 'nutrition_calorie_pct', 'Calories used'),
            _gauge(R, 'nutrition_water_pct', 'Water', severity={'red': 0, 'yellow': 50, 'green': 90}),
            *_tiles(R, [('nutrition_calories_remaining', 'Calories left'), ('nutrition_calories_burned', 'Burned so far'),
                        ('nutrition_calories_net', 'Net calories'), ('nutrition_protein_remaining', 'Protein to go'),
                        ('nutrition_water_ml', 'Water')]),
            _macro_table(R), _meals_card(R)))
    if 'nutrition' in mods and R('nutrition_alcohol_week'):
        secs.append(_section(
            _heading('Alcohol', 'mdi:glass-wine'),
            _gauge(R, 'nutrition_alcohol_week', 'Units this week', mx=14, severity={'green': 0, 'yellow': 10, 'red': 14}),
            *_tiles(R, [('nutrition_alcohol_today', 'Today'), ('nutrition_alcohol_remaining', 'Left this week')])))
    if 'steps' in mods:
        secs.append(_section(
            _heading('Steps', 'mdi:shoe-print'),
            *_tiles(R, [('nutrition_steps_today', 'Today'), ('nutrition_steps_goal', 'Goal'), ('nutrition_steps_avg_7d', '7-day average')])))
    if 'body' in mods:
        secs.append(_section(
            _heading('Weight', 'mdi:scale-bathroom'),
            *_tiles(R, [('nutrition_weight_trend_kg', 'Trend'), ('nutrition_weight_rate_kg_wk', 'Change per week')]),
            _verdict_card(R),
            _graph(R, ['nutrition_weight_trend_kg'], hours=24 * 90, names={'nutrition_weight_trend_kg': 'Weight trend (kg)'})))
    secs = [s for s in secs if s]
    return {'type': 'sections', 'title': 'Today', 'path': 'today', 'icon': 'mdi:heart-pulse', 'max_columns': 3, 'sections': secs} if secs else None


def view_riding(R, mods):
    if 'riding' not in mods:
        return None
    secs = [
        _section(_heading('Lifetime', 'mdi:bike'),
                 *_tiles(R, [('total_rides', 'Rides'), ('total_distance_mi', 'Distance'), ('total_elevation_ft', 'Climbed'),
                             ('total_time_hours', 'Moving time'), ('everests_climbed', 'Everests'), ('laps_of_earth', 'Laps of Earth'),
                             ('badges_earned', 'Badges')])),
        _section(_heading('Last ride', 'mdi:flag-checkered'),
                 *_tiles(R, [('last_ride_name', 'Ride'), ('last_ride_date', 'Date'), ('last_ride_distance_mi', 'Distance'),
                             ('last_ride_moving_time', 'Time'), ('last_ride_avg_speed_mph', 'Average speed'),
                             ('last_ride_elevation_ft', 'Climbed'), ('last_ride_avg_hr', 'Average heart rate'),
                             ('last_ride_avg_watts', 'Average power'), ('last_ride_calories', 'Calories')])),
        _section(_heading('Training', 'mdi:chart-line-variant'),
                 *_tiles(R, [('nutrition_activity_status', 'Trend')]), _weeks_card(R)),
    ]
    secs = [s for s in secs if s]
    return {'type': 'sections', 'title': 'Riding', 'path': 'riding', 'icon': 'mdi:bike', 'max_columns': 2, 'sections': secs} if secs else None


def view_recovery(R, mods):
    if 'recovery' not in mods:
        return None
    sec = _section(
        _heading('Recovery', 'mdi:battery-heart-variant'),
        _gauge(R, 'garmin_body_battery', 'Body battery', severity={'red': 0, 'yellow': 30, 'green': 60}),
        *_tiles(R, [('garmin_resting_hr', 'Resting heart rate'), ('garmin_hrv', 'HRV'), ('garmin_sleep_hours', 'Sleep'),
                    ('garmin_sleep_score', 'Sleep score'), ('garmin_steps', 'Steps'), ('garmin_stress', 'Stress')]),
        *_tiles(R, [('nutrition_garmin_status', 'Garmin sync'), ('nutrition_garmin_watch_sync', 'Watch last synced')]),
        _graph(R, ['garmin_body_battery'], names={'garmin_body_battery': 'Body battery'}),
        _graph(R, ['garmin_hrv', 'garmin_resting_hr'], hours=24 * 30, names={'garmin_hrv': 'HRV', 'garmin_resting_hr': 'Resting HR'}))
    return {'type': 'sections', 'title': 'Recovery', 'path': 'recovery', 'icon': 'mdi:heart-flash', 'max_columns': 2, 'sections': [sec]} if sec else None


def view_live(R, mods):
    if 'live' not in mods:
        return None
    cards = [_heading('Live ride', 'mdi:broadcast')]
    tracker = R('live_ride_position')
    if tracker:
        cards.append({'type': 'map', 'entities': [tracker], 'hours_to_show': 1, 'default_zoom': 15,
                      'theme_mode': 'auto', 'grid_options': {'columns': 12, 'rows': 6}})
    cards += _tiles(R, [('live_ride_status', 'Status'), ('live_ride_speed_mph', 'Speed'), ('live_ride_distance_mi', 'Distance'),
                        ('live_ride_elapsed', 'Elapsed'), ('live_ride_elevation_ft', 'Elevation'), ('live_ride_phone_battery', 'Phone battery')])
    cards.append(_graph(R, ['live_ride_speed_mph'], hours=1, names={'live_ride_speed_mph': 'Speed'}))
    sec = _section(*cards)
    return {'type': 'sections', 'title': 'Live', 'path': 'live', 'icon': 'mdi:broadcast', 'max_columns': 2, 'sections': [sec]} if sec else None


def build(R, title='Headwind'):
    """Lovelace config for whichever Headwind modules `R` can resolve. R(uid) -> entity id or None."""
    mods = detect(R)
    views = [v for v in (view_today(R, mods), view_riding(R, mods), view_recovery(R, mods), view_live(R, mods)) if v]
    return {'title': title, 'views': views}, mods


def entities_used(cfg):
    return sorted(set(re.findall(r"\b(?:sensor|binary_sensor|device_tracker)\.[a-z0-9_]+", json.dumps(cfg))))


# ── Home Assistant side ────────────────────────────────────────────────────────────────────────

def ws_url(url):
    """Core websocket for a user-typed HA URL, or the Supervisor proxy when running as an add-on."""
    u = urlparse(url.strip())
    if not u.scheme or not u.netloc:
        raise DashboardError('Home Assistant URL looks wrong (it should start with http:// or https://).')
    scheme = 'wss' if u.scheme == 'https' else 'ws'
    base = f'{scheme}://{u.netloc}{u.path.rstrip("/")}'
    return base + ('/websocket' if u.netloc == 'supervisor' else '/api/websocket')


async def _call(url, token, cmds):
    try:
        import websockets
    except ImportError:
        raise DashboardError('The "websockets" package is not installed in this Headwind install.')
    out = []
    try:
        async with websockets.connect(ws_url(url), max_size=64 * 1024 * 1024, open_timeout=10) as ws:
            await asyncio.wait_for(ws.recv(), 10)
            await ws.send(json.dumps({'type': 'auth', 'access_token': token}))
            if json.loads(await asyncio.wait_for(ws.recv(), 10)).get('type') != 'auth_ok':
                raise DashboardError('Home Assistant rejected the token. Create a new long-lived access token and try again.')
            for i, cmd in enumerate(cmds, 1):
                await ws.send(json.dumps({'id': i, **cmd}))
                while True:
                    m = json.loads(await asyncio.wait_for(ws.recv(), 30))
                    if m.get('id') == i:
                        out.append(m)
                        break
    except DashboardError:
        raise
    except (OSError, asyncio.TimeoutError) as e:
        raise DashboardError(f'Could not reach Home Assistant at {url} ({e.__class__.__name__}).')
    except Exception as e:     # websockets.exceptions.* vary by version
        raise DashboardError(f'Home Assistant connection failed ({e.__class__.__name__}: {e}).')
    return out


def call(url, token, *cmds):
    return asyncio.run(_call(url, token, list(cmds)))


def make_resolver(registry, states, prefix):
    """uid -> entity id, via the MQTT unique_id ('<prefix>_<uid>'). Only entities that currently have a state count."""
    have = {s['entity_id'] for s in states}
    by_uid = {}
    head = prefix + '_'
    for e in registry:
        uq = e.get('unique_id') or ''
        if e.get('platform') == 'mqtt' and uq.startswith(head) and e['entity_id'] in have and not e.get('disabled_by'):
            by_uid[uq[len(head):]] = e['entity_id']
    return by_uid.get


def plan(url, token, prefix, title='Headwind'):
    reg, st, dash = call(url, token, {'type': 'config/entity_registry/list'}, {'type': 'get_states'}, {'type': 'lovelace/dashboards/list'})
    for r in (reg, st):
        if not r.get('success'):
            raise DashboardError(f"Home Assistant refused a read: {r.get('error', {}).get('message', 'unknown error')}")
    R = make_resolver(reg['result'], st['result'], prefix)
    cfg, mods = build(R, title)
    existing = [d['url_path'] for d in (dash.get('result') or [])] if dash.get('success') else []
    return {'config': cfg, 'modules': mods, 'entities': entities_used(cfg), 'existing_paths': existing}


def valid_path(p):
    return bool(re.fullmatch(r'headwind-[a-z0-9]+(-[a-z0-9]+)*', p or ''))


def deploy(url, token, path, title, cfg, icon='mdi:bike-fast'):
    if not valid_path(path):
        raise DashboardError(f'Dashboard address must look like "{DEFAULT_PATH}" (lowercase letters, numbers and hyphens, starting "{OWNED_PREFIX}").')
    if not cfg.get('views'):
        raise DashboardError('Nothing to build yet: Home Assistant has no Headwind sensors. Turn on MQTT in Headwind first.')
    (lst,) = call(url, token, {'type': 'lovelace/dashboards/list'})
    exists = any(d.get('url_path') == path for d in lst.get('result') or [])
    cmds = []
    if not exists:
        cmds.append({'type': 'lovelace/dashboards/create', 'url_path': path, 'mode': 'storage', 'title': title,
                     'icon': icon, 'show_in_sidebar': True, 'require_admin': False})
    cmds.append({'type': 'lovelace/config/save', 'url_path': path, 'config': cfg})
    res = call(url, token, *cmds)
    for r in res:
        if not r.get('success'):
            raise DashboardError(f"Home Assistant said no: {r.get('error', {}).get('message', 'unknown error')}")
    return {'created': not exists, 'path': path}
