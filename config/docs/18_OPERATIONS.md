# 18 — Operations

How landlordpms runs in production, and what to do when something goes wrong. Decisions: D-061 (status and health), D-062 (jobs, backups, alerts, logging, settings), D-063 (support and import concierge).

Items marked **[VERIFY]** depend on the hosting provider, which is not chosen yet.

## 1. Processes

| Process | What it is |
|---|---|
| Web | Django behind a WSGI server (for example gunicorn, 2 × CPU + 1 workers), with `DJANGO_SETTINGS_MODULE=config.settings.prod`. |
| Reverse proxy | nginx or the platform's load balancer. It ends TLS and sets `X-Forwarded-Proto` and `X-Forwarded-For` (one proxy: `TRUSTED_PROXY_COUNT=1`). It serves `/static/` after `collectstatic`. It must **not** serve `/media/`, because uploaded files go through permission-checked views. |
| PostgreSQL | Version 16 or newer. The web and cron use the same database user. |
| Redis | The cache. Rate limits, login-code cooldowns, the status page and alert state all live here. |
| Cron | Runs the scheduled commands below. |

## 2. Cron table

All times are Africa/Nairobi. Each line runs `python manage.py <command>` with the production settings and environment.

```
*/5  *  *  *  *  send_due_messages
10   2  *  *  *  backup
30   5  *  *  *  subscriptions_daily
0    6  *  *  *  billing_daily
30   6  *  *  *  mpesa_daily
0    3  *  *  0  purge_otp_codes
15   3  *  *  0  purge_import_previews
*/15 *  *  *  *  check_jobs
```

Each command except `check_jobs` records a `JobRun` row (in admin under Core › Job runs). `check_jobs` emails an alert when a job has not succeeded within its limit:

| Job | Must succeed within |
|---|---|
| `send_due_messages` | 30 minutes |
| `billing_daily`, `mpesa_daily`, `subscriptions_daily`, `backup` | 26 hours |
| `purge_otp_codes`, `purge_import_previews` | 8 days |

To add a job, subclass `core.jobs.JobCommand`, then add it to `core.jobs.JOBS` and to the table above.

## 3. Environment variables

**Required in production** (the app refuses to start without them):

- `SECRET_KEY`: at least 50 random characters.
- `ALLOWED_HOSTS`, `SITE_URL`.
- The database: `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`.
- `REDIS_URL`.
- Email: `EMAIL_HOST`, `DEFAULT_FROM_EMAIL`.
- `FIELD_ENCRYPTION_KEYS`.
- SMS: `AT_USERNAME`, `AT_API_KEY`, `AT_CALLBACK_TOKEN` (when `SMS_BACKEND` is Africa's Talking).
- WhatsApp: `WA_*` (when WhatsApp is on).

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
| `DB_CONN_MAX_AGE` | `60` | Seconds a database connection is reused. |
| `SUPPORT_EMAIL` | empty | Where support requests are emailed. |
| `SUPPORT_WHATSAPP` | empty | A number such as `254700000000`. Shown as a wa.me link. |
| `DATA_PROTECTION_EMAIL` | empty | Shown on `/security/`. |
| `HOSTING_LOCATION` | empty | Shown on `/security/`, for example "Nairobi, Kenya" **[VERIFY]**. |

Platform billing settings (`PLATFORM_*`, `SMS_PRICE`, `SMS_WALLET_ENFORCED`, `ETIMS_ADAPTER`) are in D-060.

## 4. Deploying

1. Take a backup first: `python manage.py backup`.
2. Pull the release and run `pip install -r requirements.txt`.
3. `python manage.py migrate`.
4. `python manage.py sync_access_catalog`, when capabilities changed.
5. `python manage.py collectstatic --noinput`.
6. `python manage.py check --deploy`. It must report no issues.
7. Reload the web workers gracefully.
8. Open `/healthz` and `/status/`. Log in and open the home page.

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

Restore the latest backup into a scratch database on a separate machine (steps 2 to 4), then run `python manage.py check` and count rows in a few tables against production. Record each test here.

| Date | Backup restored | Time taken | Result | By |
|---|---|---|---|---|
| | | | | |

## 6. Alerts and error tracking

- `check_jobs` emails `OPS_ALERT_EMAILS` when a job is late or failed, or when the database or cache check fails. The same problem is emailed again after 6 hours if it is still there. An "all clear" email follows once it is fixed.
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
- **Africa's Talking, WhatsApp and Daraja keys.** Replace them at the provider, then in the environment or the organization's M-Pesa settings. `AT_CALLBACK_TOKEN` is part of the callback URLs, so update them at Africa's Talking at the same time.

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
