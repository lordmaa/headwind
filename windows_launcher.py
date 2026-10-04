"""Entry point for the packaged Windows build (Headwind.exe): a tray app that runs the server in the background and opens
Headwind in an app-style window. All user data lives in %LOCALAPPDATA%\\Headwind. Not used by the Docker/Linux install.

  Headwind.exe              start (or just open the window if it's already running)
  Headwind.exe --background start to the tray without opening a window (used by "Start with Windows")
  HEADWIND_NO_TRAY=1        headless/console mode (used by the CI smoke test)
  HEADWIND_HOST=0.0.0.0     also listen on the network, e.g. for the phone app (default: this PC only)
"""
import ctypes
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

WIN = sys.platform == 'win32'
BACKGROUND = '--background' in sys.argv
NO_TRAY = bool(os.environ.get('HEADWIND_NO_TRAY')) or not WIN

DATA = Path(os.environ.get('HEADWIND_DATA') or (Path(os.environ.get('LOCALAPPDATA') or Path.home()) / 'Headwind'))
for sub in ('', 'avatars', 'garmin_tokens'):
    (DATA / sub).mkdir(parents=True, exist_ok=True)

# A windowed (no console) exe has no stdout/stderr: send everything to a log file so nothing crashes on print().
LOG = DATA / 'headwind.log'
if sys.stdout is None or sys.stderr is None or os.environ.get('HEADWIND_LOG_TO_FILE'):
    if LOG.exists() and LOG.stat().st_size > 1_000_000:
        LOG.write_text('')
    sys.stdout = sys.stderr = open(LOG, 'a', buffering=1, encoding='utf-8')

PORT = int(os.environ.get('PORT', 5001))
HOST = os.environ.get('HEADWIND_HOST', '127.0.0.1')
URL = f'http://localhost:{PORT}'

os.environ.setdefault('DATABASE_URL', str(DATA / 'bike.db'))
os.environ.setdefault('HEADWIND_TOKEN_DIR', str(DATA / 'garmin_tokens'))
os.environ.setdefault('AVATAR_DIR', str(DATA / 'avatars'))
os.environ.setdefault('HEADWIND_ENV', str(DATA / 'headwind.env'))
os.environ.setdefault('APP_URL', URL)
Path(os.environ['HEADWIND_ENV']).touch(exist_ok=True)


def resource(rel):
    base = Path(getattr(sys, '_MEIPASS', Path(__file__).parent))
    return base / rel


def message(title, text):
    """Native message box on Windows; stdout elsewhere."""
    if WIN:
        ctypes.windll.user32.MessageBoxW(0, text, title, 0x40)   # MB_ICONINFORMATION
    else:
        print(f'{title}: {text}', flush=True)


def port_in_use():
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(('127.0.0.1', PORT)) == 0


def open_window():
    """An Edge/Chrome "app" window (no tabs or address bar); falls back to the default browser."""
    if WIN:
        for env in ('ProgramFiles(x86)', 'ProgramFiles', 'LOCALAPPDATA'):
            root = os.environ.get(env)
            if not root:
                continue
            for rel in (r'Microsoft\Edge\Application\msedge.exe', r'Google\Chrome\Application\chrome.exe'):
                exe = Path(root) / rel
                if exe.is_file():
                    subprocess.Popen([str(exe), f'--app={URL}', '--window-size=1320,880'])
                    return
    webbrowser.open(URL)


# ---- "Start with Windows" (per-user Run key, no admin needed) ----
RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'


def startup_enabled():
    if not WIN:
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, 'Headwind')
            return True
    except OSError:
        return False


def set_startup(enabled):
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, 'Headwind', 0, winreg.REG_SZ, f'"{sys.executable}" --background')
        else:
            try:
                winreg.DeleteValue(key, 'Headwind')
            except FileNotFoundError:
                pass


def main():
    if port_in_use():                  # already running: just bring up a window
        if not BACKGROUND and not os.environ.get('HEADWIND_NO_BROWSER'):
            open_window()
        return

    import logging
    logging.basicConfig(level=logging.WARNING, format='%(message)s')
    logging.getLogger('services.credentials').setLevel(logging.ERROR)   # shown via the sign-in dialog/banner instead

    from app import create_app
    from services import credentials
    from version import __version__
    from waitress import serve

    app = create_app()
    user, pw, generated = credentials.admin_login()
    threading.Thread(target=lambda: serve(app, host=HOST, port=PORT, threads=8), daemon=True).start()
    for _ in range(60):                # wait until it's actually listening
        if port_in_use():
            break
        time.sleep(0.25)

    if NO_TRAY:                        # console / CI mode
        print('=' * 64)
        print(f'  Headwind {__version__}  —  {URL}\n  Data: {DATA}')
        if generated:
            print(f'  Sign in with   username: {user}   password: {pw}')
        print('=' * 64, flush=True)
        if not BACKGROUND and not os.environ.get('HEADWIND_NO_BROWSER'):
            threading.Timer(1.0, open_window).start()
        while True:
            time.sleep(3600)

    import pystray
    from PIL import Image

    def sign_in_details(*_):
        u, p, _g = credentials.admin_login()
        message('Headwind sign-in', f'Username: {u}\nPassword: {p}\n\nChange it under Settings.')

    def toggle_startup(*_):
        set_startup(not startup_enabled())

    def quit_app(icon, *_):
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem('Open Headwind', lambda *_: open_window(), default=True),
        pystray.MenuItem('Show sign-in details', sign_in_details),
        pystray.MenuItem('Open data folder', lambda *_: os.startfile(DATA)),
        pystray.MenuItem('Start with Windows', toggle_startup, checked=lambda *_: startup_enabled()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem('Quit Headwind', quit_app),
    )
    icon = pystray.Icon('Headwind', Image.open(resource('static/logo.png')).convert('RGBA').resize((64, 64)),
                        f'Headwind {__version__}', menu)

    if generated and not BACKGROUND:   # first run: tell them their login (in a thread so it doesn't block the tray)
        threading.Thread(target=message, daemon=True, args=(
            'Welcome to Headwind',
            f'Your sign-in:\n\nUsername: {user}\nPassword: {pw}\n\nChange it under Settings. You can see it again any time '
            'from the tray icon (bottom right, near the clock) → Show sign-in details.')).start()
    if not BACKGROUND:
        open_window()
    icon.run()                         # blocks until Quit
    os._exit(0)


if __name__ == '__main__':
    main()
