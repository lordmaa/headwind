"""Sharing user-made foods between Headwind instances (e.g. yours and your partner's).

What is shared: only foods a person wrote themselves — corrected products (FoodOverride, keyed by barcode) and barcode-less
custom foods (CustomFood) — plus their photos. NOT shared: Open Food Facts copies (every instance fetches those itself), food
logs, weights, goals or anything else. AI photo estimates are never in either table, so they never travel.

How it works: each instance serves `GET /api/foods-feed` (same feed token as the ride feed). Friends pull it on the normal
sync timer into their own SharedFood table. Lookup order in the app is: your own edit -> a friend's shared food -> Open Food
Facts -> local cache. A shared food never overwrites anything of yours; to keep your own version you just edit the food.
Each sync replaces that friend's rows completely (only after the whole feed arrived), so their edits and deletions follow.
"""
import json
import logging
import urllib.error
import urllib.request

from database import get_db, query_db
from services.limits import bounded_lines
from services.peer_http import open_peer

log = logging.getLogger(__name__)
MAX_PHOTO = 8 * 1024 * 1024


def _norm(name):
    return ' '.join((name or '').lower().split())


def feed_records():
    """This instance's own user-made foods (what /api/foods-feed sends)."""
    for r in query_db('SELECT * FROM FoodOverride ORDER BY barcode'):
        yield {'type': 'food', 'kind': 'override', 'key': r['barcode'], 'barcode': r['barcode'], 'name': r['name'],
               'brand': r['brand'], 'kcal100g': r['kcal100g'], 'protein100g': r['protein100g'], 'carbs100g': r['carbs100g'],
               'fat100g': r['fat100g'], 'fibre100g': r['fibre100g'], 'servingG': r['servingG'], 'imageUrl': r['imageUrl'],
               'nutriScoreGrade': r['nutriScoreGrade'], 'updatedAt': r['updatedAt']}
    owner = query_db('SELECT id FROM Rider WHERE isDefault=1 LIMIT 1', one=True)
    if owner:
        for r in query_db('SELECT * FROM CustomFood WHERE riderId=? ORDER BY name COLLATE NOCASE', [owner['id']]):
            yield {'type': 'food', 'kind': 'custom', 'key': _norm(r['name']), 'barcode': None, 'name': r['name'], 'brand': r['brand'],
                   'kcal100g': r['calories'], 'protein100g': r['protein'], 'carbs100g': r['carbs'], 'fat100g': r['fat'],
                   'fibre100g': r['fibre100g'], 'servingG': r['servingG'], 'imageUrl': r['imageUrl'], 'updatedAt': r['createdAt']}


def _get(url, token, timeout=30):
    headers = {'User-Agent': 'Headwind/1.0'}
    if token:
        headers['X-Feed-Token'] = token
    return open_peer(url, headers, timeout=timeout)


def _fetch_photo(friend, ref):
    """Download a friend's uploaded photo ('local:<sha>.<ext>') once; returns our own local reference, or None."""
    from services import food_cache
    food_cache.init_path()
    if food_cache.local_image(ref):
        return ref                                   # already have the identical file (the name is a hash of its content)
    try:
        data = _get(friend['url'].rstrip('/') + '/api/food-image/' + ref[len('local:'):], friend['token']).read(MAX_PHOTO + 1)
        if len(data) > MAX_PHOTO:
            return None
        return food_cache.save_local_image(data, ref.rsplit('.', 1)[-1])
    except Exception as e:
        log.warning('Shared foods: photo %s from %s failed: %s', ref, friend['name'], e)
        return None


