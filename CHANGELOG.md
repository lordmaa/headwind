# Changelog

All notable changes to Headwind are documented here.

## [1.0.10] - 2026-05-23

### Fixed
- Weather backfill now runs in a background thread — clicking "Backfill All Weather" returns immediately and polls progress every 2 s; navigating away no longer aborts the backfill or locks you into the settings page

## [1.0.9] - 2026-05-23

### Fixed
- Friend sync and Garmin import: all NOT NULL numeric columns (distance, movingTime, elapsedTime, totalElevationGain, averageSpeed) now fall back to 0 instead of crashing on null values from walking/non-GPS activities

## [1.0.8] - 2026-05-23

### Fixed
- Friend sync: maxSpeed null from Garmin-imported rides no longer crashes insert (falls back to 0)
- Friend sync: commit every 50 rides instead of 500 — releases SQLite write lock more often, reducing contention with concurrent Garmin sync thread
- SQLite busy timeout increased from 30 s → 60 s

## [1.0.7] - 2026-05-23

### Fixed
- Garmin import now uses a blacklist (skip gym/yoga/strength) instead of a whitelist — walks, hikes, and any other outdoor type with an unusual Garmin typeKey are no longer missed

## [1.0.6] - 2026-05-23

### Fixed
- Garmin activity import no longer fails with NOT NULL constraint on maxSpeed — value derived from velocity stream, falls back to 0

## [1.0.5] - 2026-05-23

### Added
- Garmin activities now auto-sync on the same schedule as recovery data — no manual trigger needed after first sync

## [1.0.4] - 2026-05-23

### Added
- Garmin Connect activity import — pulls full FIT files (GPS, HR, power, cadence) for all outdoor activity types; incremental after first sync; weather fetched automatically on import
- "Sync Activities" button in Settings alongside the existing recovery sync button

## [1.0.3] - 2026-05-23

### Fixed
- Weather data now included in the P2P friend feed — no local backfill needed for friend rides
- Weather backfill batch reduced to 20 rides per request, preventing gunicorn worker timeout on Pi hardware
- SQLite busy timeout set to 30 s — background auto-sync and foreground requests no longer race to "database is locked"

## [1.0.2] - 2026-05-23

### Fixed
- Friend sync: Activity.rawData NOT NULL constraint failure on instances with older DB schemas

## [1.0.1] - 2026-05-23

### Fixed
- Feed token copy button now works on HTTP (non-HTTPS) origins using execCommand fallback
- Token regeneration and initial token generation no longer silently fail on fresh installs with no Settings row

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
