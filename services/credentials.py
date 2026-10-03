"""Per-install signing key and admin login. Anything unset or still a publicly-known placeholder (the old
Compose / .env.example values) is replaced by a random value generated once and kept beside the database, so a
default install is never reachable with a guessable key or password. Real values from the environment win."""
import json
import logging
import os
import secrets

log = logging.getLogger(__name__)

PLACEHOLDER_KEYS = {'', 'dev-secret', 'change-me-please', 'change-me-to-a-random-string'}
PLACEHOLDER_PASSWORDS = {'', 'changeme', 'password', 'admin'}


def _data_dir():
    from config import Config
    return os.path.dirname(os.path.abspath(Config.DATABASE.replace('sqlite:///', '')))


def _path(name):
    return os.path.join(_data_dir(), name)


def _write_private(path, text):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write(text)


def secret_key(env_value, db_path):
    """Env value if it's a real secret, else a persisted random key. Takes the db path explicitly because the
    Config class calls this while it is still being defined."""
    if env_value not in PLACEHOLDER_KEYS:
        return env_value
    path = os.path.join(os.path.dirname(os.path.abspath(db_path)), '.secret_key')
    try:
        with open(path) as f:
            key = f.read().strip()
        if key:
            return key
    except FileNotFoundError:
        pass
    key = secrets.token_hex(32)
    try:
        _write_private(path, key)
    except OSError as e:
        log.warning('Could not persist signing key (%s) — sessions will reset on restart', e)
    return key


def _login_file():
    return _path('.admin_login')


def admin_login():
    """(username, password, generated). Env credentials win when real; otherwise the file written by the
    settings page or first boot."""
    user = os.environ.get('APP_USERNAME', '').strip() or 'admin'
    pw = os.environ.get('APP_PASSWORD', '')
    if pw not in PLACEHOLDER_PASSWORDS:
        return user, pw, False
    try:
        with open(_login_file()) as f:
            d = json.load(f)
        if d.get('password'):
            return d.get('username') or user, d['password'], True
    except (FileNotFoundError, ValueError):
        pass
    pw = secrets.token_urlsafe(12)
    try:
        _write_private(_login_file(), json.dumps({'username': user, 'password': pw}))
    except OSError as e:
        log.warning('Could not persist generated admin password (%s)', e)
    log.warning('\n%s\n  No admin password configured — generated one for first login:\n    username: %s\n    password: %s\n'
                '  Change it under Settings. Stored in %s\n%s', '=' * 64, user, pw, _login_file(), '=' * 64)
    return user, pw, True


def save_login(username, password):
    """Persist changed login details so they survive container recreation (the .env copy does not)."""
    cur_user, cur_pw, _ = admin_login()
    _write_private(_login_file(), json.dumps({'username': username or cur_user, 'password': password or cur_pw}))


def auth_version():
    """Changes whenever the login username/password changes, so existing sessions stop being valid."""
    import hashlib
    u, pw, _ = admin_login()
    return hashlib.sha256(f'{u}\0{pw}'.encode()).hexdigest()[:16]
