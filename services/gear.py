"""Gear: bikes, which bike rode which ride, the parts on each bike and how worn they are.

Design rules:
* Mileage is COMPUTED, never stored. A bike's odometer = its starting mileage + the distance of the rides assigned to it; a part's mileage = what it had when
  fitted + the rides on that bike since the day it was fitted. Re-assigning a ride (calendar, ride page) therefore corrects every figure automatically.
* A new ride is stamped with the rider's default bike at that moment, so changing the default later never rewrites history.
* Alerts are remembered per part/level/cycle, so each one fires once and resets when the part is checked or replaced.
* Everything is plain sqlite + Pillow (no new dependencies), and no strftime('%-d'): this module must run unchanged inside the Windows .exe.
"""
import io
import logging
import os
import re
import secrets
from datetime import date, datetime, timedelta

from services.gear_catalog import BY_KIND

log = logging.getLogger(__name__)
MI = 1609.344
RIDE_SQL = "(lower(a.sportType) LIKE '%ride%' OR lower(a.sportType) LIKE '%cycl%')"
BIKE_KINDS = [('road', 'Road'), ('gravel', 'Gravel'), ('mtb', 'Mountain'), ('commuter', 'Commuter / hybrid'), ('tt', 'Time trial / triathlon'),
              ('track', 'Track / fixed'), ('ebike', 'E-bike'), ('turbo', 'Indoor / turbo'), ('other', 'Other')]
ACTIONS = [('replaced', 'Replaced'), ('serviced', 'Serviced'), ('inspected', 'Inspected / measured'), ('cleaned', 'Cleaned / lubed'),
           ('adjusted', 'Adjusted'), ('topped_up', 'Topped up'), ('other', 'Other')]
CHECK_ACTIONS = {a for a, _ in ACTIONS if a != 'replaced'}          # anything except a replacement counts as "looked after it"
SOON, DUE, OVERDUE = 0.9, 1.0, 1.25
LEVEL_ORDER = {'ok': 0, 'soon': 1, 'due': 2, 'overdue': 3}


