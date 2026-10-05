import logging
import os
import threading
import time
from datetime import datetime, timedelta
from urllib.parse import urlparse
from flask import Flask, g, jsonify, redirect, request, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix
from config import Config
from database import close_db, migrate_db

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')


def _friends_autosync(app):
    import logging
    log = logging.getLogger('friends.autosync')
    time.sleep(30)  # let the app finish starting up
    while True:
        try:
            with app.app_context():
                from database import query_db
                s = query_db('SELECT friendSyncInterval FROM Settings WHERE id=1', one=True)
                interval = int(s['friendSyncInterval']) if s and s['friendSyncInterval'] is not None else 15
            if interval <= 0:
                time.sleep(300)
                continue
            time.sleep(interval * 60)
            with app.app_context():
                from database import query_db as _qdb
                from routes.friends import _do_sync
                friends = _qdb('SELECT id FROM Friend')
                for f in (friends or []):
                    try:
                        _do_sync(f['id'])
                    except Exception as e:
                        log.warning('Auto-sync failed for friend %s: %s', f['id'], e)
        except Exception as e:
            log.warning('Friends autosync loop error: %s', e)
            time.sleep(60)


def _mqtt_heartbeat(app):
    time.sleep(10)  # let the app finish starting up
    tick = 0
    while True:
        tick += 1
        try:
            if tick % 12 == 1:                      # about hourly (and once at start-up): time-based parts (sealant, bar tape) can become due with no new ride
                with app.app_context():
                    from database import get_db
                    from services import gear
                    gear.evaluate_alerts(get_db())
                    get_db().commit()
        except Exception as e:
            log.warning('Gear alert sweep failed: %s', e)
        try:
            with app.app_context():
                from services.mqtt import push_update
                push_update(background=True)
                from services.homeassistant import sync_if_due, sync_weather_if_due
                sync_if_due()
                sync_weather_if_due()
                from services.goal_model import auto_update
                auto_update()
        except Exception:
            pass
        time.sleep(300)  # MQTT safety net + Home Assistant import poll (HA honours its own interval)


