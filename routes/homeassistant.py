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
                           weather_icon=ha.weather_icon)


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
