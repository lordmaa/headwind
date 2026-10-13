#!/bin/sh
# Normal Docker: just start gunicorn. As a Home Assistant add-on (Supervisor sets SUPERVISOR_TOKEN) first switch on ingress
# support, keep every piece of state under the add-on's persistent /data, and wire MQTT + Home Assistant up automatically.
set -e
mkdir -p /data/avatars
if [ -n "$SUPERVISOR_TOKEN" ]; then
  export HEADWIND_INGRESS=1
  mkdir -p /data/garmin_tokens
  rm -rf /app/.garmin_tokens && ln -s /data/garmin_tokens /app/.garmin_tokens
  python3 /app/scripts/addon_boot.py || echo "addon_boot: continuing without automatic Home Assistant setup"
fi
# Single worker (background threads — MQTT heartbeat, Garmin sync — must not be duplicated) with several request threads, so a long
# streaming sync or import no longer blocks every other page. Timeout 300s covers large Strava export imports.
exec gunicorn --workers 1 --threads 8 --bind 0.0.0.0:5001 --timeout 300 'app:create_app()'
