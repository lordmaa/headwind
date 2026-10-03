"""Pure-logic tests for services/shared_foods.py (overlay merge rules, name keys). The DB / HTTP paths are exercised end-to-end
by hand with two throwaway instances (see docs)."""
from services import shared_foods as sf

OFF = {'barcode': '1', 'name': 'Chicken', 'brand': 'Tesco', 'kcal_per_100g': 120, 'protein_per_100g': 22, 'carbs_per_100g': 0,
       'fat_per_100g': 3, 'fibre_per_100g': 0.5, 'serving_g': 100, 'image_url': 'https://x/off.jpg', 'nutri_score_grade': 'a'}


def _shared(**kw):
    base = {'name': 'Chicken Breast', 'brand': 'Tesco', 'kcal_per_100g': 118, 'protein_per_100g': 24, 'carbs_per_100g': 0,
            'fat_per_100g': 2, 'fibre_per_100g': None, 'serving_g': None, 'image_url': None, 'nutri_score_grade': None,
            'shared_from': 'Sam', '_overridden': True}
    base.update(kw)
    return base


def test_norm_collapses_case_and_spaces():
    assert sf._norm('  Homemade   GRANOLA ') == 'homemade granola'
    assert sf._norm(None) == ''


def test_overlay_uses_shared_values_and_tags_origin():
    out = sf.overlay(OFF, _shared())
    assert out['kcal_per_100g'] == 118 and out['name'] == 'Chicken Breast' and out['shared_from'] == 'Sam'
    assert out['_overridden'] is True


def test_overlay_keeps_off_data_where_shared_is_blank():
    out = sf.overlay(OFF, _shared())
    assert out['fibre_per_100g'] == 0.5            # shared had none -> keep Open Food Facts' value
    assert out['image_url'] == 'https://x/off.jpg'
    assert out['serving_g'] == 100                 # a friend's blank serving size must not erase ours
    assert out['brand'] == 'Tesco'
    assert out['nutri_score_grade'] == 'a'


def test_overlay_shared_fibre_and_image_win_when_present():
    out = sf.overlay(OFF, _shared(fibre_per_100g=0.0, image_url='local:abc.jpg'))
    assert out['fibre_per_100g'] == 0.0 and out['image_url'] == 'local:abc.jpg'
