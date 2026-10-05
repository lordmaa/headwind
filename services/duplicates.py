"""The same ride recorded twice (e.g. phone app AND a Garmin head unit): find it, keep the better recording, park the other.

Parked rides are NOT deleted. They move to the ActivityDuplicate table (full row as JSON) and can be restored: swapped in as the main
ride, kept as a separate ride, or discarded for good. Keeping the parking lot outside Activity means every existing query (stats,
charts, badges, HA sensors, segments) keeps seeing exactly one ride, with no filtering to remember.

Matching is on the real instant (UTC), never on a local-time string: a phone GPX is UTC, Garmin gives local time, and comparing the raw
strings during British Summer Time is how a 1-hour gap hid the first duplicate ever seen here.
"""
import json
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)
LOCAL = ZoneInfo('Europe/London')          # the app's home timezone everywhere else (workouts, goals, HA)
UTC = timezone.utc

OVERLAP_MIN = 0.60          # the two recordings must overlap for at least this share of the shorter one
DIST_TOL = 0.15             # ...and agree on distance within 15% (different devices measure a ride a little differently)
SEARCH_DAYS = 1


def ensure_schema(db):
    db.execute('''
        CREATE TABLE IF NOT EXISTS ActivityDuplicate (
            id        TEXT PRIMARY KEY,                 -- the parked activity's own id
            primaryId TEXT NOT NULL,                    -- the ride kept in its place
            riderId   INTEGER,
            source    TEXT,                             -- 'Garmin' / 'Phone or file' (display only)
            reason    TEXT,                             -- why it lost, shown to the user
            status    TEXT NOT NULL DEFAULT 'parked',   -- parked | kept_both
            rowJson   TEXT NOT NULL,                    -- the full Activity row
            parkedAt  TEXT DEFAULT (datetime('now'))
        )''')
    db.execute('CREATE INDEX IF NOT EXISTS idx_actdup_primary ON ActivityDuplicate(primaryId)')


