"""Public privacy policy for the Headwind Android app (Play Store needs a login-free URL). Source of truth:
headwind-android/PRIVACY-POLICY.md; static/legal/privacy.html is a copy of the generated page."""
from pathlib import Path

from flask import Blueprint, Response

bp = Blueprint('privacy', __name__)
_PAGE = Path(__file__).resolve().parent.parent / 'static' / 'legal' / 'privacy.html'


@bp.route('/privacy')
def privacy():
    resp = Response(_PAGE.read_bytes(), mimetype='text/html')
    resp.headers['Cache-Control'] = 'public, max-age=300, no-transform'
    return resp
