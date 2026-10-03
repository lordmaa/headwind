# Home Assistant side of Headwind

Everything that builds or deploys the HA dashboard and automations. **No secrets live here** — the HA URL and long-lived
token are read from Headwind's own `Settings` row (`haUrl`, `haToken`) by `hawss.creds()`.

| File | What it does |
|---|---|
| `build_dashboard.py` | Builds the whole **Rob** dashboard (3 tabs: Today, Body, Riding) as a Lovelace config dict. **Edit this, not HA's editor** — a redeploy replaces UI edits. |
| `deploy.py` | Validates every entity id in the config exists in HA, creates the dashboard `rob-health` if needed, saves the config. `python3 deploy.py` |
| `hawss.py` | Tiny HA websocket client (`hawss.call({...}, {...})`, `hawss.creds()`). Uses the `websockets` package already installed. |
| `deploy_hydration.py` | Idempotent: helpers (`input_datetime.headwind_water_*`), scripts `script.headwind_add_water_150/250/500`, hydration prompt + button-handler automations. |
| `deploy_protein_check.py` | The 20:00 evening protein-check automation. |
| `shot_ha.js` | Headless-Chromium screenshots of the dashboard (`node shot_ha.js phone|desktop` → `shots/`). Needs a temporary `ha_auth.json` (`{"url":..,"token":..}`) — create it from `hawss.creds()`, run, **delete it**. Uses Playwright from `/home/rob/JukeOS/node_modules`. |

Run from this directory. Typical loop: edit `build_dashboard.py` → `python3 deploy.py` → screenshot → look.

## Gotchas learned
- Custom cards installed in HA (HACS): mushroom, button-card, mini-graph-card, auto-entities, power-flow-card-plus … **not** apexcharts-card / card-mod.
  Charts are hand-drawn inline SVG inside button-card JS, fed by *attribute* sensors (`*_history`, `reality_check`, `goal_projection`, `ride_months`).
- Dashboard theme is set per view (`Frosted Glass Dark`); the theme itself is a HACS install already present.
- `sections` view; cards use `grid_options.columns` (12 = full, 6 = half, 4 = third).
- HA REST config API is used for scripts/automations (`/api/config/{script,automation}/config/<id>`); use the *new* syntax (`triggers/conditions/actions`, `trigger:`/`action:` keys).
- For event triggers the context id is `trigger.event.context.id` (not `trigger.context.id`).
- Entity ids come from the MQTT *name*: "Nutrition Steps 7 Day Average" → `sensor.headwind_nutrition_steps_7_day_average` (not the uid).
