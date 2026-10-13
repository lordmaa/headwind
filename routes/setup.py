import json
import os
import zipfile
import shutil
import tempfile

from flask import Blueprint, redirect, render_template, request
from database import get_db, query_db
from services import homeassistant as ha

bp = Blueprint('setup', __name__)

# Step 1 (riders) is the only one gated on "no riders yet" — once it creates the owner rider,
# steps 2-6 must keep working even though Rider count is now > 0 (see check below).
# 1 riders, 2 units, 3 garmin, 4 home assistant, 5 telemetry, 6 done.
_STEPS = ('1', '2', '3', '4', '5', '6')


@bp.route('/setup', methods=['GET', 'POST'])
def wizard():
    step = request.args.get('step', '1')
    if step not in _STEPS:
        step = '1'
    has_riders = query_db('SELECT COUNT(*) FROM Rider', one=True)[0] > 0

    # A completed install visiting /setup (bare, or re-POSTing step 1) goes straight to the dashboard.
    # Mid-wizard steps (2-4) stay reachable — and POST-able — even after step 1 has created the owner
    # rider; this must NOT be GET-only, or a POST to this auth-exempt route after install creates a
    # second isDefault=1 rider and every "WHERE isDefault=1 LIMIT 1" owner lookup becomes order-dependent.
    if has_riders and step == '1':
        return redirect('/dashboard')

    if request.method == 'POST':
        if step == '1':
            names = [n.strip() for n in request.form.getlist('riders[]') if n.strip()]
            from services.rider_mode import multi_rider
            if not multi_rider():
                names = names[:1]
            if not names:
                return render_template('setup.html', step='1')
            db = get_db()
            for i, name in enumerate(names):
                db.execute(
                    "INSERT INTO Rider (name, avatarPath, isDefault) VALUES (?, 'custard_cream.svg', ?)",
                    [name, 1 if i == 0 else 0],
                )
            db.commit()
            return redirect('/setup?step=2')

        if step == '2':
            units = request.form.get('units', 'imperial')
            if units not in ('imperial', 'metric'):
                units = 'imperial'
            db = get_db()
            db.execute('''
                INSERT INTO Settings (id, units) VALUES (1, ?)
                ON CONFLICT(id) DO UPDATE SET units=excluded.units
            ''', [units])
            db.commit()
            return redirect('/setup?step=3')

        if step == '5':
            from services import telemetry
            if request.form.get('telemetry') == 'off':
                telemetry.opt_out()
            else:
                telemetry.ping_once()
            return redirect('/setup?step=6')

    s = query_db('SELECT * FROM Settings WHERE id=1', one=True)
    try:
        ha_mappings = json.loads((s['haEntityMap'] if s else None) or '[]')
    except ValueError:
        ha_mappings = []
    return render_template(
        'setup.html', step=step, s=s,
        ha_targets=ha.TARGETS, ha_mappings=ha_mappings,
        ha_has_token=bool(s and s['haToken']),
        ha_weather_entity=(s['haWeatherEntity'] if s else None) or '',
    )


@bp.route('/setup/restore', methods=['POST'])
def restore():
    if query_db('SELECT COUNT(*) FROM Rider', one=True)[0] > 0:
        return redirect('/dashboard')

    f = request.files.get('backup')
    if not f or not f.filename:
        return render_template('setup.html', step='1', error='No file selected.')

    from services import backup
    from routes.settings import _db_path, _avatar_dir
    from database import migrate_db
    tmp_dir = None
    try:
        tmp_dir, tmp_db, assets = backup.stage_upload(f)
        if backup.validate(tmp_db) == 0:
            return render_template('setup.html', step='1', error='That backup has no riders — download a backup from a working Headwind instance.')
        backup.prepare(tmp_db)
        backup.apply(_db_path(), tmp_db, assets, _avatar_dir())
        migrate_db()
    except backup.BackupError as e:
        return render_template('setup.html', step='1', error=str(e))
    except Exception as e:
        return render_template('setup.html', step='1', error=f'Could not restore backup: {e}')
    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    return redirect('/dashboard')
