# Gear module: bikes, service log, consumable mileage (plan, nothing built yet)

Rob's brief (2026-10-05): add bikes with photos; a default bike per rider, changeable per ride; stats per bike; a service log ("replaced the chain"); mileage tracked on
consumables with configurable "your chain is due a check after X miles" notifications; later sold separately (Etsy).

## What exists today (checked)
- `Activity` has no bike/gear column. Rides come from Garmin sync, GPX/FIT/Strava-export import and the phone app, all through `_insert`/`garmin.py`, so one hook can stamp a bike on every new ride.
- Photo handling exists (`routes/riders.py::upload_avatar`: magic-byte detection, Pillow resize, saved under `static/avatars/`), so bike photos need no new machinery.
- Notifications exist: `services/notify.py` (ntfy), the HA `new_ride` MQTT topic (reused for walks), and the phone app can show local notifications.
- Rob's own history is 4,292 rides / ~46,400 miles with no bike assigned, so bulk-assign ("all rides before X on bike Y") is essential, not optional.
- Headwind is **public, MIT** (github.com/lordmaa/headwind). That matters for selling Gear separately (below).

## Data model (all additive, never drops anything)
- `Bike(id, riderId, name, type, brand, model, year, photoPath, boughtOn, startMeters, trackFrom, archived)`; `Rider.defaultBikeId`.
- `Activity.bikeId` (nullable). A new ride is stamped with the rider's default bike AT THAT MOMENT, so changing the default later never rewrites history.
  Old rides stay unassigned until you bulk-assign ("all rides before X on bike Y"). Editable on the ride page and in the phone app.
- `Component(id, bikeId, kind, name, installedOn, installedMeters, retiredOn, checkEveryM, replaceEveryM, checkEveryDays, notify)`; `kind` from a preset list (chain, cassette,
  chainrings, tyres front/rear, brake pads, cables, bar tape, bottom bracket, bearings, battery...) with sensible default intervals you can edit.
- `ServiceLog(id, bikeId, componentId?, date, odometerM, action replaced|serviced|inspected|cleaned|other, cost, notes, photoPath?)`. Logging "replaced" retires the old component and creates the new one at the current odometer.
- Mileage is COMPUTED, never stored: bike odometer = `startMeters` + sum of that bike's ride distances (indoor rides included: a chain wears on a turbo too); component mileage = odometer now - `installedMeters`.
  Reassigning a ride therefore corrects everything automatically.
- `ComponentAlert(componentId, level)` remembers which threshold (90% "due soon", 100% "due", 125% "overdue") was already announced, so you hear each once.

## Surfaces
- Web `/gear`: bike cards (photo, odometer, anything due), bike page (stats: rides, distance, climbing, time, average speed, best efforts, a year-by-year chart; component table with progress bars;
  service log with a "Log service" form), ride page bike picker, riders page default-bike picker.
- Phone app: Gear screen, bike picker on ride detail, camera for bike photos, local notification when something is due.
- Alerts: ntfy + the HA mobile-app push (reuse the `new_ride` channel with `kind: gear`) + phone local notification. Evaluated after every ride is added or reassigned, and nightly.
- Optional import: some Strava-export rides carry a gear id, but only 12 of Rob's 4,292 rides do (all the same bike), so history import is a nice-to-have, not a plan driver.

## Selling it separately (the decision to make first)
Headwind core is MIT and public, so anything committed to the public repo can be copied by anyone. A paid module therefore has to live OUTSIDE the public repo
(private package loaded through a small extension seam in core: register a blueprint, run a schema migration, subscribe to "ride added" and "ride deleted").
That seam is small (~half a day), is useful on its own, and keeps core free. Licensing can start as a signed licence-key file checked offline (Etsy delivers a zip + a key), with nothing phoning home.
The Android Gear screen would sit behind the existing `Entitlements.pro` gate.

## Phases (each is shippable)
0. Extension seam in core + decide repo/licence (0.5 day).
1. Bikes, photos, default bike, per-ride bike, per-bike stats, bulk-assign (web).
2. Components + service log + computed mileage.
3. Alerts (ntfy / HA / phone).
4. Android: Gear screen, ride bike picker, camera, local notifications.
5. Packaging for Etsy: private repo, licence key, install/upgrade guide, screenshots.

## Questions for Rob
1. Is "private plugin outside the MIT repo" the right way to sell it, or is Gear staying free/in-repo and the paid thing something else?
2. Default components and intervals: start from a standard road-bike list (chain ~2,000 mi check / 3,000 mi replace, tyres ~3,000 mi, pads by wear...) and let each user edit?
3. History: assign existing rides to one default bike automatically, or leave them unassigned until you choose?
4. Count indoor / virtual rides toward component wear? (Proposed: yes, with a per-ride "exclude" switch.)

## Rob's history today
4292 rides, 46,397 miles, none with a bike assigned.
