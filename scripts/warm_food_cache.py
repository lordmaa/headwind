"""Pre-fill the offline food cache: everything you've logged before plus common UK staples.
Run from the project dir:  python3 scripts/warm_food_cache.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests
from app import create_app
from database import query_db
from routes.nutrition import OFF_FIELDS, _off_product_to_dict
from services import food_cache

STAPLES = """weetabix porridge oats cornflakes granola muesli bread wholemeal bread white bread bagel crumpet wrap tortilla
pitta naan rice basmati rice pasta spaghetti noodles couscous potatoes sweet potato chips oven chips beans baked beans
chicken breast chicken thigh turkey mince beef mince steak bacon sausages ham salmon tuna cod prawns eggs
milk semi skimmed milk skimmed milk oat milk almond milk yoghurt greek yoghurt skyr cottage cheese cheddar mozzarella
butter olive oil peanut butter jam honey marmite houmous banana apple orange grapes strawberries blueberries avocado
tomato cucumber carrots broccoli spinach peppers onion mushrooms sweetcorn peas lentils chickpeas tofu
protein shake protein bar whey protein energy gel energy bar flapjack malt loaf rice cakes crisps popcorn
chocolate digestive biscuits cereal bar dairy milk kitkat ice cream pizza lasagne curry sandwich meal deal
coca cola pepsi coke zero orange juice isotonic tea coffee""".split("\n")
STAPLES = [w for line in STAPLES for w in [line]]  # each line is a space-joined run; split on known two-word items below


def queries():
    seen = set()
    app = create_app()
    with app.app_context():
        for r in query_db("SELECT DISTINCT foodName FROM FoodLog WHERE foodName IS NOT NULL"):
            seen.add(r['foodName'].strip())
    # staples: single words + the multi-word items written out in STAPLES above
    words = " ".join(STAPLES).split()
    return app, sorted(seen) + words


def main():
    app, qs = queries()
    with app.app_context():
        food_cache.init_path()
        before = food_cache.stats()
        for i, q in enumerate(qs, 1):
            try:
                r = requests.get('https://search.openfoodfacts.org/search', timeout=8,
                                 headers={'User-Agent': 'Headwind-Nutrition/1.0'},
                                 params={'q': q, 'page_size': 15, 'fields': OFF_FIELDS,
                                         'countries_tags_en': 'United Kingdom'})
                if r.ok:
                    food_cache.store([p for p in (_off_product_to_dict(h) for h in r.json().get('hits', [])) if p])
            except Exception:
                pass
            if i % 25 == 0:
                print(f'{i}/{len(qs)} queries, {food_cache.stats()} products cached', flush=True)
            time.sleep(0.4)
        print(f'products: {before} -> {food_cache.stats()} cached. Downloading photos…', flush=True)
        urls = [r[0] for r in food_cache._conn().execute(
            'SELECT imageUrl FROM FoodCache WHERE imageUrl IS NOT NULL ORDER BY useCount DESC').fetchall()]
        for i, u in enumerate(urls, 1):
            if not food_cache.cached_image(u):
                food_cache.fetch_image(u)
                time.sleep(0.1)
            if i % 250 == 0:
                print(f'{i}/{len(urls)} photos, {food_cache.image_stats()} on disk', flush=True)
        print(f'done: {food_cache.stats()} products, {food_cache.image_stats()} photos on disk')


if __name__ == '__main__':
    main()
