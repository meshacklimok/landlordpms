# 18 — Operations

How NyumbaPMS runs in production, and what to do when something goes wrong. Decisions: D-061 (status and health), D-062 (jobs, backups, alerts, logging, settings), D-063 (support and import concierge).

Items marked **[VERIFY]** depend on the hosting provider. The pilot runs on one DigitalOcean Droplet in Frankfurt (D-134; D-122 had chosen Hetzner); doc 22 is the setup as done, doc 21 the longer Hetzner version of the same steps, and `deploy/update.sh` does the deploying steps below.

## 1. Processes

| Process | What it is |
|---|---|
| Web | Django behind a WSGI server (for example gunicorn, 2 × CPU + 1 workers), with `DJANGO_SETTINGS_MODULE=config.settings.prod`. |
| Reverse proxy | nginx or the platform's load balancer. It ends TLS and sets `X-Forwarded-Proto` and `X-Forwarded-For` (one proxy: `TRUSTED_PROXY_COUNT=1`). It serves `/static/` after `collectstatic`. It must **not** serve `/media/`, because uploaded files go through permission-checked views. `/sw.js` and `/manifest.webmanifest` come from Django (D-080); do not let the proxy or a CDN cache `/sw.js`, or phones keep an old service worker. |
| PostgreSQL | Version 16 or newer. The web and cron use the same database user. It needs `CREATEDB` for the monthly restore test (D-098). |
| Redis | The cache. Rate limits, login-code cooldowns, the status page and alert state all live here. |
| Cron | Runs the scheduled commands below. |

## 2. Cron table

All times are Africa/Nairobi: set the server's time zone to it. The file to install is `deploy/crontab` (`crontab deploy/crontab` as the app user). Each line calls `deploy/run.sh <command>`, which loads `/etc/landlordpms.env`, uses the production settings, and skips a run while the previous one of the same command is still going (`flock`). A test keeps the file in step with `core.jobs.JOBS` (D-098).

```
*/5  *  *  *  *  send_due_messages
*/5  *  *  *  *  send_webhooks
*/15 *  *  *  *  maintenance_alerts
*/5  *  *  *  *  run_data_exports
10   2  *  *  *  backup
30   5  *  *  *  subscriptions_daily
0    6  *  *  *  billing_daily
30   6  *  *  *  mpesa_daily
45   23 *  *  *  reports_daily
0    3  *  *  0  purge_otp_codes
15   3  *  *  0  purge_import_previews
30   3  *  *  0  purge_assistant
45   3  *  *  0  purge_sessions
50   3  *  *  0  purge_messages
0    4  1  *  *  restore_test
*/15 *  *  *  *  check_jobs
```

Each command except `check_jobs` records a `JobRun` row (in admin under Core › Job runs). `check_jobs` emails an alert when a job has not succeeded within its limit:

| Job | Must succeed within |
|---|---|
| `send_due_messages`, `send_webhooks`, `run_data_exports` | 30 minutes |
| `maintenance_alerts` | 1 hour |
| `billing_daily`, `mpesa_daily`, `subscriptions_daily`, `reports_daily`, `backup` | 26 hours |
| `purge_otp_codes`, `purge_import_previews`, `purge_assistant`, `purge_sessions`, `purge_messages` | 8 days |
| `restore_test` | 35 days |

To add a job, subclass `core.jobs.JobCommand`, then add it to `core.jobs.JOBS`, to `deploy/crontab` and to the table above.

## 3. Environment variables

**Required in production** (the app refuses to start without them):

