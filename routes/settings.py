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
from flask import Blueprint, after_this_request, current_app, flash, jsonify, redirect, render_template, request, send_file, session
from database import get_db, migrate_db, query_db

import time
bp = Blueprint('settings', __name__)

ENV_PATH = Path(os.environ.get('HEADWIND_ENV') or Path(__file__).parent.parent / '.env')

OPENAI_MODELS = ['gpt-4o', 'gpt-4o-mini', 'o1', 'o1-mini', 'gpt-4-turbo', 'gpt-3.5-turbo']


@bp.route('/', methods=['GET', 'POST'])
def index():
    db = get_db()

    if request.method == 'POST':
        # NULL columns render as the text 'None' in hidden form fields — never store that
        form = {k: ('' if v == 'None' else v) for k, v in request.form.items()}
        current      = query_db('SELECT * FROM Settings WHERE id=1', one=True)
        _kept = lambda col: (current[col] if current and current[col] != 'None' else None)

        def field(name, default=''):
            """Posted value if this form has the field; otherwise what is already saved. Each card on the page is
            its own form, so saving one must never blank the settings that live in another."""
            if name in form:
                return form[name]
            v = current[name] if current and current[name] is not None else default
            return '' if v == 'None' else str(v)

        provider     = field('aiProvider', 'openai') or 'openai'
        openai_key   = form.get('openaiKey', '').strip()
        openai_model = field('openaiModel', 'gpt-4o') or 'gpt-4o'
        ollama_url   = (field('ollamaUrl', 'http://localhost:11434') or 'http://localhost:11434').strip()
        ollama_model = (field('ollamaModel', 'llama3.2') or 'llama3.2').strip()
        units        = field('units', 'imperial')
        if units not in ('imperial', 'metric'):
            units = 'imperial'
        kept_key     = _kept('openaiKey')
        kept_mqtt_pw = _kept('mqttPassword')

        mqtt_host     = field('mqttHost').strip()
        mqtt_port     = (field('mqttPort', '1883') or '1883').strip()
        mqtt_user     = field('mqttUser').strip()
        mqtt_password = form.get('mqttPassword', '').strip()
        garmin_email  = field('garminEmail').strip()
        garmin_password = form.get('garminPassword', '').strip()
        kept_garmin_pw = _kept('garminPassword')
        garmin_sync_hours_raw = (field('garminSyncHours', '0.5') or '0.5').strip()
        try:
            garmin_sync_hours = float(garmin_sync_hours_raw)
            if garmin_sync_hours < 0.25:
                garmin_sync_hours = 0.5
        except ValueError:
            garmin_sync_hours = 0.5
        garmin_sync_mode = field('garminSyncMode', 'health')
        if garmin_sync_mode not in ('health', 'full'):
            garmin_sync_mode = 'health'

        db.execute('''
            INSERT INTO Settings (id, aiProvider, openaiKey, openaiModel, ollamaUrl, ollamaModel,
                                  mqttHost, mqttPort, mqttUser, mqttPassword,
                                  garminEmail, garminPassword, garminSyncHours, garminSyncMode, units)
            VALUES (1,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                garminSyncMode=excluded.garminSyncMode,
                units=excluded.units
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
            units,
        ])
        db.commit()
        flash('Settings saved.', 'success')
        return redirect('/settings')

    s = query_db('SELECT * FROM Settings WHERE id=1', one=True)
    from services import credentials
    current_username = credentials.admin_login()[0]
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


@bp.route('/test-ai', methods=['POST'])
def test_ai():
    """Try the AI credentials currently typed into the form (saved or not) with a one-word request."""
    import time
    from openai import OpenAI
    f = request.form
    saved = query_db('SELECT openaiKey FROM Settings WHERE id=1', one=True)
    try:
        if (f.get('aiProvider') or 'openai') == 'ollama':
            base = (f.get('ollamaUrl') or 'http://localhost:11434').strip()
            model = (f.get('ollamaModel') or 'llama3.2').strip()
            client = OpenAI(api_key='ollama', base_url=f'{base}/v1', timeout=20)
        else:
            key = (f.get('openaiKey') or '').strip()
            if key in ('', 'None'):
                key = (saved['openaiKey'] if saved else '') or ''
            if key in ('', 'None'):
                return jsonify({'error': 'No API key entered'}), 400
            model = f.get('openaiModel') or 'gpt-4o'
            client = OpenAI(api_key=key, timeout=20)
        t0 = time.time()
        client.chat.completions.create(model=model, max_tokens=5,
                                       messages=[{'role': 'user', 'content': 'Reply with OK'}])
        return jsonify({'ok': True, 'model': model, 'ms': int((time.time() - t0) * 1000)})
    except Exception as e:
        return jsonify({'error': str(getattr(e, 'message', None) or e)[:200]}), 400


@bp.route('/test-mqtt', methods=['POST'])
def test_mqtt():
    """Connect to the broker with the values typed into the form (saved or not) — publishes nothing."""
    import paho.mqtt.client as mqtt
    f = request.form
    host = (f.get('mqttHost') or '').strip()
    if not host or host == 'None':
        return jsonify({'error': 'No broker host entered'}), 400
    try:
        port = int(f.get('mqttPort') or 1883)
    except ValueError:
        return jsonify({'error': 'Port must be a number'}), 400
    user = (f.get('mqttUser') or '').strip()
    pw = (f.get('mqttPassword') or '').strip()
    if user == 'None':
        user = ''
    if pw == 'None':
        pw = ''
    if user and not pw:
        saved = query_db('SELECT mqttPassword FROM Settings WHERE id=1', one=True)
        pw = (saved['mqttPassword'] if saved else '') or ''
    result = {}
    try:
        try:
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id='bike-tracker-test')
        except AttributeError:  # paho < 2
            client = mqtt.Client(client_id='bike-tracker-test')
        if user:
            client.username_pw_set(user, pw)
        def on_connect(c, u, flags, rc, *a):
            result['rc'] = getattr(rc, 'value', rc)
        client.on_connect = on_connect
        client.connect(host, port, keepalive=10)
        client.loop_start()
        for _ in range(50):
            if 'rc' in result:
                break
            time.sleep(0.1)
        client.loop_stop()
        client.disconnect()
    except Exception as e:
        return jsonify({'error': str(e)[:200]}), 400
    if 'rc' not in result:
        return jsonify({'error': 'Broker did not respond'}), 400
    if result['rc'] != 0:
        return jsonify({'error': 'Broker refused the login (bad username/password?)'}), 400
    return jsonify({'ok': True})


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
    from services import credentials
    stored_pw = credentials.admin_login()[1]
    if not hmac.compare_digest(current_pw.encode(), stored_pw.encode()):
        flash('Current password is incorrect.', 'error')
        return redirect('/settings')

    if new_pw and new_pw != confirm_pw:
        flash('New passwords do not match.', 'error')
        return redirect('/settings')

    credentials.save_login(new_username, new_pw)
    if new_username:
        set_key(ENV_PATH, 'APP_USERNAME', new_username)
        os.environ['APP_USERNAME'] = new_username
    if new_pw:
        set_key(ENV_PATH, 'APP_PASSWORD', new_pw)
        os.environ['APP_PASSWORD'] = new_pw

    session['av'] = credentials.auth_version()   # keep THIS session; every other one is now invalid
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
            fi_dir = os.path.join(os.path.dirname(os.path.abspath(db)), 'foodimg')
            if os.path.isdir(fi_dir):
                for name in os.listdir(fi_dir):
                    zf.write(os.path.join(fi_dir, name), f'foodimg/{name}')

        @after_this_request
        def _cleanup(resp):
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return resp

        return send_file(zip_path, as_attachment=True, download_name='headwind-backup.zip',
                         mimetype='application/zip')
    except Exception as e:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        flash(f'Export failed: {e}', 'error')
        return redirect('/settings')


@bp.route('/backup/import', methods=['POST'])
def backup_import():
    f = request.files.get('backup')
    if not f or not f.filename:
        flash('No file selected.', 'error')
        return redirect('/settings')

    from services import backup
    tmp_dir = None
    try:
        tmp_dir, tmp_db, assets = backup.stage_upload(f)
        backup.validate(tmp_db)
        backup.apply(_db_path(), tmp_db, assets, _avatar_dir())
        migrate_db()   # an older backup may predate current columns/tables
    except backup.BackupError as e:
        flash(str(e), 'error')
        return redirect('/settings')
    except Exception as e:
        flash(f'Restore failed: {e}', 'error')
        return redirect('/settings')
    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    flash('Restored from backup — previous database saved as .pre-restore.', 'success')
    return redirect('/settings')
