import os
import zipfile
import tempfile

from flask import Blueprint, redirect, render_template, request
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

    tmp_dir = tempfile.mkdtemp()
    try:
        upload = os.path.join(tmp_dir, f.filename)
        f.save(upload)

        if zipfile.is_zipfile(upload):
            with zipfile.ZipFile(upload) as zf:
                if 'headwind.db' not in zf.namelist():
                    return render_template('setup.html', error='Invalid backup zip — headwind.db not found inside.')
                zf.extractall(tmp_dir)
            tmp_db = os.path.join(tmp_dir, 'headwind.db')
            avatar_src = os.path.join(tmp_dir, 'avatars')
        else:
            tmp_db = upload
            avatar_src = None

        from routes.settings import _validate_db, _apply_restore
        tables, rider_count = _validate_db(tmp_db)
        required = {'Rider', 'Activity', 'Settings'}
        if not required.issubset(tables):
            return render_template('setup.html', error=f'Invalid backup — missing tables: {required - tables}')
        if rider_count == 0:
            return render_template('setup.html', error='That backup has no riders — download a backup from a working Headwind instance.')

        _apply_restore(tmp_db, avatar_src if avatar_src and os.path.isdir(avatar_src) else None)
    except Exception as e:
        return render_template('setup.html', error=f'Could not restore backup: {e}')

    return redirect('/dashboard')
