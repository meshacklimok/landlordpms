#!/bin/sh
# Copies new backups to storage in another data centre (doc 18 "Backups", doc 21 step 9).
# Runs nightly as the app user from /etc/cron.d/landlordpms-offsite, after the 02:10 backup.
# copy, not sync: a backup deleted here is never deleted there.
set -eu
REMOTE="${OFFSITE_REMOTE:-offsite-crypt:nyumbapms}"
if ! rclone listremotes | grep -qx "${REMOTE%%:*}:"; then
    echo "No rclone remote '${REMOTE%%:*}' yet (doc 21 step 9); nothing copied."
    exit 0
fi
exec rclone copy --max-age 48h --quiet /var/backups/landlordpms "$REMOTE"
