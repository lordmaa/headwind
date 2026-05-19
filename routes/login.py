import hmac
import os
from pathlib import Path

from dotenv import load_dotenv
from flask import Blueprint, redirect, render_template, request, session, url_for

load_dotenv(Path(__file__).parent.parent / '.env', override=True)

bp = Blueprint('login', __name__)


def _check(username, password):
    expected_u = os.environ.get('APP_USERNAME', '')
    expected_p = os.environ.get('APP_PASSWORD', '')
    if not expected_u or not expected_p:
        return False
    ok_u = hmac.compare_digest(username.encode(), expected_u.encode())
    ok_p = hmac.compare_digest(password.encode(), expected_p.encode())
    return ok_u and ok_p


@bp.route('/login', methods=['GET', 'POST'])
def login_page():
    if session.get('logged_in'):
        return redirect(url_for('dashboard.dashboard'))
    error = None
    if request.method == 'POST':
        if _check(request.form.get('username', ''), request.form.get('password', '')):
            session.permanent = True
            session['logged_in'] = True
            return redirect(request.args.get('next') or url_for('dashboard.dashboard'))
        error = 'Invalid username or password'
    return render_template('login.html', error=error)


@bp.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login.login_page'))
