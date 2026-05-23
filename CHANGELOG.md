# Changelog

All notable changes to Headwind are documented here.

## [1.0.0] - 2026-05-23

Initial public release.

### Core
- Flask + SQLite self-hosted cycling analytics, runs on Raspberry Pi 4 or any Docker host
- Session-based auth with configurable username/password via `.env`
- First-run setup wizard; backup/restore via `.zip` or raw `.db`
- UK date formatting throughout; all values stored in SI units

### Ride tracking
- Strava OAuth sync — full activity history, automatic webhook sync on ride finish
- File import — `.fit`, `.gpx`, or full Strava export `.zip` with live SSE progress bar
- GPX export from any ride detail page
- Multi-rider support — separate profiles, stats, PRs, best efforts, and trophy case per rider

### P2P Friends (federated sync)
- Token-authenticated NDJSON feed endpoint (`GET /api/feed`) — no central server
- `GET /api/riders` endpoint for rider discovery
- Two-step add flow: Connect button probes remote instance and populates rider dropdown
- Same URL can be added multiple times targeting different riders
- Incremental sync via `?since=YYYY-MM-DD` — only new rides fetched after first full pull
- Segments shared both ways with "from [name]" attribution on leaderboards
- Auto-sync background thread — configurable interval (off / 15 / 30 / 60 / 120 min), default 15 min

### Segments
- Define custom GPS segments on any ride map
- Retroactive scan against all historical rides on creation
- Per-rider PRs with combined leaderboard across all connected instances
- Effort history, 6-month trend charts, difficulty rating (Easy → Brutal)
- Polyline checkpoint matching prevents false positives on loop segments

### Analytics & data
- Speed over time, monthly distance, year-on-year comparison, activity heatmap
- Weather scatter charts — speed vs temperature, speed vs wind, speed by condition
- Best efforts at 5 / 10 / 20 / 30 / 50 / 100 miles per ride
- Trophy case — badges across 8 categories with milestone links

### Weather
- Automatic fetch on every sync and import via Open-Meteo (free, no API key)
- Headwind / tailwind / crosswind calculated from GPS route bearing
- Backfill button in Settings for historical rides

### AI coaching (optional)
- GPT-4o / GPT-4o-mini or local Ollama model — uses your own key
- Deliberately blunt tone; auto-generated on webhook sync
- Prompt includes ride stats, weather, segment comparisons, similar-ride history, Garmin recovery data

### Recovery — Garmin Connect (optional)
- Resting HR, HRV, sleep score, body battery pulled from Garmin Connect
- 30 / 60 / 90-day charts; recovery context fed into AI coaching prompt

### Home Assistant (optional)
- 17 MQTT sensors published automatically every 20 seconds
- Ride notifications via HA companion app, routed per-rider via `haDevice`

### GPS heatmap
- Full-history heatmap with date range filter
- HD PNG export at 3440×1440 with CARTO dark basemap

### Settings
- Version display with one-click update check against Docker Hub
- DB backup / restore, weather backfill, Strava webhook management
