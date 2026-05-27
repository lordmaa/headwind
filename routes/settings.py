import os
import shutil
import sqlite3
import tempfile
import threading as _threading
import urllib.request
import zipfile
from pathlib import Path

_backfill_lock  = _threading.Lock()
_backfill_state = {'running': False, 'done': 0, 'failed': 0}

from dotenv import load_dotenv, set_key
from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, send_file
from database import get_db, query_db

bp = Blueprint('settings', __name__)

ENV_PATH = Path(__file__).parent.parent / '.env'

OPENAI_MODELS = ['gpt-4o', 'gpt-4o-mini', 'o1', 'o1-mini', 'gpt-4-turbo', 'gpt-3.5-turbo']


@bp.route('/', methods=['GET', 'POST'])
def index():
    db = get_db()

    if request.method == 'POST':
        provider     = request.form.get('aiProvider', 'openai')
        openai_key   = request.form.get('openaiKey', '').strip()
        openai_model = request.form.get('openaiModel', 'gpt-4o')
        ollama_url   = request.form.get('ollamaUrl', 'http://localhost:11434').strip()
        ollama_model = request.form.get('ollamaModel', 'llama3.2').strip()

        current      = query_db('SELECT * FROM Settings WHERE id=1', one=True)
        kept_key     = current['openaiKey']    if current else None
        kept_mqtt_pw = current['mqttPassword'] if current else None

        mqtt_host     = request.form.get('mqttHost',     '').strip()
        mqtt_port     = request.form.get('mqttPort',     '1883').strip()
        mqtt_user     = request.form.get('mqttUser',     '').strip()
        mqtt_password = request.form.get('mqttPassword', '').strip()
        garmin_email  = request.form.get('garminEmail',  '').strip()
        garmin_password = request.form.get('garminPassword', '').strip()
        kept_garmin_pw = current['garminPassword'] if current else None
        garmin_sync_hours_raw = request.form.get('garminSyncHours', '2').strip()
        garmin_sync_hours = int(garmin_sync_hours_raw) if garmin_sync_hours_raw.isdigit() and int(garmin_sync_hours_raw) >= 1 else 2
        garmin_sync_mode = request.form.get('garminSyncMode', 'health')
        if garmin_sync_mode not in ('health', 'full'):
            garmin_sync_mode = 'health'

        db.execute('''
            INSERT INTO Settings (id, aiProvider, openaiKey, openaiModel, ollamaUrl, ollamaModel,
                                  mqttHost, mqttPort, mqttUser, mqttPassword,
                                  garminEmail, garminPassword, garminSyncHours, garminSyncMode)
            VALUES (1,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                aiProvider=excluded.aiProvider,
                openaiKey=excluded.openaiKey,
                openaiModel=excluded.openaiModel,
                ollamaUrl=excluded.ollamaUrl,
                ollamaModel=excluded.ollamaModel,
                mqttHost=excluded.mqttHost,
                mqttPort=excluded.mqttPort,
                mqttUser=excluded.mqttUser,
                mqttPassword=excluded.mqttPassword,
                garminEmail=excluded.garminEmail,
                garminPassword=excluded.garminPassword,
                garminSyncHours=excluded.garminSyncHours,
                garminSyncMode=excluded.garminSyncMode
        ''', [
            provider,
            openai_key if openai_key else kept_key,
            openai_model,
            ollama_url,
            ollama_model,
            mqtt_host,
            int(mqtt_port) if mqtt_port.isdigit() else 1883,
            mqtt_user,
            mqtt_password if mqtt_password else kept_mqtt_pw,
            garmin_email,
            garmin_password if garmin_password else kept_garmin_pw,
            garmin_sync_hours,
            garmin_sync_mode,
        ])
        db.commit()
        flash('Settings saved.', 'success')
        return redirect('/settings')

    s = query_db('SELECT * FROM Settings WHERE id=1', one=True)
    current_username = os.environ.get('APP_USERNAME', '')
    try:
        with open('/app/build_time.txt') as _f:
            build_time = _f.read().strip()
    except FileNotFoundError:
        build_time = None
    try:
        from version import __version__
    except ImportError:
        __version__ = 'dev'
    return render_template('settings.html', s=s, openai_models=OPENAI_MODELS,
                           current_username=current_username, build_time=build_time,
                           app_version=__version__)


@bp.route('/version-check')
def version_check():
    try:
        req  = urllib.request.Request(
            'https://hub.docker.com/v2/repositories/lordmerchant99/bike-flask/tags/latest',
            headers={'User-Agent': 'Headwind/1.0'},
        )
        resp = urllib.request.urlopen(req, timeout=8)
        import json
        data = json.loads(resp.read())
        return jsonify({'last_updated': data.get('last_updated')})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@bp.route('/weather-backfill', methods=['POST'])
