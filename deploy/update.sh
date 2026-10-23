#!/bin/bash
# Brings in a new version of NyumbaPMS (D-122, doc 21 "Updating the app"). Run as root:
#   bash /srv/landlordpms/deploy/update.sh
# The code comes from git (when the folder is a clone) or from a new /root/nyumbapms.tar.gz.
# Order: backup, code, packages, tables, static files, check, then reload with no downtime.
set -euo pipefail

APP_USER=landlordpms
APP_DIR=/srv/landlordpms
ENV_FILE=/etc/landlordpms.env
OWNER_ENV=/etc/landlordpms-owner.env
CODE_TAR="${CODE_TAR:-/root/nyumbapms.tar.gz}"

step() { printf '\n==> %s\n' "$*"; }
stop() { printf '\n*** %s\n' "$*" >&2; exit 1; }
as_app() { runuser -u "$APP_USER" -- "$@"; }

[ "$(id -u)" = 0 ] || stop "Run as root: sudo bash $0"

step "Backup before changing anything"
as_app sh "$APP_DIR/deploy/run.sh" backup

step "Code"
if [ -d "$APP_DIR/.git" ]; then
    as_app git -C "$APP_DIR" pull --ff-only
elif [ -f "$CODE_TAR" ]; then
    tmp=$(mktemp -d)
    tar -xzf "$CODE_TAR" -C "$tmp"
    [ -f "$tmp/manage.py" ] || stop "$CODE_TAR has no manage.py at the top. Make it from the project folder (doc 21 step 4)."
    # --delete removes files the new version no longer has; venv, uploads and static files stay.
    rsync -a --delete --exclude /venv/ --exclude /media/ --exclude /staticfiles/ --exclude /backups/ --exclude /web/node_modules/ --exclude /web/.next/ "$tmp"/ "$APP_DIR"/
    rm -rf "$tmp"
    chown -R "$APP_USER:$APP_USER" "$APP_DIR"
    mv "$CODE_TAR" "$CODE_TAR.installed"
else
    stop "No new code: upload $CODE_TAR first (doc 21), or make $APP_DIR a git clone."
fi

step "Python packages"
as_app "$APP_DIR/venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

step "Next.js screens: packages and build"
if ! command -v node >/dev/null; then
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
    apt-get install -yq nodejs
fi
as_app sh -c "cd $APP_DIR/web && npm ci --no-audit --no-fund && npm run build"

step "Database tables (as the owner role)"
(
    set -a; . "$ENV_FILE"; . "$OWNER_ENV"; set +a
    export DJANGO_SETTINGS_MODULE=config.settings.prod DB_POOL=0 DB_STATEMENT_TIMEOUT_MS=0
    cd "$APP_DIR" && runuser -u "$APP_USER" -- "$APP_DIR/venv/bin/python" manage.py migrate --noinput
)

step "Static files, deploy check, scheduled jobs"
as_app sh "$APP_DIR/deploy/run.sh" sync_access_catalog
as_app sh "$APP_DIR/deploy/run.sh" collectstatic --noinput -v 0
as_app sh "$APP_DIR/deploy/run.sh" check --deploy
crontab -u "$APP_USER" "$APP_DIR/deploy/crontab"

step "Reload the web workers"
cp "$APP_DIR/deploy/nyumbapms-web.service" /etc/systemd/system/nyumbapms-web.service
systemctl daemon-reload
systemctl enable nyumbapms-web >/dev/null
systemctl restart nyumbapms-web
# nginx routes come from deploy/nginx.conf; certbot edited the live copy, so it is not overwritten here (doc 22).
if ! cmp -s "$APP_DIR/deploy/landlordpms.service" /etc/systemd/system/landlordpms.service; then
    cp "$APP_DIR/deploy/landlordpms.service" /etc/systemd/system/landlordpms.service
    systemctl daemon-reload
    systemctl restart landlordpms
else
    systemctl reload landlordpms
fi
sleep 3
domain=$(sed -n 's/^ALLOWED_HOSTS=\([^,]*\).*/\1/p' "$ENV_FILE")
curl -fsS -o /dev/null -H "Host: $domain" http://127.0.0.1:8000/healthz \
    && echo "Updated; the site answers." \
    || stop "The site does not answer. See: journalctl -u landlordpms -n 50"
systemctl is-active --quiet nyumbapms-web     && echo "Next.js screens are running."     || stop "The Next.js screens are not running. See: journalctl -u nyumbapms-web -n 50"
