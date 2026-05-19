from flask import Blueprint, jsonify, request
from database import get_db, query_db
from services.ai import generate_analysis
from services.context import save_ride_memory

bp = Blueprint('kudos', __name__)


def _ensure_weather(activity, db):
    """Fetch and store weather for an activity that doesn't have it yet."""
    if activity['weatherSummary'] or not activity['startLat'] or not activity['startLng']:
        return activity
    try:
        from services.weather import fetch_weather, save_weather
        w = fetch_weather(
            activity['startLat'],
            activity['startLng'],
            activity['startDateLocal'],
            activity['streams'],
        )
        if w:
            save_weather(db, activity['id'], w)
            db.commit()
            return query_db('SELECT * FROM Activity WHERE id=?', [activity['id']], one=True)
    except Exception:
        pass
    return activity


def _is_ride(sport_type):
    t = (sport_type or '').lower()
    return 'ride' in t or 'cycling' in t


@bp.route('/rides/<rid>/kudos', methods=['POST'])
def kudos(rid):
    activity = query_db('SELECT * FROM Activity WHERE id=?', [rid], one=True)
    if not activity:
        return jsonify({'error': 'Not found'}), 404
    if not _is_ride(activity['sportType']):
        return jsonify({'error': 'AI coaching is only available for ride activities'}), 400
    try:
        from services.ai import PERSONALITIES
        data = request.get_json(silent=True) or {}
        personality_key = data.get('personality') or 'default'
        if personality_key not in PERSONALITIES:
            personality_key = 'default'

        db = get_db()
        db.execute('UPDATE Settings SET coachPersonality=? WHERE id=1', [personality_key])
        activity = _ensure_weather(activity, db)
        text = generate_analysis(activity, personality_key=personality_key)
        db.execute(
            "UPDATE Activity SET aiKudos=?, aiKudosAt=datetime('now') WHERE id=?",
            [text, rid],
        )
        db.commit()
        save_ride_memory(activity, text)
        return jsonify({'kudos': text})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
