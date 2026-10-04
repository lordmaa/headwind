"""Build the Windows (or, for testing, any-OS) packaged app with PyInstaller:  python scripts/build_windows.py [outdir]
Every module under routes/ and services/ is listed explicitly — the app imports some of them dynamically, which
PyInstaller's analysis (and --collect-submodules) can silently miss."""
import os
import sys
from pathlib import Path

import PyInstaller.__main__

root = Path(__file__).resolve().parent.parent
out = Path(sys.argv[1]) if len(sys.argv) > 1 else root / 'dist'
sep = os.pathsep   # ';' on Windows, ':' elsewhere
out.mkdir(parents=True, exist_ok=True)
(out / 'build').mkdir(exist_ok=True)
icon = out / 'build' / 'headwind.ico'   # exe icon, made from the app logo
from PIL import Image
Image.open(root / 'static' / 'logo.png').convert('RGBA').save(icon, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
args = [
    str(root / 'windows_launcher.py'), '--noconfirm', '--clean', '--onedir', '--windowed', '--name', 'Headwind', '--icon', str(icon),
    '--distpath', str(out), '--workpath', str(out / 'build'), '--specpath', str(out / 'build'),
    '--paths', str(root),
    '--add-data', f'{root / "templates"}{sep}templates', '--add-data', f'{root / "static"}{sep}static',
    '--add-data', f'{root / "version.py"}{sep}.',
    '--collect-all', 'curl_cffi', '--collect-all', 'garminconnect', '--hidden-import', 'waitress',
    '--collect-data', 'tzdata', '--hidden-import', 'tzdata', '--hidden-import', 'pystray._win32',   # zoneinfo on Windows reads timezones from this package
]
for pkg in ('routes', 'services'):
    for f in sorted((root / pkg).glob('*.py')):
        args += ['--hidden-import', pkg if f.stem == '__init__' else f'{pkg}.{f.stem}']
for mod in ('app', 'config', 'database', 'version'):
    args += ['--hidden-import', mod]
PyInstaller.__main__.run(args)
print('built:', out / 'Headwind')
