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
            garminSyncHours  REAL DEFAULT 0.5
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

    db.execute('''
        CREATE TABLE IF NOT EXISTS FoodLog (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            riderId    INTEGER REFERENCES Rider(id),
            logDate    TEXT NOT NULL,
            barcode    TEXT,
            foodName   TEXT NOT NULL,
            calories   REAL,
            protein    REAL,
            carbs      REAL,
            fat        REAL,
            servingG   REAL,
            quantity   REAL DEFAULT 1,
            createdAt  TEXT DEFAULT (datetime('now'))
        )
    ''')

    db.execute('''
        CREATE TABLE IF NOT EXISTS HydrationLog (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            riderId   INTEGER REFERENCES Rider(id),
            logDate   TEXT NOT NULL,
            ml        INTEGER NOT NULL,
            createdAt TEXT DEFAULT (datetime('now'))
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
        ('aiKudosAt',       'TEXT'),
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
        ('garminSyncHours',  'REAL DEFAULT 0.5'),
        ('feedToken',                'TEXT'),
        ('friendSyncInterval',       'INTEGER DEFAULT 15'),
        ('garminActivitySyncDate',   'TEXT'),
        ('garminSyncMode',           "TEXT DEFAULT 'health'"),
        ('heightCm',             'REAL'),
        ('sex',                  'TEXT'),
        ('birthYear',            'INTEGER'),
        ('activityFactor',       'REAL'),
        ('lossLbPerWeek',        'REAL'),
        ('stepGoal',             'INTEGER'),
        ('goalAuto',             'INTEGER DEFAULT 0'),
        ('goalAutoLast',         'TEXT'),
        ('rideKcalWeek',         'REAL'),
        ('rideGoalWeek',         'INTEGER'),
        ('goalWeightKg',         'REAL'),
        ('holidayStart',         'TEXT'),
        ('holidayEnd',           'TEXT'),
        ('garminLastOk',         'TEXT'),
        ('garminLastError',      'TEXT'),
        ('garminDeviceSync',     'TEXT'),
        ('garminFailCount',      'INTEGER DEFAULT 0'),
        ('haUrl',                'TEXT'),
        ('haToken',              'TEXT'),
        ('haEntityMap',          'TEXT'),
        ('haSyncMinutes',        'INTEGER'),
        ('haLastSync',           'TEXT'),
        ('haLastResult',         'TEXT'),
        ('haWeatherEntity',      'TEXT'),
        ('haWeatherCondition',   'TEXT'),
        ('haWeatherTempC',       'REAL'),
        ('haWeatherUpdatedAt',   'TEXT'),
        ('installId',            'TEXT'),
        ('telemetryOptOut',      'INTEGER DEFAULT 0'),
        ('telemetryPingedAt',    'TEXT'),
        ('nutritionCalGoal',     'INTEGER'),
        ('nutritionProteinGoal', 'INTEGER'),
        ('nutritionCarbGoal',    'INTEGER'),
        ('nutritionFatGoal',     'INTEGER'),
        ('nutritionWaterGoalMl', 'INTEGER DEFAULT 2500'),
        ('nutritionBmrKcal',     'INTEGER'),
        ('units',                "TEXT DEFAULT 'imperial'"),
        ('distanceBackfillDone', 'INTEGER DEFAULT 0'),
        ('nutritionWeightUnit',     "TEXT DEFAULT 'stlb'"),
        ('nutritionWeightEmaAlpha', 'REAL DEFAULT 0.1'),
        ('dietStartDate',           'TEXT'),
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
        ('totalCalories',  'INTEGER'),
        ('activeCalories', 'INTEGER'),
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
    if 'lastFoodSync' not in friend_cols:
        db.execute('ALTER TABLE Friend ADD COLUMN lastFoodSync TEXT')

    db.execute('''
        CREATE TABLE IF NOT EXISTS PlannedRoute (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            name      TEXT NOT NULL,
            waypoints TEXT NOT NULL,
            createdAt TEXT DEFAULT (datetime('now'))
        )
    ''')

    db.execute('''
        CREATE TABLE IF NOT EXISTS FoodOverride (
            barcode     TEXT PRIMARY KEY,
            name        TEXT NOT NULL,
            brand       TEXT,
            kcal100g    REAL,
            protein100g REAL,
            carbs100g   REAL,
            fat100g     REAL,
            servingG    REAL,
            updatedAt   TEXT DEFAULT (datetime('now'))
        )
    ''')

    db.execute('''
        CREATE TABLE IF NOT EXISTS SavedMeal (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            name      TEXT NOT NULL,
            createdAt TEXT DEFAULT (datetime('now'))
        )
    ''')

    db.execute('''
        CREATE TABLE IF NOT EXISTS SavedMealItem (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            mealId   INTEGER NOT NULL REFERENCES SavedMeal(id) ON DELETE CASCADE,
            foodName TEXT NOT NULL,
            calories REAL,
            protein  REAL,
            carbs    REAL,
            fat      REAL,
            servingG REAL,
            barcode  TEXT
        )
    ''')

    food_log_cols = {r[1] for r in db.execute('PRAGMA table_info(FoodLog)').fetchall()}
    for col, defn in [
        ('mealType',        "TEXT DEFAULT 'uncategorised'"),
        ('source',          "TEXT DEFAULT 'manual'"),
        ('brand',           'TEXT'),
        ('imageUrl',        'TEXT'),
        ('nutriScoreGrade', 'TEXT'),
        ('fibre',           'REAL'),
    ]:
        if col not in food_log_cols:
            db.execute(f'ALTER TABLE FoodLog ADD COLUMN {col} {defn}')

    food_override_cols = {r[1] for r in db.execute('PRAGMA table_info(FoodOverride)').fetchall()}
    for col, defn in [
        ('imageUrl',        'TEXT'),
        ('nutriScoreGrade', 'TEXT'),
        ('fibre100g',       'REAL'),
    ]:
        if col not in food_override_cols:
            db.execute(f'ALTER TABLE FoodOverride ADD COLUMN {col} {defn}')

    cf_cols = {r[1] for r in db.execute('PRAGMA table_info(CustomFood)').fetchall()}
    if cf_cols and 'fibre100g' not in cf_cols:      # (cf_cols empty = brand-new DB; the table is created below with the column)
        db.execute('ALTER TABLE CustomFood ADD COLUMN fibre100g REAL')
    fc_cols = {r[1] for r in db.execute('PRAGMA table_info(FoodCache)').fetchall()}
    if fc_cols and 'fibre100g' not in fc_cols:
        db.execute('ALTER TABLE FoodCache ADD COLUMN fibre100g REAL')

    saved_meal_item_cols = {r[1] for r in db.execute('PRAGMA table_info(SavedMealItem)').fetchall()}
    if saved_meal_item_cols and 'fibre' not in saved_meal_item_cols:
        db.execute('ALTER TABLE SavedMealItem ADD COLUMN fibre REAL')

    db.execute('''
        CREATE TABLE IF NOT EXISTS WeightLog (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            riderId   INTEGER REFERENCES Rider(id),
            logDate   TEXT NOT NULL,
            weightKg  REAL NOT NULL,
            source    TEXT DEFAULT 'manual',
            createdAt TEXT DEFAULT (datetime('now')),
            UNIQUE(riderId, logDate)
        )
    ''')

    db.execute('''
        CREATE TABLE IF NOT EXISTS FoodFavourite (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            riderId   INTEGER REFERENCES Rider(id),
            barcode   TEXT,
            foodName  TEXT NOT NULL,
            brand     TEXT,
            calories  REAL,
            protein   REAL,
            carbs     REAL,
            fat       REAL,
            servingG  REAL,
            imageUrl  TEXT,
            createdAt TEXT DEFAULT (datetime('now'))
        )
    ''')

    db.execute('''
        CREATE TABLE IF NOT EXISTS CustomFood (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            riderId   INTEGER REFERENCES Rider(id),
            name      TEXT NOT NULL,
            brand     TEXT,
            calories  REAL,
            protein   REAL,
            carbs     REAL,
            fat       REAL,
            servingG  REAL,
            imageUrl  TEXT,
            createdAt TEXT DEFAULT (datetime('now'))
        )
    ''')

    db.execute('''
        CREATE TABLE IF NOT EXISTS RecipeBook (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            riderId   INTEGER REFERENCES Rider(id),
            name      TEXT NOT NULL,
            nameKey   TEXT NOT NULL,
            calories  REAL NOT NULL,
            protein   REAL,
            carbs     REAL,
            fat       REAL,
            createdAt TEXT DEFAULT (datetime('now')),
            updatedAt TEXT DEFAULT (datetime('now'))
        )
    ''')
    db.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_recipebook_key ON RecipeBook(riderId, nameKey)')

    db.execute('''
        CREATE TABLE IF NOT EXISTS BodyMetric (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            riderId   INTEGER REFERENCES Rider(id),
            logDate   TEXT NOT NULL,
            metric    TEXT NOT NULL,
            value     REAL NOT NULL,
            unit      TEXT,
            source    TEXT DEFAULT 'homeassistant',
            updatedAt TEXT DEFAULT (datetime('now')),
            UNIQUE(riderId, logDate, metric)
        )
    ''')

    db.execute('''
        CREATE TABLE IF NOT EXISTS FoodImage (
            nameKey   TEXT PRIMARY KEY,
            name      TEXT NOT NULL,
            imageUrl  TEXT NOT NULL,
            updatedAt TEXT DEFAULT (datetime('now'))
        )
    ''')

    # Walks / runs / hikes recorded by the phone app. Deliberately NOT in Activity: ~100 queries (ride stats, badges, charts, HA
    # sensors, segments, AI) assume every Activity is a ride, so keeping workouts separate leaves all of them correct by construction.
    db.execute('''
        CREATE TABLE IF NOT EXISTS Workout (
            id                 TEXT PRIMARY KEY,
            riderId            INTEGER NOT NULL,
            sport              TEXT NOT NULL,
            name               TEXT,
            startDate          TEXT,
            startDateLocal     TEXT NOT NULL,
            distance           REAL, movingTime INTEGER, elapsedTime INTEGER,
            totalElevationGain REAL, averageSpeed REAL, maxSpeed REAL, averageHeartrate REAL,
            calories           REAL,
            steps              INTEGER,
            startLat REAL, startLng REAL,
            streams            TEXT,
            weatherSummary     TEXT,
            source             TEXT,
            createdAt          TEXT DEFAULT (datetime('now'))
        )
    ''')
    db.execute('CREATE INDEX IF NOT EXISTS idx_workout_rider_date ON Workout(riderId, startDateLocal)')

    from services.duplicates import ensure_schema as _dup_schema
    _dup_schema(db)

    # One row per paired phone/app. Only a SHA-256 of the token is stored; the token itself is shown once at pairing time.
    db.execute('''
        CREATE TABLE IF NOT EXISTS DeviceToken (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT NOT NULL,
            tokenHash  TEXT NOT NULL UNIQUE,
            createdAt  TEXT DEFAULT (datetime('now')),
            lastUsedAt TEXT,
            revokedAt  TEXT
        )
    ''')

    # Phone-generated ids for queued offline writes, so a retried food/water log is applied once (see routes/nutrition.py log_food / add_water)
    db.execute('''
        CREATE TABLE IF NOT EXISTS ClientOp (
            clientId  TEXT PRIMARY KEY,
            kind      TEXT NOT NULL,
            entryId   INTEGER,
            createdAt TEXT DEFAULT (datetime('now'))
        )
    ''')

    # Foods friends' instances shared with us (their corrected products + barcode-less custom foods); see services/shared_foods.py
    db.execute('''
        CREATE TABLE IF NOT EXISTS SharedFood (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            friendId     INTEGER NOT NULL,
            origin       TEXT,
            kind         TEXT NOT NULL,
            key          TEXT NOT NULL,
            barcode      TEXT,
            name         TEXT NOT NULL,
            brand        TEXT,
            kcal100g     REAL, protein100g REAL, carbs100g REAL, fat100g REAL, fibre100g REAL,
            servingG     REAL,
            imageUrl     TEXT,
            nutriScoreGrade TEXT,
            srcUpdatedAt TEXT,
            syncedAt     TEXT,
            UNIQUE (friendId, kind, key)
        )
    ''')
    db.execute('CREATE INDEX IF NOT EXISTS idx_sharedfood_barcode ON SharedFood(barcode)')

    db.execute('''
        CREATE TABLE IF NOT EXISTS FoodCache (
            key             TEXT PRIMARY KEY,
            barcode         TEXT,
            name            TEXT NOT NULL,
            brand           TEXT,
            searchText      TEXT NOT NULL,
            kcal100g        REAL NOT NULL,
            protein100g     REAL,
            carbs100g       REAL,
            fat100g         REAL,
            sugars100g      REAL,
            fibre100g       REAL,
            servingG        REAL,
            imageUrl        TEXT,
            nutriScoreGrade TEXT,
            useCount        INTEGER DEFAULT 0,
            lastUsed        TEXT,
            fetchedAt       TEXT DEFAULT (datetime('now'))
        )
    ''')

    db.commit()

    # A brand-new DB gets these tables from the CREATE statements above, which predate some later columns, and the column checks
    # earlier in this function ran before the tables existed. Add anything still missing now that every table is there.
    for table, col, defn in (('CustomFood', 'fibre100g', 'REAL'), ('SavedMealItem', 'fibre', 'REAL'), ('FoodCache', 'fibre100g', 'REAL')):
        have = {r[1] for r in db.execute(f'PRAGMA table_info({table})').fetchall()}
        if have and col not in have:
            db.execute(f'ALTER TABLE {table} ADD COLUMN {col} {defn}')
    db.commit()

    # ── Backfill corrupted distance data (mm vs m) ──────────────────
    settings = db.execute("SELECT distanceBackfillDone FROM Settings WHERE id=1").fetchone()
    if not settings or not settings[0]:
        db.execute("""
            UPDATE Activity SET distance = distance / 1000
            WHERE distance > 500000
              AND (lower(sportType) LIKE '%walk%'
                OR lower(sportType) LIKE '%hike%'
                OR lower(sportType) LIKE '%run%')
        """)
        db.execute("UPDATE Settings SET distanceBackfillDone=1 WHERE id=1")
        db.commit()


def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(
            current_app.config['DATABASE'],
            detect_types=sqlite3.PARSE_DECLTYPES,
            timeout=60,
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
