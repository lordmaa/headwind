from flask import Blueprint, jsonify
from database import query_db

bp = Blueprint('garmin', __name__)


@bp.route('/garmin/sync', methods=['POST'])
def sync():
    s = query_db('SELECT garminEmail, garminPassword FROM Settings WHERE id=1', one=True)
    if not s or not s['garminEmail'] or not s['garminPassword']:
        return jsonify({'error': 'Garmin credentials not configured'}), 400

    try:
        from services.garmin import sync_garmin
        from services.mqtt import push_update
        days = sync_garmin(s['garminEmail'], s['garminPassword'], days=14)
        push_update()
        return jsonify({'synced': days})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
