import hmac
import os
import time
from collections import defaultdict, deque
from pathlib import Path

from dotenv import load_dotenv
from services import credentials
from flask import Blueprint, redirect, render_template, request, session, url_for

load_dotenv(os.environ.get('HEADWIND_ENV') or Path(__file__).parent.parent / '.env', override=True)

bp = Blueprint('login', __name__)

_fails = defaultdict(deque)   # client address -> timestamps of recent failed sign-ins
_MAX_FAILS, _WINDOW = 8, 300  # 8 failures per 5 minutes, then locked out until the window clears


def _locked_out(ip):
    q, now = _fails[ip], time.monotonic()
    while q and now - q[0] > _WINDOW:
        q.popleft()
    return len(q) >= _MAX_FAILS


def _check(username, password):
    expected_u, expected_p, _ = credentials.admin_login()
    ok_u = hmac.compare_digest(username.encode(), expected_u.encode())
    ok_p = hmac.compare_digest(password.encode(), expected_p.encode())
    return ok_u and ok_p


@bp.route('/login', methods=['GET', 'POST'])
def login_page():
    if session.get('logged_in'):
        return redirect(url_for('dashboard.dashboard'))
    error = None
    if request.method == 'POST':
        ip = request.remote_addr or ''
        if _locked_out(ip):
            return render_template('login.html', error='Too many attempts — try again in a few minutes'), 429
        if _check(request.form.get('username', ''), request.form.get('password', '')):
            session.permanent = True
            session['logged_in'] = True
            session['av'] = credentials.auth_version()
            _fails.pop(ip, None)
            nxt = request.args.get('next') or ''
            # same-origin relative paths only — no //host or scheme-qualified redirects
            if not nxt.startswith('/') or nxt.startswith('//') or '\\' in nxt:
                nxt = url_for('dashboard.dashboard')
            return redirect(nxt)
        _fails[ip].append(time.monotonic())
        error = 'Invalid username or password'
    return render_template('login.html', error=error)


@bp.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login.login_page'))
