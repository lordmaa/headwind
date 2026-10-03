#!/usr/bin/env bash
set -euo pipefail

MOUNTPOINT="/mnt/garmin_sync"
SRC="$MOUNTPOINT/Garmin/Activities"

UUID="${1:-unknown}"

case "$UUID" in
  "82E3-193A")
    MODEL="edge_510"
    HEADWIND_RIDER="Sam"      # whose rides this device records in Headwind
    ;;
  *)
    MODEL="unknown_device"
    HEADWIND_RIDER=""            # unknown device: back it up, but never guess a rider
    ;;
esac

DEST="/mnt/data/garmin_vault/$MODEL"
ENDURAIN_IMPORT="/portainer/files/appdata/config/endurain/backend/data/activity_files/bulk_import"
SMB_MOUNT="/mnt/smb_garmin_backup"
SMB_DEST="$SMB_MOUNT/garmin_vault"

echo "Garmin UUID: $UUID"
echo "Syncing new files from:"
echo "  $SRC"
echo "to:"
echo "  $DEST"

mkdir -p "$DEST"

if [ ! -d "$SRC" ]; then
  echo "ERROR: $SRC not found (is the Garmin mounted?)"
  exit 1
fi

rsync -av --ignore-existing "$SRC/" "$DEST/"
# Also push new files to Endurain bulk import
if [ -d "$ENDURAIN_IMPORT" ]; then
  echo "Pushing new files to Endurain bulk import:"
  echo "  $ENDURAIN_IMPORT"
  rsync -av --ignore-existing "$SRC/" "$ENDURAIN_IMPORT/"
else
  echo "Endurain import directory not found, skipping."
fi



# Push to SMB (best-effort)
if findmnt -rno TARGET "$SMB_MOUNT" >/dev/null 2>&1; then
  mkdir -p "$SMB_DEST/$MODEL"
  echo "Pushing vault to SMB:"
  echo "  $SMB_DEST/$MODEL"
  rsync -av --ignore-existing "$DEST/" "$SMB_DEST/$MODEL/"
else
  echo "SMB not mounted, skipping push."
fi

# Send new rides to Headwind (best-effort: anything that fails stays unsent and is retried at the next plug-in).
# This replaces the old Strava uploader (cron job ~/strava/05_sync_and_upload.sh, now retired).
if [ -n "${HEADWIND_RIDER:-}" ] && [ -x /home/pi/headwind_send.sh ]; then
  /home/pi/headwind_send.sh "$DEST" "$HEADWIND_RIDER" || echo "Headwind upload incomplete - will retry at the next plug-in."
else
  echo "No Headwind rider mapped for this device, skipping Headwind upload."
fi

echo "Done."
