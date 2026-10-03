from services.mqtt import ride_summary

RIDE = {'name': 'Ride Activity', 'distance': 22.9 * 1609.344, 'movingTime': 7150, 'averageSpeed': 11.5 / 2.23694, 'maxSpeed': 28.4 / 2.23694,
        'totalElevationGain': 608, 'calories': 1173, 'averageHeartrate': None, 'averageWatts': None, 'averageCadence': None,
        'weatherSummary': 'Clear sky, 16°C, wind 18kph', 'startDateLocal': '2026-09-15T09:52:19'}


def test_generic_garmin_name_becomes_daypart_title():
    title, _ = ride_summary(RIDE, 'Sam')
    assert title == '🚴 Sam: Morning ride · 22.9 mi'


def test_real_ride_name_is_kept():
    title, _ = ride_summary({**RIDE, 'name': 'Sunderland Road Cycling'}, 'Rob')
    assert 'Sunderland Road Cycling' in title


def test_message_has_stats_and_omits_missing_sensors():
    _, msg = ride_summary(RIDE, 'Sam')
    assert '22.9 mi · 1h 59m · 11.5 mph avg (top 28)' in msg


def test_top_speed_hidden_when_it_is_just_the_average():
    _, msg = ride_summary({**RIDE, 'maxSpeed': RIDE['averageSpeed']}, 'Sam')
    assert '11.5 mph avg' in msg and 'top' not in msg
    assert '⛰ 1,995 ft · 🔥 1,173 kcal' in msg
    assert '❤️' not in msg and '⚡' not in msg          # no HR / power sensor -> no empty lines
    assert '☁️ Clear sky' in msg


def test_pr_and_year_lines():
    _, msg = ride_summary({**RIDE, 'averageHeartrate': 141.6, 'averageWatts': 168}, 'Rob', 2, (71, 1292.1, '2026'))
    assert '❤️ 142 bpm · ⚡ 168 W' in msg
    assert '🏆 2 segment PRs · 📅 2026: 1,292 mi in 71 rides' in msg
