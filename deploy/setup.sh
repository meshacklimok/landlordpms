#!/bin/bash
# One-time setup of a fresh Ubuntu 24.04 server for NyumbaPMS (D-122, doc 21). Run as root:
#   bash setup.sh
# Safe to run again: each step skips what is already done. It stops twice on purpose, and says why:
#   1. after writing /etc/landlordpms.env, so you can fill in the email and SMS details;
#   2. before the https certificate, if app.nyumbapms.com does not point at this server yet.
# The code comes from /root/nyumbapms.tar.gz (uploaded from your computer, doc 21 step 4), or from GitHub
# when REPO_URL is set and this server's deploy key is added to the repository.
set -euo pipefail

DOMAIN="${DOMAIN:-app.nyumbapms.com}"
# Other names that redirect to DOMAIN (need their own DNS A records). Set ALIASES="" to skip them.
ALIASES="${ALIASES-nyumbapms.com www.nyumbapms.com}"
CERT_EMAIL="${CERT_EMAIL:-}"
CODE_TAR="${CODE_TAR:-/root/nyumbapms.tar.gz}"
REPO_URL="${REPO_URL:-}"
BRANCH="${BRANCH:-main}"
APP_USER=landlordpms
APP_DIR=/srv/landlordpms
ENV_FILE=/etc/landlordpms.env
OWNER_ENV=/etc/landlordpms-owner.env
BACKUP_DIR=/var/backups/landlordpms

step() { printf '\n==> %s\n' "$*"; }
stop() { printf '\n*** %s\n' "$*" >&2; exit 1; }
random() { python3 -c 'import secrets; print(secrets.token_hex(32))'; }
as_app() { runuser -u "$APP_USER" -- "$@"; }

[ "$(id -u)" = 0 ] || stop "Run as root: sudo bash setup.sh"
. /etc/os-release
[ "${VERSION_ID:-}" = "24.04" ] || echo "Warning: written for Ubuntu 24.04, this is ${PRETTY_NAME:-unknown}."

step "System packages, time zone and automatic security updates"
timedatectl set-timezone Africa/Nairobi
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get upgrade -yq
apt-get install -yq python3 python3-venv git rsync postgresql postgresql-contrib redis-server nginx \
    certbot python3-certbot-nginx ufw fail2ban unattended-upgrades rclone curl ca-certificates
# Node.js 22 for the Next.js screens (D-168); Ubuntu's own package is too old for Next.js 16.
if ! command -v node >/dev/null || [ "$(node -p 'process.versions.node.split(".")[0]')" -lt 20 ]; then
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
    apt-get install -yq nodejs
fi
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF

step "Swap (2 GB), so a memory spike slows the server instead of stopping it"
if ! swapon --show | grep -q /swapfile; then
    fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
    grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

step "Firewall: only ssh, http and https"
ufw allow OpenSSH >/dev/null
ufw allow 'Nginx Full' >/dev/null
ufw --force enable >/dev/null
# Ban addresses that keep guessing ssh passwords, hammer nginx past its limits, or probe for scripts (D-139).
cat > /etc/fail2ban/jail.d/nyumbapms.local <<'EOF'
[DEFAULT]
bantime = 1h
findtime = 10m
maxretry = 5

[sshd]
enabled = true
maxretry = 4
bantime = 24h

[nginx-limit-req]
enabled = true
logpath = /var/log/nginx/error.log
maxretry = 10

[nginx-botsearch]
enabled = true
logpath = /var/log/nginx/access.log
maxretry = 3
bantime = 24h
EOF
systemctl enable --now fail2ban >/dev/null
systemctl restart fail2ban
# Key-only ssh, but only once root has a key, so this never locks you out.
if [ -s /root/.ssh/authorized_keys ]; then
    printf 'PasswordAuthentication no\nKbdInteractiveAuthentication no\nPermitRootLogin prohibit-password\nMaxAuthTries 3\nLoginGraceTime 20\nX11Forwarding no\nAllowAgentForwarding no\n' > /etc/ssh/sshd_config.d/10-nyumbapms.conf
    systemctl reload ssh
fi

