import html as _html
import json
import math

import requests
from flask import Blueprint, Response, abort, jsonify, render_template, request
from database import get_db, query_db

bp = Blueprint('route_builder', __name__, url_prefix='/planner')

_ELEV_BATCH = 100


def _haversine_mi(lat1, lng1, lat2, lng2):
    R = 3958.8
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def _total_mi(waypoints):
    d = 0.0
    for i in range(1, len(waypoints)):
        d += _haversine_mi(
            waypoints[i - 1]['lat'], waypoints[i - 1]['lng'],
            waypoints[i]['lat'],     waypoints[i]['lng'],
        )
    return d


def _fetch_elevation_m(waypoints):
    if not waypoints:
        return []
    result = []
    for i in range(0, len(waypoints), _ELEV_BATCH):
        batch = waypoints[i:i + _ELEV_BATCH]
        lats = ','.join(str(w['lat']) for w in batch)
        lngs = ','.join(str(w['lng']) for w in batch)
        try:
            r = requests.get(
                'https://api.open-meteo.com/v1/elevation',
                params={'latitude': lats, 'longitude': lngs},
                timeout=10,
            )
            r.raise_for_status()
            result.extend(r.json().get('elevation', [None] * len(batch)))
        except Exception:
            result.extend([None] * len(batch))
    return result


def _build_gpx(name, waypoints, elevations):
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<gpx version="1.1" creator="Headwind" xmlns="http://www.topografix.com/GPX/1/1">',
        '  <rte>',
        f'    <name>{_html.escape(name)}</name>',
    ]
    for i, wp in enumerate(waypoints):
        elev = elevations[i] if i < len(elevations) and elevations[i] is not None else None
        lines.append(f'    <rtept lat="{wp["lat"]}" lon="{wp["lng"]}">')
        if elev is not None:
            lines.append(f'      <ele>{elev:.1f}</ele>')
        lines.append('    </rtept>')
    lines.append('  </rte>')
    lines.append('</gpx>')
    return '\n'.join(lines)


@bp.route('/')
def index():
    center = query_db(
        'SELECT AVG(startLat) as lat, AVG(startLng) as lng FROM Activity '
        'WHERE startLat IS NOT NULL AND startLng IS NOT NULL',
        one=True,
    )
    default_lat = center['lat'] if center and center['lat'] else 54.0
    default_lng = center['lng'] if center and center['lng'] else -2.0
    return render_template('route_builder.html', default_lat=default_lat, default_lng=default_lng)


@bp.route('/routes', methods=['GET'])
def list_routes():
    rows = query_db(
        'SELECT id, name, waypoints, createdAt FROM PlannedRoute ORDER BY createdAt DESC'
    )
    result = []
    for row in rows:
        wps = json.loads(row['waypoints'] or '[]')
        result.append({
            'id':        row['id'],
            'name':      row['name'],
            'createdAt': row['createdAt'],
            'count':     len(wps),
            'distMi':    round(_total_mi(wps), 1),
        })
    return jsonify(result)


@bp.route('/routes/<int:route_id>', methods=['GET'])
def get_route(route_id):
    row = query_db(
        'SELECT id, name, waypoints, createdAt FROM PlannedRoute WHERE id=?',
        [route_id], one=True,
    )
    if not row:
        abort(404)
    wps = json.loads(row['waypoints'] or '[]')
    return jsonify({
        'id':        row['id'],
        'name':      row['name'],
        'createdAt': row['createdAt'],
        'waypoints': wps,
        'distMi':    round(_total_mi(wps), 1),
    })


@bp.route('/routes', methods=['POST'])
def save_route():
    data = request.get_json(force=True)
    name = (data.get('name') or 'Unnamed Route').strip()[:100]
    waypoints = data.get('waypoints') or []
    if len(waypoints) < 2:
        return jsonify({'error': 'Need at least 2 waypoints'}), 400
    db = get_db()
    cur = db.execute(
        'INSERT INTO PlannedRoute (name, waypoints) VALUES (?, ?)',
        [name, json.dumps(waypoints)],
    )
    db.commit()
    return jsonify({
        'id':     cur.lastrowid,
        'name':   name,
        'distMi': round(_total_mi(waypoints), 1),
        'count':  len(waypoints),
    }), 201


@bp.route('/routes/<int:route_id>', methods=['DELETE'])
def delete_route(route_id):
    db = get_db()
    db.execute('DELETE FROM PlannedRoute WHERE id=?', [route_id])
    db.commit()
    return '', 204


@bp.route('/export', methods=['POST'])
def export_current():
    data = request.get_json(force=True)
    name = (data.get('name') or 'Route').strip()[:100]
    waypoints = data.get('waypoints') or []
    if len(waypoints) < 2:
        return jsonify({'error': 'Need at least 2 waypoints'}), 400
    elevations = _fetch_elevation_m(waypoints)
    gpx = _build_gpx(name, waypoints, elevations)
    safe = name.replace(' ', '_')
    return Response(
        gpx,
        mimetype='application/gpx+xml',
        headers={'Content-Disposition': f'attachment; filename="{safe}.gpx"'},
    )


@bp.route('/routes/<int:route_id>/export.gpx')
def export_saved(route_id):
    row = query_db(
        'SELECT name, waypoints FROM PlannedRoute WHERE id=?', [route_id], one=True
    )
    if not row:
        abort(404)
    waypoints = json.loads(row['waypoints'] or '[]')
    elevations = _fetch_elevation_m(waypoints)
    gpx = _build_gpx(row['name'], waypoints, elevations)
    safe = row['name'].replace(' ', '_')
    return Response(
        gpx,
        mimetype='application/gpx+xml',
        headers={'Content-Disposition': f'attachment; filename="{safe}.gpx"'},
    )
