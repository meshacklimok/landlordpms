# 26. Maintenance and upgrades without hurting real users' data

How to change the live NyumbaPMS server (new code, new tables, package updates, server updates) without losing or damaging a landlord's records or a tenant's payment. Read it once end to end, then use the checklist in section 12 each time.

Where things are (pilot server, doc 22):

| Thing | Place |
|---|---|
| Server | DigitalOcean Droplet, Frankfurt, `64.226.127.102` (`ssh root@64.226.127.102`) |
| App code | `/srv/landlordpms` (owned by user `landlordpms`) |
| Settings file (secrets) | `/etc/landlordpms.env` and `/etc/landlordpms-owner.env` (owner database role, used only by `migrate`) |
| Database | PostgreSQL, database `landlordpms` |
| Backups | `/var/backups/landlordpms` (`db-*.dump`, `media-*.tar.gz`) |
| Web workers | systemd unit `landlordpms` (gunicorn on 127.0.0.1:8000) behind nginx |
| Scheduled jobs | the `landlordpms` user's crontab, from `deploy/crontab` |
| Helper scripts | `deploy/run.sh` (runs one manage.py command), `update.sh`, `offsite.sh`, `monthly_check.sh`, `maintenance.sh` |

Every command below that starts with `sh deploy/run.sh` loads the production settings for you. Run it as the app user:
`runuser -u landlordpms -- sh /srv/landlordpms/deploy/run.sh <command>`.

---

## 0. The rules that protect the data

1. **Never experiment on the live database.** Anything new (a migration, an import, a data fix, a load test) is tried first on a copy of the data.
2. **Never upgrade without a backup you have just taken and know works.** A backup that was never restored is a hope, not a backup.
3. **Keep the laptop away from production.** Your laptop `.env` must point at a local database. Never run `flush`, `loaddata`, `scale_test`, `loadtest_setup` or the test suite against the live database.
4. **One change at a time.** Do not mix a code release, a package update and a server reboot in one night. If something breaks you will not know which one did it.
5. **Pick a quiet time.** Late night Nairobi time. Avoid the 1st to the 7th of the month (rent days) and payday weekends. Safaricom sends M-Pesa confirmations all day.
6. **Be able to go back.** Before you start you must know how you would undo it (section 10).

Why: this database holds money records. Payments, invoices and ledger rows are append-only (database triggers refuse updates). A mistake there cannot be quietly fixed afterwards, so the care goes in before.

---

## 1. Plan the change (the day before)

1. **Write down what changes**: code only, code plus migrations, new packages, or the server itself.
2. **Read the migrations** that will run:
   ```bash
   # on your laptop, in the project folder
   git diff main --stat -- '*/migrations/*'
   python manage.py showmigrations | grep "\[ \]"
   python manage.py sqlmigrate <app> <number>
   ```
   Look for: dropped or renamed columns, `RunPython` that rewrites rows, new `NOT NULL` columns without a default, indexes on big tables. Why: these are the steps that can lock tables, lose data or fail halfway.
3. **Run the tests on your laptop** (local database only):
   ```bash
   python manage.py test
   ```
   Why: the suite includes the route access guard, query counts and hardening tests. A failure here is a failure in front of users.
4. **Commit and tag the release** so you know exactly what runs on the server:
   ```bash
   git status            # must be clean
   git tag -a v2026.10.17 -m "what changed"
   ```
   Why: if you must go back, you check out the previous tag instead of guessing.
5. **Tell the pilot landlords** if you expect more than a minute of downtime (WhatsApp or SMS: "NyumbaPMS will be updated tonight 11pm to 11:30pm").

---

## 2. One-time setup of maintenance mode (do this once, not every upgrade)

Maintenance mode shows a "we are updating" page and pauses the scheduled jobs, so nobody saves a payment or a meter reading in the middle of a migration.

Files in the repository (already created):