def ensure_schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS Bike (
        id INTEGER PRIMARY KEY AUTOINCREMENT, riderId INTEGER NOT NULL, name TEXT NOT NULL, kind TEXT DEFAULT 'road', brand TEXT, model TEXT, year INTEGER,
        photo TEXT, boughtOn TEXT, startMeters REAL DEFAULT 0, notes TEXT, archived INTEGER DEFAULT 0, createdAt TEXT DEFAULT (datetime('now')))''')
    db.execute('CREATE INDEX IF NOT EXISTS idx_bike_rider ON Bike(riderId)')
    db.execute('''CREATE TABLE IF NOT EXISTS Part (
        id INTEGER PRIMARY KEY AUTOINCREMENT, bikeId INTEGER NOT NULL, kind TEXT NOT NULL, name TEXT NOT NULL, installedOn TEXT NOT NULL, retiredOn TEXT,
        initialMeters REAL DEFAULT 0, checkEveryM REAL, replaceEveryM REAL, checkEveryDays INTEGER, replaceEveryDays INTEGER, notify INTEGER DEFAULT 1,
        cost REAL, notes TEXT, createdAt TEXT DEFAULT (datetime('now')))''')
    db.execute('CREATE INDEX IF NOT EXISTS idx_part_bike ON Part(bikeId)')
    db.execute('''CREATE TABLE IF NOT EXISTS ServiceLog (
        id INTEGER PRIMARY KEY AUTOINCREMENT, bikeId INTEGER NOT NULL, partId INTEGER, date TEXT NOT NULL, action TEXT NOT NULL, odometerM REAL, cost REAL,
        notes TEXT, createdAt TEXT DEFAULT (datetime('now')))''')
    db.execute('CREATE INDEX IF NOT EXISTS idx_svc_bike ON ServiceLog(bikeId, date)')
    db.execute('''CREATE TABLE IF NOT EXISTS PartAlert (
        partId INTEGER NOT NULL, what TEXT NOT NULL, level TEXT NOT NULL, cycle TEXT NOT NULL, notifiedAt TEXT DEFAULT (datetime('now')),
        PRIMARY KEY (partId, what, level, cycle))''')
    act = {r[1] for r in db.execute('PRAGMA table_info(Activity)').fetchall()}
    if 'bikeId' not in act:
        db.execute('ALTER TABLE Activity ADD COLUMN bikeId INTEGER')
    db.execute('CREATE INDEX IF NOT EXISTS idx_activity_bike ON Activity(bikeId)')
    rider = {r[1] for r in db.execute('PRAGMA table_info(Rider)').fetchall()}
    if 'defaultBikeId' not in rider:
        db.execute('ALTER TABLE Rider ADD COLUMN defaultBikeId INTEGER')


# ---------- small helpers ----------
def today():
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo('Europe/London')).date()


def parse_date(s):
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError:
        raise ValueError('that is not a valid date')


def to_meters(value, units):
    """A distance typed by the user in their display units (miles or km) -> metres."""
    return float(value) * (1000.0 if units == 'metric' else MI)


def _num(v, name, lo=0, hi=None):
    if v in (None, ''):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise ValueError(f'{name} must be a number')
    if x < lo or (hi is not None and x > hi):
        raise ValueError(f'{name} is out of range')
    return x


def _clean(s, n=80):
    s = (s or '').strip()
    return s[:n] or None


# ---------- bikes ----------
def create_bike(db, rider_id, name, kind='road', brand=None, model=None, year=None, bought_on=None, start_meters=0, notes=None):
    name = _clean(name)
    if not name:
        raise ValueError('give the bike a name')
    if kind not in {k for k, _ in BIKE_KINDS}:
        kind = 'other'
    yr = int(_num(year, 'year', 1900, 2100)) if year not in (None, '') else None
    if bought_on:
        parse_date(bought_on)
    cur = db.execute('INSERT INTO Bike (riderId, name, kind, brand, model, year, boughtOn, startMeters, notes) VALUES (?,?,?,?,?,?,?,?,?)',
                     [rider_id, name, kind, _clean(brand), _clean(model), yr, (bought_on or None), _num(start_meters, 'starting mileage') or 0, _clean(notes, 1000)])
    bid = cur.lastrowid
    if not db.execute('SELECT defaultBikeId FROM Rider WHERE id=?', [rider_id]).fetchone()[0]:      # the first bike becomes the default
        db.execute('UPDATE Rider SET defaultBikeId=? WHERE id=?', [bid, rider_id])
    return bid


def _req_name(v):
    v = _clean(v)
    if not v:
        raise ValueError('give the bike a name')
    return v


def update_bike(db, bike_id, **f):
    cols = {'name': _req_name, 'kind': lambda v: v if v in {k for k, _ in BIKE_KINDS} else 'other',
            'brand': _clean, 'model': _clean, 'year': lambda v: int(_num(v, 'year', 1900, 2100)) if v not in (None, '') else None,
            'boughtOn': lambda v: (parse_date(v).isoformat() if v else None), 'startMeters': lambda v: _num(v, 'starting mileage') or 0, 'notes': lambda v: _clean(v, 1000)}
    sets, args = [], []
    for k, v in f.items():
        if k in cols:
            sets.append(f'{k}=?'); args.append(cols[k](v))
    if sets:
        db.execute(f"UPDATE Bike SET {', '.join(sets)} WHERE id=?", args + [bike_id])


def get_bike(db, bike_id):
    return db.execute('SELECT * FROM Bike WHERE id=?', [bike_id]).fetchone()


def list_bikes(db, rider_id, include_archived=False):
    q = 'SELECT * FROM Bike WHERE riderId=?' + ('' if include_archived else ' AND archived=0') + ' ORDER BY archived, name COLLATE NOCASE'
    return db.execute(q, [rider_id]).fetchall()


def set_default(db, rider_id, bike_id):
    if bike_id is not None:
        b = get_bike(db, bike_id)
        if not b or b['riderId'] != rider_id or b['archived']:
            raise ValueError('that bike is not available')
    db.execute('UPDATE Rider SET defaultBikeId=? WHERE id=?', [bike_id, rider_id])


def archive_bike(db, bike_id, archived=True):
    b = get_bike(db, bike_id)
    db.execute('UPDATE Bike SET archived=? WHERE id=?', [1 if archived else 0, bike_id])
    if b and archived:
        db.execute('UPDATE Rider SET defaultBikeId=NULL WHERE id=? AND defaultBikeId=?', [b['riderId'], bike_id])


def delete_bike(db, bike_id):
    """Removes the bike, its parts and its service log. Rides that were on it simply become unassigned."""
    b = get_bike(db, bike_id)
    if not b:
        return
    db.execute('UPDATE Activity SET bikeId=NULL WHERE bikeId=?', [bike_id])
    db.execute('DELETE FROM PartAlert WHERE partId IN (SELECT id FROM Part WHERE bikeId=?)', [bike_id])
    db.execute('DELETE FROM Part WHERE bikeId=?', [bike_id])
    db.execute('DELETE FROM ServiceLog WHERE bikeId=?', [bike_id])
    db.execute('UPDATE Rider SET defaultBikeId=NULL WHERE defaultBikeId=?', [bike_id])
    db.execute('DELETE FROM Bike WHERE id=?', [bike_id])


def distance_on(db, bike_id, since=None, until=None):
    """Metres ridden on a bike (optionally from `since` to `until`, inclusive local dates)."""
    q, a = 'SELECT COALESCE(SUM(distance),0) FROM Activity WHERE bikeId=?', [bike_id]
    if since:
        q += ' AND substr(startDateLocal,1,10) >= ?'; a.append(str(since)[:10])
    if until:
        q += ' AND substr(startDateLocal,1,10) <= ?'; a.append(str(until)[:10])
    return db.execute(q, a).fetchone()[0] or 0.0


def odometer(db, bike):
    return (bike['startMeters'] or 0) + distance_on(db, bike['id'])


def bike_stats(db, bike_id):
    r = db.execute('''SELECT COUNT(*) n, COALESCE(SUM(distance),0) dist, COALESCE(SUM(movingTime),0) moving, COALESCE(SUM(totalElevationGain),0) gain,
                             MAX(distance) longest, MIN(substr(startDateLocal,1,10)) first, MAX(substr(startDateLocal,1,10)) last
                      FROM Activity WHERE bikeId=?''', [bike_id]).fetchone()
    years = db.execute('''SELECT substr(startDateLocal,1,4) y, COUNT(*) n, SUM(distance) d, SUM(totalElevationGain) g
                          FROM Activity WHERE bikeId=? GROUP BY y ORDER BY y''', [bike_id]).fetchall()
    return {'rides': r['n'], 'distance': r['dist'], 'moving': r['moving'], 'gain': r['gain'], 'longest': r['longest'], 'first': r['first'], 'last': r['last'],
            'avg_speed': (r['dist'] / r['moving']) if r['moving'] else None, 'by_year': [{'year': y['y'], 'rides': y['n'], 'distance': y['d'] or 0, 'gain': y['g'] or 0} for y in years]}


# ---------- which bike rode which ride ----------
def is_ride_row(row):
    s = (row['sportType'] or '').lower()
    return 'ride' in s or 'cycl' in s


def set_ride_bike(db, act_id, bike_id):
    """Set (or clear, with None) the bike on one ride. The bike must belong to the ride's rider."""
    a = db.execute('SELECT id, riderId, sportType FROM Activity WHERE id=?', [act_id]).fetchone()
    if not a:
        raise ValueError('ride not found')
    if bike_id is not None:
        b = get_bike(db, bike_id)
        if not b or b['riderId'] != a['riderId']:
            raise ValueError('that bike belongs to a different rider')
    db.execute('UPDATE Activity SET bikeId=? WHERE id=?', [bike_id, act_id])
    return True


def stamp_new_ride(db, act_id):
    """Called when a ride is added by any route: give it the rider's default bike, if they have one and the ride has none."""
    a = db.execute('SELECT id, riderId, sportType, bikeId FROM Activity WHERE id=?', [act_id]).fetchone()
    if not a or a['bikeId'] or not a['riderId'] or not is_ride_row(a):
        return None
    d = db.execute('SELECT defaultBikeId FROM Rider WHERE id=?', [a['riderId']]).fetchone()
    if d and d[0] and not (get_bike(db, d[0]) or {'archived': 1})['archived']:
        db.execute('UPDATE Activity SET bikeId=? WHERE id=?', [d[0], act_id])
        return d[0]
    return None


def _selection_sql(dates, date_from, date_to):
    parts, args = [], []
    if dates:
        ds = sorted({str(d)[:10] for d in dates})
        for d in ds:
            parse_date(d)
        parts.append('substr(a.startDateLocal,1,10) IN (' + ','.join('?' * len(ds)) + ')'); args += ds
    if date_from or date_to:
        lo, hi = (parse_date(date_from).isoformat() if date_from else '0000-01-01'), (parse_date(date_to).isoformat() if date_to else '9999-12-31')
        parts.append('(substr(a.startDateLocal,1,10) BETWEEN ? AND ?)'); args += [lo, hi]
    if not parts:
        raise ValueError('pick at least one date or a range')
    return '(' + ' OR '.join(parts) + ')', args


def assign_rides(db, rider_id, bike_id, dates=None, date_from=None, date_to=None, only_unassigned=True, dry_run=False):
    """Put a bike (or None to clear) on every ride of this rider on the picked dates and/or inside a date range.
    only_unassigned leaves rides that already have a bike alone. dry_run reports what WOULD change. Returns counts."""
    if bike_id is not None:
        b = get_bike(db, bike_id)
        if not b or b['riderId'] != rider_id:
            raise ValueError('that bike is not available')
    sel, args = _selection_sql(dates, date_from, date_to)
    base = f'FROM Activity a WHERE a.riderId=? AND {RIDE_SQL} AND {sel}'
    matched = db.execute(f'SELECT COUNT(*) {base}', [rider_id] + args).fetchone()[0]
    skip = ' AND a.bikeId IS NULL' if only_unassigned and bike_id is not None else ''
    already = ' AND (a.bikeId IS NOT ?)'
    changing = db.execute(f'SELECT COUNT(*) {base}{skip}{already}', [rider_id] + args + [bike_id]).fetchone()[0]
    prev = db.execute(f'SELECT a.bikeId, COUNT(*) FROM Activity a WHERE a.riderId=? AND {RIDE_SQL} AND {sel}{skip}{already} GROUP BY a.bikeId', [rider_id] + args + [bike_id]).fetchall()
    out = {'matched': matched, 'changed': changing, 'previous': {str(r[0]) if r[0] is not None else 'none': r[1] for r in prev}}
    if not dry_run and changing:
        db.execute(f'UPDATE Activity SET bikeId=? WHERE id IN (SELECT a.id {base}{skip}{already})', [bike_id, rider_id] + args + [bike_id])
    return out


def calendar_month(db, rider_id, year, month):
    """{ 'YYYY-MM-DD': {'n': rides, 'unassigned': n, 'bikes': {bikeId: n}} } for one month (rides only)."""
    first = date(year, month, 1)
    last = (date(year + (month == 12), (month % 12) + 1, 1) - timedelta(days=1))
    rows = db.execute(f'''SELECT substr(a.startDateLocal,1,10) d, a.bikeId b, COUNT(*) n FROM Activity a
                          WHERE a.riderId=? AND {RIDE_SQL} AND substr(a.startDateLocal,1,10) BETWEEN ? AND ? GROUP BY d, b''', [rider_id, first.isoformat(), last.isoformat()]).fetchall()
    out = {}
    for r in rows:
        d = out.setdefault(r['d'], {'n': 0, 'unassigned': 0, 'bikes': {}})
        d['n'] += r['n']
        if r['b'] is None:
            d['unassigned'] += r['n']
        else:
            d['bikes'][str(r['b'])] = d['bikes'].get(str(r['b']), 0) + r['n']
    return out


def ride_span(db, rider_id):
    """First / last ride date, how many rides have no bike, the oldest such ride, and the total, so the calendar can open somewhere useful."""
    r = db.execute(f"SELECT MIN(substr(a.startDateLocal,1,10)), MAX(substr(a.startDateLocal,1,10)), SUM(a.bikeId IS NULL), COUNT(*) FROM Activity a WHERE a.riderId=? AND {RIDE_SQL}", [rider_id]).fetchone()
    fu = db.execute(f"SELECT MIN(substr(a.startDateLocal,1,10)) FROM Activity a WHERE a.riderId=? AND {RIDE_SQL} AND a.bikeId IS NULL", [rider_id]).fetchone()[0]
    return {'first': r[0], 'last': r[1], 'unassigned': r[2] or 0, 'total': r[3] or 0, 'first_unassigned': fu}


def on_ride_added(db, act_id):
    """One call for every place a ride can arrive (Garmin sync, file / zip import, phone upload, typed in): give it the default bike, then see whether
    that pushed any part over a threshold. Never raises: gear must not be able to break an import. Does not commit."""
    try:
        from services import sensors            # a waiting phone-sensor sidecar that overlaps this ride attaches to it, whichever arrived first
        sensors.attach_for_ride(db, act_id)
    except Exception as e:
        log.warning('sensor attach for ride %s failed (non-fatal): %s', act_id, e)
    try:
        stamp_new_ride(db, act_id)
        a = db.execute('SELECT bikeId, riderId FROM Activity WHERE id=?', [act_id]).fetchone()
        if a and a['bikeId']:
            evaluate_alerts(db, rider_id=a['riderId'])
    except Exception as e:
        log.warning('gear update after ride %s failed (non-fatal): %s', act_id, e)


# ---------- parts ----------
def add_part(db, bike_id, kind, name=None, installed_on=None, initial_meters=0, check_m=None, replace_m=None, check_days=None, replace_days=None, notify=True, cost=None, notes=None):
    b = get_bike(db, bike_id)
    if not b:
        raise ValueError('bike not found')
    cat = BY_KIND.get(kind) or BY_KIND['custom']
    nm = _clean(name) or cat['label']
    inst = parse_date(installed_on).isoformat() if installed_on else today().isoformat()
    cur = db.execute('''INSERT INTO Part (bikeId, kind, name, installedOn, initialMeters, checkEveryM, replaceEveryM, checkEveryDays, replaceEveryDays, notify, cost, notes)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
                     [bike_id, kind if kind in BY_KIND else 'custom', nm, inst, _num(initial_meters, 'starting mileage') or 0, _num(check_m, 'check interval'), _num(replace_m, 'replace interval'),
                      int(_num(check_days, 'check days')) if check_days not in (None, '') else None, int(_num(replace_days, 'replace days')) if replace_days not in (None, '') else None,
                      1 if notify else 0, _num(cost, 'cost'), _clean(notes, 500)])
    return cur.lastrowid


