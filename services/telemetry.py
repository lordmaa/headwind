"""One-time, anonymous "an install exists" ping — see templates/setup.html's telemetry step for the exact
disclosure text shown to the user, and README.md's Telemetry section. Sends exactly two fields, once, ever
(unless the DB is wiped): a random install id and the app version. Nothing about rides, food, weight, or
any other user data. Permanently skippable via the setup wizard or TELEMETRY=off in .env.
"""
import logging
import os
import uuid

import requests

from database import get_db, query_db

log = logging.getLogger(__name__)

DEFAULT_URL = 'https://bike.smerchants.co.uk/telemetry/ping'


def install_id():
    """The random id for this install, generating one on first call. Never derived from anything personal."""
    s = query_db('SELECT installId FROM Settings WHERE id=1', one=True)
    if s and s['installId']:
        return s['installId']
    new_id = uuid.uuid4().hex
    db = get_db()
    db.execute('INSERT OR IGNORE INTO Settings (id) VALUES (1)')
    db.execute('UPDATE Settings SET installId=? WHERE id=1', [new_id])
    db.commit()
    return new_id


def ping_once():
    """Fires at most once per install, ever. Called only from the setup wizard's telemetry step (after the user has seen the disclosure); a no-op after the first
    success (or after an explicit opt-out). Never raises: a dead network or an unreachable collector must
    never affect app startup."""
    if os.environ.get('TELEMETRY', '').lower() == 'off':
        return
    s = query_db('SELECT telemetryOptOut, telemetryPingedAt FROM Settings WHERE id=1', one=True)
    if s and (s['telemetryOptOut'] or s['telemetryPingedAt']):
        return
    try:
        from version import __version__
    except ImportError:
        __version__ = 'unknown'
    try:
        url = os.environ.get('TELEMETRY_URL') or DEFAULT_URL
        r = requests.post(url, json={'install_id': install_id(), 'version': __version__}, timeout=5)
        if not r.ok:
            log.debug('Telemetry collector answered %s — not marking as sent', r.status_code)
            return
    except Exception as e:
        log.debug('Telemetry ping failed (harmless, not retried — the ping is best-effort): %s', e)
        return
    db = get_db()
    db.execute("INSERT OR IGNORE INTO Settings (id) VALUES (1)")
    db.execute("UPDATE Settings SET telemetryPingedAt=datetime('now') WHERE id=1")
    db.commit()


def opt_out():
    db = get_db()
    db.execute('INSERT OR IGNORE INTO Settings (id) VALUES (1)')
    db.execute('UPDATE Settings SET telemetryOptOut=1 WHERE id=1')
    db.commit()
