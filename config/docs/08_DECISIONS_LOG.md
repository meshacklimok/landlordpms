# 08 — Decisions Log

Format: **ID — Title — Status — Date**. Context, decision, consequences. Add new entries at the bottom. Never edit history; supersede with a new entry.

---

### D-001 — Project name is landlordpms — ACCEPTED — 2026-09-26
"RentFlow Kenya" was a working name and is retired. All docs and code use **landlordpms**.

### D-002 — Django with separate apps per domain — ACCEPTED (pre-existing)
19 apps scaffolded. Only the MVP apps get code first; the rest stay empty until needed.

### D-003 — Custom User model before first migration — PROPOSED
Login by phone or email. Set `AUTH_USER_MODEL` before running any migration; delete the existing scratch `db.sqlite3`.

### D-004 — Multi-tenancy: shared database, `organization` FK on every business table — PROPOSED
Scoped manager + view mixin + mandatory isolation tests. Rejected: schema-per-tenant (operational cost) and database-per-tenant.

### D-005 — Money as Decimal, KES, ledger-based balances — PROPOSED
No floats. Append-only ledger. No deletes on financial data; reversals instead.

### D-006 — PostgreSQL from now on — ACCEPTED (database created and migrated 2026-09-26)
Replaces "SQLite temporarily". Reason: concurrency, decimals, and payment idempotency behave differently in SQLite.

### D-007 — Time zone Africa/Nairobi — PROPOSED

### D-008 — M-Pesa collection strategy — OPEN
MVP: record-only/manual. Next: landlord's own Paybill/Till via Daraja C2B. Aggregator model deferred pending legal advice. See 01 §C.

### D-009 — Business logic in services.py — PROPOSED
Views/API/jobs call services; keeps rules testable and reusable.

### D-010 — Frontend: Django templates + Bootstrap 5 + HTMX, delivered as a PWA — PROPOSED
Mobile-first, low bandwidth. A REST API is added later for native apps.

### D-011 — Background jobs via Celery + Redis — PROPOSED

### D-012 — Start with residential rentals only — PROPOSED

### D-013 — Free tier of ~5 units — PROPOSED (validate in pilots)

### D-014 — User, Organization, Membership and Role are separate concepts — PROPOSED
No `is_landlord` flag. Membership carries the role and property scope. Platform Admin is `is_staff`/`is_superuser`, outside organizations.

### D-015 — Roles and capabilities live in the database, editable in admin — PROPOSED
Capabilities are defined in code; roles bundle them. System roles seeded; custom per-organization roles are designed for, built later.

### D-016 — Lease can have multiple tenants (LeaseTenant); tenants can have many leases — PROPOSED
No overlapping active leases on one unit. Transfer = end lease, new lease with `previous_lease`.

### D-017 — Property has exactly one managing organization — PROPOSED
Joint ownership and agency client owners are added later through an optional owner link, not by sharing properties.

### D-018 — Occupancy is derived from leases; rent history is records not edits — PROPOSED

### D-019 — Payment accounts are their own entity, many per organization, many-to-many with properties — PROPOSED
Units carry a `payment_reference`. M-Pesa transactions attach to a payment account, not the landlord. Unmatched payments go to an inbox, never guessed.

### D-020 — Billing (owed) is separate from payments (paid); ledger plus allocations — PROPOSED

### D-021 — Subscription billing (landlord pays us) is a separate app from tenant billing — PROPOSED

### D-022 — No models or views until docs 10–12 are approved — ACCEPTED

### D-023 — Roles are per-organization and fully editable; templates are copied at organization creation — ACCEPTED
Supersedes the "custom roles later" part of D-015. Each organization can rename, add, clone and edit roles. Owner role is protected and at least one Owner must exist. See doc 13.

### D-024 — Per-person capability overrides (MembershipCapability); no privilege escalation — ACCEPTED
You can only grant capabilities you hold yourself. All role and grant changes are audited.

### D-025 — Caretaker cash payments: allowed only if the Owner/Manager grants `payments.record`; `payments.confirm` decides whether it needs review — ACCEPTED
Default caretaker template does not include it. Resolves TODO decision 1.

### D-026 — Added default templates Leasing/Letting Agent and Maintenance Manager — ACCEPTED