def update_part(db, part_id, **f):
    allowed = {'name': _clean, 'installedOn': lambda v: parse_date(v).isoformat(), 'initialMeters': lambda v: _num(v, 'starting mileage') or 0,
               'checkEveryM': lambda v: _num(v, 'check interval'), 'replaceEveryM': lambda v: _num(v, 'replace interval'),
               'checkEveryDays': lambda v: int(_num(v, 'days')) if v not in (None, '') else None, 'replaceEveryDays': lambda v: int(_num(v, 'days')) if v not in (None, '') else None,
               'notify': lambda v: 1 if v else 0, 'cost': lambda v: _num(v, 'cost'), 'notes': lambda v: _clean(v, 500)}
    sets, args = [], []
    for k, v in f.items():
        if k in allowed:
            sets.append(f'{k}=?'); args.append(allowed[k](v))
    if sets:
        db.execute(f"UPDATE Part SET {', '.join(sets)} WHERE id=?", args + [part_id])


def part_rows(db, bike_id, include_retired=False):
    q = 'SELECT * FROM Part WHERE bikeId=?' + ('' if include_retired else ' AND retiredOn IS NULL') + ' ORDER BY retiredOn IS NOT NULL, kind, installedOn'
    return db.execute(q, [bike_id]).fetchall()


