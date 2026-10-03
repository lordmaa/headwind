import os
from dotenv import load_dotenv

from services.credentials import secret_key

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# HEADWIND_ENV points a second instance (same code, own data) at its own env file; default is ./.env
ENV_FILE = os.environ.get('HEADWIND_ENV') or os.path.join(BASE_DIR, '.env')
load_dotenv(ENV_FILE, override=True)


_DATABASE = os.environ.get('DATABASE_URL', os.path.join(BASE_DIR, 'bike.db'))


class Config:
    DATABASE = _DATABASE
    SECRET_KEY = secret_key(os.environ.get('SECRET_KEY', ''), _DATABASE)
    APP_URL = os.environ.get('APP_URL', 'http://localhost:5000')
    MAX_CONTENT_LENGTH  = 2 * 1024 * 1024 * 1024  # 2 GB — for large Strava-export zips (import only, no live API)
    SESSION_COOKIE_HTTPONLY  = True
    SESSION_COOKIE_SAMESITE  = 'Lax'
    SESSION_COOKIE_SECURE    = os.environ.get('SESSION_COOKIE_SECURE', 'false').lower() == 'true'
