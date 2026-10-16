#!/bin/bash
# Points the app at Resend for email, asking only for the API key (doc 22). Run as root:
#   bash /srv/landlordpms/deploy/set_email.sh
# Sets EMAIL_HOST, EMAIL_HOST_USER, EMAIL_HOST_PASSWORD and DEFAULT_FROM_EMAIL in /etc/landlordpms.env,
# restarts the app, and checks that the mail server accepts the login. Nothing is sent.
set -euo pipefail

APP_DIR=/srv/landlordpms
APP_USER=landlordpms
ENV_FILE=/etc/landlordpms.env
FROM="${DEFAULT_FROM_EMAIL:-NyumbaPMS <no-reply@nyumbapms.com>}"

stop() { printf '\n*** %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" = 0 ] || stop "Run as root: sudo bash $0"
[ -f "$ENV_FILE" ] || stop "$ENV_FILE not found: run setup.sh first."

# The key is read without echo, so it stays out of the screen and the shell history.
read -r -s -p "Paste your Resend API key (starts with re_): " KEY; echo
case "$KEY" in re_*) ;; *) stop "That does not look like a Resend key (it starts with re_).";; esac

cp -p "$ENV_FILE" "$ENV_FILE.bak"
tmp=$(mktemp)
# Drop any old lines for these four settings, then add ours; other lines are kept as they are.
grep -v -E '^(EMAIL_HOST|EMAIL_HOST_USER|EMAIL_HOST_PASSWORD|EMAIL_PORT|EMAIL_USE_TLS|DEFAULT_FROM_EMAIL)=' "$ENV_FILE" > "$tmp" || true
{
    echo "EMAIL_HOST=smtp.resend.com"
    echo "EMAIL_PORT=2587"   # same STARTTLS as 587, which DigitalOcean blocks on newer accounts
    echo "EMAIL_HOST_USER=resend"
    echo "EMAIL_HOST_PASSWORD=$KEY"
    echo "DEFAULT_FROM_EMAIL=\"$FROM\""
} >> "$tmp"
cat "$tmp" > "$ENV_FILE"      # keeps the file's owner and permissions
rm -f "$tmp"
unset KEY

printf '\n==> Restarting the app\n'
systemctl restart landlordpms

printf '\n==> Checking the mail server login\n'
runuser -u "$APP_USER" -- sh "$APP_DIR/deploy/run.sh" launch_check | grep -i "email" || true

printf '\nDone. Old settings are in %s.bak (it holds the old key, delete it when happy).\n' "$ENV_FILE"
printf 'To send a real test: sh %s/deploy/run.sh sendtestemail you@example.com\n' "$APP_DIR"
