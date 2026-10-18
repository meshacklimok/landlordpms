# NyumbaPMS — TODO

Rule: **no models or views until Phase 0 is finished and agreed.** Docs live in `docs/`. Review docs 10–13.
Legend: `[ ]` todo · `[~]` in progress · `[x]` done

## Phase 0 — Plan and environment
### Environment
- [x] Switch to PostgreSQL: database created, `.env` set
- [x] `manage.py check` and `migrate` run against Postgres
- [x] Remove old `db.sqlite3` (gone from the project folder; D-103)
- [x] Pin versions in `requirements.txt`
- [x] `SECRET_KEY` with no insecure fallback; split settings (base/dev/prod)
- [x] Move `static/css/js/script.js` to `static/js/`
- [x] Add ruff, pytest-django (`pyproject.toml`), pre-commit, GitHub Actions CI
- [x] Commit docs and settings changes

### Brainstorm and design (docs 10, 11, 12)
- [x] Users, roles, permission matrix (doc 10)
- [x] Entity map and relationships (doc 11)
- [x] Answers to open questions: multiple leases, co-tenants, transfers, ownership, multi-org (doc 11 §3)
- [x] Implementation scope tiers (doc 12)
- [x] Editable roles and capability catalog (doc 13)
- [ ] **Review and approve** docs 10–13 (owner)
- [x] Remaining brainstorm topics and recommendations (doc 14)
- [x] Approve or change the 13 Group A recommendations in doc 14 (deposits, opening balances, rent timing, payer phones, numbering, concurrency, tax fields, commercial, agency, org context, login/OTP, lapse behaviour, SMS wallet)
- [x] Review Group D foundations (doc 14 D1–D15): accounting model, no custody of funds, glossary, effective dating, addresses, DR targets
- [x] Desk benchmark of competitors (doc 16)
- [x] Decided D-039 (eTIMS priority, phone-hash test, earlier portal/WhatsApp/water)
- [x] Decided D-040 (competitor gap list, doc 16 sections 7-9) and fold approved items into docs 11/12 and the phases
- [x] Third competitor pass: 21 websites and apps (doc 16 Part 3, 2026-09-29)
- [x] **Approve or change D-046**: approved as proposed on 2026-09-29
- [ ] Hands-on trials of 3 Kenyan competitors; Daraja sandbox test for hashed phone; accountant check on eTIMS/eRITS
- [x] The 10 key mobile screens checked at phone width, design system written down, tables scroll, touch targets, number keyboards (D-096, doc 19); still to try on real phones
- [x] Financial separation, archive matrix, numbering, money, reporting metrics, notification preferences (doc 11 §22–27)
- [x] ER diagram in `docs/erd.md`, generated from the models by `python manage.py erd`; a test fails when it falls behind (D-103)
- [ ] Decide open items below

### Open decisions (record in 08_DECISIONS_LOG.md)
- [x] Caretaker cash payments: granted by Owner/Manager via `payments.record` (role or per person); `payments.confirm` decides review (D-025)
- [x] Agency/client-owner mode in v1? *Recommended no, design-in only*
- [x] Unit `payment_reference` format (e.g. `GV-A102` vs `LPM-GV-A102`)
- [x] Recommend Paybill (reference-based matching) over Till for auto-reconciliation: yes, shown on the M-Pesa pages (D-100); empty `BillRefNumber` for Tills still to confirm in the Daraja sandbox
- [x] Invoice/receipt/lease number formats (doc 11 §24)
- [x] Default payment allocation order (oldest first) and manager override
- [x] Grace days default and whether late fees are in MVP (recommended off)
- [x] Late fees: landlord's rule (fixed, or % with a maximum), daily proposal, staff charge or skip each (D-129)
- [ ] Late fees: check with counsel whether the lease must state the fee and what is reasonable (D-129 item 4)
- [x] Billing period rule for mid-month move-in (prorate by days?)
- [~] Data retention period for former tenants (90 days for organizations; tenant data period needs counsel)
- [x] Phone-only login allowed without email?