- `SECRET_KEY`: at least 50 random characters.
- `ALLOWED_HOSTS`, `SITE_URL`.
- The database: `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`.
- `REDIS_URL`.
- Email: `EMAIL_HOST`, `DEFAULT_FROM_EMAIL`.
- `FIELD_ENCRYPTION_KEYS`.
- SMS: `AT_USERNAME`, `AT_API_KEY`, `AT_CALLBACK_TOKEN` (when `SMS_BACKEND` is Africa's Talking).
- Webhooks (D-083): `WEBHOOKS_ALLOW_PRIVATE=1` lets development send to `http://` and private addresses such as a local test server. Production refuses to start with it. The server must be able to make outgoing https connections.
- USSD (D-081): `USSD_CODE` (the code shown to tenants, e.g. `*384*123#`). At Africa's Talking, point the USSD channel's callback at `https://<host>/hooks/ussd/africastalking/<AT_CALLBACK_TOKEN>/`.
- WhatsApp: `WA_*` (when WhatsApp is on).
- Subscription payments to us (D-126), optional: `PLATFORM_CALLBACK_TOKEN` (32+ characters) with `PLATFORM_DARAJA_ENVIRONMENT`, `PLATFORM_DARAJA_CONSUMER_KEY`, `PLATFORM_DARAJA_CONSUMER_SECRET`, `PLATFORM_DARAJA_PASSKEY` and `PLATFORM_PAYBILL` (or `PLATFORM_DARAJA_SHORTCODE`) for our Paybill, and/or `PLATFORM_PAYSTACK_SECRET_KEY` for our Paystack. Production refuses to start with half a Daraja setup or a short token. See 17_MPESA_GO_LIVE.md section 7.
- Alerts on staff phones (D-089): `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY` (make a pair once with `manage.py vapid_keys` and keep it: a new pair turns off every phone's alerts) and `VAPID_SUBJECT` (`mailto:` or `https://` contact for the push services). Leave the keys empty to not offer alerts. The server must be able to reach the push services over https.
- The assistant (D-091, D-115): `OPENAI_API_KEY` (from the OpenAI platform; leave empty to not offer the assistant) and optionally `ASSISTANT_MODEL` (default `gpt-5.6-terra`). `ASSISTANT_BACKEND` must stay `ai_assistant.client.OpenAIClient` in production. The server must be able to reach `https://api.openai.com`. Owners still switch the assistant on for their organization; questions, the names and figures used to answer them are sent to OpenAI, never phone numbers, emails or ID numbers.

**Operations:**

| Variable | Default | Meaning |
|---|---|---|
| `OPS_ALERT_EMAILS` | `ADMINS` | Comma-separated. Who gets job and health alerts. |
| `ADMINS` | empty | `Name <email>, …`. Gets error emails when Sentry is not used. |
| `BACKUP_DIR` | `<repo>/backups` | Where `backup` writes. Must be outside the web root. |
| `PG_DUMP`, `PG_RESTORE` | `pg_dump`, `pg_restore` | Paths to the PostgreSQL tools, the same major version as the server. |
| `SENTRY_DSN` | empty | Turns on Sentry (`pip install sentry-sdk`). |
| `SENTRY_ENVIRONMENT`, `SENTRY_RELEASE` | `production`, empty | Shown in Sentry. |
| `LOG_LEVEL` | `INFO` | The console log level. |
| `DB_POOL` | `1` | Keep a pool of connections in each process (doc 20 §1). |
| `DB_POOL_MIN`, `DB_POOL_MAX`, `DB_POOL_TIMEOUT` | `1`, `4`, `10` | Pool size per process, and seconds to wait for a free connection. Workers × max + cron must stay under the server's `max_connections`. |
| `DB_CONN_MAX_AGE` | `60` | Seconds a connection is reused, only when `DB_POOL=0`. |
| `DB_SSLMODE` | `prefer` | `require` or `verify-full` when the database is on another machine. |
| `DB_CONNECT_TIMEOUT` | `10` | Seconds to wait for the database to answer a new connection. |
| `DB_STATEMENT_TIMEOUT_MS` | `30000` | A statement running longer is cancelled. `0` turns it off (for `migrate` on large tables). |
| `DB_IDLE_IN_TRANSACTION_MS` | `60000` | A transaction left open and idle this long is ended. |
| `DB_APP_NAME` | `landlordpms` | How connections are named in `pg_stat_activity`; `deploy/run.sh` sets it per job. |
| `DB_ALERT_CONNECTIONS_PERCENT` | `80` | Alert when this share of `max_connections` is in use. |
| `DB_ALERT_LONG_QUERY_SECONDS` | `300` | Alert on a query or open transaction older than this. |
| `DB_ALERT_DISK_FREE_PERCENT`, `DB_ALERT_DISK_FREE_GB` | `10`, `2` | Alert when the backup or media disk has less free. |
| `MESSAGE_TEXT_DAYS` | `730` | Days a sent message keeps its text and address; `purge_messages` then clears them and keeps the row **[VERIFY]**. |
| `DB_ALERT_SIZE_MB` | `0` (off) | Alert when the database grows past this; set below the provider's limit. |
| `SECURITY_ALERT_SIGNIN_HOUR`, `SECURITY_ALERT_TWO_STEP_HOUR`, `SECURITY_ALERT_CSP_HOUR` | `300`, `100`, `500` | Alert when an hour has more failed sign-ins, wrong two-step codes or CSP reports than this, across the platform (D-108). Counted in the shared cache. |
| `SUPPORT_EMAIL` | empty | Where support requests are emailed. |
| `SUPPORT_WHATSAPP` | empty | A number such as `254700000000`. Shown as a wa.me link, and on the M-Pesa setup pages with a message already filled in (organization, Paybill, step). |
| `MPESA_SETUP_VIDEO_URL` | empty | Fallback only. Set the Paybill setup video link in Django admin (Mpesa › Setup video); it shows on the M-Pesa setup pages when a link is set, and nothing shows when it is empty. |
| `DATA_PROTECTION_EMAIL` | empty | Shown on `/security/` and `/privacy/`. |
| `HOSTING_LOCATION` | empty | Shown on `/security/` and `/privacy/`, for example "Nairobi, Kenya" **[VERIFY]**. |
| `SESSION_IDLE_MINUTES`, `SESSION_MAX_HOURS` | `60`, `12` | Staff are logged out after this long without a request, and this long after logging in (D-107). |
| `PORTAL_SESSION_IDLE_DAYS`, `PORTAL_SESSION_MAX_DAYS` | `14`, `30` | The same for tenants in the portal. |
| `CSP_REPORT_ONLY` | empty | `1` sends the Content Security Policy as report-only: nothing is blocked, and what would be is logged under `django.security.csp`. Use it to try a policy change, then remove it (D-107). |
| `LEGAL_NAME` | "NyumbaPMS" | The company's registered name on `/terms/` and `/privacy/` (D-095). |
| `LEGAL_REGISTRATION`, `LEGAL_ADDRESS` | empty | The company number (for example "Company No. PVT-…") and its postal and physical address, on both pages (D-124). |
| `ODPC_REGISTRATION` | empty | The number on the ODPC registration certificate, on `/privacy/` (D-124). |
| `LEGAL_REVIEWED` | false | Set to true once a lawyer has checked the Terms and Privacy Policy; removes the draft notice. Change `support/legal.py` `VERSION` whenever either text changes in a way that matters. |

Platform billing settings (`PLATFORM_*`, `SMS_PRICE`, `SMS_WALLET_ENFORCED`, `ETIMS_ADAPTER`) are in D-060.

## 4. Deploying

1. Take a backup first: `python manage.py backup`.
2. Pull the release and run `pip install -r requirements.txt`.
3. `python manage.py migrate`, as the owner role (`deploy/db_roles.sql`). Add `DB_STATEMENT_TIMEOUT_MS=0` when a migration builds an index on a large table.
4. `python manage.py sync_access_catalog`, when capabilities changed.
5. `python manage.py collectstatic --noinput`.
6. `python manage.py check --deploy`. It must report no issues.
7. Reload the web workers gracefully.
8. Open `/healthz` and `/status/`. Log in and open the home page.

Before the first launch, `python manage.py launch_check` lists what the server still lacks: PostgreSQL tools that match the server, a writable backup folder with a backup from the last day, a cache that answers, every job having run (including a restore test), alert recipients, and a real mail server. It fails until all are done, and reminds you of the two things it cannot see: the off-site copy and the uptime monitor (D-098).

`python manage.py db_health` shows the database's connections, size, disk, slow queries and indexes, and fails when something needs attention (doc 20). `python manage.py scale_test --leases 200 --confirm` times every list page against a large organization and rolls it back: run it on staging or a restored copy, never live. For many users at once, `loadtest_setup` and `tests/loadtest/locustfile.py` (doc 20 §30) run against a scratch database that is dropped afterwards. Set `DB_POOL_MAX` to at least gunicorn's threads per worker.

Before running a migration that deletes or rewrites data, restore the latest backup into a scratch database and run it there first.

## 5. Backups

`python manage.py backup` writes two files to `BACKUP_DIR`:

- `db-YYYYmmdd-HHMMSS.dump`: `pg_dump -Fc` of the whole database. It is checked with `pg_restore --list` and must contain table data.
- `media-YYYYmmdd-HHMMSS.tar.gz`: the uploaded files (ID copies, meter photos, condition-report photos, support attachments).

It keeps the latest set of each of the last 7 days, the first set of each of the last 4 weeks, and the first of each of the last 12 months. It deletes the others.

**Off-site copy.** After the nightly backup, sync `BACKUP_DIR` to encrypted object storage in a different data centre (for example `rclone sync` with a crypt remote) **[VERIFY: provider]**. The storage keys must not be able to delete old versions: turn on object versioning or object lock. Without an off-site copy, losing the server loses the backups too.

Targets: **RPO 24 hours** (at most one day of data lost), **RTO 4 hours** (running again on a new server).

### Restore

1. Build a server with PostgreSQL and Redis. Check out the release that made the backup.
2. `createdb -O <db user> landlordpms`
3. `pg_restore --no-owner --role=<db user> -d landlordpms db-….dump`
4. `tar -xzf media-….tar.gz -C <MEDIA_ROOT's parent>`
5. Set the same `FIELD_ENCRYPTION_KEYS` and `SECRET_KEY`. Without the encryption keys, the stored M-Pesa credentials cannot be read, and each organization must enter them again.
6. `python manage.py migrate` (it should have nothing to do), then `check --deploy`, then start.
7. Check: log in, open an invoice PDF and a meter photo, and look at `/status/`.
8. Messages that were due while the site was down go out on the next `send_due_messages` run. Payments that M-Pesa sent while the site was down may be missing: compare against each Paybill statement and record them by hand.

### Monthly restore test

`python manage.py restore_test` runs from cron on the 1st of each month. It restores the latest backup into a scratch database (`<db>_restore_test`) on the same server, checks that every live table is there with migrations recorded, compares row counts of organizations, units, tenants, leases, invoices and payments against live, reads the media archive, and drops the scratch database (`--keep` keeps it; `--file` picks another dump). It needs a database user that may create databases. Its one-line result is in Core › Job runs, and `check_jobs` alerts if none has succeeded in 35 days.

This proves the backups restore, but not that the server can be rebuilt. Each quarter, and before launch, also restore on a separate machine (steps 1 to 7) and time it against the RTO. Record each hand test here.

| Date | Backup restored | Time taken | Result | By |
|---|---|---|---|---|
| | | | | |

## 6. Alerts and error tracking

- `check_jobs` emails `OPS_ALERT_EMAILS` when a job is late or failed, when the database or cache check fails, or when `db_health` finds a problem (connections, long queries, low disk, size, unapplied migrations). The same problem is emailed again after 6 hours if it is still there. An "all clear" email follows once it is fixed.
- Point an external uptime monitor at `https://<site>/healthz` every minute. It returns 200 when everything is fine and 503 otherwise. `/healthz` is not redirected to HTTPS by Django, so the load balancer can call it over HTTP.
- With `SENTRY_DSN` set, unhandled errors go to Sentry without personal data. Without it, they are emailed to `ADMINS`.
- Logs go to the console at `LOG_LEVEL`; the process manager keeps them. Request bodies, codes and secrets are never logged.

## 7. Incidents

1. **Say so.** In admin, go to Support › Incidents › Add. Give it a title a landlord understands, the impact and "Investigating", and write the first update. The status page shows it within a minute.
2. **Fix.** Check `/healthz`, the logs, the job runs and Sentry. Roll back the release if it started with a deploy.
3. **Update** the incident when something changes, at least every hour while it is open.
4. **Resolve.** Set the status to Resolved, with a last update saying what happened and whether any data was affected.
5. **Follow up.** If money records may be wrong (missed M-Pesa callbacks, jobs that did not run), check the M-Pesa inbox and statements, and run `billing_daily` by hand. It is safe to run twice.
6. **Personal data breach.** Tell the affected landlords and the Office of the Data Protection Commissioner within 72 hours of learning of it (Data Protection Act 2019, s.43) **[VERIFY with counsel]**.

## 8. Rotating secrets

- **`FIELD_ENCRYPTION_KEYS`.** Put the new key first and keep the old ones after it (`new,old`), then deploy. New writes use the new key, and old values still read. To re-encrypt everything, open and save each M-Pesa account's settings, or run a one-off script. Remove the old key only after that.
- **`SECRET_KEY`.** Changing it logs everyone out and makes password-reset and invitation links stop working. In development, the field encryption key is derived from it, so set `FIELD_ENCRYPTION_KEYS` there before rotating.
- **Africa's Talking, WhatsApp and Daraja keys.** Replace them at the provider, then in the environment or the organization's M-Pesa settings. `AT_CALLBACK_TOKEN` is part of the callback URLs, so update them at Africa's Talking at the same time (SMS and USSD).

## 9. Support requests and the import concierge

- Requests arrive by email at `SUPPORT_EMAIL` and in admin under Support › Support requests. Set the status to Waiting while the customer owes a reply, and to Closed when done. `internal_note` is never shown to the customer.
- **Import help** (kind "Import my data for me"):
  1. Download the attachment from the request.
  2. Ask the owner to invite a support login (an email address we control) as a Manager, or as an Owner if balances are included.
  3. Log in as that member. Reshape the spreadsheet into the CSV templates from the Import page: units first, then tenants, then opening balances.
  4. Upload each file, read the preview with the customer if anything is unclear, and import it.
  5. Tell the customer it is done. Ask them to remove the support login, or remove it yourself if you were given Owner.
  6. Close the request, with a note of how many rows were imported.

  We never log in as a customer, and there is no impersonation.
- **Time to first invoice.** Admin › Organizations shows how long each organization took from sign-up to its first invoice. Filter by "Has invoiced: No" to find pilots who have stalled.
