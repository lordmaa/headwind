"""Phone sensor recordings ("sidecars"): what a phone's motion sensors saw during a ride, kept separate from the ride itself.

A phone can record its accelerometer / gyro / barometer next to a ride that another device (a Garmin Edge) records. Rather than fight the
duplicate-recording rule (which keeps the better ride and parks the other), the phone uploads only a sidecar: one row per second with
roughness, peak shock, gyro, tilt, pressure and (optionally) GPS. The sidecar is attached to whichever ride overlaps it in time, in whichever
order the two arrive, and survives the ride being re-imported. A ride recorded by the phone itself can carry its own sidecar the same way.
"""
import json
import logging
import math
from datetime import datetime, timezone

log = logging.getLogger(__name__)

FIELDS = ['t', 'rough', 'peak', 'gyro', 'tilt', 'press', 'lat', 'lng', 'spd', 'alt']
MIN_ROWS = 20
MAX_ROWS = 60_000          # 16 h at 1 Hz
MOUNTS = ('pocket', 'bars', 'frame', 'other')


def _utc_ms(s):
    """'YYYY-MM-DDTHH:MM:SS' (UTC, as stored in Activity/Workout.startDate) -> epoch ms, or None."""
    try:
        d = datetime.fromisoformat(str(s).replace('Z', '+00:00'))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return int(d.timestamp() * 1000)


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _alt_from_pressure(p, p0):
    """Barometric altitude (m) of pressure p (hPa) relative to p0."""
    if not p or not p0 or p <= 0 or p0 <= 0:
        return None
    return 44330.0 * (1 - (p / p0) ** 0.1903)


def validate(payload):
    """Clean an uploaded sidecar. Returns (clean_dict, error). Rows are lists aligned with `fields`; unknown fields are dropped, bad numbers become null."""
    if not isinstance(payload, dict):
        return None, 'expected a JSON object'
    cid = str(payload.get('client_id') or '').strip()[:80]
    if not cid:
        return None, 'client_id is required'
    fields = payload.get('fields')
    rows = payload.get('rows')
    if not isinstance(fields, list) or not isinstance(rows, list) or 't' not in fields:
        return None, 'fields (including t) and rows are required'
    if len(rows) < MIN_ROWS:
        return None, 'too short to keep (%d samples)' % len(rows)
    if len(rows) > MAX_ROWS:
        return None, 'too long'
    idx = {f: i for i, f in enumerate(fields) if f in FIELDS}
    out = []
    last_t = None
    for r in rows:
        if not isinstance(r, list) or len(r) < len(fields):
            continue
        t = _num(r[idx['t']])
        if t is None or (last_t is not None and t <= last_t):
            continue
        last_t = t
        out.append([int(t)] + [(_num(r[idx[f]]) if f in idx else None) for f in FIELDS[1:]])
    if len(out) < MIN_ROWS:
        return None, 'too few valid samples'
    start_ms, end_ms = out[0][0], out[-1][0]
    mount = str(payload.get('mount') or 'other')
    return {
        'client_id': cid, 'start_ms': start_ms, 'end_ms': end_ms, 'rows': out,
        'device': str(payload.get('device') or '')[:60], 'mount': mount if mount in MOUNTS else 'other',
        'sensors_only': 1 if payload.get('sensors_only') else 0,
    }, None


