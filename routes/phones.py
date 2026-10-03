"""Pair a phone with this instance: create a device token, show it once as a QR code (and text), list / revoke paired phones."""
import base64
import io
import os

import qrcode
from flask import Blueprint, current_app, jsonify, render_template, request, send_file
from urllib.parse import quote

from services import device_auth

bp = Blueprint('phones', __name__, url_prefix='/phones')


def _default_url():
    # Only an https address is useful: the Android app refuses plain http. If this instance's APP_URL isn't https the field starts empty.
    app_url = (current_app.config.get('APP_URL') or '').rstrip('/')
    return app_url if app_url.startswith('https://') else ''


@bp.route('/')
def index():
    return render_template('phones.html', devices=device_auth.devices(), default_url=_default_url())


@bp.route('/create', methods=['POST'])
def create():
    d = request.get_json() or {}
    url = (d.get('server_url') or _default_url()).strip().rstrip('/')
    if not url.startswith('https://'):
        return jsonify({'error': "The app only talks to https:// addresses (it refuses plain http, even on your home network). "
                                 "Enter the address you reach Headwind on through your reverse proxy, e.g. https://bike.example.com — "
                                 "and set APP_URL in this instance's env file to it so this box is pre-filled next time."}), 400
    token, did = device_auth.create(d.get('name') or 'Phone')
    link = f"headwind://pair?u={quote(url, safe='')}&t={token}"
    img = qrcode.make(link, box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return jsonify({'id': did, 'token': token, 'server_url': url, 'link': link,
                    'qr': 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode()})


@bp.route('/<int:device_id>/revoke', methods=['POST'])
def revoke(device_id):
    device_auth.revoke(device_id)
    return jsonify({'ok': True})


# ── the Android app itself (sideloading; served behind the normal login) ─────────────────────────────
APK_PATH = os.environ.get('HEADWIND_APK') or '/home/rob/headwind-android/dist/headwind.apk'


@bp.route('/app')
def app_page():
    info = ''
    try:
        info = open(os.path.join(os.path.dirname(APK_PATH), 'version.txt')).read().strip()
    except OSError:
        pass
    return render_template('phones_app.html', available=os.path.exists(APK_PATH), info=info,
                           size_mb=round(os.path.getsize(APK_PATH) / 1048576, 1) if os.path.exists(APK_PATH) else None)


@bp.route('/app.apk')
def app_apk():
    if not os.path.exists(APK_PATH):
        return jsonify({'error': 'no APK has been built yet'}), 404
    return send_file(APK_PATH, mimetype='application/vnd.android.package-archive', as_attachment=True, download_name='headwind.apk')
