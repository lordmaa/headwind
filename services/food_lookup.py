"""Fill in protein/carbs/fat for imported diary items by matching them to known foods.

Order of preference: (1) something the user already logged under the same name with macros,
(2) an Open Food Facts product whose name matches and whose pack size fits the item's calories.
The item's own calories (from the source app) are never changed — macros are scaled to fit them.
"""
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from services import food_cache

STOP = {'recipe', 'homemade', 'the', 'and', 'of', 'with', 'in', 'a', 'x', 'raw', 'cooked', 'skin', 'off', 'pack', 'pk'}
SIZE_RE = re.compile(r'(?:(\d+)\s*x\s*)?(\d+(?:\.\d+)?)\s*(kg|g|ml|l)\b', re.I)


def _tokens(text):
    text = SIZE_RE.sub(' ', text.lower())
    words = (w[:-1] if len(w) > 3 and w.endswith('s') else w for w in re.findall(r'[a-z]+', text))
    return {w for w in words if w not in STOP and len(w) > 1}


def _pack_grams(name):
    """Total pack size in g/ml from text like '250g', '4 x 90ml (360ml)', '6x 25g'. None if absent."""
    sizes = []
    for count, amt, unit in SIZE_RE.findall(name):
        g = float(amt) * (1000 if unit.lower() in ('kg', 'l') else 1)
        sizes.append(g * (int(count) if count else 1))
    return max(sizes) if sizes else None


def _kcal_from_macros(p, c, f):
    return 4 * (p or 0) + 4 * (c or 0) + 9 * (f or 0)


def _from_history(name, kcal):
    from database import query_db
    row = query_db('''
        SELECT calories, protein, carbs, fat FROM FoodLog
        WHERE lower(foodName)=lower(?) AND protein IS NOT NULL AND carbs IS NOT NULL AND fat IS NOT NULL
              AND calories > 0 AND COALESCE(source,'') != 'screenshot_est' ORDER BY id DESC LIMIT 1
    ''', [name], one=True)
    if not row:
        return None
    k = kcal / row['calories']
    return {'protein': round(row['protein'] * k, 1), 'carbs': round(row['carbs'] * k, 1),
            'fat': round(row['fat'] * k, 1), 'confidence': 'high', 'source': 'history',
            'match': 'previously logged'}


def name_key(name):
    return ' '.join(sorted(_tokens(name)))


def _from_recipes(name, kcal):
    """Match against recipes imported from the source app's recipe pages. Macros are scaled by
    calories, so a half portion of a recipe still lands right."""
    from database import query_db
    qtok = _tokens(name)
    if not qtok:
        return None
    best = None
    for r in query_db('SELECT name, nameKey, calories, protein, carbs, fat FROM RecipeBook WHERE calories > 0'):
        if r['protein'] is None or r['carbs'] is None or r['fat'] is None:
            continue
        ctok = set(r['nameKey'].split())
        shared = len(qtok & ctok)
        cov, prec = shared / len(qtok), shared / len(ctok) if ctok else 0
        exact = ctok == qtok
        if not exact and (cov < 0.6 or prec < 0.8):
            continue
        score = (exact, cov * prec)
        if best is None or score > best[0]:
            best = (score, r, exact)
    if not best:
        return None
    _, r, exact = best
    k = kcal / r['calories']
    return {'protein': round(r['protein'] * k, 1), 'carbs': round(r['carbs'] * k, 1), 'fat': round(r['fat'] * k, 1),
            'confidence': 'high' if exact else 'medium', 'source': 'recipe', 'match': r['name']}


def _search_off(query, uk_only=True):
    from routes.nutrition import OFF_FIELDS
    params = {'q': query, 'page_size': 10, 'fields': OFF_FIELDS}
    if uk_only:
        params['countries_tags_en'] = 'United Kingdom'
    try:
        r = requests.get('https://search.openfoodfacts.org/search', params=params, timeout=6,
                         headers={'User-Agent': 'Headwind-Nutrition/1.0'})
        return r.json().get('hits', []) if r.ok else None
    except Exception:
        return None  # None = API unreachable (distinct from [] = reachable, no results)


