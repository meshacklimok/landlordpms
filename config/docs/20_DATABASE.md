# 20 — Database

How the database is connected, protected, watched and tested, item by item (D-105). Each section says what is in place, where it lives, and what is still open. Commands are `python manage.py <name>`; the production settings are `config/settings/prod.py`.

**Summary.** PostgreSQL 16 or newer, through psycopg 3. Every organization's rows carry its id and every query is scoped by it. Money moves only through append-only ledgers, which the database itself now refuses to edit or delete. Backups run nightly and are restored into a scratch database monthly. `db_health` and the hourly alerts watch connections, long queries, disk and size.

| # | Item | Status |
|---|---|---|
| 1 | Connection | Done (pool, TLS, timeouts: new) |
| 2 | Configuration | Done (new) |
| 3 | Structure | Done |
| 4 | Migrations | Done |
| 5 | Primary keys | Done |
| 6 | Foreign keys | Done |
| 7 | Relationships | Done |
| 8 | Constraints | Done |
| 9 | Data validation | Done |
| 10 | Multi-tenant isolation | Done |
| 11 | Indexing | Done; trigram search indexes deferred |
| 12 | Query optimization | Done; arrears lightened, call list paged (§30) |
| 13 | Transactions | Done |
| 14 | Payment data integrity | Done (database triggers: new) |
| 15 | M-Pesa integration | Done; sandbox answers wait on keys (D-104) |
| 16 | Backup | Done |
| 17 | Restore testing | Done |
| 18 | Security | Done (separate roles, TLS check: new) |
| 19 | User permissions | Done (database roles: new) |
| 20 | Audit logs | Done (append-only trigger: new) |
| 21 | Error handling | Done |
| 22 | Performance monitoring | New (`db_health`) |
| 23 | Storage monitoring | New |
| 24 | Connection monitoring | New |
| 25 | Duplicate data prevention | Done |
| 26 | Data consistency | Done |
| 27 | Database testing | Done (new tests added) |
| 28 | Production monitoring | Done (database checks added to alerts) |
| 29 | Disaster recovery | Done; off-site copy is a server task |
| 30 | Scalability testing | New (`scale_test`) |
| – | Caching | Sessions cached (new); nothing else on purpose |
| – | Retention | Done; the message-text period needs confirming |

## 1. Connection

- psycopg 3 with Django's own connection pool. In production `DB_POOL` defaults to 1: each worker keeps `DB_POOL_MIN` (1) to `DB_POOL_MAX` (4) open connections and waits up to `DB_POOL_TIMEOUT` (10) seconds for a free one. Persistent connections (`DB_CONN_MAX_AGE`) apply only when the pool is off; Django does not allow both.
- **Sizing.** Workers × `DB_POOL_MAX` + cron jobs + the restore test must stay under the server's `max_connections` (100 by default). Four workers at 4 is 16. `db_health` shows how close it is.
- `DB_CONNECT_TIMEOUT` (10 s) stops a worker hanging on an unreachable server.
- Each process names itself (`DB_APP_NAME`): web workers are `landlordpms`, a cron job is `landlordpms-<command>` (set by `deploy/run.sh`), the backup is `landlordpms-backup` and the restore test `landlordpms-restore`. `db_health` and `pg_stat_activity` show who holds a connection.
- TLS: `DB_SSLMODE` (default `prefer`). Set `require`, or `verify-full` with the provider's certificate, when the database is on another machine. `launch_check` fails a remote database reached without TLS. The backup and restore tools use the same mode.

## 2. Configuration

Production ends a statement after 30 seconds (`DB_STATEMENT_TIMEOUT_MS`) and a transaction left open and idle after 60 seconds (`DB_IDLE_IN_TRANSACTION_MS`), so one stuck request cannot hold locks on the ledger. Development has neither. Both are sent with the connection, so they cover the web, cron and shell alike; `pg_dump` and the restore test do not use them.

Run `migrate` with `DB_STATEMENT_TIMEOUT_MS=0` when a migration builds an index on a large table (doc 18 §4).

`db_health` prints the settings in force: server version, both timeouts, TLS, superuser or not, and the pool.

## 3. Structure

