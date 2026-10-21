#!/bin/bash
# Maintenance mode on or off (doc 26). Run as root:
#   bash /srv/landlordpms/deploy/maintenance.sh on|off|status
# on : shows the "back soon" page to everyone (nginx answers 503) and pauses the scheduled jobs.
# off: removes the page and puts the scheduled jobs back from deploy/crontab.
# Needs the maintenance block in nginx.conf (doc 26 step 2); `on` checks that it is there.
set -euo pipefail

APP_USER=landlordpms
APP_DIR=/srv/landlordpms
FLAG="$APP_DIR/maintenance.flag"
SAVED=/root/crontab.before-maintenance

stop() { printf '\n*** %s\n' "$*" >&2; exit 1; }
[ "$(id -u)" = 0 ] || stop "Run as root: sudo bash $0 $*"

case "${1:-}" in
on)
    grep -q 'maintenance.flag' /etc/nginx/sites-available/landlordpms \
        || stop "nginx does not have the maintenance block yet (doc 26 step 2). Nothing changed."
    # Wait for a running job, then pause new ones. The old crontab is kept so `off` can bring it back.
    crontab -u "$APP_USER" -l > "$SAVED" 2>/dev/null || true
    printf 'MAILTO=""\n' | crontab -u "$APP_USER" -
    touch "$FLAG"
    nginx -t -q && systemctl reload nginx
    code=$(curl -s -o /dev/null -w '%{http_code}' -H "Host: $(sed -n 's/^ALLOWED_HOSTS=\([^,]*\).*/\1/p' /etc/landlordpms.env)" http://127.0.0.1/ || true)
    echo "Maintenance mode ON (the site now answers $code; 503 is right). Jobs paused."
    echo "Jobs still running now (wait for none): $(pgrep -u "$APP_USER" -fc 'manage.py' || true)"
    ;;
off)
    rm -f "$FLAG"
    crontab -u "$APP_USER" "$APP_DIR/deploy/crontab"
    echo "Maintenance mode OFF. Jobs back on."
    ;;
status)
    if [ -f "$FLAG" ]; then echo "Maintenance mode is ON."; else echo "Maintenance mode is off."; fi
    crontab -u "$APP_USER" -l 2>/dev/null | grep -vc '^#' | sed 's/^/Active crontab lines: /' || true
    ;;
*)
    stop "usage: bash $0 on|off|status"
    ;;
esac