- `deploy/maintenance.html`: the page people see. Self-contained, light and dark.
- `deploy/maintenance.sh`: `on`, `off`, `status`.
- `deploy/nginx.conf`: now has the maintenance block (an `error_page 503`, a `@maintenance` location, and `if (-f /srv/landlordpms/maintenance.flag) { return 503; }`).

The live nginx file was made from `nginx.conf` when the server was set up, so the new block is **not** on the server yet. Add it once:

```bash
ssh root@64.226.127.102
cd /srv/landlordpms
runuser -u landlordpms -- git pull --ff-only          # brings maintenance.html, maintenance.sh, nginx.conf
nano /etc/nginx/sites-available/landlordpms
```

In the `server { ... server_name <your domain>; ... }` block, after the line `limit_conn nyumba_conn 30;`, paste:

```nginx
    error_page 503 @maintenance;
    location @maintenance {
        root /srv/landlordpms/deploy;
        rewrite ^ /maintenance.html break;
        add_header Retry-After 600 always;
    }
    if (-f /srv/landlordpms/maintenance.flag) { return 503; }
```

If certbot made a second `server { listen 443 ssl ... }` block, paste the same lines into that block too (the one people actually reach). Then:

```bash
nginx -t                       # must say "syntax is ok"
systemctl reload nginx
```

Test it (this is the only time you do it in daytime, it takes seconds):

```bash
bash /srv/landlordpms/deploy/maintenance.sh on
curl -I https://app.nyumbapms.com/          # expect: HTTP/2 503 and Retry-After: 600
bash /srv/landlordpms/deploy/maintenance.sh off
curl -I https://app.nyumbapms.com/          # expect: HTTP/2 200 (or the normal redirect)
```

Why a 503 and not a normal page: browsers, search engines and payment providers read 503 as "temporarily away, come back". Safaricom and Paystack retry callbacks that get a 503, and Google does not de-index the site.

Note: the update script `update.sh` talks to gunicorn directly on port 8000, so its final health check still works while maintenance mode is on.

---

## 3. Take the backups (before touching anything)

### 3.1 A fresh database and media backup

```bash
ssh root@64.226.127.102
runuser -u landlordpms -- sh /srv/landlordpms/deploy/run.sh backup
ls -lh /var/backups/landlordpms | tail -4
```

It writes `db-YYYYmmdd-HHMMSS.dump` (`pg_dump -Fc`, the whole database, checked with `pg_restore --list`) and `media-YYYYmmdd-HHMMSS.tar.gz` (uploaded files: ID copies, meter photos, support attachments).

Why: the nightly backup is at 02:10. If you upgrade at 23:00 you would lose up to 21 hours on a rollback. A backup taken just now loses nothing.

Check the size: the dump should be similar to or larger than last night's. A tiny or zero file means the backup failed. Stop and fix it.

### 3.2 Copy it off the server

```bash
runuser -u landlordpms -- sh /srv/landlordpms/deploy/offsite.sh
runuser -u landlordpms -- rclone lsl offsite-crypt:nyumbapms | sort -k2,3 | tail -4
```

Why: if the server itself is damaged, backups stored on it are gone too. `offsite.sh` uses `rclone copy` (not `sync`), so something deleted here is never deleted there.

### 3.3 Take a Droplet snapshot (full-server undo)

In the DigitalOcean panel: Droplet, Snapshots, Take snapshot, name it `before-v2026.10.17`. (Or `doctl compute droplet-action snapshot <droplet-id> --snapshot-name before-v2026.10.17` if `doctl` is set up.) It takes a few minutes.

Why: the dump restores the *data*. The snapshot restores the *whole machine* (code, packages, nginx, cron, certificates). Use it when the server update, not the app, breaks.

### 3.4 Prove the backup restores

```bash
runuser -u landlordpms -- sh /srv/landlordpms/deploy/run.sh restore_test
```

