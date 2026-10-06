# Headwind public demo

A locked-down, always-fresh demo: login `test` / `test`, a fictional rider (real routes, home area removed), made-up food diary,
segments, gear, and a retro LED visitor counter. Visitors can look but not break things: writes are default-deny, rate limited,
and the whole DB resets nightly (04:00) from `demo/out/pristine.db`; dates are rebased so data always ends yesterday.

## Build the data (needs your real DB; never commit the output)
    python3 demo/build_demo_db.py --source /path/to/dev.db --out demo/out --redact "Your Name" [--allow-segment ID ...]
Check `demo/out/REPORT.txt`. The builder audits its own output and deletes it if any point falls in a privacy zone.

## Run (on the Pi, arm64)
    docker compose -f demo/docker-compose.demo.yml up -d --build      # listens on :5003
Set `DEMO_SECRET_KEY` and `DEMO_APP_URL` in the environment for a real deployment.

## Public access
Point your reverse proxy / Cloudflare tunnel at `http://headwind-demo:5001` (join the tunnel's docker network) or `host:5003`.
Real client IPs come from `CF-Connecting-IP` (rate limit + counter). The counter GIF is `/demo/counter.gif`; health is `/demo/health`.

## Notes
- Counter file lives in the `demo-data` volume and survives the nightly reset.
- Watchtower is disabled for this container (local image).
- Update data: rebuild `pristine.db`, copy to the Pi, `docker compose ... restart`, then delete the `demo-data` volume to reseed.