def _last_check_date(db, part):
    r = db.execute("SELECT MAX(date) FROM ServiceLog WHERE partId=? AND action<>'replaced'", [part['id']]).fetchone()[0]
    return max(r, part['installedOn']) if r else part['installedOn']


def _level(ratio):
    return 'overdue' if ratio >= OVERDUE else 'due' if ratio >= DUE else 'soon' if ratio >= SOON else 'ok'


def part_status(db, part, on=None):
    """How worn / how overdue a part is. Distance and time intervals are both honoured (whichever is further along wins)."""
    on = on or today()
    end = part['retiredOn'] or on.isoformat()
    installed = part['installedOn']
    since_install = (part['initialMeters'] or 0) + distance_on(db, part['bikeId'], since=installed, until=end)
    last_check = _last_check_date(db, part)
    checked = db.execute("SELECT 1 FROM ServiceLog WHERE partId=? AND action<>'replaced' LIMIT 1", [part['id']]).fetchone()
    since_check = distance_on(db, part['bikeId'], since=last_check, until=end) + (0 if checked else (part['initialMeters'] or 0))
    d_install = (date.fromisoformat(end[:10]) - date.fromisoformat(installed)).days
    d_check = (date.fromisoformat(end[:10]) - date.fromisoformat(last_check)).days

    def ratio(dist, days, every_m, every_d):
        rs = []
        if every_m:
            rs.append(dist / every_m)
        if every_d:
            rs.append(days / every_d)
        return max(rs) if rs else None

    rc = ratio(since_check, d_check, part['checkEveryM'], part['checkEveryDays'])
    rr = ratio(since_install, d_install, part['replaceEveryM'], part['replaceEveryDays'])
    check = {'ratio': rc, 'level': _level(rc) if rc is not None else 'ok'}
    replace = {'ratio': rr, 'level': _level(rr) if rr is not None else 'ok'}
    worst = max((check['level'], replace['level']), key=lambda x: LEVEL_ORDER[x])
    due = 'replace' if LEVEL_ORDER[replace['level']] >= LEVEL_ORDER[check['level']] and replace['level'] != 'ok' else ('check' if check['level'] != 'ok' else None)
    return {'meters': since_install, 'meters_since_check': since_check, 'days': d_install, 'days_since_check': d_check, 'last_check': last_check,
            'check': check, 'replace': replace, 'level': worst, 'due': due, 'retired': bool(part['retiredOn'])}


