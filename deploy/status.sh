#!/bin/bash
# What is live and what is missing on this server (D-134). Run as root:  bash /srv/landlordpms/deploy/status.sh
# Changes nothing. Shows services, pending migrations (as the owner role) and settings still empty in the env file.
APP_DIR=/srv/landlordpms
ENV_FILE=/etc/landlordpms.env
OWNER_ENV=/etc/landlordpms-owner.env
[ "$(id -u)" = 0 ] || { echo "Run as root." >&2; exit 1; }

echo "== Services"
for s in landlordpms nyumbapms-web postgresql redis-server nginx; do
    printf '%-16s %s\n' "$s" "$(systemctl is-active "$s")"
done
printf 'port 3000 (Next.js)  %s\n' "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:3000/_next/ || true)"
printf 'port 8000 (Django)   %s\n' "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/healthz || true)"
[ -d "$APP_DIR/web/.next" ] && echo "web/.next built: yes" || echo "web/.next built: NO (run deploy/update.sh)"

echo; echo "== Pending migrations"
(
    set -a; . "$ENV_FILE"; . "$OWNER_ENV"; set +a
    export DJANGO_SETTINGS_MODULE=config.settings.prod DB_POOL=0 DB_STATEMENT_TIMEOUT_MS=0
    cd "$APP_DIR" && runuser -u landlordpms -- "$APP_DIR/venv/bin/python" manage.py showmigrations --plan | grep -v '\[X\]' || echo "none"
)

echo; echo "== Settings still empty in $ENV_FILE"
for k in AT_USERNAME AT_API_KEY AT_SENDER_ID USSD_CODE PLATFORM_PAYBILL PLATFORM_DARAJA_SHORTCODE \
         PLATFORM_DARAJA_CONSUMER_KEY PLATFORM_DARAJA_CONSUMER_SECRET PLATFORM_DARAJA_PASSKEY \
         PLATFORM_PAYSTACK_SECRET_KEY WHATSAPP_BACKEND WA_PHONE_NUMBER_ID WA_ACCESS_TOKEN WA_APP_SECRET \
         LEGAL_NAME DATA_PROTECTION_EMAIL PLATFORM_KRA_PIN MPESA_ALLOWED_IPS PAYSTACK_ALLOWED_IPS; do
    v=$(sed -n "s/^$k=//p" "$ENV_FILE" | tail -n1 | tr -d '"')
    { [ -z "$v" ] || [ "$v" = FILL_ME ]; } && echo "  $k"
done
exit 0