About 30 apps, each owning its tables (doc 03). The generated diagram is `erd.md` (`erd --check` keeps it current, D-103). Shared bases in `core/models.py`: `TimeStampedModel`, `PublicIdModel`, `ArchivableModel` and `OrgScopedModel`.

## 4. Migrations

- Django migrations, about 100 files, applied in order on deploy (doc 18 §4), after a backup.
- `launch_check`, `db_health` and the hourly alerts all report unapplied migrations.
- `erd --check` fails when a model changes without its diagram.
- Data-rewriting migrations are first run on a restored copy (doc 18 §4).
- Production should run `migrate` as the owner role (§19); the app role cannot change tables.

## 5. Primary keys

`BigAutoField` everywhere (`DEFAULT_AUTO_FIELD`). Anything that appears in a URL, webhook or API carries a random UUID `public_id` as well, so ids cannot be guessed or counted.

## 6. Foreign keys

About 290 foreign keys, all enforced by PostgreSQL. Most are `PROTECT`: an organization, property, lease, tenant or invoice that money refers to cannot be deleted, only archived. `CASCADE` is kept for rows that mean nothing alone (a lease's tenants, a message's parts), and `SET_NULL` for three optional links.

## 7. Relationships

See `erd.md`. Rent and deposits have separate ledgers, a payment is split across invoices by `PaymentAllocation`, and an M-Pesa transaction links to at most one payment (D-103 item 5).

## 8. Constraints

About 60 `CheckConstraint`s and 60 `UniqueConstraint`s in the models, for example: amounts above zero, end dates after start dates, no two leases on a unit overlapping in time, one ledger posting per invoice or payment, and one M-Pesa receipt (`trans_id`). PostgreSQL checks them, so they hold for scripts and the admin as well as the forms.

## 9. Data validation

Forms validate input first (phone numbers, M-Pesa codes, amounts as `Decimal`, dates), services check business rules, and §8 is the last line. Money is never stored as a float (`core/money.py`).

## 10. Multi-tenant isolation

Every organization's table has an `organization` foreign key (`OrgScopedModel`). Views fetch through `for_org()` and `OrgScopedQuerysetMixin`; a property-limited staff member is further narrowed to their properties. `accounts/tests/test_isolation.py` signs in as one organization and requests another's records on every page, expecting 404. One shared database with a column, not a schema per landlord (D-004); PostgreSQL row-level security is not used, because every query already filters and RLS would need the organization set on each pooled connection.

## 11. Indexing

About 60 composite indexes, led by `organization` for the lists and by the due-date, status and phone columns the jobs search. Foreign keys are indexed by Django.

**Search.** Searches use `icontains`, which cannot use a normal index. They stay fast because the `organization` index first narrows them to one landlord's rows (hundreds or thousands, not millions). Trigram (`pg_trgm` GIN) indexes are deferred until `db_health` shows a table with mostly full scans; adding one is a single migration.

`db_health` lists tables of 10,000+ rows read mostly by full scans, and indexes over 1 MB never used.

## 12. Query optimization

- `select_related`/`prefetch_related` on every list; counts and sums done in SQL.
- `core/tests/test_query_counts.py` (D-097) builds an organization with 3 and then 8 leases and fails if any list page asks the database more times with more rows. It covers 24 pages.
- Findings from `scale_test` are in §30.

## 13. Transactions

About 240 `transaction.atomic` blocks: every service that posts money, allocates a payment, activates or ends a lease runs in one. About 80 `select_for_update` calls lock the lease, invoice or wallet row first, so two payments arriving together cannot both take the same balance. `ATOMIC_REQUESTS` is off; services choose their own boundaries. The idle-in-transaction timeout (§2) ends a transaction a bug leaves open.

## 14. Payment data integrity

- `LedgerEntry`, `DepositEntry` and `FollowUp` are append-only: a mistake is corrected by a reversing entry, never an edit (D-005, D-020). `AppendOnlyModel` refuses `save()` on an existing row and `delete()`.
- **New:** a PostgreSQL trigger (`billing 0008`) refuses `UPDATE` and `DELETE` on those tables from anything: a bulk `.update()`, the shell, raw SQL. `DepositEntry` may still have its `counterpart` set, the one column it declares mutable. The error reaches Django as an `IntegrityError`. A migration owner can drop the trigger, which is visible and audited by the migration itself.
- Each invoice and payment posts once (unique constraints), and balances are sums of the ledger.

