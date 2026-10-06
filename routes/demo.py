"""Demo-mode only routes (registered when HEADWIND_DEMO=1): the hit counter image and a health check. Both are public."""
import time

from flask import Blueprint, Response, jsonify, request

from services import demo

bp = Blueprint('demo', __name__)
_visitors = demo.Visitors()
_cache = {'n': -1, 'gif': b'', 'at': 0.0}


@bp.route('/demo/counter.gif')
def counter():
    """The old-school counter. Only real browsers that load the image (bots don't) count, once per half hour each."""
    path = demo.counter_db_path()
    n = demo.counter_hit(path) if _visitors.is_new(demo.client_ip(request), request.headers.get('User-Agent', '')) else demo.counter_value(path)
    if n != _cache['n']:
        _cache.update(n=n, gif=demo.counter_gif(n))
    resp = Response(_cache['gif'], mimetype='image/gif')
    resp.headers['Cache-Control'] = 'no-store, max-age=0'
    return resp


@bp.route('/demo/health')
def health():
    return jsonify(ok=True, demo=True, time=int(time.time()))