### D-027 — Three separate money flows: platform billing, property financial records, property expenses — ACCEPTED
No shared tables, sequences or reports. Subscriptions never touch a landlord's tenant ledger; expenses never change tenant balances. Doc 11 §22.

### D-028 — Archive vs never-delete matrix — ACCEPTED
Financial and audit records are never deleted; business records are archived; only unissued drafts and temporary tokens are hard-deleted; personal data is anonymised after retention. Doc 11 §23.

### D-029 — Locked per-organization number sequences assigned at issue; tenant-facing reference `{PROPERTY_CODE}-{UNIT_CODE}` — ACCEPTED
Platform documents use their own `LPM-` sequences. Doc 11 §24.

### D-030 — Money: Decimal(14,2), KES, line-level ROUND_HALF_UP, single money helper, string serialisation — ACCEPTED
Doc 11 §25.

### D-031 — Report metrics are defined once in `reports/metrics.py` (definitions in doc 11 §26) — ACCEPTED

### D-032 — Notification delivery = organization rule AND recipient preference AND consent AND channel availability AND quiet hours; staff recipients chosen by capability — ACCEPTED
Doc 11 §27.

### D-033 — Group A recommendations A1–A13 accepted (doc 14) — ACCEPTED
Deposits are an isolated sub-ledger (DEPOSIT_RECEIVED/DEDUCTION/REFUNDED/TRANSFERRED, each with deposit_type, reason and optional proof, plus a Deposit Clearance Statement); opening balances plus CSV import; rent in advance with per-lease due day and grace; extra payer phones per lease and COMPANY tenant type; locked sequences; DB constraints and row locks; organization in session with UUID URLs; phone-first login with SMS OTP; lapse leads to read-only, never deletion; SMS wallet.

### D-034 — Tax is design-in only — ACCEPTED
Fields exist (`ChargeType.is_taxable`, nullable `tax_rate`, `Invoice.subtotal/tax_total/total`, `Organization.kra_pin/vat_registered`) but no tax calculation or eTIMS work until confirmed with an accountant.

### D-035 — Agency commission is design-in only — ACCEPTED
Nullable `Property.owner` and `Property.management_fee_percent`; owner statements and remittances come later.

### D-036 — Remaining Phase 0 decisions — ACCEPTED (recommended defaults)
- Agency/client-owner mode: design-in only, not in v1 (see D-035).
- Unit payment reference: `{PROPERTY_CODE}-{UNIT_CODE}`, e.g. `GV-A102` (doc 11 §24).
- Proration: by actual days, organization can switch to a full month (doc 14 A3).
- Grace days: per lease, default 0 to 3. Late fees are off in the MVP.
- Retention for former tenants: 90 days after offboarding for organizations; tenant personal data retention period **[VERIFY with counsel]** before launch.
- Phone-only login (no email) is allowed, with SMS OTP (doc 14 A11).
- Default payment allocation: oldest invoice first, manager override with audit.

### D-037 — MRI reporting and eTIMS are staged and adapter-based — ACCEPTED
Now: design-in fields only (D-034). Later: MRI estimate report using a configurable dated rate (currently 7.5% **[VERIFY]**), then an eTIMS adapter. No hard-coded tax rate; no integration until KRA requirements are confirmed.

### D-038 — Foundations D1–D15 in doc 14 Group D accepted — ACCEPTED
Key points: single-entry append-only ledger shaped for later double-entry; we never hold customer money and landlords use their own Paybill/Till/bank; "tenant" means renter and the customer is Organization; renters are per-organization records linked to a global User only by phone; application-level isolation now with row-level security considered later; effective dating on rent, charges, roles and account assignments; structured Kenyan addresses; legal review before pilots; organization owns and can export its data; RPO/RTO defined and restore tested before real money; business rules in services; per-organization settings stored as data with feature flags and entitlements; consented and logged impersonation with rate limits; unit economics before pricing.

### D-039 — Competitor benchmark findings adopted (doc 16) — ACCEPTED
1. eTIMS/eRITS moves from "later" to "confirm before launch": accountant check in Phase 0, `EtimsAdapter` interface designed in, and a go/no-go on eTIMS submission before public launch.
2. `MpesaTransaction` stores `msisdn_raw` and `msisdn_hash`; phone matching must work with hashed numbers **[VERIFY in Daraja sandbox]**.
3. The unallocated inbox is a daily first-class screen, plus an SMS asking the payer for their unit reference.
4. Add a free tier for tiny landlords and annual discounts; no hidden per-transaction fees.
5. Consider tenant portal, WhatsApp and metered water earlier than Phase 9.

