import json

from flask import Blueprint, Response, jsonify, request, stream_with_context
from database import get_db, query_db

bp = Blueprint('garmin', __name__)


@bp.route('/garmin/connect', methods=['POST'])
def connect():
    """
    Step 1 of first-time Garmin auth, used by both the Settings page and the setup
    wizard. Attempts the login first and saves the email/password into Settings only once
    Garmin accepts them (or asks for an MFA code), so a typo never replaces working credentials. On success, tokens
    are cached in .garmin_tokens/ and every future sync just works with no further
    prompting. When Garmin challenges with MFA, returns a token the caller must pass
    to /garmin/connect/mfa along with the code within 5 minutes.
    """
    d = request.get_json(silent=True) or {}
    email = (d.get('email') or '').strip()
    password = d.get('password') or ''
    if not email or not password:
        return jsonify({'error': 'Email and password are required'}), 400

    db = get_db()
    prev = db.execute('SELECT garminEmail FROM Settings WHERE id=1').fetchone()
    try:
        from services.garmin import start_login, TOKEN_DIR
        if prev and prev['garminEmail'] and prev['garminEmail'].lower() != email.lower():
            # switching accounts: the cached tokens belong to the old one and would silently be reused
            import shutil
            shutil.rmtree(TOKEN_DIR, ignore_errors=True)
        result = start_login(email, password)
    except Exception as e:
        return jsonify({'error': str(e)}), 400   # nothing saved: the previous working credentials stay in place

    # Reached only once Garmin accepted the password (or is waiting on an MFA code)
    db.execute('''
        INSERT INTO Settings (id, garminEmail, garminPassword) VALUES (1, ?, ?)
        ON CONFLICT(id) DO UPDATE SET garminEmail=excluded.garminEmail, garminPassword=excluded.garminPassword
    ''', [email, password])
    db.commit()
    return jsonify(result)


@bp.route('/garmin/connect/mfa', methods=['POST'])
def connect_mfa():
    """Step 2: completes a login /garmin/connect paused on {'needs_mfa': True}."""
    d = request.get_json(silent=True) or {}
    token = (d.get('token') or '').strip()
    code = (d.get('code') or '').strip()
    if not token or not code:
        return jsonify({'error': 'Missing code'}), 400

    try:
        from services.garmin import complete_mfa
        result = complete_mfa(token, code)
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 400


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