def _garmin_heartbeat(app):
    """Pull Garmin health data (steps, sleep, HR…) on a fixed interval. A failure is logged, recorded (so Home
    Assistant can show 'Garmin sync failing') and retried within minutes rather than waiting a whole interval."""
    import logging
    time.sleep(60)  # let the app finish starting up
    while True:
        interval_hours = 0.5  # default 30 minutes
        ok, err = None, None
        try:
            with app.app_context():
                import json
                import logging
                from database import query_db, get_db
                log = logging.getLogger(__name__)
                s = query_db('SELECT garminEmail, garminPassword, garminSyncHours, garminSyncMode FROM Settings WHERE id=1', one=True)
                if s and s['garminEmail'] and s['garminPassword']:
                    interval_hours = s['garminSyncHours'] or 0.5
                    from services.garmin import sync_garmin, sync_garmin_activities, fetch_ride_hr, _client
                    try:
                        sync_garmin(s['garminEmail'], s['garminPassword'], days=14)
                        ok = True
                    except Exception as e:
                        ok, err = False, str(e)[:200]
                        log.warning('Garmin sync failed (will retry soon): %s', e)

                    if ok and s['garminSyncMode'] == 'full':
                        rider = query_db('SELECT id FROM Rider WHERE isDefault=1', one=True)
                        if rider:
                            try:
                                for _ in sync_garmin_activities(s['garminEmail'], s['garminPassword'], rider['id']):
                                    pass
                            except Exception as e:
                                log.warning('Garmin activity sync failed: %s', e)

                    # Backfill HR for owner's recent rides that landed without it (a plain GPX import has none)
                    missing = query_db('''
                        SELECT a.id, a.startDate, a.elapsedTime, a.streams
                        FROM Activity a
                        JOIN Rider r ON r.id = a.riderId
                        WHERE r.isDefault = 1
                          AND a.averageHeartrate IS NULL
                          AND a.startDate IS NOT NULL
                          AND a.elapsedTime IS NOT NULL
                          AND a.startDate >= datetime('now', '-7 days')
                    ''') if ok else []
                    if missing:
                        try:
                            garmin_api = _client(s['garminEmail'], s['garminPassword'])
                            db = get_db()
                            for act in missing:
                                try:
                                    streams = json.loads(act['streams']) if act['streams'] else {}
                                    time_stream = (streams.get('time') or {}).get('data')
                                    hr = fetch_ride_hr(garmin_api, act['startDate'], act['elapsedTime'], time_stream)
                                    if hr:
                                        streams['heartrate'] = {
                                            'type': 'heartrate',
                                            'data': hr['stream_data'],
                                            'series_type': 'time',
                                            'original_size': len(hr['stream_data']),
                                            'resolution': 'medium' if time_stream else 'low',
                                        }
                                        db.execute(
                                            'UPDATE Activity SET averageHeartrate=?, maxHeartrate=?, streams=? WHERE id=?',
                                            [hr['avg'], hr['max'], json.dumps(streams), act['id']],
                                        )
                                        db.commit()
                                        log.warning('Garmin HR backfill: enriched %s — avg=%s max=%s', act['id'], hr['avg'], hr['max'])
                                except Exception as hre:
                                    log.warning('Garmin HR backfill failed for %s: %s', act['id'], hre)
                        except Exception:
                            pass

                    # When did the watch itself last reach Garmin's cloud? (Headwind can only see what has been uploaded)
                    dev_sync = None
                    if ok:
                        try:
                            from datetime import datetime, timezone
                            up = _client(s['garminEmail'], s['garminPassword']).get_device_last_used().get('lastUsedDeviceUploadTime')
                            if up:
                                dev_sync = datetime.fromtimestamp(up / 1000, tz=timezone.utc).isoformat(timespec='seconds')
                        except Exception:
                            pass

                    # Record the outcome and tell HA straight away (last-sync time, fresh steps)
                    db = get_db()
                    if dev_sync:
                        db.execute('UPDATE Settings SET garminDeviceSync=? WHERE id=1', [dev_sync])
                    if ok:
                        from datetime import datetime, timezone
                        db.execute("UPDATE Settings SET garminLastOk=?, garminLastError=NULL, garminFailCount=0 WHERE id=1",
                                   [datetime.now(timezone.utc).isoformat(timespec='seconds')])
                    else:
                        db.execute("UPDATE Settings SET garminLastError=?, garminFailCount=COALESCE(garminFailCount,0)+1 WHERE id=1", [err])
                    db.commit()
                    try:
                        from services.mqtt import push_update
                        push_update()
                    except Exception:
                        pass
                    if not ok:
                        fails = (query_db('SELECT garminFailCount c FROM Settings WHERE id=1', one=True) or {'c': 1})['c'] or 1
                        interval_hours = min(interval_hours, 0.17 * min(fails, 3))   # retry after 10, 20, then 30 minutes
        except Exception as e:
            logging.getLogger(__name__).warning('Garmin heartbeat error: %s', e)
        time.sleep(interval_hours * 3600)


