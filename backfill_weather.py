#!/usr/bin/env python3
"""Run once to backfill weather for all rides missing it."""
import sys
sys.path.insert(0, '/home/rob/bike-flask')

from app import create_app
from services.weather import fetch_weather, save_weather

app = create_app()
with app.app_context():
    from database import get_db
    db = get_db()

    rows = db.execute('''
        SELECT id, startLat, startLng, startDateLocal, streams
        FROM Activity
        WHERE startLat IS NOT NULL AND startLng IS NOT NULL
          AND weatherSummary IS NULL
        ORDER BY startDateLocal DESC
    ''').fetchall()

    total = len(rows)
    print(f'Backfilling weather for {total} rides...', flush=True)

    done = failed = 0
    for i, r in enumerate(rows):
        try:
            w = fetch_weather(r['startLat'], r['startLng'], r['startDateLocal'], r['streams'])
            if w:
                save_weather(db, r['id'], w)
                db.commit()
                done += 1
            else:
                failed += 1
        except Exception as e:
            failed += 1
            print(f'  [!] {r["id"]}: {e}', flush=True)

        if (i + 1) % 100 == 0:
            print(f'  {i+1}/{total} — {done} updated, {failed} failed', flush=True)

    print(f'Done: {done} updated, {failed} failed out of {total}', flush=True)
