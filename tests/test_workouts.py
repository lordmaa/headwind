from services.workouts import estimate_calories, summary, label

KG = 80


def test_walk_calories_are_plausible():
    # 1 h at ~3 mph (4,828 m), flat, 80 kg: ACSM ≈ 250-300 kcal gross
    kcal = estimate_calories('Walk', 4828, 3600, 0, KG)
    assert 240 <= kcal <= 320


def test_hills_cost_more():
    flat = estimate_calories('Walk', 5000, 3600, 0, KG)
    hilly = estimate_calories('Walk', 5000, 3600, 250, KG)
    assert hilly > flat * 1.2


def test_run_burns_more_than_walk_for_same_time():
    assert estimate_calories('Run', 8000, 3000, 0, KG) > estimate_calories('Walk', 4000, 3000, 0, KG)


def test_no_estimate_without_data():
    assert estimate_calories('Walk', 0, 3600, 0, KG) is None
    assert estimate_calories('Walk', 3000, 30, 0, KG) is None
    assert estimate_calories('Walk', 3000, 1800, 0, None) is None


def test_label_uses_daypart_when_unnamed():
    assert label({'sport': 'Walk', 'startDateLocal': '2026-09-25T07:30:00', 'name': None}) == 'Morning walk'
    assert label({'sport': 'Run', 'startDateLocal': '2026-09-25T18:05:00', 'name': ''}) == 'Evening run'
    assert label({'sport': 'Walk', 'startDateLocal': '2026-09-25T18:05:00', 'name': 'Dog loop'}) == 'Dog loop'


def test_summary_text():
    w = {'sport': 'Walk', 'startDateLocal': '2026-09-25T07:30:00', 'name': None, 'distance': 4828.0, 'movingTime': 3600,
         'totalElevationGain': 30.0, 'calories': 270, 'steps': 6200, 'averageHeartrate': None, 'weatherSummary': 'Overcast, 14°C'}
    title, msg = summary(w, 'Sam')
    assert title == '🚶 Sam: Morning walk · 3.0 mi'
    assert '3.0 mi · 1h 00m · 20:00 /mi' in msg
    assert '🔥 270 kcal · 👣 6,200 steps' in msg and '☁️ Overcast' in msg and '❤️' not in msg