### Business preparation
- [ ] Interview 5–10 landlords/caretakers (log in doc 02)
- [ ] Choose pilot area and first 5 pilot landlords; track them each month with `python manage.py pilot_report` (D-101)
- [ ] Ask each pilot agency which accounting package it and its accountant use; build a direct sync only for one that several name (D-075 item 7)
- [ ] Register company and domain
- [ ] ODPC (data protection) registration
- [ ] Daraja developer account, sandbox
- [ ] Africa's Talking (SMS) sandbox account
- [x] Terms of Service and Privacy Policy drafts at /terms/ and /privacy/, acceptance recorded per version (D-095); lawyer review still needed before setting LEGAL_REVIEWED

## Phase 1 — Foundation *(after Phase 0 approval)*
_Status 2026-09-26: done on `feature/phase1-foundation` (D-041): migrations applied, 161 tests passing. Subscription overview in admin waits for the subscription model._
- [x] Archive mixin (`archived_at/by`, `objects`/`all_objects`) and PROTECT convention
- [x] Custom User (phone/email login) and `AUTH_USER_MODEL`
- [x] Organization, Membership
- [x] Capability catalog synced from code (doc 13)
- [x] RoleTemplate + Role + RoleCapability; copy templates into each new organization
- [x] MembershipCapability overrides; no-escalation rule; last-Owner protection
- [x] Role management UI for Owners (rename, add, clone, edit capabilities); Platform Admin edits templates in Django admin
- [x] Default templates: Owner, Manager, Accountant, Caretaker, Leasing/Letting Agent, Maintenance Manager, Maintenance Staff, Viewer
- [x] Single `can(membership, capability, property)` check used by views/services
- [x] PropertyAccess (scoped staff)
- [x] Org-scoped manager + view mixin + capability check
- [x] AuditEvent + service helper
- [x] Register / login / logout / reset / staff invite
- [x] Onboarding: create organization + Owner in one transaction, setup checklist
- [x] Base template (mobile-first), role-based home page shell
- [x] Tests: cross-organization isolation, role checks
- [x] Platform Admin: Django admin with org, user overview; subscription overview (D-060)

## Phase 2 — Properties, tenants, leases
- [x] Unit types include BED_SPACE (design-in); Property.category includes ESTATE
- [x] Branch/branding fields on Organization (design-in: brand_name, logo, brand_color, document_footer; Branch model and Property.branch; admin only, no scoping yet)
- [x] Shareable public vacancy link (/v/<token>/: no login, noindex, rate-limited, revocable; shared by units.manage or leases.draft; WhatsApp share)
- [x] Property, Building (optional), Unit (types, manual status, payment_reference); structured Kenyan address
- [x] Standalone-house shortcut (auto-create unit)
- [x] Tenant (lifecycle status, normalised phone, sensitive fields); COMPANY type, duplicate-phone warning
- [x] Lease, LeaseTenant, LeaseRentChange, LeaseCharge, LeasePayer; overlap constraint (btree_gist); ChargeType brought forward
- [x] Lease actions: activate (LSE number), notice to vacate, end/terminate, renew, transfer; overlap uses the actual end (ended_on)
- [x] CSV import: units, tenants and opening balances (imports app: preview via rolled-back dry run, apply, undo within 24h, templates)
- [x] List pages: pagination, search, filters, indexes (cross-property Units list doubles as the vacancy list; property page paginates units and counts statuses in SQL)
- [x] Archive instead of delete (properties, buildings, units, tenants, charge types; drafts may be deleted; ended leases archive once settled by the daily job, with an archived list, restore and automatic return when money moves: D-079)

