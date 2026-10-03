"""Extract food-diary entries from screenshots of another nutrition app, via the configured AI provider."""
import base64
import io
import json
from concurrent.futures import ThreadPoolExecutor, as_completed

from PIL import Image

from services.ai import _get_client_and_model

MEAL_KEYS = ['breakfast', 'lunch', 'dinner', 'snacks', 'ride_fuel', 'recovery']
MAX_EDGE = 1600  # px — plenty for reading diary text, keeps token cost down

PROMPT = """This is a screenshot (one scroll position) of a food diary from a nutrition-tracking app. Extract every logged food item.

Return ONLY a JSON object of this shape:
{
  "items": [
    {"name": "Food name (include brand/quantity text if shown, e.g. 'Weetabix x2')",
     "meal_type": "breakfast|lunch|dinner|snacks|uncategorised",
     "calories": number, "protein": number|null, "carbs": number|null, "fat": number|null}
  ],
  "day_totals": {"calories": number|null, "protein": number|null, "carbs": number|null, "fat": number|null}
}

Rules:
- Calories/macros per item are the TOTAL for the amount eaten as shown, not per 100g.
- Only use numbers visible in the screenshot; use null for macros that aren't shown. Never invent values.
- If only meal-level totals are shown (no individual items), return one item per meal named e.g. 'Breakfast (imported total)'.
- meal_type: use the meal heading the item sits under; 'uncategorised' if unclear.
- day_totals: the daily total shown on screen, or null if none is visible.
- Ignore any row that is cut off by the top or bottom edge of the screen so its name or numbers aren't fully readable.
- List items in the exact top-to-bottom order they appear. Do not merge or drop repeated items — if the same food appears twice on screen, list it twice."""


def _prepare(raw):
    img = Image.open(io.BytesIO(raw))
    img = img.convert('RGB')
    img.thumbnail((MAX_EDGE, MAX_EDGE))
    buf = io.BytesIO()
    img.save(buf, 'JPEG', quality=85)
    return 'data:image/jpeg;base64,' + base64.b64encode(buf.getvalue()).decode()


def _num(v):
    try:
        return None if v is None else round(float(v), 1)
    except (TypeError, ValueError):
        return None


def _read_one(client, model, raw):
    content = [{'type': 'text', 'text': PROMPT},
               {'type': 'image_url', 'image_url': {'url': _prepare(raw), 'detail': 'high'}}]
    resp = client.chat.completions.create(
        model=model, messages=[{'role': 'user', 'content': content}],
        response_format={'type': 'json_object'}, temperature=0,
    )
    data = json.loads(resp.choices[0].message.content)
    items = []
    for it in data.get('items') or []:
        name = str(it.get('name') or '').strip()
        kcal = _num(it.get('calories'))
        if not name or kcal is None:
            continue
        meal = it.get('meal_type') if it.get('meal_type') in MEAL_KEYS else 'uncategorised'
        items.append({'name': name[:120], 'meal_type': meal, 'calories': kcal,
                      'protein': _num(it.get('protein')), 'carbs': _num(it.get('carbs')),
                      'fat': _num(it.get('fat'))})
    totals = data.get('day_totals') or {}
    return items, {k: _num(totals.get(k)) for k in ('calories', 'protein', 'carbs', 'fat')}


def _key(it):
    return (it['name'].lower(), round(it['calories']))


def merge_scrolls(pages):
    """Join per-screenshot item lists from consecutive scroll positions.

    Adjacent screenshots overlap, so the tail of page N repeats at the head of page N+1. Drop the
    longest run where the end of what we have so far equals the start of the next page. Repeats that
    are NOT part of such an overlap (two bananas in one day) survive. Returns (items, dropped_count).
    """
    merged, dropped = [], 0
    for page in pages:
        overlap = 0
        for n in range(min(len(merged), len(page)), 0, -1):
            if [_key(i) for i in merged[-n:]] == [_key(i) for i in page[:n]]:
                overlap = n
                break
        dropped += overlap
        fresh = [dict(i) for i in page[overlap:]]
        # A meal heading that scrolled off the top leaves the first rows 'uncategorised' — inherit the
        # meal we were in at the end of the previous page.
        if merged and fresh:
            carry = merged[-1]['meal_type']
            for i in fresh:
                if i['meal_type'] != 'uncategorised':
                    break
                i['meal_type'] = carry
        merged.extend(fresh)
    return merged, dropped


