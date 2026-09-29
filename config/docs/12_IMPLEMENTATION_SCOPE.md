# 12 — What We Implement, and When

Answer to "which should we implement?": build the smallest set that carries one landlord from sign-up to a reconciled receipt, but **design the schema now** so nothing later forces a rewrite.

Legend: **BUILD** = code now · **DESIGN-IN** = put the column/relationship in the schema now, no feature yet · **LATER** = do not build yet.

## Tier 1 — BUILD in the MVP (Phases 1–4)

| Area | Models | Notes |
|---|---|---|
| Identity | User (phone/email), Organization, Membership | Multiple memberships per user |
| Access | Capability, RoleTemplate, Role, RoleCapability, MembershipCapability, PropertyAccess | Templates copied per organization; fully editable by each organization; per-person overrides (doc 13) |
| Audit | AuditEvent | From day one |
| Property | Property, Building (optional), Unit | Units 1,000+ ready; archive not delete |
| People | Tenant | Statuses Prospect/Active/Former |
| Leases | Lease, **LeaseTenant**, LeaseRentChange, LeaseCharge | Co-tenants and rent history from the start |
| Billing | ChargeType, Invoice, InvoiceLine, LedgerEntry | Idempotent generation job |
| Payments | Payment, PaymentAllocation, Receipt | Manual entry first; partial and over-payment |
| Payment accounts | PaymentAccount, PropertyPaymentAccount | Schema and admin UI now, since M-Pesa depends on it |
| Platform | Django admin for Platform Admin, seed data command | No custom console yet |
| Numbering | NumberSequence | Locked, per-organization, needed before invoices |
| Archive | `archived_at` fields, dual managers | On every archivable model (doc 11 §23) |

## Tier 2 — BUILD next (Phases 5–7)
| Area | What |
|---|---|
| M-Pesa | MpesaTransaction, C2B confirmation, STK push, matching engine, unallocated inbox |
| Communications | Message, MessageTemplate, SMS adapter, reminders, receipt delivery |
| Dashboards | Portfolio and per-property, arrears aging, per-role home pages |
| Search | Global search, indexes, filters, CSV import/export |
| Tenant portal | TenantAccount, invitation, tenant dashboard |

## Tier 3 — DESIGN-IN now, build later
| Feature | What to put in the schema now |
|---|---|
| Joint ownership / agency clients | Nullable `owner` on Property later; keep Property→Organization single FK |
| Metered utilities | `ChargeType` and invoice lines already generic |
| Late fees | Charge type LATE_FEE, lease `grace_days` |
| Multi-currency | `currency` column with default KES |
| Documents | Files linked via generic FK later; `visibility` concept agreed |
| Subscriptions | Separate app; `entitlements` service hook points in create-flows |
| API | Services/selectors layer, UUID public IDs |
| AI | Permission-aware tool layer, no direct DB access |
| Two-factor auth | User model unaffected; add later |
| Platform billing (flow A) | Separate `subscriptions` models: SubscriptionInvoice, PlatformReceipt, SmsWalletTopUp (doc 11 §22) |
| Expenses (flow C) | Expense, ExpenseCategory, Supplier designed now; built with maintenance |
| Notification preferences | NotificationPreference, OrganizationNotificationRule, ConsentRecord, built in Phase 5 (doc 11 §27) |
| Market rent | nullable `Unit.list_rent` for vacancy-loss reporting |

## Tier 4 — LATER (after the MVP is used by real landlords)
Maintenance, expenses and property net income, metered utilities, documents vault, accounting and MRI tax reports, WhatsApp and email channels, subscriptions and enforcement, platform console (custom admin), analytics, AI assistant, native mobile app, USSD.

## Deliberately NOT doing yet
- Property owned by several organizations.
- Tenant records shared across organizations.
- Aggregator model that holds tenants' money.
- Separate schema or database per tenant.

## Definition of "MVP complete"
A pilot landlord can: sign up → create an organization → add a property with units → add tenants and a lease → get automatic monthly invoices → record a payment (and later receive M-Pesa) → see it allocated → send a receipt and reminder → see collection and arrears per property. And Landlord A can provably never reach Landlord B's data.

---

## Additions from the competitor benchmark (D-039, D-040)

| Tier | Item | Notes |
|---|---|---|
| Tier 1 | Shareable vacancy link (public read-only unit page, WhatsApp share) | No marketplace |
| Tier 1 | P&L and cash flow by property; aged receivables | From flows B and C (doc 11 §22) |
| Tier 1 | `Payment.method = BANK`; CSV bank statement import into the matching engine | Bank APIs later |
| Tier 1 | Import concierge for pilots; CSV template with validation preview | Measure time to first invoice |
| Tier 1 | Optional MFA for Owner/Accountant; mandatory for Platform Admin | |
| Tier 1 | Public status page, security and data-protection page | |
| Tier 1 | `SubscriptionInvoice.vat_amount` | Confirm VAT and eTIMS for our company [VERIFY] |
| Tier 1 | `MpesaTransaction.msisdn_raw` and `msisdn_hash`; unmatched-payment SMS; inbox as a daily screen | Test in Daraja sandbox [VERIFY] |
| Tier 1 | `EtimsAdapter` interface (no implementation yet) | Accountant check first (D-039) |
| Tier 2 | Read-only Owner membership and monthly owner statement PDF | Before full agency mode |
| Tier 2 | Lease PDF generation and move-out statement | |
| Tier 2 | Prospect and Viewing (light CRM) | Leasing Agent workflow |
| Tier 2 | MaintenanceRequest: `assigned_supplier`, `due_by`, status timestamps, photo-first, urgency triage | |
| Tier 2 | Segmented announcements (arrears, lease expiry, building) | |
| Tier 2 | WhatsApp adapter; tenant portal; metered water readings by caretaker | Moved earlier from Phase 9 (D-039) |
| Tier 2 | Collectability score (A to E, rule-based) and daily "who to call" list | |
| Tier 2 | Accounting-friendly CSV export | |
| Tier 3 (design-in) | Unit type `BED_SPACE`, billing period `SEMESTER` | Hostels |
| Tier 3 (design-in) | `Property.category = ESTATE`, `COMMITTEE` role template, shared-cost split | Estates and HOAs |
| Tier 3 (design-in) | Branch and branding fields on Organization | Agencies, white-label |
| Tier 3 (design-in) | Double-entry chart of accounts | Doc 14 D1 |
| Tier 4 | E-signature, WhatsApp AI assistant, bank APIs, QuickBooks/Zoho adapters | |
| Skip | Short-stay/Airbnb, credit screening, public marketplace | |
