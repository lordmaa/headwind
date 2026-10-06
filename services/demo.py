"""Public demo mode (HEADWIND_DEMO=1): a Headwind that is safe to leave on the internet with a published login (test / test).

* WRITES are default-deny: only a short allow-list of harmless, interesting actions (log food, add water, assign bikes...) works; anything that could reach the
  network, read or replace files, change credentials, or burn CPU (imports, restores, settings, friends, Garmin, segments scans...) is refused with a friendly message.
* A few GETs that proxy to third parties or download the whole database are refused too.
* Per-IP rate limiting, security headers, no background network threads.
* Everything resets every night from a pristine copy, so vandalism is short-lived.
* An old-school hit counter (a generated GIF with LED digits) that only counts real browsers: bots don't load images.
"""
import hashlib
import io
import logging
import os
import re
import secrets
import shutil
import sqlite3
import threading
import time
from collections import defaultdict, deque
from datetime import datetime
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)
LOCAL = ZoneInfo('Europe/London')


def enabled():
    return os.environ.get('HEADWIND_DEMO') == '1'


# ---------------------------------------------------------------- what is allowed
ALLOWED_WRITES = {
    'login.login_page', 'login.logout',
    # nutrition: everything that only touches the database (not the AI / image / outbound ones)
    'nutrition.log_food', 'nutrition.log_food_batch', 'nutrition.delete_log', 'nutrition.copy_day', 'nutrition.add_favourite', 'nutrition.delete_favourite',
    'nutrition.create_custom_food', 'nutrition.update_custom_food', 'nutrition.delete_custom_food', 'nutrition.save_recipes', 'nutrition.delete_recipe',
    'nutrition.create_saved_meal', 'nutrition.delete_saved_meal', 'nutrition.log_saved_meal', 'nutrition.steps_set', 'nutrition.steps_delete',
    'nutrition.add_water', 'nutrition.delete_water', 'nutrition.log_weight', 'nutrition.delete_weight', 'nutrition.weight_refresh', 'nutrition.save_weight_settings',
    'nutrition.save_goals', 'nutrition.save_goal_profile', 'nutrition.apply_goal_plan', 'nutrition.weight_backfill_preview', 'nutrition.weight_backfill_confirm',
    'nutrition.save_override', 'nutrition.save_food_nutrition',
    # gear (bike photo uploads are ignored in demo mode, see gear routes)
    'gear.api_assign', 'gear.bike_archive', 'gear.bike_default', 'gear.bike_delete', 'gear.part_add', 'gear.bike_photo', 'gear.service_add', 'gear.bike_save',
    'gear.set_default', 'gear.log_delete', 'gear.part_edit', 'gear.ride_bike',
    # rides / workouts: label things, change type, add or remove manual activities (deleting real rides stays disabled)
    'rides.save_notes', 'rides.scan_segments', 'rides.change_sport', 'rides.resolve_duplicate',
    'workouts_ui.workouts_new', 'workouts_ui.workouts_delete', 'workouts_ui.workouts_sport',
    'route_builder.save_route', 'route_builder.delete_route',
}
# refused even for GET: third-party proxies, whole-database download, public peer/token endpoints, bulk or outbound work
DENIED_ENDPOINTS = {
    'ai_page.bulk_analyse', 'friends.feed', 'friends.food_image', 'friends.foods_feed', 'friends.riders_list', 'garmin.sync_activities', 'heatmap.tile_proxy',
    'nutrition.image_search', 'nutrition.stock_image', 'settings.backup_export', 'phones.app_apk', 'route_builder.export_saved', 'import_rides.api_upload_ride',
    'telemetry_forward.ping', 'api_v1.sync_nutrition',
}
WRITE_METHODS = ('POST', 'PUT', 'PATCH', 'DELETE')
_STATIC = ('static', 'avatar_files')


def decide(method, endpoint):
    """None if the request may proceed, otherwise a short reason."""
    if endpoint in _STATIC or endpoint is None or endpoint in ('demo.counter', 'demo.health'):
        return None
    if endpoint in DENIED_ENDPOINTS:
        return 'This is switched off in the demo.'
    if method in WRITE_METHODS and endpoint not in ALLOWED_WRITES:
        return 'That is switched off in the demo (it could change settings, reach other servers or touch files). Everything resets every night.'
    return None


