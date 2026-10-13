"""One steps number per day, from ONE source: Garmin, or the phone's own sensors. Never a mix.

The old rule took the highest of every source, which is wrong for anyone who rides: a phone counts pedalling as steps (a 40-mile ride added
~15,000), so the phone's number won on every ride day. Now the install picks a source (Settings -> Steps) and that source is the day's number.

* `Settings.stepsSource`: 'garmin' | 'phone' | NULL/'auto'. Auto = Garmin when it has been giving steps lately, otherwise the phone.
* A number someone TYPED for a day (`steps_manual`) is an explicit correction and wins for that day.
* If the chosen source has nothing for a day the answer is 0 (unknown), never a number borrowed from the other source.
"""
from database import query_db

PHONE_METRICS = ('steps_phone', 'steps_watch')           # the phone, or a watch that feeds Home Assistant: first one with a value
AUTO_DAYS = 14
AUTO_MIN = 5                                             # Garmin counts as "feeding steps" with data on this many of the last 14 days


def configured():
    r = query_db('SELECT stepsSource FROM Settings WHERE id=1', one=True)
    v = (r['stepsSource'] if r and r['stepsSource'] else 'auto').lower()
    return v if v in ('garmin', 'phone', 'auto') else 'auto'


def effective_source():
    """'garmin' or 'phone': the configured source, with 'auto' resolved."""
    c = configured()
    if c != 'auto':
        return c
    n = query_db("SELECT COUNT(*) n FROM GarminDaily WHERE steps > 0 AND date >= date('now', ?)", [f'-{AUTO_DAYS} days'], one=True)['n']
    return 'garmin' if n >= AUTO_MIN else 'phone'


def _metric(rid, day_iso, metric):
    m = query_db('SELECT value FROM BodyMetric WHERE riderId=? AND logDate=? AND metric=?', [rid, day_iso, metric], one=True)
    return int(m['value']) if m and m['value'] else None


def steps_for(rid, day_iso, garmin_steps=None, source=None):
    """(steps, label) for a day. label is 'Manual', 'Garmin' or 'Phone', or None when there is nothing. Never combines sources."""
    manual = _metric(rid, day_iso, 'steps_manual')
    if manual:
        return manual, 'Manual'
    src = source or effective_source()
    if src == 'garmin':
        if garmin_steps is None:
            g = query_db('SELECT steps FROM GarminDaily WHERE date=?', [day_iso], one=True)
            garmin_steps = g['steps'] if g else None
        return (int(garmin_steps), 'Garmin') if garmin_steps else (0, None)
    for metric in PHONE_METRICS:
        v = _metric(rid, day_iso, metric)
        if v:
            return v, 'Phone'
    return 0, None


def steps_value(rid, day_iso, garmin_steps=None):
    return steps_for(rid, day_iso, garmin_steps)[0]
