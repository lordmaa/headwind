from flask import Blueprint, render_template, request

from database import query_db
from services import activity_list

bp = Blueprint('activities', __name__)


@bp.route('/activities')
def index():
    owner = query_db('SELECT id, name FROM Rider WHERE isDefault=1 LIMIT 1', one=True)
    try:
        rid = int(request.args.get('rider') or (owner['id'] if owner else 0))
    except ValueError:
        rid = owner['id'] if owner else 0
    sport = request.args.get('sport', '')
    q = request.args.get('q', '')
    try:
        shown = min(600, max(activity_list.PAGE, int(request.args.get('n', activity_list.PAGE))))
    except ValueError:
        shown = activity_list.PAGE
    res = activity_list.page(rid, sport, q, 0, shown)
    return render_template('activities.html', items=res['items'], total=res['total'], sport=sport.lower(), q=q, shown=shown,
                           sports=activity_list.SPORTS, rider=rid, more=res['total'] > shown, step=activity_list.PAGE)
