"""Safe backup staging + restore. Uploads never choose their own filesystem paths, zips are read member-by-member
with size limits, and the live database is replaced through SQLite's backup API (atomic, WAL-aware, no file swap)."""
import os
import re
import shutil
import sqlite3
import tempfile
import zipfile

MAX_DB_BYTES = 2 * 1024 ** 3
MAX_AVATAR_BYTES = 5 * 1024 ** 2
MAX_AVATARS = 500
REQUIRED_TABLES = {'Rider', 'Activity', 'Settings'}
_AVATAR_RE = re.compile(r'^avatars/(rider_\d+\.(?:jpg|png|gif|webp))$')
_FOODIMG_RE = re.compile(r'^foodimg/([A-Za-z0-9_.-]{1,120}\.(?:jpg|png|gif|webp))$')
MAX_FOODIMGS = 50_000
MAX_FOODIMG_BYTES = 10 * 1024 ** 2


class BackupError(Exception):
    pass


def _copy_member(zf, info, dest, limit):
    if info.file_size > limit:
        raise BackupError(f'{info.filename} is too large')
    written = 0
    with zf.open(info) as src, open(dest, 'wb') as out:
        while chunk := src.read(1 << 20):
            written += len(chunk)
            if written > limit:   # trust the stream, not the zip header
                raise BackupError(f'{info.filename} is too large')
            out.write(chunk)


def stage_upload(file_storage):
    """Save an uploaded .db or backup .zip under a fresh temp dir with generated names.
    Returns (tmp_dir, db_path, assets) where assets maps 'avatars'/'foodimg' to staged dirs (None for a bare .db). Caller must shutil.rmtree(tmp_dir)."""
    tmp_dir = tempfile.mkdtemp(prefix='hw-restore-')
    try:
        upload = os.path.join(tmp_dir, 'upload.bin')
        file_storage.save(upload)
        db_path = os.path.join(tmp_dir, 'staged.db')
        if not zipfile.is_zipfile(upload):
            os.replace(upload, db_path)
            return tmp_dir, db_path, {}
        av_dir = os.path.join(tmp_dir, 'avatars')
        fi_dir = os.path.join(tmp_dir, 'foodimg')
        os.makedirs(av_dir)
        os.makedirs(fi_dir)
        found_db, n_av, n_fi = False, 0, 0
        with zipfile.ZipFile(upload) as zf:
            for info in zf.infolist():
                if info.filename == 'headwind.db':
                    _copy_member(zf, info, db_path, MAX_DB_BYTES)
                    found_db = True
                elif (m := _AVATAR_RE.match(info.filename)):
                    n_av += 1
                    if n_av > MAX_AVATARS:
                        raise BackupError('too many avatar files')
                    _copy_member(zf, info, os.path.join(av_dir, m.group(1)), MAX_AVATAR_BYTES)
                elif (m := _FOODIMG_RE.match(info.filename)):
                    n_fi += 1
                    if n_fi > MAX_FOODIMGS:
                        raise BackupError('too many food images')
                    _copy_member(zf, info, os.path.join(fi_dir, m.group(1)), MAX_FOODIMG_BYTES)
                # anything else in the archive is ignored
        if not found_db:
            raise BackupError('Invalid backup zip — headwind.db not found inside.')
        os.remove(upload)
        return tmp_dir, db_path, {'avatars': av_dir, 'foodimg': fi_dir}
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise


def validate(db_path):
    """Returns rider_count; raises BackupError if the file is not a sound Headwind database."""
    try:
        conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
        try:
            if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise BackupError('Backup database failed its integrity check.')
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not REQUIRED_TABLES.issubset(tables):
                raise BackupError(f'Invalid backup — missing tables: {REQUIRED_TABLES - tables}')
            return conn.execute('SELECT COUNT(*) FROM Rider').fetchone()[0]
        finally:
            conn.close()
    except sqlite3.DatabaseError as e:
        raise BackupError(f'Not a valid SQLite database: {e}')


def apply(live_db, staged_db, assets, avatar_dest):
    """Snapshot the live DB to <live>.pre-restore (consistent, includes WAL), then copy the staged DB over it
    via the backup API in one atomic step. Leaves the live DB untouched if anything fails."""
    snap = sqlite3.connect(live_db + '.pre-restore')
    live = sqlite3.connect(live_db, timeout=60)
    try:
        live.backup(snap)
    finally:
        snap.close()
        live.close()
    src = sqlite3.connect(f'file:{staged_db}?mode=ro', uri=True)
    dst = sqlite3.connect(live_db, timeout=60)
    try:
        src.backup(dst)
    finally:
        src.close()
        dst.close()
    for key, dest in (('avatars', avatar_dest), ('foodimg', os.path.join(os.path.dirname(os.path.abspath(live_db)), 'foodimg'))):
        src_dir = (assets or {}).get(key)
        if src_dir and os.path.isdir(src_dir):
            os.makedirs(dest, exist_ok=True)
            for name in os.listdir(src_dir):
                shutil.copy2(os.path.join(src_dir, name), os.path.join(dest, name))