# ---------------------------------------------------------------- rate limiting
class RateLimiter:
    """Sliding window per client: `limit` cost units per `window` seconds. Memory is bounded (oldest clients are dropped)."""
    def __init__(self, limit=300, window=60, max_clients=5000):
        self.limit, self.window, self.max_clients = limit, window, max_clients
        self._hits = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, client, cost=1, now=None):
        """0 if allowed, else seconds to wait."""
        now = time.monotonic() if now is None else now
        with self._lock:
            if len(self._hits) > self.max_clients:
                for k in list(self._hits)[: self.max_clients // 4]:
                    del self._hits[k]
            q = self._hits[client]
            while q and now - q[0][0] > self.window:
                q.popleft()
            if sum(c for _, c in q) + cost > self.limit:
                return max(1, int(self.window - (now - q[0][0])) + 1)
            q.append((now, cost))
            return 0


def client_ip(request):
    return (request.headers.get('CF-Connecting-IP') or request.remote_addr or '?').split(',')[0].strip()


# ---------------------------------------------------------------- the hit counter
_BOT = re.compile(r'bot|crawl|spider|slurp|curl|wget|python|requests|headless|monitor|uptime|preview|fetch|scan|java|go-http|httpclient|facebookexternalhit', re.I)
DIGITS = 7
_SEGS = {'0': 'abcdef', '1': 'bc', '2': 'abged', '3': 'abgcd', '4': 'fgbc', '5': 'afgcd', '6': 'afgedc', '7': 'abc', '8': 'abcdefg', '9': 'abcdfg'}


def counter_db_path():
    p = os.environ.get('DEMO_COUNTER_DB')
    if p:
        return p
    from flask import current_app
    return os.path.join(os.path.dirname(os.path.abspath(current_app.config['DATABASE'])), 'demo_counter.db')


def _counter_conn(path):
    c = sqlite3.connect(path, timeout=30)
    c.execute('CREATE TABLE IF NOT EXISTS counter (id INTEGER PRIMARY KEY CHECK (id = 1), n INTEGER NOT NULL, since TEXT)')
    c.execute('INSERT OR IGNORE INTO counter (id, n, since) VALUES (1, ?, ?)', [int(os.environ.get('DEMO_COUNTER_START', '0') or 0), datetime.now(LOCAL).date().isoformat()])
    return c


def counter_value(path):
    c = _counter_conn(path)
    try:
        c.commit()
        return c.execute('SELECT n FROM counter WHERE id=1').fetchone()[0]
    finally:
        c.close()


def counter_hit(path):
    c = _counter_conn(path)
    try:
        c.execute('UPDATE counter SET n = n + 1 WHERE id = 1')
        c.commit()
        return c.execute('SELECT n FROM counter WHERE id=1').fetchone()[0]
    finally:
        c.close()


class Visitors:
    """Counts a real browser once per half hour: a salted hash of (address, browser) is remembered in memory only (never stored, never logged)."""
    def __init__(self, ttl=1800, max_entries=20000):
        self.ttl, self.max_entries, self._seen, self._salt, self._lock = ttl, max_entries, {}, secrets.token_bytes(16), threading.Lock()

    def is_new(self, ip, ua, now=None):
        if not ua or _BOT.search(ua):
            return False
        now = time.time() if now is None else now
        key = hashlib.sha256(self._salt + f'{ip}|{ua}'.encode()).digest()
        with self._lock:
            if len(self._seen) > self.max_entries:
                self._seen = {k: t for k, t in self._seen.items() if now - t < self.ttl}
            t = self._seen.get(key)
            if t is None or now - t > self.ttl:
                self._seen[key] = now                     # a fixed window from the moment it was counted (refreshing does not extend it)
                return True
            return False


def counter_gif(n, scale=2):
    """A classic hit counter: bezelled black box, seven-segment lime digits with dim 'off' segments. Returns GIF bytes."""
    from PIL import Image, ImageDraw
    cw, ch, th, pad = 14, 24, 3, 5
    w, h = DIGITS * (cw + 3) + pad * 2 + 1, ch + pad * 2
    im = Image.new('P', (w * scale, h * scale))
    im.putpalette([5, 5, 5,  57, 255, 20,  14, 40, 12,  190, 190, 190,  70, 70, 70,  120, 120, 120] + [0] * (256 - 6) * 3)
    d = ImageDraw.Draw(im)
    S = lambda *p: [(x * scale, y * scale) for x, y in p]
    d.rectangle([0, 0, w * scale - 1, h * scale - 1], fill=3)                                  # bevel: light outer, dark inner
    d.rectangle([1 * scale, 1 * scale, w * scale - 1, h * scale - 1], fill=4)
    d.rectangle([2 * scale, 2 * scale, w * scale - 3 * scale, h * scale - 3 * scale], fill=0)

    def seg(x, y, name, colour):
        t, m = th, ch // 2
        horiz = {'a': (x + 2, y), 'g': (x + 2, y + m - t // 2), 'd': (x + 2, y + ch - t)}
        vert = {'f': (x, y + 2), 'b': (x + cw - t, y + 2), 'e': (x, y + m + 1), 'c': (x + cw - t, y + m + 1)}
        if name in horiz:
            hx, hy = horiz[name]
            d.polygon(S((hx, hy + t / 2), (hx + t / 2, hy), (hx + cw - 4 - t / 2, hy), (hx + cw - 4, hy + t / 2), (hx + cw - 4 - t / 2, hy + t), (hx + t / 2, hy + t)), fill=colour)
        else:
            vx, vy = vert[name]
            vl = m - 3
            d.polygon(S((vx + t / 2, vy), (vx + t, vy + t / 2), (vx + t, vy + vl - t / 2), (vx + t / 2, vy + vl), (vx, vy + vl - t / 2), (vx, vy + t / 2)), fill=colour)

    s = str(max(0, int(n))).zfill(DIGITS)[-DIGITS:]
    for i, chr_ in enumerate(s):
        x, y = pad + i * (cw + 3), pad
        on = _SEGS[chr_]
        for name in 'abcdefg':
            seg(x, y, name, 2)                       # dim 'off' segment first
        for name in on:
            seg(x, y, name, 1)
    buf = io.BytesIO()
    im.save(buf, 'GIF', optimize=False)
    return buf.getvalue()


# ---------------------------------------------------------------- nightly reset
def reset_now(live_db, pristine, bike_src=None, bike_dst=None):
    """Replace the live database with the pristine copy (SQLite backup API: atomic, WAL-aware, other connections just see the new data) and restore bike pictures."""
    if not pristine or not os.path.isfile(pristine):
        log.warning('Demo reset skipped: no pristine database at %s', pristine)
        return False
    src = sqlite3.connect(f'file:{pristine}?mode=ro', uri=True)
    dst = sqlite3.connect(live_db, timeout=60)
    try:
        src.backup(dst)
        dst.commit()
    finally:
        src.close()
        dst.close()
    if bike_src and bike_dst and os.path.isdir(bike_src):
        os.makedirs(bike_dst, exist_ok=True)
        for f in os.listdir(bike_dst):
            try:
                os.remove(os.path.join(bike_dst, f))
            except OSError:
                pass
        for f in os.listdir(bike_src):
            shutil.copy2(os.path.join(bike_src, f), os.path.join(bike_dst, f))
    log.warning('Demo data reset from %s', pristine)
    return True


def reset_loop(app, hour=4):
    """Resets once a day at `hour` London time (and whenever <data dir>/reset.flag appears). Never raises."""
    live = app.config['DATABASE']
    data_dir = os.path.dirname(os.path.abspath(live))
    pristine = os.environ.get('DEMO_PRISTINE_DB', '/demo/pristine.db')
    bike_src = os.environ.get('DEMO_PRISTINE_BIKEIMG', '/demo/bikeimg')
    done_for = datetime.now(LOCAL).date() if datetime.now(LOCAL).hour >= hour else None      # a restart after 04:00 must not reset again straight away
    flag = os.path.join(data_dir, 'reset.flag')
    while True:
        time.sleep(30)
        try:
            now = datetime.now(LOCAL)
            due = (now.hour == hour and done_for != now.date()) or os.path.exists(flag)
            if due:
                if os.path.exists(flag):
                    os.remove(flag)
                reset_now(live, pristine, bike_src, os.path.join(data_dir, 'bikeimg'))
                done_for = now.date()
        except Exception as e:                                              # pragma: no cover
            log.warning('Demo reset failed: %s', e)
