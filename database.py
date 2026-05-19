import sqlite3
from flask import current_app, g


def migrate_db():
    db = get_db()

    # ── Rider table ──────────────────────────────────────────────────
    db.execute('''
        CREATE TABLE IF NOT EXISTS Rider (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT NOT NULL,
            avatarPath TEXT,
            isDefault  INTEGER DEFAULT 0
        )
    ''')

    # Seed default riders if table is empty
    if db.execute('SELECT COUNT(*) FROM Rider').fetchone()[0] == 0:
        db.execute("INSERT INTO Rider (name, avatarPath, isDefault) VALUES ('Rob', 'custard_cream.svg', 1)")
        db.execute("INSERT INTO Rider (name, avatarPath, isDefault) VALUES ('Smithy', NULL, 0)")
        db.commit()

    # haDevice on Rider
    rider_cols = [r[1] for r in db.execute('PRAGMA table_info(Rider)').fetchall()]
    if 'haDevice' not in rider_cols:
        db.execute('ALTER TABLE Rider ADD COLUMN haDevice TEXT')
        db.commit()

    # riderId on Activity
    act_cols = [r[1] for r in db.execute('PRAGMA table_info(Activity)').fetchall()]
    if act_cols and 'riderId' not in act_cols:
        db.execute('ALTER TABLE Activity ADD COLUMN riderId INTEGER REFERENCES Rider(id)')
        # Migrate all existing activities to the default rider
        default = db.execute('SELECT id FROM Rider WHERE isDefault=1 LIMIT 1').fetchone()
        if default:
            db.execute('UPDATE Activity SET riderId=? WHERE riderId IS NULL', [default[0]])
        db.commit()

    # Settings columns
    cols = [r[1] for r in db.execute('PRAGMA table_info(Settings)').fetchall()]
    if 'ntfyUrl' not in cols:
        db.execute('ALTER TABLE Settings ADD COLUMN ntfyUrl TEXT')
    if 'webhookSubId' not in cols:
        db.execute('ALTER TABLE Settings ADD COLUMN webhookSubId TEXT')
    if 'mqttHost' not in cols:
        db.execute('ALTER TABLE Settings ADD COLUMN mqttHost TEXT')
    if 'mqttPort' not in cols:
        db.execute('ALTER TABLE Settings ADD COLUMN mqttPort INTEGER DEFAULT 1883')
    if 'mqttUser' not in cols:
        db.execute('ALTER TABLE Settings ADD COLUMN mqttUser TEXT')
    if 'mqttPassword' not in cols:
        db.execute('ALTER TABLE Settings ADD COLUMN mqttPassword TEXT')
    if 'coachingGoals' not in cols:
        db.execute('ALTER TABLE Settings ADD COLUMN coachingGoals TEXT')
    if 'garminEmail' not in cols:
        db.execute('ALTER TABLE Settings ADD COLUMN garminEmail TEXT')
    if 'garminPassword' not in cols:
        db.execute('ALTER TABLE Settings ADD COLUMN garminPassword TEXT')
    if 'garminSyncHours' not in cols:
        db.execute('ALTER TABLE Settings ADD COLUMN garminSyncHours INTEGER DEFAULT 2')

    # GarminDaily columns
    gcols = [r[1] for r in db.execute('PRAGMA table_info(GarminDaily)').fetchall()]
    if 'steps' not in gcols:
        db.execute('ALTER TABLE GarminDaily ADD COLUMN steps INTEGER')
    if 'stressScore' not in gcols:
        db.execute('ALTER TABLE GarminDaily ADD COLUMN stressScore INTEGER')
    if 'coachPersonality' not in cols:
        db.execute("ALTER TABLE Settings ADD COLUMN coachPersonality TEXT DEFAULT 'default'")

    # Segment tables
    db.execute('''
        CREATE TABLE IF NOT EXISTS Segment (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            name             TEXT NOT NULL,
            startLat         REAL NOT NULL,
            startLng         REAL NOT NULL,
            endLat           REAL NOT NULL,
            endLng           REAL NOT NULL,
            distanceM        REAL,
            sourceActivityId TEXT,
            polyline         TEXT,
            createdAt        TEXT DEFAULT (datetime('now'))
        )
    ''')
    seg_cols = [r[1] for r in db.execute('PRAGMA table_info(Segment)').fetchall()]
    if 'polyline' not in seg_cols:
        db.execute('ALTER TABLE Segment ADD COLUMN polyline TEXT')
    if 'elevationGainM' not in seg_cols:
        db.execute('ALTER TABLE Segment ADD COLUMN elevationGainM REAL')
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

    # RideMemory table
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
            createdAt   TEXT DEFAULT (datetime(\'now\')),
            updatedAt   TEXT DEFAULT (datetime(\'now\'))
        )
    ''')

    # Weather + description columns on Activity
    _act_cols = {r[1] for r in db.execute('PRAGMA table_info(Activity)').fetchall()}
    for _col, _defn in [
        ('description',    'TEXT'),
        ('notes',          'TEXT'),
        ('weatherTempC',   'REAL'),
        ('weatherWindKph', 'REAL'),
        ('weatherGustKph', 'REAL'),
        ('weatherWindDir', 'INTEGER'),
        ('weatherHumidity','INTEGER'),
        ('weatherRainMm',  'REAL'),
        ('weatherCode',    'INTEGER'),
        ('weatherSummary', 'TEXT'),
        ('weatherWindRel', 'TEXT'),
    ]:
        if _col not in _act_cols:
            db.execute(f'ALTER TABLE Activity ADD COLUMN {_col} {_defn}')
    db.commit()

    db.execute('''
        CREATE TABLE IF NOT EXISTS GarminDaily (
            date         TEXT PRIMARY KEY,
            restingHR    INTEGER,
            hrv          INTEGER,
            hrvBalanced  INTEGER DEFAULT 0,
            sleepHours   REAL,
            sleepScore   INTEGER,
            bodyBattery  INTEGER
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
