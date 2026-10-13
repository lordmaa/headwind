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
    sport_types = query_db(
        'SELECT DISTINCT sportType FROM Activity WHERE sportType IS NOT NULL AND streams IS NOT NULL AND streams != "" ORDER BY sportType'
    )
    return render_template('heatmap.html',
                           min_date=bounds['min_d'] or '',
                           max_date=bounds['max_d'] or '',
                           sport_types=sport_types)


_ESRI = 'https://server.arcgisonline.com/ArcGIS/rest/services/'
# layer name -> (Esri service path, native max zoom or None). CARTO's basemaps needed an API key from 2026, so dark/light are now
# Esri's keyless gray canvases (each is a base + a transparent place-name overlay, exported as two layers).
_TILE_LAYERS = {
    'satellite': ('World_Imagery', None),
    'labels':    ('Reference/World_Boundaries_and_Places', None),
    'dark':      ('Canvas/World_Dark_Gray_Base', 16),
    'dark_ref':  ('Canvas/World_Dark_Gray_Reference', 16),
    'light':     ('Canvas/World_Light_Gray_Base', 16),
    'light_ref': ('Canvas/World_Light_Gray_Reference', 16),
}


@bp.route('/heatmap/tile/<int:z>/<int:x>/<int:y>')
def tile_proxy(z, x, y):
    """Proxy map tiles for HD canvas export (bypasses CORS)."""
    path, native = _TILE_LAYERS.get(request.args.get('layer', 'dark'), _TILE_LAYERS['dark'])
    try:
        if native and z > native:
            # Past the layer's native zoom: take the parent tile and enlarge the right quarter/sixteenth of it — what Leaflet
            # does on screen — instead of Esri's "map data not available" placeholder.
            import io
            from PIL import Image
            dz = z - native
            r = _requests.get(f'{_ESRI}{path}/MapServer/tile/{native}/{y >> dz}/{x >> dz}', timeout=8,
                              headers={'User-Agent': 'Headwind/1.0 (self-hosted bike app)'})
            if r.status_code == 200:
                img = Image.open(io.BytesIO(r.content)).convert('RGBA')
                size = img.width >> dz
                left, top = (x & ((1 << dz) - 1)) * size, (y & ((1 << dz) - 1)) * size
                out = io.BytesIO()
                img.crop((left, top, left + size, top + size)).resize((256, 256), Image.LANCZOS).save(out, 'PNG')
                return Response(out.getvalue(), mimetype='image/png', headers={
                    'Cache-Control': 'public, max-age=86400', 'Access-Control-Allow-Origin': '*'})
        else:
            r = _requests.get(f'{_ESRI}{path}/MapServer/tile/{z}/{y}/{x}', timeout=8,
                              headers={'User-Agent': 'Headwind/1.0 (self-hosted bike app)'})
            if r.status_code == 200:
                return Response(r.content, mimetype=r.headers.get('Content-Type', 'image/png'), headers={
                    'Cache-Control': 'public, max-age=86400', 'Access-Control-Allow-Origin': '*'})
    except Exception:
        pass
    abort(502)


@bp.route('/heatmap/data')
def data():
    from_date = request.args.get('from', '')
    to_date   = request.args.get('to', '')
    sport     = request.args.get('sport', '').strip()

    where = "WHERE streams IS NOT NULL AND streams != ''"
    params = []
    if from_date and to_date:
        where += " AND date(startDateLocal) BETWEEN ? AND ?"
        params.extend([from_date, to_date])
    elif from_date:
        where += " AND date(startDateLocal) >= ?"
        params.append(from_date)
    elif to_date:
        where += " AND date(startDateLocal) <= ?"
        params.append(to_date)
    if sport:
        where += " AND lower(sportType) LIKE ?"
        params.append(f'%{sport.lower()}%')

    rows = query_db(f"SELECT streams FROM Activity {where}", params)

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
