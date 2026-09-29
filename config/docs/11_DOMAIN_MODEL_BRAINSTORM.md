# 11 — Domain Model Brainstorm

**Status: brainstorming. No models or views are written until this document and 12_IMPLEMENTATION_SCOPE.md are agreed.**

Start from the story, not the fields:
> A landlord signs up → creates a workspace → adds a property and units → adds tenants → creates leases → the system bills rent → tenants pay (M-Pesa, cash) → payments are matched and allocated → receipts and reminders go out → the landlord sees collection and arrears.

## 0. Core idea: User ≠ Organization ≠ Role

| Concept | Meaning | Example |
|---|---|---|
| **User** | The person who logs in | Mary |
| **Organization** | The business/workspace that owns the data | ABC Property Management |
| **Membership** | Mary's seat in that organization; carries the role and property scope | Mary in ABC → Accountant |
| **Role** | A named bundle of capabilities | Accountant |

There is no `is_landlord` flag. One user can have memberships in several organizations. Being a landlord is simply being an Owner in an organization.

Plus two things outside the organization world:
- **Platform Admin** (us): `is_staff`/`is_superuser` on the User. Not a role inside any organization. Sees the whole platform.
- **Tenant user**: a User linked to a Tenant record (portal). Has no Membership. Sees only their own lease data.

## 1. Capacity target (design for this from day 1)
1 landlord → 1 organization → many properties → **1,000+ units, 1,000+ tenants**, several staff.
Consequences: paginate everything, index search fields, bulk CSV import, invoice generation and reminders run as background batch jobs, dashboards use aggregate queries (not row loops), no page loads all units.

## 2. Entity map

```
PLATFORM                          ORGANIZATION WORLD
────────                          ──────────────────
User ──< Membership >── Organization ──┬─< Property ──┬─< Building (optional)
  │          │                         │              └─< Unit (building optional)
  │          ├─ Role ──< Capability    │
  │          └─< PropertyAccess ─> Property
  │                                    ├─< Tenant (person record)
  │  Tenant portal (later):            ├─< Lease ──< LeaseTenant >── Tenant
  └─ TenantAccount ──> Tenant          │     │  ├─ Unit
                                       │     │  ├─< LeaseRentChange
Subscription (separate app) ──> Org    │     │  └─< LeaseCharge (recurring)
                                       │     │
                                       │     ├─< Invoice ──< InvoiceLine ──> ChargeType
                                       │     ├─< LedgerEntry
                                       │     └─< Payment ──< PaymentAllocation ──> Invoice
                                       │            └─ Receipt (1:1)
                                       │
                                       ├─< PaymentAccount ──< PropertyPaymentAccount >── Property
                                       │        └─< MpesaTransaction ── (matched) ─> Payment
                                       ├─< Message (communications log) ─ MessageTemplate
                                       └─< AuditEvent
```

## 3. Answers to the open questions

| Question | Decision | Why |
|---|---|---|
| Can one tenant have multiple leases? | **Yes.** Over time (history) and at the same time (two units). | Tenant is a person; Lease is an agreement. |
| Can two tenants share one unit? | **Yes.** Via co-tenants on one lease. | Flatmates, couples, business partners. |
| Can one lease have multiple tenants? | **Yes** through `LeaseTenant(lease, tenant, is_primary)`. Exactly one primary, who is billed and contacted. | Cheap to build now, painful to retrofit. |
| Can two active leases exist on one unit at once? | **No** for overlapping dates (database constraint + service check). | Otherwise occupancy and billing double-count. |
| Can a tenant move from Unit A to B? | **Yes, as a transfer:** end lease A (move-out date), create lease B, store `previous_lease`. Deposit carries over by a ledger transfer entry. | Keeps history per unit and clean billing. |
| Can a landlord own properties jointly? | **Not in MVP.** Design leaves room: later `PropertyOwner(property, owner_party, share_percent)`. | Ownership shares affect statements, not rent collection. |
| Can a property belong to multiple organizations? | **No.** A property has exactly one managing organization. Sharing happens by giving people access, or by a formal transfer. | Simplest possible isolation rule. |
| Ownership vs management? | Organization = **who manages**. A separate optional `owner` (person/company) on a property is added later for agencies. | Enables agency mode without touching core tables. |
| Can a manager see only selected properties? | **Yes.** `Membership.all_properties` flag + `PropertyAccess` rows. | Agencies and larger owners need it. |
| Can a tenant record be shared across organizations? | **No.** Tenant is per organization. The same person in two organizations = two Tenant rows, optionally one User account. | Data isolation and privacy. |
| Multiple owners in one organization? | **Yes.** Several Owner memberships. | Spouses, partners. |

