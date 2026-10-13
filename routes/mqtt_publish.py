import logging

from flask import Blueprint, jsonify

from database import query_db
from services.mqtt import SENSORS, publish, _gather_stats

log = logging.getLogger(__name__)

bp = Blueprint('mqtt', __name__)


@bp.route('/mqtt/publish', methods=['POST'])
def do_publish():
    s = query_db('SELECT * FROM Settings WHERE id=1', one=True)
    if not s or not s['mqttHost']:
        return jsonify({'error': 'MQTT host not configured'}), 400

    try:
        publish(dict(s), _gather_stats())
        return jsonify({'ok': True, 'sensors': len(SENSORS)})
    except Exception as e:
        log.error('MQTT publish failed: %s', e)
        return jsonify({'error': str(e)}), 500