def bike_health(db, bike_id):
    """Worst part level on a bike (for the card badge) and how many parts need attention."""
    levels = [part_status(db, p)['level'] for p in part_rows(db, bike_id)]
    return {'level': max(levels, key=lambda x: LEVEL_ORDER[x]) if levels else 'ok', 'attention': sum(1 for l in levels if l in ('due', 'overdue')), 'soon': sum(1 for l in levels if l == 'soon')}


# ---------- service log ----------
def log_service(db, bike_id, action, on=None, part_id=None, cost=None, notes=None, new_part=None):
    """Record work done. action 'replaced' retires `part_id` on that date and (unless new_part is False) fits a fresh one with the same intervals.
    new_part = optional dict(name=..., cost=..., initial_meters=...) to describe the replacement. Returns {'log': id, 'new_part': id|None}."""
    if action not in {a for a, _ in ACTIONS}:
        raise ValueError('pick what was done')
    when = parse_date(on).isoformat() if on else today().isoformat()
    if parse_date(when) > today() + timedelta(days=1):
        raise ValueError('that date is in the future')
    b = get_bike(db, bike_id)
    if not b:
        raise ValueError('bike not found')
    part = None
    if part_id:
        part = db.execute('SELECT * FROM Part WHERE id=? AND bikeId=?', [part_id, bike_id]).fetchone()
        if not part:
            raise ValueError('that part is not on this bike')
    odo = (b['startMeters'] or 0) + distance_on(db, bike_id, until=when)
    cur = db.execute('INSERT INTO ServiceLog (bikeId, partId, date, action, odometerM, cost, notes) VALUES (?,?,?,?,?,?,?)', [bike_id, part_id, when, action, odo, _num(cost, 'cost'), _clean(notes, 500)])
    new_id = None
    if action == 'replaced' and part:
        db.execute('UPDATE Part SET retiredOn=? WHERE id=?', [when, part['id']])
        if new_part is not False:
            np_ = new_part or {}
            new_id = add_part(db, bike_id, part['kind'], name=np_.get('name') or part['name'], installed_on=when, initial_meters=np_.get('initial_meters') or 0, check_m=part['checkEveryM'],
                              replace_m=part['replaceEveryM'], check_days=part['checkEveryDays'], replace_days=part['replaceEveryDays'], notify=bool(part['notify']), cost=np_.get('cost'))
    return {'log': cur.lastrowid, 'new_part': new_id}