## 4. Roles and permissions (editable, not hardcoded)

"Roles can be altered in admin" means roles live in the database, not in code:

```
Capability   (codename, description)         e.g. payments.record, tenants.view_sensitive
Role         (organization, name, based_on_template)   per-organization copy of a RoleTemplate
RoleTemplate (name, capabilities)                       platform defaults
MembershipCapability (membership, capability, granted)  per-person override
Role ─< RoleCapability >─ Capability
Membership   (user, organization, role, all_properties, is_active)
PropertyAccess (membership, property)
```
- **System roles** (seeded, editable by Platform Admin in Django admin): Owner, Manager, Accountant, Caretaker, Maintenance, Viewer.
- **Capabilities** are defined in code (the code checks them), and synced to the database. Admins assign them to roles; they cannot invent behaviour.
- **Custom roles per organization**: built in the MVP. Templates are copied into each organization at creation and edited freely. Per-person overrides via `MembershipCapability`. Full rules in doc 13.
- The Owner role always keeps critical capabilities (cannot lock yourself out).
- Views ask "does this membership have capability X for this property?", never "is this a manager?".

Role matrix defaults are in 10_USERS_ROLES_PERMISSIONS.md; the "maybe" cells from the brainstorm are settled there as: Accountant views properties = **read-only yes**; Manager records expenses = **yes**; Manager edits M-Pesa settings = **no** (Owner only, changeable by editing the role); Maintenance = maintenance and assigned units only.

### Platform Admin (us)
Sees: all organizations, subscription plans and status, usage (properties/units/tenants), integration status, support issues, system logs, audit logs, platform analytics.
Does not see tenant-level customer data by default; time-limited, logged access with customer consent (see doc 10). Built with Django admin first, a custom "platform console" later.

## 5. Property structure (supports all three shapes)

```
Case A  Property ─ Units                       (small block; building = null)
Case B  Property ─ Building ─ Units            (large development)
Case C  Property ─ 1 Unit "Main house"         (standalone house; auto-created)
```
- `Unit.property` is required; `Unit.building` is optional (and must belong to the same property).
- `Property.type`: Apartment block, Estate, Commercial, Mixed-use, Single house.
- **Unit types:** Apartment, House, Bedsitter, Studio, Shop, Office, Warehouse, Parking, Other (+ free-text label). Stored as a choice list so reports can group by type.
- `Unit.code` unique per property (A01). `Unit.payment_reference` unique per organization (e.g. `GV-A102`), used for M-Pesa account numbers.
- **Occupancy is derived from leases**, never a stored `occupied` flag. Store only a manual state: `NORMAL`, `RESERVED`, `UNDER_MAINTENANCE`, `INACTIVE`. Effective status = Occupied if an active lease covers today, else that manual state / Available.
- Archive, do not delete, units and properties that have history.

## 6. Tenant lifecycle
`Prospect → Active → (Lease ending) → Moved out → Former`
- Only `PROSPECT`, `ACTIVE`, `FORMER` are stored on Tenant. "Lease ending" and "Moved out" come from the lease.
- A tenant is `ACTIVE` while any lease is active; `FORMER` when none are.
- Leaving never deletes anything. Leases, invoices, payments, receipts, maintenance history stay attached.
- Sensitive fields (national ID, documents) permission-gated. Phone stored normalised in E.164 (+2547…).
- Tenant onboarding: landlord creates the Tenant; portal invite (SMS link, token, expires) later creates a User linked through `TenantAccount`. Claiming is by matching the verified phone number.