step "App user and folders"
id "$APP_USER" >/dev/null 2>&1 || useradd --system --create-home --shell /bin/bash "$APP_USER"
mkdir -p "$APP_DIR" "$BACKUP_DIR"
chown "$APP_USER:$APP_USER" "$APP_DIR" "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

step "Code"
if [ -f "$APP_DIR/manage.py" ]; then
    echo "Already in $APP_DIR (deploy/update.sh brings in new versions)."
    chmod -R u+rwX,go+rX "$APP_DIR"
    chown -R "$APP_USER:$APP_USER" "$APP_DIR"
elif [ -f "$CODE_TAR" ]; then
    tar -xzf "$CODE_TAR" -C "$APP_DIR"
    chown -R "$APP_USER:$APP_USER" "$APP_DIR"
elif [ -n "$REPO_URL" ]; then
    home=$(getent passwd "$APP_USER" | cut -d: -f6)
    if [ ! -f "$home/.ssh/id_ed25519" ]; then
        as_app mkdir -p "$home/.ssh"
        as_app ssh-keygen -q -t ed25519 -N '' -C "nyumbapms-server" -f "$home/.ssh/id_ed25519"
        ssh-keyscan -t ed25519 github.com 2>/dev/null | as_app tee -a "$home/.ssh/known_hosts" >/dev/null
    fi
    if ! as_app git ls-remote "$REPO_URL" >/dev/null 2>&1; then
        echo "Add this key on GitHub: repository > Settings > Deploy keys > Add deploy key (read-only):"
        cat "$home/.ssh/id_ed25519.pub"
        stop "Then run setup.sh again."
    fi
    as_app git clone --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
else
    stop "No code. Upload it to $CODE_TAR (doc 21 step 4), or set REPO_URL, then run setup.sh again."
fi
for dir in media staticfiles; do as_app mkdir -p "$APP_DIR/$dir"; done
chmod 750 "$APP_DIR/media"

step "Python packages"
[ -x "$APP_DIR/venv/bin/python" ] || as_app python3 -m venv "$APP_DIR/venv"
as_app "$APP_DIR/venv/bin/pip" install -q --upgrade pip
as_app "$APP_DIR/venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

step "PostgreSQL: slow-query statistics, roles and database (deploy/db_roles.sql)"
pg_version=$(ls /etc/postgresql | sort -n | tail -1)
conf="/etc/postgresql/$pg_version/main/conf.d/nyumbapms.conf"
if [ ! -f "$conf" ]; then
    printf "shared_preload_libraries = 'pg_stat_statements'\nlog_min_duration_statement = 1000\n" > "$conf"
    systemctl restart postgresql
fi
if ! runuser -u postgres -- psql -tAc "SELECT 1 FROM pg_roles WHERE rolname = '$APP_USER'" | grep -q 1; then
    app_pw=$(random)
    owner_pw=$(random)
    runuser -u postgres -- psql -q -v ON_ERROR_STOP=1 -v owner_pw="'$owner_pw'" -v app_pw="'$app_pw'" \
        < "$APP_DIR/deploy/db_roles.sql"
    umask 077
    printf 'DB_USER=landlordpms_owner\nDB_PASSWORD=%s\n' "$owner_pw" > "$OWNER_ENV"
    umask 022
    echo "$app_pw" > /root/.nyumbapms-app-db-password
    chmod 600 /root/.nyumbapms-app-db-password
fi
systemctl enable --now redis-server >/dev/null

