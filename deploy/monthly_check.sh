#!/bin/bash
# The monthly check (doc 21 "Every month"), about 5 minutes. Run as root:
#   bash /srv/landlordpms/deploy/monthly_check.sh
# Install the updates it lists with: apt update && apt upgrade
set -uo pipefail
APP_USER=landlordpms
APP_DIR=/srv/landlordpms
as_app() { runuser -u "$APP_USER" -- "$@"; }

printf '\n== Services\n'
for unit in landlordpms nginx postgresql redis-server; do
    printf '  %-14s %s\n' "$unit" "$(systemctl is-active "$unit")"
done

printf '\n== Updates\n'
apt-get update -qq
echo "  $(apt list --upgradable 2>/dev/null | tail -n +2 | wc -l) package(s) to update: apt upgrade"
[ -f /var/run/reboot-required ] && echo "  A restart is needed: reboot (at night; the site is down for about a minute)."

printf '\n== Disk\n'
df -h / /var/backups | sed 's/^/  /'
echo "  Over 80% used: ask for a bigger disk."

printf '\n== Certificate\n'
certbot certificates 2>/dev/null | grep -E 'Domains|Expiry' | sed 's/^ */  /'

printf '\n== Off-site copy\n'
as_app sh "$APP_DIR/deploy/offsite.sh" >/dev/null 2>&1 \
    && as_app rclone lsl "${OFFSITE_REMOTE:-offsite-crypt:nyumbapms}" 2>/dev/null | sort -k2,3 | tail -2 | sed 's/^/  /' \
    || echo "  Could not reach the off-site storage."

printf '\n== Database health\n'
as_app sh "$APP_DIR/deploy/run.sh" db_health

printf '\n== Launch check (backups, email, cache, database)\n'
as_app sh "$APP_DIR/deploy/run.sh" launch_check