def delete_log(db, log_id):
    db.execute('DELETE FROM ServiceLog WHERE id=?', [log_id])


def service_log(db, bike_id, limit=200):
    return db.execute('''SELECT s.*, p.name AS partName FROM ServiceLog s LEFT JOIN Part p ON p.id=s.partId WHERE s.bikeId=? ORDER BY s.date DESC, s.id DESC LIMIT ?''', [bike_id, limit]).fetchall()


# ---------- alerts ----------
def _headline(bike, part, st, what, level):
    unit_m = lambda m: f'{m / MI:,.0f} mi'
    iv_m = part['replaceEveryM'] if what == 'replace' else part['checkEveryM']
    iv_d = part['replaceEveryDays'] if what == 'replace' else part['checkEveryDays']
    done = st['meters'] if what == 'replace' else st['meters_since_check']
    days = st['days'] if what == 'replace' else st['days_since_check']
    bits = []
    if iv_m:
        bits.append(f'{unit_m(done)} of {unit_m(iv_m)}')
    if iv_d:
        bits.append(f'{days} of {iv_d} days')
    verb = {'check': 'check', 'replace': 'replacement'}[what]
    state = {'soon': 'is nearly due for', 'due': 'is due for', 'overdue': 'is overdue for'}[level]
    return f"{part['name']} on {bike['name']} {state} {verb} ({', '.join(bits)})"