# ---------- time + matching (pure) ----------
def instant(value):
    """A timezone-aware UTC datetime from an ISO string. Naive strings are the home timezone's local time (what Garmin gives us)."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=LOCAL)).astimezone(UTC)


def span(row):
    """(start, end) instants of an activity row/dict, or None if it has no usable start."""
    start = instant(row['startDateLocal'] if row['startDateLocal'] else row['startDate'])
    if not start:
        return None
    secs = max(int(row['elapsedTime'] or 0), int(row['movingTime'] or 0))
    return start, start + timedelta(seconds=secs)


def same_ride(a, b):
    """True if two activity rows are probably one ride captured by two devices."""
    sa, sb = span(a), span(b)
    if not sa or not sb:
        return False
    overlap = (min(sa[1], sb[1]) - max(sa[0], sb[0])).total_seconds()
    shorter = min((sa[1] - sa[0]).total_seconds(), (sb[1] - sb[0]).total_seconds())
    da, db_ = float(a['distance'] or 0), float(b['distance'] or 0)
    if da > 0 and db_ > 0 and abs(da - db_) > DIST_TOL * max(da, db_):
        return False
    if da <= 0 or db_ <= 0:                             # no distance to compare (indoor / no GPS): demand an almost identical start and length
        la, lb = (sa[1] - sa[0]).total_seconds(), (sb[1] - sb[0]).total_seconds()
        return abs((sa[0] - sb[0]).total_seconds()) <= 120 and la > 0 and lb > 0 and abs(la - lb) <= 0.2 * max(la, lb)
    if shorter <= 0:                                    # no durations at all: fall back to "started within a minute"
        return abs((sa[0] - sb[0]).total_seconds()) <= 60 and da > 0 and db_ > 0
    return overlap >= OVERLAP_MIN * shorter


def source_label(row):
    return 'Garmin' if str(row['id']).startswith('garmin_') else ('Manual entry' if str(row['id']).startswith('man_') else 'Phone or file')


def quality(row):
    """(score, reasons) for how much a recording is worth keeping. Higher wins; a tie keeps the one already stored."""
    s, why = 0, []
    rid = str(row['id'])
    if rid.startswith('garmin_'):
        s += 40; why.append('recorded on a Garmin device')
    if rid.startswith('man_'):
        s -= 50; why.append('typed in by hand')
    if row['averageHeartrate']:
        s += 20; why.append('has heart rate')
    if row['averageWatts']:
        s += 10; why.append('has power')
    if row['averageCadence']:
        s += 5; why.append('has cadence')
    pts = 0
    try:
        pts = len(((json.loads(row['streams']) if row['streams'] else {}).get('latlng') or {}).get('data') or [])
    except (ValueError, TypeError, AttributeError):
        pass
    s += min(10, pts // 500)
    if pts == 0 and not rid.startswith('man_'):
        s -= 5; why.append('no GPS track')
    return s, why


def pick_winner(existing, new):
    """Returns (winner, loser, reason_text). `existing` wins ties."""
    se, we = quality(existing)
    sn, wn = quality(new)
    if sn > se:
        winner, loser, why = new, existing, wn
    else:
        winner, loser, why = existing, new, we
    reason = ('Kept the ' + source_label(winner) + ' recording (' + ', '.join(why) + ').') if why else ('Kept the ' + source_label(winner) + ' recording (the one already saved).')
    return winner, loser, reason


# ---------- database ----------
def _kept_both_partners(db, act_id):
    rows = db.execute("SELECT id, primaryId FROM ActivityDuplicate WHERE status='kept_both' AND (id=? OR primaryId=?)", [act_id, act_id]).fetchall()
    return {r['id'] for r in rows} | {r['primaryId'] for r in rows}


def find_duplicates(db, row, exclude_ids=()):
    """Other activities by the same rider that look like the same ride."""
    s = span(row)
    if not s:
        return []
    day = s[0].astimezone(LOCAL).date()
    lo, hi = (day - timedelta(days=SEARCH_DAYS)).isoformat(), (day + timedelta(days=SEARCH_DAYS + 1)).isoformat()
    cands = db.execute("SELECT * FROM Activity WHERE riderId=? AND id<>? AND substr(startDateLocal,1,10) BETWEEN ? AND ?", [row['riderId'], row['id'], lo, hi]).fetchall()
    skip = set(exclude_ids) | _kept_both_partners(db, row['id'])
    return [c for c in cands if c['id'] not in skip and same_ride(row, c)]


def _drop_derived(db, act_id):
    from services.segments import _refresh_prs
    seg_ids = [r[0] for r in db.execute('SELECT DISTINCT segmentId FROM SegmentEffort WHERE activityId=?', [act_id])]
    db.execute('DELETE FROM SegmentEffort WHERE activityId=?', [act_id])
    db.execute('DELETE FROM BestEffort WHERE activityId=?', [act_id])
    db.execute('DELETE FROM RideMemory WHERE rideId=?', [act_id])
    return seg_ids


def rebuild_derived(db, act_id):
    """Best efforts + segment efforts + PRs for one ride (after it has been restored as the main recording)."""
    from services.best_efforts import save_best_efforts
    from services.segments import scan_activity_against_segments, _refresh_prs
    row = db.execute('SELECT * FROM Activity WHERE id=?', [act_id]).fetchone()
    if not row:
        return
    seg_ids = _drop_derived(db, act_id)
    save_best_efforts(db, row['id'], row['startDateLocal'], row['streams'])
    segments = db.execute('SELECT * FROM Segment').fetchall()
    if segments:
        scan_activity_against_segments(db, row, segments)
        seg_ids = list({*seg_ids, *[s['id'] for s in segments]})
    for sid in seg_ids:
        _refresh_prs(db, sid)


def park(db, loser_id, primary_id, reason):
    """Move an activity out of Activity into ActivityDuplicate. Does not commit."""
    from services.segments import _refresh_prs
    row = db.execute('SELECT * FROM Activity WHERE id=?', [loser_id]).fetchone()
    if not row:
        return False
    ensure_schema(db)
    winner = db.execute('SELECT notes, description FROM Activity WHERE id=?', [primary_id]).fetchone()
    if winner:                                          # never lose something the user typed on the ride that is being parked
        for col in ('notes', 'description'):
            if row[col] and not winner[col]:
                db.execute(f'UPDATE Activity SET {col}=? WHERE id=?', [row[col], primary_id])
    db.execute('INSERT OR REPLACE INTO ActivityDuplicate (id, primaryId, riderId, source, reason, status, rowJson) VALUES (?,?,?,?,?,?,?)',
               [loser_id, primary_id, row['riderId'], source_label(row), reason, 'parked', json.dumps(dict(row))])
    seg_ids = _drop_derived(db, loser_id)
    db.execute('DELETE FROM Activity WHERE id=?', [loser_id])
    for sid in seg_ids:
        _refresh_prs(db, sid)
    log.warning('Duplicate ride: parked %s, kept %s (%s)', loser_id, primary_id, reason)
    return True


def resolve(db, new_id):
    """Call after a ride (and its best efforts / segments) has been stored. If it duplicates an existing ride, keeps the better recording
    and parks the other. Returns {'kept': id, 'parked': id, 'reason': text} or None. Does not commit."""
    ensure_schema(db)
    db.execute("DELETE FROM ActivityDuplicate WHERE id=? AND status='deleted'", [new_id])     # brought back on purpose
    new = db.execute('SELECT * FROM Activity WHERE id=?', [new_id]).fetchone()
    if not new:
        return None
    dups = find_duplicates(db, new)
    if not dups:
        return None
    current = new
    result = None
    for other in dups:                                  # normally exactly one
        winner, loser, reason = pick_winner(other, current)
        if not park(db, loser['id'], winner['id'], reason):
            continue
        result = {'kept': winner['id'], 'parked': loser['id'], 'reason': reason}
        if loser['id'] == current['id']:
            break                                       # the new ride lost; nothing more to compare
        current = db.execute('SELECT * FROM Activity WHERE id=?', [winner['id']]).fetchone() or current
    if result and result['kept'] == new_id:
        rebuild_derived(db, new_id)                     # it displaced an older row that had its own derived data
    return result


def exists_anywhere(db, act_id):
    """True if the id is a live activity OR one parked as a duplicate, so a re-sync never re-adds a ride the user already resolved.
    (Deleted rides are tombstones, see is_deleted: a file you upload yourself may bring a deleted ride back, an automatic sync may not.)"""
    ensure_schema(db)
    return bool(db.execute("SELECT 1 FROM Activity WHERE id=? UNION SELECT 1 FROM ActivityDuplicate WHERE id=? AND status<>'deleted'", [act_id, act_id]).fetchone())


def is_deleted(db, act_id):
    ensure_schema(db)
    return bool(db.execute("SELECT 1 FROM ActivityDuplicate WHERE id=? AND status='deleted'", [act_id]).fetchone())


def tombstone(db, act_id, rider_id):
    """Remember that the user deleted this ride (only the id, none of its data). Does not commit."""
    ensure_schema(db)
    db.execute("INSERT OR REPLACE INTO ActivityDuplicate (id, primaryId, riderId, source, reason, status, rowJson) VALUES (?,?,?,?,?,?,?)",
               [act_id, '', rider_id, 'deleted', 'deleted by you', 'deleted', '{}'])


def delete_activity(db, act_id):
    """The one place a ride is deleted (web button, phone app): derived data, parked duplicates of it, then a tombstone. Does not commit."""
    from services.segments import _refresh_prs
    row = db.execute('SELECT id, riderId FROM Activity WHERE id=?', [act_id]).fetchone()
    if not row:
        return False
    seg_ids = _drop_derived(db, act_id)
    db.execute('DELETE FROM Activity WHERE id=?', [act_id])
    db.execute("UPDATE ActivityDuplicate SET primaryId='' WHERE primaryId=? AND status='kept_both'", [act_id])
    for sid in seg_ids:
        _refresh_prs(db, sid)
    tombstone(db, act_id, row['riderId'])
    return True


def parked_for(db, primary_id):
    ensure_schema(db)
    return db.execute("SELECT id, source, reason, status, parkedAt, rowJson FROM ActivityDuplicate WHERE primaryId=? AND status='parked' ORDER BY parkedAt", [primary_id]).fetchall()


def _insert_row(db, row):
    cols = list(row.keys())
    db.execute(f"INSERT OR REPLACE INTO Activity ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", [row[c] for c in cols])


def _restore(db, dup_id):
    d = db.execute('SELECT * FROM ActivityDuplicate WHERE id=?', [dup_id]).fetchone()
    if not d:
        return None, None
    row = json.loads(d['rowJson'])
    if not row:                                         # discarded: the data is gone
        return None, None
    _insert_row(db, row)
    return d, row


def swap(db, dup_id):
    """Make the parked recording the main one; the current main one is parked in its place. Returns the new main id (or None)."""
    d = db.execute('SELECT * FROM ActivityDuplicate WHERE id=?', [dup_id]).fetchone()
    if not d or d['status'] != 'parked':
        return None
    old_primary = d['primaryId']
    row = json.loads(d['rowJson'])
    if not row:
        return None
    db.execute('DELETE FROM ActivityDuplicate WHERE id=?', [dup_id])
    if db.execute('SELECT 1 FROM Activity WHERE id=?', [old_primary]).fetchone():
        park(db, old_primary, dup_id, 'You chose to use the other recording.')
    _insert_row(db, row)
    others = db.execute("SELECT id FROM ActivityDuplicate WHERE primaryId=? AND id<>?", [old_primary, old_primary]).fetchall()
    for o in others:                                    # anything else parked under the old main now hangs off the new one
        db.execute('UPDATE ActivityDuplicate SET primaryId=? WHERE id=?', [dup_id, o['id']])
    rebuild_derived(db, dup_id)
    return dup_id


def keep_both(db, dup_id):
    """Restore the parked ride as its own ride and remember never to merge this pair again."""
    d, _ = _restore(db, dup_id)
    if not d:
        return None
    db.execute("UPDATE ActivityDuplicate SET status='kept_both' WHERE id=?", [dup_id])
    rebuild_derived(db, dup_id)
    return dup_id


def discard(db, dup_id):
    """Delete a parked recording for good. Its id stays remembered (so a Garmin re-sync does not bring it back)."""
    n = db.execute("UPDATE ActivityDuplicate SET rowJson='{}', status='discarded' WHERE id=? AND status='parked'", [dup_id]).rowcount
    return bool(n)
