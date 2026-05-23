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

# Persistent data lives outside the image
RUN mkdir -p /data /app/.garmin_tokens

EXPOSE 5001

ENV DATABASE_URL=/data/bike.db

# Single worker so background threads (MQTT heartbeat, Garmin sync) behave correctly.
# Timeout 300s covers large Strava export imports.
CMD ["gunicorn", "--workers", "1", "--bind", "0.0.0.0:5001", "--timeout", "300", "app:create_app()"]
