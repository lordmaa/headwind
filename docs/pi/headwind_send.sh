#!/usr/bin/env bash
# headwind_send.sh <vault dir> <rider name> [--dry-run]
# Sends new Garmin .fit files from the vault to Headwind (replaces the old Strava uploader).
# - a per-vault ledger (.headwind_sent) records what Headwind has accepted; failures stay unsent and retry on the next run
# - Headwind itself is idempotent (same start time + distance = "skipped"), so re-sending can never duplicate a ride
# - files dated before HEADWIND_MIN_DATE are never sent (the device also holds older rides that belong to someone else)
set -uo pipefail
ENV_FILE="${HEADWIND_ENV:-/home/pi/.config/headwind-upload.env}"
# shellcheck disable=SC1090
source "$ENV_FILE"                       # HEADWIND_URL, HEADWIND_TOKEN, HEADWIND_MIN_DATE
DIR="${1:?usage: headwind_send.sh <vault dir> <rider> [--dry-run]}"
RIDER="${2:?usage: headwind_send.sh <vault dir> <rider> [--dry-run]}"
DRY="${3:-}"
LEDGER="$DIR/.headwind_sent"
touch "$LEDGER"
imported=0; skipped=0; failed=0; pending=0
shopt -s nullglob
for f in "$DIR"/*.fit; do
  base="$(basename "$f")"
  [[ "$base" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2} ]] || { echo "skip (unexpected name): $base"; continue; }
  [[ "${base:0:10}" < "$HEADWIND_MIN_DATE" ]] && continue
  grep -qxF "$base" "$LEDGER" && continue
  if [[ "$DRY" == "--dry-run" ]]; then echo "would send: $base"; pending=$((pending+1)); continue; fi
  out="$(curl -sS -m 90 -w $'\n%{http_code}' -H "X-Upload-Token: $HEADWIND_TOKEN" -F "rider=$RIDER" -F "file=@$f" "$HEADWIND_URL/import/api/upload-ride" 2>&1)" \
    || { echo "FAILED (network) $base: ${out%%$'\n'*}"; failed=$((failed+1)); continue; }
  code="${out##*$'\n'}"; body="${out%$'\n'*}"
  status="$(grep -o '"status": *"[a-z]*"' <<<"$body" | grep -o '[a-z]*"$' | tr -d '"')"
  if [[ "$code" == "200" && "$status" == "imported" ]]; then echo "imported -> Headwind ($RIDER): $base"; echo "$base" >> "$LEDGER"; imported=$((imported+1)); sleep 3   # let the weather lookup breathe between new rides
  elif [[ "$code" == "200" && "$status" == "skipped" ]]; then echo "already in Headwind: $base"; echo "$base" >> "$LEDGER"; skipped=$((skipped+1))
  else echo "FAILED ($code) $base: $body"; failed=$((failed+1)); fi
done
if [[ "$DRY" == "--dry-run" ]]; then echo "dry run: $pending file(s) would be sent"; exit 0; fi
echo "Headwind: $imported imported, $skipped already there, $failed failed"
[[ "$failed" -eq 0 ]]
