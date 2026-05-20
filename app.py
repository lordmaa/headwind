import logging
import os
import threading
import time
from datetime import datetime, timedelta
from flask import Flask, redirect, request, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix
from config import Config
from database import close_db, migrate_db

logging.basicConfig(level=logging.WARNING)


def _mqtt_heartbeat(app):
    time.sleep(10)  # let the app finish starting up
    while True:
        try:
            with app.app_context():
                from services.mqtt import push_update
                push_update()
        except Exception:
            pass
        time.sleep(20)


def _garmin_heartbeat(app):
    time.sleep(60)  # let the app finish starting up
    while True:
        interval_hours = 2
        try:
            with app.app_context():
                import json
                import logging
                from database import query_db, get_db
                log = logging.getLogger(__name__)
                s = query_db('SELECT garminEmail, garminPassword, garminSyncHours FROM Settings WHERE id=1', one=True)
                if s and s['garminEmail'] and s['garminPassword']:
                    interval_hours = s['garminSyncHours'] or 2
                    from services.garmin import sync_garmin, fetch_ride_hr, _client
                    sync_garmin(s['garminEmail'], s['garminPassword'], days=14)

                    # Backfill HR for owner's recent rides where Strava had no HR data
                    missing = query_db('''
                        SELECT a.id, a.startDate, a.elapsedTime, a.streams
                        FROM Activity a
                        JOIN Rider r ON r.id = a.riderId
                        WHERE r.isDefault = 1
                          AND a.averageHeartrate IS NULL
                          AND a.startDate IS NOT NULL
                          AND a.elapsedTime IS NOT NULL
                          AND a.startDate >= datetime('now', '-7 days')
                    ''')
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
        except Exception:
            pass
        time.sleep(interval_hours * 3600)


def create_app():
    app = Flask(__name__)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
    app.config.from_object(Config)
    app.teardown_appcontext(close_db)

    with app.app_context():
        migrate_db()

    # ── Template filters ─────────────────────────────────────────
    @app.template_filter('fmt_dist')
    def fmt_dist(m):
        return f'{float(m or 0) / 1609.344:,.1f} mi'

    @app.template_filter('fmt_speed')
    def fmt_speed(mps):
        return f'{float(mps or 0) * 2.23694:.1f} mph'

    @app.template_filter('fmt_elev')
    def fmt_elev(m):
        return f'{round(float(m or 0) * 3.28084):,} ft'

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
    from routes.auth         import bp as auth_bp
    from routes.rides        import bp as rides_bp
    from routes.settings     import bp as settings_bp
    from routes.sync         import bp as sync_bp
    from routes.kudos        import bp as kudos_bp
    from routes.webhook      import bp as webhook_bp
    from routes.import_rides import bp as import_bp
    from routes.data          import bp as data_bp
    from routes.mqtt_publish  import bp as mqtt_bp
    from routes.login         import bp as login_bp
    from routes.segments      import bp as segments_bp
    from routes.riders        import bp as riders_bp
    from routes.heatmap       import bp as heatmap_bp
    from routes.ai_page       import bp as ai_bp
    from routes.garmin        import bp as garmin_bp
    from routes.garmin_page   import bp as garmin_page_bp
    from routes.about         import bp as about_bp
    from routes.setup         import bp as setup_bp

    # ── Auth guard ───────────────────────────────────────────────
    _PUBLIC = {'login.login_page', 'login.logout', 'static',
               'auth.strava', 'auth.callback', 'webhook.event'}

    @app.before_request
    def check_login():
        if request.endpoint in _PUBLIC or request.endpoint is None:
            return
        if not session.get('logged_in'):
            return redirect(url_for('login.login_page'))
        if request.endpoint != 'setup.wizard':
            from database import query_db
            if query_db('SELECT COUNT(*) FROM Rider', one=True)[0] == 0:
                return redirect('/setup')

    app.permanent_session_lifetime = timedelta(days=30)

    app.register_blueprint(dashboard_bp)
    app.register_blueprint(auth_bp,     url_prefix='/auth')
    app.register_blueprint(rides_bp,    url_prefix='/rides')
    app.register_blueprint(settings_bp, url_prefix='/settings')
    app.register_blueprint(sync_bp)
    app.register_blueprint(kudos_bp)
    app.register_blueprint(webhook_bp)
    app.register_blueprint(import_bp)
    app.register_blueprint(data_bp)
    app.register_blueprint(mqtt_bp)
    app.register_blueprint(login_bp)
    app.register_blueprint(segments_bp)
    app.register_blueprint(riders_bp)
    app.register_blueprint(heatmap_bp)
    app.register_blueprint(ai_bp)
    app.register_blueprint(garmin_bp)
    app.register_blueprint(garmin_page_bp)
    app.register_blueprint(about_bp)
    app.register_blueprint(setup_bp)

    # Start background threads only in the main worker process, not the reloader watcher
    if os.environ.get('WERKZEUG_RUN_MAIN') == 'true' or not app.debug:
        threading.Thread(target=_mqtt_heartbeat,   args=(app,), daemon=True).start()
        threading.Thread(target=_garmin_heartbeat, args=(app,), daemon=True).start()

    return app


if __name__ == '__main__':
    create_app().run(debug=True, host='0.0.0.0', port=int(os.environ.get('PORT', 5001)))
