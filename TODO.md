# landlordpms — TODO

Rule: **no models or views until Phase 0 is finished and agreed.** Docs live in `config/docs/`. Review docs 10–13.
Legend: `[ ]` todo · `[~]` in progress · `[x]` done

## Phase 0 — Plan and environment
### Environment
- [x] Switch to PostgreSQL: database created, `.env` set
- [x] `manage.py check` and `migrate` run against Postgres
- [ ] Remove old `db.sqlite3` (waiting for user's go-ahead)
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
- [ ] Sketch the 10 key mobile screens (doc 14 C2)
- [x] Financial separation, archive matrix, numbering, money, reporting metrics, notification preferences (doc 11 §22–27)
- [~] ER diagram drafted in `config/docs/erd.md`; awaiting review
- [ ] Decide open items below

### Open decisions (record in 08_DECISIONS_LOG.md)
- [x] Caretaker cash payments: granted by Owner/Manager via `payments.record` (role or per person); `payments.confirm` decides review (D-025)
- [x] Agency/client-owner mode in v1? *Recommended no, design-in only*
- [x] Unit `payment_reference` format (e.g. `GV-A102` vs `LPM-GV-A102`)
- [ ] Recommend Paybill (reference-based matching) over Till for auto-reconciliation? Confirm with Daraja docs
- [x] Invoice/receipt/lease number formats (doc 11 §24)
- [x] Default payment allocation order (oldest first) and manager override
- [x] Grace days default and whether late fees are in MVP (recommended off)
- [x] Billing period rule for mid-month move-in (prorate by days?)
- [~] Data retention period for former tenants (90 days for organizations; tenant data period needs counsel)
- [x] Phone-only login allowed without email?

### Business preparation
- [ ] Interview 5–10 landlords/caretakers (log in doc 02)
- [ ] Choose pilot area and first 5 pilot landlords
- [ ] Register company and domain
- [ ] ODPC (data protection) registration
- [ ] Daraja developer account, sandbox
- [ ] Africa's Talking (SMS) sandbox account
- [ ] Terms of Service and Privacy Policy drafts

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
- [~] Platform Admin: Django admin with org, user overview (done); subscription overview (waits for subscription model)

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
- [ ] Archive instead of delete (properties, buildings, units, tenants, charge types done; drafts may be deleted; ended leases archive once settled by the daily job)

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
- [ ] Bank payments: `method = BANK`, CSV statement import through the matching engine
- [ ] P&L, cash flow and aged receivables by property
- [x] PaymentAccount, PropertyPaymentAccount (+ admin UI; in-app page still to do)
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
- [ ] Store msisdn_raw and msisdn_hash (done); verify hash behaviour in Daraja sandbox
- [x] Unmatched-payment SMS asking for unit reference (payer, once, when the real number is known); in-app alert to `mpesa.match` holders
- [x] MpesaTransaction, callback endpoints with per-account token
- [x] Idempotent processing, raw payload storage
- [x] Matching engine (account → reference → phone → amount); reference auto-confirms via `record_system_payment`
- [x] Unallocated inbox (`/mpesa/inbox/`), transaction list, manual match / accept suggestion / ignore / restore; reversal returns it to the inbox
- [x] STK push: request from the lease (`/mpesa/request/<lease>/`), callback confirms on that lease, status check
- [ ] Verify in the Daraja sandbox: STK query "still processing" error code, whether a C2B confirmation also arrives for STK payments, STK amount limit
- [x] Encrypted Daraja credentials (`FIELD_ENCRYPTION_KEYS`), M-Pesa settings page, C2B URL registration
- [x] Daily job `mpesa_daily`: retry stuck transactions, check waiting payment requests, in-app reconciliation summary
- [x] One M-Pesa code counted once: a hand-typed code blocks automatic payment of the callback, and a code waiting in the inbox cannot be typed in (D-045 item 12)
- [ ] Safaricom go-live (checklist: config/docs/17_MPESA_GO_LIVE.md)

## Phase 7 — Dashboards, search, tenant portal
- [x] Collectability grade (A to E) and daily who-to-call list with calls and promises to pay (D-052)
- [x] Property owners, management fee and the monthly owner statement with PDF (D-053); owner login = Viewer role limited to their properties
- [x] Owner remittance records and sending the statement to the owner (D-058): payments to owners voided not deleted, still to pay on the statement and PDF, 12-month owner account, email with the PDF and an optional SMS summary, each send numbered and kept
- [x] Approve analytics decisions (doc 15 §8): chart library, snapshots, historic-trend rule, first report set (D-051)
- [~] Chart.js dashboard with drill-down and CSV export done (`/reports/`); DailySnapshot deferred by D-051 until the performance test or an arrears trend needs it
- [x] `reports/metrics.py` with the metric definitions from doc 11 §26 (occupancy, collection rate, arrears aging); NOI waits for expenses
- [x] Portfolio and per-property dashboards (property and month filters in the URL)
- [x] Per-role dashboards: the home page shows each member the work waiting and the figures their capabilities allow (D-056)
- [x] Global search with suggestions as you type, scoped like the list pages (D-054); trigram indexes wait for the performance test
- [x] Arrears aged from due date plus grace, as doc 11 §26 defines (D-054 item 1)
- [x] TenantAccount, invite by SMS, tenant dashboard (own data only) + leakage tests (D-055)
- [x] Tenant payment link: STK push started by the tenant from the rent SMS, any amount (D-046 item 1; built early on the Phase 6 branch, mpesa 0004)
- [ ] Payment link: try it end to end in the Daraja sandbox; check how long a rent SMS gets with the link and what it costs
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
- [ ] Public status page, security and data-protection page
- [ ] Import concierge process for pilots; track time to first invoice
- [ ] `SubscriptionInvoice.vat_amount`; confirm VAT/eTIMS for our company
- [ ] `EtimsAdapter` interface stub
- [ ] Platform billing (flow A): Plan, Subscription, SubscriptionInvoice, PlatformReceipt, SMS wallet top-up; separate from tenant billing
- [ ] Backups, monitoring, error tracking, HTTPS, prod settings
- [ ] Subscriptions app and entitlements service
- [ ] Onboarding polish, help docs, support channel

## Phase 9 — Later
- [ ] Prospect and Viewing (light CRM); lease PDF and move-out statement
- [x] Move-in/move-out condition reports with photos and item register (D-047)
- [x] Tenant good-standing letter: tenancy and payment record PDF, checkable by link (D-046, D-048)
- [ ] Unverified M-Pesa code check (typed code not confirmed by Daraja or statement within 24h), after statement import
- [ ] Vendor SLA on maintenance (metered water done: D-057)
- [ ] Expenses (flow C): Expense, ExpenseCategory, Supplier, link to maintenance; property net income
- [ ] Maintenance, other metered utilities (electricity sub-meters), documents
- [ ] MRI estimate report (configurable dated rate) and eTIMS adapter, after KRA/accountant confirmation (D-037)
- [ ] Accounting/MRI reports, analytics, WhatsApp/email
- [ ] Agency mode and joint ownership, custom roles per organization
- [ ] API, mobile app, USSD, AI assistant (permission-aware tools)

## Always
- [ ] Update docs and `08_DECISIONS_LOG.md` with each decision
- [ ] Tests for isolation, permissions, billing, payments, M-Pesa
- [ ] No secrets in Git
