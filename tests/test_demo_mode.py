"""Public demo mode: what is refused, rate limiting, the hit counter (bots excluded), the GIF, and the nightly reset. No network, throwaway files."""
import io
import os
import sqlite3

import pytest
from PIL import Image

from services import demo


def test_dangerous_writes_are_refused_by_default():
    for ep in ('settings.change_password', 'settings.backup_import', 'settings.index', 'friends.add', 'garmin.connect', 'import_rides.upload', 'setup.restore', 'phones.create',
               'segments.scan', 'rides.delete', 'riders.edit', 'api_v1.post_activity', 'mqtt.do_publish', 'homeassistant.save', 'sync.sync', 'kudos.kudos', 'nutrition.import_screenshot',
               'nutrition.estimate_meal_photo', 'nutrition.set_food_image', 'nutrition.upload_food_image', 'route_builder.export_current', 'totally.new.endpoint'):
        assert demo.decide('POST', ep), ep
    for method in ('PUT', 'PATCH', 'DELETE'):
        assert demo.decide(method, 'settings.change_password')


def test_the_harmless_fun_stuff_still_works():
    for ep in ('login.login_page', 'nutrition.log_food', 'nutrition.add_water', 'nutrition.log_weight', 'gear.api_assign', 'gear.bike_save', 'gear.part_add', 'workouts_ui.workouts_new', 'rides.save_notes'):
        assert demo.decide('POST', ep) is None, ep
    assert demo.decide('GET', 'dashboard.dashboard') is None and demo.decide('GET', 'static') is None and demo.decide('GET', 'demo.counter') is None


def test_proxies_downloads_and_peer_endpoints_are_refused_even_for_get():
    for ep in ('settings.backup_export', 'heatmap.tile_proxy', 'friends.feed', 'friends.foods_feed', 'nutrition.image_search', 'phones.app_apk', 'telemetry_forward.ping'):
        assert demo.decide('GET', ep), ep


def test_rate_limiter_blocks_a_burst_per_client_and_recovers():
    r = demo.RateLimiter(limit=5, window=60)
    assert [r.check('a', now=100.0 + i) for i in range(5)] == [0] * 5
    assert r.check('a', now=106.0) > 0                               # the 6th in the window waits
    assert r.check('b', now=106.0) == 0                              # other clients are unaffected
    assert r.check('a', now=100.0 + 61) == 0                         # and the window slides
    r2 = demo.RateLimiter(limit=10, window=60)
    assert r2.check('x', cost=4, now=1) == 0 and r2.check('x', cost=4, now=2) == 0 and r2.check('x', cost=4, now=3) > 0     # writes cost more


def test_rate_limiter_memory_is_bounded():
    r = demo.RateLimiter(limit=5, window=60, max_clients=100)
    for i in range(1000):
        r.check(f'ip{i}', now=float(i))
    assert len(r._hits) <= 300


def test_the_counter_counts_real_browsers_once_per_half_hour_and_ignores_bots():
    v = demo.Visitors(ttl=1800)
    chrome = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36'
    assert v.is_new('1.2.3.4', chrome, now=1000) is True
    assert v.is_new('1.2.3.4', chrome, now=1500) is False             # refresh spam
    assert v.is_new('1.2.3.4', chrome, now=1000 + 1900) is True       # back after half an hour
    assert v.is_new('5.6.7.8', chrome, now=1000) is True              # someone else
    for bot in ('Googlebot/2.1', 'curl/8.0', 'python-requests/2.31', 'UptimeRobot/2.0', 'HeadlessChrome/120', '', None):
        assert v.is_new('9.9.9.9', bot, now=1) is False, bot


def test_counter_persists_and_starts_where_told(tmp_path, monkeypatch):
    p = str(tmp_path / 'c.db')
    monkeypatch.setenv('DEMO_COUNTER_START', '41')
    assert demo.counter_value(p) == 41
    assert demo.counter_hit(p) == 42 and demo.counter_hit(p) == 43 and demo.counter_value(p) == 43


def test_counter_gif_is_a_valid_gif_for_any_number():
    for n in (0, 7, 1337, 9999999, 12345678):
        im = Image.open(io.BytesIO(demo.counter_gif(n)))
        assert im.format == 'GIF' and im.size[0] > 100 and im.size[1] > 30
    assert demo.counter_gif(1) != demo.counter_gif(2) and demo.counter_gif(5) == demo.counter_gif(5)


def test_reset_restores_the_pristine_database_and_bike_pictures(tmp_path):
    pristine, live = str(tmp_path / 'pristine.db'), str(tmp_path / 'live.db')
    for path, rows in ((pristine, ['good']), (live, ['good', 'vandalised'])):
        c = sqlite3.connect(path); c.execute('CREATE TABLE t (v TEXT)'); c.executemany('INSERT INTO t VALUES (?)', [(r,) for r in rows]); c.commit(); c.close()
    src, dst = tmp_path / 'src_img', tmp_path / 'live_img'
    src.mkdir(); dst.mkdir(); (src / 'bike_1_aaaaaaaa.jpg').write_bytes(b'x'); (dst / 'junk.jpg').write_bytes(b'y')
    assert demo.reset_now(live, pristine, str(src), str(dst)) is True
    assert [r[0] for r in sqlite3.connect(live).execute('SELECT v FROM t')] == ['good']
    assert sorted(os.listdir(dst)) == ['bike_1_aaaaaaaa.jpg']
    assert demo.reset_now(live, str(tmp_path / 'missing.db')) is False


def test_demo_is_off_unless_asked_for(monkeypatch):
    monkeypatch.delenv('HEADWIND_DEMO', raising=False)
    assert demo.enabled() is False
    monkeypatch.setenv('HEADWIND_DEMO', '1')
    assert demo.enabled() is True