### D-040 — Additions from competitor deep dive (doc 16 §7–9) — ACCEPTED
MVP: vacancy share link, P&L and cash flow by property, bank as a payment method with statement import, import concierge, optional MFA for Owner/Accountant, status and trust pages, VAT field on platform invoices. Tier 2: owner viewer and statements, lease PDF and move-out statement, prospects and viewings, vendor SLA, segmented announcements, WhatsApp, collectability score. Design-in only: BED_SPACE and semester billing, ESTATE and committee role, branch/white-label. Skip: short-stay, credit screening, public marketplace.

### D-041 — Phase 1 implementation choices — ACCEPTED (2026-09-26)
1. `PropertyAccess` ships in Phase 1, so a minimal `Property` (organization, name, code, category) ships with it. Phase 2 extends that model; it does not replace it.
2. Organization scoping is explicit: `Model.objects.for_org(org)`, which raises on `None`. There is no thread-local "current organization" and no automatic filtering.
3. `Organization.status` is ACTIVE, READ_ONLY (subscription lapsed: only capabilities marked `read_only_safe` work, and `subscription.manage` is one of them) or FROZEN (platform action: nothing works).
4. Capabilities are defined in code (`accounts/capabilities.py`) and synced after every migrate. Removed codenames are deactivated, never deleted. Existing role templates are not overwritten, except that the Owner template always gains new capabilities.
5. Org-wide capabilities (staff, roles, subscription, organization, payment accounts, full audit) also need `Membership.all_properties`. That is why property-scoped staff can never manage staff.
6. The database is PostgreSQL in every environment, including tests.

### D-042 — Phase 3 billing design — ACCEPTED (2026-09-27, open to change before merge)
1. **Signed ledger.** `LedgerEntry.amount` is signed: positive means the tenant owes more, negative is a credit, and the balance is the sum. Check constraints fix the sign per kind (INVOICE and PAYMENT_REVERSAL positive; INVOICE_VOID, PAYMENT and DEPOSIT_APPLIED negative; never zero). Rows are append-only in code; corrections are REVERSAL entries that point at what they cancel.
2. **Deposits are a separate table** (`DepositEntry`), not ledger kinds, so no query can mix them into rent. Held = sum per lease and deposit type. Deducting towards arrears writes a linked `DEPOSIT_APPLIED` credit to the rent ledger. Renewal and transfer activation move what is held as a linked pair of DEPOSIT_TRANSFERRED entries.
3. **Generated invoices are issued at once**: numbered INV-YYYY-NNNNNN (year of issue) and posted to the ledger. One invoice per lease per run holds rent and recurring charges.
4. **Idempotency lives on the line**: `(lease, billing_month, charge_type)` is unique among lines that are not void. A charge added mid-month is billed on a small extra invoice; voiding frees the month to be billed again. The lease row is locked while billing it.
5. **Proration**: one line per charge type per month. A mid-month rent change is summed exactly across its pieces and rounded once. With `prorate_partial_months` off, a partial month bills the full rate of the first billed day; a charge starting mid-month then starts next month.
6. **Timing**: the daily job bills the current month and, from `invoice_lead_days` (default 5) before it, the next month. Due date = the lease's due day in the billed month, but never before move-in or the issue date. `overdue_after` = due date + grace days, stored on the invoice.
7. **No automatic catch-up** of earlier months: those are covered by an opening balance (one live `OPENING_BALANCE` per lease, changed by reversal) or billed by hand for a chosen month.
8. **New capabilities**: `deposits.record` (Owner, Manager, Accountant) and `deposits.deduct` (sensitive; Owner, Manager). Opening balances need `invoices.adjust`; voiding needs `invoices.void` and no payments allocated.
9. **Invoices follow later lease changes.** Ending a lease, a rent change or moving a charge's end re-checks every month already billed from that day: a month whose lines no longer match is voided and billed again with its original due date. Invoices with payments are left for manual correction. A charge added from a month already billed is billed at once on its own invoice.

