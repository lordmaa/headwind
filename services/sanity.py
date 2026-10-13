"""Safety rails for junk rides. A bad import (a 3-second moving time against a real distance, a GPS blip, a unit slip) can produce a ride at 2,500 mph that
wrecks every chart's axis. Each new ride is checked against generous upper and lower limits; one that breaks them is FLAGGED (not deleted, not hidden
from its own page) with the reason, left out of the speed and weather analytics until the rider looks at it, and the rider can dismiss the flag
("it is right") or delete the ride. A dismissed ride is never flagged again.

The limits are deliberately loose: they catch the impossible, not the unusual. Typical hard-but-real rides (a 40 mph average on a descent-heavy club run, a 7,000 m
day) pass.
"""
MI = 1609.344

# upper / lower limits for a ride
MAX_AVG_MPH = 45          # average speed over the whole ride
MAX_TOP_MPH = 80          # fastest instant (GPS spikes are common, so this is above any real bike speed)
MIN_AVG_MPH = 0.9         # slower than a crawl for a recording this long means it never really moved
MIN_DIST_M = 160          # about 0.1 mile: a start-stop blip, not a ride
MAX_DIST_MI = 1500
MAX_ELEV_M = 9000
MAX_GRADE = 0.35          # climb / distance, once past a kilometre
MAX_HOURS = 72
MAX_KCAL = 15000

SANE_SQL = "(flagReason IS NULL OR flagDismissed = 1)"      # for charts: rides nobody has flagged, or that the rider cleared


def reasons(r):
    """List of readable reasons a ride looks wrong (empty = fine). `r` is a mapping with the Activity columns."""
    sport = str(r['sportType'] or '').lower()
    if 'ride' not in sport and 'cycl' not in sport:
        return []
    dist = float(r['distance'] or 0)
    moving = float(r['movingTime'] or 0)
    elapsed = float(r['elapsedTime'] or 0)
    avg = float(r['averageSpeed'] or 0) * 2.23694
    top = float(r['maxSpeed'] or 0) * 2.23694
    elev = float(r['totalElevationGain'] or 0)
    out = []
    if avg > MAX_AVG_MPH:
        out.append('average speed of %s mph is not possible on a bike' % f'{avg:,.0f}')
    if top > MAX_TOP_MPH:
        out.append('top speed of %s mph is a GPS glitch' % f'{top:,.0f}')
    if 0 < dist < MIN_DIST_M:
        out.append('only %d m long' % round(dist))
    elif dist >= 1000 and 0 < avg < MIN_AVG_MPH:
        out.append('barely moved (%.1f mph average over %.1f mi)' % (avg, dist / MI))
    if dist >= 1000 and 0 < moving < 60:
        out.append('moving time of %d s against %.1f mi' % (moving, dist / MI))
    if dist / MI > MAX_DIST_MI:
        out.append('%s miles is beyond any ride' % f'{dist / MI:,.0f}')
    if elev > MAX_ELEV_M or (dist >= 1000 and elev / dist > MAX_GRADE):
        out.append('climb of %s m does not fit %.1f mi' % (f'{elev:,.0f}', dist / MI))
    if elapsed > MAX_HOURS * 3600:
        out.append('lasts %d hours' % round(elapsed / 3600))
    if float(r['calories'] or 0) > MAX_KCAL:
        out.append('%s kcal is too many for one ride' % f'{float(r["calories"]):,.0f}')
    return out


def flag(db, act_id):
    """Check one ride and record the verdict. A ride the rider has dismissed keeps its all-clear. Returns the reason text or None. Does not commit."""
    r = db.execute('SELECT id, sportType, distance, movingTime, elapsedTime, averageSpeed, maxSpeed, totalElevationGain, calories, flagDismissed FROM Activity WHERE id=?', [act_id]).fetchone()
    if not r or r['flagDismissed']:
        return None
    why = '; '.join(reasons(r)) or None
    db.execute('UPDATE Activity SET flagReason=? WHERE id=?', [why, act_id])
    return why


def scan_all(db, riders=None):
    """Check every ride (new limits, old data). Returns the number flagged. Safe to repeat. Commits."""
    n = 0
    for (rid,) in db.execute('SELECT id FROM Activity WHERE flagDismissed IS NULL OR flagDismissed = 0').fetchall():
        if flag(db, rid):
            n += 1
    db.commit()
    return n


def dismiss(db, act_id):
    """"It is right": clear the flag for good. Does not commit."""
    db.execute('UPDATE Activity SET flagDismissed=1, flagReason=NULL WHERE id=?', [act_id])


def flagged(db, rider_id):
    return [dict(r) for r in db.execute(
        'SELECT id, name, startDateLocal, distance, averageSpeed, flagReason FROM Activity WHERE riderId=? AND flagReason IS NOT NULL AND COALESCE(flagDismissed,0)=0 ORDER BY startDateLocal DESC', [rider_id]).fetchall()]
