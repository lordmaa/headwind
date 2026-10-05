# Changelog

## [Unreleased]

### Added
- **Gear: bikes, parts and service** (new sidebar item, guide in `docs/GEAR.md`). Add bikes with photos and a default bike per rider (new rides get it automatically, whichever way they arrive),
  assign older rides by **calendar** (click days, shift-click, or a from/to range such as "everything from 1 March to today", with a preview of exactly what will change), and pick the bike on any
  ride from a dropdown. Per-bike odometer and stats. Track **replaceable parts** (chain, cassette, chainrings, tyres, tubeless sealant, brake pads/rotors/fluid, cables, bar tape, headset and wheel
  bearings, bottom bracket, cleats, suspension service, or your own) with editable check/replace intervals by distance and/or days, a **service log** (replacing a part retires it and fits a fresh one),
  and soon/due/overdue **alerts** (ntfy + Home Assistant push, once per level, plus a dashboard card). Mileage is computed from your rides, so correcting a ride's bike corrects every part.
  New tables `Bike`, `Part`, `ServiceLog`, `PartAlert`; `Activity.bikeId`, `Rider.defaultBikeId`. Bike photos live in `bikeimg/` beside the database and are part of backup/restore.
- **The same ride recorded twice is now handled.** If a ride arrives from two devices (say the phone app AND a Garmin), Headwind keeps the better
  recording (Garmin device > heart rate > power > cadence > GPS detail) and parks the other. Nothing is deleted: the ride page shows an
  "also recorded by another device" card with *Use that recording instead*, *They are different rides - keep both* and *Delete the other
  recording*. Parked rides live in `ActivityDuplicate`, so every existing stat, chart and sensor still sees exactly one ride. Matching is on
  the real UTC instant plus time overlap and distance (within 15%), never on local-time strings. Notes you typed on the parked ride are
  copied to the kept one. Existing history can be checked with `python3 scripts/resolve_existing_duplicates.py` (dry run by default).
- **Add an activity by hand** (Workouts page, and the phone app): a ride you forgot to record, a turbo session, a walk without GPS. Rides land in
  `Activity` (no map), walks/runs/hikes in `Workout`. If a real recording of the same ride turns up later it wins and the typed-in copy is parked.
- **Delete from the Workouts page** (and from the phone app, for rides and workouts). A deleted ride is remembered so a Garmin re-sync does not
  bring it back; uploading the file yourself can.

### Changed
- Phone/GPX recordings now store the UTC instant in `startDate` and the home-timezone local time in `startDateLocal` (they used to store the UTC
  time as "local", an hour out in summer, which is how the duplicate above went unnoticed). Old rows are untouched; the duplicate script can
  fix chosen ones with `--ids`.
- The Garmin sync no longer skips its copy of a ride just because a phone/file copy exists; it keeps whichever recording is better.

### Fixed
- **Max speed on imported rides was the average speed.** Imports (FIT/GPX files, Strava export, phone uploads) now store the real max
  speed, and also max heart rate and max power. Existing rides can be repaired with `python3 scripts/backfill_max_stats.py` (dry run by
  default; `--apply` to write; only rows showing the old bug are touched).
- **FIT files from newer devices** that write only `enhanced_altitude` / `enhanced_speed` no longer lose elevation and speed.
- **GPX power** (heart-rate-style extension tags) is now read.
- **Headwind/tailwind on loop rides.** The label used one start-to-finish bearing, which is meaningless for a loop. It is now judged over
  the whole route; loops that get both are labelled "Mixed". Existing labels are left as they were (`--rewind-wind` on the backfill
  script re-labels them, which can change badge counts).

## [0.1.0-beta] - 2026-10-03

First public beta of Headwind — a self-hosted cycling, nutrition and body-tracking server. An Android companion app is in development (work in progress, not yet released).
The project history was reset for this release; earlier internal version numbers (1.x) no longer apply.

### Highlights
- Ride import (FIT/GPX/Strava export zip), Garmin Connect sync (MFA-capable), analytics, heatmaps, custom segments,
  best efforts, badges, route planning.
- Nutrition: food logging (Open Food Facts, custom foods, recipes, saved meals), hydration, weight trends and goals.
- Optional: AI coaching (your own OpenAI-compatible key or local Ollama), Home Assistant / MQTT sensors, P2P friend feeds.
- Phone API (paired per-device tokens) used by the Android app, which is still a work in progress.

### Security notes
- **No default login.** If `SECRET_KEY` / `APP_PASSWORD` are unset (or still placeholders), a random signing key and admin
  password are generated on first start and stored in your data directory; the password is printed once in the logs
  (`docker logs headwind`). Set your own in `.env` if you prefer.
- Backup restore migrates and smoke-tests the uploaded database before touching live data, then swaps it in atomically; uploads can't choose file paths. Backups contain the credentials you configured in Settings — keep them private.
- A password changed in Settings now persists across container recreation (unless you later edit the credentials in `.env`).
- Login throttling, session invalidation on password change, cross-site request blocking, input size limits.
- One optional, consent-gated, anonymous install ping (random id + version only) — opt out in the setup wizard or with
  `TELEMETRY=off`.

### Platforms
- Docker (amd64 + arm64) and an experimental Windows build (`Headwind-windows-x64.zip`, see the README).

### One person per instance
- New installs track one person (the setup wizard asks for one name; the Riders page becomes a profile). A second person runs their own instance and links as a friend. `HEADWIND_MULTI_RIDER=1` (or an install that already had several local riders) keeps the multi-rider UI.

### Known limitations (beta)
- One shared admin login; in the advanced multi-rider mode phone tokens are not scoped per rider.
- Admin password is stored in plaintext in your data directory / `.env` (file mode 600 for generated ones).
- The Android app is a work in progress — expect rough edges and no public release yet.
- The arm64 (Raspberry Pi) image is new and has had little real-world testing. The Windows build is experimental and unsigned.