def create_app():
    app = Flask(__name__)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
    app.config.from_object(Config)
    app.teardown_appcontext(close_db)

    # Generates + logs the first-login password now (not lazily at first sign-in) when none is configured
    from services import credentials
    credentials.admin_login()

    with app.app_context():
        migrate_db()

    # ── Template filters ─────────────────────────────────────────
    def _get_units():
        if not hasattr(g, 'units'):
            from database import query_db
            row = query_db('SELECT units FROM Settings WHERE id=1', one=True)
            g.units = (row['units'] if row and row['units'] else 'imperial')
        return g.units

    @app.template_filter('fmt_dist')
    def fmt_dist(m):
        v = float(m or 0)
        if _get_units() == 'metric':
            return f'{v/1000:,.1f} km'
        return f'{v/1609.344:,.1f} mi'

    @app.template_filter('fmt_speed')
    def fmt_speed(mps):
        v = float(mps or 0)
        if _get_units() == 'metric':
            return f'{v*3.6:.1f} km/h'
        return f'{v*2.23694:.1f} mph'

    @app.template_filter('fmt_elev')
    def fmt_elev(m):
        v = float(m or 0)
        if _get_units() == 'metric':
            return f'{round(v):,} m'
        return f'{round(v*3.28084):,} ft'

    @app.template_filter('fmt_duration')
    def fmt_duration(secs):
        secs = int(secs or 0)
        days, rem = divmod(secs, 86400)
        h, rem    = divmod(rem, 3600)
        m, s      = divmod(rem, 60)
        if days:
            return f'{days}d {h:02d}h {m:02d}m {s:02d}s'
        return f'{h}:{m:02d}:{s:02d}' if h else f'{m}:{s:02d}'

    @app.template_filter('fmt_date')
    def fmt_date(val):
        try:
            return datetime.fromisoformat(str(val)[:19]).strftime('%-d %b %Y')
        except Exception:
            return str(val or '')

    @app.template_filter('weather_icon')
    def weather_icon(condition):
        from services.homeassistant import weather_icon as _wi
        return _wi(condition)

    @app.template_filter('sport_icon')
    def sport_icon(t):
        t = (t or '').lower()
        if 'ride' in t or 'cycling' in t: return '🚴'
        if 'run'  in t: return '🏃'
        if 'swim' in t: return '🏊'
        if 'walk' in t or 'hike' in t: return '🥾'
        return '🏅'

    @app.template_filter('wmo_label')
    def wmo_label(code):
        _labels = {
            0:'Clear sky', 1:'Mainly clear', 2:'Partly cloudy', 3:'Overcast',
            45:'Fog', 48:'Icy fog',
            51:'Light drizzle', 53:'Drizzle', 55:'Heavy drizzle',
            61:'Light rain', 63:'Rain', 65:'Heavy rain',
            71:'Light snow', 73:'Snow', 75:'Heavy snow', 77:'Snow grains',
            80:'Rain showers', 81:'Rain showers', 82:'Heavy showers',
            85:'Snow showers', 86:'Heavy snow showers',
            95:'Thunderstorm', 96:'Thunderstorm + hail', 99:'Thunderstorm',
        }
        try:
            return _labels.get(int(code), '')
        except (TypeError, ValueError):
            return ''

    # ── Blueprints ───────────────────────────────────────────────
    from routes.dashboard    import bp as dashboard_bp
    from routes.rides        import bp as rides_bp
    from routes.settings     import bp as settings_bp
    from routes.sync         import bp as sync_bp
    from routes.kudos        import bp as kudos_bp
    from routes.import_rides import bp as import_bp
    from routes.data          import bp as data_bp
    from routes.mqtt_publish  import bp as mqtt_bp
    from routes.homeassistant import bp as homeassistant_bp
    from routes.login         import bp as login_bp
    from routes.segments      import bp as segments_bp
    from routes.riders        import bp as riders_bp
    from routes.heatmap       import bp as heatmap_bp
    from routes.ai_page       import bp as ai_bp
    from routes.garmin        import bp as garmin_bp
    from routes.garmin_page   import bp as garmin_page_bp
    from routes.about         import bp as about_bp
    from routes.setup         import bp as setup_bp
    from routes.telemetry_forward import bp as telemetry_forward_bp
    from routes.friends       import bp as friends_bp
    from routes.nutrition      import bp as nutrition_bp
    from routes.route_builder  import bp as route_builder_bp

    # ── Auth guard ───────────────────────────────────────────────
    _PUBLIC = {'login.login_page', 'login.logout', 'static',
               'friends.feed', 'friends.riders_list', 'import_rides.api_upload_ride',
               'friends.foods_feed', 'friends.food_image', 'telemetry_forward.ping', 'avatar_files'}

    # The phone app authenticates with a per-device Bearer token (services/device_auth.py) instead of a login session — accepted
    # only on the app's own API paths, never on the web pages.
    # GET routes that start bulk/persistent work — treated like POSTs for the cross-site check
    _MUTATING_GETS = {'ai_page.bulk_analyse', 'garmin.sync_activities'}

    _BEARER_PATHS = ('/api/v1/', '/nutrition/api/')

    @app.before_request
    def check_login():
        if request.endpoint in _PUBLIC or request.endpoint is None:
            return
        auth = request.headers.get('Authorization', '')
        if auth.startswith('Bearer ') and request.path.startswith(_BEARER_PATHS):
            from services import device_auth
            if device_auth.verify(auth[7:].strip()):
                return
            return jsonify(error='invalid or revoked token'), 401
        from services import credentials
        if session.get('logged_in') and session.get('av') != credentials.auth_version():
            session.clear()   # password/username changed since this session was issued
        if not session.get('logged_in'):
            if request.path.startswith('/api/v1/'):
                return jsonify(error='unauthorised'), 401
            return redirect(url_for('login.login_page'))
        # CSRF: a logged-in session must not be driven by another site. Browsers send Sec-Fetch-Site / Origin;
        # non-browser clients (curl, phone app with Bearer) send neither and are unaffected.
        if request.method not in ('GET', 'HEAD', 'OPTIONS') or request.endpoint in _MUTATING_GETS:
            if request.headers.get('Sec-Fetch-Site') == 'cross-site':
                return jsonify(error='cross-site request blocked'), 403
            origin = request.headers.get('Origin')
            if origin and origin != 'null' and urlparse(origin).netloc != request.host:
                return jsonify(error='cross-origin request blocked'), 403
        if request.endpoint not in ('setup.wizard', 'setup.restore', 'garmin.connect', 'garmin.connect_mfa'):
            from database import query_db
            if query_db('SELECT COUNT(*) FROM Rider', one=True)[0] == 0:
                return redirect('/setup')

    app.permanent_session_lifetime = timedelta(days=30)

    @app.context_processor
    def inject_units():
        return {'units': _get_units()}

    @app.context_processor
    def inject_rider_mode():
        from services.rider_mode import multi_rider, owner_rider_id
        if not hasattr(g, 'multi_rider'):
            g.multi_rider = multi_rider()
        return {'multi_rider': g.multi_rider, 'owner_rider_id': owner_rider_id()}

    # Packaged/desktop installs keep photos in the user's data folder instead of the app folder (AVATAR_DIR); serve them at
    # the same /static/avatars/ URLs the templates already use.
    if os.environ.get('AVATAR_DIR'):
        from flask import send_from_directory

        @app.route('/static/avatars/<path:filename>')
        def avatar_files(filename):
            return send_from_directory(os.environ['AVATAR_DIR'], filename)

    from routes.gear import bp as gear_bp
    app.register_blueprint(gear_bp, url_prefix='/gear')
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(rides_bp,    url_prefix='/rides')
    app.register_blueprint(settings_bp, url_prefix='/settings')
    app.register_blueprint(sync_bp)
    app.register_blueprint(kudos_bp)
    app.register_blueprint(import_bp)
    app.register_blueprint(data_bp)
    app.register_blueprint(mqtt_bp)
    app.register_blueprint(homeassistant_bp)
    app.register_blueprint(login_bp)
    app.register_blueprint(segments_bp)
    app.register_blueprint(riders_bp)
    app.register_blueprint(heatmap_bp)
    app.register_blueprint(ai_bp)
    app.register_blueprint(garmin_bp)
    app.register_blueprint(garmin_page_bp)
    app.register_blueprint(about_bp)
    app.register_blueprint(setup_bp)
    app.register_blueprint(telemetry_forward_bp)
    app.register_blueprint(friends_bp)
    app.register_blueprint(nutrition_bp)
    app.register_blueprint(route_builder_bp)
    from routes.api_v1 import bp as api_v1_bp, ui as workouts_ui_bp
    from routes.phones import bp as phones_bp
    app.register_blueprint(api_v1_bp)
    app.register_blueprint(workouts_ui_bp)
    app.register_blueprint(phones_bp)

    threading.Thread(target=_mqtt_heartbeat,   args=(app,), daemon=True).start()
    threading.Thread(target=_garmin_heartbeat, args=(app,), daemon=True).start()
    threading.Thread(target=__import__('services.mqtt_commands', fromlist=['run']).run, args=(app,), daemon=True).start()
    threading.Thread(target=_friends_autosync, args=(app,), daemon=True).start()

    return app


if __name__ == '__main__':
    # use_reloader=False → single process, no duplicate background threads
    create_app().run(debug=os.environ.get('HEADWIND_DEBUG') == '1', use_reloader=False, load_dotenv=False,  # config.py loads the right env file; Flask would also pull in ./.env, leaking one instance's settings into another
                            host='0.0.0.0', port=int(os.environ.get('PORT', 5001)))
