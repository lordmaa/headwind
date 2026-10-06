#!/bin/sh
# Demo container entrypoint: seed /data from the read-only pristine copy on first start, then run gunicorn.
# The nightly reset (services/demo.py) restores from the same pristine files, so /data is disposable.
set -e
mkdir -p /data/avatars
if [ ! -f /data/bike.db ]; then
  cp /demo/pristine.db /data/bike.db
  [ -d /demo/bikeimg ] && mkdir -p /data/bikeimg && cp -r /demo/bikeimg/. /data/bikeimg/ || true
fi
exec gunicorn --workers 1 --threads 8 --bind 0.0.0.0:5001 --timeout 120 'app:create_app()'
