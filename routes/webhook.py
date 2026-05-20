import json
import threading
from flask import Blueprint, current_app, jsonify, request
from database import get_db, query_db
from services.strava import sync_one_activity
from services.ai import generate_analysis
from services.context import save_ride_memory
from services.notify import send_notification

bp = Blueprint('webhook', __name__)


def _process_new_activity(app, activity_id):
    import time
    import logging
    log = logging.getLogger(__name__)
    log.warning('Webhook thread started for activity %s — sleeping 60s', activity_id)
    time.sleep(60)
    log.warning('Webhook thread waking for activity %s — syncing', activity_id)
    with app.app_context():
        try:
            activity = sync_one_activity(activity_id)
            if not activity:
                log.warning('Webhook: sync_one_activity returned None for %s', activity_id)
                return

            log.warning('Webhook: synced activity %s — %s', activity_id, activity['name'])
            from services.mqtt import push_update
            push_update(activity)

            try:
                s = query_db('SELECT garminEmail, garminPassword FROM Settings WHERE id=1', one=True)
                if s and s['garminEmail'] and s['garminPassword']:
                    from services.garmin import sync_garmin, fetch_ride_hr, _client
                    sync_garmin(s['garminEmail'], s['garminPassword'], days=14)
                    log.warning('Webhook: Garmin sync complete for activity %s', activity_id)

                    # Enrich with Garmin HR if Strava didn't capture it
                    if not activity.get('averageHeartrate') and activity.get('startDate') and activity.get('elapsedTime'):
                        try:
                            garmin_api = _client(s['garminEmail'], s['garminPassword'])
                            streams = json.loads(activity['streams']) if activity.get('streams') else {}
                            time_stream = (streams.get('time') or {}).get('data')
                            hr = fetch_ride_hr(garmin_api, activity['startDate'], activity['elapsedTime'], time_stream)
                            if hr:
                                streams['heartrate'] = {
                                    'type': 'heartrate',
                                    'data': hr['stream_data'],
                                    'series_type': 'time',
                                    'original_size': len(hr['stream_data']),
                                    'resolution': 'medium' if time_stream else 'low',
                                }
                                db = get_db()
                                db.execute(
                                    'UPDATE Activity SET averageHeartrate=?, maxHeartrate=?, streams=? WHERE id=?',
                                    [hr['avg'], hr['max'], json.dumps(streams), str(activity_id)],
                                )
                                db.commit()
                                log.warning('Webhook: Garmin HR enriched activity %s — avg=%s max=%s', activity_id, hr['avg'], hr['max'])
                        except Exception as hre:
                            log.warning('Webhook: Garmin HR enrichment failed (non-fatal): %s', hre)
            except Exception as ge:
                log.warning('Webhook: Garmin sync failed (non-fatal): %s', ge)

            kudos = generate_analysis(activity)
            db = get_db()
            db.execute(
                "UPDATE Activity SET aiKudos=?, aiKudosAt=datetime('now') WHERE id=?",
                [kudos, str(activity_id)],
            )
            db.commit()
            save_ride_memory(activity, kudos)

            title = f"New ride: {activity['name']}"
            body  = kudos[:300] + ('…' if len(kudos) > 300 else '')
            url   = f"{current_app.config['APP_URL']}/rides/{activity_id}"
            send_notification(title, body, click_url=url)

        except Exception as e:
            log.error('Webhook processing failed for %s: %s', activity_id, e, exc_info=True)
            try:
                send_notification('Headwind sync error', str(e))
            except Exception:
                pass


@bp.route('/webhook', methods=['GET'])
def verify():
    mode      = request.args.get('hub.mode')
    token     = request.args.get('hub.verify_token')
    challenge = request.args.get('hub.challenge')

    if mode == 'subscribe' and token == current_app.config['STRAVA_WEBHOOK_TOKEN']:
        return jsonify({'hub.challenge': challenge})
    return jsonify({'error': 'Forbidden'}), 403


@bp.route('/webhook', methods=['POST'])
def event():
    data = request.get_json(silent=True) or {}

    import logging
    logging.getLogger(__name__).warning(
        'Webhook event: object_type=%s aspect_type=%s object_id=%s',
        data.get('object_type'), data.get('aspect_type'), data.get('object_id')
    )
    if data.get('object_type') == 'activity' and data.get('aspect_type') == 'create':
        app = current_app._get_current_object()
        t   = threading.Thread(
            target=_process_new_activity,
            args=(app, data['object_id']),
            daemon=True,
        )
        t.start()

    return jsonify({'status': 'ok'})


@bp.route('/webhook/subscribe', methods=['POST'])
def subscribe():
    import requests as req
    app_url = current_app.config['APP_URL']
    res = req.post('https://www.strava.com/api/v3/push_subscriptions', data={
        'client_id':     current_app.config['STRAVA_CLIENT_ID'],
        'client_secret': current_app.config['STRAVA_CLIENT_SECRET'],
        'callback_url':  f'{app_url}/webhook',
        'verify_token':  current_app.config['STRAVA_WEBHOOK_TOKEN'],
    })
    if res.ok:
        sub_id = str(res.json().get('id', ''))
        db = get_db()
        db.execute(
            'INSERT INTO Settings (id, webhookSubId) VALUES (1, ?) '
            'ON CONFLICT(id) DO UPDATE SET webhookSubId=excluded.webhookSubId',
            [sub_id],
        )
        db.commit()
        return jsonify({'subscribed': True, 'id': sub_id})
    return jsonify({'error': res.text}), 400


@bp.route('/webhook/unsubscribe', methods=['POST'])
def unsubscribe():
    import requests as req
    s = query_db('SELECT webhookSubId FROM Settings WHERE id=1', one=True)
    sub_id = s['webhookSubId'] if s else None
    if not sub_id:
        return jsonify({'error': 'No subscription on record'}), 400

    res = req.delete(
        f'https://www.strava.com/api/v3/push_subscriptions/{sub_id}',
        data={
            'client_id':     current_app.config['STRAVA_CLIENT_ID'],
            'client_secret': current_app.config['STRAVA_CLIENT_SECRET'],
        },
    )
    db = get_db()
    db.execute("UPDATE Settings SET webhookSubId=NULL WHERE id=1")
    db.commit()
    return jsonify({'unsubscribed': True})
