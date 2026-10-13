"""One person per Headwind instance is the supported default; several local riders is an advanced mode.

Multi-rider is on when HEADWIND_MULTI_RIDER=1, or automatically for an existing instance that already has more than one
local rider (friends' riders, created by friend sync, don't count). So upgrading never takes features away from anyone
who is already using them — new installs just start simple."""
import os

from database import query_db


def multi_rider():
    if os.environ.get('HEADWIND_MULTI_RIDER') == '1':
        return True
    try:
        row = query_db('SELECT COUNT(*) FROM Rider r WHERE NOT EXISTS (SELECT 1 FROM Friend f WHERE f.riderId = r.id)', one=True)
    except Exception:          # tables not created yet (very first request) — treat as single-rider
        return False
    return bool(row and row[0] > 1)


def owner_rider_id():
    try:
        row = query_db('SELECT id FROM Rider WHERE isDefault=1 ORDER BY id LIMIT 1', one=True)
    except Exception:
        return None
    return row['id'] if row else None
