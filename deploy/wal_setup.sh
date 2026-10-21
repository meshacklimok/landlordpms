#!/bin/sh
# Continuous database backup: pgBackRest ships every WAL file to a Backblaze B2 bucket (doc 18 "Backups", doc 22).
# Run once as root on the server, with the bucket details in the environment (never in this file):
#   WAL_BUCKET=nyumbapms-wal-x7k2 WAL_ENDPOINT=s3.eu-central-003.backblazeb2.com WAL_REGION=eu-central-003 \
#   WAL_KEY_ID=... WAL_KEY=... sh deploy/wal_setup.sh
# Restarts PostgreSQL once (a few seconds). Prints the encryption passphrase once: save it off the server.
set -eu
: "${WAL_BUCKET:?}" "${WAL_ENDPOINT:?}" "${WAL_REGION:?}" "${WAL_KEY_ID:?}" "${WAL_KEY:?}"
PGVER="${PGVER:-16}"
STANZA=landlordpms
CONF=/etc/pgbackrest/pgbackrest.conf

apt-get install -y pgbackrest >/dev/null
mkdir -p /etc/pgbackrest

if [ -f "$CONF" ] && grep -q '^repo1-cipher-pass' "$CONF"; then
    CIPHER=$(sed -n 's/^repo1-cipher-pass=//p' "$CONF")
    NEW_CIPHER=""
else
    CIPHER=$(openssl rand -base64 32)
    NEW_CIPHER=yes
fi

umask 077
cat > "$CONF" <<EOF
[global]
repo1-type=s3
repo1-path=/pgbackrest
repo1-s3-bucket=$WAL_BUCKET
repo1-s3-endpoint=$WAL_ENDPOINT
repo1-s3-region=$WAL_REGION
repo1-s3-key=$WAL_KEY_ID
repo1-s3-key-secret=$WAL_KEY
repo1-s3-uri-style=path
repo1-cipher-type=aes-256-cbc
repo1-cipher-pass=$CIPHER
repo1-retention-full=2
repo1-retention-diff=7
repo1-bundle=y
repo1-block=y
compress-type=lz4
process-max=1
archive-async=y
spool-path=/var/spool/pgbackrest
log-level-console=info

[$STANZA]
pg1-path=/var/lib/postgresql/$PGVER/main
EOF
chown postgres:postgres "$CONF"
chmod 640 "$CONF"
mkdir -p /var/spool/pgbackrest /var/log/pgbackrest
chown postgres:postgres /var/spool/pgbackrest /var/log/pgbackrest

cat > "/etc/postgresql/$PGVER/main/conf.d/wal_archive.conf" <<EOF
archive_mode = on
archive_command = 'pgbackrest --stanza=$STANZA archive-push %p'
archive_timeout = 60
EOF

chmod 644 "/etc/postgresql/$PGVER/main/conf.d/wal_archive.conf"
systemctl restart "postgresql@$PGVER-main"
sleep 3
sudo -u postgres pgbackrest --stanza=$STANZA stanza-create
sudo -u postgres pgbackrest --stanza=$STANZA check
sudo -u postgres pgbackrest --stanza=$STANZA --type=full backup

# weekly full (Sunday 04:30), daily differential (the other days 04:30; after the 02:10 dump and 03:40 offsite copy); server time is Africa/Nairobi
cat > /etc/cron.d/landlordpms-wal <<EOF
30 4 * * 0 postgres pgbackrest --stanza=$STANZA --type=full backup
30 4 * * 1-6 postgres pgbackrest --stanza=$STANZA --type=diff backup
EOF

echo
echo "WAL archiving is on. Bucket: $WAL_BUCKET"
if [ -n "$NEW_CIPHER" ]; then
    echo "SAVE THIS ENCRYPTION PASSPHRASE OFF THE SERVER (without it the backups cannot be read):"
    echo "$CIPHER"
fi
