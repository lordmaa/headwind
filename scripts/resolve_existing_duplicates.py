"""Find rides that were recorded twice (phone + Garmin etc.) and fix the local-time labels of old phone/GPX imports.

  python3 scripts/resolve_existing_duplicates.py                 # DRY RUN: only lists what it would do
  python3 scripts/resolve_existing_duplicates.py --apply --ids garmin_1,imp_2   # do it for those rides only (backup first: scripts/headwind-both.sh backup)
  python3 scripts/resolve_existing_duplicates.py --apply --all   # do it for EVERYTHING (changes old history totals: look at the dry run first)
  python3 scripts/resolve_existing_duplicates.py --apply --db /path/to/other.db

Duplicates are PARKED, never deleted (open the kept ride to switch, keep both or delete). Time fix: a GPX imported before 2026-10-05 stored its UTC
time in startDateLocal (an hour early in summer); those rows are rewritten to UTC instant + London local, like Garmin rides.
"""
import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services import duplicates as dup

ap = argparse.ArgumentParser()
ap.add_argument('--apply', action='store_true')
ap.add_argument('--db', default=None)
ap.add_argument('--ids', default='', help='comma-separated ride ids: only touch rows/pairs involving these')
ap.add_argument('--all', action='store_true')
args = ap.parse_args()
only = {x.strip() for x in args.ids.split(',') if x.strip()}
if args.apply and not (only or args.all):
    sys.exit('Refusing to --apply without --ids or --all (the dry run shows what each would touch).')
if args.db is None:
    from dotenv import dotenv_values
    args.db = dotenv_values(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')).get('DATABASE_URL')
db = sqlite3.connect(args.db, timeout=60)
db.row_factory = sqlite3.Row
print(('APPLYING to ' if args.apply else 'DRY RUN on ') + args.db)

# 1) time labels
fix = [r for r in db.execute("SELECT id, startDate, startDateLocal FROM Activity WHERE startDateLocal LIKE '%+00:00' OR startDateLocal LIKE '%Z'")]
print(f'\n{len(fix)} ride(s) with a UTC time stored as local:')
for r in fix:
    utc = dup.instant(r['startDateLocal'])
    new_local = utc.astimezone(dup.LOCAL).strftime('%Y-%m-%dT%H:%M:%S')
    print(f"  {r['id']}: {r['startDateLocal']} -> local {new_local}")
    if args.apply and (args.all or r['id'] in only):
        db.execute('UPDATE Activity SET startDate=?, startDateLocal=? WHERE id=?', [utc.strftime('%Y-%m-%dT%H:%M:%S'), new_local, r['id']])
if args.apply:
    db.commit()

# 2) duplicates (after the time fix, so comparisons are right)
dup.ensure_schema(db)
seen, pairs = set(), []
for row in db.execute('SELECT * FROM Activity ORDER BY startDateLocal').fetchall():
    if row['id'] in seen:
        continue
    for other in dup.find_duplicates(db, row):
        if other['id'] in seen:
            continue
        w, l, reason = dup.pick_winner(other, row) if other['createdAt'] <= row['createdAt'] else dup.pick_winner(row, other)
        pairs.append((w, l, reason)); seen.update([w['id'], l['id']])
print(f'\n{len(pairs)} duplicate pair(s):')
for w, l, reason in pairs:
    print(f"  rider {w['riderId']}  {str(w['startDateLocal'])[:19]}  keep {w['id']} ({w['distance'] / 1609.344:.1f} mi, HR {w['averageHeartrate']})  park {l['id']} ({l['distance'] / 1609.344:.1f} mi, HR {l['averageHeartrate']})  - {reason}")
    if args.apply and (args.all or {w['id'], l['id']} & only):
        dup.park(db, l['id'], w['id'], reason)
        dup.rebuild_derived(db, w['id'])
if args.apply:
    db.commit()
    print('\ndone')
else:
    print('\n(dry run: nothing changed. Add --apply to do it.)')
