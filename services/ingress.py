"""Home Assistant add-on support: run Headwind behind HA's ingress proxy (HEADWIND_INGRESS=1).

Supervisor's ingress gateway forwards requests with the path *stripped* of its /api/hassio_ingress/<token> prefix and
tells us the prefix in `X-Ingress-Path`. Headwind has ~160 hard-coded absolute paths (`href="/x"`, `fetch('/x')`), so
instead of editing every template we (1) set SCRIPT_NAME so `url_for` is right, (2) rewrite absolute paths in HTML/JS
responses and `Location` headers, and (3) inject a tiny fetch/XHR/EventSource shim for paths built at run time.
It does nothing at all unless HEADWIND_INGRESS=1, so the normal Docker/Windows installs are untouched.

Ingress users are already signed in to Home Assistant, so requests that arrive from the Supervisor gateway
(172.30.32.2) are trusted as logged in (`from_gateway`). The same app also listens on a plain port for the phone app,
which still needs its login or device token.
"""
import os
import re

GATEWAY_IP = os.environ.get('HEADWIND_INGRESS_GATEWAY') or '172.30.32.2'   # Supervisor's ingress gateway; the override exists for tests

_ATTR = re.compile(r'''(\b(?:href|src|action|formaction|poster|data-src|data-href)\s*=\s*)(["'])/(?!/)''')
_JS_CALL = re.compile(r'''(\b(?:fetch|EventSource|open)\(\s*(?:['"][A-Z]+['"]\s*,\s*)?)(["'`])/(?!/)''')
_JS_LOC = re.compile(r'''(\blocation(?:\.href)?\s*=\s*)(["'`])/(?!/)''')
_JS_ASSIGN = re.compile(r'''(\blocation\.(?:assign|replace)\(\s*)(["'`])/(?!/)''')

_SHIM = """<script>(function(){var P=%s;function f(u){return (typeof u==='string'&&u.charAt(0)==='/'&&u.charAt(1)!=='/'&&u.indexOf(P+'/')!==0)?P+u:u}
var F=window.fetch;window.fetch=function(u,o){if(u&&u.url&&typeof u!=='string'){return F.call(this,u,o)}return F.call(this,f(u),o)};
var X=XMLHttpRequest.prototype.open;XMLHttpRequest.prototype.open=function(m,u){arguments[1]=f(u);return X.apply(this,arguments)};
if(window.EventSource){var E=window.EventSource;window.EventSource=function(u,c){return new E(f(u),c)};window.EventSource.prototype=E.prototype}
var O=window.open;window.open=function(u){arguments[0]=f(u);return O.apply(this,arguments)};
window.__ingressPrefix=P;})();</script>"""


def enabled():
    return os.environ.get('HEADWIND_INGRESS') == '1'


def clean_prefix(raw):
    """Only accept what Supervisor really sends; anything else becomes no prefix (it ends up in HTML, so be strict)."""
    raw = (raw or '').strip().rstrip('/')
    return raw if re.fullmatch(r'/api/hassio_ingress/[A-Za-z0-9_\-]+', raw) else ''


def rewrite_text(text, prefix):
    """Prefix the absolute paths in an HTML/JS body (paths url_for already prefixed are left alone)."""
    if not prefix:
        return text
    keep = re.escape(prefix) + '/'

    for rx in (_ATTR, _JS_CALL, _JS_LOC, _JS_ASSIGN):
        pat = re.compile(rx.pattern[:-len('/(?!/)')] + '/(?!/)(?!' + keep[1:] + ')')
        text = pat.sub(lambda m: f'{m.group(1)}{m.group(2)}{prefix}/', text)
    return text


def shim(prefix):
    import json
    return _SHIM % json.dumps(prefix)


class PrefixMiddleware:
    """WSGI: turn X-Ingress-Path into SCRIPT_NAME and remember whether the gateway itself sent the request."""

    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        prefix = clean_prefix(environ.get('HTTP_X_INGRESS_PATH'))
        if prefix:
            environ['SCRIPT_NAME'] = prefix
            environ['headwind.ingress_prefix'] = prefix
            path = environ.get('PATH_INFO', '')
            if path.startswith(prefix):               # be tolerant if a proxy did not strip it
                environ['PATH_INFO'] = path[len(prefix):] or '/'
        environ['headwind.from_gateway'] = environ.get('REMOTE_ADDR') == GATEWAY_IP
        return self.app(environ, start_response)


def install(app):
    """Wire the middleware, response rewriting and gateway trust into a Flask app (no-op unless enabled)."""
    if not enabled():
        return
    from flask import request

    # Outermost (install() runs after ProxyFix is added) so REMOTE_ADDR is still the TCP peer, not ProxyFix's X-Forwarded-For.
    app.wsgi_app = PrefixMiddleware(app.wsgi_app)

    @app.after_request
    def _rewrite(resp):
        prefix = request.environ.get('headwind.ingress_prefix')
        if not prefix:
            return resp
        loc = resp.headers.get('Location')
        if loc and loc.startswith('/') and not loc.startswith('//') and not loc.startswith(prefix + '/'):
            resp.headers['Location'] = prefix + loc
        ctype = (resp.mimetype or '')
        if resp.direct_passthrough or resp.status_code in (204, 304):
            return resp
        if ctype in ('text/html', 'application/javascript', 'text/javascript'):
            body = resp.get_data(as_text=True)
            body = rewrite_text(body, prefix)
            if ctype == 'text/html' and '<head' in body:
                body = re.sub(r'(<head[^>]*>)', lambda m: m.group(1) + shim(prefix), body, count=1)
            resp.set_data(body)
        return resp


def from_gateway(environ):
    return bool(enabled() and environ.get('headwind.from_gateway'))
