"""Local copy of every Open Food Facts product we've seen, so search and barcode lookups still work
when the API is down and common foods come back instantly.

Uses its own short-lived SQLite connections (not Flask's request-scoped one) so it is safe to call from
worker threads. Call init_path() once from a request/app context before using it in threads.
"""
import hashlib
import re
import sqlite3

_path = None
FRESH_DAYS = 14


def init_path():
    global _path
    if _path is None:
        from flask import current_app
        _path = current_app.config['DATABASE']
    return _path


def _conn():
    c = sqlite3.connect(init_path(), timeout=30)
    c.row_factory = sqlite3.Row
    return c


def _key(p):
    if p.get('barcode'):
        return str(p['barcode'])
    return 'n:' + hashlib.sha1(f"{(p.get('name') or '').lower()}|{(p.get('brand') or '').lower()}".encode()).hexdigest()[:16]


def _search_text(p):
    return re.sub(r'[^a-z0-9]+', ' ', f"{p.get('brand') or ''} {p.get('name') or ''}".lower()).strip()


def store(products):
    """Upsert products (as produced by routes.nutrition._off_product_to_dict). Never raises."""
    rows = [p for p in products if p and p.get('name') and p.get('kcal_per_100g') is not None]
    if not rows:
        return
    try:
        with _conn() as c:
            for p in rows:
                c.execute('''
                    INSERT INTO FoodCache (key, barcode, name, brand, searchText, kcal100g, protein100g, carbs100g,
                                           fat100g, sugars100g, fibre100g, servingG, imageUrl, nutriScoreGrade, fetchedAt)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?, datetime('now'))
                    ON CONFLICT(key) DO UPDATE SET
                        name=excluded.name, brand=excluded.brand, searchText=excluded.searchText,
                        kcal100g=excluded.kcal100g, protein100g=excluded.protein100g, carbs100g=excluded.carbs100g,
                        fat100g=excluded.fat100g, sugars100g=excluded.sugars100g,
                        fibre100g=COALESCE(excluded.fibre100g, fibre100g), servingG=excluded.servingG,
                        imageUrl=COALESCE(excluded.imageUrl, imageUrl),
                        nutriScoreGrade=COALESCE(excluded.nutriScoreGrade, nutriScoreGrade),
                        fetchedAt=datetime('now')
                ''', [_key(p), p.get('barcode') or None, p['name'], p.get('brand') or '', _search_text(p),
                      p['kcal_per_100g'], p.get('protein_per_100g'), p.get('carbs_per_100g'), p.get('fat_per_100g'),
                      p.get('_sugars_per_100g'), p.get('fibre_per_100g'), p.get('serving_g'), p.get('image_url'), p.get('nutri_score_grade')])
    except Exception:
        pass


def _row_to_product(r):
    return {
        'barcode': r['barcode'] or '', 'name': r['name'], 'brand': r['brand'] or '',
        'kcal_per_100g': r['kcal100g'], 'protein_per_100g': r['protein100g'],
        'carbs_per_100g': r['carbs100g'], 'fat_per_100g': r['fat100g'],
        'serving_g': r['servingG'], 'image_url': r['imageUrl'], 'nutri_score_grade': r['nutriScoreGrade'],
        '_sugars_per_100g': r['sugars100g'], 'fibre_per_100g': r['fibre100g'], 'cached': True,
    }


def search(query, limit=12):
    """Products whose brand+name contain every word of the query, most-used first."""
    words = [w for w in re.findall(r'[a-z0-9]+', query.lower()) if len(w) > 1 or w.isdigit()]
    if not words:
        return []
    where = ' AND '.join('searchText LIKE ?' for _ in words)
    args = [f'%{w}%' for w in words]
    try:
        with _conn() as c:
            rows = c.execute(f'''
                SELECT * FROM FoodCache WHERE {where}
                ORDER BY useCount DESC, (searchText LIKE ?) DESC, length(name) ASC LIMIT ?
            ''', args + [words[0] + '%', limit]).fetchall()
        return [_row_to_product(r) for r in rows]
    except Exception:
        return []


def get_by_barcode(barcode, max_age_days=None):
    """Cached product for a barcode; if max_age_days is set, only when fetched within that window."""
    try:
        with _conn() as c:
            sql = 'SELECT * FROM FoodCache WHERE key=?'
            args = [str(barcode)]
            if max_age_days is not None:
                sql += " AND fetchedAt >= datetime('now', ?)"
                args.append(f'-{int(max_age_days)} days')
            r = c.execute(sql, args).fetchone()
        return _row_to_product(r) if r else None
    except Exception:
        return None


