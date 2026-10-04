"""Entry point for the packaged Windows build (Headwind.exe). Keeps all user data in %LOCALAPPDATA%\\Headwind, serves with
waitress (gunicorn doesn't run on Windows) and opens the browser. Not used by the Docker/Linux install."""
import os
import sys
import threading
import webbrowser
from pathlib import Path

DATA = Path(os.environ.get('HEADWIND_DATA') or (Path(os.environ.get('LOCALAPPDATA') or Path.home()) / 'Headwind'))
for sub in ('', 'avatars', 'garmin_tokens'):
    (DATA / sub).mkdir(parents=True, exist_ok=True)

PORT = int(os.environ.get('PORT', 5001))
# localhost only by default (no Windows Firewall prompt, nothing exposed). Set HEADWIND_HOST=0.0.0.0 to reach it from a phone.
HOST = os.environ.get('HEADWIND_HOST', '127.0.0.1')

os.environ.setdefault('DATABASE_URL', str(DATA / 'bike.db'))
os.environ.setdefault('HEADWIND_TOKEN_DIR', str(DATA / 'garmin_tokens'))
os.environ.setdefault('AVATAR_DIR', str(DATA / 'avatars'))
os.environ.setdefault('HEADWIND_ENV', str(DATA / 'headwind.env'))
os.environ.setdefault('APP_URL', f'http://localhost:{PORT}')
Path(os.environ['HEADWIND_ENV']).touch(exist_ok=True)

import logging  # noqa: E402
logging.basicConfig(level=logging.WARNING, format='%(message)s')
logging.getLogger('services.credentials').setLevel(logging.ERROR)   # the banner below shows the generated login instead

from app import create_app  # noqa: E402
from services import credentials  # noqa: E402
from version import __version__  # noqa: E402

app = create_app()
user, pw, from_file = credentials.admin_login()

print('=' * 64)
print(f'  Headwind {__version__}  —  http://localhost:{PORT}')
print(f'  Your data lives in: {DATA}')
if from_file:
    print(f'  Sign in with   username: {user}   password: {pw}')
    print('  (change it under Settings after signing in)')
print('  Leave this window open while you use Headwind. Close it to stop.')
print('=' * 64, flush=True)

threading.Timer(2.0, lambda: webbrowser.open(f'http://localhost:{PORT}')).start()

from waitress import serve  # noqa: E402
serve(app, host=HOST, port=PORT, threads=8)
