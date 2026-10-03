import http.server
import threading
import urllib.error

import pytest

from services.peer_http import open_peer


class _H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/same':
            self.send_response(302); self.send_header('Location', '/ok'); self.end_headers()
        elif self.path == '/off':
            self.send_response(302); self.send_header('Location', 'http://127.0.0.2:1/x'); self.end_headers()
        else:
            self.send_response(200); self.end_headers(); self.wfile.write(b'hi')

    def log_message(self, *a):
        pass


@pytest.fixture
def server():
    s = http.server.HTTPServer(('127.0.0.1', 0), _H)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield f'http://127.0.0.1:{s.server_port}'
    s.shutdown()


def test_same_origin_redirect_followed(server):
    assert open_peer(server + '/same').read() == b'hi'


def test_cross_origin_redirect_refused(server):
    with pytest.raises(urllib.error.HTTPError):
        open_peer(server + '/off', {'X-Feed-Token': 'secret'})


def test_non_http_scheme_refused():
    with pytest.raises(ValueError):
        open_peer('file:///etc/passwd')