def bump_use(barcode):
    """A logged food floats to the top of offline search results."""
    try:
        with _conn() as c:
            c.execute("UPDATE FoodCache SET useCount=useCount+1, lastUsed=datetime('now') WHERE key=?", [str(barcode)])
    except Exception:
        pass


def stats():
    try:
        with _conn() as c:
            return c.execute('SELECT COUNT(*) FROM FoodCache').fetchone()[0]
    except Exception:
        return 0


# ── Product photos ─────────────────────────────────────────────────────
# Stored on disk next to the database (so they persist with it) and served from there first.
import os
import threading

IMAGE_HOSTS = ('openfoodfacts.org', 'openfoodfacts.net', 'wikimedia.org')
_EXT = {'image/jpeg': 'jpg', 'image/png': 'png', 'image/webp': 'webp', 'image/gif': 'gif'}
_MIME = {v: k for k, v in _EXT.items()}
_img_lock = threading.Lock()


def _image_dir():
    d = os.path.join(os.path.dirname(init_path()), 'foodimg')
    os.makedirs(d, exist_ok=True)
    return d


def image_allowed(url):
    from urllib.parse import urlparse
    host = urlparse(url).netloc.lower()
    return url.startswith('https://') and any(host == h or host.endswith('.' + h) for h in IMAGE_HOSTS)


def cached_image(url):
    """(bytes, mimetype) if this photo is already on disk, else None."""
    base = os.path.join(_image_dir(), hashlib.sha1(url.encode()).hexdigest())
    for ext, mime in _MIME.items():
        if os.path.exists(f'{base}.{ext}'):
            with open(f'{base}.{ext}', 'rb') as fh:
                return fh.read(), mime
    return None


def fetch_image(url, timeout=8):
    """Photo from disk, else download and keep it. None if unavailable."""
    import requests
    if not image_allowed(url):
        return None
    hit = cached_image(url)
    if hit:
        return hit
    try:
        r = requests.get(url, timeout=timeout, headers={'User-Agent': 'Headwind-Nutrition/1.0'}, allow_redirects=False)
        if r.is_redirect and image_allowed(r.headers.get('Location', '')):   # follow one hop, only to another allowed host
            r = requests.get(r.headers['Location'], timeout=timeout, headers={'User-Agent': 'Headwind-Nutrition/1.0'}, allow_redirects=False)
        mime = r.headers.get('Content-Type', 'image/jpeg').split(';')[0].strip()
        if not r.ok or mime not in _EXT or len(r.content) > 2_000_000:
            return None
        path = os.path.join(_image_dir(), f'{hashlib.sha1(url.encode()).hexdigest()}.{_EXT[mime]}')
        with _img_lock:
            tmp = path + '.tmp'
            with open(tmp, 'wb') as fh:
                fh.write(r.content)
            os.replace(tmp, path)
        return r.content, mime
    except Exception:
        return None


def image_stats():
    try:
        files = [f for f in os.listdir(_image_dir()) if not f.endswith('.tmp')]
        return len(files)
    except Exception:
        return 0


_LOCAL_RE = re.compile(r'^local:([0-9a-f]{40})\.(jpg|png|webp|gif)$')


def is_local(url):
    return bool(_LOCAL_RE.match(url or ''))


def local_image(url):
    """(bytes, mimetype) for a 'local:<sha>.<ext>' reference (a photo the user picked or uploaded)."""
    m = _LOCAL_RE.match(url or '')
    if not m:
        return None
    path = os.path.join(_image_dir(), f'{m.group(1)}.{m.group(2)}')
    if not os.path.exists(path):
        return None
    with open(path, 'rb') as fh:
        return fh.read(), _MIME[m.group(2)]


def save_local_image(data, ext='jpg'):
    """Keep image bytes on disk; returns the 'local:' reference to store in the database."""
    sha = hashlib.sha1(data).hexdigest()
    path = os.path.join(_image_dir(), f'{sha}.{ext}')
    if not os.path.exists(path):
        with _img_lock:
            with open(path + '.tmp', 'wb') as fh:
                fh.write(data)
            os.replace(path + '.tmp', path)
    return f'local:{sha}.{ext}'