def weather_backfill():
    from flask import current_app
    from database import get_db

    db = get_db()

    with _backfill_lock:
        if _backfill_state['running']:
            remaining = db.execute('''
                SELECT COUNT(*) FROM Activity
                WHERE startLat IS NOT NULL AND startLng IS NOT NULL AND weatherSummary IS NULL
            ''').fetchone()[0]
            return jsonify(done=_backfill_state['done'], failed=_backfill_state['failed'],
                           remaining=remaining, running=True)

        remaining = db.execute('''
            SELECT COUNT(*) FROM Activity
            WHERE startLat IS NOT NULL AND startLng IS NOT NULL AND weatherSummary IS NULL
        ''').fetchone()[0]

        if remaining == 0:
            return jsonify(done=_backfill_state['done'], failed=_backfill_state['failed'], remaining=0)

        _backfill_state['running'] = True
        _backfill_state['done']    = 0
        _backfill_state['failed']  = 0

    app = current_app._get_current_object()

    def _run():
        from services.weather import fetch_weather, save_weather
        with app.app_context():
            inner_db = get_db()
            try:
                while True:
                    rows = inner_db.execute('''
                        SELECT id, startLat, startLng, startDateLocal, streams
                        FROM Activity
                        WHERE startLat IS NOT NULL AND startLng IS NOT NULL
                          AND weatherSummary IS NULL
                        ORDER BY startDateLocal DESC
                        LIMIT 20
                    ''').fetchall()
                    if not rows:
                        break
                    for r in rows:
                        try:
                            w = fetch_weather(r['startLat'], r['startLng'], r['startDateLocal'], r['streams'])
                            if w:
                                save_weather(inner_db, r['id'], w)
                                with _backfill_lock:
                                    _backfill_state['done'] += 1
                        except Exception:
                            with _backfill_lock:
                                _backfill_state['failed'] += 1
                    inner_db.commit()
            finally:
                with _backfill_lock:
                    _backfill_state['running'] = False

    _threading.Thread(target=_run, daemon=True).start()
    return jsonify(done=0, failed=0, remaining=remaining, started=True)


@bp.route('/password', methods=['POST'])
def change_password():
    load_dotenv(ENV_PATH, override=True)
    current_pw  = request.form.get('currentPassword', '')
    new_username = request.form.get('newUsername', '').strip()
    new_pw      = request.form.get('newPassword', '').strip()
    confirm_pw  = request.form.get('confirmPassword', '').strip()

    import hmac
    stored_pw = os.environ.get('APP_PASSWORD', '')
    if not hmac.compare_digest(current_pw.encode(), stored_pw.encode()):
        flash('Current password is incorrect.', 'error')
        return redirect('/settings')

    if new_pw and new_pw != confirm_pw:
        flash('New passwords do not match.', 'error')
        return redirect('/settings')

    if new_username:
        set_key(ENV_PATH, 'APP_USERNAME', new_username)
        os.environ['APP_USERNAME'] = new_username
    if new_pw:
        set_key(ENV_PATH, 'APP_PASSWORD', new_pw)
        os.environ['APP_PASSWORD'] = new_pw

    flash('Login details updated.', 'success')
    return redirect('/settings')


def _db_path():
    p = current_app.config.get('DATABASE') or os.environ.get('DATABASE_URL', '')
    return p.replace('sqlite:///', '')


def _avatar_dir():
    return os.path.join(current_app.root_path, 'static', 'avatars')


def _validate_db(path):
    """Open a SQLite file and return (tables set, rider_count) or raise."""
    conn = sqlite3.connect(path)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    rider_count = conn.execute('SELECT COUNT(*) FROM Rider').fetchone()[0] if 'Rider' in tables else 0
    conn.close()
    return tables, rider_count


def _apply_restore(tmp_db, avatar_src_dir=None):
    """Replace live DB with tmp_db and optionally copy avatars. Saves .pre-restore."""
    db = _db_path()
    shutil.copy2(db, db + '.pre-restore')
    shutil.move(tmp_db, db)
    if avatar_src_dir and os.path.isdir(avatar_src_dir):
        dest = _avatar_dir()
        os.makedirs(dest, exist_ok=True)
        for name in os.listdir(avatar_src_dir):
            shutil.copy2(os.path.join(avatar_src_dir, name), os.path.join(dest, name))


@bp.route('/backup/export')
def backup_export():
    db = _db_path()
    if not db or not Path(db).exists():
        flash('Database file not found.', 'error')
        return redirect('/settings')

    tmp_dir = tempfile.mkdtemp()
    try:
        # Consistent DB snapshot via SQLite backup API
        snap = os.path.join(tmp_dir, 'headwind.db')
        src = sqlite3.connect(db)
        dst = sqlite3.connect(snap)
        src.backup(dst)
        src.close()
        dst.close()

        zip_path = os.path.join(tmp_dir, 'headwind-backup.zip')
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.write(snap, 'headwind.db')
            av_dir = _avatar_dir()
            if os.path.isdir(av_dir):
                for name in os.listdir(av_dir):
                    zf.write(os.path.join(av_dir, name), f'avatars/{name}')

        return send_file(zip_path, as_attachment=True, download_name='headwind-backup.zip',
                         mimetype='application/zip')
    except Exception as e:
        flash(f'Export failed: {e}', 'error')
        return redirect('/settings')


@bp.route('/backup/import', methods=['POST'])
def backup_import():
    f = request.files.get('backup')
    if not f or not f.filename:
        flash('No file selected.', 'error')
        return redirect('/settings')

    tmp_dir = tempfile.mkdtemp()
    try:
        upload = os.path.join(tmp_dir, f.filename)
        f.save(upload)

        if zipfile.is_zipfile(upload):
            with zipfile.ZipFile(upload) as zf:
                if 'headwind.db' not in zf.namelist():
                    flash('Invalid backup zip — headwind.db not found inside.', 'error')
                    return redirect('/settings')
                zf.extractall(tmp_dir)
            tmp_db = os.path.join(tmp_dir, 'headwind.db')
            avatar_src = os.path.join(tmp_dir, 'avatars')
        else:
            tmp_db = upload
            avatar_src = None

        tables, _ = _validate_db(tmp_db)
        required = {'Rider', 'Activity', 'Settings'}
        if not required.issubset(tables):
            flash(f'Invalid backup — missing tables: {required - tables}', 'error')
            return redirect('/settings')

        _apply_restore(tmp_db, avatar_src)
    except Exception as e:
        flash(f'Restore failed: {e}', 'error')
        return redirect('/settings')

    flash('Restored from backup — previous database saved as .pre-restore.', 'success')
    return redirect('/settings')
