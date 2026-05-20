import os
import shutil
import sqlite3
import tempfile
from pathlib import Path

from flask import Blueprint, current_app, flash, redirect, render_template, request
from database import get_db, query_db

bp = Blueprint('setup', __name__)


@bp.route('/setup', methods=['GET', 'POST'])
def wizard():
    if query_db('SELECT COUNT(*) FROM Rider', one=True)[0] > 0:
        return redirect('/dashboard')

    if request.method == 'POST':
        names = [n.strip() for n in request.form.getlist('riders[]') if n.strip()]
        if names:
            db = get_db()
            for i, name in enumerate(names):
                db.execute(
                    "INSERT INTO Rider (name, avatarPath, isDefault) VALUES (?, 'custard_cream.svg', ?)",
                    [name, 1 if i == 0 else 0],
                )
            db.commit()
            return redirect('/dashboard')

    return render_template('setup.html')


@bp.route('/setup/restore', methods=['POST'])
def restore():
    if query_db('SELECT COUNT(*) FROM Rider', one=True)[0] > 0:
        return redirect('/dashboard')

    f = request.files.get('backup')
    if not f or not f.filename:
        return render_template('setup.html', error='No file selected.')

    db_path = current_app.config.get('DATABASE_URL') or os.environ.get('DATABASE_URL', '')
    db_path = db_path.replace('sqlite:///', '')

    tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    try:
        f.save(tmp.name)
        conn = sqlite3.connect(tmp.name)
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        required = {'Rider', 'Activity', 'Settings'}
        if not required.issubset(tables):
            conn.close()
            os.unlink(tmp.name)
            return render_template('setup.html', error=f'Invalid backup — missing tables: {required - tables}')
        rider_count = conn.execute('SELECT COUNT(*) FROM Rider').fetchone()[0]
        conn.close()
        if rider_count == 0:
            os.unlink(tmp.name)
            return render_template('setup.html', error='That backup has no riders — it may be an empty or cleared database. Download a backup from a working Headwind instance.')
    except Exception as e:
        return render_template('setup.html', error=f'Could not read backup file: {e}')

    shutil.move(tmp.name, db_path)
    return redirect('/dashboard')
