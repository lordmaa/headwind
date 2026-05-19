import json

import requests as _requests
from flask import Blueprint, Response, abort, jsonify, render_template, request
from database import query_db

bp = Blueprint('heatmap', __name__)

_SAMPLE     = 50   # points per activity for the live map
_SAMPLE_HD  = 250  # points per activity for HD export


@bp.route('/heatmap')
def index():
    bounds = query_db(
        "SELECT MIN(date(startDateLocal)) as min_d, MAX(date(startDateLocal)) as max_d "
        "FROM Activity WHERE streams IS NOT NULL AND streams != ''",
        one=True
    )
    return render_template('heatmap.html',
                           min_date=bounds['min_d'] or '',
                           max_date=bounds['max_d'] or '')


@bp.route('/heatmap/tile/<int:z>/<int:x>/<int:y>')
def tile_proxy(z, x, y):
    """Proxy map tiles for HD canvas export (bypasses CORS)."""
    layer = request.args.get('layer', 'dark')
    sub   = ['a', 'b', 'c', 'd'][(x + y + z) % 4]
    if layer == 'satellite':
        url = f'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'
    elif layer == 'labels':
        url = f'https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}'
    elif layer == 'light':
        url = f'https://{sub}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}@2x.png'
    else:
        url = f'https://{sub}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}@2x.png'
    try:
        r = _requests.get(url, timeout=8,
                          headers={'User-Agent': 'Mozilla/5.0 bike-flask/1.0'})
        if r.status_code == 200:
            return Response(r.content, mimetype='image/png', headers={
                'Cache-Control': 'public, max-age=86400',
                'Access-Control-Allow-Origin': '*',
            })
    except Exception:
        pass
    abort(502)


@bp.route('/heatmap/data')
def data():
    from_date = request.args.get('from', '')
    to_date   = request.args.get('to', '')

    if from_date and to_date:
        rows = query_db(
            "SELECT streams FROM Activity "
            "WHERE streams IS NOT NULL AND streams != '' "
            "AND date(startDateLocal) BETWEEN ? AND ?",
            [from_date, to_date]
        )
    elif from_date:
        rows = query_db(
            "SELECT streams FROM Activity "
            "WHERE streams IS NOT NULL AND streams != '' "
            "AND date(startDateLocal) >= ?",
            [from_date]
        )
    elif to_date:
        rows = query_db(
            "SELECT streams FROM Activity "
            "WHERE streams IS NOT NULL AND streams != '' "
            "AND date(startDateLocal) <= ?",
            [to_date]
        )
    else:
        rows = query_db(
            "SELECT streams FROM Activity "
            "WHERE streams IS NOT NULL AND streams != ''"
        )

    hd     = request.args.get('hd') == '1'
    sample = _SAMPLE_HD if hd else _SAMPLE
    places = 5 if hd else 4

    tracks = []
    for row in rows:
        try:
            s = json.loads(row['streams'])
            latlng = (s.get('latlng') or {}).get('data') or []
            if len(latlng) < 2:
                continue
            step = max(1, len(latlng) // sample)
            pts = [[round(p[0], places), round(p[1], places)] for p in latlng[::step]]
            if len(pts) >= 2:
                tracks.append(pts)
        except Exception:
            pass

    return jsonify({'tracks': tracks, 'count': len(tracks)})
