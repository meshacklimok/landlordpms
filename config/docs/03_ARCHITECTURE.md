# 03 — Architecture

## Principles
1. **Multi-tenant by default.** Every business row belongs to an Organization.
2. **Money is a ledger.** Append-only entries; corrections are new entries.
3. **Idempotent payments.** The same M-Pesa callback can arrive many times and only one payment results.
4. **Thin views, fat services.** Business rules live in `services.py`, so HTML views, the API, and background jobs share them.
5. **Background work is async.** Invoicing, SMS, and callbacks processing run in a task queue.

## Three separate money flows
Landlord → LandlordPMS (`subscriptions`), Tenant → Landlord (`billing`/`payments`/`mpesa`), Landlord → Supplier (`expenses`). They never share tables, number sequences or reports. Details in doc 11 §22.

## Money, numbering, history, reporting, notifications
Rules for decimal money and rounding (doc 11 §25), locked number sequences (§24), archive vs never-delete (§23), exact report metrics (§26) and notification preferences (§27) apply to every app.

## App boundaries (build in this order)

| App | Owns |
|-----|------|
| `accounts` | User, Organization, Membership (role), invites |
| `properties` | Property, Building (optional), Unit |
| `tenants` | Tenant (person), contacts |
| `leases` | Lease (tenant ↔ unit, rent, deposit, dates, status) |
| `billing` | Charge types, Invoices, InvoiceLines, ledger entries, arrears |
| `payments` | Payment, Allocation (payment → invoice), Receipt, Reversal |
| `mpesa` | Raw callbacks, STK requests, shortcode config, reconciliation |
| `notifications` | Templates, messages, delivery status, channels |
| Later | maintenance, expenses, accounting, utilities, documents, reports, analytics, subscriptions, audit, support, ai_assistant |

**Reduce apps for now:** empty apps are harmless, but do not add code to later apps until the MVP works. `audit` is the exception: start a simple audit log in Phase 1 (see below).

## Identity and access

```
User ──< Membership >── Organization
            │
            └─ role: OWNER | MANAGER | ACCOUNTANT | CARETAKER
                     (+ optional scope: specific properties)
```
- A user can belong to several organizations (an accountant serving many landlords).
- Optional `PropertyAccess` table scopes a Manager/Caretaker to particular properties.
- **The active organization comes from the session**, and every queryset is filtered by it.

### Tenant isolation pattern
```python
class OrgScopedModel(models.Model):
    organization = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT)
    objects = OrgScopedManager()   # .for_org(org) is mandatory in views
    class Meta:
        abstract = True
```
- Views use a mixin that resolves `request.organization` and rejects access if the user has no membership.
- **Every app gets a test proving org A cannot read or write org B's data.**

## Core data model (sketch)

```
Organization ─┬─ Property ─┬─ Building? ─┐
              │            └─────────────┴─ Unit ─── Lease ─── Tenant
              │
              ├─ Invoice ─── InvoiceLine
              ├─ LedgerEntry   (tenant/lease account: debit/credit)
              ├─ Payment ─── Allocation ─→ Invoice
              └─ MpesaTransaction (raw, unique on TransID)
```

### Ledger rules
- `LedgerEntry(lease, type, amount, direction, ref_type, ref_id, created_at)`.
- Invoice creation posts DEBITs, payment allocation posts CREDITs.
- Balance = sum(debits) − sum(credits). Never stored as the source of truth (a cached copy is allowed for speed).
- No `DELETE` on financial tables. Use `status=VOID/REVERSED` plus a reversing entry.
- Invoice generation is idempotent: unique constraint `(lease, period, charge_type)`.

### Payment rules
- `MpesaTransaction.trans_id` is `unique=True`. Insert inside `transaction.atomic()`; a duplicate is a no-op.
- Store the raw callback body untouched.
- Match to a tenant by account reference (unit code), then by phone. If ambiguous, mark **UNMATCHED** for manual assignment. Never guess.
- Overpayments create a credit balance; underpayments leave the invoice partially paid.

## M-Pesa flow (Option 1 — landlord's own shortcode)
```
Tenant pays → Safaricom → POST /mpesa/confirmation/<org_token>/
   → verify source + store raw → dedupe by TransID
   → enqueue reconcile task → match lease → Payment + Allocation + LedgerEntry
   → Receipt → notify tenant & landlord
```
- Callback URLs contain an unguessable per-organization token and are allow-listed where possible.
- Respond quickly (< a few seconds) with the expected acknowledgement; do the work in the background.
- Daraja credentials per organization are stored encrypted, never in plain text.

## Background jobs
Use **Celery + Redis** (or Django's built-in tasks when suitable). Jobs:
- monthly invoice generation
- reminder sending
- callback reconciliation
- report/PDF generation
- retry failed SMS

## Audit log (start early)
`AuditEvent(org, actor, action, object_type, object_id, before, after, ip, at)`. Written by services for: login, lease/invoice/payment changes, role changes, exports.

## Frontend
- Server-rendered Django templates + Bootstrap 5, mobile-first, lightweight HTMX for interactivity.
- Make it a **PWA** (installable, offline shell) for caretakers with poor connectivity.
- Charts: Chart.js.
- Keep pages light (data costs money for users).

## Project layout conventions
```
<app>/
  models.py  services.py  selectors.py  forms.py  views.py  urls.py  admin.py
  tests/  (test_models.py, test_services.py, test_views.py, test_permissions.py)
config/settings/  base.py  dev.py  prod.py   (split settings)
templates/<app>/…
```

## Configuration
- `.env` for secrets, `.env.example` committed.
- `DATABASE_URL`, `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`, `MPESA_*`, `SMS_*`, `SENTRY_DSN`.

## Scaling path
1. Single server + Postgres + Redis (first thousands of units).
2. Managed Postgres, read replicas for reports, object storage (S3-compatible) for files, CDN for static.
3. Horizontal web workers behind a load balancer; Celery worker pools per queue.
4. Partition/archive old ledger and message tables if they grow large.