## 7. Lease lifecycle
`Draft → Active → (Expiring) → Renewed | Ended | Terminated`
- Stored statuses: `DRAFT, ACTIVE, ENDED, TERMINATED, RENEWED`. **Expiring** is derived (`end_date` within N days).
- Fields: unit, start_date, end_date (null = periodic/rolling), rent amount, deposit, billing frequency (monthly default; quarterly/annual/custom later), due day, grace days, notice period, terms, `previous_lease`.
- **Rent changes are records, not edits**: `LeaseRentChange(lease, effective_from, amount, reason, created_by)`. Rent on any date is derived from these, so history and audit are clear.
- Renewal = new lease (or a term extension) linked by `previous_lease`. Termination records date and reason.
- Recurring extras (water, garbage, service charge) are `LeaseCharge(lease, charge_type, amount, frequency, active_from/to)`.
- Billing derives from the lease.

## 8. Billing (what is owed) — separate from payments (what was paid)

```
ChargeType  (org-level: RENT, WATER, GARBAGE, SERVICE_CHARGE, DEPOSIT, LATE_FEE, OTHER)
Invoice     (org, lease, number [per-org sequence], period_start/end, issue_date, due_date, status, total)
InvoiceLine (invoice, charge_type, description, quantity, unit_price, amount)
LedgerEntry (org, lease, date, kind, amount [signed], invoice?, payment?, reason)  ← append-only
```
- Invoice status: `DRAFT, ISSUED, PARTIALLY_PAID, PAID, VOID`. **Overdue** is derived (due date + grace passed and unpaid).
- Generation is idempotent: unique `(lease, period, charge_type)`. Run by a scheduled job. Proration for move-in/out mid-month.
- **Ledger**: invoice issued = debit, payment allocated = credit, adjustment/waiver/reversal = its own entries, deposit held/refunded/transferred = its own kinds. Balance = sum of entries. Nothing is edited or deleted.
- Utilities: **MVP = fixed recurring charges**. Metered billing (previous/current reading × rate) is a later `utilities` module that produces invoice lines.
- Late fees/penalties: designed for (charge type + rule), built later, off by default.
- Arrears aging (0–30, 31–60, 61–90, 90+ days) is a query over unpaid invoices.

## 9. Payments and allocation

```
Payment            (org, lease, amount, method, paid_at, reference, status, payment_account?, recorded_by, mpesa_transaction?)
PaymentAllocation  (payment, invoice, amount)
Receipt            (payment 1:1, number [per-org sequence], issued_at, pdf)
```
- Methods: `MPESA, CASH, BANK, CHEQUE, OTHER`. Status: `PENDING_REVIEW, CONFIRMED, REVERSED`.
- **Allocation order (default):** oldest unpaid invoice first, then next. Can be overridden by a manager. Unallocated remainder becomes tenant credit (positive ledger balance) applied to the next invoice.
- **Partial payments:** 25,000 owed, 10,000 paid → invoice `PARTIALLY_PAID`, 15,000 outstanding; later 5,000 → 10,000 outstanding.
- **Overpayment:** creates credit, never lost.
- **Reversal:** payment `REVERSED` plus reversing ledger entries and audit record. Never deleted.
- **Receipt** contents: organization, tenant, property, unit, amount, method, M-Pesa reference, date, invoices settled, remaining balance, receipt number. Delivered as PDF, SMS/WhatsApp/email link, and in the tenant portal.
- Caretaker-recorded cash payments enter `PENDING_REVIEW` until confirmed.

## 10. M-Pesa and payment accounts