def sync_friend(friend):
    """Pull one friend's foods. Returns (count, error). Never raises."""
    url = friend['url'].rstrip('/') + '/api/foods-feed'
    try:
        resp = _get(url, friend['token'])
    except urllib.error.HTTPError as e:
        return 0, ('friend has not updated Headwind yet (no foods feed)' if e.code == 404 else f'HTTP {e.code}')
    except Exception as e:
        return 0, str(e)
    rows, origin, complete = [], friend['name'], False
    try:
        for raw in bounded_lines(resp, 20 * 1024 ** 2, 100_000, 120):
            line = raw.strip()
            if not line:
                continue
            obj = json.loads(line.decode())
            if obj.get('type') == 'meta':
                origin = obj.get('name') or origin
            elif obj.get('type') == 'food' and obj.get('kind') in ('override', 'custom') and obj.get('key') and obj.get('name'):
                rows.append(obj)
            elif obj.get('type') == 'end':
                complete = True
    except Exception as e:
        return 0, f'feed interrupted: {e}'
    if not complete:
        return 0, 'feed incomplete - nothing changed'      # never prune on a partial read
    db = get_db()
    keep = set()
    for r in rows:
        img = r.get('imageUrl')
        if img and str(img).startswith('local:'):
            img = _fetch_photo(friend, img)
        elif img and not str(img).startswith('https://'):
            img = None
        db.execute('''
            INSERT INTO SharedFood (friendId, origin, kind, key, barcode, name, brand, kcal100g, protein100g, carbs100g, fat100g,
                                    fibre100g, servingG, imageUrl, nutriScoreGrade, srcUpdatedAt, syncedAt)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, datetime('now'))
            ON CONFLICT(friendId, kind, key) DO UPDATE SET
                origin=excluded.origin, barcode=excluded.barcode, name=excluded.name, brand=excluded.brand, kcal100g=excluded.kcal100g,
                protein100g=excluded.protein100g, carbs100g=excluded.carbs100g, fat100g=excluded.fat100g, fibre100g=excluded.fibre100g,
                servingG=excluded.servingG, imageUrl=excluded.imageUrl, nutriScoreGrade=excluded.nutriScoreGrade,
                srcUpdatedAt=excluded.srcUpdatedAt, syncedAt=datetime('now')
        ''', [friend['id'], origin, r['kind'], str(r['key']), r.get('barcode'), r['name'], r.get('brand') or '', r.get('kcal100g'),
              r.get('protein100g'), r.get('carbs100g'), r.get('fat100g'), r.get('fibre100g'), r.get('servingG'), img,
              r.get('nutriScoreGrade'), r.get('updatedAt')])
        keep.add((r['kind'], str(r['key'])))
    for old in db.execute('SELECT id, kind, key FROM SharedFood WHERE friendId=?', [friend['id']]).fetchall():
        if (old['kind'], old['key']) not in keep:
            db.execute('DELETE FROM SharedFood WHERE id=?', [old['id']])
    db.execute("UPDATE Friend SET lastFoodSync=datetime('now') WHERE id=?", [friend['id']])
    db.commit()
    log.info('Shared foods: %d food(s) from %s', len(rows), origin)
    return len(rows), None


def _as_product(r):
    return {
        'barcode': r['barcode'], 'name': r['name'], 'brand': r['brand'] or '',
        'kcal_per_100g': r['kcal100g'], 'protein_per_100g': r['protein100g'], 'carbs_per_100g': r['carbs100g'],
        'fat_per_100g': r['fat100g'], 'fibre_per_100g': r['fibre100g'], 'serving_g': r['servingG'],
        'image_url': r['imageUrl'], 'nutri_score_grade': r['nutriScoreGrade'],
        'shared_from': r['origin'],
        '_overridden' if r['kind'] == 'override' else '_custom': True,
    }


def by_barcode(barcode):
    """A friend's corrected version of this barcode, as a product dict (or None). Latest edit wins if several friends have one."""
    if not barcode:
        return None
    r = query_db("SELECT * FROM SharedFood WHERE barcode=? AND kind='override' ORDER BY srcUpdatedAt DESC LIMIT 1", [barcode], one=True)
    return _as_product(r) if r else None


def overlay(product, shared):
    """Apply a shared food on top of an Open Food Facts product (same merge the local override does)."""
    return {
        **product,
        'name': shared['name'],
        'brand': shared.get('brand') or product.get('brand', ''),
        'kcal_per_100g': shared['kcal_per_100g'], 'protein_per_100g': shared['protein_per_100g'],
        'carbs_per_100g': shared['carbs_per_100g'], 'fat_per_100g': shared['fat_per_100g'],
        'fibre_per_100g': shared['fibre_per_100g'] if shared.get('fibre_per_100g') is not None else product.get('fibre_per_100g'),
        'serving_g': shared.get('serving_g') or product.get('serving_g'),
        'image_url': shared.get('image_url') or product.get('image_url'),
        'nutri_score_grade': shared.get('nutri_score_grade') or product.get('nutri_score_grade'),
        'shared_from': shared['shared_from'],
        '_overridden': True,
    }


def search(q, limit=8):
    """Friends' barcode-less custom foods whose name/brand contains every word of the query."""
    words = [w for w in _norm(q).split() if w]
    if not words:
        return []
    cond = ' AND '.join(["lower(name || ' ' || coalesce(brand,'')) LIKE ?"] * len(words))
    rows = query_db(f"SELECT * FROM SharedFood WHERE kind='custom' AND {cond} ORDER BY name COLLATE NOCASE LIMIT ?",
                    [f'%{w}%' for w in words] + [limit])
    return [_as_product(r) for r in rows]