step "Settings file $ENV_FILE"
if [ ! -f "$ENV_FILE" ]; then
    app_pw=$(cat /root/.nyumbapms-app-db-password)
    fernet=$("$APP_DIR/venv/bin/python" -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')
    vapid=$(cd "$APP_DIR" && "$APP_DIR/venv/bin/python" -c '
import base64
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()
key = ec.generate_private_key(ec.SECP256R1())
public = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
private = key.private_numbers().private_value.to_bytes(32, "big")
print("VAPID_PUBLIC_KEY=" + b64(public))
print("VAPID_PRIVATE_KEY=" + b64(private))')
    cat > "$ENV_FILE" <<EOF
# NyumbaPMS production settings (doc 18 lists every setting). Values with spaces or < > need double quotes.
# Everything is filled in except the keys only you have: replace each placeholder, and add the optional keys
# below when you have them. After a change: systemctl restart landlordpms
# Keep a copy of FIELD_ENCRYPTION_KEYS in your password manager: without it, saved M-Pesa keys cannot be read.
DEBUG=False
SECRET_KEY=$(random)
ALLOWED_HOSTS=$DOMAIN
SITE_URL=https://$DOMAIN
FIELD_ENCRYPTION_KEYS=$fernet
TRUSTED_PROXY_COUNT=1

DB_NAME=landlordpms
DB_USER=landlordpms
DB_PASSWORD=$app_pw
DB_HOST=localhost
DB_PORT=5432
REDIS_URL=redis://127.0.0.1:6379/0
BACKUP_DIR=$BACKUP_DIR

# Email: from your email provider (Brevo, Zoho, Mailgun...). Port 587 with TLS is the default.
EMAIL_HOST=FILL_ME
EMAIL_HOST_USER=FILL_ME
EMAIL_HOST_PASSWORD=FILL_ME
DEFAULT_FROM_EMAIL="NyumbaPMS <no-reply@nyumbapms.com>"
# Optional second provider for mail to tenants and owners (receipts, statements, invitations); the one above
# keeps sign-in codes and alerts. Remove the # in front of these four lines to use it.
#EMAIL_MESSAGES_HOST=smtp-relay.brevo.com
#EMAIL_MESSAGES_USER=
#EMAIL_MESSAGES_PASSWORD=
#EMAIL_MESSAGES_PORT=587
OPS_ALERT_EMAILS=FILL_ME
SUPPORT_EMAIL=support@nyumbapms.com
HELLO_EMAIL=hello@nyumbapms.com
SUPPORT_PHONE="0748 578 190"

# SMS: your live Africa's Talking account. AT_SENDER_ID stays empty until NYUMBAPMS is approved.
SMS_BACKEND=notifications.providers.sms.AfricasTalkingSmsSender
AT_USERNAME=FILL_ME
AT_API_KEY=FILL_ME
AT_SENDER_ID=
AT_CALLBACK_TOKEN=$(random)

# USSD: the code Africa's Talking gives you, e.g. *384*123#. Empty hides it.
USSD_CODE=

# Alerts on staff phones (generated here; nothing to fill in).
$vapid
VAPID_SUBJECT=mailto:support@nyumbapms.com

# Optional: the assistant. Leave empty to not offer it.
OPENAI_API_KEY=
ASSISTANT_MODEL=gpt-5.6-terra

# M-Pesa (Daraja) keys are not set here: each landlord enters theirs in the app (M-Pesa > Settings).
# Optional: Safaricom's and Paystack's published callback IPs, comma-separated, to refuse anyone else.
MPESA_ALLOWED_IPS=
PAYSTACK_ALLOWED_IPS=

# Optional: landlords paying their subscription to you online (D-126). Fill either or both, then run
# manage.py platform_payments (and --register for the Paybill). See 17_MPESA_GO_LIVE.md section 7.
PLATFORM_PAYBILL=
PLATFORM_CALLBACK_TOKEN=$(random)
PLATFORM_DARAJA_ENVIRONMENT=PRODUCTION
PLATFORM_DARAJA_SHORTCODE=
PLATFORM_DARAJA_CONSUMER_KEY=
PLATFORM_DARAJA_CONSUMER_SECRET=
PLATFORM_DARAJA_PASSKEY=
PLATFORM_PAYSTACK_SECRET_KEY=

# Optional: tenants asking on WhatsApp (Meta WhatsApp Cloud API). Leave WHATSAPP_BACKEND empty to keep it off;
# to turn it on set it to notifications.providers.whatsapp.CloudApiWhatsAppSender and fill the three WA_ values from Meta.
WHATSAPP_BACKEND=
WA_PHONE_NUMBER_ID=
WA_ACCESS_TOKEN=
WA_APP_SECRET=
WA_VERIFY_TOKEN=$(random)

# Shown on the terms and privacy pages and on invoices for your subscription fees.
LEGAL_NAME=
DATA_PROTECTION_EMAIL=
SUPPORT_WHATSAPP=254748578190
HOSTING_LOCATION="DigitalOcean, Frankfurt, Germany (EU)"
PLATFORM_KRA_PIN=
EOF
    chown root:"$APP_USER" "$ENV_FILE"
    chmod 640 "$ENV_FILE"
    rm -f /root/.nyumbapms-app-db-password
fi
if grep -q '^[A-Z_]*=FILL_ME' "$ENV_FILE"; then
    stop "Fill in the FILL_ME values: nano $ENV_FILE   Then run setup.sh again."
fi

step "Database tables (as the owner role), static files and the deploy check"
(
    set -a; . "$ENV_FILE"; . "$OWNER_ENV"; set +a
    export DJANGO_SETTINGS_MODULE=config.settings.prod DB_POOL=0 DB_STATEMENT_TIMEOUT_MS=0
    cd "$APP_DIR" && runuser -u "$APP_USER" -- "$APP_DIR/venv/bin/python" manage.py migrate --noinput
)
as_app sh "$APP_DIR/deploy/run.sh" sync_access_catalog
as_app sh "$APP_DIR/deploy/run.sh" collectstatic --noinput -v 0
as_app sh "$APP_DIR/deploy/run.sh" check --deploy

step "Next.js screens: install packages and build"
as_app sh -c "cd $APP_DIR/web && npm ci --no-audit --no-fund && npm run build"

step "Web services and nginx"
cp "$APP_DIR/deploy/landlordpms.service" /etc/systemd/system/landlordpms.service
cp "$APP_DIR/deploy/nyumbapms-web.service" /etc/systemd/system/nyumbapms-web.service
systemctl daemon-reload
systemctl enable landlordpms nyumbapms-web >/dev/null
systemctl restart landlordpms nyumbapms-web
if [ ! -f /etc/nginx/sites-available/landlordpms ]; then
    if [ -n "$ALIASES" ]; then
        sed -e "s/ALIASES/$ALIASES/g" -e "s/DOMAIN/$DOMAIN/g" "$APP_DIR/deploy/nginx.conf" > /etc/nginx/sites-available/landlordpms
    else
        # No aliases: drop the redirect block (from its comment to the first closing brace).
        sed -e '/^# The bare domain and www/,/^}/d' -e "s/DOMAIN/$DOMAIN/g" "$APP_DIR/deploy/nginx.conf" > /etc/nginx/sites-available/landlordpms
    fi
    ln -sf /etc/nginx/sites-available/landlordpms /etc/nginx/sites-enabled/landlordpms
    rm -f /etc/nginx/sites-enabled/default
fi
nginx -t -q
systemctl reload nginx

step "Scheduled jobs and the nightly off-site copy"
crontab -u "$APP_USER" "$APP_DIR/deploy/crontab"
echo "40 3 * * * $APP_USER sh $APP_DIR/deploy/offsite.sh" > /etc/cron.d/landlordpms-offsite

step "https certificate for $DOMAIN"
if [ -d "/etc/letsencrypt/live/$DOMAIN" ]; then
    echo "Already there; it renews by itself."
else
    here=$(curl -4 -fsS https://api.ipify.org || true)
    there=$(getent ahostsv4 "$DOMAIN" | awk 'NR==1 {print $1}' || true)
    [ -n "$here" ] && [ "$here" = "$there" ] || \
        stop "$DOMAIN points at '${there:-nothing}', this server is '$here'. Add the DNS A record (doc 21 step 2), wait a few minutes, run setup.sh again."
    [ -n "$CERT_EMAIL" ] || stop "Run again with your email for certificate notices: CERT_EMAIL=you@example.com bash setup.sh"
    names="-d $DOMAIN"
    for a in $ALIASES; do names="$names -d $a"; done
    # shellcheck disable=SC2086
    certbot --nginx $names --non-interactive --agree-tos -m "$CERT_EMAIL" --redirect
fi

step "First backup and the launch check"
as_app sh "$APP_DIR/deploy/run.sh" backup
as_app sh "$APP_DIR/deploy/run.sh" launch_check || true
printf '\nDone. Open https://%s\n' "$DOMAIN"
printf 'Create your login:  sudo -u %s sh %s/deploy/run.sh createsuperuser\n' "$APP_USER" "$APP_DIR"
