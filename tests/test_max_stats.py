"""Max speed/HR/power on import, FIT enhanced fields, whole-route wind classification, and the backfill's safety rules."""
import datetime
import json
import math
import sqlite3
from types import SimpleNamespace

import pytest
from flask import Flask

import database
from services import parser, weather
from services.max_stats import backfill


# ---------- GPX ----------
def _gpx(speeds_mps, hr=None, power=None, spike_at=None):
    """Straight north track, 1 s per point, with the given per-second speeds (m/s)."""
    t0 = datetime.datetime(2026, 5, 1, 8, 0, 0)
    lat, pts = 54.0, []
    for i, v in enumerate(speeds_mps):
        lat += v / 111_000
        la = lat + (0.01 if spike_at == i else 0)        # a one-point GPS jump ~1.1 km
        ext = ''
        if hr or power:
            ext = ('<extensions><gpxtpx:TrackPointExtension xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v1">'
                   + (f'<gpxtpx:hr>{hr[i]}</gpxtpx:hr>' if hr else '') + (f'<gpxtpx:power>{power[i]}</gpxtpx:power>' if power else '')
                   + '</gpxtpx:TrackPointExtension></extensions>')
        pts.append(f'<trkpt lat="{la:.6f}" lon="-1.6"><ele>100</ele><time>{(t0 + datetime.timedelta(seconds=i)).strftime("%Y-%m-%dT%H:%M:%SZ")}</time>{ext}</trkpt>')
    return ('<?xml version="1.0"?><gpx version="1.1" creator="t" xmlns="http://www.topografix.com/GPX/1/1"><trk><name>t</name><type>cycling</type><trkseg>'
            + ''.join(pts) + '</trkseg></trk></gpx>').encode()


def test_gpx_max_speed_is_real_not_average():
    speeds = [5.0] * 60 + [12.0] * 30 + [5.0] * 60          # avg ~7, real top speed 12 m/s
    a = parser.parse_gpx(_gpx(speeds))
    assert a['maxSpeed'] == pytest.approx(12.0, abs=0.6)
    assert a['maxSpeed'] > a['averageSpeed'] * 1.3


def test_gpx_gps_spike_does_not_make_a_fake_max():
    a = parser.parse_gpx(_gpx([6.0] * 120, spike_at=60))
    assert a['maxSpeed'] < 15                                 # 10 s windowing swallows a 1 km jump (raw would be >1000 m/s)


def test_gpx_max_heart_rate_and_power():
    a = parser.parse_gpx(_gpx([6.0] * 30, hr=list(range(120, 150)), power=[200] * 29 + [900]))
    assert a['maxHeartrate'] == 149 and a['maxWatts'] == 900 and a['averageWatts'] > 200


def test_gpx_without_hr_has_no_hr_keys():
    a = parser.parse_gpx(_gpx([6.0] * 30))
    assert 'maxHeartrate' not in a and 'maxWatts' not in a


# ---------- FIT (fitparse stubbed: tests the field handling, not the binary decoder) ----------
class _Msg:
    def __init__(self, name, **fields):
        self.name = name; self._f = fields
    def __iter__(self):
        return iter(SimpleNamespace(name=k, value=v) for k, v in self._f.items())


def _fit(monkeypatch, messages):
    import fitparse
    monkeypatch.setattr(fitparse, 'FitFile', lambda f: SimpleNamespace(get_messages=lambda: iter(messages)))


def _records(n, **extra):
    t0 = datetime.datetime(2026, 5, 1, 8, 0, 0)
    return [_Msg('record', timestamp=t0 + datetime.timedelta(seconds=i), position_lat=int(54 * 2 ** 31 / 180) + i * 100,
                 position_long=int(-1.6 * 2 ** 31 / 180), distance=i * 6.0, **{k: (v(i) if callable(v) else v) for k, v in extra.items()}) for i in range(n)]


def test_fit_enhanced_only_fields_are_used(monkeypatch):
    recs = _records(40, enhanced_altitude=lambda i: 100 + i, enhanced_speed=lambda i: 6.0 + (4 if i == 20 else 0))
    _fit(monkeypatch, recs + [_Msg('session', start_time=recs[0]._f['timestamp'], total_distance=240.0, total_timer_time=40)])
    a = parser.parse_fit(b'x'); s = json.loads(a['streams'])
    assert s['altitude']['data'][0] == 100 and len(s['altitude']['data']) == 40            # elevation no longer lost
    assert s['velocity_smooth']['data'][20] == 10.0 and a['maxSpeed'] == 10.0               # derived from the enhanced speed stream


