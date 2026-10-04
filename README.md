# Headwind

[![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20A%20Coffee-Support-FFDD00?style=for-the-badge&logo=buy-me-a-coffee&logoColor=black)](https://buymeacoffee.com/lordmerchant)

**Your rides. Your food. Your hardware. Your friends.**

Self-hosted cycling analytics and nutrition tracking with no cloud, no subscription, and no one else touching your data. Run it solo on your own server or connect with friends directly — instance to instance, no central server involved.

## Why Headwind

Most cycling apps own your data. Headwind doesn't exist in the cloud — it runs on your hardware, your network, and speaks only to services you explicitly configure.

- **No subscription** — free forever, self-hosted
- **No cloud middleman** — your rides stay on your machine
- **No lock-in** — import FIT/GPX files or a Strava data-export zip; export any ride as GPX
- **P2P social** — connect directly to a friend's instance and share segment leaderboards, no central server required
- **AI coaching** — optional, uses your own API key or a local Ollama model; nothing phoned home without your config (a single one-time anonymous install ping is the one exception — permanently opt-out-able in the setup wizard, see Telemetry below)

## Quick start

No clone needed — just grab two files:

```bash
curl -O https://raw.githubusercontent.com/lordmaa/headwind/main/docker-compose.yml
curl -O https://raw.githubusercontent.com/lordmaa/headwind/main/.env.example

docker compose up -d
# → http://localhost:5001
docker logs headwind 2>&1 | grep -A3 "generated one"   # first-login username + password
```

Optional: `mv .env.example .env` and set `SECRET_KEY`, `APP_USERNAME`, `APP_PASSWORD` before `docker compose up` to choose your own. If you leave them blank (or at the example placeholders), Headwind generates a random signing key and admin password on first start and stores them in `./data`.

Docker pulls the pre-built image automatically (currently **amd64 only** — Raspberry Pi / ARM images are not published yet). Data lives in `./data` (database, avatars, food photos, generated login) and `./garmin_tokens` next to your `docker-compose.yml`, so it survives restarts and upgrades.

First run takes you through a setup wizard. AI, MQTT, and Garmin are all optional — you can import `.fit` / `.gpx` files straight away without any of them configured.

### Minimum `.env`

| Variable | Description |
|---|---|
| `SECRET_KEY` | Optional — random string; generated and stored in `data/` if blank |
| `APP_USERNAME` | Login username (default `admin`) |
| `APP_PASSWORD` | Optional — generated and printed once in the logs if blank |

Everything else (OpenAI, MQTT, Garmin) is optional and documented in `.env.example`.

## Windows (experimental)

Download `Headwind-windows-x64.zip` from the [Releases](../../releases) page, extract it anywhere (not Program Files), and run `Headwind\Headwind.exe`. A console window opens and shows your first-login username and password, then your browser opens at `http://localhost:5001`. Keep the window open while you use Headwind; close it to stop.

- Your data lives in `%LOCALAPPDATA%\Headwind` (database, photos, generated login, Garmin tokens) and survives updates — just replace the extracted folder.
- It listens on this PC only by default. To reach it from a phone on your network, set the environment variable `HEADWIND_HOST=0.0.0.0` before launching (Windows will ask about the firewall).
- The build is **unsigned**, so Windows SmartScreen shows "unknown publisher" (More info → Run anyway) and some antivirus tools flag PyInstaller apps. If that worries you, use Docker or run from source.

## Backups

**Settings → Backup** downloads a zip with your database, rider avatars and food photos; restoring it (Settings, or the setup wizard on a fresh install) replaces the current data after an integrity check and keeps a `.pre-restore` copy of what was there. It does **not** include the Garmin login-token files, your admin password/signing key (`./data/.admin_login`, `./data/.secret_key`) or `.env` — but the database itself holds anything you configured in Settings (Garmin email/password, AI/OpenAI key, MQTT and Home Assistant credentials), so **a backup file contains those credentials plus your health and GPS history. Treat it as a secret.** On a new host you'll re-enter your Garmin login and set a password.

## Phone app over HTTPS

The Android app is a work in progress. It only talks to HTTPS servers. Put Headwind behind a TLS reverse proxy (Caddy, nginx, Cloudflare Tunnel, Tailscale serve) before pairing a phone; plain `http://localhost:5001` is fine for the browser UI.

## Telemetry

On first setup, Headwind sends **one single, one-time, anonymous ping** — a random install id and the version
number, nothing else — so we have a rough idea how many instances exist. No rides, food, weight, location, or
anything else is ever sent, then or later. The setup wizard shows exactly this text and a permanent opt-out
toggle before it happens; `TELEMETRY=off` in the `.env` next to your `docker-compose.yml` skips asking entirely. The code is in `services/telemetry.py`
if you want to read exactly what it does rather than take our word for it.

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
python3 app.py  # port 5001 (set HEADWIND_DEBUG=1 for Flask debug mode — never on a network-reachable machine)
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
- **Garmin sync** — automatic activity + recovery sync, MFA-capable connect flow, no CLI needed
- **Phone app (work in progress)** — an Android app that records rides live and syncs them here; still in development, not publicly released
- **File import** — `.fit`, `.gpx`, or a Strava data-export zip with live progress bar
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
- **Activity type filtering** — all charts, heatmap, and dashboard stats filter by sport (rides, runs, walks, etc.)
- **Measurement units** — display preferences toggle between Imperial (mi, mph, ft) and Metric (km, km/h, m)
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

### Nutrition tracker (PWA)
- Installable Progressive Web App — add to home screen on iOS/Android, installable and fast to reopen (it needs a connection to your Headwind server — there is no offline mode in the web app)
- **Barcode scanner** — point your phone camera at any food packaging, pulls macros from Open Food Facts instantly (no API key)
- Text search across the Open Food Facts database as fallback
- Log meals across Breakfast / Lunch / Dinner / Snacks with custom serving sizes
- Daily calorie and macro targets (protein, carbs, fat) with ring charts showing progress
- Water intake logging
- Manual override to fix any incorrect Open Food Facts entries per barcode
- Calorie data pushed to Home Assistant as an MQTT sensor alongside your ride stats

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
- **Data sources** — Garmin Connect, Open-Meteo, Open Food Facts
- **Integrations** — Home Assistant via MQTT, ntfy.sh
- **PWA** — Nutrition tracker installable on iOS/Android with barcode scanning
