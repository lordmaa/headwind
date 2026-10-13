"""Nutri-Score labelling and UK nutrition-claim tag calculation.

Pure functions, no Flask/DB/network — keep this testable in isolation.
"""

NUTRI_SCORE_LABELS = {
    'a': 'Excellent',
    'b': 'Excellent',
    'c': 'Good',
    'd': 'OK',
    'e': 'Poor',
}

# UK/EU nutrition-claim thresholds (per 100g, solids)
HIGH_FIBRE_G = 6.0
HIGH_PROTEIN_ENERGY_RATIO = 0.20  # protein must supply >= 20% of energy (kcal)
LOW_FAT_G = 3.0
LOW_SUGAR_G = 5.0


def nutri_score_label(grade):
    """'a'..'e' -> 'Excellent'/'Good'/'OK'/'Poor'; None/unknown -> None."""
    if not grade:
        return None
    return NUTRI_SCORE_LABELS.get(grade.strip().lower())


def nutrient_tags(kcal_per_100g=None, protein_per_100g=None, fat_per_100g=None,
                   fibre_per_100g=None, sugars_per_100g=None):
    """Returns a list of {'label', 'color'} tags, color matching the macro
    tile it corresponds to (protein/fibre/carbs/fat). Any missing value
    simply skips that tag rather than guessing."""
    tags = []

    if fibre_per_100g is not None and fibre_per_100g >= HIGH_FIBRE_G:
        tags.append({'label': 'High Fibre', 'color': 'fibre'})

    if kcal_per_100g and protein_per_100g is not None and kcal_per_100g > 0:
        protein_energy_ratio = (protein_per_100g * 4) / kcal_per_100g
        if protein_energy_ratio >= HIGH_PROTEIN_ENERGY_RATIO:
            tags.append({'label': 'High Protein', 'color': 'protein'})

    if fat_per_100g is not None and fat_per_100g <= LOW_FAT_G:
        tags.append({'label': 'Low Fat', 'color': 'fat'})

    if sugars_per_100g is not None and sugars_per_100g <= LOW_SUGAR_G:
        tags.append({'label': 'Low Sugar', 'color': 'carbs'})

    return tags