This restores the latest dump into a scratch database (`landlordpms_restore_test`), checks every table and the migration list, compares row counts for organizations, units, tenants, leases, invoices and payments against live, reads the media archive, and drops the scratch database. It prints a one-line result. It must say it passed. (It already runs on the 1st of each month; run it by hand before a risky upgrade.)

Why: this is the only proof the file you just made can bring the data back.

### 3.5 Note the numbers before you start

```bash
runuser -u landlordpms -- sh /srv/landlordpms/deploy/run.sh db_health
runuser -u landlordpms -- sh /srv/landlordpms/deploy/run.sh shell -c "
from accounts.models import Organization
from payments.models import Payment
from billing.models import Invoice
from leases.models import Lease
print('orgs', Organization.objects.count(), 'leases', Lease.objects.count(),
      'invoices', Invoice.objects.count(), 'payments', Payment.objects.count())
"
```

Write the four numbers down. Why: "all the data is still there" is a claim you can check only if you recorded the before.

---

## 4. Try the change on a copy first

On your laptop (not the server), with a **local** database:

1. Download last night's dump from the offsite storage (or `scp root@64.226.127.102:/var/backups/landlordpms/db-<latest>.dump .`). Real tenant data is personal data: keep it on an encrypted disk, and delete it when done.
2. Restore it into a scratch database:
   ```bash
   createdb nyumba_copy
   pg_restore --no-owner -d nyumba_copy db-<latest>.dump
   ```
3. Point a temporary `.env` at `nyumba_copy`, check out the new release, and run:
   ```bash
   python manage.py migrate
   python manage.py check --deploy
   python manage.py runserver
   ```
4. Click through: sign in, a lease, an invoice PDF, record a test payment, the tenant portal, reports.
5. Drop it when you are done: `dropdb nyumba_copy`, and delete the dump.

Why: this shows whether the migrations work on **real-shaped data** (old rows with odd values), which a clean test database never does. If a migration deletes or rewrites data, doc 18 requires this step.

For a large-data check use `scale_test` only on this copy, never live:
`python manage.py scale_test --leases 200 --confirm`.

---

## 5. Go into maintenance mode

```bash
ssh root@64.226.127.102
bash /srv/landlordpms/deploy/maintenance.sh on
```

What it does and why:

- Saves the app user's crontab to `/root/crontab.before-maintenance`, then installs an empty one. Why: `send_due_messages`, `billing_daily`, `mpesa_daily`, `maintenance_alerts` and `send_webhooks` must not run against half-changed tables.
- Creates `/srv/landlordpms/maintenance.flag`. nginx sees the file and answers every request with the 503 page.
- Reloads nginx and prints how many `manage.py` jobs are still running.

Wait until that count is 0 (run `pgrep -u landlordpms -fa manage.py` to look). Jobs that started before the pause finish on their own, usually in seconds.

Confirm from your phone (mobile data, not the office Wi-Fi): the site shows "We are updating NyumbaPMS".

Note: while the site is in maintenance, M-Pesa confirmations (C2B) and Paystack webhooks get a 503. Safaricom and Paystack retry. Section 9 shows how to catch any that do not come back.

---

## 6. Do the upgrade

The script `update.sh` does the whole sequence in a safe order, and it takes its own backup first (a second one, which is fine):

```bash
bash /srv/landlordpms/deploy/update.sh
```

What each step does and why:

| Step | What | Why |
|---|---|---|
| Backup | `run.sh backup` | Last point to go back to |
| Code | `git pull --ff-only` (or unpack `/root/nyumbapms.tar.gz`) | `--ff-only` refuses to merge; you get exactly the commits you tagged |
| Packages | `pip install -r requirements.txt` | New libraries the code needs |
| Tables | `manage.py migrate --noinput` as the **owner** role | Only the owner role may change the structure; the web role cannot (this is deliberate) |
| Access catalog | `sync_access_catalog` | Loads new permissions/capabilities |
| Static files | `collectstatic --noinput` | New CSS/JS |
| Check | `check --deploy` | Must report no issues |
| Cron | installs `deploy/crontab` | See the warning below |
| Reload | `systemctl reload landlordpms` | New workers start on the new code, old ones finish their requests |
| Health | `curl http://127.0.0.1:8000/healthz` | Proves the site answers |