def evaluate_alerts(db, rider_id=None, send=True):
    """Finds parts that have newly crossed a threshold, remembers them (so each fires once per cycle) and optionally notifies.
    Returns the list of new alerts [{part, bike, what, level, text}]. Does not commit."""
    q = '''SELECT p.*, b.name AS bikeName, b.riderId AS riderId FROM Part p JOIN Bike b ON b.id=p.bikeId
           WHERE p.retiredOn IS NULL AND p.notify=1 AND b.archived=0'''
    args = []
    if rider_id:
        q += ' AND b.riderId=?'; args.append(rider_id)
    new = []
    for p in db.execute(q, args).fetchall():
        st = part_status(db, p)
        for what in ('check', 'replace'):
            lvl = st[what]['level']
            if lvl == 'ok':
                continue
            cycle = st['last_check'] if what == 'check' else p['installedOn']
            have = {r[0] for r in db.execute('SELECT level FROM PartAlert WHERE partId=? AND what=? AND cycle=?', [p['id'], what, cycle])}
            if lvl in have:
                continue
            for l in LEVEL_ORDER:                       # mark this and every lower level as announced; only the current one is sent
                if l != 'ok' and LEVEL_ORDER[l] <= LEVEL_ORDER[lvl]:
                    db.execute('INSERT OR IGNORE INTO PartAlert (partId, what, level, cycle) VALUES (?,?,?,?)', [p['id'], what, l, cycle])
            bike = {'name': p['bikeName']}
            new.append({'part': p['id'], 'bikeId': p['bikeId'], 'riderId': p['riderId'], 'what': what, 'level': lvl, 'text': _headline(bike, p, st, what, lvl)})
    if send and new:
        _notify(new)
    return new


