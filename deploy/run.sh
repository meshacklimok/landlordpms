#!/bin/sh
# Runs one manage.py command for cron (D-098 item 3).
# Loads the environment file, uses the production settings, and skips the run while the last one is still going.
#   sh deploy/run.sh <command> [arguments]
set -eu
[ $# -ge 1 ] || { echo "usage: $0 <command> [arguments]" >&2; exit 2; }
APP_DIR="${APP_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
ENV_FILE="${ENV_FILE:-/etc/landlordpms.env}"
LOCK_DIR="${LOCK_DIR:-/tmp}"
set -a
. "$ENV_FILE"
set +a
export DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS_MODULE:-config.settings.prod}"
# Names the connection in pg_stat_activity, so `db_health` shows which job holds it (doc 20 §24).
export DB_APP_NAME="landlordpms-$1"
cd "$APP_DIR"
exec flock --nonblock --conflict-exit-code 0 "$LOCK_DIR/landlordpms-$1.lock" "$APP_DIR/venv/bin/python" manage.py "$@"