## 15. M-Pesa integration

- Receipts are unique (`MpesaTransaction.trans_id`), so a repeated callback cannot pay twice.
- Callbacks are stored before they are matched, and unmatched ones wait in the inbox.
- Hand-typed codes Safaricom never confirmed are flagged (D-066).
- STK and C2B for the same receipt are joined, with `c2b_confirmed_at` recording when C2B came (D-104).
- What Safaricom actually sends is checked by `daraja_sandbox_check` once sandbox keys exist (D-104); go-live is doc 17.

## 16. Backup

`backup` runs nightly at 02:10 (`pg_dump -Fc` plus a media archive), keeps a set number, and is alerted on after 26 hours without success (doc 18 §5). Backups are git-ignored (D-103). An off-site copy is a server task (doc 18 §5) that `launch_check` reminds about.

## 17. Restore testing

`restore_test` runs on the 1st of each month. It restores the latest backup into a scratch database, checks every table and the migrations, compares row counts with live, and reads the media archive (D-098). It is alerted on after 35 days.

## 18. Security

- The app's user is not a superuser and does not own the tables (§19). `launch_check` fails if it is a superuser.
- TLS to a remote database (§1).
- Secrets such as Daraja keys are encrypted in their columns (`FIELD_ENCRYPTION_KEYS`, D-045).
- Every query is parameterized by the ORM; the few raw queries (`core/dbhealth.py`, the triggers) take no user input.
- `db_health` never shows query text from `pg_stat_activity`, because it holds bound values such as phone numbers. `pg_stat_statements` stores queries with values replaced by `$1`.

## 19. User permissions

**Database roles** (`deploy/db_roles.sql`):

| Role | May | Used by |
|---|---|---|
| `landlordpms_owner` | Own the schema, create and alter tables | `migrate` only |
| `landlordpms` | Read and write rows, create a database (for the restore test), read monitoring views | Web workers, cron |

The file also moves an existing install, where the app user owns everything, to these roles.

**App roles.** Staff roles and capabilities are in docs 10 and 13; they decide what a person may see, not what the database allows.

## 20. Audit logs

`AuditEvent` records who did what to which record, with changes, IP and time. **New:** the same append-only trigger as the ledger (`audit 0002`) refuses edits and deletes, so the log cannot be quietly rewritten from a shell.

## 21. Error handling

- Database errors roll back their transaction.
- Unhandled errors go to Sentry without personal data, or to `ADMINS` by email.
- `/healthz` returns 503 when the database does not answer.
- A statement that hits the timeout raises a `QueryCanceled` error, logged like any other.
- `db_health` and the alerts treat a monitoring query that fails (say, a view the role cannot read) as nothing to report rather than an error.

## 22. Performance monitoring

`db_health` prints:

- the cache hit ratio (below 99% suggests more memory);
- commits, rollbacks and deadlocks;
- temporary-file bytes (large sorts that spilled to disk);
- with `pg_stat_statements`, the slowest queries by total time.

**Monitoring setup.** `pg_stat_statements` needs `shared_preload_libraries = 'pg_stat_statements'` in `postgresql.conf` and a restart (most managed PostgreSQL services have it on), then `CREATE EXTENSION pg_stat_statements` (in `deploy/db_roles.sql`). `launch_check` reports whether it is installed. For a slow-query log as well, set `log_min_duration_statement = 1000` on the server.

## 23. Storage monitoring

`db_health` shows the database size, the largest tables with their index size, free disk for the backup folder and media, and tables with many dead rows waiting for vacuum.

Alerts:

