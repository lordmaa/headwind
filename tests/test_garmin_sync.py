"""Garmin incremental sync: same-day rides, retry-after-failure, and time-aware duplicate detection.
Runs against a throwaway SQLite file with a fake Garmin client — no network, no real data."""
import io
import zipfile

import pytest
from flask import Flask

import database
from services import garmin


def _zip():
    b = io.BytesIO()
    with zipfile.ZipFile(b, 'w') as z:
        z.writestr('x_ACTIVITY.fit', b'fit')
    return b.getvalue()


class FakeAPI:
    class ActivityDownloadFormat:
        ORIGINAL = 'orig'

    def __init__(self, acts, fail=()):
        self.acts, self.fail = acts, set(fail)

    def get_activities(self, start=0, limit=100):
        return self.acts[start:start + limit]

    def download_activity(self, aid, dl_fmt=None):
        if str(aid) in self.fail:
            raise RuntimeError('boom')
        return _zip()


def _act(aid, local, dist=10000):
    return {'activityId': aid, 'activityType': {'typeKey': 'cycling'}, 'startTimeLocal': local,
            'startTimeGMT': local, 'distance': dist, 'activityName': f'ride {aid}'}


@pytest.fixture
def env(tmp_path, monkeypatch):
    app = Flask(__name__)
    app.config['DATABASE'] = str(tmp_path / 't.db')
    with app.app_context():
        database.migrate_db()
        db = database.get_db()
        db.execute("INSERT INTO Rider(name) VALUES ('r')")
        db.commit()
        monkeypatch.setattr('services.parser.parse_fit', lambda b: {
            'distance': 10000, 'movingTime': 1800, 'elapsedTime': 1800, 'streams': None})
        monkeypatch.setattr('services.weather.fetch_weather', lambda *a, **k: None)

        def run(acts, fail=()):
            monkeypatch.setattr(garmin, '_client', lambda e, p: FakeAPI(acts, fail))
            return list(garmin.sync_garmin_activities('e', 'p', 1))
        yield db, run


def _ids(db):
    return sorted(r[0] for r in db.execute('SELECT id FROM Activity'))


def test_second_commute_same_day_is_imported_later(env):
    db, run = env
    run([_act(1, '2026-10-01 08:00:00')])
    # evening ride, identical distance, same day, uploaded after the first sync
    run([_act(2, '2026-10-01 17:30:00'), _act(1, '2026-10-01 08:00:00')])
    assert _ids(db) == ['garmin_1', 'garmin_2']


def test_failed_download_is_retried_not_skipped_forever(env):
    db, run = env
    run([_act(2, '2026-10-02 09:00:00'), _act(1, '2026-10-01 08:00:00')], fail={'2'})
    assert _ids(db) == ['garmin_1']
    run([_act(2, '2026-10-02 09:00:00'), _act(1, '2026-10-01 08:00:00')])
    assert _ids(db) == ['garmin_1', 'garmin_2']


def test_same_ride_from_another_source_keeps_the_garmin_recording(env):
    """A phone/file copy of the ride already exists: the Garmin one (device + sensors) replaces it, the old copy is parked, not lost."""
    db, run = env
    db.execute("INSERT INTO Activity (id,name,type,sportType,startDate,startDateLocal,distance,movingTime,elapsedTime,"
               "totalElevationGain,averageSpeed,maxSpeed,riderId,createdAt,updatedAt) VALUES "
               "('fit_x','a','Ride','Ride','2026-10-01T08:01:00','2026-10-01T08:01:00',10050,1700,1750,0,0,0,1,'n','n')")
    db.commit()
    run([_act(1, '2026-10-01 08:00:00')])
    assert _ids(db) == ['garmin_1']
    parked = db.execute("SELECT id, primaryId, status FROM ActivityDuplicate").fetchall()
    assert [tuple(r) for r in parked] == [('fit_x', 'garmin_1', 'parked')]
    run([_act(1, '2026-10-01 08:00:00')])               # a later sync must not bring the parked copy back or duplicate anything
    assert _ids(db) == ['garmin_1']