## Phase 3 — Billing
- [x] NumberSequence with row-locked allocation (50 parallel invoice issues test)
- [x] `core/money.py` (parse, round, prorate, format) + tests; `{% load money %}{{ v|money }}` filter
- [x] Invoice, InvoiceLine, LedgerEntry (signed, append-only) and DepositEntry; opening balances (D-042)
- [x] Idempotent monthly invoice generation (+ proration); rebill on lease end, rent change, charge change
- [x] Daily command `billing_daily`: generation, moved-out tenants, archive settled leases (schedule it)
- [x] Selectors: invoice list, lease statement with running balance, arrears with FIFO aging
- [x] Pages: invoices, bill a month, void, lease account (statement, opening balance, deposits), arrears and aging
- [x] Deposit ledger: DEPOSIT_TRANSFERRED on lease transfer (D-016), deduction, refund, clearance statement
- [x] Tests: totals, rounding, duplicates, proration, rebill, aging, deposits

## Phase 4 — Payments and receipts
- [x] Bank payments: `method = BANK`, CSV statement import through the matching engine, bank inbox, bank accounts page; M-Pesa statement import (D-064)
- [x] P&L, cash flow and aged receivables by property, with CSV (D-065; expenses added in D-067; PDF and accrual basis later)
- [x] PaymentAccount, PropertyPaymentAccount (+ admin UI; bank accounts page in D-064)
- [x] Payment, PaymentAllocation; partial, over-payment, credit
- [x] Reversal flow; caretaker cash review queue (any recorder without `payments.confirm`; give a caretaker `payments.record` to use it)
- [x] Receipt numbering and PDF
- [x] Tests: allocation, reversal, credit