def _from_off(name, kcal, sink=None):
    from routes.nutrition import _off_product_to_dict, _apply_override
    from services import food_cache
    qtok = _tokens(name)
    if not qtok:
        return None
    pack = _pack_grams(name)
    query = ' '.join(re.sub(r'\([^)]*\)', ' ', SIZE_RE.sub(' ', name)).split())

    best = None
    for uk_only in (True, False):
        hits = _search_off(query, uk_only)
        if hits is None:  # API down — use the local copy instead
            products = food_cache.search(query, limit=10)
        else:
            # Same chain every other lookup path uses — otherwise a user's own corrected macros for a barcode
            # get silently ignored here, unlike search/lookup which always route through _apply_override.
            products = [_apply_override(pp) for pp in (_off_product_to_dict(h) for h in hits) if pp]
            if sink is not None:
                sink.extend(p for p in products if p)
        for p in products:
            if not p or p['protein_per_100g'] is None or p['carbs_per_100g'] is None or p['fat_per_100g'] is None:
                continue
            ctok = _tokens(f"{p['brand']} {p['name']}")
            shared = len(qtok & ctok)
            coverage = shared / len(qtok)          # how much of what we asked for the product has
            precision = shared / len(ctok) if ctok else 0  # how much of the product is what we asked for
            implied_g = kcal / p['kcal_per_100g'] * 100
            size_ok = bool(pack) and abs(implied_g - pack) / pack <= 0.3
            macro_kcal = _kcal_from_macros(p['protein_per_100g'], p['carbs_per_100g'], p['fat_per_100g'])
            if abs(macro_kcal - p['kcal_per_100g']) > 0.35 * p['kcal_per_100g'] + 10:
                continue  # product's own numbers don't add up — untrustworthy entry
            # A wrong flavour/variant is worse than no match, so be strict on wording
            if coverage < 0.75 or precision < 0.6:
                continue
            score = (round(coverage * precision, 1), size_ok, -abs(implied_g - pack) / pack if pack else 0)
            if best is None or score > best[0]:
                best = (score, p, implied_g, size_ok, coverage * precision)
        if best and best[3]:
            break  # confident UK match — no need to widen the search
    if not best:
        return None
    _, p, g, size_ok, coverage = best
    f = g / 100
    return {'protein': round(p['protein_per_100g'] * f, 1), 'carbs': round(p['carbs_per_100g'] * f, 1),
            'fat': round(p['fat_per_100g'] * f, 1),
            'confidence': 'high' if (size_ok and coverage >= 0.9) else 'medium', 'source': 'off',
            'match': f"{p['brand']} {p['name']}".strip()}


def enrich_items(items, progress=None):
    """Adds protein/carbs/fat + a 'lookup' dict to items lacking macros. Mutates and returns items."""
    # history lookups touch sqlite (request-scoped connection) so run them on this thread first
    results = {}
    pending = []
    for i, it in enumerate(items):
        if it.get('protein') is not None and it.get('carbs') is not None and it.get('fat') is not None:
            continue
        h = _from_recipes(it['name'], it['calories']) or _from_history(it['name'], it['calories'])
        if h:
            results[i] = h
        else:
            pending.append(i)
    total = len(pending)
    seen = []  # every OFF product returned, stored afterwards in one go
    food_cache.init_path()
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(_safe_off, items[i], seen): i for i in pending}
        for done, fut in enumerate(as_completed(futures), 1):
            res = fut.result()
            if res:
                results[futures[fut]] = res
            if progress:
                progress('lookup', done, total)
    food_cache.store(seen)
    for i, res in results.items():
        it = items[i]
        it['lookup'] = {k: res[k] for k in ('confidence', 'source', 'match')}
        it['protein'], it['carbs'], it['fat'] = res['protein'], res['carbs'], res['fat']
    return items


def _safe_off(it, sink=None):
    try:
        return _from_off(it['name'], it['calories'], sink)
    except Exception:
        return None