def extract_foods(images, progress=None):
    """images: raw image bytes in scroll order (top of diary first). Returns
    {'items': [...], 'day_totals': {...}, 'overlap_dropped': n}. Raises ValueError with a user-facing message."""
    client, model = _get_client_and_model()
    if client is None:
        raise ValueError('No AI provider configured — add an OpenAI key in Settings.')
    for raw in images:
        try:
            _prepare(raw)
        except Exception:
            raise ValueError('One of the files is not a readable image.')

    results = [None] * len(images)
    try:
        with ThreadPoolExecutor(max_workers=min(4, len(images))) as pool:
            futures = {pool.submit(_read_one, client, model, raw): i for i, raw in enumerate(images)}
            for done, fut in enumerate(as_completed(futures), 1):
                results[futures[fut]] = fut.result()  # slot by index so scroll order is preserved
                if progress:
                    progress('read', done, len(images))
    except (ValueError, TypeError):
        raise ValueError('The AI returned something unreadable — try again.')

    items, dropped = merge_scrolls([r[0] for r in results])
    cals = [r[1]['calories'] for r in results if r[1].get('calories') is not None]
    totals = {'calories': max(cals) if cals else None}
    for k in ('protein', 'carbs', 'fat'):
        vals = [r[1][k] for r in results if r[1].get(k) is not None]
        totals[k] = max(vals) if vals else None
    return {'items': items, 'day_totals': totals, 'overlap_dropped': dropped}


def _estimate_shapes(items):
    """Ask the AI for a rough protein/carbs/fat guess per item from its name and calories.
    Only the *proportions* between items are used, so the guesses need not be accurate."""
    client, model = _get_client_and_model()
    if client is None or not items:
        return {}
    listing = '\n'.join(f'{i}. {it["name"]} — {it["calories"]:.0f} kcal' for i, it in enumerate(items))
    prompt = ('Estimate protein, carbs and fat (grams) for each of these logged foods/recipes, so that '
              '4*protein + 4*carbs + 9*fat is roughly its calories. Return ONLY JSON: '
              '{"items": [{"i": 0, "protein": n, "carbs": n, "fat": n}, ...]}\n\n' + listing)
    try:
        resp = client.chat.completions.create(
            model=model, messages=[{'role': 'user', 'content': prompt}],
            response_format={'type': 'json_object'}, temperature=0)
        rows = json.loads(resp.choices[0].message.content).get('items') or []
        return {int(r['i']): (_num(r.get('protein')) or 0, _num(r.get('carbs')) or 0, _num(r.get('fat')) or 0)
                for r in rows}
    except Exception:
        return {}


def fill_from_day_totals(result):
    """For items that still have no macros (recipes, unmatched foods): the app's day total minus what the
    matched items account for is exactly what the unmatched items add up to. Split that between them using
    AI-guessed proportions (falling back to calories). Exact when there is only one unmatched item."""
    items, totals = result['items'], result.get('day_totals') or {}
    keys = ('protein', 'carbs', 'fat')
    if any(totals.get(k) is None for k in keys):
        return
    unmatched = [it for it in items if all(it.get(k) is None for k in keys)]
    if not unmatched:
        return
    residual = {k: max(0.0, totals[k] - sum(it.get(k) or 0 for it in items if it not in unmatched)) for k in keys}
    if len(unmatched) == 1:
        shares = {0: {k: 1.0 for k in keys}}
    else:
        guess = _estimate_shapes(unmatched)
        shares = {}
        for j, k in enumerate(keys):
            weights = [guess.get(i, (0, 0, 0))[j] for i in range(len(unmatched))]
            if sum(weights) <= 0:
                weights = [it['calories'] for it in unmatched]
            tot = sum(weights)
            for i, w in enumerate(weights):
                shares.setdefault(i, {})[k] = w / tot
    for i, it in enumerate(unmatched):
        for k in keys:
            it[k] = round(residual[k] * shares[i][k], 1)
        it['lookup'] = {'confidence': 'estimate', 'source': 'day-total',
                        'match': 'exact — the only item without a match' if len(unmatched) == 1
                                 else 'estimated from the day total'}


RECIPE_PROMPT = """This is a screenshot from a nutrition-tracking app showing one or more of the user's own recipes
(a recipe page or a list of recipes). Extract every recipe whose name AND calories are visible.

Return ONLY JSON: {"recipes": [{"name": "Recipe name", "calories": number,
  "protein": number|null, "carbs": number|null, "fat": number|null}]}

Rules:
- Use one consistent basis per recipe: calories, protein, carbs and fat must all describe the same amount
  (prefer per serving if shown, otherwise the whole recipe as shown).
- Only use numbers visible on screen; null for anything not shown. Never invent values.
- Ignore rows cut off by the screen edge. List each recipe once."""


def _read_recipes(client, model, raw):
    content = [{'type': 'text', 'text': RECIPE_PROMPT},
               {'type': 'image_url', 'image_url': {'url': _prepare(raw), 'detail': 'high'}}]
    resp = client.chat.completions.create(model=model, messages=[{'role': 'user', 'content': content}],
                                          response_format={'type': 'json_object'}, temperature=0)
    out = []
    for r in json.loads(resp.choices[0].message.content).get('recipes') or []:
        name, kcal = str(r.get('name') or '').strip(), _num(r.get('calories'))
        if name and kcal:
            out.append({'name': name[:120], 'calories': kcal, 'protein': _num(r.get('protein')),
                        'carbs': _num(r.get('carbs')), 'fat': _num(r.get('fat'))})
    return out


