from flask import Blueprint, jsonify
from database import query_db
from services.strava import sync_activities

bp = Blueprint('sync', __name__)


@bp.route('/sync', methods=['POST'])
def sync():
    athlete = query_db('SELECT id FROM Athlete LIMIT 1', one=True)
    if not athlete:
        return jsonify({'error': 'Not connected'}), 401
    try:
        synced = sync_activities()
        if synced:
            from services.mqtt import push_update
            push_update()
        return jsonify({'synced': synced})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
