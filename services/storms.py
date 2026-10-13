"""UK & Ireland named storms (Met Office / Met Éireann / KNMI naming scheme), and which of a rider's rides fell on a storm day.

NAMED_STORMS is a reference table copied from the Met Office's own season archive pages (weather.metoffice.gov.uk/warnings-and-advice/uk-storm-centre/
uk-storm-season-YYYY-YY), 2015/16 to 2025/26: (name, first day of UK impact, last day of UK impact). A named storm is an official warning-level event,
so a ride on one of these days is worth a second look; but a storm is regional, so a ride on the day is only a *candidate* until the rider confirms it.
Add new storms to the end of the list as the Met Office names them.
"""
NAMED_STORMS = [
    ('Abigail', '2015-11-12', '2015-11-13'), ('Barney', '2015-11-17', '2015-11-18'), ('Clodagh', '2015-11-29', '2015-11-29'), ('Desmond', '2015-12-05', '2015-12-06'),
    ('Eva', '2015-12-24', '2015-12-24'), ('Frank', '2015-12-29', '2015-12-30'), ('Gertrude', '2016-01-29', '2016-01-29'), ('Henry', '2016-02-01', '2016-02-02'),
    ('Imogen', '2016-02-08', '2016-02-08'), ('Jake', '2016-03-02', '2016-03-02'), ('Katie', '2016-03-27', '2016-03-28'),
    ('Angus', '2016-11-20', '2016-11-20'), ('Barbara', '2016-12-23', '2016-12-24'), ('Conor', '2016-12-25', '2016-12-26'), ('Doris', '2017-02-23', '2017-02-23'), ('Ewan', '2017-02-26', '2017-02-26'),
    ('Aileen', '2017-09-12', '2017-09-13'), ('Ophelia', '2017-10-16', '2017-10-17'), ('Brian', '2017-10-21', '2017-10-21'), ('Caroline', '2017-12-07', '2017-12-07'),
    ('Dylan', '2017-12-30', '2017-12-31'), ('Eleanor', '2018-01-02', '2018-01-03'), ('Fionn', '2018-01-16', '2018-01-16'), ('David', '2018-01-18', '2018-01-18'),
    ('Georgina', '2018-01-24', '2018-01-24'), ('Hector', '2018-06-13', '2018-06-14'),
    ('Ali', '2018-09-19', '2018-09-19'), ('Bronagh', '2018-09-20', '2018-09-21'), ('Callum', '2018-10-12', '2018-10-13'), ('Deirdre', '2018-12-15', '2018-12-16'),
    ('Erik', '2019-02-08', '2019-02-09'), ('Freya', '2019-03-03', '2019-03-04'), ('Gareth', '2019-03-12', '2019-03-13'), ('Hannah', '2019-04-27', '2019-04-27'),
    ('Atiyah', '2019-12-08', '2019-12-09'), ('Brendan', '2020-01-13', '2020-01-14'), ('Ciara', '2020-02-08', '2020-02-09'), ('Dennis', '2020-02-15', '2020-02-16'),
    ('Jorge', '2020-02-28', '2020-03-01'), ('Ellen', '2020-08-19', '2020-08-20'), ('Francis', '2020-08-25', '2020-08-25'),
    ('Alex', '2020-10-02', '2020-10-04'), ('Barbara', '2020-10-21', '2020-10-21'), ('Aiden', '2020-10-31', '2020-10-31'), ('Bella', '2020-12-26', '2020-12-27'),
    ('Christoph', '2021-01-19', '2021-01-22'), ('Darcy', '2021-02-06', '2021-02-08'), ('Evert', '2021-07-30', '2021-07-30'),
    ('Arwen', '2021-11-26', '2021-11-27'), ('Barra', '2021-12-07', '2021-12-08'), ('Malik', '2022-01-29', '2022-01-29'), ('Corrie', '2022-01-30', '2022-01-31'),
    ('Dudley', '2022-02-16', '2022-02-17'), ('Eunice', '2022-02-18', '2022-02-18'), ('Franklin', '2022-02-20', '2022-02-21'),
    ('Otto', '2023-02-17', '2023-02-17'), ('Noa', '2023-04-12', '2023-04-12'), ('Antoni', '2023-08-05', '2023-08-05'), ('Betty', '2023-08-18', '2023-08-19'),
    ('Agnes', '2023-09-27', '2023-09-28'), ('Babet', '2023-10-18', '2023-10-21'), ('Ciarán', '2023-11-01', '2023-11-02'), ('Debi', '2023-11-13', '2023-11-13'),
    ('Elin', '2023-12-09', '2023-12-09'), ('Fergus', '2023-12-10', '2023-12-10'), ('Gerrit', '2023-12-27', '2023-12-28'), ('Henk', '2024-01-02', '2024-01-02'),
    ('Isha', '2024-01-21', '2024-01-22'), ('Jocelyn', '2024-01-23', '2024-01-24'), ('Kathleen', '2024-04-06', '2024-04-07'), ('Lilian', '2024-08-22', '2024-08-23'),
    ('Ashley', '2024-10-20', '2024-10-21'), ('Bert', '2024-11-22', '2024-11-25'), ('Conall', '2024-11-27', '2024-11-27'), ('Darragh', '2024-12-06', '2024-12-07'),
    ('Éowyn', '2025-01-24', '2025-01-24'), ('Floris', '2025-08-04', '2025-08-05'),
    ('Amy', '2025-10-03', '2025-10-04'), ('Benjamin', '2025-10-22', '2025-10-23'), ('Claudia', '2025-11-14', '2025-11-14'), ('Bram', '2025-12-08', '2025-12-10'),
    ('Goretti', '2026-01-08', '2026-01-09'), ('Chandra', '2026-01-26', '2026-01-27'), ('Dave', '2026-04-04', '2026-04-05'),
]