def extract_recipes(images, progress=None):
    """Recipe screenshots (any order — recipes are independent). Duplicates across screenshots collapse to
    the most complete copy. Returns {'recipes': [...]}."""
    from services.food_lookup import name_key
    client, model = _get_client_and_model()
    if client is None:
        raise ValueError('No AI provider configured — add an OpenAI key in Settings.')
    for raw in images:
        try:
            _prepare(raw)
        except Exception:
            raise ValueError('One of the files is not a readable image.')
    found = {}
    try:
        with ThreadPoolExecutor(max_workers=min(4, len(images))) as pool:
            futures = [pool.submit(_read_recipes, client, model, raw) for raw in images]
            for done, fut in enumerate(futures, 1):
                for r in fut.result():
                    key = name_key(r['name'])
                    filled = sum(r[k] is not None for k in ('protein', 'carbs', 'fat'))
                    if key not in found or filled > found[key][0]:
                        found[key] = (filled, r)
                if progress:
                    progress('read', done, len(images))
    except (ValueError, TypeError):
        raise ValueError('The AI returned something unreadable — try again.')
    return {'recipes': [v[1] for v in found.values()]}


# ── Photo of a meal → estimate ───────────────────────────────────────────────

MEAL_PROMPT = """You estimate the nutrition of a meal from a photo, for a UK person logging what they ate (often at a restaurant, cafe or takeaway).

Return ONLY JSON of this shape:
{
  "name": "short dish name the person would type, e.g. 'Chicken tikka masala with pilau rice'",
  "items": [{"name": "component", "portion": "~150 g", "calories": number, "protein": number, "carbs": number, "fat": number, "fibre": number}],
  "confidence": "low" | "medium" | "high",
  "assumptions": "one or two short sentences: the portion size you assumed and any hidden oil, butter or sauce you allowed for",
  "calories_low": number,
  "calories_high": number
}

Rules:
- List each visible component as its own item (the curry, the rice, the naan, chips, dressing, a drink if visible).
- Estimate the amount actually on the plate. Use anything in the photo for scale (cutlery, plate, hand, packaging).
- Restaurant and takeaway food usually carries more oil, butter and sugar than home cooking, and portions are often larger; allow for that.
- Macros are in grams, calories in kcal, for the amount shown (not per 100 g). Be realistic; make the numbers consistent (kcal about 4 x protein + 4 x carbs + 9 x fat).
- calories_low and calories_high are a plausible range for the whole meal.
- Always give your best estimate, even when unsure — say so through "confidence" and "assumptions". Do not refuse.
- If the photo does not contain food, return "items": [] and "name": ""."""


def estimate_meal(image_bytes, hint=None):
    """AI estimate for one meal photo. Returns {name, items[], totals{}, confidence, assumptions, calories_low/high}.
    Totals are summed from the items here rather than trusted from the model."""
    client, model = _get_client_and_model()
    if client is None:
        raise ValueError('No AI provider configured — add an OpenAI key in Settings.')
    try:
        url = _prepare(image_bytes)
    except Exception:
        raise ValueError('That file is not a readable image.')
    text = MEAL_PROMPT + (f"\n\nThe user adds this note about the meal: {hint.strip()[:400]}" if hint and hint.strip() else '')
    resp = client.chat.completions.create(
        model=model, temperature=0.2, response_format={'type': 'json_object'},
        messages=[{'role': 'user', 'content': [{'type': 'text', 'text': text}, {'type': 'image_url', 'image_url': {'url': url, 'detail': 'high'}}]}])
    try:
        d = json.loads(resp.choices[0].message.content)
    except (ValueError, TypeError):
        raise ValueError('The AI returned something unreadable — try again.')

    items = []
    for it in d.get('items') or []:
        kcal = _num(it.get('calories'))
        if kcal is None:
            continue
        items.append({'name': str(it.get('name') or '')[:80], 'portion': str(it.get('portion') or '')[:40], 'calories': kcal,
                      'protein': _num(it.get('protein')) or 0, 'carbs': _num(it.get('carbs')) or 0,
                      'fat': _num(it.get('fat')) or 0, 'fibre': _num(it.get('fibre')) or 0})
    if not items:
        raise ValueError("I couldn't see any food in that photo — try a closer, brighter shot.")
    totals = {k: round(sum(i[k] for i in items), 1) for k in ('calories', 'protein', 'carbs', 'fat', 'fibre')}
    lo, hi = _num(d.get('calories_low')), _num(d.get('calories_high'))
    if not lo or not hi or lo > hi or not (lo * 0.5 <= totals['calories'] <= hi * 1.5):
        lo, hi = round(totals['calories'] * 0.8), round(totals['calories'] * 1.25)
    return {'name': str(d.get('name') or 'Meal').strip()[:120] or 'Meal', 'items': items, 'totals': totals,
            'confidence': d.get('confidence') if d.get('confidence') in ('low', 'medium', 'high') else 'medium',
            'assumptions': str(d.get('assumptions') or '')[:300], 'calories_low': lo, 'calories_high': hi}
