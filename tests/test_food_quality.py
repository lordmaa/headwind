import pytest

from services.food_quality import nutri_score_label, nutrient_tags


@pytest.mark.parametrize('grade,label', [
    ('a', 'Excellent'),
    ('A', 'Excellent'),
    ('b', 'Excellent'),
    ('c', 'Good'),
    ('d', 'OK'),
    ('e', 'Poor'),
    (None, None),
    ('', None),
    ('z', None),
])
def test_nutri_score_label(grade, label):
    assert nutri_score_label(grade) == label


def test_nutrient_tags_high_fibre():
    tags = nutrient_tags(fibre_per_100g=7.5)
    assert {'label': 'High Fibre', 'color': 'fibre'} in tags


def test_nutrient_tags_fibre_below_threshold():
    tags = nutrient_tags(fibre_per_100g=5.9)
    assert not any(t['label'] == 'High Fibre' for t in tags)


def test_nutrient_tags_high_protein():
    # 20g protein * 4 kcal/g = 80kcal, /100kcal total = 80% ratio -> well above 20%
    tags = nutrient_tags(kcal_per_100g=100, protein_per_100g=20)
    assert {'label': 'High Protein', 'color': 'protein'} in tags


def test_nutrient_tags_protein_below_threshold():
    # 3g protein * 4 = 12kcal / 200kcal = 6% ratio -> below 20%
    tags = nutrient_tags(kcal_per_100g=200, protein_per_100g=3)
    assert not any(t['label'] == 'High Protein' for t in tags)


def test_nutrient_tags_low_fat():
    tags = nutrient_tags(fat_per_100g=2.0)
    assert {'label': 'Low Fat', 'color': 'fat'} in tags


def test_nutrient_tags_fat_above_threshold():
    tags = nutrient_tags(fat_per_100g=3.1)
    assert not any(t['label'] == 'Low Fat' for t in tags)


def test_nutrient_tags_low_sugar():
    tags = nutrient_tags(sugars_per_100g=4.9)
    assert {'label': 'Low Sugar', 'color': 'carbs'} in tags


def test_nutrient_tags_no_data_returns_empty():
    assert nutrient_tags() == []


def test_nutrient_tags_missing_kcal_skips_protein_tag():
    tags = nutrient_tags(protein_per_100g=30)
    assert not any(t['label'] == 'High Protein' for t in tags)


def test_nutrient_tags_multiple_at_once():
    tags = nutrient_tags(kcal_per_100g=100, protein_per_100g=20, fat_per_100g=1, fibre_per_100g=8, sugars_per_100g=1)
    labels = {t['label'] for t in tags}
    assert labels == {'High Fibre', 'High Protein', 'Low Fat', 'Low Sugar'}
