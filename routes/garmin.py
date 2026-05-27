import json

from flask import Blueprint, Response, jsonify, stream_with_context
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


@bp.route('/garmin/sync-activities')
def sync_activities():
    s = query_db('SELECT garminEmail, garminPassword, garminSyncMode FROM Settings WHERE id=1', one=True)
    if not s or not s['garminEmail'] or not s['garminPassword']:
        return jsonify({'error': 'Garmin credentials not configured'}), 400
    if s['garminSyncMode'] != 'full':
        return jsonify({'error': 'Garmin activity sync is not enabled'}), 403

    rider = query_db('SELECT id FROM Rider WHERE isDefault=1', one=True)
    if not rider:
        return jsonify({'error': 'No default rider found'}), 400

    def generate():
        try:
            from services.garmin import sync_garmin_activities
            for status in sync_garmin_activities(s['garminEmail'], s['garminPassword'], rider['id']):
                yield f"data: {json.dumps(status)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={'X-Accel-Buffering': 'no', 'Cache-Control': 'no-cache'},
    )