def summarise(rows):
    """Headline numbers for a sidecar: roughness, peak shock, tilt, barometric climb, hard braking, roughest stretches."""
    n = len(rows)
    ix = {f: i for i, f in enumerate(FIELDS)}
    col = lambda f: [r[ix[f]] for r in rows]
    rough = [v for v in col('rough') if v is not None]
    peak = [v for v in col('peak') if v is not None]
    tilt = [abs(v) for v in col('tilt') if v is not None]
    press = [v for v in col('press') if v is not None]
    spd = col('spd')
    out = {'samples': n, 'duration_s': round((rows[-1][0] - rows[0][0]) / 1000)}
    if rough:
        srt = sorted(rough)
        out.update(rough_mean=round(sum(rough) / len(rough), 2), rough_p95=round(srt[int(0.95 * (len(srt) - 1))], 2), rough_max=round(srt[-1], 2))
    if peak:
        out['peak_max'] = round(max(peak), 1)
    if tilt:
        out['tilt_max'] = round(max(tilt), 1)
    if len(press) >= 5:
        # smooth, then add up the rises: barometric climb is far steadier than GPS climb
        k = 5
        sm = [sum(press[max(0, i - k):i + k + 1]) / len(press[max(0, i - k):i + k + 1]) for i in range(len(press))]
        alts = [_alt_from_pressure(p, sm[0]) for p in sm]
        gain = 0.0
        floor = alts[0]
        for a in alts[1:]:
            if a - floor >= 1.0:
                gain += a - floor
                floor = a
            elif a < floor:
                floor = a
        out.update(press_start=round(press[0], 1), press_end=round(press[-1], 1), baro_climb_m=round(gain))
    # hard braking: speed falling by 2.5+ m/s within about a second, while moving
    hard, maxdec, prev, run = 0, 0.0, None, False
    for i, s in enumerate(spd):
        if s is None or prev is None or prev[0] is None:
            prev = (s, rows[i][0]); run = False
            continue
        dt = (rows[i][0] - prev[1]) / 1000.0
        dec = (prev[0] - s) / dt if 0 < dt <= 2.5 else 0
        if dec >= 2.5 and prev[0] > 3:
            if not run:
                hard += 1
            run = True
            maxdec = max(maxdec, dec)
        else:
            run = False
        prev = (s, rows[i][0])
    if any(s is not None for s in spd):
        out.update(hard_brakes=hard, max_decel=round(maxdec, 1))
    return out


