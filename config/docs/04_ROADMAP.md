# 04 — Roadmap

Legend: `[ ]` not started, `[~]` in progress, `[x]` done.

## Phase 0 — Groundwork (see 01_PRE_BUILD_CHECKLIST)
- [ ] Decisions locked, PostgreSQL working, repo housekeeping, landlord interviews, legal started

## Phase 1 — Foundation
- [x] Split settings, `.env.example`, CI, tests, linting
- [x] Custom User (phone/email login)
- [x] Organization, Membership, roles
- [x] Org-scoping mixin/manager + isolation tests
- [x] Audit log basics
- [x] Auth UI: register, login, logout, password reset, invite staff
- [x] Base template, mobile-first layout, empty dashboard
**Exit:** two organizations exist and provably cannot see each other's data.

## Phase 2 — Property, Tenants, Leases
- [x] Property, Unit (Building optional), CRUD + list/search
- [x] Tenant CRUD
- [x] Lease create/end/renew, unit occupancy status
- [x] CSV import for units and tenants (essential for landlords with many units)
**Exit:** a landlord can set up 20 units and tenants in under 15 minutes.

## Phase 3 — Billing
- [x] Charge types, invoices, ledger
- [x] Monthly invoice generation job (idempotent)
- [x] Arrears view, tenant statement
- [x] Tests for rounding, proration, duplicates
**Exit:** invoices generate automatically and balances are always derivable from the ledger.

## Phase 4 — Payments (manual) & receipts
- [x] Record payment, allocate to invoices, partial/over-payment
- [x] Reversal flow
- [x] PDF/shareable receipts (PDF done; sending to tenants comes with Phase 5 SMS)
**Exit:** first pilot landlord runs a real rent cycle.

## Phase 5 — Notifications
- [ ] SMS (Africa's Talking) reminders and receipts
- [ ] WhatsApp templates
- [ ] Message log, delivery status, opt-out
- [ ] Scheduled reminders

## Phase 6 — M-Pesa
- [ ] Daraja sandbox: C2B register URLs, confirmation, STK Push
- [ ] Idempotent processing, unmatched-payment inbox
- [ ] Per-organization shortcode config, encrypted secrets
- [ ] Production go-live with Safaricom
**Exit:** 90%+ of pilot payments reconcile automatically.

## Phase 7 — Dashboard & reports
- [ ] Expected vs collected, vacancy, arrears, lease expiries
- [ ] Owner statements for agencies, exports (CSV/PDF)

## Phase 8 — Launch
- [ ] Production deployment, HTTPS, backups, monitoring
- [ ] Subscription plans and unit limits
- [ ] Terms/Privacy live, ODPC registration done
- [ ] Onboarding flow, help articles, support channel

## Phase 9 — Expansion
Maintenance, expenses, utilities (water/electricity), accounting/MRI reports, document vault, tenant portal, API + mobile app, Swahili, USSD, analytics, AI assistant.

## Working cadence
- One vertical slice at a time: model → service → view → template → tests → docs → commit.
- Demo to a pilot user at the end of every phase from Phase 2.