```
PaymentAccount          (org, type PAYBILL|TILL|BANK|MPESA_PERSONAL|CASH, number, display_name, status, credentials [encrypted])
PropertyPaymentAccount  (property, payment_account, is_default)
MpesaTransaction        (payment_account?, trans_id UNIQUE, bill_ref_number, msisdn, amount, paid_at,
                         raw_payload, status, matched_lease?, payment?, matched_by, failure_reason)
```
Principles:
- **An organization has many payment accounts** (no fixed limit; plans may limit later). Never store till/paybill on the User or Organization row.
- **A payment account can serve many properties, and a property can have many accounts.** The database doesn't care which scenario the landlord picks.
- Incoming callback → look up the shortcode → `PaymentAccount` → organization. If no account is found, it is stored as orphaned and alerts the Platform Admin; it is never dropped.
- **Payment reference:** each unit has a `payment_reference` (e.g. `GV-A102`). Tenants enter it as the Paybill account number. Recommend Paybill for automatic matching because Till (Buy Goods) payments may not carry an account reference **[VERIFY with Daraja docs]**.
- **Matching engine** tries signals in order, and only auto-allocates when confident:
  1. Payment account → organization
  2. Bill reference → unit → active lease
  3. Phone number → tenant
  4. Amount vs open invoice
  5. Otherwise → **Unallocated inbox** for manual matching.
- Handles: duplicates (unique `trans_id`), failed STK, reversals, unallocated payments, manual reconciliation, per-property collection reporting.
- Flow: `MpesaTransaction → PaymentAccount → Organization → Property → Unit → Tenant/Lease → Payment → Allocation → Invoice`.

## 11. Communication center

