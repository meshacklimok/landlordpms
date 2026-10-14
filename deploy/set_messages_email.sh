#!/bin/bash
# Points mail to tenants and owners (receipts, statements, invitations) at Brevo, asking only for the SMTP login
# and key (doc 22). Run as root:
#   bash /srv/landlordpms/deploy/set_messages_email.sh
# Sets the EMAIL_MESSAGES_* lines in /etc/landlordpms.env and restarts the app. Sign-in codes and alerts stay on
# the provider set by set_email.sh. Port 2525 is used because DigitalOcean blocks 25, 465 and 587.
set -euo pipefail

ENV_FILE=/etc/landlordpms.env

stop() { printf '\n*** %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" = 0 ] || stop "Run as root: sudo bash $0"
[ -f "$ENV_FILE" ] || stop "$ENV_FILE not found: run setup.sh first."

read -r -p "Brevo SMTP login (looks like 8a1b2c001@smtp-brevo.com): " LOGIN
read -r -s -p "Brevo SMTP key (starts with xsmtpsib-): " KEY; echo
[ -n "$LOGIN" ] || stop "No login given."
case "$KEY" in xsmtpsib-*) ;; *) stop "That does not look like a Brevo SMTP key (it starts with xsmtpsib-).";; esac

cp -p "$ENV_FILE" "$ENV_FILE.bak"
tmp=$(mktemp)
grep -v -E '^EMAIL_MESSAGES_(HOST|PORT|USER|PASSWORD|USE_TLS)=' "$ENV_FILE" > "$tmp" || true
{
    echo "EMAIL_MESSAGES_HOST=smtp-relay.brevo.com"
    echo "EMAIL_MESSAGES_PORT=2525"
    echo "EMAIL_MESSAGES_USER=$LOGIN"
    echo "EMAIL_MESSAGES_PASSWORD=$KEY"
} >> "$tmp"
cat "$tmp" > "$ENV_FILE"      # keeps the file's owner and permissions
rm -f "$tmp"
unset KEY

systemctl restart landlordpms
printf '\nDone. Old settings are in %s.bak (delete it when happy).\n' "$ENV_FILE"
printf 'Send a real test through Brevo:
  sh /srv/landlordpms/deploy/run.sh sendtestemail --using messages you@example.com
'
