import json

from flask import Blueprint, jsonify, render_template, request

from database import get_db, query_db
from services import homeassistant as ha

bp = Blueprint('homeassistant', __name__, url_prefix='/settings/home-assistant')


def _creds():
    """URL/token from the form if typed, else the saved ones (the token is never sent back to the page)."""
    saved = query_db('SELECT haUrl, haToken FROM Settings WHERE id=1', one=True)
    url = (request.form.get('url') or (saved['haUrl'] if saved else '') or '').strip()
    token = (request.form.get('token') or (saved['haToken'] if saved else '') or '').strip()
    return url, token


@bp.route('/')
def index():
    s = query_db('''SELECT haUrl, haToken, haEntityMap, haSyncMinutes, haLastSync, haLastResult,
                            haWeatherEntity, haWeatherCondition, haWeatherTempC, haWeatherUpdatedAt
                     FROM Settings WHERE id=1''', one=True)
    try:
        mappings = json.loads((s['haEntityMap'] if s else None) or '[]')
    except ValueError:
        mappings = []
    recent = query_db('''
        SELECT logDate, metric, value, unit FROM BodyMetric ORDER BY logDate DESC, metric LIMIT 12
    ''')
    weights = query_db("SELECT logDate, weightKg FROM WeightLog WHERE source='homeassistant' ORDER BY logDate DESC LIMIT 5")
    return render_template('home_assistant.html', url=(s['haUrl'] if s else '') or '',
                           has_token=bool(s and s['haToken']), mappings=mappings,
                           minutes=(s['haSyncMinutes'] if s else None) or 15,
                           last_result=s['haLastResult'] if s else None, last_sync=s['haLastSync'] if s else None,
                           targets=ha.TARGETS, recent=recent, weights=weights,
                           weather_entity=(s['haWeatherEntity'] if s else None) or '',
                           weather_condition=s['haWeatherCondition'] if s else None,
                           weather_temp_c=s['haWeatherTempC'] if s else None,
                           weather_updated_at=s['haWeatherUpdatedAt'] if s else None,
                           weather_icon=__import__('services.icons', fromlist=['weather']).weather)


@bp.route('/save', methods=['POST'])
def save():
    d = request.get_json() or {}
    url = (d.get('url') or '').strip().rstrip('/')
    if url and not url.startswith(('http://', 'https://')):
        return jsonify({'error': 'URL must start with http:// or https://'}), 400
    try:
        minutes = max(5, min(int(d.get('minutes') or 15), 1440))
    except (TypeError, ValueError):
        minutes = 15
    mappings = [{'entity_id': m['entity_id'], 'target': m['target'], 'name': m.get('name', '')}
                for m in (d.get('mappings') or []) if m.get('entity_id') and m.get('target') in ha.TARGETS]
    weather_entity = (d.get('weather_entity') or '').strip()
    db = get_db()
    token = (d.get('token') or '').strip()
    db.execute('INSERT INTO Settings (id) VALUES (1) ON CONFLICT(id) DO NOTHING')
    db.execute('UPDATE Settings SET haUrl=?, haEntityMap=?, haSyncMinutes=?, haWeatherEntity=? WHERE id=1',
               [url, json.dumps(mappings), minutes, weather_entity])
    if token:
        db.execute('UPDATE Settings SET haToken=? WHERE id=1', [token])
    db.commit()
    if weather_entity:
        try:
            ha.sync_weather()  # best-effort — reflect the new entity immediately rather than waiting for the heartbeat
        except ha.HAError:
            pass
    return jsonify({'ok': True})


@bp.route('/test', methods=['POST'])
def test():
    url, token = _creds()
    if not url or not token:
        return jsonify({'error': 'Enter the Home Assistant URL and a token first.'}), 400
    try:
        return jsonify({'ok': True, **ha.test(url, token)})
    except ha.HAError as e:
        return jsonify({'error': str(e)}), 400


@bp.route('/entities', methods=['POST'])
def entities():
    url, token = _creds()
    try:
        return jsonify(ha.list_entities(url, token))
    except ha.HAError as e:
        return jsonify({'error': str(e)}), 400


@bp.route('/sync', methods=['POST'])
def sync_now():
    try:
        days = int(request.form.get('days') or 0) or None
        return jsonify({'ok': True, **ha.sync(days=days)})
    except ha.HAError as e:
        return jsonify({'error': str(e)}), 400


# ── Dashboard wizard: build a Headwind dashboard in Home Assistant from its own cards ──────────────

def _dash_creds():
    url, token = _creds()
    if not url or not token:
        return None, None, (jsonify({'error': 'Connect Home Assistant first (URL and token on the Home Assistant page).'}), 400)
    return url, token, None


@bp.route('/dashboard')
def dashboard():
    from services import ha_dashboard as hd
    s = query_db('SELECT haUrl, haToken FROM Settings WHERE id=1', one=True)
    return render_template('ha_dashboard.html', connected=bool(s and s['haUrl'] and s['haToken']),
                           default_path=hd.DEFAULT_PATH, labels=hd.MODULE_LABELS,
                           in_addon=bool(s and (s['haUrl'] or '').startswith('http://supervisor')))


@bp.route('/dashboard/preview', methods=['POST'])
def dashboard_preview():
    from services import ha_dashboard as hd, mqtt
    url, token, err = _dash_creds()
    if err:
        return err
    title = (request.get_json(silent=True) or {}).get('title') or 'Headwind'
    try:
        p = hd.plan(url, token, mqtt._prefix(), title[:60])
    except hd.DashboardError as e:
        return jsonify({'error': str(e)}), 400
    return jsonify({'modules': [{'key': m, 'label': hd.MODULE_LABELS[m]} for m in p['modules']],
                    'views': [v['title'] for v in p['config']['views']], 'entities': len(p['entities']),
                    'existing': [x for x in p['existing_paths'] if x.startswith(hd.OWNED_PREFIX)]})


@bp.route('/dashboard/create', methods=['POST'])
def dashboard_create():
    from services import ha_dashboard as hd, mqtt
    url, token, err = _dash_creds()
    if err:
        return err
    d = request.get_json(silent=True) or {}
    title = (d.get('title') or 'Headwind')[:60]
    path = (d.get('path') or hd.DEFAULT_PATH).strip().lower()
    try:
        p = hd.plan(url, token, mqtt._prefix(), title)
        res = hd.deploy(url, token, path, title, p['config'])
    except hd.DashboardError as e:
        return jsonify({'error': str(e)}), 400
    return jsonify({'ok': True, **res, 'views': [v['title'] for v in p['config']['views']], 'entities': len(p['entities'])})
