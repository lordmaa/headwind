"""Optional: relay the install ping to a separate collector (headwind-telemetry). Inert unless TELEMETRY_FORWARD_URL is
set — i.e. only on the one instance whose public hostname the released app pings. Everywhere else /telemetry/ping is a 404."""
import os

import requests
from flask import Blueprint, abort, jsonify, request

bp = Blueprint('telemetry_forward', __name__)


@bp.route('/telemetry/ping', methods=['POST'])
def ping():
    target = os.environ.get('TELEMETRY_FORWARD_URL')
    if not target:
        abort(404)
    body = request.get_data(cache=False)[:4096]
    try:
        r = requests.post(target, data=body, timeout=3, headers={
            'Content-Type': 'application/json',
            'X-Forwarded-For': request.remote_addr or ''})   # lets the collector rate-limit per client
    except requests.RequestException:
        return jsonify(error='collector unavailable'), 503
    return (r.content, r.status_code, {'Content-Type': 'application/json'})