def storm_on(day):
    """Names of the named storms affecting the UK on 'YYYY-MM-DD' (usually zero or one)."""
    return [n for n, a, b in NAMED_STORMS if a <= day <= b]


def storm_rides(db, rider_id, sport=None):
    """Rides that fell on a named-storm day, newest first: [{id, name, date, storm, distance, avg_mph, wind_kph, gust_kph, condition, confirmed}].
    `confirmed` = the rider has already settled the condition by hand (so it is not re-suggested)."""
    from services import weather
    rows = db.execute("""SELECT id, name, startDateLocal, distance, averageSpeed, weatherWindKph, weatherGustKph, weatherCode, weatherSource
                         FROM Activity WHERE riderId=? AND startDateLocal IS NOT NULL ORDER BY startDateLocal DESC""", [rider_id]).fetchall()
    out = []
    for r in rows:
        day = str(r['startDateLocal'])[:10]
        names = storm_on(day)
        if not names:
            continue
        out.append({'id': r['id'], 'name': r['name'], 'date': day, 'storm': ' / '.join(names), 'distance': r['distance'] or 0,
                    'avg_mph': round((r['averageSpeed'] or 0) * 2.23694, 1), 'wind_kph': r['weatherWindKph'], 'gust_kph': r['weatherGustKph'],
                    'condition': weather.bucket(r['weatherCode']), 'confirmed': r['weatherSource'] == 'manual'})
    return out


MIN_GUST_KPH = 50      # a named storm only counts for a ride if gusts of this size (about 31 mph) were felt at the ride's own place and hour. On the ordinary windy days of one
                       # rider's history, 40-49 km/h gusts are common (about 16% of rides) and 50+ much rarer, so 50 separates "windy" from "storm" on a storm day
MAYBE_GUST_KPH = 40    # a named-storm day with gusts from here to MIN_GUST_KPH is only suggested, never claimed
VIOLENT_GUST_KPH = 75  # gusts this strong (severe gale) with no named storm are suggested too: before 2015 storms were not named by the Met Office


def claim(db, act_id):
    """Called when a ride's weather is saved: a ride on a named-storm day that had real gusts is a Storm ride, whatever the lookup called it (Rain, Snow, Cloudy...), because the
    most extreme weather decides the condition. Skips rides the rider has settled by hand. Returns True if it claimed the ride. Does not commit."""
    from services import weather
    r = db.execute('SELECT startDateLocal, weatherGustKph, weatherSource FROM Activity WHERE id=?', [str(act_id)]).fetchone()
    if not r or r['weatherSource'] in ('manual', 'storm') or not r['startDateLocal']:
        return False
    names = storm_on(str(r['startDateLocal'])[:10])
    if not names or (r['weatherGustKph'] or 0) < MIN_GUST_KPH:
        return False
    weather.set_condition(db, act_id, 'Storm', note='Storm ' + names[0], source='storm')
    return True


def claim_all(db, rider_id):
    """Apply the rule to every ride (history). Returns the number claimed. Commits."""
    n = 0
    for r in db.execute('SELECT id FROM Activity WHERE riderId=? AND weatherGustKph IS NOT NULL', [rider_id]).fetchall():
        if claim(db, r['id']):
            n += 1
    db.commit()
    return n


def reassess(db, rider_id):
    """Take back rides an earlier, looser rule claimed (gusts under MIN_GUST_KPH): back to the lookup's own condition. Needs the weather service for each (a few calls). Commits."""
    from services import weather
    n = 0
    for r in db.execute("SELECT id, startLat, startLng, startDateLocal, streams, weatherGustKph FROM Activity WHERE riderId=? AND weatherSource='storm' AND COALESCE(weatherGustKph,0) < ?",
                        [rider_id, MIN_GUST_KPH]).fetchall():
        weather.set_condition(db, r['id'], None)
        lat, lng = weather.origin(r)
        w = weather.fetch_weather(lat, lng, r['startDateLocal'], r['streams']) if lat is not None else None
        if w:
            weather.save_weather(db, r['id'], w, claim=False)
        n += 1
    db.commit()
    return n


def violent_unnamed(db, rider_id):
    """Rides with severe-gale gusts on a day with no named storm (candidates only)."""
    out = []
    for r in db.execute("""SELECT id, name, startDateLocal, distance, weatherWindKph, weatherGustKph, weatherCode, weatherSource FROM Activity
                           WHERE riderId=? AND weatherGustKph >= ? ORDER BY startDateLocal DESC""", [rider_id, VIOLENT_GUST_KPH]).fetchall():
        day = str(r['startDateLocal'])[:10]
        if storm_on(day) or r['weatherSource'] in ('manual', 'storm'):
            continue
        out.append({'id': r['id'], 'name': r['name'], 'date': day, 'storm': 'violent wind, no named storm', 'distance': r['distance'] or 0, 'wind_kph': r['weatherWindKph'],
                    'gust_kph': r['weatherGustKph'], 'condition': None, 'confirmed': False})
    return out
