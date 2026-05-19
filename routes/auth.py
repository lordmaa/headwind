import requests
from flask import Blueprint, current_app, redirect, request
from database import get_db

bp = Blueprint('auth', __name__)


@bp.route('/strava')
def strava():
    params = {
        'client_id':      current_app.config['STRAVA_CLIENT_ID'],
        'redirect_uri':   current_app.config['APP_URL'] + '/auth/callback',
        'response_type':  'code',
        'approval_prompt': 'auto',
        'scope':          'read,activity:read_all',
    }
    url = 'https://www.strava.com/oauth/authorize?' + '&'.join(f'{k}={v}' for k, v in params.items())
    return redirect(url)


@bp.route('/callback')
def callback():
    error = request.args.get('error')
    code  = request.args.get('code')

    if error or not code:
        return redirect('/?error=strava_denied')

    res = requests.post('https://www.strava.com/oauth/token', json={
        'client_id':     current_app.config['STRAVA_CLIENT_ID'],
        'client_secret': current_app.config['STRAVA_CLIENT_SECRET'],
        'code':          code,
        'grant_type':    'authorization_code',
    })
    if not res.ok:
        return redirect('/?error=token_exchange')

    data    = res.json()
    athlete = data['athlete']
    db      = get_db()

    db.execute('''
        INSERT INTO Athlete (id, firstname, lastname, profile, accessToken, refreshToken, expiresAt)
        VALUES (?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
            firstname=excluded.firstname, lastname=excluded.lastname,
            profile=excluded.profile, accessToken=excluded.accessToken,
            refreshToken=excluded.refreshToken, expiresAt=excluded.expiresAt
    ''', [
        athlete['id'], athlete['firstname'], athlete['lastname'],
        athlete.get('profile', ''),
        data['access_token'], data['refresh_token'], data['expires_at'],
    ])
    db.commit()

    return redirect('/dashboard')
