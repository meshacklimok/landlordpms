# 14 — Remaining Brainstorm Topics and Recommendations

Topics not yet covered in docs 10–13. Each has a **recommendation**. Group A changes the database, so decide it **before models**. Groups B and C can be decided during the phase that builds them.

Items marked **[VERIFY]** involve Kenyan law/tax/Safaricom rules to confirm with an expert.

---

## Group A — Decide before models (affect the schema)

**Status: all 13 recommendations ACCEPTED (D-033 to D-035). Tax is design-in only, with fields present but no tax logic. Refinements from review are marked *Accepted refinement*.**

### A1. Deposits
Kenyan tenants usually pay a deposit (often 1–2 months' rent, sometimes plus a separate water/electricity deposit).
- Deposits are **not income**. They are a liability held for the tenant.
- **Recommend:** track deposits as ledger entries of their own kind: `DEPOSIT_RECEIVED`, `DEPOSIT_DEDUCTION` (with reason), `DEPOSIT_REFUNDED`, `DEPOSIT_TRANSFERRED` (to a new lease on transfer). Separate deposit types (rent deposit, utility deposit). A **deposit statement** is printed at move-out. Deposits never mix into the rent balance or arrears.

*Accepted refinement (A1): isolated deposit sub-ledger.*
- Entry types: `DEPOSIT_RECEIVED`, `DEPOSIT_DEDUCTION`, `DEPOSIT_REFUNDED`, `DEPOSIT_TRANSFERRED`; every entry has a `deposit_type` (RENT, WATER, ELECTRICITY, OTHER).
- **No fake zero balance:** deposits never offset rent. Rent owed KES 30,000 with a KES 30,000 deposit shows *Rent due 30,000* and *Deposit held 30,000* separately. Applying a deposit to arrears is an explicit, audited `DEPOSIT_DEDUCTION` with reason.
- Every deduction requires a `deposit_type`, a reason, and optional photo/document proof (evidence stored via `documents`). Needs capability `deposits.deduct`.
- `DEPOSIT_TRANSFERRED` moves the held amount from Lease A to Lease B (for example Unit 102 to Unit 204) as one linked pair of entries.
- Output: a **Deposit Clearance Statement** at move-out (held, deductions with reasons and proof, refund, balance), used to settle disputes.

### A2. Opening balances and go-live migration
Every landlord starts with existing tenants who already owe or have paid in advance.
- **Recommend:** an `OPENING_BALANCE` ledger entry per lease at a chosen go-live date, plus a CSV import (property, unit, tenant, phone, rent, deposit held, balance brought forward). Invoicing starts from the go-live month. Historic notebook records are not required.
- Without this, no existing landlord can adopt the product. It is an MVP feature.

### A3. Rent timing: in advance, due day, grace
Kenyan rent is normally paid **in advance** for the coming month, due by a set day (often 1st–5th).
- **Recommend:** invoice generated N days before the period (default 5), `due_day` per lease, `grace_days` per lease, overdue = past due date + grace. Billing period is a calendar month by default; allow other periods later.
- **Proration:** first and last partial months prorated by days. Organization setting: prorate by actual days (default) or charge a full month.

### A4. Tenant vs payer
The person paying is often not the tenant (spouse, employer, relative, company paying for staff).
- **Recommend:** allow **extra phone numbers on a lease** (`LeasePayer` numbers), and let matching use them. Unknown payer + correct unit reference still matches. Unknown payer + no reference goes to the unallocated inbox. Corporate tenants (a company leasing a unit) are a Tenant of type `COMPANY`.

### A5. Numbering (invoices, receipts, leases, payment references)
*Expanded in doc 11 §24, which adds lease numbers, internal payment references, tenant-facing references and platform sequences.*
- **Recommend:** per-organization sequences with a prefix and year (e.g. `INV-2026-000124`, `RCT-2026-000089`), generated inside a transaction with a row lock (`select_for_update` on a counter row) so numbers are unique and gap-tolerant under concurrency. Numbers are immutable once issued.

### A6. Concurrency and integrity
Financial data is corrupted by races, not by bad code.
- **Recommend:** database constraints for everything that must never happen (unique `trans_id`, no overlapping active leases, allocation ≤ payment amount, allocation ≤ invoice outstanding), atomic transactions in services, `select_for_update` when allocating, idempotency keys on payment creation, and ledger entries that are append-only. Tests that run allocation concurrently.

### A7. Tax fields **[VERIFY]**
- Residential landlords may pay **Monthly Rental Income (MRI) tax** to KRA on gross rent.
- **Commercial** rent can involve **VAT** (for VAT-registered landlords) and corporate tenants may withhold tax. KRA also has electronic invoicing (**eTIMS**) requirements for some taxpayers.
- **Recommend:** don't compute tax in the MVP, but **design-in**: each `ChargeType` has `is_taxable`, a nullable `tax_rate`; `Invoice` has `subtotal`, `tax_total`, `total`; `Organization` has optional `kra_pin` and `vat_registered`. Later add MRI summary report and eTIMS integration once confirmed with an accountant.

*Accepted refinement (A7): future MRI and eTIMS support.* Residential landlords are subject to Monthly Rental Income tax, currently reported as 7.5% **[VERIFY the rate, the annual income thresholds and filing rules with KRA or an accountant]**. The plan is staged:
1. **Now (design-in):** tax fields only (D-034). Keep `Invoice`/`Receipt` lines and `Payment` dates clean so tax reports can be derived later.
2. **Later (report):** an MRI summary report from collected rent per organization and month, as a helper for the landlord's own filing. It is labelled "estimate, not tax advice". The rate is a configurable, dated setting, not hard-coded.
3. **Later (integration):** eTIMS invoice submission behind an adapter interface, like the SMS and M-Pesa adapters, once KRA requirements for landlords are confirmed. Store the KRA control-unit/invoice reference on the invoice when that arrives.

### A8. Commercial vs residential
Commercial leases differ: VAT, longer terms, rent escalation clauses (e.g. 5–10% yearly), service charge, and different legal protection.
- **Recommend:** launch residential-first, but keep `Property.category` (RESIDENTIAL / COMMERCIAL / MIXED), `LeaseRentChange` (handles scheduled escalation), and `LeaseCharge` (service charge). No separate module needed.

### A9. Agency features: commission and owner remittance
Management companies earn a % of collected rent and pay the rest to property owners.
- **Recommend:** **design-in only.** Nullable `Property.owner` (a person/company, not a login), `Property.management_fee_percent`. Later: monthly **owner statement** (collected − fee − expenses = remittance) and remittance records. 

### A10. Organization context in the app (URLs and switching)
- **Recommend:** the active organization is stored in the session and shown in a header switcher (users with several organizations). Every object URL uses UUIDs, with the organization enforced on the server. No subdomain-per-organization at the start. Revisit for branded agency portals.

### A11. Identity and login
- **Recommend:** phone number is the primary identifier (E.164), email optional. **OTP via SMS** to verify the phone at signup and for password reset. Handle shared phones (a phone can belong to several Tenant rows in different organizations, but a User account is unique per phone). Handle SIM swaps: password reset requires OTP plus a cool-down notice.

### A12. Subscription lapse and organization offboarding
- **Recommend:** if a subscription lapses → 14-day grace → **read-only mode** (data always visible and exportable, no new invoices or messages). Never delete data on non-payment. On cancellation: export tool, 90-day retention, then anonymise or delete per policy **[VERIFY retention with counsel]**. Exceeding plan limits blocks *creating* new units/staff, never blocks viewing.

### A13. SMS and message cost control
Messages cost money. Who pays?
- **Recommend:** per-organization **SMS credit wallet** (top-up by M-Pesa) or a monthly bundle per plan. Every message logs cost units. Sender ID registration handled per provider. Quiet hours (no messages 21:00–07:00 EAT), tenant opt-out, per-day cap per tenant. Plan the `Message.cost` and `Organization.sms_balance` design now.

---

## Group B — Decide in the phase that builds it

### B1. Move-in and move-out process
Move-in: inspection/condition record, meter readings, keys, deposit and first-rent invoice, welcome message. Move-out: notice, inspection, final meter readings, prorated final invoice, deposit settlement, unit returned to Available.
**Recommend:** a `Lease` status flow with a simple checklist (JSON checklist first, structured later). Notice-to-vacate date stored on the lease (typical notice is one month **[VERIFY]**).

### B2. Rent increases
**Recommend:** a scheduled `LeaseRentChange` with `effective_from` in the future, a `notice_sent_at` field, and a reminder to send the notice. Bulk increase tool (e.g. +5% across a property) that creates changes for review, not silently applied.

### B3. Shared costs: service charge, water, garbage
**Recommend:** MVP = fixed recurring `LeaseCharge`s. Phase 9 = metered water (readings) and **split of a shared bill across units** (equal, by unit size, by occupants). Keep `InvoiceLine` generic so both produce lines.

### B4. Cash-handling fraud controls
Caretakers collecting cash is the biggest fraud risk in the market.
**Recommend:** receipt issued with every cash payment (sequential numbers), tenant is notified by SMS of every recorded payment (tenant will complain if the caretaker records less), daily cash summary per caretaker, `PENDING_REVIEW` for unconfirmed cash, alerts for edits/reversals after the fact, and full audit trail. The tenant SMS alone catches most theft.

### B5. Disputes and corrections
**Recommend:** a tenant can flag an invoice or payment as "disputed" (note, status). Corrections are credit notes / adjustments / reversals, never edits. Dispute list visible to Manager/Accountant.

### B6. Notices and announcements
Bulk messages to a building (water outage, meeting, notice). **Recommend:** `Announcement` = a Message batch to a selected property/building/unit group, with a recipient preview and a cost estimate before sending.

### B7. Reports and exports
**Recommend:** define a fixed report list before dashboards: rent roll, collection by property, arrears aging, occupancy, vacancy, deposit liability, payment by method, unallocated payments, caretaker cash, expenses by property. CSV export first, PDF next. Exports require `reports.export` and are audited.

### B8. Data import quality
**Recommend:** import with **validation preview** (rows OK / errors), dry-run, undo within 24 hours for a batch, normalisation of phone numbers and unit codes, and a downloadable template.

### B9. Tenant screening and references
Payment history could become a rental reference. **Recommend:** later, opt-in, only with tenant consent. Not in the MVP.

### B10. Legal documents
Tenancy agreement templates, notices, demand letters. **Recommend:** later; generate PDFs from a template filled with lease data. Have a lawyer review the template and include a disclaimer.

---

## Group C — Product, engineering and operations

### C1. Offline and weak connectivity
Caretakers may be in low-signal areas.
**Recommend:** PWA with cached shell, queue for recording payments/maintenance while offline (server-side idempotency key prevents duplicates), light pages, small images, no heavy JS frameworks.

### C2. UX before views
**Recommend:** sketch the 10 key mobile screens (dashboard, unit list, unit detail, tenant detail, record payment, invoice, arrears, add lease, unallocated inbox, tenant portal home) in Figma or paper **before** writing templates. Define a small design system: colours, buttons, tables, status badges.

### C3. Accessibility and language
Large tap targets, readable fonts, clear error messages, Swahili strings, simple wording (avoid accounting jargon: "Amount owed" instead of "Receivable").

### C4. Internal events
When something happens (invoice issued, payment received) several things follow (message, audit, dashboard cache).
**Recommend:** explicit service calls plus a small `events` module (publish/subscribe inside the process) that enqueues background tasks. Avoid hidden Django signals for business logic. Use an outbox table for anything that must not be lost.

### C5. Background job reliability
**Recommend:** every job idempotent and retryable; jobs log start/finish; a failed-jobs dashboard for Platform Admin; scheduled jobs (invoice generation, reminders, reconciliation) run per organization in batches so 1,000+ units do not block.

### C6. Performance at 1,000+ units
Indexes on `(organization, status)`, phone, unit code, invoice number, `trans_id`. Use aggregate queries and `select_related`. Cache dashboard totals with invalidation on payment/invoice. Test with seeded large data (10,000 units, 50,000 invoices) early.

### C7. Environments, releases, migrations
**Recommend:** local / staging / production; CI runs tests and `makemigrations --check`; backwards-compatible migrations (add columns before using them); a migration on financial tables is reviewed by hand; feature flags for unfinished features; staging uses Daraja sandbox.

### C8. Observability and support tooling
**Recommend:** Sentry for errors, structured logs with organization ID, a health endpoint, uptime alerts. A Platform Admin "support view": look up an organization, its recent errors, webhook log, failed messages, with consent-based impersonation logged in the audit trail.

### C9. Monetisation details
**Recommend:** pilot free, then plans by units (see doc 05). Add-ons: SMS credits, extra staff seats. Consider optional per-transaction pricing for M-Pesa reconciliation only after legal review **[VERIFY]**. Annual plans with discount. Pay via M-Pesa STK push.

### C10. Demo and seed data
**Recommend:** a management command that creates a demo organization with realistic Kenyan data (properties, units, tenants, invoices, payments) for testing, screenshots, sales demos and performance tests. Never run on production organizations.

### C11. Product analytics for us
**Recommend:** privacy-friendly analytics: activation (time to first invoice), weekly active organizations, feature usage. No tenant personal data in analytics.

### C12. Documentation for users
**Recommend:** short in-app help, Swahili video walkthroughs, an onboarding checklist, printable "how tenants pay" card (Paybill, account reference) that landlords can post at the building.

---

## Recommended decisions to record now
| # | Decision |
|---|---|
| A1 | Deposits are separate ledger kinds, never income |
| A2 | Opening balances + CSV go-live import are MVP |
| A3 | Rent in advance; invoice N days before; per-lease due day and grace; prorate by days |
| A4 | Extra payer phone numbers per lease; COMPANY tenant type |
| A5 | Per-organization locked sequences for invoice and receipt numbers |
| A6 | DB constraints + atomic + row locks for payments and allocation |
| A7 | Tax fields designed in, calculation later |
| A8 | Residential first; commercial fields designed in |
| A9 | Agency commission/remittance designed in only |
| A10 | Organization in session, UUID URLs, no subdomains yet |
| A11 | Phone-first login with SMS OTP |
| A12 | Lapse → read-only, never delete for non-payment |
| A13 | SMS wallet, quiet hours, opt-out, message cost logged |

---

## Group D — Foundations (decide before models, because they are expensive to change later)

| # | Foundation | Recommendation |
|---|---|---|
| D1 | **Accounting model** | Keep a single-entry, append-only ledger per lease plus a separate deposit ledger for the MVP. Design entries (debit/credit, `kind`, `source`, `reversal_of`) so a double-entry chart of accounts can be added when the accounting module arrives. Decide this now; retrofitting is the costliest change. |
| D2 | **We never hold customer money** | Rent goes straight to the landlord's own Paybill, Till or bank account. Holding or pooling funds may trigger payment-service licensing by the Central Bank **[VERIFY with counsel]**. Record this as a product principle and state it in the Terms. |
| D3 | **Whose Paybill?** | Landlord's own Paybill/Till configured in the app (current design). An aggregator Paybill run by us is a different legal and financial product; do not mix. |
| D4 | **Vocabulary clash: "tenant"** | In code, "tenant" means the renter and the SaaS customer is `Organization`. Never use "tenant" for multi-tenancy in models or docs. Keep a glossary in doc 03. |
| D5 | **Tenant identity across organizations** | A renter is a per-organization `Tenant` record, optionally linked to one global `User` by phone. No cross-organization data sharing without consent (protects privacy, enables a future opt-in payment reference). |
| D6 | **Isolation depth** | Application-level scoping now (scoped manager, tests). Consider PostgreSQL Row Level Security later as defence in depth for the most sensitive tables. |
| D7 | **Effective dating** | Rent, charges, roles and payment-account assignments have `effective_from/to`, never overwritten, so history and backdated corrections stay explainable. |
| D8 | **Addresses and locations** | Structured: county, sub-county/ward, estate/area, street/landmark, optional GPS. Kenyan addresses are landmark-based; free text alone kills later analysis by area. |
| D9 | **Legal basis of the product** | Tenancy law and eviction/notice rules (Landlord and Tenant Act, Rent Restriction Act, distress for rent, notice periods) **[VERIFY with counsel]**. The product records and reminds; it does not give legal advice or auto-evict. Terms of Service, Privacy Policy, and data processing terms before pilots. |
| D10 | **Data ownership and portability** | Organization owns its data; full export any time; retention and deletion per D-036 and doc 06. |
| D11 | **Disaster recovery** | Define RPO/RTO (for example lose at most 15 minutes, restore within 4 hours), automated backups, and a tested restore drill before real money is handled. |
| D12 | **Service layer** | Business rules in services (`billing/services.py`), not in views or models, so web, API, jobs and the AI assistant all call one path. |
| D13 | **Configuration vs code** | Per-organization settings (grace days, due day, proration, quiet hours) live in a settings table with defaults, not in code. Feature flags and plan entitlements gate unfinished or paid features. |
| D14 | **Support and abuse** | Impersonation only with logged consent, rate limits on login/OTP/callbacks, and a way to freeze an organization. |
| D15 | **Business viability** | Unit economics per unit (SMS, hosting, support) against price, and competitor scan, so pricing (doc 05) is grounded before launch. |
