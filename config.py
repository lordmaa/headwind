import os
from dotenv import load_dotenv

load_dotenv(override=True)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret')
    DATABASE = os.environ.get('DATABASE_URL', os.path.join(BASE_DIR, 'bike.db'))
    STRAVA_CLIENT_ID = os.environ.get('STRAVA_CLIENT_ID', '')
    STRAVA_CLIENT_SECRET = os.environ.get('STRAVA_CLIENT_SECRET', '')
    APP_URL = os.environ.get('APP_URL', 'http://localhost:5000')
    STRAVA_WEBHOOK_TOKEN = os.environ.get('STRAVA_WEBHOOK_TOKEN', 'changeme')
    MAX_CONTENT_LENGTH  = 2 * 1024 * 1024 * 1024  # 2 GB — for large Strava export zips
    SESSION_COOKIE_HTTPONLY  = True
    SESSION_COOKIE_SAMESITE  = 'Lax'
    SESSION_COOKIE_SECURE    = os.environ.get('SESSION_COOKIE_SECURE', 'false').lower() == 'true'