### D-043 — Phase 4 payments design — ACCEPTED (2026-09-27, open to change before merge)
1. **One ledger entry per payment, not per allocation.** A `Payment` gets one `LedgerEntry.PAYMENT` (posted on confirm) and, if reversed, one `PAYMENT_REVERSAL`, both linking back through a new nullable `LedgerEntry.payment` FK (same shape as the existing `invoice` FK). `PaymentAllocation` rows are bookkeeping for which invoices a payment covers (drives `Invoice.amount_paid`/status and the receipt's line list); they do not drive the lease balance, so an unallocated remainder is not a second ledger entry — it is simply a payment with `amount` greater than the sum of its allocations, swept into invoices later.
2. **Maker/checker by capability, not role** (doc 13): `payments.record` creates a `Payment`; if the actor also holds `payments.confirm` it is confirmed in the same call (allocated, posted, receipted). Otherwise it sits `PENDING_REVIEW`, posts nothing, and waits in a review queue for a `payments.confirm` holder to confirm or reject.
3. **Default allocation is oldest open invoice first.** `payments.allocate` can override with an explicit invoice/amount list at confirm time. A separate "apply credit" action, also `payments.allocate`, sweeps a payment's unallocated remainder into invoices that are open now — used after new invoices are issued.
4. **Reversal only from `CONFIRMED`** (`payments.reverse`, sensitive, reason required): unwinds each `PaymentAllocation` (`Invoice.amount_paid` and status roll back), writes one `PAYMENT_REVERSAL` for the full amount, sets `Payment.status = REVERSED`. Rejecting a still-`PENDING_REVIEW` payment (`payments.confirm`) just sets it to `REVERSED` directly, since nothing was posted yet.
5. **Receipts are numbered and rendered to PDF at confirm time** (`RCT-YYYY-NNNNNN`, ReportLab — pure Python, no system dependencies) and kept even if the payment is later reversed; nothing here is ever hard-deleted (D-028).
6. **`PaymentAccount` ships without `credentials`.** The M-Pesa columns are Phase 6's concern; this slice only needs `type`, `number`, `display_name` and the usual archive lifecycle.
7. **Scope for this slice**: `PaymentAccount`/`PropertyPaymentAccount`, `Payment`/`PaymentAllocation`, reversal, receipts. Bank-statement CSV import and the P&L/cash-flow/aged-receivables reports are deferred to a follow-up slice.

### D-044 — Phase 5 communications design — ACCEPTED (2026-09-28, open to change before merge)
1. **The notification type catalog lives in code** (`notifications/catalog.py`), like capabilities: codename, audience (tenant or staff), default channels, default on/off, mandatory, urgent, reminder offsets and the placeholders its templates may use. Rows store the codename; there is no catalog table.
2. **`OrganizationNotificationRule` holds only changes.** No row means the catalog default. A row stores enabled, channels in order, reminder offsets in days and whether co-tenants also receive it. Mandatory types cannot be switched off.
3. **`NotificationPreference` holds opt-outs** per recipient (tenant or staff user), per type (or all types) and per channel. **`ConsentRecord`** logs grants and revocations per recipient and channel. For service messages about a tenancy (invoices, reminders, receipts, property notices), consent to SMS is implied by the lease; an explicit revocation (a STOP reply, or staff recording it) blocks the channel for every non-mandatory type. WhatsApp needs an explicit grant. There are no marketing messages. **[VERIFY the implied-consent basis with counsel before pilots.]**
4. **`Message` is the log of every send, skip and failure** (doc 11 §27), never deleted. One row per recipient per event: the delivery engine tries the rule's channels in order and uses the first one allowed; if none is allowed, it writes one SKIPPED row with the reason. `dedupe_key` is unique per organization, so a trigger or a daily job that runs twice sends nothing twice.
5. **Delivery check** = rule enabled AND not opted out AND consent AND channel available AND address known. Quiet hours (organization setting, default 21:00–07:00 Africa/Nairobi) do not skip a message: non-urgent messages wait in QUEUED until quiet hours end.
6. **Sending runs after commit**, then again from `send_due_messages` (run every few minutes by cron) for delayed and failed messages. A failed send is retried up to 3 times with backoff, then marked FAILED. No Celery until the volume needs it.
7. **Templates**: default bodies are in code for each type, channel and language (English and Swahili, Swahili **[VERIFY with a native speaker]**). A `MessageTemplate` row is an organization's override, archivable. Bodies use `{placeholder}` fields only, filled by a safe formatter, never the Django template engine, so staff-edited text cannot run code. Unknown placeholders are refused when saving.
8. **Recipient language**: new `Tenant.language` (en or sw, default en). Staff get English until user settings exist.
9. **In-app** is a `Message` on the IN_APP channel to a staff user, with `read_at`, shown under a bell in the header. Tenants have no in-app channel until the tenant portal (Phase 7).
10. **SMS wallet**: `channels.sms_available(org)` returns True until platform billing (Phase 8) adds the wallet. Provider cost is still stored on each message.
11. **Build order**: (1) models, catalog, delivery engine, message log, dispatch; (2) triggers: invoice issued (not on rebill re-issues), rent due soon, rent overdue, payment received with a receipt link, and the daily reminders job; (3) pages: message log, notification settings, templates, the bell, and tenant opt-out; (4) Africa's Talking adapter, delivery reports and STOP replies; (5) segmented announcements; (6) WhatsApp adapter.
12. **Step 2 details** (2026-09-28): invoice issued fires from the monthly run (job or Generate button) only, never from `rebill_from` or `bill_missing`. Bulk triggers queue messages for `send_due_messages` instead of sending inline. Due-soon offsets count days before the due date; the nearest reached offset is sent, and none if the invoice was issued on or after that day. Overdue offsets count days after `overdue_after` (1 = first overdue day); a reminder missed by the job is sent up to 2 days late, then dropped. Reminders run inside `billing_daily`. Payment received goes to the payer first; the receipt link is a random `Receipt.share_token` served at `/r/<token>/` without login (rate-limited, noindex) and stops working when the payment is reversed. Absolute links use the new `SITE_URL` setting (required in production). Payment waiting for review is in-app to members who may confirm it for that property. SMS dates are numeric (dd/mm/yyyy) so they read the same in both languages; a balance in credit shows as zero.
13. **Step 3 details** (2026-09-28): pages live under `/messages/`. The log shows tenant messages only (staff in-app messages belong to the bell) and is scoped to the tenants the member can see; failed messages can be sent again with `messages.send`, which resets the attempts. Settings (`organization.manage`) edit on/off, reminder days (at most 3, 0–60, overdue from 1) and co-tenants per type, plus quiet hours; a rule equal to the default is deleted, not stored. Templates (`templates.manage`) archive the previous wording on every save, and saving the default text removes the override. Staff stop or resume SMS for a tenant from the tenant page (`tenants.manage`); this writes a STAFF `ConsentRecord`, and required messages still go. The nav bell counts unread in-app messages, and opening the inbox marks the shown ones read.
14. **Step 4 details** (2026-09-28): `core.sms.AfricasTalkingSmsSender` posts to the classic `version1/messaging` form API with `urllib` (no new dependency); the username `sandbox` switches to the sandbox URL. Recipient codes 100–102 count as sent and the cost is stored; codes 403, 404, 406 and 409 (invalid number, unsupported number, blacklisted, do-not-disturb) and HTTP 4xx fail at once without retries. Africa's Talking does not sign its callbacks, so the delivery-report, inbound-SMS and bulk opt-out callbacks live under a secret URL, `/hooks/sms/africastalking/<AT_CALLBACK_TOKEN>/<delivery|inbound|optout>/`, and return 404 while the token is unset. `Success` marks a sent message DELIVERED; `Failed`, `Rejected`, `AbsentSubscriber` and `Expired` mark it FAILED; in-flight statuses change nothing. A shared sender ID cannot tell organizations apart, so a STOP reply (STOP, UNSUBSCRIBE, ACHA, SIMAMA and similar) or a network opt-out revokes SMS for every tenant record with that phone, in every organization, and START (or ANZA) restores it; each change is a SMS_REPLY `ConsentRecord` and an audit event. **[VERIFY the API fields, status codes and callback fields in the Africa's Talking sandbox.]**
15. **Step 5 details** (2026-09-28): an `Announcement` row records the text, who sent it, a plain-language summary of the audience and the recipient count; each recipient gets one `announcement` `Message` linked to it (dedupe key per announcement and tenant, so a double submit sends nothing twice; the form also carries a one-time id). The audience is the tenants on leases occupying a unit today in the properties the member can see (`messages.send_bulk`), narrowed by any of: properties, buildings, owing money past its due date for at least N days (only offered with `invoices.view`), and lease contract ending within N days. The primary tenant always, co-tenants unless unticked; a tenant on two matching leases gets one message. The text may use `{tenant_name}`, `{unit}` and `{property}`, filled per recipient before the organization name is added. Sending is two steps: a preview shows who will receive it, who will be skipped and why (the same checks as sending), the SMS parts per message and an estimated cost (`SMS_PRICE_ESTIMATE`, KES per part, default 0.80, **[VERIFY against the Africa's Talking price list]**), then Send. Small batches (25 or fewer) send at once, larger ones through `send_due_messages`. Announcements wait for quiet hours unless "send now, even in quiet hours" is ticked. Members with every property see all announcements; scoped members see the ones they sent. Saved segments are deferred.
16. **Step 6 details** (2026-09-28): WhatsApp goes through the **Meta WhatsApp Cloud API** directly (`core.whatsapp.CloudApiWhatsAppSender`, `urllib`, Graph API version in `WA_API_VERSION`), from one platform business number shared by every organization, like the SMS sender ID; per-organization numbers are deferred. `WHATSAPP_BACKEND` unset means the channel is unavailable and tenants get SMS.
    - **Only approved templates.** Messages we start must use a template Meta approved, so the WhatsApp wording is fixed in code for each tenant type (English and Swahili) and organizations cannot edit it (the templates page shows it read-only). The approved template is named `<type>_v1` in `en` or `sw`; its parameters are the `{field}` values in the order they appear. `manage.py whatsapp_templates` prints the text to submit to Meta (category Utility, fields as `{{1}}`, `{{2}}`…). Parameter values are flattened to one line because Meta refuses new lines in parameters, and an empty value is sent as "-". Each message stores its template name, language and parameters, so a retry sends exactly the same. Each WhatsApp body is the SMS wording with "Message from {org_name}." first and "Reply STOP to stop WhatsApp messages." last, since Meta refuses templates that start or end with a field. The announcement template (free `{text}`) may be classed as marketing by Meta **[VERIFY at submission]**.
    - **WhatsApp first, then SMS.** Tenant types default to WhatsApp then SMS: a tenant who agreed to WhatsApp gets it, everyone else gets SMS as before. When nothing can be sent, the log shows the first reason that is not "channel not available", so a tenant who stopped SMS shows "No consent", not "WhatsApp not available".
    - **Consent is an explicit grant** (item 3): staff record it on the tenant page with a note of how the tenant agreed **[VERIFY what record counsel needs]**. A STOP-type WhatsApp reply revokes WhatsApp for every tenant with that phone in every organization, and START restores it only for tenants who had agreed before (a first grant never comes from a reply, because the shared number cannot tell which landlord the tenant means).
    - **Webhook** at `/hooks/whatsapp/`: GET answers Meta's verification with `WA_VERIFY_TOKEN`; POST must carry a valid `X-Hub-Signature-256` (HMAC-SHA256 of the raw body with `WA_APP_SECRET`) or gets 403. Status `delivered` or `read` marks a message DELIVERED, `failed` marks it FAILED with Meta's code and title, `sent` changes nothing. Meta does not report a price per message, so WhatsApp cost stays blank and the announcement estimate counts SMS only.
    - **Errors**: HTTP 429, 5xx and Meta's throttling codes (4, 80007, 130429, 131048, 131056) are retried; any other 4xx fails at once (for example 131026 not on WhatsApp, 132001 template missing, 190 expired token). **[VERIFY codes against the Cloud API error reference.]** Production refuses to start with the Cloud API backend unless `WA_PHONE_NUMBER_ID`, `WA_ACCESS_TOKEN`, `WA_APP_SECRET` and `WA_VERIFY_TOKEN` are set.
    - Deferred: per-organization numbers, WhatsApp prices in estimates, opt-in from a tenant's own message, free replies inside Meta's 24-hour window, and a conversation inbox.
