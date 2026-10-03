# Changelog

## [0.1.0-beta] - 2026-10-03

First public beta of Headwind — a self-hosted cycling, nutrition and body-tracking server with a companion Android app.
The project history was reset for this release; earlier internal version numbers (1.x) no longer apply.

### Highlights
- Ride import (FIT/GPX/Strava export zip), Garmin Connect sync (MFA-capable), analytics, heatmaps, custom segments,
  best efforts, badges, route planning.
- Nutrition: food logging (Open Food Facts, custom foods, recipes, saved meals), hydration, weight trends and goals.
- Optional: AI coaching (your own OpenAI-compatible key or local Ollama), Home Assistant / MQTT sensors, P2P friend feeds.
- Phone API for the Android app (paired per-device tokens).

### Security notes
- **No default login.** If `SECRET_KEY` / `APP_PASSWORD` are unset (or still placeholders), a random signing key and admin
  password are generated on first start and stored in your data directory; the password is printed once in the logs
  (`docker logs headwind`). Set your own in `.env` if you prefer.
- Backup restore validates the database and is applied atomically; uploads can't choose file paths.
- Login throttling, session invalidation on password change, cross-site request blocking, input size limits.
- One optional, consent-gated, anonymous install ping (random id + version only) — opt out in the setup wizard or with
  `TELEMETRY=off`.

### Known limitations (beta)
- One shared admin login; phone tokens are not scoped per rider.
- Admin password is stored in plaintext in your data directory / `.env` (file mode 600 for generated ones).
- Multi-arch (ARM) images are not yet published.
