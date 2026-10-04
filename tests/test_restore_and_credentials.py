"""Restore must reject incompatible backups before touching live data; a Settings password change must survive the
environment re-supplying the original credentials. Disposable temp files only."""
import os
import sqlite3

import pytest
from flask import Flask

import database
from services import backup, credentials


# ---------- credentials ----------
@pytest.fixture
def creds(tmp_path, monkeypatch):
    monkeypatch.setattr(credentials, '_data_dir', lambda: str(tmp_path))
    for k in ('APP_USERNAME', 'APP_PASSWORD'):
        monkeypatch.delenv(k, raising=False)
    return monkeypatch


def test_env_wins_without_saved_file(creds):
    creds.setenv('APP_USERNAME', 'rob'); creds.setenv('APP_PASSWORD', 'compose-pw-1')
    assert credentials.admin_login()[:2] == ('rob', 'compose-pw-1')


def test_settings_change_survives_env_resupply(creds):
    creds.setenv('APP_USERNAME', 'rob'); creds.setenv('APP_PASSWORD', 'compose-pw-1')
    credentials.save_login(None, 'changed-in-settings')
    # container recreated: Compose supplies the ORIGINAL credentials again
    assert credentials.admin_login()[:2] == ('rob', 'changed-in-settings')


def test_operator_editing_env_still_wins(creds):
    creds.setenv('APP_USERNAME', 'rob'); creds.setenv('APP_PASSWORD', 'compose-pw-1')
    credentials.save_login(None, 'changed-in-settings')
    creds.setenv('APP_PASSWORD', 'operator-new-pw')
    assert credentials.admin_login()[1] == 'operator-new-pw'


def test_generated_password_is_stable_and_changeable(creds):
    creds.setenv('APP_PASSWORD', 'changeme')            # placeholder -> generate
    first = credentials.admin_login()[1]
    assert first not in credentials.PLACEHOLDER_PASSWORDS and credentials.admin_login()[1] == first
    credentials.save_login(None, 'mine-now-123')
    assert credentials.admin_login()[1] == 'mine-now-123'


def test_auth_version_changes_with_password(creds):
    creds.setenv('APP_PASSWORD', 'compose-pw-1')
    a = credentials.auth_version(); credentials.save_login(None, 'other-pw-22')
    assert credentials.auth_version() != a


# ---------- restore ----------
def _live_db(tmp_path):
    path = str(tmp_path / 'live.db')
    app = Flask('x'); app.config['DATABASE'] = path
    with app.app_context():
        database.migrate_db(); db = database.get_db()
        db.execute("INSERT INTO Rider (name,isDefault) VALUES ('Original',1)"); db.commit()
    return path


def _riders(path):
    return [r[0] for r in sqlite3.connect(path).execute('SELECT name FROM Rider')]


def test_incompatible_backup_rejected_before_anything_changes(tmp_path):
    live = _live_db(tmp_path)
    bad = str(tmp_path / 'bad.db')
    c = sqlite3.connect(bad)   # right table names, wrong shape: passes the old validate()
    c.executescript('CREATE TABLE Rider(x TEXT); CREATE TABLE Activity(y TEXT); CREATE TABLE Settings(z TEXT);'
                    "INSERT INTO Rider VALUES ('a');")
    c.commit(); c.close()
    assert backup.validate(bad) == 1                       # old check lets it through...
    with pytest.raises(backup.BackupError):
        backup.prepare(bad)                                # ...the new one doesn't
    assert _riders(live) == ['Original'] and not os.path.exists(live + '.pre-restore')


def test_older_backup_is_migrated_then_applied(tmp_path):
    live = _live_db(tmp_path)
    other = str(tmp_path / 'other.db')
    app = Flask('y'); app.config['DATABASE'] = other
    with app.app_context():
        database.migrate_db(); db = database.get_db()
        db.execute("INSERT INTO Rider (name,isDefault) VALUES ('FromBackup',1)"); db.commit()
    backup.validate(other); backup.prepare(other)
    backup.apply(live, other, {}, str(tmp_path / 'av'))
    assert _riders(live) == ['FromBackup'] and os.path.exists(live + '.pre-restore')


def test_asset_copy_failure_leaves_live_db_untouched(tmp_path):
    live = _live_db(tmp_path)
    other = str(tmp_path / 'other.db')
    app = Flask('z'); app.config['DATABASE'] = other
    with app.app_context():
        database.migrate_db(); db = database.get_db()
        db.execute("INSERT INTO Rider (name,isDefault) VALUES ('FromBackup',1)"); db.commit()
    avatars = tmp_path / 'staged_av'; avatars.mkdir(); (avatars / 'rider_1.jpg').write_bytes(b'x')
    blocker = tmp_path / 'not_a_dir'; blocker.write_text('file where the avatar dir should be')
    with pytest.raises(Exception):
        backup.apply(live, other, {'avatars': str(avatars)}, str(blocker))
    assert _riders(live) == ['Original']