def test_fit_prefers_device_session_maxima(monkeypatch):
    recs = _records(30, speed=6.0, heart_rate=lambda i: 120 + i)
    _fit(monkeypatch, recs + [_Msg('session', start_time=recs[0]._f['timestamp'], total_distance=180.0, total_timer_time=30,
                                   max_speed=11.5, max_heart_rate=181, max_power=1000)])
    a = parser.parse_fit(b'x')
    assert a['maxSpeed'] == 11.5 and a['maxHeartrate'] == 181 and a['maxWatts'] == 1000


def test_fit_session_junk_max_is_ignored(monkeypatch):
    recs = _records(30, speed=6.0)
    _fit(monkeypatch, recs + [_Msg('session', start_time=recs[0]._f['timestamp'], total_distance=180.0, total_timer_time=30, max_speed=400.0)])
    assert parser.parse_fit(b'x')['maxSpeed'] == 6.0                                          # 400 m/s rejected, stream value used


# ---------- wind ----------
def _route(points):
    return json.dumps({'latlng': {'data': points}})


def _line(lat0, lon0, bearing_deg, n=60, step_m=20):
    b = math.radians(bearing_deg)
    return [[lat0 + math.cos(b) * step_m * i / 111_000, lon0 + math.sin(b) * step_m * i / (111_000 * math.cos(math.radians(lat0)))] for i in range(n)]


def _classify(streams, wind_dir, kph=20):
    return weather._wind_relative(weather._wind_exposure(streams, wind_dir), kph)


def test_wind_straight_route_headwind_tailwind_crosswind():
    north = _route(_line(54, -1.6, 0))                        # riding north
    assert _classify(north, 0) == 'headwind'                  # wind FROM the north
    assert _classify(north, 180) == 'tailwind'
    assert _classify(north, 90) == 'crosswind'


def test_wind_loop_is_mixed_not_a_random_headwind():
    out = _line(54, -1.6, 0, 60); back = _line(out[-1][0], out[-1][1], 180, 60)          # up and back down: start == finish
    assert _classify(_route(out + back), 0) == 'mixed'
    assert _classify(_route(out + back), 123) == 'mixed'


def test_wind_calm_below_threshold():
    assert _classify(_route(_line(54, -1.6, 0)), 0, kph=3) == 'calm'


# ---------- backfill ----------
@pytest.fixture
def db(tmp_path):
    app = Flask('x'); app.config['DATABASE'] = str(tmp_path / 't.db')
    with app.app_context():
        database.migrate_db(); d = database.get_db(); d.row_factory = sqlite3.Row
        yield d


def _ins(d, id, avg, mx, streams, hr=None):
    d.execute("INSERT INTO Activity (id,name,type,sportType,startDate,startDateLocal,distance,movingTime,elapsedTime,totalElevationGain,"
              "averageSpeed,maxSpeed,maxHeartrate,streams,createdAt,updatedAt) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,'n','n')",
              [id, id, 'Ride', 'Ride', '2026-05-01T08:00:00', '2026-05-01T08:00:00', 1000, 100, 100, 0, avg, mx, hr, json.dumps(streams)])


def test_backfill_fixes_only_the_bug_signature(db):
    st = {'velocity_smooth': {'data': [5.0, 12.0, 6.0]}, 'heartrate': {'data': [120, 160, 140]}}
    _ins(db, 'buggy', 7.0, 7.0, st)            # max == average: the old bug
    _ins(db, 'good', 7.0, 11.0, st)            # a device recorded a real max: must not be touched
    c = backfill(db, apply=False)
    assert c['maxSpeed'] == 1 and db.execute("select maxSpeed from Activity where id='buggy'").fetchone()[0] == 7.0   # dry run wrote nothing
    c = backfill(db, apply=True)
    assert db.execute("select maxSpeed, maxHeartrate from Activity where id='buggy'").fetchone()[:] == (12.0, 160.0)
    assert db.execute("select maxSpeed from Activity where id='good'").fetchone()[0] == 11.0                            # untouched
    assert backfill(db, apply=True)['maxSpeed'] == 0                                                                    # idempotent