def roughest(rows, span_s=20, top=3):
    """The roughest stretches: [{lat, lng, rough, t}] over rolling windows, with GPS where the sidecar has it, never two windows overlapping."""
    ix = {f: i for i, f in enumerate(FIELDS)}
    pts = [(r[0], r[ix['rough']], r[ix['lat']], r[ix['lng']]) for r in rows if r[ix['rough']] is not None]
    if len(pts) < span_s:
        return []
    wins = []
    for i in range(0, len(pts) - span_s, max(1, span_s // 4)):
        w = pts[i:i + span_s]
        m = sum(p[1] for p in w) / len(w)
        mid = w[len(w) // 2]
        wins.append((m, mid))
    wins.sort(key=lambda x: -x[0])
    chosen = []
    for m, mid in wins:
        if all(abs(mid[0] - c['t']) > span_s * 1000 for c in chosen):
            chosen.append({'t': mid[0], 'rough': round(m, 2), 'lat': mid[2], 'lng': mid[3]})
        if len(chosen) >= top:
            break
    return chosen


def store(db, rider_id, payload):
    """Validate and save a sidecar (idempotent per client_id), then try to attach it to a ride. Returns ({'id', 'attached'}, error). Does not commit."""
    clean, err = validate(payload)
    if err:
        return None, err
    summary = summarise(clean['rows'])
    summary['roughest'] = roughest(clean['rows'])
    start_iso = datetime.fromtimestamp(clean['start_ms'] / 1000, timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
    end_iso = datetime.fromtimestamp(clean['end_ms'] / 1000, timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
    row = db.execute('SELECT id FROM RideSensor WHERE clientId=?', [clean['client_id']]).fetchone()
    vals = [json.dumps(clean['rows'], separators=(',', ':')), json.dumps(summary, separators=(',', ':')), clean['device'], clean['mount'], clean['sensors_only']]
    if row:
        sid = row[0]
        db.execute('UPDATE RideSensor SET data=?, summary=?, device=?, mount=?, sensorsOnly=?, startUtc=?, endUtc=? WHERE id=?', vals + [start_iso, end_iso, sid])
    else:
        db.execute('INSERT INTO RideSensor (riderId, clientId, startUtc, endUtc, data, summary, device, mount, sensorsOnly) VALUES (?,?,?,?,?,?,?,?,?)',
                   [rider_id, clean['client_id'], start_iso, end_iso] + vals)
        sid = db.execute('SELECT last_insert_rowid()').fetchone()[0]
    return {'id': sid, 'attached': attach(db, sid)}, None


def _candidates(db, rider_id):
    """(kind, id, start_ms, end_ms) for the rider's rides and workouts."""
    out = []
    for kind, table, dur in (('activity', 'Activity', 'COALESCE(elapsedTime, movingTime, 0)'), ('workout', 'Workout', 'COALESCE(elapsedTime, movingTime, 0)')):
        for r in db.execute(f'SELECT id, startDate, {dur} AS d FROM {table} WHERE riderId=? AND startDate IS NOT NULL', [rider_id]).fetchall():
            s = _utc_ms(r['startDate'])
            if s is not None and r['d']:
                out.append((kind, r['id'], s, s + int(r['d']) * 1000))
    return out


def _best_overlap(sensor_span, cands):
    s0, s1 = sensor_span
    best = None
    for kind, rid, a0, a1 in cands:
        ov = min(s1, a1) - max(s0, a0)
        if ov <= 0:
            continue
        frac = ov / max(1, min(s1 - s0, a1 - a0))
        if frac >= 0.5 and (best is None or ov > best[0]):
            best = (ov, kind, rid)
    return best


def attach(db, sensor_id):
    """Link a sidecar to the ride/workout that overlaps it most (at least half of the shorter span). Returns {'kind', 'id'} or None. Does not commit."""
    s = db.execute('SELECT riderId, startUtc, endUtc FROM RideSensor WHERE id=?', [sensor_id]).fetchone()
    if not s:
        return None
    span = (_utc_ms(s['startUtc']), _utc_ms(s['endUtc']))
    best = _best_overlap(span, _candidates(db, s['riderId']))
    if not best:
        db.execute('UPDATE RideSensor SET rideKind=NULL, rideId=NULL WHERE id=?', [sensor_id])
        return None
    db.execute('UPDATE RideSensor SET rideKind=?, rideId=? WHERE id=?', [best[1], best[2], sensor_id])
    return {'kind': best[1], 'id': best[2]}


def attach_for_ride(db, ride_id):
    """A ride (or workout) has just arrived: attach any waiting sidecar that overlaps it. Never raises. Does not commit."""
    try:
        for kind, table in (('activity', 'Activity'), ('workout', 'Workout')):
            r = db.execute(f"SELECT riderId, startDate, COALESCE(elapsedTime, movingTime, 0) AS d FROM {table} WHERE id=?", [ride_id]).fetchone()
            if not r:
                continue
            s0 = _utc_ms(r['startDate'])
            if s0 is None or not r['d']:
                return
            span = (s0, s0 + int(r['d']) * 1000)
            for s in db.execute('SELECT id, startUtc, endUtc FROM RideSensor WHERE riderId=? AND (rideId IS NULL OR rideId=?)', [r['riderId'], ride_id]).fetchall():
                best = _best_overlap((_utc_ms(s['startUtc']), _utc_ms(s['endUtc'])), [(kind, ride_id, span[0], span[1])])
                if best:
                    db.execute('UPDATE RideSensor SET rideKind=?, rideId=? WHERE id=?', [kind, ride_id, s['id']])
            return
    except Exception as e:
        log.warning('sensor attach for %s failed (non-fatal): %s', ride_id, e)


def for_ride(db, ride_id, max_points=500):
    """The sidecar attached to a ride, shaped for display: summary plus thinned series against minutes since the ride started. None if there is none."""
    s = db.execute('SELECT id, startUtc, endUtc, data, summary, device, mount, sensorsOnly, rideKind FROM RideSensor WHERE rideId=? ORDER BY id DESC LIMIT 1', [ride_id]).fetchone()
    if not s:
        return None
    rows = json.loads(s['data'])
    table = 'Activity' if s['rideKind'] == 'activity' else 'Workout'
    ride = db.execute(f'SELECT startDate FROM {table} WHERE id=?', [ride_id]).fetchone()
    r0 = _utc_ms(ride['startDate']) if ride else rows[0][0]
    step = max(1, len(rows) // max_points)
    ix = {f: i for i, f in enumerate(FIELDS)}
    sub = rows[::step]
    p0 = next((r[ix['press']] for r in rows if r[ix['press']]), None)
    series = {
        'min': [round((r[0] - r0) / 60000, 2) for r in sub],
        'rough': [r[ix['rough']] for r in sub], 'tilt': [r[ix['tilt']] for r in sub],
        'alt': [None if r[ix['press']] is None else round(_alt_from_pressure(r[ix['press']], p0) or 0, 1) for r in sub],
    }
    return {'id': s['id'], 'device': s['device'], 'mount': s['mount'], 'sensors_only': bool(s['sensorsOnly']), 'summary': json.loads(s['summary']), 'series': series}