## Phase 5 — Communications
- [x] WhatsApp adapter (moved earlier)
- [x] Segmented announcements
- [x] NotificationType catalog (in code), OrganizationNotificationRule, NotificationPreference, ConsentRecord (D-044)
- [x] Delivery rule engine (org rule + preference + consent + channel + quiet hours); audience by capability; `send_due_messages` (schedule every few minutes)
- [x] Owner notification settings page; tenant opt-out per channel
- [x] Message log, MessageTemplate overrides (safe `{field}` rendering), provider adapter interface
- [x] SMS adapter (Africa's Talking), delivery status, opt-out
- [x] Triggers: invoice issued (monthly run only), due soon, overdue (daily job), payment received with public receipt link `/r/<token>/`, payment waiting for review (in-app); set `SITE_URL` in production
- [x] In-app notifications

## Phase 6 — M-Pesa
- [ ] Store msisdn_raw and msisdn_hash (done); verify hash behaviour in Daraja sandbox (`daraja_sandbox_check`, D-104)
- [x] Unmatched-payment SMS asking for unit reference (payer, once, when the real number is known); in-app alert to `mpesa.match` holders
- [x] MpesaTransaction, callback endpoints with per-account token
- [x] Idempotent processing, raw payload storage
- [x] Matching engine (account → reference → phone → amount); reference auto-confirms via `record_system_payment`
- [x] Unallocated inbox (`/mpesa/inbox/`), transaction list, manual match / accept suggestion / ignore / restore; reversal returns it to the inbox
- [x] STK push: request from the lease (`/mpesa/request/<lease>/`), callback confirms on that lease, status check
- [ ] Verify in the Daraja sandbox: STK query "still processing" error code, whether a C2B confirmation also arrives for STK payments, STK amount limit: run `python manage.py daraja_sandbox_check` (D-104)
- [x] Encrypted Daraja credentials (`FIELD_ENCRYPTION_KEYS`), M-Pesa settings page, C2B URL registration
- [x] Daily job `mpesa_daily`: retry stuck transactions, check waiting payment requests, in-app reconciliation summary
- [x] One M-Pesa code counted once: a hand-typed code blocks automatic payment of the callback, and a code waiting in the inbox cannot be typed in (D-045 item 12)
- [ ] Safaricom go-live (checklist: docs/17_MPESA_GO_LIVE.md)
- [ ] When the Droplet is switched on again: add `SUPPORT_WHATSAPP=254748578190` to `/etc/landlordpms.env`, restart, run `migrate`, and paste the setup video link in admin (Mpesa › Setup video) (D-174)

## Phase 7 — Dashboards, search, tenant portal
- [x] Collectability grade (A to E) and daily who-to-call list with calls and promises to pay (D-052)
- [x] Property owners, management fee and the monthly owner statement with PDF (D-053); owners see their own statements in the owner portal (D-128)
- [x] Owner remittance records and sending the statement to the owner (D-058): payments to owners voided not deleted, still to pay on the statement and PDF, 12-month owner account, email with the PDF and an optional SMS summary, each send numbered and kept
- [x] Approve analytics decisions (doc 15 §8): chart library, snapshots, historic-trend rule, first report set (D-051)
- [~] Chart.js dashboard with drill-down and CSV export done (`/reports/`); DailySnapshot deferred by D-051 until the performance test or an arrears trend needs it
- [x] `reports/metrics.py` with the metric definitions from doc 11 §26 (occupancy, collection rate, arrears aging); NOI is income less approved expenses (D-067)
- [x] Portfolio and per-property dashboards (property and month filters in the URL)
- [x] Per-role dashboards: the home page shows each member the work waiting and the figures their capabilities allow (D-056)
- [x] Global search with suggestions as you type, scoped like the list pages (D-054); trigram indexes wait for `db_health` to show full scans (D-105)
- [x] Arrears aged from due date plus grace, as doc 11 §26 defines (D-054 item 1)
- [x] TenantAccount, invite by SMS, tenant dashboard (own data only) + leakage tests (D-055)
- [x] Tenant payment link: STK push started by the tenant from the rent SMS, any amount (D-046 item 1; built early on the Phase 6 branch, mpesa 0004)
- [x] Paystack for landlords without a Paybill: set up by a Platform Admin, used only when no Paybill collects, signed webhook, verify on "Check" (D-117, mpesa 0010)
- [ ] Paystack: try it with a Paystack Kenya test account, then one live payment (doc 17 §6)
- [ ] Subscription payments to us (D-126): set the PLATFORM_* keys, run `platform_payments --register`, paste the Paystack webhook URL, pay one test invoice each way (doc 17 §6)
- [ ] Payment link: try it end to end in the Daraja sandbox (SMS length answered in D-104 item 4: 2 parts each, the due-soon message goes from 1 to 2)
- [x] Annual rental income pack; owner resident/non-resident flag for the tax estimate (D-046, D-050)
- [x] Quarterly and yearly billing (`Lease.Frequency`) (D-046, D-049)

## Metered water (D-057, between Phase 7 and 8)
- [x] Meters per property (own or shared, equal or weighted split, rate per m³, minimum charge; prepaid meters are never billed)
- [x] Readings with an optional photo, the monthly round page, flags (lower, zero while let, much higher, long gap)
- [x] Approval (flagged readings need a note), reject with reason, undo while not invoiced
- [x] Charges prorated by lease days, billed with next month's rent or on their own invoice if that month is out
- [ ] Try a round on a phone in the field; check the flag thresholds against a real month

## Phase 8 — Launch
- [x] Optional MFA for Owner/Accountant, mandatory for Platform Admin (D-059: authenticator app, recovery codes, admin gate)
- [x] Public status page, security and data-protection page, `/healthz` (D-061)
- [x] Import concierge process for pilots (doc 18); time to first invoice in admin (D-063)
- [x] `SubscriptionInvoice.vat_amount` (D-060); still confirm VAT registration and rate for our company [VERIFY]
- [x] `EtimsAdapter` interface stub (D-060: `subscriptions/etims.py`, null adapter by default)
- [x] Platform billing (flow A): Plan, Subscription, SubscriptionInvoice, PlatformReceipt, SMS wallet top-up; separate from tenant billing (D-060)
- [x] Job records and alerts, backups with pruning, optional Sentry, logging, HTTPS and prod settings review (D-062, doc 18)
- [ ] Before launch: install pg_dump on the server, set up cron (doc 18), an uptime monitor on `/healthz`, and do the first restore test; the cron file, a monthly `restore_test` and `launch_check` are ready (D-098), so on the day run `crontab deploy/crontab` and `launch_check` until it passes
- [x] Subscriptions app and entitlements service (D-060)
- [x] Setup checklist with links, help pages, support requests with attachments (D-063)

## Phase 9 — Later
- [x] Prospects and viewings, light CRM: holds, vacancy-link enquiries, convert to tenant (D-072)
- [x] Lease agreement PDF and move-out statement (D-073)
- [x] Move-in/move-out condition reports with photos and item register (D-047)
- [x] Tenant good-standing letter: tenancy and payment record PDF, checkable by link (D-046, D-048)
- [x] Unverified M-Pesa code check: a typed code no callback or statement brought in within 24h, or with another amount, is flagged; "Codes to check" page, payment warning, home task, daily alert, mark checked with a note (D-066)
- [x] Vendor SLA on maintenance: start-by and fix-by targets per priority set by the organization, performance by supplier and staff, late alerts, times in the supplier SMS (D-074; metered water done: D-057)
- [x] Expenses (flow C): expenses with maker/checker approval, receipts, categories and suppliers; net operating income in the P&L, money out in the cash flow, taken off the owner statement (D-067; repair costs linked to maintenance requests in D-068)
- [x] Maintenance requests and jobs: priorities with due times, assigning staff and suppliers (SMS), statuses, notes and photos, costs as expenses, tenant reports and updates in the portal, home tasks and the unit page (D-068)
- [x] Add a recurring charge (e.g. garbage) to every lease at a property in one go, with a preview (D-069)
- [x] Electricity sub-meters on the same meters, billed in kWh as "Metered electricity" (D-070)
- [x] Documents: private files on properties, units, leases and tenants, sharing with the tenant portal, expiry reminders (D-071)
- [ ] eTIMS adapter, after KRA/accountant confirmation (D-037); the MRI estimate with dated rates is done in the income pack (D-050)
- [x] Accounting export for the accountant: double-entry journal (QuickBooks import layout), accounts with totals, invoice, payment and expense registers, as one ZIP (D-075)
- [x] Email to tenants: a copy of invoices (with the PDF), reminders, receipts, announcements and repair updates, stop link, reply-to address; invoice PDF for staff and the portal (D-076)
- [x] MRI reports (D-050) and analytics (D-051); arrears over time from daily snapshots, with a backfill (D-077)
- [x] Agency mode (owners, fees, statements and payments to owners: D-053, D-058), joint ownership with shares (D-078), custom roles per organization
- [x] Installable app (PWA): manifest, icons, service worker caching static files only, offline page, install button (D-080)
- [x] USSD menu for tenants on feature phones: balance, how to pay, last payment (D-081)
- [x] Pay rent from the USSD menu with an M-Pesa prompt (D-084)
- [x] Report a repair from the USSD menu (D-085)
- [x] Optional PIN for the USSD menu (D-086)
- [x] Read-only API v1 with organization API keys (D-082)
- [x] Webhooks for payments, invoices, leases and tenants (D-083)
- [x] API writes: record payments (with an Idempotency-Key), add and update tenants, read one item (D-087)
- [x] Offline data entry: meter rounds typed without a connection are kept on the phone and sent later (D-088)
- [x] Alerts on staff phones (Web Push): opt in per device, no details on the lock screen, quiet hours (D-089)
- [x] Repair reports typed offline are kept and sent later (D-090); payments are deliberately never queued offline
- [x] Ask the assistant: read-only, permission-aware tools, owner switch, Claude via the Anthropic API (D-091)
- [x] Assistant actions with a confirm step: record a payment, message a tenant, assign a repair (D-092)
- [x] Tenants ask on WhatsApp: own balance, how to pay, receipts and repairs, read-only (D-093)
- [x] Tenant assistant actions (report a repair, M-Pesa prompt) and a portal ask page (D-094)
- [x] Pre-launch checks over every route: visitors, another organization, staff kept to some properties, junk ids, query counts, error reports without personal data, the production deploy check (D-097); a penetration test by someone else is still to do
- [x] Ready for the server: `deploy/crontab` and `deploy/run.sh`, a monthly automatic restore test into a scratch database, and `launch_check` listing what the server still lacks (D-098)
- [x] Database review (D-105, doc 20): connection pool and timeouts, append-only enforced by PostgreSQL triggers, separate owner and app roles, `db_health` with alerts, `scale_test`, cached sessions
- [x] Notices to vacate from either side with history, the tenant's notice in the portal, move-out reminder, staff alert, "moving out" filters and home task (D-106); Swahili and WhatsApp templates [VERIFY]
- [x] Notice to vacate and demand letters: numbered, frozen, checkable PDFs with warnings before issuing and a record of how each was served, with the signed copy (D-109); wording [VERIFY with counsel]
- [x] Advocate handover pack for a tenant in arrears or a disputed move-out: one ZIP with a brief, statement, payments, follow-ups, letters as served, move-out papers and photos, documents, record.json and an s.106B certificate; download audited with its SHA-256 (D-122); checks [VERIFY with counsel]
- [x] E-signature of the agreement (renewals included) and the move-out statement: SMS link and code for each tenant, the landlord signs in the app, frozen PDF with SHA-256, signing record page, filed copy, public check page (D-118, esign 0001)
- [x] E-signature legal limits: only short leases (two years or less, no option to renew), evidence pack with an Evidence Act s.106B certificate, keep copies six years after the lease ends (D-118 item 10)
- [ ] E-signature: an advocate to confirm D-118 item 10 (s.58 over s.3(3) for short tenancies), the consent wording and the certificate wording; a licensed provider only if they require one
- [x] Arrears load only the columns they use; call list paged; home vacant count checked (doc 20 §30)
- [x] Message text cleared after `MESSAGE_TEXT_DAYS` by `purge_messages` (doc 20, Retention)
- [x] Load test with Locust (doc 20 §30): fixed concurrent M-Pesa deadlocks (row locks only on their own row); home page arrears computed once
- [x] Agencies: branches give access to every property in them, fixed monthly fees, management fee income report by branch, owner and property (D-127)
- [x] Owner portal: owners invited by SMS and email see their statements, payments to them, statements sent and their properties; tenant names hidden (D-128)
- [x] Tenant portal: messages with the office, lease terms and agreement PDF, renewal offers the tenant accepts or declines and staff turn into a draft (D-131)
- [x] Growth basics: public front page, pricing, FAQ, 7 tutorials, demo booking form with admin list and email, refer-a-landlord with rewards on the first payment, campaign and referral sign-up sources, robots.txt and sitemap (D-133)
- [ ] Growth next: testimonials once real, changelog, partner/agent commissions, sign-up sources in `pilot_report`, Swahili for the public pages
- [ ] Repeat the load test on the production server with gunicorn before launch; record the ceiling (doc 20 §30)
- [ ] Confirm the two-year message-text period with counsel (doc 06)
- [ ] On the server: `shared_preload_libraries = 'pg_stat_statements'`, roles from `deploy/db_roles.sql`, `DB_SSLMODE=require` if the database is remote
- [ ] Native mobile app: plan approved (D-099): none before the pilot; installed-app opens are counted in `pilot_report` (D-102); then a Play Store wrapper if asked for, a staff app only on evidence

## Always
- [ ] Update docs and `08_DECISIONS_LOG.md` with each decision
- [ ] Tests for isolation, permissions, billing, payments, M-Pesa
- [ ] No secrets in Git (history checked 2026-10-01, none found; backups ignored, D-103)
