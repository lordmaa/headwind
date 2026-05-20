import os
import shutil
import sqlite3
import tempfile
from pathlib import Path

from dotenv import load_dotenv, set_key
from flask import Blueprint, flash, redirect, render_template, request, send_file
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

        db.execute('''
            INSERT INTO Settings (id, aiProvider, openaiKey, openaiModel, ollamaUrl, ollamaModel,
                                  mqttHost, mqttPort, mqttUser, mqttPassword,
                                  garminEmail, garminPassword, garminSyncHours)
            VALUES (1,?,?,?,?,?,?,?,?,?,?,?,?)
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
                garminSyncHours=excluded.garminSyncHours
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
        ])
        db.commit()
        flash('Settings saved.', 'success')
        return redirect('/settings')

    s = query_db('SELECT * FROM Settings WHERE id=1', one=True)
    current_username = os.environ.get('APP_USERNAME', '')
    return render_template('settings.html', s=s, openai_models=OPENAI_MODELS,
                           current_username=current_username)


@bp.route('/weather-backfill', methods=['POST'])
def weather_backfill():
    from flask import jsonify
    from database import get_db
    from services.weather import fetch_weather, save_weather

    db = get_db()
    rows = db.execute('''
        SELECT id, startLat, startLng, startDateLocal, streams
        FROM Activity
        WHERE startLat IS NOT NULL AND startLng IS NOT NULL
          AND weatherSummary IS NULL
        ORDER BY startDateLocal DESC
        LIMIT 100
    ''').fetchall()

    done, failed = 0, 0
    for r in rows:
        try:
            w = fetch_weather(r['startLat'], r['startLng'], r['startDateLocal'], r['streams'])
            if w:
                save_weather(db, r['id'], w)
                done += 1
        except Exception:
            failed += 1

    db.commit()
    remaining = db.execute('''
        SELECT COUNT(*) FROM Activity
        WHERE startLat IS NOT NULL AND startLng IS NOT NULL AND weatherSummary IS NULL
    ''').fetchone()[0]

    return jsonify(done=done, failed=failed, remaining=remaining)


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


@bp.route('/backup/export')
def backup_export():
    from flask import current_app
    db_path = current_app.config.get('DATABASE_URL') or os.environ.get('DATABASE_URL', '')
    db_path = db_path.replace('sqlite:///', '')
    if not db_path or not Path(db_path).exists():
        flash('Database file not found.', 'error')
        return redirect('/settings')

    # Use SQLite's online backup API so we get a consistent snapshot even under load
    tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    tmp.close()
    try:
        src = sqlite3.connect(db_path)
        dst = sqlite3.connect(tmp.name)
        src.backup(dst)
        src.close()
        dst.close()
        return send_file(tmp.name, as_attachment=True, download_name='headwind-backup.db',
                         mimetype='application/octet-stream')
    except Exception as e:
        flash(f'Export failed: {e}', 'error')
        return redirect('/settings')


@bp.route('/backup/import', methods=['POST'])
def backup_import():
    from flask import current_app
    f = request.files.get('backup')
    if not f or not f.filename:
        flash('No file selected.', 'error')
        return redirect('/settings')

    db_path = current_app.config.get('DATABASE_URL') or os.environ.get('DATABASE_URL', '')
    db_path = db_path.replace('sqlite:///', '')
    if not db_path:
        flash('Could not determine database path.', 'error')
        return redirect('/settings')

    # Write upload to a temp file and validate it's a real SQLite DB with expected tables
    tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    try:
        f.save(tmp.name)
        conn = sqlite3.connect(tmp.name)
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        conn.close()
        required = {'Rider', 'Activity', 'Settings'}
        if not required.issubset(tables):
            flash(f'Invalid backup — missing tables: {required - tables}', 'error')
            return redirect('/settings')
    except Exception as e:
        flash(f'Invalid database file: {e}', 'error')
        return redirect('/settings')

    # Back up the current DB alongside it, then replace
    backup_of_current = db_path + '.pre-restore'
    shutil.copy2(db_path, backup_of_current)
    shutil.move(tmp.name, db_path)

    flash('Database restored from backup. Previous database saved as .pre-restore alongside the db file.', 'success')
    return redirect('/settings')