- under `DB_ALERT_DISK_FREE_PERCENT` (10%) or `DB_ALERT_DISK_FREE_GB` (2 GB) free disk;
- the database over `DB_ALERT_SIZE_MB` (off by default; set it below the provider's limit).

## 24. Connection monitoring

`db_health` shows connections in use against `max_connections`, by application name and state, and queries or transactions running longer than `DB_ALERT_LONG_QUERY_SECONDS` (300). Both raise alerts, connections at `DB_ALERT_CONNECTIONS_PERCENT` (80%).

## 25. Duplicate data prevention

Unique constraints on:

- an M-Pesa receipt;
- a user's phone number, and a tenant's ID number within an organization;
- a unit code per property;
- an invoice per lease and period;
- one posting per source document;
- numbering sequences.

Imports preview and match existing rows before writing. Idempotency keys on API writes (D-087) stop a retried request creating twice. Tenant phone numbers are not unique on purpose (family members share phones).

## 26. Data consistency

Balances are always computed from the ledger, never stored, so they cannot drift. Totals that are stored (invoice amounts, wallet balances) change only inside a locked transaction. The append-only triggers (§14) keep history from being rewritten.

## 27. Database testing

The suite runs against PostgreSQL (not SQLite), so constraints, triggers and locking are the real thing. Key tests:

- `accounts/tests/test_isolation.py`: organizations cannot see each other.
- `core/tests/test_query_counts.py`: list pages do not grow their query count.
- `core/tests/test_database.py` (new): the triggers, `db_health` thresholds, `launch_check` lines, connection options, the session purge and clearing old message text.
- Ledger, payment and M-Pesa tests in each app, including duplicate callbacks.
- `core/tests/test_erd.py`: the diagram matches the models.

## 28. Production monitoring

`check_jobs` (every 15 minutes) emails `OPS_ALERT_EMAILS` on late jobs, a failed database or cache check, and now every `db_health` problem: connections, long queries, low disk, size and unapplied migrations. `/healthz` serves an external uptime monitor. Sentry catches errors.

## 29. Disaster recovery

- Nightly backups (§16) with a monthly restore test (§17). The restore steps are in doc 18 §5.
- Lost data is at most one day: the time since the last backup.
- Recovery takes as long as a restore, which the monthly test times.
- Moving to point-in-time recovery (WAL archiving, or a managed database's built-in recovery) would cut the loss to minutes. It is the next step if a day is too much.

## 30. Scalability testing

`scale_test --leases 200` builds an organization with 200 units, each with:

- a tenant and an active lease;
- an invoice and a part payment;
- a repair, an expense and a prospect.

It then times every list page with its query count and database time, and rolls everything back. It needs `--confirm` when `DEBUG` is off; run it on staging or a restored copy (`restore_test --keep`), not live.

**Findings at 200 leases** (development machine, DEBUG on, so slower than production):

- **Home page, about 1.1 s, 57 queries.** The vacant-unit count measured 91 ms, but on its own it runs in 8 ms with the right plan (one anti-join on the `unit, status` index). The scale test's rows are never committed, and PostgreSQL reads rows of an open transaction slowly, so the test overstates first reads. No change.
- **Call list, about 1 s.** Most of it was drawing 200 rows on one page. "To call" is now paged 25 at a time like the other lists; the count beside it still covers everyone. "Promised" and "Done today" stay whole.
- **Arrears** (`billing/selectors.py` `_arrears_from`) read every ledger entry and its invoice as full objects. It still reads every entry of a lease that owes money, because credits pay the oldest charges across the whole history, and changing that would change the money logic. It now loads only the six ledger columns and two invoice dates the calculation uses. With 200 leases and 14 months of invoices this went from 585 ms to 251 ms, with the same totals, aging and lateness. If it becomes slow again, the next step is a dated opening balance per lease (a decision about money, not just speed).
- Every other page took under 0.5 s.

**Limits of the test.** It runs inside one uncommitted transaction (so it can be undone), on one machine, with one user. It finds pages whose query count or Python work grows with data. It is not a load test of many users at once; the load test below is.

### Load test

`loadtest/locustfile.py` (Locust, a development requirement) has many people use the site at once:

- **Staff (3 in 9):** the home page, the lists and lease accounts, 2–8 s apart.
- **Tenants (5 in 9):** their portal page and lease, 5–20 s apart.
- **Safaricom (1 in 9):** a C2B confirmation every 1–5 s. Each one is a new receipt to a real unit's reference, so it is matched, allocated, receipted and posted to the ledger.

`python manage.py loadtest_setup --leases 200 --staff 10 --tenants 50` builds the organization and writes `loadtest/setup.json`. That file holds live sessions and is git-ignored; delete it afterwards. The command refuses real SMS and WhatsApp senders, since the tenants' numbers are made up. Its rows stay and the ledger cannot be deleted from, so run it on a database you will drop:

1. Create a scratch database (`createdb landlordpms_loadtest`), and with `DB_NAME=landlordpms_loadtest` run `migrate` and `loadtest_setup`.
2. Start the site against it with `DB_POOL=1`.
3. Run `locust -f loadtest/locustfile.py --host http://127.0.0.1:8005 --users 60 --spawn-rate 5 --run-time 5m --headless`.
4. Drop the database.

**First run (2026-10-03).** Development laptop, Windows, `runserver` (one process), DEBUG on, Locust on the same machine. 200 leases with 14 months of history.

- **Two payments at once deadlocked.** 25 of 72 confirmations failed with "deadlock detected" on the organization row. The lease lock (`select_for_update()` with `select_related(..., "organization")`) locked the joined organization and property rows as well. Every insert that points at the organization holds a share lock on it, so two payments in one organization could each wait for the other. The failed transactions stayed RECEIVED for the retry job, so no money was lost, but the payer's receipt was late. Every such lock now names only its own row (`of=("self",)`): payments, invoicing, the daily billing job, repairs, inspections, M-Pesa and bank matching, the portal invitation and subscriptions. `test_row_locks_with_joins_lock_only_their_own_row` keeps it that way. After the fix: 280 confirmations, none failed, all matched and posted.
- **The home page worked out the arrears twice:** once for the call-list task, once for the dashboard. It now does it once: 1.3 s down to 0.65 s in-process. The arrears remain its largest part (see above).
- **Pool waits.** With `DB_POOL_MAX` 8, `runserver` started more threads than there were connections, and requests failed after the 10 s pool timeout. This is an artefact of `runserver`: gunicorn runs a fixed number of threads per worker, so set `DB_POOL_MAX` to at least the threads per worker.
- **Capacity.** One process served about 5 requests a second. At 60 users (about 9 a second wanted) it was saturated: no failures, but a median of 2.8 s. At 20 users there were no failures, a median of 0.23 s and a 95th percentile of 1 s. The home page took 0.86 s, the arrears and call list 0.6 s, the portal 0.12 s and an M-Pesa confirmation 0.32 s. Production runs several gunicorn workers (doc 18) with DEBUG off.

**Before launch:** run it again on the production-sized server with gunicorn and settings `prod`. Raise the users until the 95th percentile passes 2 s; that is the ceiling to watch for.

## Caching

- Production keeps sessions in Redis and writes them through to the database (`cached_db`), so a page view no longer reads the session table, and a Redis restart logs nobody out. `purge_sessions` deletes expired ones weekly.
- Rate limits and OTP cooldowns already use Redis.
- **Not cached, on purpose:**
  - permissions, since a removed staff member must lose access at once;
  - balances and reports, since figures must match the ledger.

  The per-page cost of permissions is 8 small indexed queries.

## Retention

- **Purged weekly:** expired sessions, OTP codes, import previews and assistant questions over 90 days.
- **Pruned as they go:**
  - webhook deliveries;
  - API idempotency records;
  - job runs over 90 days (by `check_jobs`).
- **The message log** (`notifications.Message`, one row per SMS, WhatsApp, email or in-app notice): `purge_messages` clears the text, subject, address and WhatsApp parameters of messages older than `MESSAGE_TEXT_DAYS` (730 days) **[VERIFY]**.

  The row itself stays, for three reasons:
  - its dedupe key stops the same reminder being sent twice;
  - its type, status, cost and links to the lease, invoice or payment remain for the log and the SMS bill;
  - deleting it would leave gaps in a tenant's history.

  It works in batches of 2,000 so no statement nears the 30-second limit. The two-year period should be confirmed against the Data Protection Act (doc 06) with counsel.
- **Never purged:** the ledger and the audit log, which are kept for the tax record-keeping period.