**Warning:** `update.sh` re-installs `deploy/crontab`, which turns the scheduled jobs back on before you have checked the site. This is acceptable (migrations are finished by then), but do not be surprised. If you want the jobs paused until you have tested, run `bash deploy/maintenance.sh on` again after `update.sh` finishes, test, then `off`.

If `update.sh` stops with an error, **do not re-run it blindly**. Read the message, go to section 10.

### 6.1 A migration that takes long

A migration that builds an index on a big table can hit the statement timeout. `update.sh` already runs migrate with `DB_STATEMENT_TIMEOUT_MS=0` and `DB_POOL=0`. Watch it in a second terminal:

```bash
runuser -u landlordpms -- sh /srv/landlordpms/deploy/run.sh db_health
journalctl -u landlordpms -n 50 --no-pager
```

### 6.2 Making migrations safe to roll back

Write migrations in two releases when you can:

1. Release A: **add** the new column (nullable), write to both old and new.
2. Release B (a week later): remove the old column.

Why: if release A fails you can put the old code back, because the old code still finds its column.

---

## 7. Check it works (still in maintenance mode)

Maintenance mode hides the site from everyone but you can still reach the app directly on the server:

```bash
curl -fsS -H "Host: app.nyumbapms.com" http://127.0.0.1:8000/healthz
curl -fsS -H "Host: app.nyumbapms.com" http://127.0.0.1:8000/status/ | head
runuser -u landlordpms -- sh /srv/landlordpms/deploy/run.sh check --deploy
runuser -u landlordpms -- sh /srv/landlordpms/deploy/run.sh showmigrations | grep "\[ \]"   # must print nothing
journalctl -u landlordpms -n 80 --no-pager                                                  # no tracebacks
systemctl is-active landlordpms nginx postgresql redis-server                               # four times "active"
```

Then compare the counts you wrote down in step 3.5. They must be equal or only higher (new records), never lower.

To click through the site while others still see the maintenance page, use an SSH tunnel from your laptop:

```bash
ssh -L 8000:127.0.0.1:8000 root@64.226.127.102
```

and open `http://localhost:8000` (if the app refuses the host name, temporarily add `localhost` to `ALLOWED_HOSTS` in the env file; remove it after, and restart). Test with your own **test organization**: sign in, open a lease, create and delete a test charge, open an invoice PDF and a meter photo.

Why: you find the problem while users still see the polite page, not after they hit it.

---

## 8. Leave maintenance mode

```bash
bash /srv/landlordpms/deploy/maintenance.sh off
curl -I https://app.nyumbapms.com/healthz
```

`off` removes the flag file and puts the real crontab back (from `deploy/crontab`). Check the jobs are back:

```bash
crontab -u landlordpms -l | head
runuser -u landlordpms -- sh /srv/landlordpms/deploy/run.sh check_jobs
```

Then watch for 10 minutes:

```bash
journalctl -u landlordpms -f
```

Why watch: some bugs only show with real traffic and real data shapes. Stay until a few real pages and a few webhooks have passed without errors.

---

## 9. Catch payments that arrived during the window

If maintenance lasted more than a few minutes, some M-Pesa or Paystack notices may not have come back.

