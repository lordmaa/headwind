# bike-flask

A self-hosted cycling dashboard built with Flask and SQLite. Syncs from Strava, supports manual FIT/GPX imports, and runs on your own hardware.

## Quick start

No clone needed — just grab two files:

```bash
curl -O https://raw.githubusercontent.com/lordmaa/bike-flask/main/docker-compose.yml
curl -O https://raw.githubusercontent.com/lordmaa/bike-flask/main/.env.example

mv .env.example .env
# Edit .env — set SECRET_KEY, APP_USERNAME, APP_PASSWORD

docker compose up -d
# → http://localhost:5001
```

Docker pulls the pre-built image automatically. The database is created fresh on first run and stored in a named volume so it survives restarts and upgrades.

Strava, AI coaching, MQTT, and Garmin are all optional — you can import `.fit` / `.gpx` files straight away without any API keys. The app seeds two placeholder riders on a fresh install; rename them under **Riders** in the UI.

### Minimum `.env`

| Variable | Description |
|---|---|
| `SECRET_KEY` | Random string — `python3 -c "import secrets; print(secrets.token_hex(32))"` |
| `APP_USERNAME` | Login username |
| `APP_PASSWORD` | Login password |

Everything else (Strava, OpenAI, MQTT, Garmin) is optional and documented in `.env.example`.

### Portainer

Deploy as a Git stack pointing at this repo. The `.env` file is optional — set `SECRET_KEY`, `APP_USERNAME`, and `APP_PASSWORD` in Portainer's **Environment variables** section instead.

### Build from source

```bash
git clone https://github.com/lordmaa/bike-flask.git
cd bike-flask
cp .env.example .env
docker compose -f docker-compose.dev.yml up --build
```

### Run without Docker

```bash
pip install -r requirements.txt
cp .env.example .env
python3 app.py  # port 5001
```

## Features

### Ride Tracking
- **Strava sync** — full activity history via OAuth, automatic webhook sync when you finish a ride
- **File import** — drag-and-drop `.fit`, `.gpx`, or zipped Strava export; live progress stream
- **GPX export** — download any ride as a GPX file from the ride page
- **Multi-rider** — multiple riders share one login; each has their own profile, stats, and PRs

### Ride Detail
- Interactive Leaflet map with GPS track
- Elevation, heart rate, power, cadence, and speed stream charts
- Wind direction arrows overlaid on the map, colour-coded by strength
- Segment efforts and PRs for that ride
- Co-rider badges for others who rode the same day
- AI coaching analysis (on-demand or auto on webhook sync)

### Analytics
- Speed over time with 20-ride rolling average
- Monthly distance, year-on-year comparison, activity heatmap
- Ride length distribution, rides by day of week
- Calories over time
- **Weather performance scatter charts** — speed vs temperature (by season), speed vs wind (by headwind/tailwind/crosswind), average speed by weather condition; each dot links to that ride

### Achievements
- Best efforts at 5, 10, 20, 30, 50, and 100 miles — fastest continuous stretch per ride
- Year and month filter dropdowns
- AI coaching prompt includes any records set on the current ride

### Segments
- Define custom GPS segments on any ride map — click start, click end, name it
- Retroactive scan against all historical rides on creation
- Per-rider PRs, leaderboard, effort history, difficulty rating (Easy → Brutal)
- 6-month trend charts per segment

### Weather
- Automatic weather fetch on every sync and file import via Open-Meteo (free, no API key)
- Conditions stored per ride: temperature, wind speed/direction/gusts, humidity, rain, WMO code
- Wind relation (headwind/tailwind/crosswind/calm) calculated from GPS route bearing
- Backfill button in Settings to fetch weather for historical rides without it

### AI Coaching
- GPT-4o / GPT-4o-mini or local Ollama model
- Prompt includes ride stats, weather conditions, segment comparisons (vs recent/best/first effort), similar-ride history, and Garmin recovery data
- Deliberately blunt tone — told to say when a ride was poor, not to encourage
- Auto-generated on new rides via Strava webhook; manually triggered on older rides

### Recovery (Garmin Connect)
- Pulls resting HR, HRV, sleep score, and body battery from Garmin Connect
- 30/60/90-day charts for each metric
- Recovery context fed into the AI coaching prompt for the ride's date

### Home Assistant Integration
- **17 MQTT sensors** — total rides, distance, elevation, calories, time, Everests climbed, laps of Earth, last ride details
- **Auto-updates every 20 seconds** via background heartbeat thread
- **Ride notifications** — tappable HA companion app notification fires when a new ride syncs, links directly to the ride page; routed per-rider (Rob → Pixel 10, Smiffy → Pixel 8)
- Manual "Publish to HA" button in Settings

### GPS Heatmap
- Full-history GPS heatmap with date range filter
- HD export at 3440×1440 PNG with CARTO dark basemap

### Other
- Dashboard search and filter — by name, date range, distance, sport type
- Dark ocean theme throughout
- UK date format (`15 May 2026`) everywhere
- ntfy.sh push notifications for new rides and sync errors
- Strava webhook subscription management in Settings

## Stack

- **Backend** — Python / Flask, SQLite
- **Frontend** — Vanilla JS, Chart.js 4.4, Leaflet 1.9
- **Data sources** — Strava API, Open-Meteo, Garmin Connect (unofficial)
- **Integrations** — Home Assistant via MQTT, ntfy.sh
