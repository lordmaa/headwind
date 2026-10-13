"""Global "calorie burn adjustment": shave a set percentage off every burn *estimate* as it is pulled in.

Why at ingest: Headwind is the single place calories enter (Garmin/FIT/Strava-export rides, phone GPX rides, walks/runs,
Garmin daily totals), so adjusting there passes the corrected number on to everything downstream with no per-screen
logic: dashboards, the HA sensors, the API, the Android app. The unadjusted figure is kept in `caloriesRaw`
(`totalCaloriesRaw`/`activeCaloriesRaw` on GarminDaily) so the setting is reversible and can be re-applied to history.

Not adjusted: food calories (an intake estimate, so shaving it would push the wrong way), numbers the user typed in
themselves, and rides received from friends (their own instance already applied its own setting).
"""
from database import get_db, query_db

MAX_PCT = 50.0


def clamp(pct):
    try:
        v = float(pct)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(MAX_PCT, v))


def get_pct():
    try:
        r = query_db('SELECT calorieAdjustPct FROM Settings WHERE id=1', one=True)
        return clamp(r['calorieAdjustPct']) if r and r['calorieAdjustPct'] is not None else 0.0
    except Exception:
        return 0.0


def apply(kcal, pct=None):
    """Raw kcal -> adjusted kcal. None stays None, 0 stays 0."""
    if kcal is None:
        return None
    p = get_pct() if pct is None else clamp(pct)
    return round(float(kcal) * (1 - p / 100.0), 1) if p else kcal


def pair(kcal, pct=None):
    """(raw, adjusted) ready to store. Raw is None when there is nothing to adjust."""
    if kcal is None:
        return None, None
    return kcal, apply(kcal, pct)


def reapply_all(pct=None, include_unrecorded=False):
    """Recompute stored figures from the raw ones with the current percentage. Rows with no raw figure (typed in by
    hand, or from friends) are left alone. With include_unrecorded=True, pre-existing own rows that have no raw
    figure yet adopt their current value as the raw one first (that is how history is brought under the setting).
    Returns the number of rows touched per table."""
    p = get_pct() if pct is None else clamp(pct)
    f = 1 - p / 100.0
    db = get_db()
    out = {}
    if include_unrecorded:
        # own rides only: friends' rides are stored under ids that start f<friendId>_
        db.execute("UPDATE Activity SET caloriesRaw=calories WHERE caloriesRaw IS NULL AND calories IS NOT NULL AND id NOT LIKE 'f%\\_%' ESCAPE '\\'")
        db.execute("UPDATE Workout SET caloriesRaw=calories WHERE caloriesRaw IS NULL AND calories IS NOT NULL AND COALESCE(source,'')<>'manual'")
        db.execute("UPDATE GarminDaily SET totalCaloriesRaw=totalCalories WHERE totalCaloriesRaw IS NULL AND totalCalories IS NOT NULL")
        db.execute("UPDATE GarminDaily SET activeCaloriesRaw=activeCalories WHERE activeCaloriesRaw IS NULL AND activeCalories IS NOT NULL")
    out['Activity'] = db.execute('UPDATE Activity SET calories=ROUND(caloriesRaw*?,1) WHERE caloriesRaw IS NOT NULL', [f]).rowcount
    out['Workout'] = db.execute('UPDATE Workout SET calories=ROUND(caloriesRaw*?,1) WHERE caloriesRaw IS NOT NULL', [f]).rowcount
    out['GarminDaily'] = db.execute('UPDATE GarminDaily SET totalCalories=CAST(ROUND(totalCaloriesRaw*?) AS INTEGER) WHERE totalCaloriesRaw IS NOT NULL', [f]).rowcount
    db.execute('UPDATE GarminDaily SET activeCalories=CAST(ROUND(activeCaloriesRaw*?) AS INTEGER) WHERE activeCaloriesRaw IS NOT NULL', [f])
    db.commit()
    return out
