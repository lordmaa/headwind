"""Ride photos and mechanical faults.

* A photo is stored as a resized JPEG (plus a thumbnail) in `rideimg/` beside the database. Its location comes from the picture's own GPS tag when it has one; phones'
  file pickers often strip that, so otherwise the time it was taken is matched to the ride's track. Either way the saved file carries no EXIF.
* A fault is a note on one ride ("chain snapped, rode home") that can carry photos. When the ride has a bike it is also written to that bike's service log, so Gear shows
  it, and the AI ride summary and the Faults statistics page both read from the same table. Editing or deleting the fault updates the log entry with it.
No new dependencies (sqlite + Pillow), and no strftime('%-d') so it runs inside the Windows .exe.
"""
import io
import json
import os
import re
import secrets
import logging
from datetime import datetime

log = logging.getLogger(__name__)

CATEGORIES = [
    ('puncture', 'Puncture / flat'), ('tyre', 'Tyre damage / blow-out'), ('chain', 'Chain (snapped, dropped, skipping)'), ('gears', 'Gears / derailleur / shifter'),
    ('brakes', 'Brakes'), ('wheel', 'Wheel, spokes or hub'), ('pedals', 'Pedals / cleats'), ('crank', 'Crank / bottom bracket'), ('frame', 'Frame / fork'),
    ('contact', 'Bars, stem, saddle or seatpost'), ('electrics', 'Electrics / e-bike / computer / lights'), ('crash', 'Crash damage'), ('other', 'Something else'),
]
OUTCOMES = [('fixed', 'Fixed it and carried on'), ('limped', 'Carried on, but limping'), ('ended', 'Ride ended early (walked, rescued or got a lift)')]
SEASONS = ['Winter', 'Spring', 'Summer', 'Autumn']
_CAT = dict(CATEGORIES)
_OUT = dict(OUTCOMES)
_NAME_RE = re.compile(r'^rp_[a-f0-9]{12}(?:_t)?\.jpg$')
MAX_PHOTO_BYTES = 20 * 1024 * 1024
MAX_PHOTOS_PER_RIDE = 60