1. **M-Pesa (landlords' Paybills):** each landlord's own statement from the M-Pesa portal for the window. In the app, open the M-Pesa overview and the unmatched payments list. Record any payment missing (Safaricom normally retries; the app is built to ignore a repeat).
2. **Platform subscription payments** (our Paybill/Paystack): check the subscription payments page and the Paystack dashboard for the same window.
3. **Messages** that were due go out on the next `send_due_messages` run (every 5 minutes) automatically.
4. Run `daraja_sandbox_check` only on the sandbox, never live.

Why: a payment that is not recorded looks to the tenant like rent they did not pay, and may trigger a late fee or a reminder. Reconciling early prevents that.

---

## 10. If something goes wrong

Stay in maintenance mode while you fix it. Do not open the site to users on a broken state.

| What failed | What to do |
|---|---|
| `git pull` or `pip install` failed (nothing migrated yet) | Fix it, or `git -C /srv/landlordpms checkout <previous tag>`, then reinstall requirements: `runuser -u landlordpms -- /srv/landlordpms/venv/bin/pip install -r /srv/landlordpms/requirements.txt`, `systemctl restart landlordpms`. Data was not touched. |
| App code is broken, migrations were additive | Check out the previous tag, `pip install`, `systemctl restart landlordpms`. The new nullable columns do no harm. |
| A migration failed halfway | Postgres wraps each migration in a transaction, so a failed one rolls back. Read the error, fix the migration, run `update.sh` again. If an earlier migration in the same run had already finished and you need it undone, restore the backup (below). |
| Data is wrong or missing | Restore the backup you took in step 3.1 (below). |
| Server itself broken (OS update, disk, network) | Restore the Droplet snapshot from step 3.3 in the DigitalOcean panel. You lose everything written after the snapshot, so reconcile payments (section 9). |

### Restoring the database backup (data rollback)

This replaces live data with the backup. Anything entered after the backup is lost, which is why we keep the window short and the site in maintenance.

```bash
systemctl stop landlordpms
runuser -u landlordpms -- crontab -r || true         # jobs stay off (maintenance.sh on already did this)
# keep the damaged database for study instead of deleting it:
runuser -u postgres -- psql -c "ALTER DATABASE landlordpms RENAME TO landlordpms_broken_$(date +%Y%m%d);"
runuser -u postgres -- createdb -O landlordpms landlordpms
runuser -u postgres -- pg_restore --no-owner --role=landlordpms -d landlordpms /var/backups/landlordpms/db-<the one from step 3.1>.dump
git -C /srv/landlordpms checkout <previous tag>      # the code that matches that database
runuser -u landlordpms -- /srv/landlordpms/venv/bin/pip install -r /srv/landlordpms/requirements.txt
systemctl start landlordpms
runuser -u landlordpms -- sh /srv/landlordpms/deploy/run.sh migrate --check      # nothing to do
runuser -u landlordpms -- sh /srv/landlordpms/deploy/run.sh check --deploy
```

(If the DB role names or the rename fail because of open connections, stop gunicorn first, as above. Role names come from `deploy/db_roles.sql`; if your roles are separate for owner and app, grant them again with that file.)

Restore the uploaded files too, only if media was damaged:
`tar -xzf /var/backups/landlordpms/media-<same stamp>.tar.gz -C <parent of MEDIA_ROOT>`.

Keep the same `SECRET_KEY` and `FIELD_ENCRYPTION_KEYS`. Without the encryption keys the stored M-Pesa credentials cannot be read and every landlord would have to enter them again.

Then leave maintenance mode (section 8) and reconcile payments (section 9).

---

## 11. Maintenance that is not a release

### 11.1 Server updates (Ubuntu packages, security patches)

```bash
bash /srv/landlordpms/deploy/monthly_check.sh        # lists services, updates waiting, disk, certificate, off-site copy, db_health, launch_check
```

Install updates in a quiet hour, after a backup and snapshot:

```bash
apt update && apt upgrade
[ -f /var/run/reboot-required ] && echo "reboot needed"
```

Reboot only in maintenance mode, at night (about a minute of downtime):

```bash
bash /srv/landlordpms/deploy/maintenance.sh on
reboot
# ssh back in after a minute
systemctl is-active landlordpms nginx postgresql redis-server
bash /srv/landlordpms/deploy/maintenance.sh status      # the flag file survives a reboot: still ON
bash /srv/landlordpms/deploy/maintenance.sh off
```

A PostgreSQL major version change is not an ordinary update. Plan it like a migration: restore a copy on a second Droplet first, and keep the `PG_DUMP`/`PG_RESTORE` tools on the same major version as the server.

### 11.2 Fixing a record by hand

Landlords' data is theirs. If support must correct something:

1. Take a backup first (`run.sh backup`).
2. Use the app's own screens as that organization's owner/staff where possible, because they write the audit trail.
3. If you must use the shell, wrap it so an error undoes it, and write down what and why:
   ```bash
   runuser -u landlordpms -- sh /srv/landlordpms/deploy/run.sh shell
   >>> from django.db import transaction
   >>> with transaction.atomic():
   ...     # one small, exact change; print the row before and after
   ```
4. Never edit payments or ledger rows to "fix" a figure. Add a reversing entry the way the app does. The triggers refuse updates on purpose.

### 11.3 Experiments on production

Use a separate test organization (your own) on production for demos and trying features. Never use a real landlord's organization as a test bed. Use `seed_demo` only on a local or staging database.

### 11.4 Changing settings or secrets

Edit `/etc/landlordpms.env`, then `systemctl restart landlordpms` (reload is not enough for new env values). Keep a copy of the old value for rollback. Check with `run.sh check --deploy` and `launch_check` afterwards.

### 11.5 Certificates, domain, DNS

`certbot renew --dry-run` tests renewals without changing anything. `monthly_check.sh` shows the expiry date.

---

## 12. The checklist (print this, tick as you go)

**The day before**
- [ ] Read the migrations (`sqlmigrate`), none destroy data without a plan
- [ ] Tests pass on the laptop (local database)
- [ ] Release committed and tagged, working tree clean
- [ ] Tried on a restored copy of last night's backup
- [ ] Told pilot landlords the time

**Before (night, quiet hour)**
- [ ] `run.sh backup`, file size looks normal
- [ ] `offsite.sh`, new file visible in the off-site list
- [ ] Droplet snapshot taken and finished
- [ ] `run.sh restore_test` passed
- [ ] Wrote down org, lease, invoice, payment counts
- [ ] `maintenance.sh on`, phone shows the "updating" page, no job running

**During**
- [ ] `update.sh` finished with "Updated; the site answers."
- [ ] `showmigrations` shows nothing unapplied, `check --deploy` clean
- [ ] Logs show no tracebacks
- [ ] Counts equal or higher than before
- [ ] Tested through the tunnel with the test organization

**After**
- [ ] `maintenance.sh off`, jobs back (`crontab -l`, `check_jobs`)
- [ ] Watched logs for 10 minutes
- [ ] Reconciled M-Pesa and Paystack payments for the window
- [ ] Kept snapshot and backups for at least 7 days, then delete the snapshot
- [ ] Wrote the date, tag and result in doc 8 (decisions log) or doc 18

**Monthly**
- [ ] `monthly_check.sh`
- [ ] `restore_test` result is recent (1st of the month), and a hand-restore on a separate machine each quarter (doc 18, restore table)

---

## 13. Why the order is what it is

1. **Plan, copy test** come first because they find problems when nobody is affected.
2. **Backups** come before any change so every later step has a safe place to return to; the snapshot covers what a dump cannot (the machine).
3. **Restore test** turns the backup from hope into proof.
4. **Maintenance mode** stops new writes, so data cannot be half old and half new.
5. **Update** runs in a fixed order: code, packages, tables, static files, check, reload. Tables change before the new code serves anyone.
6. **Checks** run before users return.
7. **Reconciliation** closes the one gap maintenance cannot: payments that happen outside our control.
8. **Rollback** is decided in advance, not invented under pressure.
