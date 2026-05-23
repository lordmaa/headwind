import sqlite3
from flask import current_app, g


def migrate_db():
    db = get_db()

    # ── Create all tables first ──────────────────────────────────────
    db.execute('''
        CREATE TABLE IF NOT EXISTS Rider (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT NOT NULL,
            avatarPath TEXT,
            isDefault  INTEGER DEFAULT 0,
            haDevice   TEXT
        )
    ''')

    db.execute('''
        CREATE TABLE IF NOT EXISTS Activity (
            id                  TEXT PRIMARY KEY,
            name                TEXT,
            type                TEXT,
            sportType           TEXT,
            startDate           TEXT,
            startDateLocal      TEXT,
            timezone            TEXT,
            distance            REAL,
            movingTime          INTEGER,
            elapsedTime         INTEGER,
            totalElevationGain  REAL,
            averageSpeed        REAL,
            maxSpeed            REAL,
            averageHeartrate    REAL,
            maxHeartrate        REAL,
            averageWatts        REAL,
            maxWatts            REAL,
            weightedAvgWatts    REAL,
            kilojoules          REAL,
            averageCadence      REAL,
            calories            REAL,
            sufferScore         REAL,
            startLat            REAL,
            startLng            REAL,
            city                TEXT,
            country             TEXT,
            summaryPolyline     TEXT,
            kudosCount          INTEGER,
            streams             TEXT,
            rawData             TEXT,
            description         TEXT,
            notes               TEXT,
            riderId             INTEGER REFERENCES Rider(id),
            weatherTempC        REAL,
            weatherWindKph      REAL,
            weatherGustKph      REAL,
            weatherWindDir      INTEGER,
            weatherHumidity     INTEGER,
            weatherRainMm       REAL,
            weatherCode         INTEGER,
            weatherSummary      TEXT,
            weatherWindRel      TEXT,
            aiKudos             TEXT,
            createdAt           TEXT DEFAULT (datetime('now')),
            updatedAt           TEXT DEFAULT (datetime('now'))
        )
    ''')

    db.execute('''
        CREATE TABLE IF NOT EXISTS Settings (
            id               INTEGER PRIMARY KEY,
            aiProvider       TEXT,
            openaiKey        TEXT,
            openaiModel      TEXT,
            ollamaUrl        TEXT,
            ollamaModel      TEXT,
            ntfyUrl          TEXT,
            webhookSubId     TEXT,
            mqttHost         TEXT,
            mqttPort         INTEGER DEFAULT 1883,
            mqttUser         TEXT,
            mqttPassword     TEXT,
            coachingGoals    TEXT,
            coachPersonality TEXT DEFAULT 'default',
            garminEmail      TEXT,
            garminPassword   TEXT,
            garminSyncHours  INTEGER DEFAULT 2
        )
    ''')

    db.execute('''
        CREATE TABLE IF NOT EXISTS GarminDaily (
            date         TEXT PRIMARY KEY,
            restingHR    INTEGER,
            hrv          INTEGER,
            hrvBalanced  INTEGER DEFAULT 0,
            sleepHours   REAL,
            sleepScore   INTEGER,
            bodyBattery  INTEGER,
            steps        INTEGER,
            stressScore  INTEGER
        )
    ''')

    db.execute('''
        CREATE TABLE IF NOT EXISTS Segment (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            name             TEXT NOT NULL,
            startLat         REAL NOT NULL,
            startLng         REAL NOT NULL,
            endLat           REAL NOT NULL,
            endLng           REAL NOT NULL,
            distanceM        REAL,
            elevationGainM   REAL,
            sourceActivityId TEXT,
            polyline         TEXT,
            createdAt        TEXT DEFAULT (datetime('now'))
        )
    ''')

    db.execute('''
        CREATE TABLE IF NOT EXISTS SegmentEffort (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            segmentId    INTEGER NOT NULL REFERENCES Segment(id) ON DELETE CASCADE,
            activityId   TEXT NOT NULL,
            activityDate TEXT,
            elapsedSecs  INTEGER NOT NULL,
            avgSpeedMps  REAL,
            isPR         INTEGER DEFAULT 0,
            createdAt    TEXT DEFAULT (datetime('now')),
            UNIQUE(segmentId, activityId)
        )
    ''')

    db.execute('''
        CREATE TABLE IF NOT EXISTS RideMemory (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            rideId      TEXT UNIQUE NOT NULL,
            rideDate    TEXT,
            rideName    TEXT,
            distanceMi  REAL,
            movingTime  INTEGER,
            elevationFt REAL,
            avgSpeedMph REAL,
            avgPower    REAL,
            normPower   REAL,
            avgHR       REAL,
            calories    REAL,
            userNotes   TEXT,
            aiSummary   TEXT,
            tags        TEXT,
            createdAt   TEXT DEFAULT (datetime('now')),
            updatedAt   TEXT DEFAULT (datetime('now'))
        )
    ''')

    db.execute('''
        CREATE TABLE IF NOT EXISTS Athlete (
            id           INTEGER PRIMARY KEY,
            firstname    TEXT,
            lastname     TEXT,
            profile      TEXT,
            accessToken  TEXT,
            refreshToken TEXT,
            expiresAt    INTEGER
        )
    ''')

    db.execute('''
        CREATE TABLE IF NOT EXISTS BestEffort (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            activityId   TEXT NOT NULL,
            activityDate TEXT NOT NULL,
            distanceMi   REAL NOT NULL,
            elapsedSecs  INTEGER NOT NULL,
            avgSpeedMps  REAL,
            UNIQUE(activityId, distanceMi)
        )
    ''')

    db.commit()

    # ── Backfill columns added after initial release ─────────────────
    # (Safe to run on old DBs that predate the CREATE TABLE above)

    rider_cols = {r[1] for r in db.execute('PRAGMA table_info(Rider)').fetchall()}
    if 'haDevice' not in rider_cols:
        db.execute('ALTER TABLE Rider ADD COLUMN haDevice TEXT')

    act_cols = {r[1] for r in db.execute('PRAGMA table_info(Activity)').fetchall()}
    for col, defn in [
        ('riderId',         'INTEGER REFERENCES Rider(id)'),
        ('description',     'TEXT'),
        ('notes',           'TEXT'),
        ('weatherTempC',    'REAL'),
        ('weatherWindKph',  'REAL'),
        ('weatherGustKph',  'REAL'),
        ('weatherWindDir',  'INTEGER'),
        ('weatherHumidity', 'INTEGER'),
        ('weatherRainMm',   'REAL'),
        ('weatherCode',     'INTEGER'),
        ('weatherSummary',  'TEXT'),
        ('weatherWindRel',  'TEXT'),
        ('aiKudos',         'TEXT'),
    ]:
        if col not in act_cols:
            db.execute(f'ALTER TABLE Activity ADD COLUMN {col} {defn}')

    if 'riderId' in act_cols and 'riderId' not in act_cols:
        default = db.execute('SELECT id FROM Rider WHERE isDefault=1 LIMIT 1').fetchone()
        if default:
            db.execute('UPDATE Activity SET riderId=? WHERE riderId IS NULL', [default[0]])

    settings_cols = {r[1] for r in db.execute('PRAGMA table_info(Settings)').fetchall()}
    for col, defn in [
        ('ntfyUrl',          'TEXT'),
        ('webhookSubId',     'TEXT'),
        ('mqttHost',         'TEXT'),
        ('mqttPort',         'INTEGER DEFAULT 1883'),
        ('mqttUser',         'TEXT'),
        ('mqttPassword',     'TEXT'),
        ('coachingGoals',    'TEXT'),
        ('coachPersonality', "TEXT DEFAULT 'default'"),
        ('garminEmail',      'TEXT'),
        ('garminPassword',   'TEXT'),
        ('garminSyncHours',  'INTEGER DEFAULT 2'),
        ('feedToken',            'TEXT'),
        ('friendSyncInterval',   'INTEGER DEFAULT 15'),
    ]:
        if col not in settings_cols:
            db.execute(f'ALTER TABLE Settings ADD COLUMN {col} {defn}')

    db.execute('''
        CREATE TABLE IF NOT EXISTS Friend (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL,
            url         TEXT NOT NULL,
            token       TEXT,
            riderId     INTEGER REFERENCES Rider(id),
            lastSynced  TEXT,
            createdAt   TEXT DEFAULT (datetime('now'))
        )
    ''')

    garmin_cols = {r[1] for r in db.execute('PRAGMA table_info(GarminDaily)').fetchall()}
    for col, defn in [
        ('steps',              'INTEGER'),
        ('stressScore',        'INTEGER'),
        ('hrStream',           'TEXT'),
        ('bodyBatteryStream',  'TEXT'),
    ]:
        if col not in garmin_cols:
            db.execute(f'ALTER TABLE GarminDaily ADD COLUMN {col} {defn}')

    seg_cols = {r[1] for r in db.execute('PRAGMA table_info(Segment)').fetchall()}
    for col, defn in [
        ('polyline',       'TEXT'),
        ('elevationGainM', 'REAL'),
        ('friendId',       'INTEGER REFERENCES Friend(id)'),
        ('sourceSegId',    'INTEGER'),
    ]:
        if col not in seg_cols:
            db.execute(f'ALTER TABLE Segment ADD COLUMN {col} {defn}')

    friend_cols = {r[1] for r in db.execute('PRAGMA table_info(Friend)').fetchall()}
    if 'riderName' not in friend_cols:
        db.execute('ALTER TABLE Friend ADD COLUMN riderName TEXT')

    db.commit()


def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(
            current_app.config['DATABASE'],
            detect_types=sqlite3.PARSE_DECLTYPES,
        )
        g.db.row_factory = sqlite3.Row
        g.db.execute('PRAGMA journal_mode = WAL')
    return g.db


def close_db(e=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()


def query_db(query, args=(), one=False):
    cur = get_db().execute(query, args)
    rv = cur.fetchall()
    return (rv[0] if rv else None) if one else rv
