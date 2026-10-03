"""HTTP to a friend's Headwind instance. Only http(s) URLs; redirects are followed only within the same origin, so a
peer can't bounce us (and our X-Feed-Token header) to another host or an internal service."""
import urllib.error
import urllib.request
from urllib.parse import urlparse


def _origin(url):
    u = urlparse(url)
    return (u.scheme, u.hostname, u.port or (443 if u.scheme == 'https' else 80))


class _SameOriginRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if _origin(newurl) != _origin(req.full_url):
            raise urllib.error.HTTPError(req.full_url, code, 'cross-origin redirect refused', headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_SameOriginRedirect)


def open_peer(url, headers=None, timeout=30):
    if urlparse(url).scheme not in ('http', 'https'):
        raise ValueError('friend URL must start with http:// or https://')
    return _opener.open(urllib.request.Request(url, headers=headers or {}), timeout=timeout)
