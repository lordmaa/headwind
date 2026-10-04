"""Recompute true max speed / max heart rate / max power for already-imported rides (see services/max_stats.py).

  python3 scripts/backfill_max_stats.py            # dry run: shows what WOULD change, writes nothing
  python3 scripts/backfill_max_stats.py --apply    # writes (take a backup first: scripts/headwind-both.sh backup)
  python3 scripts/backfill_max_stats.py --apply --rewind-wind   # ALSO re-label headwind/tailwind with the new whole-route method
                                                                  (can change badge counts such as "Headwind Hero")

Uses the database named by DATABASE_URL / HEADWIND_ENV, exactly like the app."""
import sqlite3
import sys

sys.path.insert(0, '.')
from config import Config            # noqa: E402  (also loads the env file)
from services.max_stats import backfill   # noqa: E402

apply = '--apply' in sys.argv
db = sqlite3.connect(Config.DATABASE, timeout=60)
db.row_factory = sqlite3.Row
counts = backfill(db, apply=apply, rewind_wind='--rewind-wind' in sys.argv)
if apply:
    db.commit()
print(('APPLIED' if apply else 'DRY RUN (nothing written)') + f" on {Config.DATABASE}")
print(f"  rides scanned: {counts['rides']}")
print(f"  max speed fixed: {counts['maxSpeed']} | max heart rate set: {counts['maxHeartrate']} | max power set: {counts['maxWatts']} | wind labels changed: {counts['windRel']}")
