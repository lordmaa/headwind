# Changelog

## [Unreleased]

### Fixed
- Start-up no longer rewrites the `SegActivity` view every time (it only does when it is missing or changed), so a start-up, a helper script or a backup running alongside a live instance can no longer hit "database is locked" there.

## [0.2.0-beta] - 2026-10-10

Upgrading is automatic: new columns and tables are added on start-up and nothing is dropped. Existing data is not changed unless you press
*Apply to history* on the calorie setting below.

### Added
- **Calorie burn adjustment** (Settings → *Calorie burn adjustment*). Burn figures from watches and formulas tend to read high, so you can take a
  percentage (0–50%) off every burn estimate **as it is pulled in**: Garmin/FIT/Strava-export rides, phone GPX rides, walks and runs, and Garmin's
  daily totals. Everything downstream (dashboards, MQTT/Home Assistant, the API, the phone app) then carries the corrected number. The original is
  kept alongside (`caloriesRaw`, and `totalCaloriesRaw`/`activeCaloriesRaw` on `GarminDaily`), so it is reversible: *Save and apply to history*
  re-works your own stored rides, walks/runs and Garmin totals from the originals, and can be re-run with a different percentage. Never touched:
  food calories, numbers you type in yourself, and rides received from friends (their own instance applies its own setting). Default is 0 (off).
  `/api/v1/today` now includes `calorie_adjust_pct` so the phone app applies the same figure to its live estimate.
- **Running: stats from your recorded runs.** Best times for 1 km, 1 mile, 3 km, 5 km, 10 km, 15 km, half and full marathon (the fastest stretch of
  that distance inside any one run, from the GPS, so a 10 km run also gives a 5 km time), pace, totals for this week/month/year/all time, 12 weeks of
  distance, recent runs, longest run, fastest pace and the latest route. Recordings under 400 m or 2 minutes are ignored. Runs come from the phone app
  (Workouts) or from a Garmin. Best times are cached per run in the new `RunEffort` table. Published to Home Assistant as
  `Nutrition Run Stats`, `Nutrition Run Route` and `Nutrition Run Segments` sensors.
- **Run segments.** Segments now have a sport: **ride** or **run**. Runs only match run segments and rides only match ride segments (a walk or hike
  has none), with a slower minimum speed for runs. Create a run segment from a run's page: phone-recorded runs and walks now open the same page as a
  ride (map, elevation, segments table, *Create Segment*), linked from each row on **Workouts**. The **Segments** page has *Ride segments* / *Run
  segments* tabs, and run segments show **pace per mile** instead of mph. Efforts and PRs work across runs from the phone and from a Garmin, a new run
  is matched as soon as it arrives, deleting a run or changing its type removes/recreates its efforts and re-picks the PRs, and run segments are shared
  with linked friends (older peers without the field treat everything as a ride). New `Segment.sport` column and a `SegActivity` view over rides and
  workouts for leaderboard queries.
- **Home Assistant: route map and segments.** New `Nutrition Ride Route` / `Nutrition Ride Segments` sensors carry the latest ride's track (thinned to
  fit an attribute) and the segments it crossed, with your time, rank, gap to your best and comparison with last time. The example dashboard
  (`docs/ha/build_dashboard.py`) gets a **map card** (route over map tiles, segments overlaid, PRs in gold) and a **segments card** on the Riding tab,
  and a new **Running** tab: latest run and map, best times, totals, weekly distance, recent runs and run segments. Gaps are shown as minutes and seconds.
- The example dashboard's **cycling outlook** card (next few hours and tomorrow) appears only if your Home Assistant has `sensor.cycling_conditions` and
  `sensor.cycling_verdict`; `docs/ha/deploy.py` now checks, so a standard install is not asked for entities it does not have.

### Changed
- Segment leaderboards, the Segments list and segment pages read activities through the new `SegActivity` view, so a segment's efforts can come from
  either a ride or a phone-recorded run. Ride behaviour is unchanged.
- Weight pace: when the short window reads flat or gaining only because of a holiday spike but the 12-week trend is a real loss, the 12-week pace is used.

### Added (earlier, same release)
- **Change an activity's type** (e.g. a walk that was recorded as a ride): a Type picker on the ride page and on each Workouts row, and in the phone app. Moving between rides and walks/runs/hikes moves the activity and re-estimates its calories for the new sport.
- **Gear: assign a date range from a bike's own page** (the full calendar is still there, and opens with the bike pre-selected).
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