def _notify(alerts):
    try:
        from services.notify import send_notification
        title = 'Gear: ' + (f'{len(alerts)} things need attention' if len(alerts) > 1 else 'something needs attention')
        send_notification(title, '\n'.join(a['text'] for a in alerts))
    except Exception as e:
        log.warning('gear notification failed (non-fatal): %s', e)
    try:
        from services.mqtt import push_gear_alert
        push_gear_alert(alerts)
    except Exception as e:
        log.warning('gear MQTT push failed (non-fatal): %s', e)


def due_items(db, rider_id, include_soon=True):
    """Every active part of the rider's bikes that needs attention, worst first (for the Gear page and the dashboard card)."""
    out = []
    for b in list_bikes(db, rider_id):
        for p in part_rows(db, b['id']):
            st = part_status(db, p)
            if st['level'] == 'ok' or (st['level'] == 'soon' and not include_soon):
                continue
            out.append({'bike': b, 'part': p, 'status': st})
    out.sort(key=lambda x: -LEVEL_ORDER[x['status']['level']])
    return out


# ---------- photos ----------
_PHOTO_RE = re.compile(r'^bike_\d+_[a-f0-9]{8}\.jpg$')
MAX_PHOTO_BYTES = 12 * 1024 * 1024


def photo_dir(db_path):
    d = os.path.join(os.path.dirname(os.path.abspath(db_path)), 'bikeimg')
    os.makedirs(d, exist_ok=True)
    return d


def valid_photo_name(name):
    return bool(_PHOTO_RE.match(name or ''))


def save_photo(db, bike_id, data, db_path):
    """Store an uploaded picture (any common format) as a resized JPEG; returns the new filename. The old picture is removed."""
    from PIL import Image
    if len(data) > MAX_PHOTO_BYTES:
        raise ValueError('that picture is too large (limit 12 MB)')
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception:
        raise ValueError('that does not look like a picture')
    img = img.convert('RGB')                                     # also drops EXIF (location etc.)
    img.thumbnail((1400, 1400), Image.LANCZOS)
    name = f'bike_{bike_id}_{secrets.token_hex(4)}.jpg'
    d = photo_dir(db_path)
    img.save(os.path.join(d, name), 'JPEG', quality=85, optimize=True)
    old = db.execute('SELECT photo FROM Bike WHERE id=?', [bike_id]).fetchone()
    db.execute('UPDATE Bike SET photo=? WHERE id=?', [name, bike_id])
    if old and old[0] and valid_photo_name(old[0]):
        try:
            os.remove(os.path.join(d, old[0]))
        except OSError:
            pass
    return name


def remove_photo(db, bike_id, db_path):
    old = db.execute('SELECT photo FROM Bike WHERE id=?', [bike_id]).fetchone()
    db.execute('UPDATE Bike SET photo=NULL WHERE id=?', [bike_id])
    if old and old[0] and valid_photo_name(old[0]):
        try:
            os.remove(os.path.join(photo_dir(db_path), old[0]))
        except OSError:
            pass
