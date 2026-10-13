"""Device tokens for the phone app: one per paired phone, revocable, stored only as a SHA-256 hash (shown once at pairing)."""
import hashlib
import secrets
from datetime import datetime, timedelta

from database import get_db, query_db

PREFIX = 'hw_'


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def create(name):
    token = PREFIX + secrets.token_urlsafe(32)
    db = get_db()
    cur = db.execute('INSERT INTO DeviceToken (name, tokenHash) VALUES (?, ?)', [(name or 'Phone').strip()[:60], _hash(token)])
    db.commit()
    return token, cur.lastrowid


def verify(token):
    """True for a known, un-revoked token. Records last use (at most once a minute, so a chatty app doesn't write on every call)."""
    if not token or not token.startswith(PREFIX):
        return False
    row = query_db('SELECT id, lastUsedAt, appVersion FROM DeviceToken WHERE tokenHash=? AND revokedAt IS NULL', [_hash(token)], one=True)
    if not row:
        return False
    now = datetime.utcnow()
    last = row['lastUsedAt']
    ver = _app_version()
    if not last or now - datetime.strptime(last, '%Y-%m-%d %H:%M:%S') > timedelta(minutes=1) or (ver and ver != row['appVersion']):
        db = get_db()
        db.execute("UPDATE DeviceToken SET lastUsedAt=datetime('now'), appVersion=COALESCE(?, appVersion) WHERE id=?", [ver, row['id']])
        db.commit()
    return True


def _app_version():
    """The build the phone says it is (X-Headwind-App header, e.g. '0.21.2-beta'), or None. Older apps do not send it."""
    try:
        from flask import request
        v = (request.headers.get('X-Headwind-App') or '').strip()
    except RuntimeError:
        return None
    return v[:30] if v and all(c.isalnum() or c in '.-_' for c in v) else None


def devices():
    return [dict(r) for r in query_db('SELECT id, name, createdAt, lastUsedAt, revokedAt, appVersion FROM DeviceToken ORDER BY id DESC')]


def revoke(device_id):
    db = get_db()
    db.execute("UPDATE DeviceToken SET revokedAt=datetime('now') WHERE id=? AND revokedAt IS NULL", [device_id])
    db.commit()
