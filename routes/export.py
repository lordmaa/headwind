"""Export the food diary and/or activities for a date range as TXT or PDF, to paste or upload into an AI chat. Calendar page + download."""
from datetime import date

from flask import Blueprint, Response, jsonify, render_template, request

from database import query_db
from services import export as ex
from services.simple_pdf import text_to_pdf

bp = Blueprint('export', __name__, url_prefix='/export')


def _rider():
    r = query_db('SELECT id FROM Rider WHERE isDefault=1 ORDER BY id LIMIT 1', one=True)
    return r['id'] if r else None


def _what():
    w = [x for x in (request.args.get('what') or 'food,activities,body').split(',') if x in ('food', 'activities', 'body')]
    return w or ['food']


@bp.route('/')
def page():
    return render_template('export.html', today=date.today().isoformat())


@bp.route('/days')
def days():
    """Which days of a month have food / activities, for the calendar dots."""
    try:
        y, m = int(request.args['year']), int(request.args['month'])
        first = date(y, m, 1)
        last = date(y + (m == 12), (m % 12) + 1, 1)
    except (KeyError, ValueError):
        return jsonify(error='bad month'), 400
    return jsonify(days=ex.day_counts(_rider(), first, date.fromordinal(last.toordinal() - 1)))


@bp.route('/download')
def download():
    rid = _rider()
    if not rid:
        return Response('No rider set up yet', 400)
    try:
        start, end = ex.parse_range(request.args.get('start', ''), request.args.get('end', ''))
    except ValueError as e:
        return Response(str(e) if 'days' in str(e) else 'Pick a start and an end date', 400)
    fmt = request.args.get('fmt', 'txt')
    text = ex.render_text(rid, start, end, _what(), full=request.args.get('full') == '1')
    name = 'headwind-%s-%s_to_%s' % ('-'.join(_what()), start.isoformat(), end.isoformat())
    if fmt == 'pdf':
        return Response(text_to_pdf(text, 'Headwind export %s to %s' % (start.isoformat(), end.isoformat())), mimetype='application/pdf',
                        headers={'Content-Disposition': 'attachment; filename="%s.pdf"' % name})
    plain = text.replace('\x01', '')
    if request.args.get('inline'):
        return Response(plain, mimetype='text/plain; charset=utf-8')
    return Response(plain, mimetype='text/plain; charset=utf-8', headers={'Content-Disposition': 'attachment; filename="%s.txt"' % name})