def ensure_schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS RideFault (
        id INTEGER PRIMARY KEY AUTOINCREMENT, rideId TEXT NOT NULL, riderId INTEGER, bikeId INTEGER, partId INTEGER, category TEXT NOT NULL DEFAULT 'other',
        outcome TEXT, minutesLost INTEGER, description TEXT, lat REAL, lng REAL, date TEXT, logId INTEGER, createdAt TEXT DEFAULT (datetime('now')))''')
    if 'repairedOn' not in {r[1] for r in db.execute('PRAGMA table_info(RideFault)').fetchall()}:
        db.execute('ALTER TABLE RideFault ADD COLUMN repairedOn TEXT')
    if 'clientId' not in {r[1] for r in db.execute('PRAGMA table_info(RideFault)').fetchall()}:
        db.execute('ALTER TABLE RideFault ADD COLUMN clientId TEXT')       # the phone's id for it, so a retried upload does not create a second one
    if 'remindedCount' not in {r[1] for r in db.execute('PRAGMA table_info(RideFault)').fetchall()}:
        db.execute('ALTER TABLE RideFault ADD COLUMN remindedCount INTEGER DEFAULT 0')    # nudges sent about it while it stayed unrepaired
    db.execute('CREATE INDEX IF NOT EXISTS idx_fault_ride ON RideFault(rideId)')
    db.execute('CREATE INDEX IF NOT EXISTS idx_fault_rider ON RideFault(riderId, date)')
    db.execute('''CREATE TABLE IF NOT EXISTS RidePhoto (
        id INTEGER PRIMARY KEY AUTOINCREMENT, rideId TEXT NOT NULL, faultId INTEGER, file TEXT NOT NULL, lat REAL, lng REAL, takenAt TEXT, placedBy TEXT,
        caption TEXT, createdAt TEXT DEFAULT (datetime('now')))''')
    if 'clientId' not in {r[1] for r in db.execute('PRAGMA table_info(RidePhoto)').fetchall()}:
        db.execute('ALTER TABLE RidePhoto ADD COLUMN clientId TEXT')
    db.execute('CREATE INDEX IF NOT EXISTS idx_rphoto_ride ON RidePhoto(rideId)')


# ---------- photos ----------
def photo_dir(db_path):
    d = os.path.join(os.path.dirname(os.path.abspath(db_path)), 'rideimg')
    os.makedirs(d, exist_ok=True)
    return d


def valid_name(name):
    return bool(_NAME_RE.match(name or ''))


def _dms(vals, ref):
    try:
        d, m, s = (float(x) for x in vals)
    except (TypeError, ValueError):
        return None
    v = d + m / 60.0 + s / 3600.0
    return -v if str(ref).upper() in ('S', 'W') else v


def read_exif(img):
    """(lat, lng, taken 'YYYY-MM-DDTHH:MM:SS' or None) from a PIL image; any part may be missing."""
    lat = lng = taken = None
    try:
        ex = img.getexif()
        gps = ex.get_ifd(0x8825) if ex else {}
        if gps and 2 in gps and 4 in gps:
            lat, lng = _dms(gps.get(2), gps.get(1)), _dms(gps.get(4), gps.get(3))
            if lat is None or lng is None or (abs(lat) < 1e-6 and abs(lng) < 1e-6) or abs(lat) > 90 or abs(lng) > 180:
                lat = lng = None
        raw = (ex.get_ifd(0x8769) or {}).get(36867) or ex.get(306)
        if raw:
            taken = datetime.strptime(str(raw).strip()[:19], '%Y:%m:%d %H:%M:%S').isoformat()
    except Exception:
        pass
    return lat, lng, taken


def _ride_track(db, rid):
    """(startLocal datetime, elapsed seconds, [[lat, lng]...], time stream or None) for a ride or a phone workout, or None."""
    row = db.execute('SELECT startDateLocal, elapsedTime, movingTime, streams, summaryPolyline FROM Activity WHERE id=?', [rid]).fetchone()
    if not row:
        row = db.execute('SELECT startDateLocal, elapsedTime, movingTime, streams, NULL AS summaryPolyline FROM Workout WHERE id=?', [rid]).fetchone()
    if not row or not row['startDateLocal']:
        return None
    try:
        start = datetime.fromisoformat(str(row['startDateLocal'])[:19].replace('Z', ''))
    except ValueError:
        return None
    st = {}
    try:
        st = json.loads(row['streams']) if row['streams'] else {}
    except ValueError:
        pass
    pts = (st.get('latlng') or {}).get('data') or []
    times = (st.get('time') or {}).get('data') or None
    if not pts and row['summaryPolyline']:
        try:
            import polyline
            pts = polyline.decode(row['summaryPolyline'])
        except Exception:
            pts = []
    return start, (row['elapsedTime'] or row['movingTime'] or 0), pts, times


def locate_by_time(db, rid, taken):
    """Where on the ride's track was it at `taken` (local time)? None if it was not during the ride."""
    if not taken:
        return None
    t = _ride_track(db, rid)
    if not t or not t[2]:
        return None
    start, elapsed, pts, times = t
    try:
        off = (datetime.fromisoformat(taken) - start).total_seconds()
    except ValueError:
        return None
    if off < -300 or off > (elapsed or 0) + 300:
        return None
    off = min(max(off, 0), elapsed or 0)
    if times and len(times) == len(pts):
        lo, hi = 0, len(times) - 1
        while lo < hi:
            mid = (lo + hi) // 2
            if times[mid] < off:
                lo = mid + 1
            else:
                hi = mid
        i = lo
    else:
        i = int(round((off / elapsed) * (len(pts) - 1))) if elapsed else 0
    p = pts[min(max(i, 0), len(pts) - 1)]
    return float(p[0]), float(p[1])


def add_photo(db, rid, data, db_path, fault_id=None, caption=None, lat=None, lng=None, taken=None, client_id=None, taken_fallback=None):
    """Store one uploaded picture against a ride (and optionally a fault); returns the new row as a dict.
    The phone may send the location/time it read from the picture itself (lat, lng, taken) because Android hides GPS from the file the server receives;
    those win over anything the server can read. client_id makes a retried upload return the first copy."""
    from PIL import Image, ImageOps
    if client_id:
        old = db.execute('SELECT * FROM RidePhoto WHERE rideId=? AND clientId=?', [rid, client_id]).fetchone()
        if old:
            return {'id': old['id'], 'file': old['file'], 'lat': old['lat'], 'lng': old['lng'], 'placedBy': old['placedBy'], 'duplicate': True}
    if len(data) > MAX_PHOTO_BYTES:
        raise ValueError('that picture is too large (limit 20 MB)')
    if db.execute('SELECT COUNT(*) FROM RidePhoto WHERE rideId=?', [rid]).fetchone()[0] >= MAX_PHOTOS_PER_RIDE:
        raise ValueError(f'a ride can have up to {MAX_PHOTOS_PER_RIDE} pictures')
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception:
        raise ValueError('that does not look like a picture')
    if lat is None or lng is None:
        lat, lng, t2 = read_exif(img)
        taken = taken or t2 or taken_fallback
    placed = 'exif' if lat is not None else None
    if lat is None:
        pos = locate_by_time(db, rid, taken)
        if pos:
            lat, lng = pos
            placed = 'time'
    img = ImageOps.exif_transpose(img).convert('RGB')               # rotate as the camera meant, then drop all EXIF
    base = secrets.token_hex(6)
    d = photo_dir(db_path)
    full = img.copy()
    full.thumbnail((1800, 1800), Image.LANCZOS)
    full.save(os.path.join(d, f'rp_{base}.jpg'), 'JPEG', quality=85, optimize=True)
    img.thumbnail((420, 420), Image.LANCZOS)
    img.save(os.path.join(d, f'rp_{base}_t.jpg'), 'JPEG', quality=80, optimize=True)
    cur = db.execute('INSERT INTO RidePhoto (rideId, faultId, file, lat, lng, takenAt, placedBy, caption, clientId) VALUES (?,?,?,?,?,?,?,?,?)',
                     [rid, fault_id, f'rp_{base}.jpg', lat, lng, taken, placed, (caption or '').strip()[:200] or None, client_id])
    return {'id': cur.lastrowid, 'file': f'rp_{base}.jpg', 'lat': lat, 'lng': lng, 'placedBy': placed}


def _remove_files(db_path, name):
    d = photo_dir(db_path)
    for n in (name, name.replace('.jpg', '_t.jpg')):
        if valid_name(n):
            try:
                os.remove(os.path.join(d, n))
            except OSError:
                pass


def delete_photo(db, pid, db_path):
    row = db.execute('SELECT file FROM RidePhoto WHERE id=?', [pid]).fetchone()
    if row:
        db.execute('DELETE FROM RidePhoto WHERE id=?', [pid])
        _remove_files(db_path, row['file'])
    return bool(row)


def _default_db_path():
    from flask import current_app
    return current_app.config['DATABASE']


def delete_ride_extras(db, rid, db_path=None):
    """Called when a ride or workout is deleted: its photos, files and faults (and the service-log entries they made) go with it."""
    db_path = db_path or _default_db_path()
    for p in db.execute('SELECT id FROM RidePhoto WHERE rideId=?', [rid]).fetchall():
        delete_photo(db, p['id'], db_path)
    for f in db.execute('SELECT id FROM RideFault WHERE rideId=?', [rid]).fetchall():
        delete_fault(db, f['id'], db_path)


def move_extras(db, old_id, new_id):
    """The activity changed family (ride <-> walk/run/hike) and got a new id: keep its photos; faults only make sense on a ride, so they and their log entries go."""
    db.execute('UPDATE RidePhoto SET faultId=NULL WHERE rideId=?', [old_id])
    for f in db.execute('SELECT id, logId FROM RideFault WHERE rideId=?', [old_id]).fetchall():
        if f['logId']:
            db.execute('DELETE FROM ServiceLog WHERE id=?', [f['logId']])
        db.execute('DELETE FROM RideFault WHERE id=?', [f['id']])
    db.execute('UPDATE RidePhoto SET rideId=? WHERE rideId=?', [new_id, old_id])


def photos_for(db, rid):
    return [dict(r) for r in db.execute('SELECT * FROM RidePhoto WHERE rideId=? ORDER BY COALESCE(takenAt, createdAt), id', [rid]).fetchall()]


def set_photo_place(db, pid, lat, lng):
    if lat is None or lng is None:
        db.execute('UPDATE RidePhoto SET lat=NULL, lng=NULL, placedBy=NULL WHERE id=?', [pid])
    else:
        db.execute("UPDATE RidePhoto SET lat=?, lng=?, placedBy='manual' WHERE id=?", [float(lat), float(lng), pid])


# ---------- faults ----------
def _clean(s, n):
    s = (s or '').strip()
    return s[:n] or None


def category_label(key):
    return _CAT.get(key, 'Something else')


def _first_photo_position(db, fault_id):
    r = db.execute('SELECT lat, lng FROM RidePhoto WHERE faultId=? AND lat IS NOT NULL ORDER BY id LIMIT 1', [fault_id]).fetchone()
    return (r['lat'], r['lng']) if r else None


def _ride_row(db, rid):
    r = db.execute('SELECT id, riderId, bikeId, startDateLocal FROM Activity WHERE id=?', [rid]).fetchone()
    if not r:
        raise ValueError('faults can be logged on rides')
    return r


def _log_notes(f):
    bits = [category_label(f['category'])]
    if f['description']:
        bits.append(f['description'])
    if f['outcome'] in _OUT:
        bits.append(_OUT[f['outcome']].lower())
    if f['minutesLost']:
        bits.append(f'{f["minutesLost"]} min lost')
    bits.append(f'repaired {f["repairedOn"]}' if f['repairedOn'] else 'REPAIR PENDING')
    return ('Fault on a ride: ' + ' - '.join(bits))[:500]


def sync_log(db, fault_id):
    """Write (or refresh, or drop) the fault's entry in its bike's service log. A fault with no bike has no entry yet."""
    from services import gear
    f = db.execute('SELECT * FROM RideFault WHERE id=?', [fault_id]).fetchone()
    if not f:
        return
    if f['logId']:
        db.execute('DELETE FROM ServiceLog WHERE id=?', [f['logId']])
        db.execute('UPDATE RideFault SET logId=NULL WHERE id=?', [fault_id])
    if not f['bikeId'] or not gear.get_bike(db, f['bikeId']):
        return
    part = f['partId'] if f['partId'] and db.execute('SELECT 1 FROM Part WHERE id=? AND bikeId=?', [f['partId'], f['bikeId']]).fetchone() else None
    when = (f['date'] or gear.today().isoformat())[:10]
    odo = (gear.get_bike(db, f['bikeId'])['startMeters'] or 0) + gear.distance_on(db, f['bikeId'], until=when)
    cur = db.execute('INSERT INTO ServiceLog (bikeId, partId, date, action, odometerM, notes) VALUES (?,?,?,?,?,?)', [f['bikeId'], part, when, 'fault', odo, _log_notes(f)])
    db.execute('UPDATE RideFault SET logId=? WHERE id=?', [cur.lastrowid, fault_id])


def add_fault(db, rid, category, outcome=None, description=None, part_id=None, minutes_lost=None, lat=None, lng=None, client_id=None):
    if client_id:
        old = db.execute('SELECT id FROM RideFault WHERE rideId=? AND clientId=?', [rid, client_id]).fetchone()
        if old:
            return old['id']
    r = _ride_row(db, rid)
    cat = category if category in _CAT else 'other'
    out = outcome if outcome in _OUT else None
    try:
        mins = int(minutes_lost) if minutes_lost not in (None, '') else None
    except (TypeError, ValueError):
        raise ValueError('minutes lost must be a number')
    if mins is not None and not 0 <= mins <= 1440:
        raise ValueError('minutes lost is out of range')
    day = str(r['startDateLocal'])[:10]
    cur = db.execute('INSERT INTO RideFault (rideId, riderId, bikeId, partId, category, outcome, minutesLost, description, lat, lng, date, repairedOn, clientId) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                     [rid, r['riderId'], r['bikeId'], part_id or None, cat, out, mins, _clean(description, 500),
                      float(lat) if lat not in (None, '') else None, float(lng) if lng not in (None, '') else None, day, day if out == 'fixed' else None, client_id])
    sync_log(db, cur.lastrowid)
    return cur.lastrowid


def update_fault(db, fault_id, category=None, outcome=None, description=None, part_id=None, minutes_lost=None):
    f = db.execute('SELECT * FROM RideFault WHERE id=?', [fault_id]).fetchone()
    if not f:
        raise ValueError('fault not found')
    try:
        mins = int(minutes_lost) if minutes_lost not in (None, '') else None
    except (TypeError, ValueError):
        raise ValueError('minutes lost must be a number')
    db.execute('UPDATE RideFault SET category=?, outcome=?, description=?, partId=?, minutesLost=? WHERE id=?',
               [category if category in _CAT else f['category'], outcome if outcome in _OUT else None, _clean(description, 500), part_id or None, mins, fault_id])
    sync_log(db, fault_id)


REMIND_AFTER_DAYS = (2, 7, 21)       # a pending repair is mentioned at these ages, then left alone


def remind_pending(db, send=True):
    """Nudge about faults still unrepaired after 2, 7 and 21 days: one message per rider covering everything newly due. Does not commit.
    Returns [{riderId, count, text}]."""
    from services import gear
    today = gear.today()
    rows = db.execute("SELECT f.*, b.name AS bikeName, p.name AS partName FROM RideFault f LEFT JOIN Bike b ON b.id=f.bikeId LEFT JOIN Part p ON p.id=f.partId "
                      "WHERE f.repairedOn IS NULL AND COALESCE(f.remindedCount, 0) < ?", [len(REMIND_AFTER_DAYS)]).fetchall()
    by_rider = {}
    for f in rows:
        try:
            age = (today - gear.parse_date(f['date'])).days
        except Exception:
            continue
        n = f['remindedCount'] or 0
        if age < REMIND_AFTER_DAYS[n]:
            continue
        passed = sum(1 for d in REMIND_AFTER_DAYS if age >= d)      # catching up after downtime: mentioned once, not three times in a row
        db.execute('UPDATE RideFault SET remindedCount=? WHERE id=?', [passed, f['id']])
        what = category_label(f['category']) + (f' ({f["partName"]})' if f['partName'] else '') + (f' on {f["bikeName"]}' if f['bikeName'] else '')
        by_rider.setdefault(f['riderId'], []).append(f'{what}, {age} days ago')
    out = [{'riderId': r, 'count': len(v), 'text': 'Still to repair: ' + '; '.join(v)} for r, v in by_rider.items()]
    if send and out:
        _send_reminders(out)
    return out


def _send_reminders(items):
    from database import query_db
    for it in items:
        title = 'Repair pending' if it['count'] == 1 else f"{it['count']} repairs pending"
        try:
            from services.notify import send_notification
            send_notification(title, it['text'])
        except Exception as e:
            log.warning('fault reminder (ntfy) failed: %s', e)
        try:
            from services import mqtt
            s = query_db('SELECT mqttHost, mqttPort, mqttUser, mqttPassword FROM Settings WHERE id=1', one=True)
            if not s or not (s['mqttHost'] or '').strip():
                continue
            r = query_db('SELECT name, haDevice FROM Rider WHERE id=?', [it['riderId']], one=True)
            payload = {'title': title, 'message': it['text'], 'url': '/gear/faults', 'rider': r['name'] if r else None, 'kind': 'gear', 'ride_id': None}
            if r and r['haDevice']:
                payload['ha_device'] = r['haDevice']
            auth = {'username': s['mqttUser'], 'password': s['mqttPassword'] or ''} if s['mqttUser'] else None
            mqtt._broker_send([{'topic': f'{mqtt._prefix()}/new_ride', 'payload': json.dumps(payload), 'qos': 1}], s['mqttHost'].strip(), int(s['mqttPort'] or 1883), auth)
        except Exception as e:
            log.warning('fault reminder (MQTT) failed: %s', e)


def set_fault_place(db, fault_id, lat, lng):
    """Pin a fault where it happened (lat+lng None clears the pin)."""
    if not db.execute('SELECT 1 FROM RideFault WHERE id=?', [fault_id]).fetchone():
        raise ValueError('fault not found')
    if (lat is None) != (lng is None) or (lat is not None and not (-90 <= lat <= 90 and -180 <= lng <= 180)):
        raise ValueError('bad location')
    db.execute('UPDATE RideFault SET lat=?, lng=? WHERE id=?', [lat, lng, fault_id])


def set_repaired(db, fault_id, repaired=True, on=None):
    """Mark a fault repaired (today, or `on`) or put it back on the pending list; the service-log entry follows."""
    from services import gear
    if not db.execute('SELECT 1 FROM RideFault WHERE id=?', [fault_id]).fetchone():
        raise ValueError('fault not found')
    when = None
    if repaired:
        when = gear.parse_date(on).isoformat() if on else gear.today().isoformat()
    db.execute('UPDATE RideFault SET repairedOn=? WHERE id=?', [when, fault_id])
    sync_log(db, fault_id)


def pending(db, rider_id):
    """Faults not yet repaired, oldest first, with the bike, part and ride they belong to."""
    rows = db.execute('SELECT f.*, b.name AS bikeName, p.name AS partName, a.name AS rideName FROM RideFault f LEFT JOIN Bike b ON b.id=f.bikeId LEFT JOIN Part p ON p.id=f.partId '
                      'LEFT JOIN Activity a ON a.id=f.rideId WHERE f.riderId=? AND f.repairedOn IS NULL ORDER BY f.date, f.id', [rider_id]).fetchall()
    return [dict(r, categoryLabel=category_label(r['category']), outcomeLabel=_OUT.get(r['outcome'])) for r in rows]


def delete_fault(db, fault_id, db_path):
    f = db.execute('SELECT * FROM RideFault WHERE id=?', [fault_id]).fetchone()
    if not f:
        return False
    if f['logId']:
        db.execute('DELETE FROM ServiceLog WHERE id=?', [f['logId']])
    for p in db.execute('SELECT id FROM RidePhoto WHERE faultId=?', [fault_id]).fetchall():
        delete_photo(db, p['id'], db_path)
    db.execute('DELETE FROM RideFault WHERE id=?', [fault_id])
    return True


def resync_ride_bike(db, rid):
    """The ride's bike was set or changed: move its faults onto that bike's service log (a part from the old bike no longer applies)."""
    a = db.execute('SELECT bikeId FROM Activity WHERE id=?', [rid]).fetchone()
    if not a:
        return
    for f in db.execute('SELECT id, bikeId FROM RideFault WHERE rideId=?', [rid]).fetchall():
        if f['bikeId'] != a['bikeId']:
            db.execute('UPDATE RideFault SET bikeId=?, partId=NULL WHERE id=?', [a['bikeId'], f['id']])
        sync_log(db, f['id'])


def faults_for(db, rid):
    out = []
    for f in db.execute('SELECT f.*, p.name AS partName, b.name AS bikeName FROM RideFault f LEFT JOIN Part p ON p.id=f.partId LEFT JOIN Bike b ON b.id=f.bikeId WHERE f.rideId=? ORDER BY f.id', [rid]).fetchall():
        d = dict(f)
        d['categoryLabel'] = category_label(f['category'])
        d['outcomeLabel'] = _OUT.get(f['outcome'])
        out.append(d)
    return out


def fault_ride_ids(db, ids):
    """Of these ride ids, the ones with at least one fault (for the badge on the activity list)."""
    ids = [str(i) for i in ids]
    if not ids:
        return set()
    q = ','.join('?' * len(ids))
    return {r[0] for r in db.execute(f'SELECT DISTINCT rideId FROM RideFault WHERE rideId IN ({q})', ids).fetchall()}


# ---------- statistics ----------
def season_of(date_str):
    m = int(str(date_str)[5:7])
    return SEASONS[0] if m in (12, 1, 2) else SEASONS[1] if m in (3, 4, 5) else SEASONS[2] if m in (6, 7, 8) else SEASONS[3]


def stats(db, rider_id, year=None):
    """Everything the Faults page shows, for one rider (optionally one calendar year)."""
    q, a = ('SELECT f.*, b.name AS bikeName, p.name AS partName, act.name AS rideName FROM RideFault f LEFT JOIN Bike b ON b.id=f.bikeId LEFT JOIN Part p ON p.id=f.partId '
            'LEFT JOIN Activity act ON act.id=f.rideId WHERE f.riderId=?'), [rider_id]
    if year:
        q += ' AND substr(f.date,1,4)=?'
        a.append(str(year))
    rows = db.execute(q + ' ORDER BY f.date DESC, f.id DESC', a).fetchall()
    by_cat, by_season, by_bike, by_month = {}, {s: 0 for s in SEASONS}, {}, {}
    lost = 0
    for f in rows:
        by_cat[f['category']] = by_cat.get(f['category'], 0) + 1
        if f['date']:
            by_season[season_of(f['date'])] += 1
            by_month[f['date'][:7]] = by_month.get(f['date'][:7], 0) + 1
        by_bike[f['bikeName'] or 'No bike set'] = by_bike.get(f['bikeName'] or 'No bike set', 0) + 1
        lost += f['minutesLost'] or 0
    rq, ra = ("SELECT COUNT(*), COALESCE(SUM(distance),0) FROM Activity WHERE riderId=? AND (lower(sportType) LIKE '%ride%' OR lower(sportType) LIKE '%cycl%')"), [rider_id]
    if year:
        rq += ' AND substr(startDateLocal,1,4)=?'
        ra.append(str(year))
    n_rides, metres = db.execute(rq, ra).fetchone()
    # season totals need the rides ridden per season to be a fair rate
    season_rides = {s: 0 for s in SEASONS}
    sq, sa = "SELECT startDateLocal FROM Activity WHERE riderId=? AND (lower(sportType) LIKE '%ride%' OR lower(sportType) LIKE '%cycl%')", [rider_id]
    if year:
        sq += ' AND substr(startDateLocal,1,4)=?'
        sa.append(str(year))
    for r in db.execute(sq, sa).fetchall():
        season_rides[season_of(r[0])] += 1
    years = [r[0] for r in db.execute("SELECT DISTINCT substr(date,1,4) FROM RideFault WHERE riderId=? AND date IS NOT NULL ORDER BY 1 DESC", [rider_id]).fetchall()]
    return {
        'pending': sum(1 for f in rows if not f['repairedOn']), 'total': len(rows), 'rides_with': len({f['rideId'] for f in rows}), 'rides': n_rides, 'metres': metres, 'minutes_lost': lost,
        'by_category': sorted(((k, category_label(k), v) for k, v in by_cat.items()), key=lambda x: -x[2]),
        'by_season': [(s, by_season[s], season_rides[s]) for s in SEASONS], 'by_bike': sorted(by_bike.items(), key=lambda x: -x[1]),
        'by_month': sorted(by_month.items())[-12:], 'recent': [dict(f, categoryLabel=category_label(f['category']), outcomeLabel=_OUT.get(f['outcome'])) for f in rows[:60]], 'years': years,
    }


# ---------- for the AI ----------
def ai_lines(db, activity):
    """Text for the ride analysis prompt: this ride's faults, and the recent fault pattern on its bike."""
    rid = str(activity['id'])
    mine = faults_for(db, rid)
    lines = []
    if mine:
        lines.append('MECHANICAL FAULT(S) ON THIS RIDE (ground truth - the rider marked these; they explain pace, stops and an early finish):')
        for f in mine:
            s = f'  - {f["categoryLabel"]}'
            if f['partName']:
                s += f' ({f["partName"]})'
            if f['description']:
                s += f': {f["description"]}'
            if f['outcomeLabel']:
                s += f' [{f["outcomeLabel"]}]'
            if f['minutesLost']:
                s += f' [about {f["minutesLost"]} min lost]'
            s += f' [repaired {f["repairedOn"]}]' if f['repairedOn'] else ' [not repaired yet]'
            lines.append(s)
    rider = activity['riderId'] if 'riderId' in activity.keys() else None
    bike = activity['bikeId'] if 'bikeId' in activity.keys() else None
    if rider:
        q = "SELECT category, date, rideId FROM RideFault WHERE riderId=? AND date >= date(?, '-180 day') AND date <= ? AND rideId != ?"
        a = [rider, str(activity['startDateLocal'])[:10], str(activity['startDateLocal'])[:10], rid]
        if bike:
            q += ' AND bikeId=?'
            a.append(bike)
        recent = db.execute(q + ' ORDER BY date DESC', a).fetchall()
        if recent:
            counts = {}
            for r in recent:
                counts[category_label(r['category'])] = counts.get(category_label(r['category']), 0) + 1
            lines.append(f'Earlier faults in the last 6 months{" on this bike" if bike else ""}: ' + ', '.join(f'{k} x{v}' for k, v in sorted(counts.items(), key=lambda x: -x[1])) +
                         '. Mention a repeat of the same fault if there is one; do not invent causes.')
    return '\n'.join(lines)


def photo_json(p, base='/api/v1/ride-photos/'):
    return {'id': p['id'], 'ride_id': p['rideId'], 'fault_id': p['faultId'], 'url': base + p['file'], 'thumb_url': base + p['file'].replace('.jpg', '_t.jpg'),
            'lat': p['lat'], 'lng': p['lng'], 'taken_at': p['takenAt'], 'placed_by': p['placedBy'], 'caption': p['caption'], 'client_id': p['clientId']}


def fault_json(f):
    return {'id': f['id'], 'ride_id': f['rideId'], 'bike_id': f['bikeId'], 'bike_name': f.get('bikeName'), 'part_id': f['partId'], 'part_name': f.get('partName'),
            'category': f['category'], 'category_label': category_label(f['category']), 'outcome': f['outcome'], 'outcome_label': _OUT.get(f['outcome']),
            'minutes_lost': f['minutesLost'], 'description': f['description'], 'lat': f['lat'], 'lng': f['lng'], 'date': f['date'], 'repaired_on': f['repairedOn'],
            'pending': not f['repairedOn'], 'in_service_log': bool(f['logId']), 'client_id': f.get('clientId')}
