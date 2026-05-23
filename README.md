# Headwind

**Your rides. Your hardware. Your friends.**

Self-hosted cycling analytics with no cloud, no subscription, and no one else touching your data. Run it solo on a Raspberry Pi or connect with friends directly — instance to instance, no central server involved.

## Why Headwind

Most cycling apps own your data. Headwind doesn't exist in the cloud — it runs on your hardware, your network, and speaks only to services you explicitly configure.

- **No subscription** — free forever, self-hosted
- **No cloud middleman** — your rides stay on your machine
- **No lock-in** — import from Strava, FIT/GPX files, or a full Strava export zip; export any ride as GPX
- **P2P social** — connect directly to a friend's instance and share segment leaderboards, no central server required
- **AI coaching** — optional, uses your own API key or a local Ollama model; nothing phoned home without your config

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

Docker pulls the pre-built image automatically. Data is stored as bind mounts under `/portainer/files/appdata/config/headwind` so it survives restarts and upgrades.

Strava, AI, MQTT, and Garmin are all optional — you can import `.fit` / `.gpx` files straight away without any API keys.

### Minimum `.env`

| Variable | Description |
|---|---|
| `SECRET_KEY` | Random string — `python3 -c "import secrets; print(secrets.token_hex(32))"` |
| `APP_USERNAME` | Login username |
| `APP_PASSWORD` | Login password |

Everything else (Strava, OpenAI, MQTT, Garmin) is optional and documented in `.env.example`.

### Portainer

Deploy as a Git stack pointing at this repo. Set `SECRET_KEY`, `APP_USERNAME`, and `APP_PASSWORD` in Portainer's **Environment variables** section.

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

### Friends — P2P between instances
The social layer that makes Headwind different. Each instance exposes a token-authenticated feed endpoint. Add a friend's URL and token, pick which rider on their instance to follow, and their rides sync directly into your local database. Segment leaderboards update automatically to include both riders. No account, no relay server, no third party.

- Connect to any Headwind instance by URL + feed token
- Multi-rider support — one URL can be added multiple times targeting different riders
- Auto-sync every 15 minutes by default (configurable: off / 15 / 30 / 60 / 120 min)
- Incremental — only fetches rides newer than last sync after the first full pull
- Segments shared both ways — their segments appear on your leaderboards with attribution
- Runs over LAN, VPN, or public WAN — your choice

### Segments
- Define custom GPS segments on any ride map — click start, click end, name it
- Retroactive scan against all historical rides on creation
- Per-rider PRs with combined leaderboard across all connected instances
- Effort history, 6-month trend charts, difficulty rating (Easy → Brutal)

### Ride tracking
- **Strava sync** — full activity history via OAuth, automatic webhook sync on ride finish
- **File import** — `.fit`, `.gpx`, or full Strava export zip with live progress bar
- **Multi-rider** — separate profiles, stats, PRs, best efforts, and trophy case per rider
- **GPX export** — download any ride from the ride detail page

### Ride detail
- Satellite map with elevation, HR, power, cadence, and speed stream charts
- Wind direction overlaid on the map, colour-coded by strength
- Segment efforts and PRs for that ride
- Co-rider badges for others who rode the same day

### Analytics
- Speed over time with rolling average, monthly distance, year-on-year comparison
- Ride length distribution, rides by day of week, activity heatmap
- Weather scatter charts — speed vs temperature, speed vs wind, speed by condition
- Per-rider switcher throughout

### Trophy case
- Badges across 8 categories: Ride Count, Distance, Elevation, Epic Rides, Speed, Climbing, Weather, Segments
- Milestone badges link to the ride that earned them
- Weather badges for cold, hot, rain, storms, headwinds — with personal records

### Best efforts
- Fastest continuous stretch at 5, 10, 20, 30, 50, and 100 miles per ride
- Year and month filters; records fed into the AI coaching prompt

### Recovery (Garmin Connect)
- Resting HR, HRV, sleep score, and body battery from Garmin Connect
- 30/60/90-day charts; recovery data fed into AI coaching context

### Weather
- Automatic fetch on every sync and import via Open-Meteo (free, no API key)
- Temperature, wind, humidity, rain, WMO condition code
- Headwind/tailwind/crosswind calculated from GPS route bearing
- Backfill button for historical rides

### AI coaching (optional)
- GPT-4o / GPT-4o-mini or local Ollama — uses your own key, nothing phoned home by default
- Prompt includes ride stats, weather, segment comparisons, similar-ride history, and Garmin recovery
- Deliberately blunt tone — told to say when a ride was poor, not to encourage
- Auto-generated on webhook sync; manually triggered on older rides

### GPS heatmap
- Full-history heatmap with date range filter
- HD export at 3440×1440 PNG

### Home Assistant
- 17 MQTT sensors — distance, elevation, calories, Everests climbed, laps of Earth, last ride details
- Ride notifications via HA companion app, routed per-rider
- Auto-updates every 20 seconds

## Stack

- **Backend** — Python / Flask, SQLite
- **Frontend** — Vanilla JS, Chart.js 4.4, Leaflet 1.9
- **Data sources** — Strava API, Open-Meteo, Garmin Connect
- **Integrations** — Home Assistant via MQTT, ntfy.sh