```
MessageTemplate (org nullable, key, channel, language, body)
Message         (org, recipient tenant/user/phone, channel [SMS|WHATSAPP|EMAIL|IN_APP], template, body,
                 status [QUEUED|SENT|DELIVERED|FAILED], provider, provider_id, failure_reason, related object, sent_at)
```
- One provider **interface** per channel (`send(message)`), with adapters (Africa's Talking, Meta, email). Swapping providers touches only the adapter.
- Triggers (domain events): invoice issued, rent due soon, payment received/receipt, rent overdue, lease expiring, maintenance update, announcement.
- Tenant opt-out respected; messages logged for delivery reports and billing usage.
- MVP: SMS + in-app. Email, WhatsApp later.

## 12. Audit and history
- `AuditEvent(org, actor, action, object_type, object_id, changes{field: [old, new]}, ip, created_at)`.
- Written from services for: login, role/membership changes, lease/rent changes, invoice void/adjust, payment record/reverse, exports, settings, support-access sessions.
- Example: Mary, Updated Lease, Monthly Rent, 20,000 → 5,000, 26 Sept 2026.
- Soft deletion: `archived_at/archived_by` on Property, Unit, Tenant, Lease. Financial tables cannot be deleted at all. Foreign keys use `PROTECT`.

## 13. Documents (later)
`Document(org, file, category, visibility [PRIVATE|SHARED_WITH_TENANT], linked to property/lease/tenant, uploaded_by)`.
Rules: private object storage, signed URLs, size and type limits, permission check on every download.

## 14. Maintenance and expenses (later)
- `MaintenanceRequest(org, unit, tenant?, title, priority, status, assigned_to, cost)`; Maintenance role sees assigned requests only; tenants can submit and follow their own.
- `Expense(org, property, category, amount, date, receipt, recorded_by)` → property net income = collected rent − expenses.

## 15. Subscriptions (SaaS billing — separate app)
`Plan`, `Subscription(org, plan, status, period)`, `UsageCounter`. **Landlord pays LandlordPMS**, which is a totally different money flow from tenant rent. Core models never contain plan logic. Limits are enforced by one `entitlements` service (units, staff, payment accounts, SMS, AI usage) called from the places that create things.

## 16. Onboarding flow
```
Sign up (phone/email + password + accept terms)
  → verify phone/email
  → create Organization (name, type) + Owner membership   [one transaction]
  → setup checklist (derived from data, no table): add property → add units → add tenants
      → create leases → set rent → add payment method → dashboard
```
Also: staff invite (SMS/email link, role + property scope, expires), tenant invite (later). Sample data option and CSV import for large portfolios.

## 17. Reporting, dashboards, search
- Portfolio: properties, units, occupied, vacant, expected rent, collected, outstanding, collection rate, expenses, net income.
- **Per property**: same metrics per property, driven by `Payment → Lease → Unit → Property`.
- Later: arrears aging, tenant payment behaviour, revenue trends, occupancy trends.
- Dashboards per role: Owner (financial + occupancy), Accountant (invoices, payments, reconciliation), Maintenance (open jobs), Tenant (rent, bills, receipts, requests).
- Search across name, phone, unit code, invoice number, M-Pesa reference → indexes on those columns from day 1; Postgres trigram search later.
- Filters: property, building, unit, tenant, payment status, lease status, arrears, date.

## 18. Tenant portal (separate experience)
Sees only their own: profile, unit, lease, current bills, payment history, receipts, balance, next due date, maintenance requests, notices, documents shared with them. Hard rule: every query filters by the tenant's own lease IDs. Tested for tenant-to-tenant leakage.

## 19. API and AI
- Business logic in `services.py`/`selectors.py` so a REST API (later) reuses it.
- AI never touches the database directly: `AI → permission layer → approved tools (e.g. get_outstanding_balance(org)) → database → answer`. Tools always take the caller's organization/membership.

## 20. Cross-cutting rules (apply to every model)
1. Organization FK on every business table (`PROTECT`), scoped manager.
2. `created_at`, `updated_at`, `created_by` where meaningful.
3. Money: `DecimalField(12,2)`, KES only for now (currency column reserved).
4. All timestamps timezone-aware, displayed in Africa/Nairobi; due dates are dates, not datetimes.
5. UUID public IDs for anything exposed in URLs/webhooks.
6. Statuses as `TextChoices`; derived states are computed, not stored.
7. External providers (M-Pesa, SMS, WhatsApp, email, storage, AI) live behind adapters.
8. Every app: tests for cross-organization isolation and permission checks.

## 21. Risks to watch
- Modelling occupancy as a flag → contradicts leases.
- Storing balances directly → drift and disputes; use the ledger.
- Tying M-Pesa to the landlord rather than the payment account.
- Letting an incoming callback be dropped or processed twice.
- Building tenant portal, maintenance, AI too early.

---

# Part 2 — Financial separation, history, numbering, money, reporting, notifications

## 22. Three separate money flows (never mixed)

| | **A. Platform billing** | **B. Property financial records** | **C. Property expenses** |
|---|---|---|---|
| Story | Landlord pays LandlordPMS | Tenant pays landlord (rent, deposits, utilities) | Landlord pays a supplier/contractor |
| Issuer / payee | LandlordPMS (our company) | The organization | Supplier or contractor |
| Customer / payer | The organization (billing contact, KRA PIN) | The tenant | The organization |
| App | `subscriptions` | `billing`, `payments`, `mpesa` | `expenses` (+ `maintenance` link) |
| Core records | Plan, Subscription, SubscriptionInvoice, SubscriptionPayment, PlatformReceipt, SmsWalletTopUp, UsageCounter | Invoice, InvoiceLine, LedgerEntry, Payment, PaymentAllocation, Receipt, MpesaTransaction | Expense, ExpenseCategory, Supplier, optional link to MaintenanceRequest, attachments |
| Money goes to | LandlordPMS's own Paybill/bank | The organization's payment accounts | Supplier (M-Pesa, bank, cash) |
| Tax treatment | LandlordPMS's own VAT/eTIMS obligations on its invoices **[VERIFY]** | Landlord's MRI or VAT obligations **[VERIFY]** | Expense deductions and supplier withholding **[VERIFY]** |
| Numbering | Platform sequences (`LPM-INV-…`, `LPM-RCT-…`) owned by us | Per-organization sequences (`INV-…`, `RCT-…`) | Per-organization (`EXP-…`) |
| Appears in | Subscription page, platform revenue reports | Tenant statements, rent reports, dashboards | Expense reports, property net income |

Rules:
1. **Separate tables, separate sequences, separate reports.** No foreign key from a subscription invoice to a rent invoice or the reverse.
2. A landlord's platform subscription or SMS top-up **never** appears in that landlord's tenant ledger or property income.
3. Expenses **never** reduce a tenant's balance. They only affect property profit reports.
4. Platform revenue reports are Platform Admin only and never expose organization-level tenant data.
5. Only the `subscriptions` app knows about plans and entitlements; the other apps ask the `entitlements` service.
6. The organization's tax details (`kra_pin`, `vat_registered`, billing contact) are used for both A (as customer) and B/C (as issuer/payer), but they are settings, not shared records.
7. Property net operating income = collected rent (B) − expenses paid (C). It never includes A.

## 23. Soft deletion and history: what may be archived, what may never go

| Category | Records | Rule |
|---|---|---|
| **Never deleted** (only voided or reversed, with reason, actor and audit event) | Invoice, InvoiceLine, LedgerEntry, Payment, PaymentAllocation, Receipt, MpesaTransaction (raw payload kept), Expense, SubscriptionInvoice, SubscriptionPayment, PlatformReceipt, AuditEvent, Message log | Database `PROTECT`; no delete button; corrections are new entries |
| **Archived** (`archived_at`, `archived_by`, hidden by default, restorable) | Organization (deactivated), Property, Building, Unit, Tenant, Lease (once ended and settled), PaymentAccount, ChargeType, Role, Membership, Supplier, MaintenanceRequest, Document, MessageTemplate | Archive blocked while there is an active lease, unsettled balance or open request |
| **Hard delete allowed** | Never-issued drafts (unnumbered draft invoice/lease), expired invitations and OTPs, sessions, unconfirmed import staging rows, temporary files | Only records that carry no financial or audit meaning |
| **Anonymised on request** | Tenant/user personal data (name, phone, national ID, emails) after the legal retention period, or on a valid erasure request | Personal fields replaced; financial rows and totals kept intact **[VERIFY retention with counsel; tax record-keeping is commonly several years]** |

Implementation notes:
- Two managers: `objects` (excludes archived) and `all_objects`.
- Archiving needs the `*.archive` capability, and restoring needs `*.restore`. Both are audited.
- `on_delete=PROTECT` on every business foreign key. Deleting an Organization is never a cascade; it is a formal offboarding job (export → retention window → anonymise).
- Uniqueness (unit code, payment reference) holds across archived rows, so old references cannot be reused for a different unit by accident.
- Moving a tenant out changes status only. Leases, invoices, payments, receipts and maintenance stay attached.

## 24. Numbering

One `NumberSequence(organization, key, period, prefix, next_value)` table, with a unique constraint on `(organization, key, period)`. Numbers are allocated inside the same transaction that issues the document, using a row lock, so there are no duplicates.

| Document | Format | Scope |
|---|---|---|
| Invoice | `INV-2026-000124` | per organization, resets each year |
| Credit note | `CN-2026-000007` | per organization |
| Receipt | `RCT-2026-000089` | per organization |
| Lease | `LSE-2026-000042` | per organization |
| Internal payment reference | `PAY-2026-000310` | per organization |
| Expense | `EXP-2026-000210` | per organization |
| Maintenance request | `MNT-2026-000031` | per organization |
| **External reference** | M-Pesa TransID, bank reference | stored separately as `external_reference`, unique per source |
| **Tenant-facing payment reference** | `{PROPERTY_CODE}-{UNIT_CODE}`, e.g. `GV-A102` | unique per organization; the account number a tenant types on Paybill |
| Platform subscription invoice/receipt | `LPM-INV-2026-000001`, `LPM-RCT-2026-000001` | platform-wide, ours only |

Rules:
1. Numbers are assigned when a document is **issued**, not when drafted. Drafts have no number and cannot exhaust the sequence.
2. Numbers are **immutable** and never reused. A voided invoice keeps its number and still appears in lists.
3. Organizations may set the prefix (`GV-INV`) but not edit issued numbers.
4. Payment reference: uppercase, no easily confused characters (no `O/0`, `I/1` in generated codes), short enough to type on a phone. If a unit's reference must change, the old one stays as an alias so late payments still match.
5. Concurrency test: 50 parallel invoice issues must produce 50 distinct numbers.
6. Whether tax invoices must be gap-free is a compliance question **[VERIFY]**; the design (assign at issue, keep voids) already supports it.

## 25. Money and currency

| Rule | Decision |
|---|---|
| Type | `DecimalField(max_digits=14, decimal_places=2)` for all money. Never `float`. |
| Currency | `KES` only for now. Each organization has `currency` (default KES). Money rows carry a `currency` column (`CharField(3)`, default KES) so multi-currency is possible later without a rewrite. One document, one currency. |
| Quantities and rates | Meter units and quantities `Decimal(14,4)`. Percentages (tax, fees, penalties) `Decimal(6,3)`. |
| Rounding | `ROUND_HALF_UP` to 2 decimals, **at the line level**. Document totals equal the sum of rounded lines, and are never rounded again. |
| Proration | `amount = round(monthly_rent × days_used ÷ days_in_month, 2)`. Days counted in Africa/Nairobi. |
| Signed amounts | Ledger stores a positive `amount` with a `direction` (DEBIT/CREDIT), or a signed amount, chosen once and enforced with a check constraint. |
| Constraints | `Payment.amount > 0`, `Allocation.amount > 0`, allocation total ≤ payment, allocation ≤ invoice outstanding, invoice total = sum of lines. Enforced by database `CheckConstraint`s plus service validation. |
| Input | Server-side parse from a string using `Decimal`. Reject negatives, more than 2 decimals, non-numbers. Strip `KES`, commas, spaces. |
| API/JSON | Money is serialised as a string (`"20000.00"`). |
| Display | `KES 20,000.00`, thousands separators, one helper `format_money()` used everywhere. |
| M-Pesa | Amounts arrive as decimals; parse with `Decimal`, never float; store the raw payload untouched. |
| Helpers | Single `core/money.py` (parse, round, prorate, format). All apps use it. Unit tests for edge cases (leap years, 31-day months, 0.005 rounding). |

## 26. Reporting: exact metric definitions

Dashboards, PDFs and exports must all read from **one place** (`reports/metrics.py`), so the numbers agree. All periods use Africa/Nairobi month boundaries. Every report can be scoped by property, building, unit type and period, and respects the viewer's property scope and capability (`reports.view_financial`).

| Metric | Definition |
|---|---|
| Rentable units | Non-archived units that are not `INACTIVE` |
| Occupied units | Rentable units with an active lease covering the date |
| Occupancy rate | Occupied ÷ rentable |
| Vacant units | Rentable − occupied (with `UNDER_MAINTENANCE`/`RESERVED` shown separately) |
| Expected rent (period) | Rent lines on invoices for the period, excluding void |
| Collected for period | Confirmed allocations against that period's rent invoices |
| **Collection rate** | Collected for period ÷ expected rent |
| Cash received (period) | Confirmed payments dated in the period, including advances and arrears clearing; it differs from "collected for period" and both are shown |
| Outstanding | Unpaid balance of issued invoices |
| **Arrears** | Outstanding on invoices past due date + grace, by aging bucket (0–30, 31–60, 61–90, 90+) |
| Tenant credit | Positive balances from over-payments |
| Deposits held | Sum of deposit ledger entries; a liability, not income, reported separately |
| Gross potential rent | Sum of `Unit.list_rent` (nullable, design-in) for rentable units; vacancy loss = potential − expected |
| Property income | Rent and other charges collected (cash basis), by property |
| Property expenses | Expenses (flow C) by property and category, in the period |
| **Net operating income** | Property income − property expenses |
| Tenant payment behaviour | Average days late, late payment count (later) |
| Unallocated payments | Count and amount waiting in the M-Pesa inbox |
| Caretaker cash | Cash recorded per person, confirmed vs pending |

Not in property reports: platform subscription fees, SMS top-ups, deposits (separate section), refunds.
Performance: aggregate queries with indexes; cache dashboard totals and invalidate on invoice/payment change; heavy reports run as background jobs; daily snapshot table for trends (later).

## 27. Notification preferences: who receives what

Two layers must both allow a message, plus the recipient's channel and consent.

```
NotificationType     catalog defined in code (see table)
OrganizationNotificationRule (organization, type, enabled, channels, timing offsets, template)
NotificationPreference (recipient user OR tenant, type, channel, enabled)
ConsentRecord        (subject, channel, granted_at, source, revoked_at)
Recipient settings   (language en|sw, preferred channel, quiet hours, whatsapp_opt_in)
```

### Effective delivery
Send only if: the organization rule is enabled **and** the recipient has not opted out of this type/channel **and** consent exists for the channel **and** the channel is available (SMS wallet has balance, WhatsApp template approved) **and** it is outside quiet hours (or the type is urgent). Otherwise fall back to the next channel or in-app, and log why in the `Message` record.

### Audience matrix
| Notification type | Tenant (primary) | Co-tenants | Payer phones | Owner | Manager | Accountant | Caretaker | Maintenance Mgr/Staff | Leasing Agent |
|---|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
| Invoice issued | ● | ◐ | ○ | ○ | ○ | ○ | ○ | ○ | ○ |
| Rent due soon | ● | ◐ | ○ | ○ | ○ | ○ | ○ | ○ | ○ |
| Rent overdue | ● | ◐ | ○ | ◐ digest | ◐ digest | ○ | ◐ assigned | ○ | ○ |
| Payment received / receipt | ● | ◐ | ● (the payer) | ◐ | ◐ | ○ | ◐ | ○ | ○ |
| Payment recorded by staff (fraud check) | ● | ○ | ○ | ◐ | ◐ | ○ | ○ | ○ | ○ |
| Unmatched M-Pesa payment | ○ | ○ | ○ | ● | ● | ● | ○ | ○ | ○ |
| Lease expiring | ◐ | ◐ | ○ | ● | ● | ○ | ○ | ○ | ● |
| Rent increase notice | ● | ◐ | ○ | ◐ | ◐ | ○ | ○ | ○ | ○ |
| Maintenance update | ● (own request) | ○ | ○ | ◐ | ◐ | ○ | ◐ | ● | ○ |
| Maintenance assigned | ○ | ○ | ○ | ○ | ○ | ○ | ○ | ● | ○ |
| Announcement / notice | ● | ◐ | ○ | ○ | ○ | ○ | ◐ | ○ | ○ |
| Daily cash summary | ○ | ○ | ○ | ◐ | ◐ | ◐ | ○ | ○ | ○ |
| Weekly collection summary | ○ | ○ | ○ | ● | ◐ | ◐ | ○ | ○ | ○ |
| Low SMS balance | ○ | ○ | ○ | ● | ◐ | ○ | ○ | ○ | ○ |
| Subscription expiring / failed (flow A) | ○ | ○ | ○ | ● billing contact | ○ | ○ | ○ | ○ | ○ |
| Security alert (login, reset, role change) | the user concerned | | | ● | | | | | |

`●` default on · `◐` optional, off by default · `○` never.

### Rules
- **Staff recipients are chosen by capability and property scope, not role name.** "Unmatched M-Pesa payment" goes to whoever holds `mpesa.match`; "maintenance assigned" goes to the assignee.
- **Tenants:** primary tenant is the default recipient; co-tenants and extra payer phones are opt-in per lease. A payer who isn't a tenant receives only the receipt for their own payment.
- **Mandatory types cannot be turned off:** security alerts and legally required notices. Tenants may opt out of marketing-style messages and reminders by channel, but their receipt is always available in-app.
- **Consent and opt-out** are logged. "STOP" replies are honoured per channel and organization.
- **Quiet hours** default 21:00–07:00 EAT; urgent types (security, maintenance emergency) override.
- **Language:** per recipient (English/Swahili), template chosen accordingly.
- **Channel order** default: WhatsApp (if opted in) → SMS → in-app; email for reports and documents. Cost is logged per message and charged to the organization's SMS wallet.
- **Digest vs instant:** overdue and cash summaries are batched for staff (daily/weekly) to avoid message floods.
- Owners see a **Notification settings** page: per type, enable/disable, channels, timing (e.g. reminder 3 days before due, again on due date, 2 days after), and who inside the organization receives it.
- Tenants see (in the portal or via SMS keywords) simple toggles per channel.
- Every send, skip and failure is recorded on `Message` with the reason.
