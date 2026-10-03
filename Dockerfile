FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Stamp build time so the settings page can show it and check for updates
RUN date -u +%Y-%m-%dT%H:%M:%SZ > /app/build_time.txt

# Persistent data lives outside the image; rider avatars are symlinked into the /data volume so they survive upgrades
RUN mkdir -p /data/avatars /app/.garmin_tokens && rm -rf /app/static/avatars && ln -s /data/avatars /app/static/avatars

EXPOSE 5001

ENV DATABASE_URL=/data/bike.db

# Single worker (background threads — MQTT heartbeat, Garmin sync — must not be duplicated) with several request threads, so a long
# streaming sync or import no longer blocks every other page.
# Timeout 300s covers large Strava export imports.
CMD ["sh", "-c", "mkdir -p /data/avatars && exec gunicorn --workers 1 --threads 8 --bind 0.0.0.0:5001 --timeout 300 'app:create_app()'"]
