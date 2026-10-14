# 08 — Decisions Log

Format: **ID — Title — Status — Date**. Context, decision, consequences. Add new entries at the bottom. Never edit history; supersede with a new entry.

---

### D-001 — Project name is landlordpms — ACCEPTED — 2026-09-26
"RentFlow Kenya" was a working name and is retired. All docs and code use **landlordpms**. *Superseded by D-121: the product is now NyumbaPMS.*

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

### D-008 — M-Pesa collection strategy — ACCEPTED (own Paybill/Till via Daraja C2B, see D-045)
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

### D-045 — Phase 6 M-Pesa design — ACCEPTED (2026-09-28, open to change before merge)
1. **Each organization collects on its own Paybill or Till through its own Daraja app (C2B).** We never hold tenants' money. This settles D-008 for now; the aggregator model stays deferred pending legal advice.
2. **Credentials live in `mpesa.DarajaCredentials`**, one row per M-Pesa payment account (Paybill or Till): environment (sandbox or production), shortcode, consumer key, consumer secret and the STK passkey. The three secrets are encrypted at rest with `cryptography` Fernet (`core/crypto.py`, MultiFernet over `FIELD_ENCRYPTION_KEYS`, comma-separated, the first key encrypts, so keys can be rotated). They are never sent back to the browser: the settings form shows whether each is set, and a blank field keeps the stored value. Editing them needs `mpesa.settings`; the audit log records which fields changed, never their values. Production refuses to start without `FIELD_ENCRYPTION_KEYS`.
3. **Callbacks carry an unguessable per-account token** (`/hooks/c2b/<token>/confirm/`, `/validate/`, `/stk/`; Daraja refuses callback URLs containing words such as "mpesa", "safaricom", "exe", "cmd", "sql" or "query", so tokens that contain one are regenerated and a SITE_URL with one is refused at registration), generated when credentials are first saved and replaceable. An unknown token gets 404. Validation always accepts (Safaricom enables it only on request, so we cannot rely on rejecting a wrong reference at payment time) **[VERIFY]**. An optional `MPESA_ALLOWED_IPS` restricts callbacks to Safaricom's addresses **[VERIFY the current list]**.
4. **`MpesaTransaction` stores the raw payload first, then processes it.** `trans_id` is unique, so a repeated callback changes nothing and is still acknowledged. Amounts are parsed with `Decimal`. `TransTime` is read as Africa/Nairobi. A shortcode that does not match the account is stored as FLAGGED and never matched. Nothing is deleted: a transaction that is not rent is IGNORED with a reason.
5. **Phones may arrive hashed.** Daraja may send a SHA-256 hash in `MSISDN` instead of the number. We keep `msisdn_raw` as received and `msisdn_hash`: the value itself when it is 64 hex characters, otherwise the SHA-256 of the number as `2547XXXXXXXX` **[VERIFY the exact hash input in the sandbox]**. Phone matching hashes the tenants' and extra payers' (`LeasePayer`) numbers the same way. A payer can only be texted when the real number arrived.
6. **Matching, in order** (doc 11 §10):
    - **Reference.** The account reference, ignoring case, spaces and dashes, equals a unit's `payment_reference` in the account's organization; the unit has exactly one lease in force; and the account serves that unit's property (or serves no property in particular). This counts as **confident**: the payment is confirmed at once — posted, allocated oldest invoice first, receipted and the tenant notified — with no reviewer (Safaricom has already confirmed the money). Recorded as `payments.record_system_payment`, with `confirmed_by` empty and the source in the audit log.
    - **Phone.** The payer's number (or hash) belongs to a tenant or extra payer on exactly one lease in force in the organization. If several leases match, the one whose balance due equals the amount wins. This is only a **suggestion**.
    - **Otherwise** the transaction is UNMATCHED and waits in the inbox.
7. **The unallocated inbox is a daily screen** (`mpesa.match`, scoped to the properties the member can see; transactions on an account that serves no property need an organization-wide member). The actions are: accept the suggestion, match to any lease the member can see, or ignore with a reason. A match creates a confirmed payment exactly as in item 6. Reversing that payment later (`payments.reverse`) puts the transaction back in the inbox. Holders of `mpesa.match` get an in-app alert for each unmatched payment. When the real number is known, the payer gets one SMS saying the payment was received but not matched and asking for the unit reference.
8. **STK push** (Lipa na M-Pesa Online) on Paybill accounts that have a passkey: staff with `payments.record` send a request to a tenant's phone from the lease page. An `StkRequest` row tracks it. The STK callback creates the `MpesaTransaction` from `MpesaReceiptNumber` and matches it to that lease with confidence. A C2B confirmation for the same receipt is a duplicate and is ignored **[VERIFY whether Daraja sends both]**.
9. **Daraja client** (`mpesa/daraja.py`) is an adapter like SMS: OAuth token cached per account for less than its lifetime, register C2B URLs (response type `Completed`), STK push and STK query; `urllib`, no SDK. `MPESA_CLIENT` selects it; a fake client is used in tests.
10. **Daily job** `mpesa_daily`: retries transactions stuck in RECEIVED, queries STK requests still pending after 10 minutes, and writes a reconciliation summary per account for the day before (count and total received, auto-matched, matched by hand, ignored, still waiting). The summary goes to organization-wide `mpesa.view_transactions` holders in-app. Importing the M-Pesa statement CSV to catch missed callbacks (and Till accounts without Daraja) is deferred with the bank CSV import (D-043 item 7).
11. **Build order**: (1) encryption, credentials, Daraja client, the account's M-Pesa settings page and URL registration; (2) callbacks, `MpesaTransaction`, the matching engine and system confirmation; (3) the inbox, transaction list, manual match and ignore, alerts and the payer SMS; (4) STK push; (5) the daily job, the reconciliation summary and the go-live checklist.
12. **One M-Pesa code is counted once** (2026-09-29, from doc 16 Part 3). If a live payment (not reversed) in the organization already carries the transaction's code as its reference, typed in by hand, for example from the tenant's SMS, then the transaction is never paid automatically. It waits in the inbox as UNMATCHED, with the typed payment's lease as the suggestion and a note saying to check it and ignore this one. Staff get the in-app alert; the payer gets no "could not match" SMS, because they already have a receipt. Matching it by hand or accepting the suggestion is refused while the typed payment is live. After that payment is reversed, the transaction can be matched normally. The other way round: recording an M-Pesa payment by hand with a code that is waiting in the inbox (RECEIVED, UNMATCHED or FLAGGED) is refused, and "Record it anyway" does not override it; staff match it from the inbox instead.

### D-046 — Third competitor pass: what to build next — APPROVED (2026-09-29)
From doc 16 Part 3 (21 websites and apps). Item 12 of D-045 is already built. The owner approved the list as proposed on 2026-09-29.
1. **Next (after Phase 6 is merged):** a tenant **payment link**, a private link in rent SMS where the tenant starts an STK push for any amount (pay in parts, no wallet). Needs `StkRequest.requested_by` to allow empty (the tenant starts it), limits per link and phone, and an agreed rule for which phones may be used (any Kenyan number, or only the lease's tenants and payers).
   **Built 2026-09-29 (`mpesa/paylinks.py`):**
   - One `PayLink` per lease with a private token, `/p/<token>/`. It is made the first time a rent message needs it, and only when a Paybill with a passkey collects for the property. Staff with `payments.record` can make a new link (the old one stops at once), turn it off, or turn it on, from the lease's "Request M-Pesa payment" page.
   - **Phone rule (decided): any Kenyan number.** Relatives and employers often pay. The prompt still needs the PIN of whoever owns that phone, so the risk is nuisance prompts, not lost money.
   - **Limits:** 3 prompts per link in 10 minutes, 10 per link a day, 5 a day to one phone from any of the organization's links, 30 page loads or sends per IP an hour, plus the existing 2-minute wait per lease and phone.
   - `StkRequest.requested_by` may be empty; `StkRequest.pay_link` records the link. One of the two must be set (constraint).
   - The link works while the lease is ACTIVE, or ENDED/TERMINATED with money still owed; never for a draft, archived or renewed lease, or an organization that is not operational.
   - The page shows the organization, property, unit, amount owed and the Paybill with the account number as a fallback. It never shows tenant names or phones. It is `noindex` and sends no referrer.
   - `{pay_link}` is in the default SMS wording of invoice issued, rent due soon and rent overdue. It is empty when the lease has no working link, and the text reads as before. It is **not** in the WhatsApp wording, so the `_v1` templates for Meta are unchanged. The link adds about 45 characters and can push a message into a second SMS segment.
   - A payment for an ended lease follows D-045: it waits in the M-Pesa inbox for someone to match it.
2. **Move metered water forward** to right after Phase 7: caretaker readings with photo and timestamp, flags for negative or extreme usage, approval before billing, bill lines showing the readings, a minimum charge, and shared-meter splits (equal, weighted, flat). Prepaid meters are marked "not billed".
3. **Tier 2 additions:** move-in and move-out condition reports with photos and an item register per unit (feeding deposit deductions); a tenant good-standing letter (PDF from the ledger); quarterly and yearly billing; the annual rental income pack.
4. **Design-in now:** owner resident or non-resident flag for the tax estimate (7.5% or 10% after the Finance Act 2026 **[VERIFY]**); opt-in late fees with grace and cap (default off, **[VERIFY with counsel]**); a RESERVED hold on units; `Meter.kind` (postpaid or prepaid).
5. **Commercial:** every feature on every tier, a free tier up to 3 units, pilot prices locked for 12 months, and referral credit (doc 05).
6. **Skip:** wallets, escrow, payouts, interest on balances, rent discounting (they break D-038), multi-currency, short-stay and off-plan sales.

### D-047 — Move-in and move-out condition reports — ACCEPTED (2026-09-29, open to change before merge)
D-046 item 3, first part. Built on `feature/condition-reports`, cut from `feature/phase6-mpesa`.
1. **A new `inspections` app.** Each unit has an **item register** (`UnitItem`): area (e.g. Kitchen), item, quantity and a note, archivable. The first report on a unit with an empty register fills it from a default list in code, chosen by unit type (rooms and fittings for homes, doors, shutters and fittings for shops and offices). Staff with `inspections.record` add, rename and archive items on the unit's register page.
2. **One report per lease per kind** (`ConditionReport`, MOVE_IN or MOVE_OUT), unless the earlier one was cancelled. Reports are for issued leases only: a draft can be deleted, and the evidence must not go with it. Move-in needs an ACTIVE lease; move-out an ACTIVE, ENDED or TERMINATED one. A RENEWED lease gets neither, because the tenant stays.
3. **DRAFT → COMPLETED, or CANCELLED.** Starting a report copies the unit's register into its lines (area, item and quantity as they were that day). A draft is edited freely: each line gets a condition (Good, Fair, Poor, Damaged, Missing or Not checked) and a note; items found on the day are added to the register and the report. **Completing** needs every line assessed ("Not checked" is an answer; blank is not) and then locks the report: nothing changes after that. A mistaken report is cancelled with a reason and kept; a new one can then be started.
4. **Photos** belong to a line or to the report in general. The upload is checked and re-encoded by Pillow: EXIF orientation applied, then saved as JPEG at most 1600 px on the long side, which strips EXIF data such as the phone's location. At most 10 MB per upload, 10 photos per line and 60 per report. File names are random, under `condition-reports/<year>/`, and photos are only served through a view that checks the member may see the report, never from `/media/`. Each photo keeps who uploaded it and when. Photos can be removed while the report is a draft only.
5. **On the day**: who inspected it, the date (not in the future), whether a tenant was present, the tenant's comments, keys handed over (number), and general notes.
6. **Move-out compares with move-in.** The move-out report shows, per item, the condition and photos from the latest completed move-in report of this lease, or of the lease it renewed on the same unit, and marks items that are worse (Good < Fair < Poor < Damaged < Missing; "Not checked" is not compared).
7. **Deposit deductions cite the report.** A completed move-out report offers "Deduct from deposit" to members with `deposits.deduct`, with the reason filled from the items that are worse. It runs the normal `deposits.deduct` (same checks and ledger entries) and stores the report on the `DepositEntry`, so the clearance statement links to the evidence.
8. **Capabilities**: `inspections.view` (see reports, the register and photos) and `inspections.record` (start, fill, complete and cancel reports, and edit the register). Both are scoped by property. They are granted by migration to Owner, Manager and Caretaker roles; Leasing Agent gets both; Viewer and Maintenance Manager get view.
9. **Pages**: a "Condition reports" card on the lease page (start or open each kind), the reports and a register link on the unit page, the report page at `/inspections/<report>/` (a form while a draft, read-only and printable once completed), and the register at `/inspections/units/<unit>/items/`. Everything is audited: started, completed, cancelled, photo added or removed, register changes.
10. **Deferred**: the tenant confirming the report in the portal (Phase 7), meter readings (with metered water), a PDF of the report (print from the browser meanwhile), inspections during the tenancy, and AI summaries.

### D-048 — Tenancy and payment record letter — ACCEPTED (2026-09-29, open to change before merge)
D-046 item 3, second part. Built on `feature/condition-reports`, in a new `letters` app. The owner chose: all four facts, each switchable in the organization's settings; a new capability; issue whatever the record shows; numbered, stored and checkable.
1. **A letter states facts, never a judgment.** Titled "Tenancy and payment record", it can be issued whatever the record shows. Before issuing, staff see what it will say and a warning if it shows late or unpaid invoices or money owed.
2. **Always stated**: the tenants on the latest lease, each lease of the tenancy (property, unit, dates) and whether the tenant is current or when the tenancy ended. A tenancy is a lease and the leases it renewed or moved from (`previous_lease`); a letter asked for on any of them covers all of them and is filed on the latest issued one. A draft lease gets no letter.
3. **Chosen by the organization** (`Organization.letter_show_*`, all on by default, changed by `organization.manage` on `/letters/settings/`, audited; letters already issued do not change):
    - **Payment record**: invoices that fell due (issued, not void, above zero, grace period over), split into paid on time, paid late and not yet paid in full. On time means confirmed payments dated on or before the invoice's `overdue_after` (due date plus the lease's grace days) cover it. Reversed payments do not count.
    - **Balance**: the ledger balance over the tenancy that day (owed, in credit, or nothing owed).
    - **Deposit**: the amount held while the tenancy runs, or once it has ended, what was refunded and deducted (net of corrections; transfers inside the tenancy cancel out). Never the reasons for deductions.
    - **Rent**: the rent now, or at the end of the tenancy.
4. **Numbered, stored and frozen.** `TenancyLetter`: LTR-YYYY-NNNNNN per organization, the facts frozen as JSON, an A4 PDF drawn once with ReportLab and served only through a permission-checked view, who issued it and when. Later payments do not change a letter.
5. **Checkable.** Each letter prints a private link `/l/<code>/` (random 16-byte code). Without a login it shows the issuing organization, the number, the date and the same facts in the same words, so a copy can be compared. It is `noindex`, sends no referrer and is limited to 30 views per IP a minute. An unknown code gets 404.
6. **Withdrawn, never deleted.** A letter issued in error is withdrawn with a reason; the check page then says it was withdrawn (not why).
7. **Capability** `tenants.issue_letter` (issue, download and withdraw), scoped by property, granted by migration to Owner and Manager.
8. **Pages**: a "Tenancy letter" card on the lease page, and `/letters/lease/<lease>/` with a preview of what a letter issued today says, the issue button, and the letters of the tenancy. Issuing and withdrawing are audited.
9. **Deferred**: sending the letter to the tenant by SMS or email, a tenant asking for one in the portal (Phase 7), the organization's logo on the letter (with the branding settings page), and letters in Swahili.

### D-049 — Quarterly and yearly billing — ACCEPTED (2026-09-29, open to change before merge)
D-046 item 3, third part. Built on `feature/billing-frequency`, cut from `feature/condition-reports`. The owner chose: periods from the lease's start month, rent entered per month, charges follow the lease.
1. **`Lease.billing_frequency`** offers Monthly (default), Quarterly and Yearly. It is set on the lease form while the lease is a draft and carried over by a renewal or transfer. To change it on an active lease, renew the lease.
2. **Periods count from the lease's start month**: a quarterly lease starting on 15 March bills March–May, June–August and so on, and a yearly one March to February. A partial first or last month is prorated inside its period, as for monthly leases.
3. **Rent and charges stay monthly amounts.** An invoice has one line per month per charge ("Rent April 2026"), so idempotency, proration, rebilling and every monthly figure (rent roll, arrears, letters, reports) work unchanged. The lease page shows the rent per invoice.
4. **Recurring charges follow the lease**: water, garbage and service charge go on the same period invoice. `LeaseCharge.frequency` stays Monthly and is not used.
5. **One invoice per period**, issued `invoice_lead_days` before the period starts (the daily job bills the period that holds this month and, within the lead days, next month), due on the lease's due day in the period's first month, never before the move-in or the issue date. Billing any month of a period, by the job or on "Bill a month", bills the whole period. A lease activated in the middle of a period is billed for the whole of it.
6. **Changes after billing**: when an end date, rent change or charge change makes a period invoice wrong, the whole invoice is voided (if nothing was paid on it) and the period billed again with the original due date. A charge added later is billed on one extra invoice per period. An invoice with a payment is still left for correction by hand, as for monthly leases; this matters more for quarterly and yearly leases, and a credit note for the unused months is the natural follow-up.
7. Invoices show their period as "Jan – Mar 2026" in lists and payment screens.

### D-050 — Annual rental income pack and the landlord's tax residence — ACCEPTED (2026-09-29, open to change before merge)
D-046 items 3 and 4, and the MRI report staged by D-037. Built on `feature/billing-frequency` after D-049. The owner chose: the tax residence lives on the organization.
1. **`Organization.landlord_tax_residence`**: Resident in Kenya (default) or Non-resident. Only people with `organization.manage` change it, from the income page, and each change is audited. One flag per organization; agency mode will need it per property owner (D-040).
2. **The pack** (`/reports/income/`, `reports.view_financial`) covers one calendar year and only the properties the member can see: a summary, a table by month, totals by property and the confirmed payments. Exports need `reports.export`: CSV by month, by property and by payment, and an A4 landscape PDF drawn on request (nothing stored).
3. **Billed** is live invoice lines by the month they bill, so a quarterly invoice counts in its three months (D-049). Deposits are left out.
4. **Received** is confirmed payments dated in the year; a reversed payment is left out whenever it was reversed. Each allocation is split across its invoice's lines by their share, so paying an invoice of rent and water counts partly as rent. Money not yet applied to an invoice is counted as rent and shown on its own line. Deposits received are shown but never taxed.
5. **The tax estimate** is each month's taxable rent (rent received plus unapplied money) times the rate in force that month, rounded per month. Rates are dated in code (`reports/income.py`) **[VERIFY with KRA or a tax adviser]**: resident 10% from 2016 and 7.5% from 1 January 2024; non-resident 10% final tax from 1 July 2026 (Finance Act 2026), with no rate before that, so no estimate is shown for those months. Other charges (water, service charge) are not taxed in the estimate **[VERIFY]**.
6. **Notes on every pack**: the year is not over; only your properties are included; unapplied money counted as rent; months with no rate; a resident's yearly rent outside KES 288,000–15,000,000, the band the monthly regime covers **[VERIFY]** (the low end is only checked once the year is over); expenses are not tracked yet, so figures are gross; "Estimate only. Confirm with your tax adviser before filing."
7. Not in this step: expenses and net income, a withholding statement per owner for agencies, eTIMS or eRITS submission, and filing.

### D-051 — Phase 7 analytics: chart library, live figures, first report set — ACCEPTED (2026-09-29, open to change before merge)
Answers doc 15 §8. The owner said to use the recommended defaults. Built on `feature/phase7-dashboards`, cut from `feature/billing-frequency`.
1. **Chart library: Chart.js 4**, loaded from jsDelivr like Bootstrap, only on pages that draw a chart. No build step. Every chart has a table beside it (for screen readers and as a fallback when JavaScript is off), and the figures behind it export as CSV to people with `reports.export`.
2. **Live figures first, no snapshot table yet.** Monthly trends are grouped queries over invoice lines, allocations and payments, so they come straight from the source. `DailySnapshot` waits until the 10,000-unit performance test (doc 15 §7) shows it is needed, or until arrears over time are wanted (the only trend the source cannot rebuild). This departs from doc 15's recommendation of snapshots from Phase 7.
3. **Trends are "as recorded now"**: a late payment or a reversal changes the past months. The page says so. No "as at date" view yet.
4. **First report set**: a dashboard with five headline numbers (occupancy, rent expected, rent collected, collection rate, arrears), then cash received, a 12-month rent trend, arrears aging and occupancy by property. Next come the who-to-call list with collectability grades, then the monthly owner statement PDF. Income against expenses waits for the expenses app (flow C).
5. **One place for definitions**: `reports/metrics.py`, following doc 11 §26.
   - *Rentable* units are live units that are not Inactive, on live properties.
   - *Occupied* units are rentable units with an occupying lease on the day. The day is today for the current month and the month's last day for a past month.
   - *Expected rent* is live rent lines on issued, part-paid or paid invoices, by the month each line bills. A quarterly invoice counts in its three months (D-049).
   - *Collected for the month* is each confirmed payment's allocation, split across its invoice's live lines by their share (as in D-050), keeping the rent lines of that month. It is rounded per invoice and month.
   - *Collection rate* is collected ÷ expected, with no rate when nothing was billed ("no rent billed yet", not 0%).
   - *Cash received* is confirmed payments dated in the month, including advances and arrears clearing.
   - *Arrears* is what is overdue today, from the existing FIFO aging (billing.selectors), in buckets 1–30, 31–60, 61–90 and 90+ days (counted from due date plus grace since D-054).
   - *Unmatched M-Pesa* is the count and amount waiting in the inbox the member can see.
6. **Scope**: the page needs `dashboard.view_financial` and counts only the properties the member can see. A scoped manager never sees organization totals. Filters are one property and a month, and they are kept in the URL. Counts sit beside rates ("8 of 10 units").
7. **Drill-down**: occupancy opens the units list, cash received opens the payments list for that month and property, and arrears opens the arrears page.
8. Not in this step: caching, `DailySnapshot`, per-role dashboards, and the Swahili labels (strings are wrapped, but there is no translation yet).

### D-052 — Collectability grade and the daily who-to-call list — ACCEPTED (2026-09-29, open to change before merge)
Phase 7 step 2 (D-051 item 4; doc 16 Tier 2 item 7). The rules are rule-based and explainable; there is no machine learning. Built on `feature/phase7-dashboards`.
1. **Tenancy, not lease**: a grade covers a lease and the leases it renewed or moved from (`previous_lease`), so a renewal keeps its history.
2. **Days late per invoice**: this is how long after `overdue_after` (due date plus grace) the invoice was paid in full, using the dates of its confirmed allocations. It is 0 when paid in time, which is the same "on time" the tenancy letter uses (D-048). An invoice still unpaid counts its days late up to today. Opening balances have no invoice and are not graded.
3. **Window**: the tenancy's last 6 invoices whose `overdue_after` has passed, within 12 months. A tenancy with fewer than 2 such invoices gets no grade and shows as "New".
4. **Grade from the average days late**:
   - **A**: every invoice was paid in time.
   - **B**: 5 days or fewer.
   - **C**: 15 days or fewer.
   - **D**: 30 days or fewer.
   - **E**: more than 30 days.

   An invoice unpaid more than 30 days past `overdue_after` caps the grade at D, and more than 60 days caps it at E. The page shows the average and the count ("4 of 6 paid in time"), so a grade can be checked.
5. **Follow-ups** (`billing.FollowUp`, append-only) record an outcome: spoke to them, no answer, promised to pay, disputes the amount, or other. Each has a short note, and a promise has a date and an optional amount. Recording one needs the new capability `arrears.follow_up`, granted to Owner, Manager and Accountant by accounts 0010. Only the latest follow-up counts.
6. **Promise state**: a promise is *pending* until its date. It is *kept* when confirmed payments dated from the day it was made to its date add up to the promised amount (or any payment, when no amount was given). Otherwise it is *broken* once its date passes.
7. **The list** (`/billing/call-list/`, `invoices.view`) covers leases with money overdue today, in the member's property scope. It has three sections:
   - *To call*: broken promises first, then the largest amount overdue.
   - *Promised*: pending promises, by date.
   - *Done today*: leases followed up today.

   It can be filtered by property and grade. Phone numbers show to people with `tenants.view`, as `tel:` links.
8. Not in this step: reminder sequences, the grade in the tenancy letter or visible to tenants, and grade history.

### D-053 — Property owners and the monthly owner statement — ACCEPTED (2026-09-29, open to change before merge)
Phase 7 step 3 (D-051 item 4; doc 12 Tier 2; doc 16 Tier 2 item 8). It builds the D-035 design-in and the statement from doc 14 A9. Built on `feature/phase7-dashboards`. Recommended defaults, taken because the user said "next".
1. **`PropertyOwner`** (properties app): the person or company who owns one or more properties. It has a name, and an optional phone, email and note. It is not a login. `Property.owner` (nullable) and `Property.management_fee_percent` (nullable, 0 to 100) are set on the property form. Owners are added and edited at `/properties/owners/` by people with `properties.manage`. Every change is audited. No deleting.
2. **The statement** (`/reports/owner-statement/`, `reports.view_financial`) covers one owner and one calendar month. It includes only that owner's properties the member can see, archived ones included. Properties with no owner set make up their own statement, addressed to the organization, so a self-managing landlord gets one too. It defaults to last month. The A4 PDF needs `reports.export`. It is drawn on request from the live figures; nothing is stored or numbered yet.
3. **Figures**, per unit and per property:
   - **Billed**: live invoice lines by the month they bill (D-049), without deposits.
   - **Collected**: confirmed payments dated in the month (cash basis, because it is what can be paid over). Each payment is split across its invoices as in D-050: rent, other charges and deposit. Money not yet applied to an invoice counts as rent.
   - **Balance at month end**: the lease's ledger up to the last day of the month.
   - **Occupied**: units at month end, for live properties.
4. **Management fee**: the property's percent times the rent collected (rent plus unapplied money), rounded per property. Water, service charge and other charges carry no fee. No percent set means no fee.
5. **Due to the owner** = rent and other charges collected − the management fee − expenses. Expenses show as "not tracked yet" until the expenses app (flow C). Deposits received are listed, but they are held and not paid over.
6. **Owner login**: invite the owner with the existing Viewer role, limited to their properties. It is read-only and has no tenant names, because Viewer lacks `tenants.view`. Tenant names show on the statement only for members with `tenants.view`. There is no new role or capability.
7. **Not in this step**: remittance records (money paid over to the owner), stored numbered statements, sending the statement by email or SMS, expenses, and a tax residence for each owner (D-050 item 1).

### D-054 — Arrears aged from due date plus grace; global search with suggestions — ACCEPTED (2026-09-29, open to change before merge)
Phase 7 step 4 (doc 11 §17 and §26; doc 12 "Global search"). Built on `feature/phase7-dashboards`. The user said: "do that professionally, add also autocomplete".
1. **Aging basis corrected.** Doc 11 §26 defines arrears as what is outstanding past the due date plus grace. The FIFO aging in `billing.selectors` counted from the due date. Each unpaid amount now carries `late_from`: `Invoice.overdue_after` for an invoice, and the entry date for any other debit (opening balance, deposit deduction). The buckets, `LeaseArrears.days_overdue`, the arrears list order, the call list's overdue amount and days (D-052), the dashboard arrears figure (D-051) and the announcement filter by days overdue all follow it. Money within its grace days now shows as current. `oldest_due` still reports the due date itself.
2. **One search box** in the top bar for every member, plus a results page at `/search/?q=`. It searches:
   - **tenants**: name, contact person, phone or other phone (full number in any format, or 4 or more digits of it), and ID number when the member has `tenants.view_sensitive`;
   - **properties**: name, or the exact code;
   - **units**: code, or the exact payment reference;
   - **leases**: number, exact unit code, or the tenant's name when the member has `tenants.view`;
   - **invoices**: number;
   - **payments**: reference or receipt number;
   - **M-Pesa transactions**: code (exact or leading), account reference, payer phone.
3. **The same rules as the list pages.** A group appears only with that list's view capability (`tenants.view`, `properties.view`, `units.view`, `leases.view`, `invoices.view`, `payments.view`, `mpesa.view_transactions`). It is built from the list's own visibility helper, so a scoped member sees only their properties and nothing crosses organizations. Tenant names are left out of lease results without `tenants.view`. Archived tenants, properties, units and leases are not searched; invoices and payments are, as on their lists.
4. **Ranking**: exact match, then leading match, then anything else, then by name or newest first. Queries shorter than 2 characters return nothing. Queries are trimmed, whitespace collapsed and capped at 60 characters.
5. **Suggestions** (`/search/suggest/?q=`, JSON, `Cache-Control: private, no-store`) return the first 5 per group, with a flag when there are more and a link to that group's own list filtered by the same words. The results page shows 20 per group. The script (`static/js/search.js`, no library):
   - waits 180 ms after typing;
   - cancels a request that a newer one replaces, and caches answers for the page;
   - ignores an answer to a query that is no longer in the box;
   - follows the ARIA combobox pattern (arrow keys, Enter opens, Escape closes, `/` focuses the box), announces the count to screen readers, and highlights the match using text nodes only.
   The box is a plain GET form, so search works without JavaScript.
6. **Indexes**: every query is first limited to one organization, which is indexed. Matching inside a value (`icontains`) scans that organization's rows, which is fine at the sizes in doc 11 §27. Postgres trigram indexes (`pg_trgm`) wait until the performance test shows a need. No migration.
7. **Not in this step**: searching notes, messages, letters or inspections; spelling tolerance; recent searches.

### D-055 — Tenant portal: invitation, account and read-only tenant pages — ACCEPTED (2026-09-29, open to change before merge)
Phase 7 step 5 (doc 11 §6 and §18; doc 12 "Tenant portal"; D-039 moved it earlier). Built on `feature/phase7-dashboards`. Recommended defaults, taken because the user said "next".
1. **Same login as staff.** A tenant signs up or logs in with the phone number and a password, and verifies the phone by SMS code, exactly as staff do. No new way to log in, so no new attack surface. One `User` can be staff in one organization and a tenant in another, or both in the same one.
2. **`TenantAccount`** (new `portal` app) links a `User` to one `Tenant` record. A tenant has at most one live account; revoking keeps the row, and a later invitation makes a new one. One user may hold accounts in several organizations and sees them all together.
3. **Invitation** (`PortalInvitation`, `tenants.invite_portal`, already granted to Owner and Manager):
   - Staff invite from the tenant's page.
   - The SMS carries a link with a random token; only its SHA-256 hash is stored. The link lasts 7 days, and a new invitation replaces any pending one.
   - Accepting needs a logged-in user whose **verified phone equals the invited phone and the tenant's current phone**. That is the claim rule of doc 11 §6.
   - Staff can revoke a pending invitation or a live account; both are audited.
   - No invitation is sent when the tenant turned SMS off (D-044), or when they have no lease past draft (there would be nothing to show).
4. **What a tenant sees** (doc 11 §18), read-only:
   - each of their leases that is not a draft (unit, property, rent, dates, status);
   - the balance and the next amount due with its date;
   - open invoices;
   - a 12-month statement with a running balance;
   - confirmed payments, with their receipt PDFs;
   - the "Pay with M-Pesa" link when the lease has one (D-046).
   Co-tenants on a joint lease each see that lease. Staff names, notes, audit data and other tenants on the property are never shown.
5. **Isolation.** Every portal query starts from the user's own lease IDs: live accounts, of tenants not archived, in organizations not archived. Anything else is a 404. Leakage tests cover:
   - another tenant in the same organization;
   - another organization;
   - a revoked account;
   - an archived tenant;
   - draft leases;
   - receipts of reversed payments;
   - staff pages opened by a tenant.
6. **Routing.** A logged-in user with no staff membership but a live tenant account goes to `/my/` instead of the "create your workspace" page. A user with both sees "My home" in the top bar. Tenant-only users get a small top bar without staff links.
7. **Not in this step**: maintenance requests, notices and shared documents in the portal; in-app messages to tenants; paying inside the portal other than through the existing pay link; Swahili; per-role staff dashboards (still open in TODO).

### D-056 — Per-role home pages — ACCEPTED (2026-09-29, open to change before merge)
Phase 7 step 6, the last open item (doc 11 §17; doc 12 "per-role home pages"; D-051 item 8). Built on `feature/phase7-dashboards`. The user said "do it".
1. **By capability, not by role.** Roles are data (doc 13), so the home page (`/`) has no per-role templates. Each part appears only when the member holds the capability of the page it links to, so a link never ends on "not allowed". The default templates therefore get different home pages, and a renamed or custom role gets the right one without code. The logic is in `reports/home.py`.
2. **"Today" comes first**: counts of work waiting, each linking to the page that deals with it. An item with nothing waiting is hidden. When nothing is waiting, the page says "Nothing is waiting for you."
   - *Payments to confirm*: `payments.confirm`; opens the review queue.
   - *M-Pesa payments to match*: `mpesa.match`; opens the inbox.
   - *Tenants to call*, with broken promises noted, and *promises to pay due today*: `invoices.view` and `arrears.follow_up`; opens the call list (D-052). A Viewer can open the list but is not given calls to make.
   - *Draft leases to check and activate*: `leases.activate`.
   - *Units available to let*: `units.view` and `units.list_vacant`, shown to those who can let them (`leases.draft` or `prospects.manage`).
   - *Leases ending within 60 days* (the existing "expiring" rule), the soonest 5: `leases.view`. The tenant's name shows only with `tenants.view`.
   - *Condition reports to finish* (drafts), the oldest 5: `inspections.record`.
3. **Figures.** With `dashboard.view_financial`, the page shows the existing collections card (D-051). With only `dashboard.view_summary`, which every default template holds, it shows counts and no amounts or names: units occupied out of rentable, vacant (with how many are reserved or under repair), and how many leases have rent overdue. This is doc 10's "occupancy and arrears count only" for a caretaker. The overdue count uses the same aging as the arrears list, without `invoices.view`.
4. **Scope.** Every count is built from its list page's own visibility helper, so it covers only the member's properties and matches what that page lists.
5. **Tests**: a table for the eight default templates, a scoped manager, promises (due today and broken), and a caretaker's page showing no money, names or links they cannot open.
6. **Not in this step**: maintenance jobs (no maintenance app yet), rearranging or hiding cards per person, caching, and notifications for the same items.

### D-057 — Metered water — ACCEPTED (2026-09-29, open to change before merge)
D-046 item 2, moved to right after Phase 7. Built on `feature/metered-water`, cut from `feature/phase7-dashboards`. The user said to go ahead, with one change: **a photo of the meter is optional, never required.**
1. **Meters** (`meters.Meter`) belong to a property: a label (e.g. "A1 water" or the serial), `kind` Postpaid or Prepaid, the units it serves, the rate per m³ and an optional minimum charge. A meter serving one unit is that unit's own meter; one serving several is shared, and splits its bill **equally** or **by weight** (`MeterUnit.weight`). A flat water fee is not a meter: it stays a recurring Water charge on the lease. **Prepaid meters** can be read but are never billed ("not billed" on every page).
2. **Readings** (`MeterReading`): the date, the value in m³ (three decimals), an optional photo, an optional note, who recorded it and when. The first reading of a meter, and a reading marked "meter replaced", is a **baseline**: it starts the count and bills nothing. A reading must be dated after the meter's latest live reading and not in the future.
3. **Flags**, worked out when the reading is recorded:
   - *Lower than last time*: cannot be approved. Reject it, or record it again as "meter replaced".
   - *No water used* while a served unit was let.
   - *Much higher than usual*: more than 3 times the daily average of the last three approved periods, and at least 5 m³ over it.
   - *Long gap*: more than 45 days since the last reading.
   Approving a flagged reading needs a note saying why.
4. **Approval before billing.** Readings wait as Submitted until someone with `meters.approve` approves or rejects them (a reason is required to reject). The approver takes the rate in force then, and it is stored on the reading. A member who holds both capabilities may approve their own reading, since small landlords work alone; the audit log shows who did both.
5. **What each lease pays.** Usage × rate for the meter. A shared meter splits that by weight across **all** the units it serves; the minimum charge applies to each unit's share. Each unit's share is divided between the leases that covered it by days, over the days from the day after the last reading to the reading date. Days with no lease are not billed: the landlord carries a vacant unit's water. Each lease's amount is rounded once (`MeterCharge`).
6. **Billing.** A charge is billed in the month after the reading (a reading on 30 September goes on October's invoice). For a lease that has ended, it is billed in the lease's last month instead. It is a separate line on the invoice showing the readings, e.g. "Water A1: 1,234.000 → 1,250.500 m³ = 16.500 m³ × 150.00 (1 Sep – 30 Sep)". If that period is already invoiced, a small extra invoice holds the water lines. Approval bills it at once when its month is already being billed; otherwise the daily job picks it up with the rent. A line points at its `MeterCharge`, so each charge is billed once (a constraint), and voiding the invoice frees it to be billed again.
7. **Undoing an approval** is allowed only while none of its charges is on a live invoice (void the invoice first). The reading goes back to Submitted and its charges are cancelled.
8. **Capabilities**: `meters.view` (Owner, Manager, Accountant, Caretaker, Viewer), `meters.record` (Owner, Manager, Caretaker), `meters.approve` (Owner, Manager, Accountant) and `meters.manage` to add meters and set rates (Owner, Manager). They are granted to existing roles by a data migration, as with 0010.
9. **Pages** (`/meters/`): the meters of each property, with their last reading; a phone-friendly **reading round** for a property (one box per meter, the last value shown, an optional photo each); the approval queue with flags and bulk approval of unflagged readings; a meter page with its history and charges. The home page gets a "meter readings to approve" item.
10. **Not in this step**: electricity meters, tenant-submitted readings, readings in the portal (the invoice line already shows them), SMS about high usage, tiered (block) tariffs, and a standing charge.

### D-058 — Owner remittances and sending the owner statement — ACCEPTED (2026-09-29, open to change before merge)
D-053 item 7, taken right after metered water. Built on `feature/owner-remittances`, cut from `feature/metered-water`. The user asked for this step before Phase 8.
1. **Remittances** (`reports.OwnerRemittance`) record money paid out to a property owner. Each has:
   - the owner and the statement month it pays (the first of the month);
   - the amount (above zero) and the date paid (not in the future);
   - the method (M-Pesa, bank, cash, cheque, other), a reference and a note;
   - who recorded it and when.
   A mistake is **voided with a reason**, never deleted. Recording and voiding are audited. The app only records the payout: it does not send the money.
2. **On the statement.** When the statement is for a real owner, it adds "Paid to the owner" (the live remittances for that month) and "Still to pay" (due − paid), and lists the remittances. The PDF shows both figures.
   - This needs the member to see **every** property of that owner. Otherwise the due figure is partial, so remittances are hidden and a note says why.
   - "No owner set" (the organization's own properties) has no remittances.
3. **Owner account page** (`/reports/owners/<id>/`). It shows, for the last 12 months: collected, fee, due, paid and still to pay, with the total still to pay, and every remittance, including voided ones. It needs `reports.view_financial` and full visibility of the owner; otherwise it returns 404.
4. **Sending the statement.** A "Send to owner" button on the statement page shows only when all of these hold: the owner is real, the member sees every property of that owner, and the month is over.
   - **Email** with the PDF attached, when the owner has an email.
   - An optional **SMS summary** (collected, fee, due, paid), when the owner has a phone. It uses the new `owner_statement` notification type (SMS only), sent to the bare number as with the M-Pesa payer (D-045 item 7).
   - Each send is kept (`OwnerStatementSend`). A send stores:
     - a number, OST-YYYY-NNNNNN;
     - the PDF, private and served only to members who can see the statement;
     - the figures as sent;
     - the email address with sent/failed and the error;
     - the SMS message.
   - Sends are listed on the statement page, and a statement can be sent again after a correction; each send gets a new number. Each send is audited.
5. **Capabilities.**
   - `owners.remit`: record and void remittances. Granted to Owner and Accountant.
   - `owners.send_statement`: send statements. Granted to Owner, Manager and Accountant.
   - Both are granted to existing roles by a data migration, as with 0010 and 0011.
6. **Home page.** A new "owner statements to send" item shows, under `owners.send_statement`. It counts owners the member fully sees who have an email or phone and have no statement sent for last month.
7. **Not in this step**:
   - paying owners through M-Pesa B2C or the bank;
   - expenses on the statement;
   - carrying amounts still to pay beyond the 12 months shown;
   - WhatsApp;
   - sending statements automatically;
   - an owner login beyond the existing Viewer role.

### D-059 — Two-step login (MFA) — ACCEPTED (2026-09-29, open to change before merge)
Phase 8, first item (doc 12 Tier 1; doc 16 item 14; doc 06). Built on `feature/phase8-mfa`, cut from `feature/owner-remittances`. The user said "yes" to starting Phase 8 with MFA.
1. **An authenticator app (TOTP), not SMS.** Codes follow RFC 6238: 6 digits, a 30-second step and SHA-1, which Google Authenticator, Microsoft Authenticator and Authy all read. The second factor is not SMS, for two reasons:
   - Password reset already goes by SMS, so an SMS code would be the same factor twice.
   - A SIM swap would get past both.
   The secret is encrypted at rest with `core.crypto`. The code is checked with the standard library, allowing one step either side for clock drift, and a code already used cannot be used again (the last step is stored). Setup shows a QR code, drawn on the server as SVG with the small `segno` library (a new dependency), plus the key as text and an `otpauth://` link for setting up on the same phone.
2. **Turning it on** is from a new "Security" page (`/account/security/`) for any logged-in user: scan, then enter a code to confirm. It is not switched on until a code has been checked.
3. **Recovery codes.** When MFA is switched on, 10 single-use codes are shown once and stored hashed. Using one is audited. The user can make a new set, which needs a current code and replaces the old set.
4. **Login.**
   - After a correct password, a user with MFA is **not logged in yet**. The session holds the pending user for 5 minutes, and the next page asks for a code or a recovery code.
   - Wrong codes are rate-limited: 5 per user per 15 minutes. Past that the user must wait, and the attempt is audited.
   - Resetting the password by SMS does **not** switch MFA off: the next login still asks for a code.
5. **Turning it off** needs the password and a current code (or a recovery code). It is audited.
   - A user who has lost both the phone and the recovery codes asks support.
   - A Platform Admin can then reset that user's MFA from Django admin after checking who they are. That is audited as well, and the user's other sessions are ended.
6. **Who.**
   - **Optional for everyone** in an organization.
   - The home page shows a one-line suggestion to members whose role holds any *sensitive* capability (the `sensitive` flag in the catalog: void invoices, reverse payments, M-Pesa settings, payment accounts, deposit deductions, data export, reports export). This follows capabilities, not role names, so the Owner and Accountant templates get it without naming them.
   - **Mandatory for Platform Admins** (`is_staff`). Django admin opens only in a session that passed a code. An admin without MFA is sent to set it up first.
7. **Sessions.** A successful code marks the session as verified. Switching MFA on or off, or a reset by a Platform Admin, ends the user's other sessions.
8. **Not in this step**:
   - "remember this device";
   - passkeys/WebAuthn;
   - an organization setting that makes MFA mandatory for its members;
   - SMS or email as a second factor.

### D-060 — Platform billing, plans and entitlements, SMS wallet — ACCEPTED (2026-09-29, open to change before merge)
Phase 8 (doc 11 §15 and §22 flow A, doc 12 Tier 1, doc 14 A12/A13, docs 05 and 10 for prices and limits). Built on `feature/phase8-platform-billing`, cut from `feature/phase8-mfa`. The user said "finish" Phase 8; defaults taken.
1. **Plans** live in a table seeded from docs 05 and 10, editable by Platform Admins in Django admin. Prices are **[VERIFY with pilots]**.

   | Plan | Units | Staff seats | KES / month | KES / year |
   |------|------|------|------|------|
   | Free | 5 | 2 | 0 | 0 |
   | Starter | 25 | 4 | 799 | 7,990 |
   | Business | 100 | 10 | 1,999 | 19,990 |
   | Professional | 300 | 30 | 4,999 | 49,990 |
   | Enterprise | no limit | no limit | set per customer | set per customer |

   A year costs ten months. Enterprise is not self-serve: a Platform Admin sets it up.
2. **Subscription**: one per organization. Status is TRIAL, ACTIVE, PAST_DUE or LAPSED. It has a plan, a billing interval (monthly or yearly) and the current paid period.
   - A new organization starts a **30-day trial on Business**.
   - An existing organization without a subscription is given the same trial the first time it is looked at.
3. **The money flow is kept apart from rent** (doc 11 §22): own tables, own numbers (`LPM-INV-2026-000001`, `LPM-RCT-2026-000001` from a platform sequence), no link to tenant invoices, and nothing in an organization's ledgers or reports.
4. **Invoices.**
   - `SubscriptionInvoice` snapshots the plan, interval, period and the customer's name and KRA PIN. It stores the net amount, `vat_amount` and total.
   - VAT is added only while `PLATFORM_VAT_REGISTERED` is on, at `PLATFORM_VAT_RATE` (16%). Both are **[VERIFY with our accountant]**, and plan prices are before VAT.
   - An invoice is OPEN, PAID or VOID. It is never deleted: a mistake is voided with a reason.
5. **How an invoice is raised.**
   - **Choosing a plan** (Owner, or anyone with `subscription.manage`) issues an invoice at once. When it is paid, the new period starts on the payment date.
   - Moving from a paid plan credits the unused days of the current paid period as a line on the new invoice. The credit is never more than the new amount.
   - A downgrade is refused while usage is over the new plan's limits.
   - Choosing Free needs no invoice: it applies at once if usage fits.
6. **Renewal.** The daily job `subscriptions_daily`:
   - Issues the renewal invoice 7 days before a paid period ends, due on the last day.
   - Ends trials. An organization whose usage fits Free moves to Free; otherwise it goes PAST_DUE with an open invoice for Business monthly.
   - Moves a subscription past its due date to PAST_DUE.
   - After **14 days of grace**, moves it to LAPSED and sets the organization **read-only** (doc 14 A12). Data stays visible and exportable, nothing new can be created, and nothing is deleted.
   - When payment arrives, the subscription is ACTIVE and the organization ACTIVE again. A FROZEN organization stays frozen.
7. **Payments.** Until our own Paybill exists, a Platform Admin records each payment in Django admin: invoice, amount, method (M-Pesa, bank, other), reference (unique) and date.
   - Recording a payment that covers the invoice marks it paid, issues a `PlatformReceipt` and starts the period.
   - The subscription page shows how to pay: Paybill `PLATFORM_PAYBILL` with the invoice number as the account.
   - Self-serve STK push to our Paybill and C2B matching come with the go-live of our own Daraja account **[VERIFY]**.
8. **Documents.** The invoice and the receipt are PDFs from the subscription page. Any member with `subscription.view` can download them.
9. **Entitlements**: one service, `subscriptions.entitlements`, and nothing else knows about plans.
   - `check(org, "units", adding=n)` is called where units are created (the unit form, the single-house main unit and the CSV import).
   - `check(org, "seats", adding=1)` is called when staff are invited (not for the Viewer role). Accepting is not checked again, because a pending invitation already holds its seat.
   - Seats count active members and pending invitations, except in the Viewer role (doc 10: tenants and viewers are not seats).
   - Over the limit, *creating* is refused with a message naming the plan. Viewing is never blocked, and data over a limit after a downgrade stays.
10. **SMS wallet** (doc 14 A13): an append-only list of entries per organization (top-up, message, adjustment), with the balance held on a locked row.
    - A Platform Admin records top-ups in Django admin, with the payment reference, and the top-up gets a receipt.
    - Each SMS the organization sends is charged `SMS_PRICE` per segment (KES 1.00 **[VERIFY]**) when it is sent.
    - When the balance is zero or below, `sms_available(org)` is false and messages fall back to the next channel or are skipped with "Channel not available".
    - The balance may go slightly negative from a message already queued.
    - Login codes, invitations and other platform messages are never charged.
    - `SMS_WALLET_ENFORCED` switches the check on. It is on in production and off by default elsewhere, so development and tests are unchanged.
11. **eTIMS**: `subscriptions.etims.EtimsAdapter` is an interface with `submit(invoice)`. The default `NullEtimsAdapter` submits nothing. `SubscriptionInvoice.etims_reference` is there for the control-unit number when a real adapter arrives, after the accountant's go/no-go (D-039) **[VERIFY]**.
12. **Pages**: `/subscription/` shows the plan, usage against limits, the trial or period end, the open invoice with how to pay, invoice and receipt history, the SMS wallet balance with its last entries, and plan choice.
    - `subscription.view` (org-wide) sees the page.
    - `subscription.manage` changes the plan. It is read-only-safe, so a lapsed organization can still pay.
    - The top bar shows a warning while in trial (last 7 days), PAST_DUE or LAPSED.
13. **Not in this step**:
    - self-serve M-Pesa payment;
    - automatic matching of payments to our Paybill;
    - seat add-ons;
    - a platform revenue report beyond admin lists;
    - dunning SMS to the owner;
    - plan-included SMS bundles;
    - AI usage limits.


### D-061 — Status page, health check and the security page — ACCEPTED (2026-09-29, open to change before merge)

**Context.** Phase 8 asks for a public status page and a security and data-protection page (doc 04, doc 06). Landlords trusting us with rent money want to see that we are up, and what we do with their data.

**Decision.**
1. **`/healthz`** returns JSON for the load balancer and uptime monitor. It needs no login and is not rate limited.
   - It checks the database (`SELECT 1`) and the cache (write and read one key).
   - Everything passing returns 200 `{"status": "ok", "checks": {...}}`. Any failure returns 503 with the failing check named. No versions, hosts or error text are shown.
2. **`/status/`** is a public page with no login. For each part it shows "Working", "Slow or partly down" or "Down":
   - **Web app**: it is working when the page renders.
   - **Database** and **cache**: the same checks as `/healthz`.
   - **Scheduled jobs**: each known job (below) is working when its last run succeeded within its expected interval. A job that is late or failed counts as partly down. The page shows no job output.
   - **Incidents**: a Platform Admin writes these in Django admin.
     - The fields are title, impact (minor, major, outage), status (investigating, identified, monitoring, resolved), public updates with times, start and end.
     - Open incidents show at the top. Resolved ones stay listed for 14 days.
     - Nothing is shown about any organization.
   - The result is cached for 60 seconds, so the page cannot be used to load the database. Saving an incident in admin clears the cache.
3. **`/security/`** is a public page in plain language covering:
   - HTTPS everywhere.
   - Passwords stored hashed.
   - Two-step login, open to everyone and required for our own staff (D-059).
   - Provider keys encrypted at rest.
   - Every change recorded in an audit log.
   - Staff see only the properties they are given.
   - Daily backups with a restore test (D-062).
   - Where the data is hosted **[VERIFY]**.
   - The Kenya Data Protection Act 2019, and our ODPC registration **[VERIFY]**.
   - Export on request, and deletion on request after the legal retention period.
   - The contact for data protection is `DATA_PROTECTION_EMAIL`, and the hosting location is `HOSTING_LOCATION`; each line is left out while its setting is empty.
   - It is linked, with the status page, from a footer on every page, including the login page.
4. **Not in this step**:
   - status history graphs;
   - email or SMS subscription to status updates;
   - an external uptime service, which the operator sets up against `/healthz` (doc 18).


### D-062 — Operations: job records, backups, alerts, error tracking and logging — ACCEPTED (2026-09-29, open to change before merge)

**Context.** Phase 8 asks for backups, monitoring, error tracking and a production settings review before real landlords' money runs through the system (doc 04, doc 06).

**Decision.**
1. **Job records.** Every scheduled command records a `JobRun`:
   - the job name, start and end, whether it succeeded, a short summary, and the error text on failure;
   - a failure is recorded and then raised again, so cron still sees it.

   The known jobs and how often they must succeed:

   | Job | Must succeed within |
   |---|---|
   | `send_due_messages` | 30 minutes |
   | `billing_daily`, `mpesa_daily`, `subscriptions_daily` | 26 hours |
   | `backup` | 26 hours |
   | `purge_otp_codes`, `purge_import_previews` | 8 days |

   `check_jobs` deletes runs older than 90 days.
2. **Alerts.** `check_jobs`, run every 15 minutes, emails `OPS_ALERT_EMAILS` when:
   - a known job is late or its last run failed;
   - the health checks fail.

   It sends the same alert at most once every 6 hours, and sends an "all clear" email when things are fine again.
3. **Backups.** `manage.py backup` writes a PostgreSQL custom-format dump (`pg_dump -Fc`) and a tar.gz of the media folder to `BACKUP_DIR`, with a date-stamped name. It then:
   - checks the dump with `pg_restore --list`, and records the sizes;
   - keeps the latest set of each of the last 7 days, the first set of each of the last 4 weeks and the first set of each of the last 12 months, and deletes the rest. Other files in the folder are never touched.

   A failed dump is deleted and the run is recorded as failed.

   Copying off the server (encrypted object storage) is done by the operator's sync tool, as described in doc 18 **[VERIFY: provider]**.

   The aims are **RPO 24 hours** (at most one day lost), and **RTO 4 hours** to restore on a new server. Point-in-time recovery with WAL archiving is the next step once paying customers pass 50.
4. **Restore test.** Doc 18 gives the steps. A restore into a scratch database is done monthly, and each test is recorded in the doc's table.
5. **Error tracking.** When `SENTRY_DSN` is set and `sentry-sdk` is installed, errors go to Sentry with `send_default_pii=False`, 10% of requests traced, and the environment and release from the environment. Otherwise, errors are emailed to `ADMINS` in production.
6. **Logging.** Plain lines to the console (the process manager keeps them), with the level set by `LOG_LEVEL`. Request bodies, codes and secrets are never logged. `django.security` warnings are always kept.
7. **Production settings review.** Production now also sets:
   - HSTS for one year, with preload once the domain is steady **[VERIFY]**;
   - `SECURE_CONTENT_TYPE_NOSNIFF`;
   - `SECURE_REFERRER_POLICY = "same-origin"`;
   - `X_FRAME_OPTIONS = "DENY"`;
   - the session cookie `HttpOnly` and `SameSite=Lax`;
   - `CSRF_TRUSTED_ORIGINS` from `SITE_URL`;
   - a `DB_CONN_MAX_AGE` of 60 seconds;
   - a `SECRET_KEY` check that it is at least 50 characters and not the development key.

   `manage.py check --deploy` passes in production.
8. **Doc 18 (`config/docs/18_OPERATIONS.md`)** covers:
   - the processes;
   - the cron table;
   - environment variables;
   - deploying;
   - backups and the restore steps;
   - alerts;
   - what to do in an incident (including posting on the status page);
   - rotating `FIELD_ENCRYPTION_KEYS` and `SECRET_KEY`.
9. **Not in this step**:
   - infrastructure as code;
   - WAL archiving;
   - multi-region;
   - an on-call rota.


### D-063 — Onboarding polish, help pages and support requests — ACCEPTED (2026-09-29, open to change before merge)

**Context.** Phase 8 asks for onboarding polish, help docs and a support channel. It also asks for the import concierge for pilots, with time to first invoice tracked (doc 04, doc 05).

**Decision.**
1. **Setup checklist.**
   - Each step on the home checklist is worked out from real data and links to the page that does it:
     - add a property;
     - add units;
     - add a tenant;
     - start a lease;
     - set up how tenants pay (a payment account; today these are made on the M-Pesa page);
     - issue the first invoice;
     - invite a team member (optional).
   - It shows "n of 6 done", and hides itself once every required step is done. As before, it is shown to members who hold `properties.manage`.
2. **Help pages** at `/help/` are written as templates in the repo, so they are reviewed like code. They are open to anyone logged in. They are in English only for now; Swahili follows with the rest of the Swahili review.
   - Topics:
     - getting started;
     - properties and units;
     - tenants and leases;
     - invoices and payments;
     - M-Pesa;
     - messages and SMS credit;
     - water meters;
     - staff and roles;
     - subscription and billing;
     - importing data.
   - An index page lists them, with a simple title search on the page.
3. **Support requests** at `/help/contact/`, for any member, including members of a frozen or read-only organization. Tenants in the portal are sent back to it, because they contact their landlord, not us.
   - The kinds are: a question, a problem, or import help ("do my import for me").
   - It takes a subject, a message, and one optional attachment (≤ 10 MB; spreadsheets, CSV, PDF, text or images). The attachment is stored under a random name and only downloaded through admin.
   - The request records the organization, the member, the page they came from (only a page on our own site) and the browser.
   - It emails `SUPPORT_EMAIL` and confirms on screen with the request number (`SUP-YYYY-NNNNNN`).
   - Platform Admins work the requests in Django admin with a status: open, waiting on the customer, or closed.
   - A WhatsApp link (`SUPPORT_WHATSAPP`, wa.me) is shown when set.
4. **Import concierge.**
   - An import-help request is flagged in admin.
   - The Platform Admin works it by running the import with the customer's file through the existing import screens. They log in as a member the customer invited; there is no impersonation.
   - Doc 18 gives the steps.
5. **Time to first invoice.**
   - The organization admin list shows how long it took from creation to the first invoice that is not a draft (`Invoice.created_at`), or "—".
   - It is filterable by "has invoiced".
6. **Not in this step**:
   - in-app chat;
   - a knowledge-base CMS;
   - video tours;
   - impersonation;
   - customer satisfaction ratings.

### D-064 — Bank and M-Pesa statement import, bank matching and the bank inbox — ACCEPTED (2026-09-29, open to change before merge)

**Context.** Phase 4 still lacks bank payments. Doc 12 and doc 16 plan for them to come in as `Payment.method = BANK`, through a CSV bank statement run by the same matching rules as M-Pesa, with an inbox for what cannot be placed. D-045 item 10 left the M-Pesa statement import for the same step. That import catches callbacks that never arrived, and covers Tills and Paybills that have no Daraja app.

**Decision.**
1. **One upload page** at `/bank/import/`. The member picks a payment account and uploads a CSV (≤ 1 MB, ≤ 5,000 lines). The account type decides how the file is read:
   - For a bank account, it is a bank statement.
   - For a Paybill or Till, it is an M-Pesa organization statement.
   - Personal M-Pesa and cash accounts cannot import. A personal statement comes as a PDF.
   - Bank accounts are added and linked to the properties they collect for on `/bank/accounts/`, which needs `payment_accounts.manage`. Until now they could only be added in admin.
   - The file is read at once into a preview. The preview shows:
     - the new credit lines;
     - lines already imported (skipped);
     - debit lines (skipped and counted);
     - lines that could not be read, with the reason.
   - Nothing is stored until "Import". The preview row is kept for 24 hours and then emptied by the daily `purge_import_previews`, as with other imports.
   - There is no undo. A matched line is taken off by reversing its payment, which puts the line back in the inbox. An unwanted line is ignored with a reason.
2. **Who.**
   - Importing needs `mpesa.match` ("Match payments in the unallocated inbox"), so no new capability is added.
   - A member limited to some properties can import only for an account that serves one of their properties. An account that serves no property in particular needs a member with every property.
   - Automatic matches are made by the system, as with a callback, even on leases the importer cannot see. `StatementImport` records who uploaded the file.
3. **Reading a bank CSV.**
   - The header row is found among the first 30 rows by known names, ignoring case and punctuation:
     - date: transaction, posting, value or booking date;
     - description: narrative, narration, particulars or details;
     - reference: ref, cheque or receipt number;
     - credit: money in, paid in or deposit;
     - debit: money out or withdrawal;
     - amount;
     - balance.
   - A date and either a credit or an amount column are required. When a file is not recognised, the page names the columns it looks for, so the headers can be renamed. A column map saved per account comes later.
   - **Dates** are day first (`31/01/2026`, `31-01-2026`, `31.01.2026`, `31 Jan 2026`, `31-Jan-26`), or ISO. A time after the date is allowed.
   - **Amounts:**
     - `1,234.50`, `KES 1,234.50` and `1234.5` are all accepted.
     - `(1,234.50)`, `-1,234.50` and a trailing `DR` mean money out; a trailing `CR` means money in.
     - With a single amount column, only positive amounts are credits.
   - Commas, semicolons and tabs are detected, and UTF-8 with or without a BOM is accepted, with a Windows-1252 fallback.
   - Equity, KCB and Co-op exports are the first targets [VERIFY with real exports].
4. **Bank lines** (`BankTransaction`) are stored per payment account:
   - posted date, description, reference, amount and running balance, if given;
   - the import it came from;
   - a status: MATCHED, UNMATCHED or IGNORED;
   - a note, a suggested lease, and the payment once matched (with by whom and when).
   - **Fingerprint:** a statement line has no unique id, so a fingerprint made from the date, amount, description, reference and balance, plus the occurrence number of identical lines in the file, is unique per account. A statement that overlaps an earlier one therefore adds only the new lines.
   - Lines are never deleted.
5. **Bank matching** follows D-045's rules:
   - **Reference, confident:** the description and reference are split into words, and pairs of neighbouring words are joined (`GV A1` → `GVA1`). If exactly one unit's payment reference is found, ignoring case and dashes, the account serves that unit's property, and the unit has exactly one active lease, the line is confirmed at once with `record_system_payment(method=BANK, reference=<bank reference, else the description>)`, allocated oldest first and receipted. The reference check is shared with M-Pesa.
   - **Already recorded:** when the line's reference is already on a live payment, or the lease already has a live bank payment of the same amount within 3 days that is not linked to a bank line, it is left UNMATCHED with that lease suggested and a note. Staff check it and ignore one.
   - **Suggestion only:** among the active leases the account serves, a lease is suggested when at least two words of a tenant's name (three letters or more) appear in the description. If several leases qualify, the one whose balance equals the amount is suggested.
   - **Otherwise** the line is UNMATCHED with the reason.
6. **Bank inbox** at `/bank/inbox/`, needing `mpesa.match`. It works like the M-Pesa inbox:
   - accept the suggestion, choose a lease, ignore with a reason, or put back;
   - a match confirms at once;
   - a reversed bank payment returns its line to the inbox.
   - Scope rule:
     - An unmatched line is seen by members whose properties the account serves, or by members with every property when the account serves none in particular.
     - A matched line follows its lease's property.
   - Bank lines are listed at `/bank/lines/` with `payments.view`.
   - There are no SMS or email alerts for imported lines; the import summary says how many are waiting.
7. **M-Pesa statement import** for Paybill and Till accounts.
   - It reads the org-portal CSV columns [VERIFY]: Receipt No., Completion Time, Details, Transaction Status, Paid In, Withdrawn, Balance, Other Party Info and A/C No.
   - Only completed money-in rows are taken. When there is no A/C No. column, the account reference is taken from `Acc. <ref>` in Details.
   - The payer's number and name come from Other Party Info (`2547… - NAME`). A masked number is kept only as the raw value.
   - For a receipt number not seen before, a new `MpesaTransaction` is created with `source = STATEMENT`. It is matched by the same `process` as a callback, and anything unmatched lands in the existing M-Pesa inbox. No staff or payer alerts are sent.
   - Known receipt numbers are counted as already received.
8. **Not in this step:**
   - PDF statements;
   - bank APIs;
   - saved column maps;
   - reconciling statement balances;
   - importing money out;
   - the Kenyan unverified-code check (doc 16, later).

### D-065 — Profit and loss, cash flow and aged receivables by property — ACCEPTED (2026-09-29, open to change before merge)

**Context.** Phase 4 also asks for profit and loss, cash flow and aged receivables by property (doc 04). Doc 11 §22 defines net operating income as collected rent less expenses. §26 says property income is on a cash basis, deposits are shown apart, and arrears use aging buckets. Expenses (flow C) are not built yet.

**Decision.**
1. **Common rules.**
   - The pages are under Reports and need `reports.view_financial`. CSV export needs `reports.export`.
   - Only the properties the member can see are counted, with an optional filter to one property.
   - A period runs from one month to another, 12 months at most, and defaults to the last 6 months including this one. Figures are shown by month with a total.
   - Money received is split as in the income pack (D-050): each confirmed payment by its allocations to rent, other charges and deposits, plus money not yet applied. Reversed payments are left out.
2. **Profit and loss** (`/reports/profit-loss/`), cash basis.
   - Income is rent collected, other charges collected, and money not yet applied (shown on its own line, counted as rent as in D-050).
   - Expenses show "not recorded yet" until flow C exists.
   - Net operating income is income less expenses.
   - Memo lines: rent billed for each month and the collection rate (rent collected ÷ rent billed), for information. Deposits received are shown as not income.
3. **Cash flow** (`/reports/cash-flow/`).
   - Money in: rent, other charges, deposits, and money not yet applied.
   - Money out:
     - Deposit refunds (`DepositEntry` REFUNDED, by entry date). A refund later corrected is left out, whenever corrected, as reversed payments are.
     - Payments to owners (`OwnerRemittance` not voided, by paid-on date).
     - Owner payments belong to an owner, not a property, so they are shown only when the whole organization is in view (all properties, no filter). Otherwise a note says they are left out.
   - Net cash is money in less money out.
4. **Aged receivables by property** (`/reports/receivables/`), as of today.
   - Each property with money owed gets a row with the aging buckets (current, 1–30, 31–60, 61–90, over 90 days) and a total, from the same FIFO aging as the arrears page.
   - Each row opens to its leases.
   - Money that is only current is included, so the total is everything owed.
   - Credit balances are left out; they are not receivables.
5. **Not in this step:**
   - accrual-basis accounts;
   - budgets;
   - a balance sheet;
   - expense entry;
   - PDF versions of these reports.

### D-066 — Unverified M-Pesa codes — ACCEPTED (2026-09-29, open to change before merge)

**Context.** Staff often record an M-Pesa payment by hand from the code in the tenant's SMS. A made-up or edited SMS then becomes a receipt. D-045 item 12 stops a real code from counting twice, but nothing yet says that a typed code never reached our Paybill or Till. Doc 16 Part 3 item 14 (Silqu) flags a typed code that no callback or statement confirms within 24 hours, and D-064 left this until the M-Pesa statement import existed.

**Decision.**
1. **What is checked:** live payments (pending review or confirmed) with `method = MPESA` and a reference, that are not the payment of an `MpesaTransaction`. These are the ones typed by hand. Payments made from a callback, an STK request or a statement line are already proven.
2. **When a code counts as seen:** an `MpesaTransaction` in the same organization has that code (ignoring case), whatever its status or source. If its amount differs from the payment's, the payment is flagged at once as **"amount does not match"**.
3. **When a code counts as missing** (**"not found"**): it was recorded more than 24 hours ago, and something should have brought it in:
   - the payment's account (or, when it has none, any Paybill or Till of the organization) had its C2B URLs registered on or before the date paid; or
   - an imported M-Pesa statement for that account covers the date paid. `StatementImport` now keeps the first and last dates of the lines read (`period_from`, `period_to`); imports made before this change have none and do not count.
   Otherwise the code **cannot be checked**. It is not flagged, and the page says how many are in this state and why.
4. **Where it shows.**
   - A "Codes to check" tab on the M-Pesa pages (`/mpesa/codes/`), needing `mpesa.match`, scoped by the lease's property like the payments list.
   - A warning on the payment page.
   - A "Today" task on the home page for `mpesa.match` holders.
   - A daily in-app alert `mpesa_unverified_codes` from `mpesa_daily` to each `mpesa.match` holder who has any, once a day.
5. **What staff do.**
   - Reverse the payment (or reject it while pending) with the existing actions.
   - Or mark the code **checked** with a note (for example "seen on the Safaricom portal, paid to our other Till"). This needs `mpesa.match`, is audited, and is kept as a `CodeCheck`. A checked payment leaves the list.
   - A code that arrives later (callback or statement) clears the flag without anyone acting.
6. **Not in this step:** blocking the confirmation of a pending payment, SMS to the tenant, and checks against the Safaricom transaction status API.

### D-067 — Expenses, suppliers and net operating income — ACCEPTED (2026-09-29, open to change before merge)

**Context.** Flow C (D-027, doc 11 §22) is the landlord paying suppliers and contractors. Doc 11 §14 plans `Expense(org, property, category, amount, date, receipt, recorded_by)`, and property net operating income is collected rent less expenses paid. The profit and loss report (D-065), the cash flow report and the owner statement (D-053) all show expenses as "not recorded yet". The capabilities `expenses.view`, `expenses.submit` and `expenses.approve` and `contractors.manage` exist from doc 13. Doc 16 asks for receipt photos taken on a phone. Maintenance is not built yet, so the link from an expense to a maintenance request waits for it.

**Decision.**
1. **What an expense is.** Money already paid out for one property (cash basis): the property, a category, a description, the amount, the date paid, the method (M-Pesa, bank, cash, cheque, other), a reference, an optional supplier and an optional receipt. Each gets a number `EXP-<year>-000001` per organization. Expenses are never deleted or edited: a wrong one is rejected or voided with a reason, and the right one is recorded again.
2. **Recording and approval** (maker/checker, doc 13).
   - Recording needs `expenses.submit` on the property. It waits for approval.
   - When the recorder also has `expenses.approve` on the property, it is approved at once, as payments are with `payments.confirm`.
   - `expenses.approve` holders approve or reject (with a reason) a waiting expense, and void (with a reason) an approved one.
   - Only approved expenses count in reports. Waiting ones are listed, with their total, in a note.
   - With the default roles, a Manager's expenses wait for the Owner or the Accountant.
3. **Duplicates.** A reference already on a live expense (waiting or approved) of the same organization is refused, ignoring case and spaces, so an M-Pesa code or a cheque number is recorded once.
4. **Receipt.** A photo, re-encoded as with condition reports (D-047), or a PDF up to 10 MB that starts with `%PDF-`. It is optional. One can be added later to an expense that has none, by its recorder or an approver. It is served only to members who can see the expense, never cached publicly; a PDF downloads rather than opening in the browser.
5. **Categories** belong to the organization. The first time expenses are opened, these are created: Repairs and maintenance, Water, Electricity, Security, Cleaning and garbage, Caretaker and staff wages, Insurance, Land rates and ground rent, Professional and legal fees, Other. `expenses.approve` holders with every property add, rename and archive categories. An archived category stays on its expenses.
6. **Suppliers** belong to the organization: name, phone, email, KRA PIN, how they are paid (free text, e.g. a Till or bank account) and a note. They are managed with `contractors.manage` and archived, not deleted. Anyone recording an expense can pick one. The supplier page shows what has been paid to each (approved expenses on the properties the member can see).
7. **Pages** (under "Expenses", needing `expenses.view`, scoped by property; anything outside the member's properties is a 404):
   - the list at `/expenses/`, filtered by property, category, supplier, status and months, with the total, and CSV export with `reports.export`;
   - record an expense at `/expenses/new/`, phone-first, with the camera for the receipt;
   - one expense, with approve, reject, void and add receipt;
   - suppliers and categories.
   - A home task "expenses to approve" for approvers.
8. **Reports.**
   - **Profit and loss:** expenses by category and by month, by date paid. Net operating income is income less expenses.
   - **Cash flow:** expenses paid are money out.
   - **Owner statement:** approved expenses on the owner's properties paid in the month are listed under each property and taken off: due to the owner = collected − management fee − expenses. The fee stays a share of rent collected. A statement sent keeps the expenses figure, and the SMS summary gains `{expenses}`. Due can be below zero when expenses are more than what was collected.
   - **Annual rental income pack:** monthly rental income tax is on gross rent, so expenses are not taken off **[VERIFY with a tax adviser]**. The note now says so.
9. **Audit.** Recording, approving, rejecting, voiding, adding a receipt, and every change to suppliers and categories.
10. **Not in this step:**
    - the link to maintenance requests (maintenance is not built);
    - bills not yet paid (payables), due dates and part payments;
    - recurring expenses;
    - costs for the whole organization, or split across properties;
    - withholding tax on supplier payments and eTIMS invoices **[VERIFY]**;
    - paying suppliers from the app;
    - reading money out from bank statements (D-064 item 8);
    - budgets.

### D-068 — Maintenance requests and jobs — ACCEPTED (2026-09-29, open to change before merge)

**Context.** Doc 11 §14 plans `MaintenanceRequest(org, unit, tenant?, title, priority, status, assigned_to, cost)`: the Maintenance role sees only the requests assigned to it, and tenants submit and follow their own. Doc 12 and doc 16 (item 10) add an assigned supplier, a due date, status timestamps, photos first and urgency triage. The capabilities `maintenance.view`, `maintenance.view_assigned`, `maintenance.create`, `maintenance.assign`, `maintenance.update`, `maintenance.close`, `maintenance.costs` and `contractors.manage` exist from doc 13, and so do the Maintenance Manager and Maintenance Staff roles. Doc 11 §27 lists "maintenance update" (to the tenant) and "maintenance assigned" (to the assignee). Numbers are `MNT-2026-000031` (doc 11 §19). Expenses (D-067) left the link to maintenance until now.

**Decision.**
1. **What a request is.** A repair or problem at one property, for one unit or for the common areas (no unit). It has a title, a description, a kind (plumbing, electrical, water supply, doors and locks, roof and walls, painting, appliances, pests, security, cleaning and garbage, other), a priority, a due time, photos and a history of updates. Each gets a number `MNT-<year>-000001` per organization. Requests are never deleted: a wrong or duplicate one is cancelled with a reason.
2. **Priority and due time** (triage). Emergency (due in 24 hours), High (3 days), Normal (7 days), Low (30 days). The due time is set from the priority when the request is made, and is changed with the priority, or on its own, by `maintenance.assign` holders. An open request past its due time is overdue.
3. **Status.** New → Assigned → In progress → Done → Closed. On hold (waiting for parts, access or the tenant) can be set from Assigned or In progress and left again. Cancelled (with a reason) from any open status. Done means the worker says the work is finished; Closed means someone with `maintenance.close` has checked it. `maintenance.close` holders can also close straight from any open status, and reopen a Done or Closed request with a reason (it goes back to Assigned, or New when nobody is assigned). The time of the first assignment, the start, Done, Closed and Cancelled is kept, so time to respond and time to fix can be measured.
4. **Assignment.** `maintenance.assign` holders assign a staff member, a supplier (D-067), or both. A staff member must be able to see the request: `maintenance.view` or `maintenance.view_assigned` on its property. The supplier gets an SMS with the job (number, property, unit, title, priority and the assigner's name) if they have a phone and the box is ticked. Reassigning is allowed and recorded.
5. **Who sees what** (scoped by property; anything else is a 404).
   - `maintenance.view`: every request on the member's properties.
   - `maintenance.view_assigned` alone: only the requests assigned to them.
   - `maintenance.create`: report a request on a property the member can see.
   - `maintenance.update`: add notes and photos, start, put on hold and mark done, on requests the member can see.
   - Costs (below) are shown to holders of `maintenance.costs` or `expenses.view` on the property. With the default roles, Maintenance Staff never see money.
6. **Costs.** A cost is an expense (D-067) linked to the request: `Expense.maintenance_request`. It is recorded from the request page by members with both `maintenance.costs` and `expenses.submit` on the property, with the request's supplier and the "Repairs and maintenance" category picked in advance, and it is approved like any other expense. The request shows its approved and waiting costs; the expense links back to the request. A voided or rejected expense stops counting, as in D-067.
7. **Tenants.**
   - From the portal (D-055), a tenant reports a problem on a unit of one of their active leases: title, what is wrong, whether it is an emergency (Emergency or Normal; staff can change it) and up to 3 photos. At most 5 a day per tenant account.
   - Staff can mark a request as reported by the tenant of the unit's active lease; it is then theirs in the same way.
   - In the portal the tenant sees their requests (on their leases) with the number, status, due time and the updates that were shared with them, and can add a comment. Staff notes are private unless "tell the tenant" is ticked. Status changes are shown to the tenant.
8. **Notifications.**
   - `maintenance_reported` (in-app, staff): a new request, to `maintenance.assign` holders on the property. Emergencies are urgent.
   - `maintenance_assigned` (in-app, staff): to the staff member assigned.
   - `maintenance_supplier_job` (SMS to the supplier's phone): see item 4.
   - `maintenance_update` (tenant, WhatsApp or SMS): to the reporting tenant when the request is received, when it is Done or Closed (only once), when it is cancelled, and when a staff note is shared with them. **[VERIFY the Swahili]**
9. **Photos.** Re-encoded as with condition reports (D-047), up to 10 per request. They are served only to members who can see the request and to its tenant, never cached publicly.
10. **Pages** (under "Maintenance", needing `maintenance.view` or `maintenance.view_assigned`):
    - the list at `/maintenance/`: open requests first, filtered by property, status, priority, kind, "assigned to me" and "overdue", with open, overdue and average days to close in the last 90 days, and CSV with `reports.export`;
    - report a request at `/maintenance/new/`, phone-first, with the camera;
    - one request: details, photos, the history and every action allowed.
    - Home tasks: "new requests to assign" (`maintenance.assign`), "jobs assigned to you" (open, assigned to the member) and "overdue jobs" (`maintenance.view`).
    - The unit page shows its open requests with a link to them.
11. **Audit.** Reporting, every status change, assignment, priority or due change, note, photo and cost.
12. **Not in this step:**
    - supplier logins, quotes and approving a quote before the work starts;
    - supplier bills not yet paid (D-067 item 10);
    - planned and recurring maintenance;
    - charging a tenant for damage (use an invoice adjustment for now);
    - setting the unit to "under maintenance" automatically (the unit page does it);
    - the tenant rating the work;
    - spare parts and stock;
    - working offline (doc 14 C1).

### D-069 — Adding a charge to every lease at a property — ACCEPTED (2026-09-29, open to change before merge)

**Context.** Garbage, service charge, security and similar fixed extras are recurring lease charges (`LeaseCharge`, doc 11 §8, D-049 item 4), added one lease at a time from the lease page. In an apartment block where every unit pays the same garbage fee, that means opening every lease. The user asked for one button that adds it to the whole block.

**Decision.**
1. **Where.** The property page gets "Add a charge to all leases" for members with `charges.manage` on the property, when it is not archived and has open leases. It opens `/properties/<id>/charges/`.
2. **What it does.** One recurring charge type (Garbage is picked by default), an amount per month and a start date are added to every open lease (draft or active) on the property through the same service as the lease page (`add_charge`), so the same rules, audit and rebilling apply.
3. **Start date.** Defaults to the first day of next month, so invoices already issued are not changed. A lease that starts later gets the charge from its own start date. An earlier date is allowed and rebills the months already billed, as on the lease page.
4. **Preview first.** The page lists the leases that will get the charge and the ones skipped with the reason (already has that charge for those dates, ends before the start date), and nothing is saved until "Add to N leases" is pressed. Leases skipped are left alone; the rest are added.
5. **New leases** do not pick the charge up automatically; it is added on the lease like today. A property-level default charge list is left for later.
6. **Changing or ending** the charge stays per lease.

### D-070 — Electricity sub-meters — ACCEPTED (2026-09-30, open to change before merge)

**Context.** D-057 built metered water and left electricity out (item 10). Many Kenyan blocks have one supply for the building and a sub-meter per unit, or common-area lighting shared by all units. The landlord reads each sub-meter and bills the units used, much like water. Where each unit has its own prepaid token meter, nothing is billed by the landlord.

**Decision.**
1. **One meters app, two utilities.** `Meter.utility` is Water (the default, so every existing meter stays water) or Electricity. Everything in D-057 applies to both: readings, the optional photo, flags, approval, undo, the shared split and billing in the month after the reading.
2. **Units.** Water is read in m³, electricity in kWh; the rate is per m³ or per kWh. Pages, forms and invoice lines show the right unit.
3. **Invoice lines.** Electricity charges go on the invoice under a system charge type "Metered electricity" (new category `METERED_ELECTRICITY`, made on first use like "Metered water", never a recurring lease charge). A line reads, for example, "Electricity A1: 1,200.000 → 1,320.000 kWh = 120.000 kWh × 25.00".
4. **Flags.** "Nothing used while let" replaces "No water used while let". "Much higher than usual" needs at least 5 m³ over the usual use for water and at least 50 kWh for electricity; the "3 times the usual" rule is the same.
5. **Prepaid token meters** are recorded as Prepaid: readings can be kept, nothing is billed.
6. **Pages.** The nav link and page are "Meters", with Water and Electricity shown on each meter and a filter by utility. The reading round covers both, each box showing its unit. The property page button reads "Meters". The home task says "meter readings to approve".
7. **Rates are the landlord's.** The app does not check the price per kWh against the KPLC tariff. **[VERIFY with EPRA whether a landlord may resell electricity to tenants and at what price; a note on the meter form says to follow the rules.]**
8. **Not in this step:** a standing (fixed monthly) charge, time-of-use or tiered tariffs, pass-through of the KPLC bill split by readings, gas, and tenant-submitted readings.

### D-071 — Documents — ACCEPTED (2026-09-30, open to change before merge)

**Context.** Landlords keep signed lease agreements, copies of tenants' IDs, title deeds, insurance, county permits and house rules in folders or on WhatsApp. Doc 11 §13 sketched `Document(org, file, category, visibility, linked to property/lease/tenant, uploaded_by)`, and doc 13 already has `documents.view`, `documents.upload`, `documents.manage` and `documents.share_with_tenant`. Generating lease agreements from a template (doc 14 B10) is a separate step.

**Decision.**
1. **What a document is.** A title, a category, one file, an optional "expires on" date and note, who uploaded it and when. It belongs to one place: a property, a unit, a lease or a tenant. A unit or lease document also records its property, so it is scoped like everything else on that property. It is never deleted: it is archived and can be restored.
2. **Categories** are a fixed list: Lease agreement, ID or passport copy, KRA PIN certificate, Title deed or ownership, Insurance, Licence or permit, House rules, Notice or letter, Utility bill, Contract, Photo, Other.
3. **Files.** A PDF, a Word or Excel file (.docx, .xlsx) up to 10 MB is kept as it is, after checking it really is one; a photo (JPEG or PNG) is re-encoded as with condition reports (D-047). Anything else is refused. Files get a random name, are never public, and are served only through a page that checks who is asking: photos open in the browser, everything else downloads under its original name.
4. **Who sees what** (anything else is a 404).
   - `documents.view` on the property: its property, unit and lease documents.
   - Tenant documents (IDs, KRA PIN) also need `tenants.view_sensitive` and the tenant must be one the member can see. With the default roles, a Leasing Agent sees lease documents but not ID copies.
   - `documents.upload` to add one where the member can see it; `documents.manage` to change the details, archive and restore; `documents.share_with_tenant` to share or stop sharing.
5. **Sharing with the tenant.** Only lease and property documents can be shared. In the portal (D-055) the lease page lists the shared documents of that lease, and, while the lease is active, the shared documents of its property (house rules, notices). Unit and tenant documents are never shown in the portal. Sharing does not send a message.
6. **Expiry.** A document with "expires on" shows as expired or expiring within 30 days. The home task "documents expiring within 30 days" goes to `documents.manage` holders; archived documents do not count.
7. **Pages.** "Documents" in the nav with filters (property, category, where it belongs, expiring, archived, a search on the title). Upload from the list (choosing a property) or from the property, unit, lease and tenant pages, which each show a Documents card. One document: details, download, edit, share and archive.
8. **Audit.** Upload, edit, share and unshare, archive and restore. Opening a tenant document is audited too.
9. **Not in this step:**
   - generating agreements and notices from templates, and e-signature (doc 14 B10);
   - versions of a document (upload a new one and archive the old);
   - several files at once;
   - a virus scan of Word and Excel files **[VERIFY before production: ClamAV or the storage provider's scan]**;
   - a storage limit per plan;
   - tenants uploading documents from the portal, and telling them when one is shared.

### D-072 — Prospects and viewings — ACCEPTED (2026-09-30, open to change before merge)

**Context.** Doc 13 already has `prospects.view` and `prospects.manage` (Manager, Leasing Agent, Owner), and D-040 made a public vacancy link with WhatsApp and Call buttons. There was nowhere to keep the people who ask about a unit: who called, who viewed, who was promised which unit, and who became a tenant. The TODO item is a light CRM, not a full applications pipeline.

**Decision.**
1. **What a prospect is.** A name and phone (normalised like tenants), optional email, the property and optionally the unit they asked about, how they found us (walk-in, phone, WhatsApp, vacancy link, referral, sign board, online, other), an optional budget, move-in date and "looking for", who handles them, and a follow-up date. The same phone may be an open prospect twice at one property only after a warning.
2. **Status follows what happened**, staff never pick it: New (not reached yet), Contacted (a note marked "I spoke to them"), Viewing booked, Viewed, Unit held, then Became a tenant or Lost. Cancelling a viewing or releasing a hold moves it back. Lost needs a reason (price, unit, location, took another place, no response, not suitable, other with a note) and can be reopened.
3. **Viewings.** A unit of the property, a time between a week ago and 90 days ahead, and optionally who shows it (told in the app). Each is settled as Viewed, Did not come or Cancelled, with an optional comment.
4. **Holding a unit.** One available unit can be held for one prospect until a date at most 30 days ahead. The unit's status becomes Reserved, so it drops out of vacancies and the vacancy link says it is no longer available. The hold ends when the prospect is lost, when it is released by hand, when a lease on the unit is activated, or when someone changes the unit status on the unit page (the new status stays). Nothing is released automatically when the date passes: it becomes a home task to extend or release. This lets a Leasing Agent reserve a unit through a prospect without `units.set_status`.
5. **Becoming a tenant** needs `tenants.manage` too. It creates the tenant from the name, phone and email, or links a tenant the member can see with the same phone, then opens the draft lease for the held unit (or the one asked about) with the tenant filled in, when the member can draft leases there. The hold stays until that lease is activated.
6. **Enquiries from the vacancy link.** While the unit is available, the public page also has a short form (name, phone, optional message) with a privacy line. It makes a prospect with source "vacancy link" for whoever shared the link, if they still work on prospects there, or unassigned otherwise. The same phone asking again about the same property adds to the open prospect instead of a new one. At most 5 enquiries per IP an hour; a hidden field catches bots, which see a normal thank-you.
7. **Notifications (in app, staff):** a new enquiry (to the handler, or everyone with `prospects.manage` on the property when there is none), a prospect assigned to you, and a viewing you are to show.
8. **Pages.** "Prospects" in the nav: open prospects first, filters (property, status, mine, to call, name or phone, unit), counts (open, to call, closed in 90 days and the share who became tenants) and viewings in the next 7 days. One prospect: details, viewings, hold, handler, history, note with follow-up date, lost and reopen. The unit page shows who it is held for and links to its prospects. Home tasks for `prospects.manage`: prospects to call, viewings today, holds past their date.
9. **Scope and audit.** Like other property data: out of scope is a 404; an archived property's prospects are read only. Every change is audited, and the unit status changes made by a hold are audited as `unit.status` naming the prospect.
10. **Not in this step:**
    - SMS or WhatsApp to prospects from the app;
    - applications, references and screening;
    - listing on property websites;
    - deleting old lost prospects automatically **[VERIFY with ODPC guidance how long enquiry data may be kept]**;
    - CSV export;
    - the lease PDF and move-out statement (the rest of the TODO item).

### D-073 — Lease agreement PDF and move-out statement — ACCEPTED (2026-09-30, open to change before merge)

**Context.** Doc 16 moved "lease PDF generation and the move-out statement" into Tier 2. Landlords retype every lease into Word, and at move-out they work out what the tenant owes and what comes back from the deposit in a notebook. That is where most disputes start. We already have the lease terms, the rent ledger, the deposit sub-ledger with its clearance statement (doc 14 A1) and move-out condition reports that deductions can cite (D-047). Doc 14 B10 warns that agreement templates need a lawyer's review.

**Decision.**
1. **Lease agreement PDF.** An A4 PDF drawn with ReportLab, like letters (D-048), from the lease record only:
   - the landlord (organization, with its KRA PIN when set) and each tenant (name, phone, ID and KRA PIN);
   - the premises (property, address, unit and its label);
   - the term (start and end, or "periodic, until ended by notice");
   - rent per month and per invoice, billing frequency, due day and grace days, and rent changes already agreed for later dates;
   - recurring charges in force, the deposit, the notice period, and other phones allowed to pay;
   - the lease's own "terms and notes" text;
   - signature and date lines for the landlord, each tenant and a witness.

   We add **no legal clauses of our own**. The PDF says it was prepared from the lease record and that the parties should have their agreement reviewed **[VERIFY: a lawyer-reviewed standard clause set before we offer one]**. A draft lease prints "DRAFT – not yet in force" and has no lease number.
2. **Who can print what.** Anyone with `leases.view` who can see the lease. ID numbers and KRA PINs print only for members with `tenants.view_sensitive`. For others the lines are left blank to fill in by hand.
3. **Move-out statement.** Available when a lease has had notice given, or has ended or been terminated. A renewed lease is not a move-out. It needs `invoices.view` on the property. It is one page and a PDF showing the facts as at today:
   - The tenancy: tenants, unit, lease number, start date, date notice was given, and the move-out date (the day it ended, or "expected by" under notice).
   - **Rent account**:
     - charges (the opening balance plus invoices, less voids);
     - adjustments and corrections;
     - payments (less reversals);
     - deposit applied to the balance;
     - the resulting balance, owed or in credit;
     - the invoices still unpaid, oldest first.
   - **Deposits**, per type as in the clearance statement: received, each deduction with its date and reason (and the move-out report it cites), refunded, transferred, and still held.
   - **Condition**: the completed move-out report's date and how many items were worse than at move-in, or "no move-out inspection recorded".
   - **Settlement**: deposit still held, less the balance owed (or plus the credit). This gives "due to the tenant", "owed by the tenant", or "nothing due either way".
   - Signature lines for the landlord and the tenant.
4. **The statement moves no money.** Deductions, applying a deduction to the balance, and refunds stay on the Statement and deposits page, with their own capabilities and audit. The statement links there.
5. **Generated on demand, kept on request.** Both PDFs are drawn fresh each time, because the lease and ledgers change. "Save a copy to documents" (with `documents.upload`) stores the PDF on the lease through the documents app (D-071):
   - The agreement is filed as "Lease agreement". The statement is filed as "Notice or letter" and titled with the day.
   - The member may share the copy with the tenant in the same step when they have `documents.share_with_tenant`.
   - A saved copy is frozen and audited like any upload. The usual flow is print, sign, and upload the signed scan.
6. **Pages.**
   - The lease page gets a "Paperwork" card: "Agreement PDF", and "Move-out statement" once it applies.
   - The deposit clearance on Statement and deposits links to the move-out statement.
   - Out of scope is a 404. A missing capability hides the link, and a direct request gets a 403 or 404 as elsewhere.
7. **Not in this step:**
   - editable agreement templates and standard clauses;
   - e-signature (legal validity **[VERIFY]**);
   - final meter readings on the statement;
   - sending the statement to the tenant by SMS or email;
   - a verification code on the PDFs like letters;
   - a tenant download in the portal other than through a shared copy.

### D-074 — Maintenance targets and supplier performance (vendor SLA) — ACCEPTED (2026-09-30, open to change before merge)

**Context.** D-068 gives each maintenance request a due time from its priority (24 hours, 3, 7 or 30 days, fixed in code), keeps the time of each step, and shows overdue jobs. Doc 16 item 10 and the Tier 2 list ask for vendor SLA timers: someone should be on the job quickly, it should be fixed in time, and the landlord should see which suppliers and staff keep to that. Missing today: a target for starting the work, the organization's own targets, a measure of who meets them, and an alert when a job goes late.

**Decision.**
1. **Two targets per request.**
   - **Respond by**: the work has started ("Start work"; a job marked done or closed without being started counts as responded then).
   - **Fix by**: the existing due time; the job is Done (or Closed).
   - Defaults: Emergency 4 hours / 24 hours; High 1 day / 3 days; Normal 2 days / 7 days; Low 7 days / 30 days.
   - Targets are clock time. Nights, weekends, holidays and time on hold are not paused.
2. **The organization's targets.** Members with `organization.manage` set both targets for each priority at `/maintenance/targets/`, in hours or days: at least 1 hour, at most 365 days, and respond by no later than fix by. A priority not set uses the default. Changes apply to new requests and to later priority changes; times already set on requests stay. Audited.
3. **Setting the times.**
   - Both times are set from the targets when a request is reported.
   - A new priority resets both from now. Respond by is left alone once the work has started.
   - Moving the fix-by time by hand (D-068 item 2) is still allowed. It is counted on the request ("due time moved") as well as kept in the history and the audit, so extensions are visible on the performance page.
   - Existing requests get respond by = reported + the default for their priority.
4. **Met or missed.** For each target a request is:
   - **met**: responded or done by the time;
   - **missed**: responded or done after it, or still not responded or done with the time passed;
   - **pending**: not yet, and the time has not passed.
   Cancelled requests are not counted. A reopened job has no done time until it is done again (D-068 item 3), so it is measured on its final fix. The request page shows each time with "on time", "late by …" or "overdue".
5. **Performance** at `/maintenance/performance/` (needs `maintenance.view`; scoped to the member's properties). It covers requests reported in the last 30, 90 (default) or 365 days, optionally for one property, with one row per supplier and one per staff member. Each row shows:
   - jobs;
   - finished;
   - responded on time and fixed on time, as met out of met plus missed;
   - average hours to respond and average days to fix;
   - open now and overdue now;
   - reopened;
   - due time moved;
   - approved cost, only for `maintenance.costs` or `expenses.view` holders.
   Credit goes to whoever is on the request now; reassignments stay in the history. Each row links to the list filtered by that supplier or staff member. CSV with `reports.export`.
6. **List and CSV.** The list gains filters for "late to start", supplier and staff member. The summary shows the share fixed on time in the last 90 days, with a link to Performance. The CSV adds respond by, started, responded on time and fixed on time.
7. **Late alerts.** A new job `maintenance_alerts` runs every 15 minutes. When an open request passes its respond-by time without being started, or its fix-by time without being done, `maintenance_late` (in-app, staff) goes once to the assigned staff member and to the `maintenance.assign` holders on the property. Emergencies are urgent. The dedupe key includes the target and its time, so a moved due time can alert again. It must succeed within 1 hour.
8. **The supplier's SMS** (`maintenance_supplier_job`) now says when to attend and when to finish, so the supplier knows the targets. **[VERIFY the Swahili]**
9. **Not in this step:**
   - targets agreed per supplier or per kind of job;
   - business hours, holidays and pausing the clock while on hold;
   - supplier logins to confirm arrival;
   - SMS reminders to suppliers;
   - penalties or deductions from supplier payments;
   - the tenant rating the work.

### D-075 — Accounting export: journal and registers for the accountant — ACCEPTED (2026-09-30, open to change before merge)

**Context.** Doc 16 item 6 and the Tier 2 list in doc 12 ask for an accounting-friendly CSV export ("Standard CSV export now, QuickBooks/Zoho adapter later"). Accountants keep the landlord's books in QuickBooks, Zoho Books, Wave or Excel and today retype invoices, receipts and expenses from our pages. We have the pieces: the signed rent ledger (D-042), the deposit sub-ledger, invoices with lines by charge type, payments with receipts and money accounts, approved expenses (D-067) and payments to owners (D-058). There is no chart of accounts or double entry in the app yet (doc 14 D1), so the export makes one from these records.

**Decision.**
1. **One download: a ZIP of CSV files** at `/reports/accounting-export/`, for a range of whole months (at most 12, as the other finance reports) and all the member's properties or one of them. It needs `reports.view_financial` and `reports.export`, and only the member's properties are included. Each download is audited ("reports.accounting_export", with the months and property).
2. **`journal.csv`: double entry, accrual basis.** One balanced journal per event, one row per account, with the columns QuickBooks Online's journal import maps directly: Journal no, Date, Account, Debit, Credit, Description, Name, Property. The events and accounts:
   - **Invoice issued** (issue date): debit *Accounts receivable* with the invoice total; credit *Income - <charge type>* for each line, *VAT payable* for its tax, and *Deposit clearing* for deposit lines (a deposit is not income).
   - **Invoice voided** (void date): the same, reversed.
   - **Payment confirmed** (date paid): debit the money account, credit *Accounts receivable*. The money account is the payment account's name when set, otherwise the method (*M-Pesa*, *Bank*, *Cash*, *Other money account*; cheques go to *Bank*). Money not yet applied stays as a credit in receivables.
   - **Payment reversed** (reversal date): the same, reversed.
   - **Balance brought forward**: *Accounts receivable* against *Opening balance equity*. **Adjustments**: against *Rent adjustments*.
   - **Deposits** (deposit sub-ledger, by entry date):
     - received: debit *Deposit clearing*, credit *Tenant deposits held*;
     - deducted and kept: debit *Tenant deposits held*, credit *Income - Deposit deductions*;
     - deducted towards the balance: debit *Tenant deposits held*, credit *Accounts receivable*;
     - refunded: debit *Tenant deposits held*, credit *Money account not recorded*;
     - moved to a renewed or transferred lease: each side against *Deposit transfers*, which nets to zero.
   - *Deposit clearing* nets to zero when a billed deposit is also recorded as held. What is left shows deposits held from before go-live (debit) or deposits billed but not recorded as held (credit).
   - **Expense approved** (date paid): debit *Expense - <category>*, credit the method's money account. **Expense voided** (void date): reversed.
   - **Payment to an owner** (date paid): debit *Payments to owners*, credit the method's money account. **Voided** (void date): reversed. Only when the whole organization is in view, as in the cash flow (D-065).
   - **Corrections** (ledger and deposit REVERSAL rows) reverse the lines of the entry they cancel, on their own date.
   - Events are never rewritten: something voided later appears as a reversal in the month it was voided, so a closed month in the accountant's books stays closed. This differs from the cash-basis profit and loss, which leaves reversed payments and voided expenses out altogether.
   - Account names are fixed English strings so a mapping made once keeps working. Only charge type, category and payment account names come from the organization.
   - The journal's receivables for all time equal the rent ledger's balances.
3. **`accounts.csv`**: every account used, its suggested QuickBooks type (Accounts receivable, Income, Expense, Bank, Other current asset, Other current liability, Equity), and its debits, credits and net for the range: a trial balance of the period's movements.
4. **Registers**, for reconciling:
   - `invoices.csv`: issued in the range;
   - `payments.csv`: confirmed or reversed, paid in the range, with receipt number, money account and reversal date;
   - `expenses.csv`: approved or later voided, paid in the range, with supplier and KRA PIN.
5. **The page** shows the range and property filter, what each file holds, the account list, and a totals check (debits equal credits). It is linked from the finance report tabs.
6. **Not in this step**: direct QuickBooks or Zoho connections (doc 12 Tier 4), a chart of accounts the organization edits or maps in the app, management fee journals, owner-by-owner books, and eTIMS (D-037).
7. **Direct sync deferred until pilots ask (2026-10-04).** This export is the accounting integration for launch. A direct connection to QuickBooks Online, Zoho Books or ERPNext needs OAuth, the vendor's app review, account mapping, and handling of edits and reversals. That work would serve only the organizations using that one package, and Kenyan accountants use several (QuickBooks Online and Desktop, Sage, Zoho Books, ERPNext, Excel). During the pilot, each agency is asked which package it and its accountant use. A sync is built only for a package that several of them name, and data then flows one way, from the app into the accounting package. Until then, accountants import `journal.csv`.

### D-076 — Email to tenants — ACCEPTED (2026-09-30, open to change before merge)

**Context.** The TODO line "MRI reports, analytics, email to tenants" is mostly done: the income pack (D-050) covers MRI and the dashboards (D-051) cover analytics. Email is still missing. The EMAIL channel exists in the catalog and `delivery._send_email` exists, but no tenant type has email wording, so nothing is ever emailed. Kenyan tenants read SMS and WhatsApp first. Email matters for tenants who keep records, companies renting commercial units, and tenants abroad, and it can carry the invoice and receipt as PDFs, which an SMS cannot.

**Decision.**
1. **Email is a copy, not a step in the fallback chain.** For tenant types with email wording, the phone channels are tried as before (WhatsApp, then SMS), and an email copy is written as well when the tenant has an email address. A tenant who stopped SMS but has an email still gets the email.
   - The types: invoice issued, rent due soon, rent overdue, payment received, announcement and maintenance update. Owner, payer and supplier types stay SMS only.
   - No email address means no email row at all, so the log does not fill with skips. A stopped email is logged as skipped ("recipient opted out").
   - The copy has the event's dedupe key with `|email` added, so a rerun never sends it twice.
2. **Organizations switch it per type** with "Also email tenants who have an email address" on the notification settings page. It is stored as EMAIL in the rule's channels, and it is on by default. A rule must keep at least one phone channel.
3. **What the email holds.**
   - A subject from the catalog ("Invoice INV-2026-000123 from Green Villas"), stored on the message (`Message.subject`).
   - A plain-text body that keeps its line breaks. Organizations edit the email wording on the templates page like SMS wording, in English and Swahili ([VERIFY] the Swahili).
   - A footer that says why the tenant gets it, with a link to stop emails.
   - It is sent from `DEFAULT_FROM_EMAIL` under the organization's name. Replies go to the organization's reply-to address (`Organization.reply_email`, set on the notification settings page), when one is set.
4. **Attachments.**
   - Invoice issued: the invoice as a PDF, drawn when sent (new `billing/pdf.py`). Staff can download the same PDF from the invoice page, and tenants from their portal.
   - Payment received: the stored receipt PDF.
5. **Stopping.**
   - Each email carries a signed link `/e/<token>/`. It opens a page with one button that stops emails from that organization; the stop is recorded as a revoked email consent from the source "Email link".
   - The same URL takes the one-click POST of the `List-Unsubscribe` and `List-Unsubscribe-Post` headers, so mail apps show their own unsubscribe button.
   - Staff stop or resume email on the tenant page, like SMS.
6. **Failures.** A refused address is a permanent failure. Other SMTP errors retry like SMS. Email costs nothing from the SMS wallet and does not wait for quiet hours.
7. **Announcements.** The preview says how many tenants also get an email copy.
8. **Not in this step:** HTML email, bounce and complaint webhooks from a provider, emails to staff, emailing the lease agreement, move-out statement or good-standing letter from their pages, and tenants choosing email in the portal.

### D-077 — Daily arrears snapshots and arrears at month end — ACCEPTED (2026-09-30, open to change before merge)
The analytics step left open by D-051 item 2. Arrears over time is the one trend the source cannot rebuild as it was known then: a payment recorded late, or back-dated, changes every past day. The TODO line "MRI reports, analytics" is otherwise covered: the MRI estimate is D-050, the dashboard D-051. The eTIMS adapter still waits on D-037.
1. **`reports.DailySnapshot`**: one row per property per day, holding the overdue amounts in the four aging buckets (1–30, 31–60, 61–90, over 90 days; "not yet due" left out) and how many leases have money overdue. The figures come from the same FIFO aging as the arrears page and the dashboard (billing.selectors), through a new `organization_arrears`, which needs no member because a job calls it.
2. **Which properties**: every live property gets a row, with zeros when nothing is overdue, so a zero is not mistaken for a gap. An archived property gets one only while it still has arrears.
3. **The job `reports_daily`** runs at 23:45 (doc 18) for every organization that is not frozen, so each row is the day's closing figure. Running it again the same day replaces that day's rows. It is expected every 26 hours.
4. **Backfill**: `manage.py backfill_snapshots --days N [--org ID]` rebuilds past days from the ledger as recorded now: only entries dated up to each day count. These rows are marked `rebuilt` and never replace a row the job wrote on the day.
5. **Dashboard**: "Arrears at month end" shows the last recorded day of each of the 12 months, stacked by days overdue, with a table and the figures in the CSV export. It counts only the properties the member can see, or the one property chosen. A month with nothing recorded is shown as "Not recorded", not as zero. Months that include rebuilt rows are marked *.
6. **Kept for good.** The table is small: one row per property per day. Nothing is purged yet.
7. **Not in this step:** occupancy and collection snapshots (the source rebuilds them), an "as at date" view of the dashboard, and per-role dashboards.

### D-078 — Joint ownership: owners' shares of a property — ACCEPTED (2026-09-30, open to change before merge)
The last open part of the TODO line "Agency mode and joint ownership, custom roles per organization". Agency mode is D-053 and D-058 (owners, fees, statements, payments to owners, owners as Viewers); custom roles already exist (doc 13). A property could have only one owner.
1. **`properties.PropertyShare`** (property, owner, percent), unique per property and owner. A property with no shares is owned wholly by `Property.owner`, as before. With shares, the rows list every owner, the percentages add up to exactly 100, and `Property.owner` is one of them: the main owner. Existing data needs no migration.
2. **Setting them**: a new "Owners and shares" page on the property (`properties.manage`, in scope). Two or more rows adding up to 100 make the property jointly owned, and the largest share becomes the main owner (the first entered on a tie). One row at 100% makes a sole owner and removes the shares; no rows clear the owner. Each owner appears once, and each share is more than 0 and at most 100. The change is audited (`property.shares`) with the owners before and after.
3. **The property form** still picks the main owner. While there are shares, it only accepts one of the co-owners; to change who the owners are, use the shares page.
4. **The statement**: each co-owner gets a statement that includes the property. The lines, collected, fee and expenses are for the whole property. Due on the property is worked out as before (collected less fee and expenses), and the owner gets their percentage of it, rounded to the cent. The summary then shows "Due on the properties", "Less other owners' shares" and "Due to the owner"; each joint block shows the share, and a note explains it. The PDF shows the same. The saved send and the SMS carry the owner's due.
5. **Payments to owners stay per owner and month** (D-058), against that owner's due. Rounding can leave a cent between the co-owners' parts and the whole; it is not assigned to anyone.
6. **Who counts as an owner of a property** now includes co-owners everywhere: the statement owner choices, "sees all of the owner's properties" (which gates payments and sending), statements to send on the home page, and the property counts on the Owners page.
7. **Not in this step:** shares that change within a month (the shares at the time the statement is drawn apply to the whole month), fees that differ per owner, per-owner tax (for example withholding on rent to non-residents), and co-owner history.

### D-079 — Archived leases: finding them, restoring them, and bringing them back when money moves — ACCEPTED (2026-10-01, open to change before merge)
The last open part of the TODO line "Archive instead of delete". The daily job already archives a closed lease the day after it ends once it is settled: nothing owed either way, no deposit held and its last month billed (`billing.jobs.archive_settled_leases`, audited `lease.archive`). An archived lease then left the lease list, the unit page and search, and nothing could bring it back.
1. **Finding them.** The lease list gets "Show archived leases" (`?archived=on`), with the same search and status filter. The unit page lists every lease of the unit, archived ones included, so its history stays whole; the tenant page already did. An archived lease shows an "Archived" badge next to its status. Search still leaves archived leases out (D-054).
2. **The lease page** of an archived lease says when it was archived and by whom ("the daily job" when it was automatic) and that it is out of the working lists. Recording payments, M-Pesa requests and deposit actions stay closed while it is archived, as before.
3. **Restoring by hand.** Members with `leases.terminate` on the property can restore it (`leases.restore_lease`, audited `lease.restore`), for example to record a late payment, a refund or a correction. A lease restored by hand is left alone by the daily job for 30 days, counted from its latest `lease.restore` event, and is archived again after that if it is still settled.
4. **Coming back when money moves.** If an archived lease is no longer settled (a payment reversed, an invoice voided, a deposit entry reversed), the daily job restores it, audited `lease.restore` with source "daily job" and the reason, so it shows again in the lists and arrears work. Arrears and statements already read archived leases.
5. **No new capability and no migration.** Archiving by hand is not added: the job archives the day after a lease ends, once settled.
6. **Not in this step:** archiving drafts (drafts are deleted), archiving leases that ended before go-live with an unknown balance, and a bulk restore.

### D-080 — Installable app (PWA) — ACCEPTED (2026-10-01, open to change before merge)
The first part of the TODO line "API, mobile app, USSD, AI assistant". D-010 said the web app is delivered as a PWA, and doc 07 lists the installable PWA as the first mobile surface, before USSD, a native app and a public API. Until now there was no manifest and no service worker.
1. **The manifest** is served at `/manifest.webmanifest` (application/manifest+json) from a view, so its URLs follow the URL config. Name "NyumbaPMS", start URL `/?source=app`, scope `/`, display standalone, portrait-friendly, theme and background colours matching the site, and PNG icons at 192 and 512 pixels (normal and maskable) plus an Apple touch icon, kept under `static/icons/`. Every page links it and carries the theme colour and Apple web app tags.
2. **The service worker** is served at `/sw.js` (scope `/` needs it at the root) with `Cache-Control: no-cache`, so a new version is picked up on the next visit. Its cache name carries a version taken from a hash of its own text and the files it lists, so a deploy that changes them replaces the old cache.
3. **What is cached: no personal or money data.** The worker caches only the site's own static files (CSS, JavaScript, icons), the Bootstrap files from the CDN and the offline page. Pages, JSON, PDFs, uploads, receipts and anything that is not a GET always go to the network and are never stored, because a shared phone may be used by someone else after logout. When a page cannot load because there is no connection, the offline page is shown.
4. **The offline page** (`/offline/`, no login) says there is no connection, that nothing was saved on the phone, and offers to try again. It shows no names, balances or other data.
5. **Installing.** Browsers that offer installation (Chrome and Edge on Android and computers) show an "Install the app" item in the menu, which opens the browser's own prompt; it disappears once installed or when running as the app. On iPhone, the help explains Share, then Add to Home Screen. The same app serves staff and tenants: the start URL sends each to their own home.
6. **Not in this step:** working offline with data (for example meter readings entered without a connection and sent later), push notifications, a native app and app store listings, the public API, USSD and the AI assistant.

### D-081 — USSD menu for tenants: balance, how to pay, last payment — ACCEPTED (2026-10-01, open to change before merge)
The next part of the TODO line "API, native mobile app, USSD, AI assistant". Doc 02 notes many tenants use feature phones, and doc 07 lists a USSD menu (balance, receipt) as the next surface after the installable app. Africa's Talking already carries our SMS, so it carries USSD too.
1. **One platform code** (for example `*384*123#`), shared by every organization like the SMS sender ID. It is set in `USSD_CODE` and only shown in help and on the tenant portal; the code itself is bought from Africa's Talking **[VERIFY cost and the code before launch]**. Africa's Talking posts each step to `/hooks/ussd/africastalking/<token>/` with the same secret token as the SMS hooks (`AT_CALLBACK_TOKEN`); a wrong or missing token is a 404. The reply is plain text starting `CON` (more to come) or `END`, at most 182 characters per screen.
2. **Who is calling.** The network gives the caller's number, so no PIN is asked in this step: the same trust as an SMS to that number. The number is matched to tenants' main phone only (not the other phone, which may be a relative), tenants not archived, in organizations that are operating. Their leases count if issued and not archived, and not renewed (the renewal carries on). A number with no lease gets "This number is not registered" and the advice to ask the landlord. Payers on a lease (D-016) who are not tenants get nothing.
3. **The menu.** With more than one lease the first screen lists them (property and unit, at most 5). Then, for the chosen lease: 1 Balance, 2 How to pay, 3 Last payment.
   - **Balance**: what is owed (or "nothing owed" when in credit or settled), what is overdue, and the next amount due and its date.
   - **How to pay**: the property's payment accounts (as on the payment form) with the unit's payment reference, at most two: Paybill number and account, Till number, or bank name and number.
   - **Last payment**: the amount, date and receipt number of the latest confirmed payment.
4. **Language**: the tenant's message language (English or Swahili). With several leases whose tenant records differ, English is used for the list. The Swahili text **[VERIFY with a native speaker]**.
5. **Limits and privacy**: at most 30 steps per number per hour, after which the menu ends with "Try again later". Nothing is stored about the session. Names of other tenants are never shown. A wrong choice ends the session with "Invalid choice".
6. **Not in this step:** a PIN, paying from the menu (sending an M-Pesa prompt), reporting a repair, SMS copies of statements or receipts, menus for staff and caretakers, and a separate code per organization.

### D-082 — Read-only API v1 with organization API keys — ACCEPTED (2026-10-01, open to change before merge)
The next part of the TODO line "API, native mobile app, AI assistant". Doc 07 puts the public API before a native app ("only after the API is stable"), and agencies and accounting tools want to read rents and payments without exports. This step is **read-only**: it reads the same data as the pages through the same permission check (doc 13).
1. **No new dependency.** Doc 07 names Django REST Framework, but a read-only API with six lists needs only plain Django views returning JSON, as the SMS and WhatsApp clients use `urllib`. DRF and an OpenAPI schema can come with writes. The endpoints are described in the help page instead.
2. **API keys** (new app `api`, model `ApiKey`). The owner (or anyone with `organization.manage`) creates a key on *Staff → API keys* with a name, the data it may read (any of: properties and units, tenants, leases, invoices, payments) and an optional expiry (30, 90 or 365 days, or none).
   - **Showing the key:** the key `lpk_<prefix>_<secret>` is shown **once**. Only its SHA-256 hash and the 8-character prefix are stored. The secret is 32 random URL-safe characters, so a plain hash is enough (no slow hash needed).
   - **Limit:** at most 10 live keys per organization.
   - **Revoking:** a key can be revoked at any time. Creating and revoking are audited (`api_key.create`, `api_key.revoke`).
3. **Whose rights.** A key acts as the staff member who made it and never with more rights than the scopes it was given. Each list also needs that member's own capability (`properties.view`, `units.view`, `tenants.view`, `leases.view`, `invoices.view`, `payments.view`), and only their properties are shown. A key stops working at once when it is revoked or expired, or when its maker:
   - leaves or is deactivated;
   - loses `organization.manage`;
   - belongs to an organization that is frozen or archived.
   A read-only (lapsed) organization can still read.
4. **Requests.** `GET /api/v1/<resource>/` with `Authorization: Bearer <key>`. The resources are `me`, `properties`, `units`, `tenants`, `leases`, `invoices` and `payments`.
   - **Format:** JSON. IDs are the public UUIDs, money is a string (`"15000.00"`, doc 11 §25), and dates are ISO.
   - **Paging:** pages of `limit` (default 50, at most 200), with a signed `next_cursor` to pass back as `cursor`. A sync can ask for `updated_since=<ISO date-time>`.
   - **Errors:** `{"error": {"code", "message"}}` with 401 (missing, wrong, revoked or expired key), 403 (scope or permission), 400 (bad parameter), 404 and 429.
   - **Excluded:** archived properties, units, tenants and leases are left out; invoices and payments on archived leases stay, as on the pages. Tenants' sensitive fields (ID number, KRA PIN, emergency contact, notes) are never sent. Drafts are left out of invoices, and invoice lines are not included in this step.
5. **Limits.** 120 requests per key per minute, then 429 with `Retry-After`. `last_used_at` is updated at most once a minute. Each request is not audited, but the key page shows when it was last used. The API sends `Cache-Control: no-store` and does not use cookies or CSRF.
6. **Not in this step:** writing through the API, webhooks, OAuth for third-party apps, keys per staff member below owner level, an OpenAPI schema, the native app, offline data entry and push notifications.

### D-083 — Webhooks: tell another program when money or leases change — ACCEPTED (2026-10-01, open to change before merge)
The second half of doc 07's "Public API + webhooks". With D-082 a program can read; with webhooks it no longer has to poll. A confirmed M-Pesa payment can reach an accounting tool within minutes.
1. **Events come from the audit trail.** Every change that matters is already an audit event (doc 14). A job reads new audit events and turns them into webhook deliveries, so no service has to remember to send one:

   | Webhook event | Audit actions |
   |---|---|
   | `payment.confirmed` | `payment.confirm` (by hand, M-Pesa, bank or STK) |
   | `payment.reversed` | `payment.reverse` |
   | `invoice.issued` | `invoice.issue` (by hand and by the monthly job) |
   | `invoice.voided` | `invoice.void` |
   | `lease.activated` | `lease.activate` |
   | `lease.ended` | `lease.end`, `lease.terminate`, `lease.renewed`, `lease.transferred` |
   | `tenant.created` | `tenant.create` |
   | `tenant.updated` | `tenant.update` |

   An endpoint only gets events from after it was added: there is no backfill.
2. **Endpoints.** The owner (or anyone with `organization.manage`) adds an endpoint on *Staff → Webhooks*, giving an `https://` URL and the events it wants. There are at most 5 per organization.
   - **Who sends.** Like an API key, an endpoint sends with its maker's rights. It is paused while the maker cannot manage the organization or the organization is frozen. Each event also needs the maker's view capability for that kind of data, for example `payments.view`.
   - **The secret.** Each endpoint has a signing secret `whsec_…`, shown once and stored encrypted with the field key (doc 06), since it is needed again to sign.
   - **Managing it.** An endpoint can be paused, resumed, sent a test event (`ping`) or deleted.
3. **What is sent.** A POST of JSON `{"id", "type", "created_at", "data": {"object": {...}}}`.
   - **The object** is the resource as the API returns it (D-082), read when the delivery is first made: the payment, invoice, lease or tenant, never tenants' sensitive fields. If the object can no longer be read, `object` is null.
   - **Headers:** `X-LPMS-Event`, `X-LPMS-Delivery` (the delivery's UUID, the same on every retry) and `X-LPMS-Signature: t=<unix time>,v1=<hex HMAC-SHA256 of "<t>.<body>" with the secret>`. Receivers should check the signature, reject times more than 5 minutes old, and ignore a delivery UUID they have seen.
4. **Sending.** The `send_webhooks` job runs every 5 minutes, next to `send_due_messages`. It picks up audit events once they are two minutes old, so a slow transaction that commits late is not skipped. While an endpoint is paused its events still queue up and go out when it is resumed, unless they are older than 30 days. A 2xx answer within 10 seconds counts as delivered; anything else is retried after 1 minute, 5 minutes, 30 minutes, 2 hours, 6 hours and 12 hours, then marked failed.
   - **Auto-pause:** after 25 failures in a row without a success, the endpoint is paused and the page says why.
   - **Redeliver:** a failed delivery can be sent again by hand.
   - **Clean-up:** deliveries older than 30 days are deleted.
5. **Safety.** Only `https://` URLs on the standard port are accepted. The host must resolve only to public addresses (no private, loopback, link-local or reserved ranges), checked when the endpoint is saved and again before each send. Redirects are not followed, and at most 1 KB of the answer is read and kept. Development may allow `http://` and private addresses with `WEBHOOKS_ALLOW_PRIVATE`; production refuses that setting.
6. **Not in this step:** events for maintenance, meters, expenses and messages, payloads with invoice lines, choosing properties per endpoint, writes through the API, and per-event retries chosen by the receiver.

### D-084 — Pay rent from the USSD menu with an M-Pesa prompt — ACCEPTED (2026-10-01, open to change before merge)
The next part of the TODO line "USSD PIN, paying and repairs from USSD". With D-081 a tenant on a feature phone can see what they owe. With this step they can pay it from the same menu. The M-Pesa prompt (STK push, D-045 item 8) works on feature phones as well.
1. **Menu item 4, "Pay with M-Pesa".** It is shown only when the lease can take payments the same way as a payment link (D-046 item 1): active, or ended with money still owed, the organization operating, and a Paybill with a passkey that collects for the property.
2. **The steps.**
   - The first screen shows what is owed, if anything, and asks for an amount.
   - The tenant types any whole amount from 1 to 250,000 shillings. Paying in parts is fine; nothing is suggested or forced.
   - A confirmation screen shows the amount and the house: 1 Confirm, 2 Cancel.
   - Confirming ends the session with "You will get an M-Pesa prompt on this phone. Enter your M-Pesa PIN to pay."
3. **Which phone.** The prompt goes only to the number that dialled, never to a number typed in. The network vouches for that number (D-081 item 2), and the prompt needs that phone's own M-Pesa PIN. So no separate USSD PIN is needed to pay: someone else holding the phone could at most pay from its owner's M-Pesa, which needs the PIN anyway.
4. **When it is sent.** A phone cannot show the M-Pesa prompt while a USSD session is still open. So the prompt is sent just after the closing screen has gone back to Africa's Talking: the reply is sent first, then the prompt **[VERIFY on a real phone that the prompt appears; if it is too soon, add a short delay]**. If Safaricom refuses the request, the tenant gets no prompt, and the failed request shows on the lease like any other.
5. **Limits.** The existing rule still applies: no second prompt to the same phone for the same lease within 2 minutes. On top of that, there are at most 5 prompts a day from USSD to one phone. When a limit is reached, the confirm step ends with a message saying so, and nothing is sent.
6. **Records.** The request is an `StkRequest` marked as started from USSD, with no staff member. It is audited like payment-link requests (`via: ussd`), and the payment is confirmed on the lease when Safaricom's callback arrives, as for every prompt.
7. **Not in this step:** a USSD PIN for balance and history, reporting a repair, choosing between several Paybills, and a text message when the payment is received (the existing payment SMS already does that).

### D-085 — Report a repair from the USSD menu — ACCEPTED (2026-10-01, open to change before merge)
The next part of the TODO line "USSD PIN and repairs from USSD". Tenants with a portal account can report a repair there (D-068 item 7). A tenant on a feature phone could only call the office. With this step they can report it from the USSD menu and get the same text-message updates.
1. **Menu item "Report a repair".** It is shown only for an active lease. The extra items are numbered after the first three: "4. Pay with M-Pesa" when paying is offered, then "Report a repair" as 5, or as 4 when paying is not offered. The numbers are worked out again at every step from the same rules, so they stay the same within a session.
2. **The steps.**
   - Choose what is wrong: 1 Water or plumbing, 2 Electricity, 3 Doors and locks, 4 Roof and walls, 5 Security, 6 Other. These map to the request kinds of D-068.
   - Describe it in a few words, up to 100 characters, or type 0 to skip. A description with a "*" cannot be told apart from the next step, so it is refused with "Invalid choice".
   - Answer "Is it an emergency?": 1 Yes, 2 No. A yes makes the request an emergency, as the portal's emergency box does.
   - The session ends with "Thank you. Your repair number is MNT-…. We will send you updates by SMS."
3. **Who reported it.** The request belongs to the lease and to the tenant whose main phone dialled, and its source is "USSD". No user account is behind it, so the reporter and the first history line have no user. Staff see "Tenant, from USSD", the way portal reports show "from the portal". The network vouches for the number, as in D-081 item 2. The worst a stranger holding the phone could do is report a repair, which staff can cancel, so no USSD PIN is asked for this either.
4. **Same handling as the portal.**
   - Staff who assign repairs are notified, urgently for an emergency.
   - The tenant gets the "received" message and later updates by SMS or WhatsApp (D-068).
   - Respond-by and fix-by times follow the organization's targets (D-074).
   - There are no photos.
5. **Limits.**
   - At most 5 reports a day from USSD for one lease, the same number as the portal (`MAX_PORTAL_PER_DAY`).
   - The same kind and description from the same lease within 10 minutes gives back the request already made, not a second one. This covers a gateway that sends the last step twice.
6. **Not in this step:** a USSD PIN, following or commenting on a repair from USSD (the SMS updates cover following), common-area reports, and photos.

### D-086 — An optional PIN for the USSD menu — ACCEPTED (2026-10-01, open to change before merge)
The last USSD part of the TODO line. D-081 opens the menu to whoever holds the tenant's main phone. That covers paying (D-084: the M-Pesa PIN still protects the money) and repairs (D-085: the worst case is a report staff can cancel). It leaves the balance, the last payment and the receipt number open to a family member or anyone else holding the phone. Some tenants will want that closed; most will not want another number to remember.
1. **Off unless the tenant turns it on.** Item "0. Set a PIN" is on every lease menu. It is numbered 0 so the other items keep their numbers. The steps:
   - Enter 4 digits.
   - Enter them again.
   - The session ends with "PIN set. You will be asked for it every time."
   - A PIN that is all one digit (1111) or a run (1234, 4321) is refused, and so is a second entry that does not match.
2. **One PIN per phone number**, not per landlord. One phone dials one menu, which lists houses from every landlord the number rents from (D-081 item 2), so the PIN covers them all. It is stored as a keyed hash of the phone and the PIN, never as the PIN itself.
3. **Asked first.** With a PIN set, the first screen is "Enter your PIN:", before the house list. Africa's Talking sends everything typed so far at every step, so the PIN is the first part of every step's text and is checked again at each one. Nothing is stored between steps. Pay and repair are behind the PIN as well.
4. **Wrong PINs.**
   - A wrong PIN ends the session with "Wrong PIN."
   - 5 wrong PINs in a row lock the menu for that phone for an hour: "Too many wrong PINs. Try again in an hour, or ask your landlord to remove the PIN."
   - A right PIN resets the count.
   - With 10,000 possible PINs, 5 tries an hour, and the 30-steps-an-hour limit of D-081 on top, guessing is not practical.
5. **Changing and removing.** With a PIN set, item 0 is "PIN", with "1. Change PIN" and "2. Remove PIN". The tenant has already typed the PIN to get there. Changing takes the same two entries as setting.
6. **Forgotten PIN.** Staff who can manage portal access (`tenants.invite_portal`) see "USSD PIN: on" on the tenant page and can remove it. This clears the PIN for the phone number, so it also affects another landlord who rents to the same number. Removing only makes the menu as open as before, and whoever uses it still needs the phone itself, so this is accepted. It is audited in the staff member's organization. The tenant can set a new PIN from the menu.
7. **What it does not cover.** The PIN does not protect text messages, the portal (which has its own login) or M-Pesa prompts sent by staff. The PIN does pass through Africa's Talking in the text of each step. We never log the text.

### D-087 — API writes: record payments, add and update tenants — ACCEPTED (2026-10-01, open to change before merge)
The first part of the TODO line "API writes; native mobile app; AI assistant". D-082 made the API read-only. The writes asked for most are the ones an accounting tool or a bank integration needs: put money received elsewhere on a lease, and keep the tenant list in step with another system. Everything else (leases, invoices, expenses, repairs) stays read-only for now.
1. **Three writes, through the same services as the pages.**
   - `POST /api/v1/payments/` with `lease_id`, `amount`, `method` (`MPESA`, `CASH`, `BANK`, `CHEQUE` or `OTHER`) and optional `paid_at`, `reference` and `tenant_id`. It goes through `record_payment`: it is confirmed and receipted at once if the key's maker may confirm, else it waits for review as from the page. The receipt SMS and webhooks follow as usual.
   - `POST /api/v1/tenants/` with `name`, `phone` and optional `kind`, `contact_person`, `alt_phone`, `email` and `language`.
   - `PATCH /api/v1/tenants/<id>/` with any of those fields.
   - The sensitive fields (ID number, KRA PIN, emergency contact, notes) cannot be written, as they cannot be read. An unknown field is refused with 400 rather than ignored, so a typo does not pass silently.
   - `GET /api/v1/<resource>/<id>/` reads one item, so a write can be checked afterwards.
2. **Write scopes.** A key gets two more choices under "May change": *record payments* and *add and update tenants*. Each also gives the matching read scope, because a write answers with the item. Existing keys stay read-only. The maker's own capabilities still apply (`payments.record`, `tenants.manage`, and their properties), and a read-only (lapsed) organization can still read but not write.
3. **No double payments on a retry.** `POST /payments/` needs an `Idempotency-Key` header (1 to 100 characters). The first successful answer is kept for 24 hours per key. Sending the same `Idempotency-Key` again with the same body returns that answer without recording again. With a different body it returns 422 `idempotency_mismatch`. Writes by one key are taken one at a time, so two retries arriving together cannot both record. Failed requests are not kept, so the caller can fix the body and retry with the same key. The header is optional for tenant writes; when it is sent, it works the same way.
4. **Requests and answers.** The body is JSON (`Content-Type: application/json`, at most 16 KB). A create answers 201 and an update 200, with the item as the list endpoints show it. Errors use the D-082 shape, plus 422 `invalid` with a `fields` object for checks the page would show on the form (e.g. a wrong phone number or an unactivated lease). A missing scope or capability is 403, and a lease or tenant the maker cannot see is 404.
5. **Audit.** Writes are audited by the same services as on the pages, in the maker's name. Each event also records which key made it (`api_key`), so the activity log shows that a change came from the API.
6. **Not in this step:** writing leases, invoices, expenses or repairs; allocating a payment to chosen invoices (it is spread oldest first, as on the page); bulk endpoints; OAuth; an OpenAPI schema.

### D-088 — Meter rounds that work without a connection — ACCEPTED (2026-10-01, open to change before merge)
The "offline data entry" part of the TODO line "Native mobile app; AI assistant; offline data entry and push notifications in the app". Doc 14 C1 and doc 16 (item 16, "Build") ask for an offline queue for caretakers, and D-080 named meter readings as the first case. Meter rooms and basements often have no signal, and today a round typed there is lost when "Save readings" fails. A native app, push notifications and the AI assistant stay for later. Doc 07 puts the native app after a stable API, which D-082 and D-087 have only just started.
1. **What works offline: the meter round page once it is open.** The caretaker opens *Read meters* for a property while connected, then walks the meters. If "Save readings" cannot reach the server, the round is kept on the phone instead of being lost: "No connection. The readings are kept on this phone and will be sent when you are back online."
   - Pages are still never cached (D-080 item 3), so the round page itself cannot be opened for the first time without a connection.
   - Payments and repairs are not queued in this step. A payment entered twice is worse than a reading entered twice, and the M-Pesa and USSD paths already cover most payments.
2. **What is kept on the phone:** the reading date, and for each meter filled in its id, label, value, note and "new meter" tick, in the browser's own storage for this site. No names, balances or tenant data are kept. Photos are not kept: they are large, and they are optional. A photo chosen for a queued round is dropped, and the page says so.
3. **Sending.** Every page of the app tries to send kept rounds when it loads and when the connection comes back. A kept round is sent as JSON to `POST /meters/round/<property>/offline/`. It goes through the same check as the form (`record_round`: all or nothing, `meters.record` on that property, dates and values checked).
   - Each round carries an id made on the phone. The server keeps `meters.OfflineRound` (user, id, property, when it was entered, how many readings), unique per user and id, so a round sent twice (for example the answer was lost) is recorded once.
   - The readings are audited as usual, and an extra `meters.offline_round` event notes when the round was typed on the phone.
4. **Whose round.** A kept round belongs to the person who typed it. It is sent only while that person is logged in, so a shared phone does not send one person's round as another's. Logging out with rounds still waiting asks for confirmation, because they will only be sent when that person logs in again on this phone.
5. **When the server refuses it.** If a reading is refused (for example someone recorded that meter in the meantime, or the date is now before the last reading), nothing in that round is recorded (all or nothing, as in the form). The round stays on the phone marked "Needs fixing", with the server's reasons. On the round page the caretaker can put it back into the form to correct and send, or discard it. A round sent while logged out, or refused by an expired security token, simply waits for the next try.
6. **Seeing what is waiting.** The round page lists the rounds kept on this phone for that property: when they were typed, how many readings, and whether they are waiting or need fixing. The menu shows a small "N waiting to send" badge while anything is kept.
7. **Not in this step:** opening pages offline, other forms offline (payments, repairs, inspections), photos offline, background sync while the app is closed, push notifications, a native app.

### D-089 — Alerts on staff phones (Web Push) — ACCEPTED (2026-10-01, open to change before merge)
The "push notifications in the app" part of the remaining TODO line. Staff alerts (a payment waiting for review, an unmatched M-Pesa payment, a repair reported or assigned, a new enquiry) go only to the bell today, so they are seen only when someone opens the site. The installed app (D-080) can show phone notifications through the browser's Web Push, with no native app and no app store.
1. **What is pushed: staff in-app notifications only.** When `notify` writes an in-app message for a staff member, it is also pushed to the devices where that person turned alerts on. Tenants are not pushed: they get SMS, WhatsApp and email as before. The bell stays the record; a push is a nudge and nothing is lost if it never arrives.
2. **No details on the lock screen.** The push carries the kind of alert as the title ("Payment waiting for review") and "Open NyumbaPMS to see it." as the text, plus a link to the notifications page. No names, phone numbers, amounts or units, because a phone's lock screen is seen by others. Push payloads are still encrypted end to end (RFC 8291).
3. **Opting in, per device.** On the notifications page: "Alerts on this phone: Turn on / Turn off". Turning on asks the browser's permission and registers the device. The page also shows how many devices have alerts on, with "Turn off everywhere". Logging out turns alerts off for that device, so a shared phone does not show one person's alerts to the next.
4. **Quiet hours.** During the organization's quiet hours nothing is pushed unless the alert is urgent; the bell still has it.
5. **Sending.** Pushes go after the change is saved, at most 10 devices per person, with a short timeout. Alerts written by bulk jobs are pushed by `send_due_messages`. An alert older than one hour is not pushed. A device the push service says is gone (404 or 410) is removed; one that fails 5 times in a row is removed.
6. **Safety.** A device address must be `https` on a known push service (Google, Mozilla, Microsoft, Apple; `PUSH_ALLOWED_HOSTS` adds more), so the server cannot be made to call other addresses. The keys are VAPID keys in settings (`VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT`; `manage.py vapid_keys` makes a pair). Without keys, alerts are not offered. Done with the `cryptography` library already installed; no new dependency.
7. **Not in this step:** pushes to tenants, choosing which alert types to push (the in-app opt-outs already apply), grouping several alerts into one, a native app.

### D-090 — Reporting a repair without a connection — ACCEPTED (2026-10-01, open to change before merge)
The next part of "more forms offline (payments, repairs)". Caretakers find problems in places with poor signal (basements, stairwells, water tanks), and a report that fails to save is lost today. It works like the meter round (D-088), with the same rules.
1. **What works offline: the "Report a problem" page once it is open.** If "Report" cannot reach the server, the report is kept on the phone: "No connection. The report is kept on this phone and will be sent when you are back online." Pages are still never cached (D-080).
2. **What is kept:** the property and unit ids with their names as shown in the form, what is wrong, the details, the kind, the priority and the "reported by the tenant" tick. Photos are not kept, and the page says so; they can be added on the request page later.
3. **Sending.** Kept reports are sent with kept rounds: when any page loads and when the connection comes back, as JSON to `POST /maintenance/new/offline/`. They go through the same service as the form (`report`, which checks `maintenance.create` on that property). Each report carries an id made on the phone, kept in `maintenance.OfflineReport` (user, id, the request) and unique per user and id, so a report sent twice makes one request. The answer gives the request number.
4. **When it was reported.** The request's time, and so its respond-by and fix-by times, is when it was typed on the phone, not when it reached the server. A time in the future, or more than 7 days back, is replaced with the time it arrived. The audit event records both.
5. **Refused, whose, and what is waiting:** as D-088 items 4–6. A refused report (for example the unit was archived) stays on the phone marked "Needs fixing", with the reasons, and can be put back into the form or discarded. Only the person who typed it sends it. The menu badge counts reports and rounds together.
6. **Payments are not queued.** A payment typed offline and sent later could be recorded twice, or after the tenant paid by M-Pesa, and its receipt would go out late. M-Pesa, USSD and the API already cover payments made away from the office. This is left out on purpose, not for later.
7. **Not in this step:** photos offline, notes and status changes on a request offline, inspections offline.

### D-091 — Ask the assistant: questions answered from your own figures — ACCEPTED (2026-10-01, open to change before merge)
The "AI assistant (permission-aware tools)" part of the TODO line. Doc 12 asks for a permission-aware tool layer with no direct database access. Doc 15 §6 says questions like "which tenants are late?" go through the same permission-aware figures as the pages, never raw SQL. Doc 16 item 7 says it should be an assistant limited to permitted figures, not a general chatbot. The user chose to build it now, using Claude through the Anthropic API.
1. **Off until two switches are on.** The platform sets `ANTHROPIC_API_KEY`, and an owner (`organization.manage`) switches the assistant on for their organization under Ask → Settings. The switch says what is sent and to whom. Switching it on or off is audited. While either switch is off, the page says so and nothing is sent. With no key the menu link is hidden.
2. **Who can ask:** any staff member of an organization with it switched on, and only staff. There is no new capability: a question can only reach figures the member can already see. Tenants, the portal, USSD and WhatsApp are not included.
3. **It answers only through tools.** Every tool is read-only, checks the same capability as the matching page and counts only the member's properties. A tool the member may not use answers "not allowed", and the assistant says so. The tools:
   - **find**: tenants, properties, units, leases, invoices and payments by name, code or number, as global search does (D-054).
   - **who owes**: leases with money overdue, with tenant, unit, amount and days overdue, optionally for one property (`invoices.view`).
   - **lease**: one lease's tenants, unit, rent, dates, balance and its last payments (`leases.view`; the balance needs `invoices.view` and the payments need `payments.view`).
   - **summary**: one month's occupancy, rent expected and collected, the collection rate and arrears by age (`dashboard.view_financial`).
   - **vacant units**: available units with their rent, optionally for one property (`units.list_vacant`).
   - **open repairs**: unfinished maintenance requests with priority, status and whether they are late (`maintenance.view`, or only the member's own jobs with `maintenance.view_assigned`).
4. **What leaves the system.** The question, earlier turns in the same conversation, and what the tools return: names of tenants, properties and units, amounts, dates, statuses and repair titles. Never phone numbers, emails, ID numbers, KRA PINs, notes, documents or photos. Tools never put these in their answers. As a second guard, anything shaped like a phone number, an email or a long ID number is masked in every text sent. Nothing else from the organization is sent.
5. **Text written by other people is data.** Repair titles and names are typed by tenants and staff. The instructions to the model say to treat tool results as data and never follow instructions found in them. The worst a crafted title can do is change the wording of an answer: the tools only read, and only what the asker can already see. Answers are shown as plain text. Links are added by our code from the tool results, never from the model's text.
6. **Limits:**
   - a question is at most 500 characters;
   - at most 6 tool calls per question;
   - one answer is at most 1,024 tokens;
   - the provider has 30 seconds per call;
   - each member can ask 30 questions a day, and each organization 300.
   A follow-up question carries the last 6 questions and answers of the conversation, kept in the session. "New conversation" clears them, and so does logging out.
7. **Records.** Each question is kept in `ai_assistant.Question`: who asked, the question, the answer, the tools used with their inputs, the tokens and whether it was answered, failed or limited. The asker sees their own recent questions. Records are deleted after 90 days by the weekly `purge_assistant` job. The provider's model is set by `ASSISTANT_MODEL` (default `claude-sonnet-5-5`).
8. **When it fails** (no answer within the time limit, a provider error, the tool limit reached), the asker sees "The assistant could not answer just now. Try again, or use the reports." The failure is recorded, and nothing else changes.
9. **Not in this step:**
   - actions such as recording a payment, sending a message or assigning a repair (they need a confirm step first);
   - answers to tenants, and a WhatsApp assistant;
   - streaming answers;
   - charts;
   - questions about documents and photos.

### D-092 — The assistant proposes, the person confirms — ACCEPTED (2026-10-01, open to change before merge)
The "assistant actions with a confirm step" part of the TODO line, left out of D-091 (item 9). Staff can ask the assistant to record a payment, message a tenant or assign a repair. The model never changes anything itself: it can only propose, and the change happens when the person presses Confirm on a card that shows exactly what will be done.
1. **Three proposal tools, which write nothing:**
   - **propose payment**: a lease (by number or unit), an amount, how it was paid (M-Pesa, cash, bank, cheque or other), the date (default today, never in the future) and a reference. Needs `payments.record` on that lease's property.
   - **propose message**: a lease and the text, sent to the tenants on it as a notice (D-044 item 15) to that one lease. Needs `messages.send_bulk` on that property, the same as the notices page. The text is checked like a notice (length, `{tenant_name}`-style fields only).
   - **propose assignment**: an open repair (by number), a staff member who can see that property's maintenance, a supplier, or both. Needs `maintenance.assign` on that property.
   Each tool checks the permission and finds the lease, repair, person or supplier among those the member can see. It answers the model with what will be shown or with the reason it cannot be proposed. At most 3 proposals per answer; they count towards the 6 tool calls.
2. **The confirm card.** Under the answer, each proposal is a card with the exact details: lease, unit, property, tenants, amount, method, date and reference; or the message text and who receives it with the SMS cost; or the repair and who it goes to. It has **Confirm** and **Dismiss** buttons. A message's text can be edited on the card before confirming. Nothing else can be changed there; to change an amount or a person, dismiss it and ask again.
3. **Confirming runs the normal service as the person.** Confirm calls the same service as the page (`record_payment`, the notice `send`, `assign`), with the person's own permissions checked again at that moment and the usual audit entry. A payment entered by someone who cannot confirm payments still waits for review (D-025). An extra `assistant.confirmed` audit event notes that it came from the assistant.
4. **Proposals live on the server.** A proposal is kept in the person's session under a random id, with the ids of what it refers to. The browser sends only that id (and an edited message text), so the card cannot be altered to point at another lease or amount. A proposal can be used once, lasts 30 minutes, and is cleared by "New conversation" and by logging out. A message proposal's id doubles as the notice's one-time id, so a double click sends once.
5. **Why this is safe from text written by others.** A crafted repair title or name could at worst make the model propose something odd. It still cannot do anything without the person's click, cannot reach anything the person could not do on the page, and the card shows exactly what would happen.
6. **Instructions to the model:** it never says an action is done; it says a card is shown to confirm. It proposes only what the person asked for, never on its own initiative.
7. **Not in this step:** other actions (invoices, leases, expenses, closing repairs), editing amounts or people on the card, the tenant and WhatsApp assistant.

### D-093 — Tenants ask on WhatsApp — ACCEPTED (2026-10-01, open to change before merge)
The "tenant/WhatsApp assistant" part of the TODO line. Tenants already get rent reminders and receipts on WhatsApp (D-044 item 16). Today a reply other than STOP or START is ignored. With this step a tenant can send a question ("How much do I owe?", "Nilipe wapi?", "Is my repair done?") and get an answer from their own figures. It uses the same engine as the staff assistant (D-091), with its own tools, switch and limits.
1. **Off until three things are on:** the platform's `ANTHROPIC_API_KEY`, the WhatsApp Cloud API (D-044 item 16), and an owner's second switch, "Answer tenants on WhatsApp", under Ask → Settings. The switch is separate from the staff one because tenants are different people whose data goes to the provider. It says what is sent, and switching it is audited.
2. **Who is asking.** WhatsApp vouches for the sender's number, as the network does for USSD (D-081 item 2). The number is matched the same way: tenants' main phone only, tenants not archived, organizations operating, leases issued and not archived. Only leases in organizations with the switch on count. A number with none gets no reply: the business number is shared by every organization, and silence tells a stranger nothing.
3. **Tools, read-only and limited to the asker's own leases:**
   - **my leases**: property and unit, rent, due day, start and end dates and status.
   - **balance**: owed, overdue, credit and the next amount due with its date (as USSD).
   - **how to pay**: the property's payment accounts and the unit's payment reference (as USSD).
   - **payments**: the last 5 confirmed payments with receipt numbers.
   - **repairs**: repairs on the lease that are open or were closed in the last 30 days, with status and dates.
   With several leases, a tool takes the unit or property to choose one, or says which ones there are. The tools never return names, phone numbers, ID numbers, notes or other tenants' data. The mask of D-091 item 4 still covers everything sent.
4. **A USSD PIN closes the money tools.** A tenant who set a USSD PIN (D-086) asked for their balance to be private from whoever holds the phone. For that number, balance and payments answer "not allowed" and the assistant points to the USSD menu or the portal. How to pay, leases and repairs still answer.
5. **It only reads.** It cannot record a payment, send an M-Pesa prompt, report a repair or change anything. It points to the USSD menu (`USSD_CODE`), the portal or the landlord instead. Text in tool results is data, never instructions (D-091 item 5).
6. **Answering.** The webhook stores the question and answers Meta at once. The question is then answered after the response is sent, the way USSD sends its M-Pesa prompt (D-084). The reply is a plain WhatsApp text message. Free text is allowed because it answers within 24 hours of the tenant's message, and service replies cost nothing. Each question is answered once: the WhatsApp message id is unique, so Meta retrying a webhook does not answer twice. A failure replies "Sorry, I could not answer just now. Please try again later or contact your landlord."
7. **Limits:**
   - a question is at most 300 characters (longer ones are cut);
   - at most 4 tool calls and 512 tokens per answer;
   - 10 questions per phone number a day;
   - 300 tenant questions per organization a day, counted apart from staff questions.
   Over the limit, the reply says so once and later questions that day get no reply. A follow-up carries the last 4 answered questions from the same number within 30 minutes.
8. **Records.** Questions are kept in the same `Question` table, marked as from WhatsApp with the tenant and the message id and no user, and purged after 90 days (D-091 item 7). Staff do not see tenants' questions in this step. Replies are not added to the tenant's message history (they are not notifications), and a STOP for WhatsApp messages does not stop answers to the tenant's own questions.
9. **Not in this step:** reporting a repair or paying through WhatsApp, a staff view of tenants' questions, asking in the portal, voice notes and photos, and WhatsApp numbers per organization.

### D-094 — Tenants act through the assistant, and ask in the portal — ACCEPTED (2026-10-01, open to change before merge)
The TODO line left by D-093. A tenant who asks the assistant can now report a repair or get an M-Pesa prompt, after confirming, and can also ask on the tenant portal. These are the same two actions the USSD menu offers (D-084, D-085), with the same checks.
1. **One switch for tenants.** The owner's tenant switch becomes "Answer tenants". It covers the portal ask page, and WhatsApp where the platform has it. The portal page needs only the platform key; WhatsApp still needs the Cloud API too. The switch text says what is sent.
2. **The portal ask page** (Portal → Ask). It is shown to a logged-in tenant when at least one of their leases is in an organization with the switch on. The tenant sees the same five tools as on WhatsApp, over their portal leases that are active, ended or terminated in those organizations. The USSD PIN does not apply there, because the portal login is stronger than holding the phone. A follow-up carries the last 4 questions of the conversation, kept in the session. "New conversation" clears it.
3. **Two actions, proposed and then confirmed** (as D-092):
   - **report a repair**: what is wrong (a few words), the kind (the USSD list) and whether it is an emergency, on an active lease.
   - **M-Pesa prompt**: an amount for one lease, sent to the tenant's own phone on record. This is the WhatsApp sender's number, or the portal account's verified phone. It is offered only where the USSD menu would offer it: a Kenyan number, a property that takes payments and a Paybill with a passkey.
   The model can propose at most one action per answer. A proposal writes nothing.
4. **Confirming in the portal** works as for staff: a card shows the details with Confirm and Dismiss. It is held in the session by a random id, used once, and lasts 30 minutes.
5. **Confirming on WhatsApp.** The service, not the model, adds the details and "Reply YES to confirm" (or "Jibu NDIYO kuthibitisha") to the answer. The proposal is kept on that question. A next message of YES, NDIYO or NDIO from the same number within 10 minutes confirms the latest open proposal, and the result is sent back: "Reported as MR-…" or "Check your phone for the M-Pesa prompt". Any other message ends the open proposal and is answered as a question. A YES with nothing open is treated as a question. A webhook retried by Meta confirms once, because the proposal is marked used in the same transaction.
6. **The same checks as the menu.** A repair goes through the service's tenant report, with its 5-a-day limit and the 10-minute repeat check. Its source is "Assistant" on the repair page. A prompt goes through `stk.send`, with its 2-minute wait and 5 prompts per phone a day counted together with USSD. On the M-Pesa requests page it shows "from the assistant". A failure is told to the tenant in plain words. Like USSD reports, these repairs have no staff reporter, so the reporter check allows either source.
7. **Limits.** Tenant questions in the portal count 10 a day per portal user. The 300 a day per organization covers both channels. Confirmations are not questions and do not count.
8. **A USSD PIN on WhatsApp** still hides balances and payments (D-093 item 4). Reporting a repair and getting a prompt stay allowed: the prompt goes only to that phone and needs the M-Pesa PIN to pay.
9. **Records.** Questions are marked "portal" or "WhatsApp". A confirmed action is audited as `assistant.tenant_confirmed` with what was done.
10. **Not in this step:** photos with a repair, paying a different amount than proposed (ask again), and other tenant actions such as notice to vacate.

### D-095 — Terms of Service and Privacy Policy, and recording who accepted which version — ACCEPTED (2026-10-01, open to change before merge)
Sign-up already asks "I accept the Terms of Service and Privacy Policy", but there were no such pages, and nothing recorded what was accepted. Doc 06 asks for a privacy notice at sign-up and clear roles: the landlord is the controller of tenants' data, and we process it for them. Doc 14 D2 and D9 ask the Terms to say we never hold customer money and do not give legal advice.
1. **Two public pages,** `/terms/` and `/privacy/`, in plain English, open to anyone. Links go in the page footer, on the sign-up form, and between the two pages. The Terms include the data-processing terms for landlords (we act on the landlord's instructions; confidentiality; security; the services we use; telling them of a breach without delay; helping with tenants' requests; returning or deleting data at the end), so there is no third document.
2. **One version for both,** a date in `support/legal.py` (`VERSION`), shown as "Effective" on both pages. A change to either text that matters gets a new date.
3. **Draft notice.** Until a lawyer has checked them, both pages say they are a draft under legal review. The notice goes when `LEGAL_REVIEWED=true` is set. The details that depend on the company come from settings: `LEGAL_NAME` (default "NyumbaPMS"), `SUPPORT_EMAIL`, `DATA_PROTECTION_EMAIL` and `HOSTING_LOCATION`. Missing ones fall back to the contact page.
4. **Recording acceptance.** The user gets `terms_version` and `terms_accepted_at`. Sign-up sets them to the current version and the audit event `user.register` records the version.
5. **A new version.** A signed-in user whose recorded version is not the current one (including people added before this step) sees a notice at the top of every page: "We have updated our Terms of Service and Privacy Policy", with a Read link and an "I accept" button. Accepting records the version, audited as `user.accept_terms`. The notice does not block use; it stays until accepted.
6. **What the pages say we use,** matched to the code: hosting, Africa's Talking for SMS and USSD, Safaricom Daraja for M-Pesa, Meta for WhatsApp (only where set up), an email provider, web push from the browser's provider, Sentry for errors without personal data (where set up), and Anthropic for the assistant (only when an owner switches it on; phone numbers, emails and ID numbers are masked first, D-091). Assistant questions are kept 90 days.
7. **Cookies.** Only those needed to sign in and keep forms safe; no tracking or advertising cookies, so no cookie banner.
8. **Not in this step:** Swahili versions, an e-signature on the Terms, the ODPC registration number (added when registered), and a separate data-processing agreement for large landlords who ask for one.

### D-096 — The 10 key screens on a phone, and the small design system — ACCEPTED (2026-10-01, open to change before merge)
Doc 14 C2 asked to sketch 10 key mobile screens before writing templates. The templates exist now (Bootstrap 5, mobile first, D-010), so the useful step is to write down what each screen shows on a phone, check it, and fix what does not work at phone width. A native app would start from the same list.
1. **The 10 screens and the record of them** go in `config/docs/19_MOBILE_SCREENS.md`: for each one, what it is for, the main action, what shows first on a 360-pixel-wide phone, and what was found. They are: home, units, unit, tenant, record a payment, invoice, arrears, add a lease, the M-Pesa inbox of unmatched payments, and the tenant portal home.
2. **The design system** is written down from what the pages already use: Bootstrap 5.3 colours and components, cards for lists on a phone instead of wide tables, and status badges with one colour meaning per state (green active or paid, blue occupied or info, yellow needs attention, red overdue or failed, grey ended). No new library and no new colours.
3. **Tables.** Every table sits in a `table-responsive` wrapper, so a wide one scrolls inside its card instead of pushing the page sideways. A test checks every template.
4. **Tap targets.** On touch screens, small buttons and form controls are at least 40 pixels high (doc 14 C3), through one CSS rule in the base page. Pages on a desktop look the same as before.
5. **Keyboards.** Money fields open the number keyboard (`inputmode="decimal"`), and phone fields the phone keyboard, on every form that takes them. A test checks the forms on the key screens.
6. **Not in this step:** Figma drawings, a redesign, and the native app itself.

### D-097 — Pre-launch checks across every route — ACCEPTED (2026-10-01, open to change before merge)
The remaining TODO items are outside the code or wait on others, so the owner chose a pass over the whole app before pilots: who can reach what, what one organization can see of another, and what leaves the server. Each check is a test that walks every route, so new pages are covered without new tests.
1. **Visitors.** Every route is opened and posted to without signing in. Only a fixed list of public routes may answer (sign-in, sign-up, password reset, invitations, the status, security, Terms and Privacy pages, the offline page and app files, pay, receipt, vacancy and letter links, provider callbacks, `/healthz`); everything else sends the visitor to the sign-in page, and the API answers 401. Nothing needed fixing.
2. **Another organization.** The test makes one organization's first record in every table carry the id the sweep asks for (number 500000, and a fixed public id), with "Canary" in its names. The other organization's owner then opens every route, posts to it empty, posts junk ids, and posts the first organization's ids. No page may show a Canary record, no route may answer anything but not found or not allowed for those ids, no row of the first organization may change, and nothing may write to its audit log. No leaks were found.
3. **Junk ids.** Several forms passed a posted id straight to the database, so a value that was not an id gave a server error instead of "not found": the owner statement's payment and send buttons, charge types, expense categories, inspection photos, lease tenants, letters and meter readings. They now go through one helper (`core.ids.uuid_or_none`), so anything that is not an id finds nothing.
4. **Staff kept to some properties.** A manager with one property opens every page. They may see the organization-wide lists that are shared on purpose: property owners (D-053) and suppliers (D-067), and the names of both. They must see nothing else of another property. One page showed more: editing an owner listed all of the owner's properties, including ones the member cannot see. It now lists only the ones they can.
5. **Query counts.** The 24 main lists, the home page and a property's page are opened with 2 leases and with 8. None asks the database more often with more rows. A test keeps it that way.
6. **What leaves the server.** Error reports to Sentry now leave out request bodies and local variables, which hold phone numbers, names and amounts, so the Privacy Policy's "sent without personal data" is true. Logs keep no request bodies or codes. The console SMS and WhatsApp senders print messages, and production refuses them unless `ALLOW_CONSOLE_SMS` is set for staging. No secrets are in Git, and `.env` is ignored.
7. **Production settings.** A test runs `manage.py check --deploy` on the production settings with made-up values and fails on any warning. The one warning left, HSTS preload, is silenced with its reason: it waits for the domain (D-062 item 7).
8. **Not in this step:** a penetration test by someone else, rate limits under load, row-level security in the database (D-038), and the 10,000-unit performance test (doc 15).

### D-098 — Ready for the server: a restore test, the cron file, and a launch check — ACCEPTED (2026-10-01, open to change before merge)
TODO line "Before launch" needs a server, which is not chosen yet. What can be done now is in the code, so the steps on the day are short and nothing is forgotten.
1. **`python manage.py restore_test`.** Restores the latest backup (or `--file`) into a scratch database named `<database>_restore_test`, never the live one, then compares it with the live database: every live table must exist in the copy, the copy must have data, and its migrations are listed against the live ones (a backup older than the last deploy is reported, not failed). The media archive must open. It reports the time taken and the row counts of key tables, prints a line for the restore log in doc 18, and drops the scratch database unless `--keep` is given. It is a recorded job. The database user needs `CREATEDB` for this.
2. **A monthly restore test is watched.** `restore_test` runs from cron on the 1st of each month, and `check_jobs` alerts if no restore test has succeeded in 35 days. This proves each month that the backups restore. It does not replace the quarterly test on a separate machine, which proves the server can be rebuilt (doc 18).
3. **The cron file is in the repository** (`deploy/crontab`), with `deploy/run.sh`, which loads the environment file, runs one command, and skips a run when the last one is still going (`flock`). Times are Africa/Nairobi. A test checks that the file runs every job in `core.jobs.JOBS` and nothing unknown, so a new job cannot be left out.
4. **`python manage.py launch_check`** lists what the server still lacks and exits with an error until all is done:
   - `pg_dump` and `pg_restore` are found and not older than the database server;
   - the backup folder can be written to and has a backup from the last 26 hours;
   - the cache answers;
   - every job has run;
   - a restore test has passed;
   - alert emails have somewhere to go;
   - email is not the console.

   It also reminds the operator of the two things it cannot see: the off-site copy and the uptime monitor on `/healthz`.
5. **Not in this step:** choosing the host, the off-site storage provider, and the uptime service; the server itself.

### D-099 — Native mobile app: not now; a Play Store wrapper first, a staff app only on evidence — ACCEPTED (approved by the owner 2026-10-01)
The TODO line "Native mobile app". Doc 07 said a native app comes "only after the API is stable", and doc 16 §11 found that Kenyan rental products are web first: tenants are never asked to install anything. Since then the web app has gained most of what a native app was wanted for:
- it can be installed (D-080);
- caretakers can do meter rounds and report repairs offline (D-088, D-090);
- staff get alerts on their phones (D-089);
- tenants are reached by SMS links, the portal, USSD and WhatsApp (D-081, D-093);
- the main screens have been checked at phone width (D-096).

This decision is a plan. The owner approved it on 2026-10-01, and its first code step (item 3) is D-102.
1. **No native app before the pilot.** A second codebase would double the work on every change and need its own releases, store reviews and security checks. The installable web app stays the only app for staff and tenants.
2. **Tenants are never asked to install an app.** They keep SMS payment links, the portal, USSD and WhatsApp. This holds even if a staff app is built later.
3. **Measure first.** During the pilot, count how many sessions open from the installed app (the start URL already carries `?source=app`, D-080), on which phones, and how often offline rounds and push alerts are used. Also ask the pilot caretakers what they miss. Adding this count is the first code step after approval: a few lines in the analytics, with no personal data.
4. **Step one, if landlords or caretakers want "a real app" from the Play Store:**
   - **Approach:** an Android Trusted Web Activity, which is the same web app in a Play Store shell (built with Bubblewrap). It needs a signing key, `/.well-known/assetlinks.json` served by the site, and a Play developer account (a one-off fee).
   - **Play Store requirements:** the Data safety form, the Privacy page from D-095, and for a new personal account a closed test with testers before release **[VERIFY: current Play rules]**.
   - **What we get:** the store listing and the trust it brings, and the app opens full screen with no browser bar. Updates ship with each web deploy and there is no second codebase.
   - **iPhone:** no wrapper. Apple rejects apps that only wrap a website (App Store guideline 4.2), so iPhone users keep the installed web app. Since iOS 16.4 it can receive push once added to the home screen.
5. **Step two, only on evidence: a staff app for caretakers.** Build it only if the pilot shows needs the web app cannot meet, such as:
   - work that must be done offline beyond rounds and repair reports;
   - reliable background sync while the app is closed;
   - a camera that works reliably for meter photos;
   - unlocking with a fingerprint;
   - Android phones whose browsers drop the offline queue.

   If it is built:
   - **Who and what:** staff only, starting with the caretaker's day: today's tasks, the meter round with photos, repair reports and updates, and payments waiting for review. Payments stay online only (D-090).
   - **Technology:** React Native with Expo, over the API. It is JavaScript, like the existing front-end scripts, and it gives one codebase for Android and later iPhone. Flutter is the alternative if a hired mobile developer prefers it.
   - **Prerequisite:** the API needs sign-in per staff member with device tokens that can be revoked, and permissions that match the web roles. Today's API keys are per organization and for integrations only (D-082, D-087). This, and an OpenAPI description, come before any app screen.
   - **Design:** the app follows doc 19's design system, so the two look and work the same.
6. **What would change this plan:** pilot evidence that many staff do not install the web app or lose offline work, a large share of iPhone users missing alerts, or a partner (for example an agency) needing a store app.
7. **Not in this step:** any code, store accounts, signing keys, the per-staff API sign-in, and choosing a mobile developer.

### D-100 — Recommend a Paybill over a Till, and say why on the M-Pesa pages — ACCEPTED (2026-10-01, open to change before merge)
The open decision "Recommend Paybill (reference-based matching) over Till for auto-reconciliation?" (TODO, Phase 0). Doc 11 already leaned to Paybill. The code built since makes the difference concrete:
- a payment is confirmed on its own only when its account number is a unit's payment reference (D-045);
- a payment from a matching phone number only gives a suggestion in the inbox, because a phone can be shared and relatives pay too;
- payment requests to a tenant's phone and the pay links in rent messages need a Paybill with a passkey (D-045 step 3, D-049).
1. **Recommend a Paybill.** Tenants type their unit's reference as the account number, so each payment is matched and receipted at once. A Paybill also allows payment requests and pay links.
2. **A Till stays supported**, for landlords who already have one and whose tenants know it:
   - **What happens:** its payments arrive, but with no account number. Each one waits in the M-Pesa inbox, with a suggestion when the phone number is a tenant's, for someone to match.
   - **What it cannot do:** payment requests and pay links are not offered for it.
   - **Rule kept:** nothing about a Till payment is confirmed on the phone number alone.
3. **Said where it matters:**
   - the "Add a Paybill or Till" form explains the choice;
   - a Till's account page says what it does not do;
   - the M-Pesa help page explains the same.
4. **[VERIFY in the Daraja sandbox]** that a Buy Goods C2B confirmation has an empty `BillRefNumber`. If Safaricom ever passes a reference for Tills, the same matching applies unchanged. A payment request on a Till ("CustomerBuyGoodsOnline") is possible in Daraja, but it is not built. It would be added only if pilot landlords with Tills need it.
5. **Not in this step:** moving landlords from a Till to a Paybill, and payment requests on a Till.

### D-101 — A pilot report against doc 02's success measures — ACCEPTED (2026-10-01, open to change before merge)
The pilot onboarding kit was the next item. Most of it already exists: `seed_demo` fills a demo organization, the home page has a setup checklist (`accounts/setup.py`), and doc 02 has the landlord interview log. What was missing is a way to see whether the pilot is working. Doc 02 sets the measures: set-up in under 15 minutes, the monthly rent cycle run in the app, and 90% or more of payments reconciled without manual work.
1. **`python manage.py pilot_report [--month YYYY-MM] [--org <public id>] [--csv]`** gives one line per organization for a month (by default the last full month). It is run by the platform operator; there is no page for it.
2. **What it measures:**
   - **Set-up time:** minutes from sign-up to the first active lease.
   - **Rent cycle:** invoices issued for the month (not draft or void), and confirmed payments with the total collected.
   - **Matched without work:** M-Pesa and bank money received in the month, split into matched on its own (by reference or payment request), matched by hand, and still waiting. Its share on its own is the 90% measure.
   - **Staff use:** staff who logged in within 30 days, out of all staff.
   - **Measures missed:** each line ends with the doc 02 measures the organization does not meet yet.
3. **Counts only.** No tenant names, phone numbers or per-person amounts are in the report, so it can be shared with the team.
4. **Not in this step:**
   - a page in the app;
   - counting installed-app use (waits on D-099's approval);
   - the landlord interviews themselves, and choosing the pilot area and landlords.

### D-102 — Count opens of the installed app — ACCEPTED (2026-10-01, open to change before merge)
D-099 item 3: measure how much the installed web app is used before deciding on any store app.
1. **What is counted.** An open is a GET of `/?source=app`, the start URL in the manifest (D-080). A middleware after the organization middleware adds one to a row in `support.AppOpen` for (day, organization, audience, platform):
   - **audience:** staff (with their organization), tenant (signed in without an organization; no organization is stored), or signed out;
   - **platform:** Android, iPhone or other, read from the browser's User-Agent.
2. **Counts only.** No user, IP address, device or full User-Agent is stored, so the Privacy page needs no change. A count that cannot be written is logged and never fails the page.
3. **Where it shows.**
   - `pilot_report` (D-101) gives each organization's staff opens in the month, by platform, and ends with tenant and signed-out opens.
   - Django admin lists the rows, read-only.
4. **Not in this step:**
   - counting how often offline rounds and push alerts are used (both are already visible: queued rounds arrive as readings, and push subscriptions are in admin);
   - a Play Store wrapper (D-099 item 4).

### D-103 — Keep backups out of Git and generate the ER diagram from the models — ACCEPTED (2026-10-01, open to change before merge)
Two "Always" and Phase 0 items that need no one outside the codebase: no secrets in Git, and the ER diagram awaiting review.
1. **Secrets check.** The Git history and the uncommitted work were searched for keys, tokens, private keys, `.env` files and database dumps. None were found; the only password is the made-up one the test accounts use, and `.env.example` holds placeholders only.
2. **Backups are ignored.** `python manage.py backup` writes to `<project>/backups/` when `BACKUP_DIR` is not set, and that folder was not in `.gitignore`, so a plain `git add .` could have committed tenants' data. `.gitignore` now has `backups/` and `*.dump`, and a test checks both with `git check-ignore`.
3. **The ER diagram is generated.** `config/docs/erd.md` was a Phase 0 draft written before any models existed. `python manage.py erd` now writes it from the models, in six sections by area, one Mermaid diagram each. It leaves out every table's link to the organization and links to a user that only say who created, approved or was given something.
4. **It stays current.** `python manage.py erd --check` fails when the file differs from the models, and a test runs it, so a model change without a regenerated diagram fails the suite. A new app not placed in a section also fails, with the app named.
5. **Replaces the draft's review points.** The draft's four questions are answered by the code: tenants sign in through the portal's own accounts, rent and deposits have separate ledgers (`LEDGER_ENTRY`, `DEPOSIT_ENTRY`), an M-Pesa transaction links to at most one payment, and `PAYMENT_ALLOCATION` splits a payment across invoices.
6. **Also:** the old `db.sqlite3` is already gone from the project folder, so that TODO item is closed.

### D-104 — A Daraja sandbox check that answers the open M-Pesa questions in one run — ACCEPTED (2026-10-02, open to change before merge)
The Phase 6 and payment-link TODO items wait on Safaricom's sandbox. This step turns them into one command, so they close in one sitting once sandbox keys are in.
1. **The command.** `python manage.py daraja_sandbox_check --account <payment account id> --phone <test phone>` runs these steps in order:
   - `token`: get an access token;
   - `urls`: register the callback URLs;
   - `paybill`: simulate a Paybill payment to see how the phone number arrives (number, SHA-256 of 2547…, SHA-256 of 07…, masked) and whether the reference matches a lease;
   - `till`: simulate a Buy Goods payment to see whether it carries an account number;
   - `stk-query`: send a KES 1 prompt and query it straight away to get the "still processing" answer;
   - `stk-c2b`: wait for the prompt to be paid, then see whether a C2B confirmation also comes for the same receipt;
   - `limit`: send a prompt above `stk.MAX_AMOUNT` to see whether Daraja refuses it;
   - `sms`: measure the rent SMS with the payment link.

   Options:
   - `--only` picks steps;
   - `--wait` sets how long to wait for callbacks;
   - `--out answers.md` writes the findings as a table.

   Each finding says the question, what came back, what the code assumes, and whether they match. The command changes no assumption itself; a "differs" is a code change for a person to make.
2. **Sandbox only.** The check refuses production credentials, and the client's new `simulate_c2b` refuses them too. Test prompts are recorded as sent by the organization's owner. A step with no lease to pay, or no phone, is skipped and says why, and a failing step does not stop the rest.
3. **When the C2B confirmation came is now stored.** `MpesaTransaction.c2b_confirmed_at` is set when a C2B confirmation arrives, including one for a receipt the STK callback already stored. Before this, a duplicate confirmation left no trace, so it could not be told whether Safaricom sends both. Migration `mpesa 0009` sets it to the row's creation time for past C2B rows.
4. **SMS length, answered now.** A payment link (`SITE_URL` + `/p/` + token) is 45 characters with a 25-character site address such as `https://app.example.co.ke`:
   - the invoice and overdue messages stay at 2 parts;
   - the due-soon message goes from 1 part to 2.

   That makes each message 2 parts, KES 2.00 at `SMS_PRICE` 1.00. The network counts a message with any character outside GSM-7 in 70-character parts; the step reports both the app's count and the network's.
5. **Not in this step:**
   - changing the code's assumptions (that waits for the answers);
   - trying the payment link with a real person on a sandbox phone (it needs the hosted site).
6. **Also:** the full suite found the restore test failing with "permission denied to terminate process" when it dropped its scratch database just after a restore. This was probably autovacuum, which a forced drop cannot end for a role that is not a superuser. `core/restore.py` now drops plainly first and forces only when a session is still connected.

### D-105 — Database: pooled and time-limited connections, append-only enforced by PostgreSQL, health monitoring and a scale test — ACCEPTED (2026-10-02, open to change before merge)
A review of the database against 30 points (connection to scalability testing, plus indexing and caching). Most were already in place; doc 20 maps each one to where it lives. This step adds what was missing.
1. **Connections.** Production uses psycopg's pool through Django (`DB_POOL`, default on; `DB_POOL_MIN` 1, `DB_POOL_MAX` 4 per worker), ends a statement after 30 s and an idle open transaction after 60 s, names each process (`DB_APP_NAME`, set per cron job by `deploy/run.sh`) and takes `DB_SSLMODE` for TLS. The backup and restore tools use the same TLS mode.
2. **Append-only in the database.** Triggers (`billing 0008`, `audit 0002`) refuse UPDATE and DELETE on the ledger, deposit entries, follow-ups and the audit log, whoever connects. Before this only the model's `save()` and `delete()` refused, so a bulk `.update()` or the shell could still change history. A deposit entry may still have its `counterpart` set. One test that set a follow-up's date with a bulk update now records the follow-up properly.
3. **Separate roles.** `deploy/db_roles.sql` creates an owner that runs `migrate` and an app role that only reads and writes rows (plus CREATEDB for the restore test and `pg_read_all_stats` for monitoring), and moves an existing install over.
4. **Monitoring.** `python manage.py db_health` reports connections, long queries (who and how long, never the query text), size, disk, cache hits, deadlocks, slow queries (with `pg_stat_statements`), tables read by full scans, unused indexes, vacuum and unapplied migrations. Its problems join `check_jobs` alerts, with limits in `DB_ALERT_*`. `launch_check` adds TLS, not-a-superuser, pool, statement timeout, `pg_stat_statements` and migrations.
5. **Scale test.** `python manage.py scale_test --leases 200` builds an organization, times every list page with its query count, and rolls it all back. The query-count test (D-097) now shares its builder (`core/scale.py`). Acting on what it found at 200 leases:
   - the arrears calculation still reads a lease's whole history (oldest-first matching is unchanged) but loads only the columns it uses: 585 ms to 251 ms at 14 months, same results;
   - the call list's "To call" is paged 25 at a time;
   - the home page's vacant-unit count was a measuring artefact: rows of an uncommitted transaction read slowly, and the query alone takes 8 ms.
6. **Caching.** Production sessions are `cached_db`; `purge_sessions` clears expired ones weekly (8-day alert limit). Permissions and balances are not cached, so access removal and figures are always current.
7. **Message log retention.** `purge_messages` (weekly) clears the text, subject, address and WhatsApp parameters of messages older than `MESSAGE_TEXT_DAYS` (730) and keeps the row. The dedupe key, type, status, cost and links stay. Deleting rows was rejected: the dedupe key is what stops a reminder for a long-unpaid invoice being sent again. The period is **[VERIFY]** with counsel (doc 06). Job runs were already pruned after 90 days by `check_jobs`.
8. **Not done:**
   - trigram search indexes (searches are already narrowed to one organization; `db_health` will show when a table needs one);
   - point-in-time recovery.
9. **Load test** (doc 20 §30). `loadtest_setup` builds an organization and writes sessions and an M-Pesa callback address for `loadtest/locustfile.py` (staff, tenants and Safaricom at once). Run on a scratch database, the first run found:
   - **Concurrent M-Pesa payments deadlocked** on the organization row. Row locks taken with `select_related` locked the joined shared rows too, while inserts held share locks on them. Every such lock now uses `of=("self",)`, enforced by a test.
   - **The home page computed arrears twice;** it now computes them once and passes them to the call list and dashboard.
   - **Capacity:** one development process served about 5 requests a second. At 20 users: median 0.23 s, 95th percentile 1 s, no failures. To be repeated on the production server before launch.

### D-106 — Notices to vacate: from either side, with history, the tenant's own notice in the portal, reminders and move-out tracking — ACCEPTED (2026-10-04, open to change before merge)
This builds on the single notice date a lease already had (doc 14 B1). It came out of a review of other Kenyan rental systems, which treat vacating notices as their own workflow. Here the notice joins what we already have (leases, the portal, notifications, condition reports and the home page) instead of being a separate module.
1. **Notice history.** `LeaseNotice` records:
   - who gave it (tenant or landlord) and how (in person, phone, message, email or portal);
   - the day it was given and the move-out day;
   - an optional reason;
   - whether both sides agreed in writing to a shorter notice;
   - who recorded it, and if it was withdrawn, when, by whom and why.

   One notice can be open per lease (database constraint). A later notice replaces the open one, which is kept and stamped "Replaced by a later notice". `Lease.notice_given_on` and the new `Lease.move_out_on` copy the open notice so lists can filter on them. Migration `leases 0004` creates a history row for each notice recorded before this step.
2. **Rules** (`leases.services._check_notice`):
   - not dated in the future or before the lease started;
   - the move-out day defaults to the end of the notice period;
   - an earlier day needs the "shorter notice agreed" box;
   - on a fixed-term lease, the move-out day cannot be after the end date (that needs a renewal).
3. **The tenant's notice in the portal.** A "Moving out" card on the lease page and a notice form (`/my/leases/<id>/notice/`) let a tenant give notice. The notice is dated today and must be at least the notice period. There is one per lease, with no withdrawing or changing in the portal; the tenant is told to call the office. The notice is audited under the tenant's user.
4. **Messages.** Three new types:
   - `notice_given`: an in-app alert to staff who can end leases at the property, except the person who recorded it;
   - `notice_received`: a WhatsApp/SMS to the tenant when the tenant gave notice, with the move-out day and the balance;
   - `move_out_soon`: a reminder a week before the move-out day, sent by the daily job once per notice. It is skipped when the notice itself came inside that week.

   No message is sent for a landlord's notice: that is a legal document and is served as a letter (D-109). The Swahili text is **[VERIFY]**, and the WhatsApp templates need Meta approval before they send on WhatsApp (SMS until then).
5. **Seeing who is leaving:**
   - the unit list gets a "Notice given" filter and a "Leaving <day>" badge;
   - the lease list gets `?notice=1` ("Tenants moving out", soonest first);
   - the home page shows tenants moving out within 14 days or past their day with the lease still open;
   - the lease page banner says who gave the notice, how, the reason and the move-out day, links to the move-out inspection, takes a reason when withdrawing, and lists the notice history.
6. **Not in this step:**
   - generated notice letters and the handover pack for an advocate (D-109);
   - a tenant asking to give notice through the assistant;
   - showing the unit to prospects while the tenant is still in it.

### D-107 — Security, first batch: Content Security Policy, session limits, per-IP limits, Argon2, dependency scanning, two-step login an organization can require — ACCEPTED (2026-10-04, open to change before merge)
From a security review after D-106. Isolation between organizations was already covered by the route sweeps (D-097), and logins were already rate limited. This batch closes the next gaps. Legal notices move to D-109, after the second security batch (D-108).
1. **Content Security Policy** (Django's own `ContentSecurityPolicyMiddleware`, policy in `CONTENT_SECURITY_POLICY` in `config/settings/base.py`):
   - Scripts run only from our own static files and two CDN packages, allowed by exact version (`bootstrap@5.3.3/`, `chart.js@4.4.4/`), never the whole jsdelivr host. No `'unsafe-inline'` or `'unsafe-eval'` for scripts.
   - Changed 2026-10-05: Bootstrap 5.3.3, Bootstrap Icons 1.11.3 and Chart.js 4.4.4 are now served from `static/vendor/` (byte-identical to the pinned CDN files), and the policy allows scripts, styles and fonts from our own site only. A page loaded unstyled when the browser could not reach jsdelivr (network or an ad blocker); local files also let the installed app show its full look offline.
   - Every CDN `<script>` and `<link>` carries a `sha384` integrity hash and `crossorigin="anonymous"`, so a changed file is refused.
   - Inline styles stay allowed (`style-src 'unsafe-inline'`): about 70 `style=` attributes would need moving for little gain, since CSS cannot run code.
   - `frame-ancestors 'none'`, `object-src 'none'`, `base-uri` and `form-action` limited to our own site.
   - All 38 inline handlers (`onclick=`, `onchange=`, `onsubmit=` and the like) and the three inline `<script>` blocks moved to `static/js/ui.js` and `static/js/sms_editor.js`, which pages ask for with `data-` attributes (`data-confirm`, `data-autosubmit`, `data-print`, `data-reload`, `data-copy`, `data-filter`, `data-sms-box`). `ui.js` is in the offline cache, so the offline page's "Try again" works.
   - Browsers report what was blocked to `/csp-report/` (`core/csp.py`): at most 10 KB per report and 20 a minute per IP. Only the page path, rule, blocked origin and script file are logged (`django.security.csp`), never query strings or code samples.
   - `CSP_REPORT_ONLY=1` sends the policy as report-only, to try a change in production without blocking anything.
   - A test fails if a template gains an inline script or handler, or a CDN tag without a hash.
2. **Session limits** (`core/sessions.py`). Staff (anyone in an organization, or a Platform Admin) are logged out after 60 minutes without a request or 12 hours after logging in, whichever comes first. Tenants in the portal use their own phones and sign in with SMS codes, so theirs are 14 days idle and 30 days in all. All four are settings (`SESSION_IDLE_MINUTES`, `SESSION_MAX_HOURS`, `PORTAL_SESSION_IDLE_DAYS`, `PORTAL_SESSION_MAX_DAYS`). The last-seen time is saved at most once a minute. Sessions from before this change count from their next request.
3. **Per-IP limits on sign-up and password reset**: 20 of each per IP per hour, on top of the per-phone code limits (5 an hour, 60 seconds apart). Kept generous because mobile networks put many phones behind one address. A form with errors does not use one up.
4. **Argon2** password hashing (`argon2-cffi`). PBKDF2 hashes from before still work and are re-saved with Argon2 at the next login.
5. **Two-step login an organization can require** (`Organization.require_mfa`, the Staff page):
   - Only someone who can manage the organization can switch it, and only once they use two-step login themselves, so they cannot lock themselves out. Switching is audited (`organization.require_mfa_on`/`_off`).
   - When on, `OrgMemberRequiredMixin` sends a member without two-step login to Security to set it up, and asks a member with it for a code if this session has not given one. Pages outside the organization (Security, logout, switching organization, the tenant portal) are not affected.
   - Two-step login cannot be switched off while any of the member's organizations requires it. The Staff page shows who uses it.
6. **Dependency scanning**: `pip-audit` runs when a requirements file changes and every Monday (`.github/workflows/audit.yml`), failing on any known advisory. Dependabot opens grouped weekly pull requests for pip packages and GitHub Actions. ruff's flake8-bandit rules (`S`) run in CI. Each finding outside tests was fixed (an `assert` that `python -O` would skip, an exception swallowed on the status page) or marked with a `noqa` comment that says why (fixed https addresses, subprocess calls without a shell). Both workflows get a read-only token.
7. **Next batches** (not in this step):
   - a test that fails the build when a new route has no cross-organization check;
   - upload checks on the file's actual content, with photos re-encoded to drop location data;
   - security alerts for spikes in failed logins, an API key used from a new IP, or bulk exports (`core/alerts.py`).

   Larger, as their own steps: PostgreSQL row-level security as a second guard on organization scoping, and the Data Protection Act tools (a tenant's data exported or erased, and old closed leases anonymised).

### D-108 — Security, second batch: every route names who may use it, upload contents checked, security alerts — ACCEPTED (2026-10-04, open to change before merge)
The next batch named in D-107 item 7. Legal notices move to D-109.
1. **Every route names who may use it** (`core/tests/test_access_map.py`). The D-097 sweeps cover visitors and other organizations; this covers roles inside one organization:
   - A staff view must name a capability that exists in `accounts/capabilities.py`. A view open to every role (15, such as search, the inbox and maintenance, which check inside) or to whoever is signed in (the tenant portal, one's own account), or public, must be on a list in the test with the reason. A new route that is on none fails the build.
   - A member whose role has no capabilities opens and posts to every capability route (141), and each must refuse.
2. **Security alerts** (`accounts/alerts.py`), read from the audit trail so they fire whichever page, API call or admin action made the change. Sent after commit; a failure to send is logged and never undoes the change.
   - To the account holder by SMS: two-step login switched off, or reset by support, or a recovery code used (with how many are left). A changed password already sent one.
   - To everyone who can manage the organization, in the inbox: a member's role or capabilities changed, a role changed, a member joined. New staff notification type `security_change`, which cannot be switched off and ignores quiet hours. The one who made the change does not get the inbox message.
   - Also by SMS to all of them, the one who made the change included in case it was not them: where money or data can go (M-Pesa settings, a payment account added, an API key or webhook created or changed) and two-step login no longer required.
3. **Attack alerts to the operators** (`core/watch.py`, through `check_jobs`): failed sign-ins (Django's `user_login_failed`), wrong two-step codes and CSP reports are counted per hour across the platform. More than 300, 100 or 500 in an hour (`SECURITY_ALERT_*_HOUR`) is an alert. The line names the threshold and the hour, not the count, so a continuing attack does not send an email on every check. Counts live in the shared cache.
4. **Support attachments checked by content** (`support.services.process_attachment`), like documents and receipts already were: a PDF must start like one, `.xlsx`/`.ods` must be a zip, `.xls` must have the Office signature, CSV and text must have no binary bytes, and pictures are decoded and saved again as JPEG, which drops EXIF data such as the location. Every other upload already did this (D-047, D-067).
5. **Not in this step**: an API key used from a new IP and bulk exports (D-107 item 7) wait until pilot use shows what is normal. PostgreSQL row-level security and the Data Protection Act tools remain their own steps.

### D-109 — Notice to vacate and demand letters: numbered, frozen, checkable, with a record of how each was served — ACCEPTED (2026-10-05, open to change before merge)
D-106 left the landlord's notice to vacate without any message, because it is a legal document served as a letter. This step adds that letter and a demand letter for rent arrears. The owner chose: frozen and numbered like the tenancy letter (D-048); fixed plain wording with an optional note; a warning, not a block, on commercial property; and a record of service with an optional proof upload. The advocate handover pack is the next step and reads from what this one records.
1. **One model, two kinds.** `NoticeLetter` (letters app): "Notice to vacate" (NTV-YYYY-NNNNNN) or "Demand for rent arrears" (DEM-YYYY-NNNNNN), numbered per organization. As with D-048, the facts are frozen as JSON when it is issued, the A4 PDF is drawn once with ReportLab and served only through a permission-checked view, and a private code opens `/l/<code>/`, the same check page as tenancy letters. A letter issued in error is withdrawn with a reason, never deleted.
2. **Capability** `leases.issue_notice` ("Issue notices to vacate and demand letters, and record how they were served"), scoped by property, granted by migration to Owner and Manager.
3. **Notice to vacate letter.**
   - Made from an open notice the landlord gave (D-106) on an active lease. Staff record the notice first, then print its letter. One live letter per notice; withdrawing or replacing the notice withdraws its letter ("The notice was withdrawn" / "Replaced by a later notice").
   - States: the tenants, the premises (property, address and unit), the lease number, the date of the notice, the notice period in the lease, the day to hand over the unit, that a shorter notice was agreed in writing (when it was), the notice's reason if staff choose to print it, and a staff note. Then that rent and charges stay payable until the unit is handed over, that the unit will be inspected with the tenant, and that the deposit is settled under the lease once the final account is drawn. It ends with a line for the tenant to acknowledge receipt.
   - Warnings before issuing: the property is commercial or mixed-use (a business tenancy may fall under the Landlord and Tenant (Shops, Hotels and Catering Establishments) Act, Cap. 301, which has its own prescribed notice form; check with an advocate) **[VERIFY]**; the notice is dated before today.
4. **Demand letter.**
   - For any issued lease whose tenancy (D-048) has arrears. Arrears are the balance less what is still unpaid on invoices whose grace days have not run out, so rent billed in advance is never demanded.
   - States: the tenants, the premises, the lease number, the amount in arrears as at the day it is issued, the invoices past their grace days with what is unpaid on each (the oldest 20, then "and N more"), other items on the account (opening balance, adjustments, unallocated credit) as one line when they are not zero, and a request to pay within N days of receiving the letter (staff choose 1–60, default 7). It gives the ways to pay that the property's payment accounts allow (Paybill with the unit's payment reference, Till, bank) and how to dispute the amount. Then that if it is not paid, the landlord may take further steps to recover it, which may include legal action. A staff note is optional.
   - Warnings before issuing: a promise to pay that has not fallen due yet, the latest follow-up says the tenant disputes the amount, or a demand already served whose days have not run out.
5. **Wording.** Fixed, short and plain, the same for every organization, marked **[VERIFY with counsel]**. Each letter says it does not take away any rights the tenant has under the law or the lease. No editable templates in this step.
6. **Record of service.** After printing, staff record when the letter was served (not before it was issued, not in the future), how (handed to the tenant, handed to an adult at the premises, fixed to the door of the premises, registered post, courier, email, other), who served it, who received it, a note, and optionally a scan or photo of the signed copy. The proof goes through the documents app on the lease as "Notice or letter" (needs `documents.upload`), so it gets the usual content checks. A record can be corrected; each change is audited with the old and new values.
   - A demand's days count from the day it was served: the letters page and the lease page show "pay by" once it is served.
   - A notice to vacate served after the notice's date shows a warning when the move-out day is less than the notice period after service and no shorter notice was agreed **[VERIFY: when a notice takes effect]**.
7. **Pages.** `/letters/lease/<lease>/notices/` shows both kinds: what a letter issued today would say, the warnings, the issue form, and the letters of the tenancy with their service status, a service form and withdraw. The lease page gets a "Notice and demand letters" card with the latest live letters, and the notice banner links to the notice letter. Issuing, recording service and withdrawing are audited (`notice_letter.issue`, `.serve`, `.withdraw`).
8. **Not in this step:**
   - the advocate handover pack (next);
   - sending a letter by SMS, WhatsApp or email, or showing it in the tenant portal;
   - home tasks for letters not yet served or demands whose days have run out;
   - editable templates, letters in Swahili, and the prescribed Cap. 301 form;
   - interest or late fees in the demand (late fees are their own step).

### D-110 — More analysis on the reports and money pages: comparisons, breakdowns and shares — ACCEPTED (2026-10-05, open to change before merge)
The owner asked for more analysis on the reports and money pages. Each page now says how a figure compares with before, where it came from, and what share of the whole it is. Nothing new is stored: every figure is worked out from the same records as the totals already on the page and adds up to them. Each chart also has a table with the same figures.
1. **Dashboard** (`/reports/`):
   - Each headline tile shows its change against last month: occupancy in units, rent expected, rent collected and cash in percent, collection rate in points. Arrears compare with last month's month-end snapshot (D-077) when one exists.
   - "<month> compared" is a table of the month against last month and the same month last year.
   - Collection by property, the lowest rate first.
   - How tenants paid: cash by method, with counts and shares.
   - Empty units: the asking rent (`list_rent`) of vacant rentable units, plus a count of those with no asking rent.
   - How reliably tenants pay: the collectability grade (D-052) mix of occupying leases.
   - The five largest amounts overdue and their share of all arrears. This needs `invoices.view`.
   - The CSV gains collection by property, cash by method and empty units.
   - These extra figures are worked out only on the dashboard page (`metrics.dashboard(analysis=True)`). The home page and the assistant skip them.
2. **Profit and loss and cash flow**:
   - Tiles with the change against the previous period of the same length (`finance.previous_period`), shown on the page only. The CSV does not run the second report.
   - Profit and loss adds margin (net operating income ÷ income) and expenses as a share of income. Its summary has this period, the previous period and the change, and each expense category shows its share of expenses.
   - Cash flow shows where money in came from (rent, other charges, deposits, not yet applied) as shares, plus net cash a month.
   - A Chart.js chart by month (bars for in and out, a line for net) when the period is longer than one month, and a monthly-average row.
   - By property, when more than one property is in view: income, expenses, net operating income, margin, share of income and collection rate (profit and loss), or money in, expenses, refunds and net cash (cash flow). Payments to owners are not split by property, so these figures are before owner payments. Each property links to the report filtered to it. Both CSVs gain the by-property section, and the profit and loss CSV gains "Expenses % of income" and "Margin %".
3. **Aged receivables**:
   - Tiles: owed in total and the number of leases owing, overdue and its share, the share more than 60 days late, and the largest balance.
   - A bar of how late the money is, a "Share of total" row under the table and in the CSV, and the ten largest balances.
4. **Invoices list**: totals for the invoices matching the search (drafts and void invoices left out): billed, paid and its share, still to pay, and overdue with a count.
5. **Arrears page** (`billing.selectors.arrears_summary`):
   - Tiles: owed in total and how many leases, past due, more than 60 days late, the average a lease owes, and the median days late.
   - A bar of how late the money is, a "Share of total" row under the table, and a by-property table (leases, owed, share, over 60 days) when more than one property owes.
6. **Expenses list**: the totals also give the average approved expense and the spending a month over the months with expenses. Approved spending is broken down by category, by property (when more than one) and by the top five suppliers, each with its share and a bar. Rows are `{name, total, n}`.
7. **Property page** (`metrics.property_glance`): members who can see the dashboard's money get a "How it is doing" card for the property.
   - It shows occupancy with the asking rent of the empty units, rent collected against expected, the collection rate against last month, and what is overdue.
   - Members who can see financial reports also get a line with the last 12 months' income, expenses, net operating income, margin and collection rate.
   - It links to the dashboard and the profit and loss filtered to the property. Archived properties do not show it.
8. **Lease account page** (`billing.selectors.payment_record`): a "Payment record" card shows:
   - The tenancy's collectability grade (D-052) with how many of its invoices were paid in time, the average days late, and how late the oldest unpaid bill is.
   - What has been billed over the lease's life (issued, partly paid and paid invoices; drafts and void ones are left out) and the share paid.
   - For members with `payments.view`: confirmed payments received, how many, the average payment and the date of the last one.
   The card is hidden until the lease has an invoice or a payment.
9. **Maintenance performance** (`/maintenance/performance/`):
   - Tiles for all jobs in the period: jobs finished, open and overdue; the share fixed and started on time; average days to fix and hours to start; and, for members who see costs, the approved cost and cost a job. Members who don't see costs get reopened jobs and moved due times instead.
   - The same table as for suppliers and staff, by kind of job (most jobs first), by priority (most urgent first) and by property (when no property is chosen and more than one has jobs). Each row links to the list filtered to it, and the cost cell shows the cost a job (`Performance.cost_per_job`).
   - `services.performance` groups by `kind`, `priority` or `property` as well as by `supplier` and `assigned_to`; `services.overall` adds everything up into one row.
   - The CSV gains the "All", "Kind", "Priority" and "Property" rows and a "Cost per job" column.
10. **Not in this step**: budgets or targets to compare against, forecasts, year-to-date columns, and saved custom reports. The payments list already had its own totals tiles.

### D-111 — Light and dark mode, one flat colour system, and a self-hosted font — ACCEPTED (2026-10-05, open to change before merge)
The owner asked for a professional look in light and dark mode, with no gradients, using free tools only. The pages already use Bootstrap 5.3, which has light and dark colour modes built in (`data-bs-theme`), so no new framework is added.
1. **Free tools only, all self-hosted or already in use:** Bootstrap 5.3 (MIT) and its colour modes; Inter (SIL Open Font License), one 48 KB variable font file in `static/fonts/` with its licence, so the Content Security Policy needs no font CDN and the installed app has it offline; Chart.js 4 (MIT), already used. No paid themes, kits or fonts.
2. **Tokens.** Every colour is a CSS variable in `static/css/app.css`, defined twice: for light (`:root, [data-bs-theme=light]`) and for dark (`[data-bs-theme=dark]`). The base page has no inline styles; templates use Bootstrap classes and no colour of their own (a test checks).
3. **Light:** a light grey page (`#f5f6f8`) with white cards, hairline borders and a faint shadow; text `#1a1d23`; one accent, indigo (`#4f46e5`), for primary buttons and links, the same indigo the charts already used.
4. **Dark:** a separate palette, not the light one inverted. The page is a dark grey (`#0e1015`), never pure black. Cards are a little lighter (`#161a23`) and get their depth from that and a border instead of shadows. Accent and status colours are softer so they do not glare, and text and buttons keep at least 4.5:1 contrast.
5. **No gradients.** Flat fills only. A test fails if `gradient(`, `bg-gradient` or a canvas gradient appears in a template, stylesheet or script.
6. **Choosing the theme:** Light, Dark or System (the default, which follows the phone or computer and changes with it). The switch is in the Account menu, and in the footer on signed-out pages. The choice is kept on the device (`localStorage`). `static/js/theme.js` loads in `<head>` without `defer`, so the page is drawn in the right colours from the start. Printing always uses light.
7. **Charts** take their colours from the same tokens and redraw when the theme changes. Both palettes were checked for colour-blind separation and contrast against the card. How late money is uses one hue from light to dark (the old yellow-to-red ramp failed those checks).
8. **Also:** the offline page has its own light and dark colours. The installed app caches the stylesheet, font and theme script. M-Pesa codes are grey instead of pink, and the payment method icons have dark versions.
9. **Not in this step:** a saved per-user theme on the server, a high-contrast theme, and a design-tool (Figma) file.
10. **Update (2026-10-05): deep green and ink, a side menu, new Home.** The owner asked for research on other sites (Google Stitch named) and a new palette. Stitch builds on Material Design 3: tinted surfaces and hairlines instead of shadows, a pale "container" tint for the chosen item, rounder corners, and in dark mode a light accent with dark text on it. Money apps such as Mercury use one calm accent sparingly and keep red for money owed. So:
    - **Light:** indigo is replaced by deep green (`#146c4a`, white text 6.4:1), with near-black green-tinted ink (`#17201b`) on a faint green-grey page (`#f3f5f2`). Cards are flat (no shadow), 1rem corners. Information banners use a quiet blue tint instead of Bootstrap's cyan.
    - **Dark:** page `#0f1411`, cards `#171d1a`; the primary button is light green `#6fd3a1` with dark text `#05281a` (8.7:1).
    - **Charts:** series 1 is the green, series 2 amber, series 3 blue (was a second green); they also differ in lightness for colour-blind readers.
    - **Side menu** on laptops (992px and wider): Home, then the same Property / Tenants / Money / More groups as before, each link with an icon (Bootstrap Icons, self-hosted, now loaded and cached on every page). The chosen link has the green tint. Phones and tablets keep the top bar and its dropdown menus.
    - **Home:** figure tiles (occupancy, rent collected, arrears) for members who can see money, Today beside the setup checklist and summary, and shortcut tiles. **Dashboard:** the same figure tiles. New shared classes: `.page-head`, `.stat`, `.tile`, `.task-count`.
    - Other pages pick up the new colours and corners through the tokens; they are not otherwise redesigned in this step.

### D-112 — Unit by unit: analysis for small landlords that also helps large ones — ACCEPTED (2026-10-05, open to change before merge)
The owner asked for analysis that suits landlords with around 30 units as well as larger ones. D-110's breakdowns are mostly by property, and most of them show only when there is more than one property. A landlord with one or two buildings reads the portfolio one unit and one tenant at a time. Nothing new is stored. Rent expected and collected use the dashboard's split (`metrics._rent`), so the 12-month totals match the dashboard's for the units still in use.
1. **New page** (`/reports/units/`, `reports.units.unit_report`). It needs `dashboard.view_financial`, has a property filter, and is linked from the dashboard. It covers every rentable unit (live, not Inactive, on live properties) over the last 12 months to today.
2. **A 12-month grid per unit.** Each month shows whether its rent invoices were paid in time (the whole invoice paid by `overdue_after`), paid late, part paid, not paid and overdue, not due yet, or nothing billed. When a month has several invoices, the worst state counts. Each state has a mark as well as a colour, so the grid does not depend on colour alone.
3. **Per unit:**
   - The tenant, when the member has `tenants.view`, and the tenancy grade (D-052).
   - The monthly rent and when it took effect, the asking rent, and how far under it the rent is.
   - Collected against expected over 12 months, and the number of months paid in time.
   - Days empty in the window, counted from the window start or from when the unit came in (its first lease or its creation, whichever is earlier). Lost rent at the asking rent (asking × 12 × days ÷ 365) is shown as an estimate.
   - Overdue now and days late, when the member has `invoices.view`.
4. **Tiles:**
   - Units paid in full this month, with how many are overdue, part paid or not yet due.
   - Collection rate over 12 months.
   - Tenants who paid every billed month in time.
   - Average rent of occupied units, and the units under their asking rent with the total gap a month.
   - Empty days, the estimated loss, and the units empty now.
   - A list of leases ending or tenants moving out (notice, close or contract end) in the next 90 days.
5. **For any portfolio size:**
   - **By kind of unit:** grouped by the unit's label (e.g. "2 bedroom"), or by its type when there is no label. It shows units, occupancy, average rent, average asking rent, 12-month collection rate and the share of time empty.
   - **When in the month tenants pay:** confirmed payments of the last 12 months in five-day bands, with counts and the share of the money.
   - With more than 200 units in view (`UNIT_LIMIT`) and no property chosen, the page asks for a property instead of listing every unit. The figures still count all units, and the CSV lists every one.
6. **CSV** (`reports.export`): one row per unit with each month's state, then the by-kind and pay-day tables.
7. **Not in this step:** a chosen past month (the window always ends this month), natural sorting of unit codes (A2 before A10), and per-unit expenses or profit.

### D-113 — The reports dashboard in charts: every section drawn, profit beside rent, and what stands out in words — ACCEPTED (2026-10-05, open to change before merge)
The owner asked for `/reports/` to show everything in detail, in charts, designed professionally. Most of D-110's figures were tables. Nothing new is stored, and every figure comes from the functions the page already used (`metrics`, `finance.money_report`, `snapshots`), so the charts and tables always agree.
1. **Layout.** A wider page (`page-wide`, up to 1240px; `base.html` gains a `page_class` block). A sticky section menu: Overview, Rent, Profit, Occupancy, Arrears, Tenants. Every chart sits in a card with a title and a line saying what it shows, and its table below (open by default, or under "Show as a table"). There is a Print button; printing hides the menus and the folded tables, and uses the light palette.
2. **Overview.** Eight tiles, each with a 12-month trend line drawn as SVG on the server (`{% sparkline %}`, no script; missing months are gaps): rent collected, collection rate, occupancy, arrears (from month-end snapshots), rent expected, cash received, and this month's expenses and net operating income. Without profit and loss access, the last two are replaced by empty units. Then "What stands out" (`reports.insights.highlights`): up to eight sentences on what changed and what needs attention (collection rate against last month, rent collected against last year, the lowest-collecting property, money over 90 days late, how much the largest debts make up, empty units, the 12-month profit and the largest expense category, unmatched M-Pesa). Each sentence has an icon (good, needs attention, note) and the words say the same, so colour is not the only signal.
3. **Rent.** Rent expected and collected (bars) with cash received (line) over 12 months; the collection rate by month (its own chart, never a second axis); collection by property as stacked bars (collected, still to collect); how tenants paid as a doughnut with its table.
4. **Profit** (only with `reports.view_financial`). Income, expenses, net operating income and margin over the 12 months to the chosen month, with monthly averages; income and expenses by month (bars) with net operating income (line); expenses by category (doughnut); net operating income by property (bars, a loss in the late colour).
5. **Occupancy.** Units now as a doughnut (occupied, empty, reserved, in repair); the occupancy rate on the last day of each of 12 months (`metrics.occupancy_trend`, units counted as they are set up now); occupied and empty units by property.
6. **Arrears and tenants.** Arrears by days overdue with each bucket's share; the largest debts with their share as a bar; arrears at month end (stacked, as before); tenants by payment grade, coloured from good (green) through a neutral middle to bad (the late colours).
7. **Colours.** Two series colours added (`--chart-4` pink, `--chart-5` violet) and a neutral (`--chart-neutral`); light `--chart-1` moved to `#1a7f56` and the dark series stepped down so all five pass the colour-blind, lightness and contrast checks against each page surface. A sixth category or more shares the neutral grey.
8. **Not in this step:** a chosen period longer than 12 months, and per-unit profit.

### D-114 — Payment information in charts across the app: in time or late, methods, rings and month strips — ACCEPTED (2026-10-05, open to change before merge)
The owner asked for better graphs and charts everywhere payment information can be shown, as bars, circles, pie charts or another clear form. Nothing new is stored; every chart reads the figures its page already shows, and the same figures are always in a table on the page.
1. **One way to draw.** `static/js/charts.js` draws every `<canvas data-chart="ID">` from the JSON in `<script id="ID">` (`json_script`), so a page adds a chart with no new script and nothing inline (CSP stays `script-src 'self'`). `core/charts.py` builds the descriptions (`chart`, `series`, `doughnut`, `fold`). Bar, line and doughnut; stacked, 100% stacked (the tooltip keeps the real figure) and bars that run across, growing with their rows. Colours are the D-113 tokens: series 1–5 in a fixed order, a sixth or more folded into "Other" or the neutral grey; the late scale for how late money is. Charts redraw when the theme changes. `static/js/finance.js` is gone; profit and loss and cash flow use the same script.
2. **Paid in time or late** (`reports.patterns`). Each month's rents counted as paid in time, paid late, part paid, not paid and not due yet, from the same cells as the unit-by-unit page (`units.rent_states`, now shared). Shown as 100% stacked bars on the dashboard and the unit-by-unit page, with the share of rents due that were paid in time (rents not yet due are left out).
3. **Dashboard, Rent section.** The month's collection rate as a ring (`{% ring %}`, SVG on the server, no script, a dash when there is no rate); paid in time or late by month; when in the month tenants pay (amount by day range); payment methods over 12 months as stacked bars.
4. **Payments list.** Above the list, for the confirmed payments that match the filters (`payments.selectors.received`, in the organisation's currency, with a note when other currencies are left out): money received each day (spans under 62 days) or month (up to 36), how tenants paid as a doughnut with its key, and by property (top 8, the rest as "Other").
5. **Money pages.** Aged receivables: a doughnut of what is owed by how late it is, and each property's balance as stacked bars by bucket. Profit and loss: income and expenses by month with net operating income as a line, expenses by category as a doughnut (largest four and "Other"), net operating income by property (a loss in the late colour). Cash flow: money in and out with net cash, where money in came from as a doughnut, net cash by property. Rental income: money received each month (rent and the rest) with rent billed as a line, rent billed against received by property.
6. **One lease.** The lease account page shows the last 12 months of rent billed against paid, each paid bar coloured by how it was paid with its own key, and rings for months paid in time and rent collected. The tenant portal's lease page shows the same 12 months as a month strip (CSS bars, no script, a mark in each so colour is not the only signal) with a paid-in-time ring.
7. **Rings elsewhere.** Home: occupancy and this month's rent collected. Unit by unit: collected over 12 months, plus occupancy and rent collected by kind of unit as bars, and when tenants pay as bars.
8. **Not in this step:** charts in the PDFs, and a chosen period on the payments charts beyond the list's own date filters.

### D-115 — The assistant runs on OpenAI — ACCEPTED (2026-10-05, open to change before merge)

**Context.** D-091 built the assistant on Claude through the Anthropic API. The user chose OpenAI instead. At the middle level the two cost about the same: GPT-5.6 Terra at $2 / $12 per million tokens in / out against Claude Sonnet 5.5 at $2 / $10, about $42 against $40 per 1,000 questions by our estimate of 15,000 tokens in and 1,000 out each.

1. **`ai_assistant.client.OpenAIClient`** calls OpenAI Chat Completions (`https://api.openai.com/v1/chat/completions`) with the standard library, like the Anthropic client did. It takes and returns the same shape as before (text, `tool_use` and `tool_result` blocks), so the tool loop, confirm cards, the tenant assistant and WhatsApp are unchanged. A refusal, a bad tool call or a bad response fails the question as before.
2. **Settings.** `OPENAI_API_KEY` (no key: the assistant is not offered), `ASSISTANT_MODEL` (default `gpt-5.6-terra`) and `ASSISTANT_BACKEND` (default `ai_assistant.client.OpenAIClient`). Production refuses any other backend, so the privacy page always names the provider in use. Each client names its own key setting, and `client.enabled()` checks the key of the chosen backend.
3. **Reasoning off.** OpenAI refuses function tools with reasoning on Chat Completions for `gpt-5.6-terra` ("use /v1/responses or set reasoning_effort to 'none'"), so `reasoning_effort` is `none`. The questions are lookups and filling in confirm cards, which do not need it, and it is cheaper and quicker. If answers turn out weak, move the client to the Responses API, which allows reasoning with tools.
4. **Pages.** The privacy page, terms, help page, Ask settings and the portal Ask page now name OpenAI and the GPT model. What is sent and what is masked (D-091 item 4) is unchanged.
5. **Kept.** `AnthropicClient` stays in the code for development only; going back means changing the production check and those pages.
6. **[VERIFY]** with a real key: tool calls, the confirm step and a WhatsApp answer on `gpt-5.6-terra`, and the tokens recorded per question.

### D-116 — The assistant answers how-to questions from the help pages — ACCEPTED (2026-10-06, open to change before merge)

Staff asked the assistant how to use the app ("how do I record a cash payment?") and it could only say it did not know, because its tools read figures only. Fine-tuning a model is costly, goes stale with every change and would send our text to the provider for training; the help pages (D-063 item 2) are already written and reviewed like code. So the assistant reads them.

1. **`how_to` tool.** Takes one help topic (the slugs in `support/help.py`, listed with their descriptions in the tool's description) and returns that page's body as plain text, its links as "label [url]", and the page itself. `support.help.plain_text` renders only the topic's `body` block, so the text is exactly what the help page shows. Every member can read the help, so the tool has no permission check. The help page and the pages it links show under "Pages used".
2. **Prompt.** Figures come from the data tools; how to do something, where, what a feature does or why something happened comes from `how_to`, naming the page to go to. It never describes buttons or steps the help does not mention, and says so and suggests Help or Contact support when the help does not cover it. When a name or unit matches more than one thing it asks which one.
3. **Keeping it right.** Improving an answer means improving the help page: both staff reading Help and the assistant get the fix. A test renders every topic and keeps each under 6,000 characters.
4. **Not yet.** The tenant assistant keeps its own narrower tools; tenant help pages would come first.

### D-117 — Paystack for landlords without a Paybill — SUPERSEDED by D-140 (2026-10-13); was ACCEPTED (2026-10-06)

**Context.** Each landlord connects their own Paybill through their own Daraja app (D-045). Some small landlords have no Paybill and will not get one soon. Of the Kenyan aggregators compared (Paystack, Pesapal, Flutterwave, IntaSend, Kopo Kopo), Paystack has the clearest API: an M-Pesa prompt by `/charge`, signed webhooks, test keys, and 1.5% per M-Pesa payment, paid out to the landlord's bank (T+2 by default) **[VERIFY current fees and settlement]**.

1. **Daraja stays the main way.** `stk.stk_accounts(lease)` returns the Paybills with a passkey that collect for the property, as before. Only when there are none does it return the landlord's Paystack account. Payment requests from staff, the payment link, USSD and the assistant all go through `stk.send`, so they all pick up Paystack in the same way.
2. **Only a Platform Admin sets it up**, in Django admin (Paystack accounts). The landlord opens their own Paystack account, so the money goes to them and we never hold it (doc 14 D2). The admin pastes the landlord's secret key: `sk_test_…` for test mode, `sk_live_…` for live. The key is encrypted like the Daraja keys, never shown again, and a blank field keeps it. Saving makes a `PaymentAccount` of type "M-Pesa through Paystack" (one per organization) and a `PaystackCredentials` row, and writes an audit event naming what changed, never the key. "Check the connection" proves the key works. Landlord staff see the account on M-Pesa settings, read-only.
3. **Requests.** The request is a normal `StkRequest`. Its `checkout_request_id` is our own reference, `lpms-<public id>`, which is sent to Paystack. The charge's email is the primary tenant's, or `<unit reference>@PAYSTACK_EMAIL_DOMAIN` (default: the site's host). A refusal is kept as FAILED and shown, as with Daraja.
4. **Webhook.** It arrives at `/hooks/ps/<token>/`. Each landlord's account has its own token, which only finds the account. The proof that the webhook came from Paystack is the `x-paystack-signature`: an HMAC-SHA512 of the body made with that account's secret key. A bad signature gets 401, and an unknown token 404. `PAYSTACK_ALLOWED_IPS` can also be set. When `charge.success` arrives, an `MpesaTransaction` (source Paystack) is stored once per code and the payment is confirmed on the lease the request named. The code is the M-Pesa receipt if Paystack sends one in `receipt_number`, so a code typed from the tenant's SMS is still recognised; otherwise it is `PS<Paystack id>`. A currency other than KES is flagged. Paystack's fee goes in the note. `charge.failed` closes the request as "Not paid". Other events are acknowledged and ignored.
5. **Checking.** "Check" on a waiting request asks Paystack's `/transaction/verify`. Unlike a Daraja query, its answer carries the payment, so a success is recorded straight away.
6. **Not included.** No Paybill number is shown on the payment link for a Paystack account. Paystack payments are not part of the M-Pesa statement import. There is no self-service setup for landlords (the admin does it). There are no Paystack card payments.
7. **[VERIFY]** with a Paystack Kenya test account and then a live one: the `/charge` reply for M-Pesa, whether `receipt_number` is the M-Pesa code, the webhook fields (`paid_at`, `fees`, `authorization`), the KES limits and the source IPs of webhooks.

### D-118 — Signing the agreement and move-out statement electronically — ACCEPTED (2026-10-06, open to change before merge)

**Context.** Leases and move-out statements are printed, signed by hand, scanned and uploaded (D-073), or never signed at all. Tenants are often away when a lease is drafted or a deposit is settled. Kenya's Evidence Act and the Kenya Information and Communications Act recognise electronic signatures, and an "advanced" signature from a licensed certification service provider carries a stronger presumption. Which documents need more than the simple kind was researched on 2026-10-06 (item 10). We build the simple kind in the app now, with a strong record of who signed what and when, for the documents where the law allows it. A licensed provider comes later, and only if an advocate says a document we need needs one.

1. **What is signed.** The tenancy agreement of a draft or active lease (a renewal is a new draft lease, so renewals are covered) and the move-out statement once it applies (D-073). Staff with the new capability `leases.esign` (Owner and Manager) choose "Send for e-signature" on the lease. The PDF is drawn once, frozen and stored with its SHA-256 fingerprint, so what is signed cannot change underneath the signers. Its signature block says it is signed electronically and points to the signing record, instead of blank lines. One waiting request per lease and kind; a new one needs the old one cancelled.
2. **Who signs.** Every tenant on the lease, each by a private link, and the landlord, by a staff member with `leases.esign` signing in the app. Each tenant needs a phone. The request expires after 14 days.
3. **The tenant's link.** `/sign/<token>/` needs no login. Only the SHA-256 of the token is stored; the raw token appears only in the SMS. It is sent by SMS when the tenant has not stopped SMS. Otherwise, or if the SMS fails, staff make a new link and the page shows it once so they can share it themselves. A new link stops the old one working.
4. **Proving it is the tenant.** Before seeing the document the tenant asks for a 6-digit code sent to the phone on file. The code is stored as an HMAC and works for 10 minutes and 5 tries. Resends wait 60 seconds and are capped at 5 an hour per signer, with per-IP limits on the page. The code SMS is a security message and goes even if the tenant stopped SMS. A correct code marks the browser session as verified for that signer for 30 minutes.
5. **Signing.** The tenant reads the PDF, types their full name and ticks "I agree to sign electronically, and that this has the same effect as signing on paper". We record the time, IP address and browser, with the code check. The tenant can instead decline with a reason, which closes the request and tells whoever sent it. Staff sign for the landlord the same way: typed name and tick, with the staff user, IP address and time recorded.
6. **When everyone has signed.** A signing record page (document, number, fingerprint of the original, each signer with role, masked phone, how they were checked, time and IP address, and the check link) is added after the original pages with pypdf. The fingerprint of the signed PDF is stored, and the copy is filed in the lease's documents, shared with the tenant if the sender chose that. Whoever sent it gets an in-app notice. The tenant's link then offers the signed copy after a code check.
7. **Checking a copy.** The check link `/s/<code>/` shows, without a login, who signed what and when, and both fingerprints, so anyone can compare a copy's SHA-256. It is rate-limited and not indexed, as with letters (D-048).
8. **Audit.** `esign.create`, `esign.send_link`, `esign.sign`, `esign.decline`, `esign.cancel`, `esign.complete` and `esign.evidence`. The signing pages leave the rest of the app alone; nothing about rent or the lease changes when a document is signed. Activating a signed draft lease is still a separate step.
9. **Not in this step.** No licensed provider or certificate-based signing; no witness signature; no signing other documents or uploaded PDFs; no signing inside the tenant portal (the SMS link is used); no reminders before expiry.
10. **What the law allows (researched 2026-10-06; to be confirmed by an advocate before launch).**
    - *Short leases only.* Land Act 2012 s.58: a short-term lease (two years or less without an option to renew, or periodic) "may be made orally or in writing" and is not registrable. If it can be oral, a typed name with an SMS code check is enough, and KICA says a contract is not invalid only for being electronic. Law of Contract Act s.3(3) bars a suit on a contract for the disposition of an interest in land (its s.3(6) "transfer" includes a lease) unless it is in writing, signed by all parties and each signature is attested by a witness present; s.3(6) accepts an *advanced* electronic signature, which practitioners read as needing a licensed certification service provider. So: an agreement whose end date is more than two years after its start cannot be sent (`is_long_lease`; the page says to print it and sign on paper before a witness), and the sender must tick that the lease gives no option to renew, since the app cannot read one in the free-text terms. The tick is stored (`no_renewal_option`, a check constraint for the agreement), audited and printed on the signing record. The advocate should confirm that s.58 governs short tenancies over s.3(3).
    - *Move-out statement.* An account of money, not an interest in land: the simple kind is enough.
    - *Court and tribunal.* Evidence Act s.106B(4) wants electronic records produced with a certificate signed by a person in a responsible position; the Court of Appeal treats it as mandatory. The request page offers an evidence pack (`leases.esign` only, audited `esign.evidence`): the PDF as sent, the signed copy, `record.json` (fingerprints, signers with phones, IP addresses and browsers, and the request's audit trail) and `certificate.pdf`, a s.106B(4) certificate that identifies the files by SHA-256, says how they were produced, covers the s.106B(2) conditions and leaves name, position, signature and date blank.
    - *Keeping copies.* Six years after the lease ends, or after signing if later (Limitation of Actions Act s.4, contract claims; this covers the five years of Tax Procedures Act s.23 for rental records). The request page and the certificate show the date. Documents are archived, never deleted, so nothing removes a signed copy; deleting after that date, as the Data Protection Act asks, comes with the tenant data retention work (D-107).
    - *Never electronically.* Wills, negotiable instruments and documents of title (KICA s.83B); none is signed here.
    - *Stamp duty* is due on tenancy agreements within 30 days of signing, however they are signed; the app does not handle it.
    - **[VERIFY with an advocate]** the points above, the consent wording and the certificate wording.

### D-119 — Owners teach the assistant their own rules — ACCEPTED (2026-10-06, open to change before merge)

The assistant knew the figures (D-091) and the app's help (D-116) but not how each organization works: office hours, deposit refunds, house rules, rubbish days. Staff and tenants ask those most. An owner now writes them down for the assistant.

1. **`KnowledgeEntry`** (`ai_assistant`): organization, title (the question or topic, 120 characters), answer (1,500), an optional property (empty means all), "tenants can be told this", who added and last changed it. At most 100 per organization. Removed entries are deleted; the audit log keeps their titles.
2. **Who manages.** `organization.manage`, as with the assistant's switches: the page `/ask/knowledge/`, linked from the Ask settings. Adding, changing and removing are audited (`assistant.knowledge_added`, `_changed`, `_removed`).
3. **No contact details.** An entry whose title or answer the assistant's mask would change (phone numbers, emails, KRA PINs, long numbers) is refused with a message to say where to find them instead, so what the owner wrote is what the assistant says, and D-091 item 4 holds.
4. **Staff tool `knowledge`.** Entries on all properties or on the properties the member can see; no capability beyond membership, as they are the organization's own guidance. The prompt sends questions about how the organization does things there, before the general help.
5. **Tenant tool `landlord_notes`.** Only entries marked for tenants, in the tenant's landlords' organizations, on all properties or on the tenant's own. Used only once "Answer tenants" is on, like every tenant answer. The tenant prompt now allows these topics.
6. **Picking entries.** When all of a member's entries come to 6,000 characters or less, all are given, so wording and Swahili questions still find them; above that, the five sharing the most words with the question (title words count double), or, with no match, the titles so the model can ask again.
7. **Not done.** No per-entry language, ordering or expiry; owners write in the language their people ask in, or both.

### D-120 — An organization's own SMS sender name — ACCEPTED (2026-10-06, open to change before merge)

Every SMS went out under the platform's sender ID (`AT_SENDER_ID`). Agencies and larger landlords want tenants to see their own name. Africa's Talking lets one account hold many sender IDs and choose one per message (`from`), so this needs no second account.

1. **Default stays the platform's name.** An organization uses its own name only once a Platform Admin has approved it. Until then, after a rejection or after cancelling, nothing changes.
2. **Asking.** On the Subscription page, under SMS credit, a member with `subscription.manage` asks for a name: 3 to 11 letters or digits, at least one letter, no spaces, not the platform's own. The organization keeps it as `sms_sender_id` with `sms_sender_status` REQUESTED and the time. Operators (`OPS_ALERT_EMAILS`, else `ADMINS`) get an email. Asking again for a different name, or cancelling, also drops an approved name. Audited: `organization.sender_id_requested`, `organization.sender_id_cancelled`.
3. **Registering and approving.** Done outside the app: the landlord's letter on letterhead (and registration papers) go to Africa's Talking, which registers the name with Safaricom, Airtel and Telkom for a one-off fee per network, in about 1 to 3 weeks. It is a paid add-on: the fee is charged to the landlord by invoice or as an SMS credit adjustment. Then the admin selects the organization in Django admin (filter "SMS sender name status") and runs **Approve SMS sender names**, or writes a reason in the note and runs **Reject SMS sender names**. The note shows on the landlord's Subscription page. Audited: `organization.sender_id_approved`, `organization.sender_id_rejected`.
4. **What uses it.** Tenant notifications (every SMS from `notifications.delivery`), portal invitations and e-signature links and codes. Login codes, password and security alerts, and staff invitations keep the platform's name, because they speak for NyumbaPMS.
5. **Falling back.** A name may be registered on one network before another. If Africa's Talking answers 402 (InvalidSenderId) for the organization's name, the same message goes again under the platform's name at once, and a warning is logged. **[VERIFY the 402 code in the sandbox.]**
6. **Replies and STOP.** Alphanumeric names cannot receive replies, so STOP handling (D-044 step 4) is unchanged and still platform-wide.
7. **Not done.** No self-service payment of the registration fee; no per-network approval status; no organization's own Africa's Talking account.

### D-121 — The product is NyumbaPMS, at app.nyumbapms.com — ACCEPTED (2026-10-06, open to change before merge)
Supersedes the name in D-001. The owner has the domain nyumbapms.com.

1. **Name.** Everything a person sees says **NyumbaPMS**: page titles and the brand in the menu, the installed app's name, login and security SMS, staff invitations, emails and ops alert subjects, the authenticator label (issuer), push notifications, terms, privacy, status and help pages, e-signature certificates, platform invoices (seller name) and the default `LEGAL_NAME`. The webhook `User-Agent` is `NyumbaPMS-Webhooks/1`.
2. **Address.** The app runs at `https://app.nyumbapms.com` (`ALLOWED_HOSTS=app.nyumbapms.com`, `SITE_URL=https://app.nyumbapms.com`). The bare `nyumbapms.com` is kept free for a marketing page. The site address is 25 characters, the same length as the example in the SMS payment-link sum above, so SMS lengths do not change. Email goes from `NyumbaPMS <no-reply@nyumbapms.com>`; the domain needs SPF, DKIM and DMARC records for the email provider before launch.
3. **What keeps the old name**, because changing it breaks something and nobody sees it: the repository and folder, database name and roles (`landlordpms`, `landlordpms_owner`), `DB_APP_NAME` values, `/srv/landlordpms` and `/etc/landlordpms.env` in `deploy/`, the `landlordpms_append_only()` function in migrations, the `X-LPMS-*` webhook headers (a published contract), the `lpms-` service-worker cache and `lpms.offline.rounds` storage key (renaming would drop offline data on phones) and the `lpms-` Paystack reference prefix.
4. **Outside the app, to do.** DNS for `app.nyumbapms.com` and the TLS certificate; SMS sender ID `NYUMBAPMS` registered with Africa's Talking (`AT_SENDER_ID`); M-Pesa callback URLs registered again under the new address (`daraja_sandbox_check` confirms); VAPID subject on the new domain. "Nyumba Zetu" is a competitor (doc 16): check the name with a lawyer or KIPI search before printing materials.

### D-122 — The advocate handover pack — ACCEPTED (2026-10-06, open to change before merge)
D-109 recorded the letters and how they were served so that a matter could later be handed to an advocate. Until now staff did that by exporting a statement, finding the letters and scans, and writing a covering note. Advocates ask for the same things every time: who the parties are, the premises and lease, the amount and how it is made up, proof that payment was asked for and how, what the tenant said, and records they can produce in court.
1. **One ZIP, made on demand.** `/letters/lease/<lease>/handover/` shows the amount, what to check and what the pack will hold; the download is a POST that returns `<lease>-handover-YYYYMMDD.zip`. Nothing new is stored and there is no migration: the pack is drawn from the records each time over the whole tenancy (D-048 `tenancy`, renewals and moves included). The audit event `handover.pack` records the purpose, the balance, what is owed past grace days, the advocate named, the file count and the SHA-256 of the ZIP, so the copy handed over can be identified later.
2. **Two purposes.** Rent arrears or a disputed move-out. Both hold the same files; the purpose sets the brief's title and which checks are shown.
3. **What it holds.**
   - `00-brief.pdf`: prepared for (optional advocate or firm), landlord and contacts, premises and kind of property, each tenant (kind, contact person, phones, email, ID and KRA PIN), each lease of the tenancy with its dates and status, rent now, due day and grace, notice period, deposit held, notice given and end; the balance, what is owed past grace days, how late (aging buckets), the oldest late date, the last payment and what is unpaid oldest first (40 rows, then "and N more"); every notice and demand letter with its service record, and notices recorded on the lease; the latest 30 follow-ups; what to check before acting; an optional note from the landlord (1,000 characters); and the list of enclosed files with their SHA-256.
   - `01-statement.pdf` and `.csv`: every ledger row of the tenancy with a running balance. CSV text that a spreadsheet could run as a formula is prefixed with `'`.
   - `02-payments.csv` (references, who recorded and confirmed, reversals) and `03-follow-ups.csv`.
   - `04-letters/`: each NTV and DEM letter's PDF as issued (withdrawn ones too, as the brief says they were withdrawn), and the signed copy recorded at service.
   - `05-move-out-statement.pdf` when the move-out statement applies (D-073).
   - `06-condition/`: completed condition reports with move-in beside move-out (landscape PDF) and their photos.
   - `07-documents/`: the documents of the tenancy's leases and its tenants, except the signed copies already filed under letters.
   - `record.json` (the figures, letters, the letters' audit trail and each file's SHA-256) and `certificate.pdf`, an Evidence Act s.106B(4) certificate in the D-118 form that identifies every file by SHA-256 and leaves name, position, signature and date blank.
4. **Who may.** `leases.issue_notice` on the property (Owner and Manager); no new capability. Elsewhere it is a 404. Inside the pack each part follows the member's own permissions: ID numbers, KRA PINs and tenant documents need `tenants.view_sensitive`, documents need `documents.view`, payments need `payments.view` and condition reports need `inspections.view`. The brief says when something was left out for that reason.
5. **What to check before acting** [VERIFY with counsel]. For arrears: nothing owed past grace days; no demand recorded as served; a served demand whose days have not run out; an amount above KES 1,000,000, the Small Claims Court limit; a deposit still held that may have to be set off. For a move-out: no move-out statement yet, and no completed move-out or move-in condition report. For both: the latest follow-up disputes the amount or holds a promise not yet due; payments waiting for review; commercial or mixed-use property (Cap. 301 and the Business Premises Rent Tribunal); no signed tenancy agreement among the documents; a tenant with no ID number.
6. **Links.** The lease page's "Notice and demand letters" card has an "Advocate pack" button (not on drafts), and the notices page links to it.
7. **Not in this step:** sending the pack to an advocate from the app or giving an advocate a login; advocate contacts kept per organization; tracking the matter after handover (court dates, costs, outcome); distress for rent through an auctioneer; a pack for many tenants at once; Swahili.

### D-122 — The pilot runs on one Hetzner server — ACCEPTED (2026-10-06, open to change before merge)
For the first year, with at most about 10 landlords. Compared with DigitalOcean, Railway and Render (prices from 2026 comparison sites): Hetzner is the cheapest, and the app runs on it unchanged, because cron, backups to a local folder, the restore test and uploads on disk all assume one Linux server. Railway and Render would need uploads moved to object storage and the 15 jobs and backups reworked. DigitalOcean is the fallback (same setup, about twice the price, optional managed PostgreSQL).

1. **The server.** Ubuntu 24.04, 2 vCPU and 4 GB, in Germany, with Hetzner's server backups on. PostgreSQL, Redis, gunicorn and nginx on the same machine. None of the hosts compared has an African region **[VERIFY: whether the Data Protection Act requires tenant data in Kenya]**.
2. **Files** (`deploy/`): `setup.sh` (a fresh server to a running site; safe to run again; stops to have the settings filled in and when DNS is not ready), `landlordpms.service` (gunicorn, 5 workers, graceful reload), `nginx.conf` (static files, a default server that drops other host names, no `/media/`: uploads only go through the app's checks), `update.sh` (backup, code, packages, `migrate` as the owner role, access catalog, static files, `check --deploy`, reload, health check), `offsite.sh` (nightly encrypted `rclone copy` to a Hetzner Storage Box with snapshots; copy, never sync) and `monthly_check.sh`. `gunicorn` is added to `requirements.txt`.
3. **Secrets.** `setup.sh` makes `SECRET_KEY`, `FIELD_ENCRYPTION_KEYS`, `AT_CALLBACK_TOKEN` and both database passwords. `/etc/landlordpms.env` is readable by root and the app user; the owner role's password is in `/etc/landlordpms-owner.env`, readable by root only, and used only for `migrate`.
4. **Code on the server.** Uploaded as a tar file from the developer's computer, or pulled from GitHub with a read-only deploy key. Either works with `update.sh`.
5. **Server hygiene.** Firewall (ssh, http, https), fail2ban, key-only ssh once a key is present, automatic security updates, 2 GB swap, time zone Africa/Nairobi.
6. **Not done.** No staging server (restore a backup into a scratch database to try a risky migration, doc 18 §4); no zero-downtime PostgreSQL upgrades; no container images.

### D-123 — Owners edit the organization, expenses and repairs in place — ACCEPTED (2026-10-06, open to change before merge)
The Owner role already held every capability, but some things could not be changed from the app: the organization's own details only in Django admin, and an expense or a repair's description only by voiding or cancelling and starting again.

1. **Settings page** (`/organization/`, `organization.manage`, "Settings" in the More menu). Edits the name, type, the name tenants see (`brand_name`, printed on letters and notices), office phone and email (`billing_phone`, `billing_email`, printed on notices and the handover pack), the footer on letters, invoice lead days (0 to 28) and whether part months are charged by the day. Changes apply to letters and invoices made from then on. Audited as `organization.update` with each changed field. Below the form it links every other settings page the member may open (messages and reminders, wording, letters, charge types, M-Pesa, expense categories, repair targets, tax residence, assistant knowledge, staff, roles, subscription), so they are found in one place. KRA PIN, VAT, logo and brand colour stay off the page because nothing uses them yet.
2. **Editing an expense** (`/expenses/<id>/edit/`). Property, category, supplier, what was paid for, amount, date, method and reference, with the same checks as recording (the expense's own reference is not a duplicate; a category, supplier or property archived since may be kept but not newly chosen). Who: a member with `expenses.approve` on the property, for any submitted or approved expense; the member who recorded it, while it still waits for approval. The status never changes, so maker/checker holds: a recorder's edit still needs approval, and only approvers touch approved figures. Moving it to another property needs the same power there. Voided and rejected expenses stay as they were. Audited as `expenses.edit` with before and after. The receipt is still added on the expense page.
3. **Editing a repair** ("Edit what is wrong" on the request page). What is wrong, the details and the kind, while the request is open, for members with `maintenance.update`. The request's history gets a line saying what changed, and it is audited as `maintenance.edit`. The unit is not editable: a tenant's request is tied to their lease.
4. **Not in this step:** editing payments (still corrected by reversal), approved meter readings, issued invoices (adjustments and credit notes), or issued letters; bulk edits.

### D-124 — Fuller Terms of Service and Privacy Policy — ACCEPTED (2026-10-06, open to change before merge)

The D-095 texts were short and plain. Before launch they need the clauses a binding SaaS agreement normally has, and the notice section 29 of the Data Protection Act, 2019 asks for. Compared against other Kenyan property software terms (Jengo PMS, HOMUtopia) and Buildium, and checked against the Act, the Data Protection (General) Regulations, 2021 and ODPC guidance.

1. **Terms** now have: definitions; how acceptance is recorded (audit log, evidence); not legal or tax advice; never holding money (now naming Paystack); account and Owner responsibility; landlord duties as controller, including sensitive data (family details) and tenancy law; acceptable use (Computer Misuse and Cybercrimes Act); other providers; the assistant; fees, trial, renewal, 14 days' grace then read-only, refunds, 30 days' notice of price rises (matching `subscriptions/services.py`); data ownership and our IP; the processor contract (instructions, confidentiality, security, sub-processors with a right to object, transfers, breach notice within 48 hours, help with requests and audits, deletion); availability; suspension; ending (30 days to export, deletion within 90 days); warranties (Consumer Protection Act carve-out); liability (no indirect loss; cap at the greater of 12 months' fees and KES 10,000); indemnity; events outside our control; a section for tenants in the portal; changes; Kenyan law, negotiation, mediation, then arbitration in Nairobi under the Arbitration Act, 1995 (courts for urgent relief and small claims); notices, transfer, severability, waiver, language.
2. **Privacy** now has: who the controller is in each role; sources of data; e-signature records; sensitive data; a table of purposes and section 30 bases; no automated decisions with legal effect; sub-processors including Paystack; disclosures to authorities; transfers outside Kenya (sections 48 to 50, hosting included); a retention list (message text after `MESSAGE_TEXT_DAYS`); the full list of rights (portability, withdrawal, direct marketing within 7 days); breach notice (72 hours to the Commissioner, 48 hours to landlords); device storage (theme, offline queue); children (section 33).
3. **New settings:** `LEGAL_REGISTRATION`, `LEGAL_ADDRESS`, `ODPC_REGISTRATION`. `VERSION` is 2026-10-06, so signed-in users are asked to accept again.
4. **[VERIFY] with a Kenyan advocate before `LEGAL_REVIEWED=true`:** the liability cap and its KES 10,000 floor, refunds of SMS credit, the 30/90-day deletion periods, the arbitration clause and appointing body, whether the ODPC registration covers us as processor, the cross-border safeguards for Hetzner, OpenAI, Meta and Sentry, and the time limits for answering requests under the Regulations.

### D-125 — Staff send an invoice, a receipt or a message to one tenant in their own words — ACCEPTED (2026-10-07, open to change before merge)
Invoices and receipts went to tenants only by the automatic messages, in the organization's fixed wording. Owners asked to send them again, by the channel they choose, with words of their own.

1. **Who:** members with `messages.send` on the property (Owner and Manager by default; an Owner can give it to any role or person). Without it the page is a 404 and the buttons are hidden.
2. **Where:** "Send to tenant" on an issued invoice (not draft or void), "Send receipt" on a payment that has a receipt, "Send message" on a tenant. All open `/messages/send/?invoice=|payment=|tenant=`. For an invoice or payment the message can go to any tenant on the lease, the payer or main tenant first.
3. **What:** the staff member picks email, SMS or WhatsApp. The text starts as the organization's wording of the automatic message (`invoice_issued`, `payment_received`) filled in, or a greeting for a plain message, and can be changed freely. Email has its own subject and carries the invoice or receipt PDF, with the usual stop-emails line. SMS gets the organization's name added and must fit three SMS. WhatsApp goes with the already approved `announcement` template ("Message from {org}." + the text + how to stop), so no new Meta approval is needed; line breaks become spaces and it is kept under 900 characters.
4. **Rules kept:** the tenant's stops and consent apply (WhatsApp needs the tenant's agreement), SMS needs credit, SMS and WhatsApp wait for quiet hours unless "send now" is ticked. A channel that cannot reach the tenant is shown with the reason instead of the form.
5. **Records:** each send is a `Message` of the new type `tenant_message` ("Message from staff"), linked to the lease, invoice or payment, with `created_by`; audited as `message.composed`. The form's one-time id stops a double click sending twice. The type is not on the message settings or wording pages: there is nothing to switch off or reword.
6. **Not in this step:** sending to several tenants at once (announcements do that), owner statements (already emailed), attaching other documents, or sending to an address that is not on the tenant.

### D-126 — Landlords pay the subscription online: our Paybill (Daraja) or our Paystack — ACCEPTED (2026-10-08, open to change before merge)
Subscription invoices (D-060) were paid by Paybill or bank and recorded by hand by a Platform Admin. Landlords now pay them online, into our own accounts. There are two ways, so either can be stopped without stopping the other.

1. **Our Paybill through Daraja.** "Pay with M-Pesa" on the Subscription page sends a prompt to the phone given (STK push), for what is left on the invoice rounded up to whole shillings. Safaricom's callback names the request, so the payment goes on that invoice. A landlord who pays the Paybill by hand types the invoice's **account number** (the invoice number's digits, e.g. `2026000123`; the full `LPM-INV-…` also works). The C2B confirmation is matched by it. The account number is on the page and the invoice PDF.
2. **Our Paystack account.** "Pay with card or M-Pesa (Paystack)" opens Paystack's checkout page (`/transaction/initialize`). The signed webhook settles it. So does the verify call when the landlord comes back, whichever is first. The Content Security Policy's `form-action` allows `https://checkout.paystack.com` for this redirect.
3. **Switching.** A way is offered when its keys are set (`PLATFORM_DARAJA_*` + `PLATFORM_CALLBACK_TOKEN`, or `PLATFORM_PAYSTACK_SECRET_KEY`) and it is on in Django admin (Subscriptions → Online payment switch). Switching needs no restart. Callbacks for payments already made still arrive when a way is off.
4. **Once per code.** Every payment is stored once as a `PlatformTransaction` (unique code: the M-Pesa receipt, or `PS<id>` for Paystack). A prompt's callback and its C2B confirmation, or a webhook and a verify, therefore count once. Each attempt is a `PlatformCheckout` (pending, paid or failed). The daily job closes attempts unanswered after 24 hours, and a late Paystack success still counts.
5. **Money that cannot go on an invoice** stays UNMATCHED and is emailed to `OPS_ALERT_EMAILS`. This covers an unknown account number, an invoice already paid, another shortcode, or another currency. An overpayment of a shilling or more is applied and emailed too. The admin records it by hand (an invoice payment or an SMS top-up) with the same code, which links it.
6. **Callbacks:** `/hooks/plat/<PLATFORM_CALLBACK_TOKEN>/<confirm|validate|stk|ps>/`. The token is compared in constant time, and the optional `MPESA_ALLOWED_IPS`/`PAYSTACK_ALLOWED_IPS` apply. Paystack's events must also carry our key's HMAC-SHA512 signature, and only references of our own checkouts are acted on. The app is entirely apart from landlords' rent accounts (the `mpesa` app); only the HTTP clients are shared.
7. **Setup:** `manage.py platform_payments` checks both keys and prints the URLs, and `--register` registers our Paybill's C2B URLs with Safaricom. The launch check reports which ways are set and whether they are live. Production refuses to start with half a Daraja setup, a short callback token or a key that is not `sk_test_`/`sk_live_`.
8. **Reviewed with it:** the landlords' rent integrations (D-045, D-117) and Africa's Talking. A receipt or Paystack code already stored for another landlord's account is now never linked to this account's request. A C2B confirmation counts only through the account that holds the code. An Africa's Talking reply with an unreadable status no longer raises. A sender name refused for the whole request now also falls back to the platform name.

### D-127 — Agencies: branches that give access, fixed monthly fees and the agency's fee income — ACCEPTED (2026-10-09, open to change before merge)
Letting agencies manage many owners' properties from more than one office. Branches existed as a field but did nothing, an agency's fee could only be a percentage, and nothing showed the agency what it earned.

1. **Branches give access.** A member can be given branches as well as single properties (staff page and invitations). They then see every property in those branches, including ones added later. Access is the union of the properties given one by one and the branches' properties (`accessible_property_ids`). An archived branch gives nothing. Giving branches follows the same rule as giving properties: nobody grants more than they hold.
2. **Managing branches:** `/organization/branches/` (`organization.manage`, on the Settings page). Add, rename, set an office phone and email, archive and restore. A branch with live properties cannot be archived. The property form has a branch choice for members with `organization.manage` when the organization has branches; moving a property to another branch changes who sees it, so only they may do it. The property list filters by branch.
3. **Fixed monthly fee.** A property can have a fixed fee (`management_fee_fixed`, KES a month) on top of, or instead of, the percentage of rent collected. It is charged for every month the property is managed, from the month it was added through the month it was archived, even when nothing is collected; so the amount due to the owner can be negative. The statement, its PDF and the owner account show it ("10% of rent + KES 2,000.00 a month") with a note.
4. **Fee income report:** `/reports/fees/` (`reports.view_financial`, CSV with `reports.export`). The last 12 finished months, worked out exactly as on the statements: by month, by branch, by owner (jointly owned properties under the main owner) and by property, with the share of all fees and fees as a part of rent collected. Only the properties the member sees are counted, and properties without any fee are listed so they are not forgotten.
5. **Not in this step:** fees on other charges, fees billed to the owner as invoices, VAT on fees, per-branch bank accounts or M-Pesa numbers, and branch managers approving each other's work.

### D-128 — The owner portal — ACCEPTED (2026-10-09, open to change before merge)
Owners of the properties an agency manages asked to see their statements without waiting for an email. Until now the only way was a Viewer seat, which takes a staff seat and shows more than the owner's own figures.

1. **Invitation.** On the owner page, members with the new `owners.invite_portal` capability (Owner and Manager) invite the owner. The link goes by SMS to the owner's phone and, if they have one, by email. It works for 7 days; a new one cancels the pending one. Only someone who sees every one of the owner's properties may invite, cancel or remove access, because the portal shows the owner all of them. The owner needs a phone and at least one property.
2. **Claiming** needs a login with the invited phone, verified (as the tenant portal, D-055). There is no email verification, so an email link alone is not enough. One login can hold owner records in several organizations. Accepting, inviting, cancelling and removing are audited (`owner_portal.*`).
3. **What the owner sees** (`/owner/`): the last 12 months (collected, fee, due, paid, still to pay), payments made to them (voided ones left out), the statements the office sent them (the PDF as sent), the statement of any of the last 24 months with its PDF, and their properties with units, how many are let and the fee. Tenant names are left out, and expense descriptions are not links. Every page is `no-store`.
4. **Who sees what:** everything starts from the user's live owner accounts. A revoked account, or an archived organization, closes the portal. Anything else is a 404. A user without a staff seat lands in the tenant portal, then the owner portal, then onboarding.
5. **Not in this step:** owners approving expenses or repairs, messages between owner and office, owner payout bank details, and an owner logging in by email.

### D-129 — Late fees: the landlord's rule, staff approve each fee — ACCEPTED (2026-10-09, open to change before merge)
D-046 item 4 designed in opt-in late fees, off by default. Pilot landlords asked to charge them. The owner chose how a fee is worked out (a fixed amount, or a percentage with a maximum) and that staff approve every fee before a tenant is charged.

1. **The rule** is on the Settings page (`/organization/`, `organization.manage`): no late fees (the default), a fixed amount per late invoice, or a percentage of what is unpaid with an optional maximum. Switching on sets `late_fees_from` to that day, so invoices already overdue never get a fee. Changing the rate later keeps that day. Switching off clears the rule and skips fees still waiting.
2. **The daily job** (`billing_daily`) proposes one fee per invoice still unpaid after its due date plus the lease's grace days. A fee is proposed only for invoices that went overdue on or after `late_fees_from`. An invoice that is itself a late fee never gets one, so fees never pile on fees. A proposed fee is `LateFee` PENDING, at most one per invoice, enforced in the database. Nothing is charged and the tenant is not told.
3. **Approving** on `/billing/late-fees/` (`invoices.generate` on the property; a home task "late fees to approve"). Staff tick fees and choose Charge or Skip, with an optional reason. Charging works the fee out again from what is still unpaid. It then issues an invoice of its own, with one line of the system "Late fee" charge type (made on first use), due that day plus the lease's grace days. That invoice goes in the ledger, sends the usual invoice message and is audited (`invoice.issue`, `late_fee.charge`). A fee whose invoice was paid or voided before approval is skipped, never charged. The daily job also skips such fees. Skips are audited (`late_fee.skip`), and a skipped fee never comes back.
4. **Not in this step:** charging without approval, a second fee on the same invoice as it stays unpaid (daily or monthly interest), fees on deposits alone, and waiving a fee once charged other than by voiding its invoice. **[VERIFY with counsel]** that a late fee clause in the lease is needed, and what amount is reasonable.

### D-130 — Estates, water vendors and garbage collectors: their own words, same billing — ACCEPTED (2026-10-11, open to change before merge)
Other Kenyan billers (a Bomahut comparison) charge many households monthly and chase arrears like a landlord, but do not talk of tenants and units. The owner chose to serve them on the existing invoicing, meters, M-Pesa matching and portal, without touching landlord workspaces.

1. **Three new organization types** (`ESTATE`, `WATER`, `WASTE`) beside individual, company and agency. Chosen at sign-up or on the Settings page. No data model change beyond the choices.
2. **Wording only.** `accounts/vocabulary.py` maps tenant, unit, property, lease and rent to member/house/estate/membership/service charge, customer/connection/zone/account/water charge, or client/pickup point/route/contract/collection fee. `Organization.vocab` and the `vocab` template variable carry it. Landlord, company and agency organizations, and any unknown type, get the original words, so nothing changes for them.
3. **Done:** the staff menu and the headings and buttons of the tenants, units and properties lists; the English subject of the rent reminder and overdue emails ("Service charge reminder"), the email footer ("{org} bills you") and the "Lease" label on the invoice PDF. SMS bodies were already neutral. WhatsApp wording is untouched, as Meta approved it. The Swahili stays as it is **[VERIFY]**. The tenant portal lease page (`/my/leases/`) uses the lease organization's words through the `word` template filter, since portal pages are not opened as staff of the organization: headings "Your service charge over 12 months" and "Your membership", and the rent line label. For these organizations the portal also leaves out the deposit and notice-to-leave rows and the "Moving out" card, and the notice page answers 404 (checked on the server, not only hidden). On the staff lease page the same organizations lose the deposit rows, the notice-to-vacate form and the "Notice and demand letters" card (with the advocate pack); the notices and handover pages and the notice action answer 404. Deposits are one switch, `Organization.takes_deposits` (landlords always; the other kinds by the owner's choice of "We hold deposits" on the Settings page, `Organization.holds_deposits`, off by default, asked only of those kinds): without it the lease account page drops the deposit card and clearance section, recording, deducting and refunding raise an error in `billing/deposits.py`, and the report deduction is closed. The move-out statement (with its e-sign documents and the handover pack) does not apply, through `paperwork.move_out_applies`. Receipts print the organization's words for the place and the lease ("Estate / house", "Membership"); a receipt is drawn once when issued, so earlier ones are unchanged. The staff pages for tenants, leases, units, properties, payments and invoices use `vocab` for their headings, buttons and labels (72 exact `{% trans %}` tags replaced in 20 templates; sentences and messages inside them keep the rental words for now). Choosing deposits does not bring back the move-out statement.
4. **Not in this step:** the sentences inside the staff pages already changed, the other staff templates (about 60) and the reports, the rest of the tenant portal (home, messages, repairs), the lease end options and charge types that mean rent, and a landing page for each kind of biller. Each is done by replacing literal words with `vocab` terms, one area at a time.

### D-131 — Tenant portal: messages with the office, lease terms and renewal offers — ACCEPTED (2026-10-11, open to change before merge)
The portal showed money, repairs, documents and notice to vacate, but a tenant could not write to the office, read the lease again, or answer a renewal without a phone call.

1. **Messages.** One thread per lease (`PortalMessage`, 1000 characters a note, 10 notes an hour per lease). The tenant writes at `/my/leases/<id>/messages/`; staff with `messages.view` read and, with `messages.send`, reply at `/conversations/` (a menu item under Tenants). Staff hear by an in-app alert (`portal_message_received`, to holders of `messages.send` on the property); the tenant hears by SMS (`portal_message_reply`) and opens the portal. Notes are never deleted. The same page lists announcements and staff-written messages (`tenant_message`) sent to the lease's tenants, not rent reminders.
2. **Lease terms.** A "Your lease" card (dates, rent, billing, due day, grace, deposit, notice, terms) and the agreement PDF at `/my/leases/<id>/agreement/`, made without ID numbers or KRA PINs.
3. **Renewal offers** (`RenewalOffer`, one open per lease). Staff with `leases.draft` make one from the lease page (start, end, rent, answer-by date, note); the tenants are told by SMS. The tenant accepts or declines in the portal; staff get an alert. **Accepting changes nothing on the lease:** staff press "Start the renewal draft", which runs the normal `renew_lease`, and then check and activate it. Not offered while notice to vacate is open or a draft successor exists. Offers lapse on the answer-by date; staff can withdraw one. All steps are in the audit log.
4. **Not in this step:** attachments in the thread, WhatsApp or email for the new SMS types, signing the renewal in the portal (the draft is signed the usual way), a tenant counter-offer, and a menu badge for unread tenant notes (the inbox and the alerts show them).

### D-132 — Unit bands with every feature included, a one-off price for small landlords, Free off by default — ACCEPTED (2026-10-11, open to change before merge)
D-060 had four paid plans from 25 units up. Landlords with fewer than 20 units are the pilot market, and a monthly fee is a hard sell to them.

1. **Bands, all features in each.** A plan limits only units and team members (viewers free), as before. New migration `0004_unit_bands_and_one_off` sets: Lite up to 10 units (KSh 499 a month, 4,990 a year), Starter up to 20 (799 / 7,990), Growth up to 50 (1,299 / 12,990), Business up to 100 (1,999 / 19,990), Professional up to 300 (4,999 / 49,990), Enterprise by arrangement. Prices are before VAT. The yearly price is ten months (two free). The ladder falls from about KSh 40 a unit at the top of Starter to about 17 at the top of Professional.
2. **One-off.** `Plan.one_off_price` and `Plan.one_off_years` (5), and a third interval `ONCE`. Lite is KSh 12,490 and Starter 19,990: two and a half years of the yearly price for five years of use, about 58% off paying monthly for the same time. The invoice's period runs `one_off_years` from payment. When it ends, the daily job issues a normal renewal at the yearly price, so a one-off never means unlimited cost to us. Moving to a bigger band credits the unused part, like any upgrade; if the credit is more than the new price, the extra is not refunded. SMS stays on the wallet and is not included. A price of 0 means no one-off offer (Growth and up have none).
3. **Free is off.** The migration keeps the `free` row but sets it inactive, so nobody is offered it. A Platform Admin can switch it on in Django admin ("is active"; it needs every price 0). With Free off, a trial that ends is invoiced for the first band, in the admin's order, that holds the units and team in use (`services.fitting_plan`), instead of falling to Free.
4. **Editing in admin.** The plan list (Django admin, Plans) edits units, team members, all three prices, one-off years, self-serve, active and order in place, and each change is written to the audit log (`plan.admin_change`). The Subscription page shows each price and the percent saved against paying monthly, worked out from the prices, so there is no separate discount field.
5. **Not in this step:** a coupon or promo code, a refund of leftover credit, an end-of-term reminder for one-off customers beyond the normal renewal invoice, and price changes for people already subscribed (their next invoice uses the plan's current price).
6. **Above 100 units: by message.** Professional is no longer self-serve (migration 0005); its price stays in admin as a guide. The Subscription page shows a "More than 100 units?" card with a Message us button (the support contact form) and a WhatsApp link when `SUPPORT_WHATSAPP` is set. A Platform Admin agrees the price, then sets the organization's plan, or issues the invoice, in admin.

### D-133 — Growth basics: front page, pricing, FAQ, tutorials, demo booking, referrals, sign-up sources — ACCEPTED (2026-10-11, open to change before merge)
A visitor who was not signed in met a login form and nothing else. New app `growth` (migration 0001).

1. **Public pages, no sign-in:** the front page at `/` (members still get their home), `/pricing/` (read live from the plans, so it never disagrees with the Subscription page), `/faq/` (about 30 answers in 7 groups, with schema.org FAQPage data), `/tutorials/` and `/tutorials/<slug>/` (7 step-by-step guides written from the help pages), `/robots.txt` and `/sitemap.xml`. Text is in `growth/content.py`. Signed-out pages get a header and footer with these links.
2. **Demo booking** at `/demo/`: name, phone, kind of business, units band, how (call, video, visit), a day within 45 days and a time of day. Saved as a `DemoRequest` (status New, Contacted, Booked, Demo given, Cancelled, worked in Django admin), emailed to `OPS_ALERT_EMAILS` and, if an email was given, confirmed to the visitor. Five requests an hour per IP; a hidden field catches scripts; the same phone asking again within a day updates its open request.
3. **Refer a landlord** at `/refer/` (`organization.manage`, menu "Refer a landlord"): one code per organization, a link `/register/?ref=CODE` and a WhatsApp share button. A referred organization and its referrer each get `REFERRAL_REWARD_DAYS` (30) added to the trial or paid period **when the new one first pays us in full**, never at sign-up. At most `REFERRAL_MAX_REWARDS_PER_YEAR` (12) rewards a year per referrer. Not counted: the same user or billing phone as the referrer. A referrer whose subscription has lapsed, or who is over the cap, gets nothing and the row says so (`REFERRER_LAPSED`); the new customer is still rewarded. The referrer never sees the new business's name, in the page or the audit log.
4. **Sign-up sources:** `?ref=` and `?utm_source/medium/campaign` (or an outside referring site) are kept in the session by `CaptureCampaignMiddleware` and saved as `SignupSource` when the organization is made, and on demo requests, so the owner can see which ads, groups and partners bring paying customers.
5. **Hooks:** `accounts.services.create_organization` calls `growth.referrals.attach`; `subscriptions.services._settle` calls `growth.referrals.on_payment` for a paid invoice.
6. **Not in this step:** testimonials and customer counts (none are real yet, so none are shown), a blog, a changelog page, a self-serve calendar for demo slots, Swahili text for the new pages, cash or SMS-credit rewards, a partner or agent commission programme, a "cost of vacancy" calculator, and showing sign-up sources in `pilot_report`. [VERIFY] the FAQ answers about Paystack and the Terms wording on referral rewards with counsel before launch.

### D-134 — The pilot runs on DigitalOcean (Frankfurt), not Hetzner — ACCEPTED (2026-10-11, open to change before merge)
Changes the host in D-122; everything else in D-122 stands (one server, PostgreSQL, Redis, gunicorn and nginx on the same machine, `deploy/setup.sh`). The owner chose DigitalOcean; the same scripts work because they only need Ubuntu 24.04.

1. **Region:** Frankfurt (FRA1). DigitalOcean has no African region; Frankfurt has good routes to Nairobi (roughly 140–180 ms). The cross-border safeguards in D-122 item 4 now name DigitalOcean instead of Hetzner **[VERIFY]**.
2. **Sizes:** the $6 Droplet (1 GB, with the 2 GB swap from `setup.sh`) while there are no clients; resize to $12 (2 GB) a day before the first client, "CPU and RAM only" so it can be undone; $24 (4 GB, 2 vCPU) for several clients. $4 (512 MB) is too small. Weekly Droplet backups on from the first client.
3. **Off-site backups:** a Hetzner Storage Box no longer applies; DigitalOcean Spaces (or another S3 or SFTP store) with `rclone` encryption instead. Not yet written or tested.
4. **Code change:** `deploy/setup.sh` writes `HOSTING_LOCATION="DigitalOcean, Frankfurt, Germany (EU)"`.
5. **Docs:** doc 22 is the setup as done, with the card problems met; doc 21 stays as the Hetzner version.

### D-135 — Developer console: platform analytics and a team with page-by-page access — ACCEPTED (2026-10-11, open to change before merge)
The developer needs to see how the app performs across all organizations, and to let their own staff see some of it. This is a separate area at `/console/`, not part of any landlord's pages.

1. **Seats:** `analytics.PlatformStaff` (one per person) has a role (Owner, Analyst, Finance, Support, Engineer) that sets usual pages, and a per-person list of pages that can be ticked up or down. Superusers are implicit owners with no row. A person without a seat gets a plain 404, so the pages are not confirmed to exist.
2. **Pages (capabilities):** overview, revenue, behaviour (how tenants pay), engagement (usage), organizations, health, staff. Each page checks its own capability; no one can give a page they do not hold, only superusers make owners, and no one edits their own seat.
3. **Sign-in:** two-step login must be switched on and passed for the session.
4. **Privacy:** counts and shares only. Organization names are shown (the developer is their processor); no tenant names, phones or per-person amounts. The organizations list can be downloaded as CSV, and the download is audited (`console.export`). Adding and changing seats are audited too (`console.staff_added`, `console.staff_changed`).
5. **Analytics:** sign-ups, funnel and cohorts; MRR and subscription money collected; on-time share, method mix, pay days, auto-matching and reversals across all organizations; daily/weekly/monthly active people, logins, feature use from the audit trail, installed-app opens, messages; per-organization ranking and detail (reusing the pilot scores); health (failed messages, stuck M-Pesa money, support, incidents, security counts). Charts use the existing chart specs with a table of the same figures beside each.
6. **Left alone:** the existing `is_staff` pages (sender IDs, M-Pesa, subscriptions) are unchanged.
7. **Not in this step:** server and error monitoring (use Sentry or the host's tools), saved date ranges, e-mailed weekly digests, per-seat view logs beyond downloads and changes.

### D-136 — Console watchlist and seat activity — ACCEPTED (2026-10-11, open to change before merge)
Adds to D-135. 1. **Watchlist** (`/console/organizations/watchlist/`, needs the organizations page): organizations that may leave or need a call, scored by plain rules shown beside each: silent 14/30 days or never used, subscription payment due or lapsed, trial ending within 7 days (worse with no active lease), no rent collected after the 10th, five or more payments waiting to be matched, no active lease after two weeks. Worst first. 2. **Last opened:** `PlatformStaff.last_seen_at`, updated at most every five minutes, shown on the team page. 3. **Not yet:** e-mailed digests of the watchlist, date-range pickers, and saved views.

### D-137 — Owner-only controls: organization status, trial extensions, announcements, audit log — ACCEPTED (2026-10-11, open to change before merge)
Adds to D-135. Only superusers reach these (a seat holder gets 403, whatever pages they hold); two-step login applies as everywhere in the console. 1. **Organization status** (Active, Read-only, Frozen) from the organization page, with a reason of at least five characters, audited as `console.organization_status`. 2. **Extend a trial** by 1–90 days, with a reason, audited as `console.trial_extended`. 3. **Announcements** (`Announcement`, `/console/control/announcements/`): a notice at the top of every signed-in page, with a kind (information, warning, urgent), an optional end time, and an on/off switch; audited. 4. **Audit log** (`/console/control/audit/`): all organizations' events, filtered by action, organization or person, 100 to a page. Read only, as the table is append-only. 5. **Not yet:** signing in as a user (impersonation) — left out on purpose until there is a need that justifies the risk; plan changes by hand; feature switches per organization.

### D-138 — Console: growth, where it is used, messages against limits, AI use, and the owner's money ledger — ACCEPTED (2026-10-11, open to change before merge)
Adds to D-135. New capabilities `growth`, `channels` and `finance`. 1. **Growth** (`/console/growth/`): organizations, people, tenants and units over time; people by role (owner, caretaker, agent and so on); average and typical units per organization; size bands; withdrawal rate (closed, lapsed or frozen organizations out of all ever signed up); sign-up to paying conversion; units by county. 2. **Messages and AI** (`/console/channels/`): email, SMS and WhatsApp sent, failed and skipped this month, by type and provider, with a month-end projection against the plan allowance set in `EMAIL_/SMS_/WHATSAPP_MONTHLY_LIMIT` so you know when to upgrade; AI questions, tokens, failures and cost per answer. 3. **Payment methods** on the payments page, by count as well as by money. 4. **Money** (`/console/money/`, `PlatformExpense`): you enter hosting, AI, email, SMS and other costs, once or repeating monthly or yearly with an optional end date, each with a note; net profit is subscriptions and top-ups received minus those costs, by month and running total. Add, change and delete are audited. 5. **Not yet:** currency other than KES, automatic import of provider bills, tax.


### D-139 — Security batch 3: edge limits, browser headers, server sandboxing — ACCEPTED (2026-10-12, open to change before merge)
Adds to D-107 and D-108 (CSP, session limits, per-IP limits, Argon2, MFA, route access guard). Nothing earlier is loosened.

1. **Browser headers** (`core/headers.py`): `Permissions-Policy` switches off camera, microphone, location, motion, USB and payment APIs; `Cross-Origin-Resource-Policy: same-origin`; every page seen by a signed-in person is `Cache-Control: private, no-store` so the Back button on a shared phone or computer cannot show a tenant's statement after logout. Responses that set their own Cache-Control keep it.
2. **Request size:** form bodies over 5 MB (files excluded), more than 1000 fields or 20 files are refused with 400.
3. **nginx** (`deploy/nginx.conf`): 20 requests/second per address (burst 60) and 10/minute (burst 10) on sign-in, sign-up, code, reset, invite and staff-admin paths; 30 open connections per address; short header/body timeouts against slow-connection attacks; small header buffers; unusual HTTP methods refused; probes for dotfiles, `.php`, `.env`, `.git`, `.sql`, backups, wp-admin, phpMyAdmin and similar are closed (444) before Django. Webhooks from providers fall under the general limit only.
4. **gunicorn:** request line 4094 bytes, 50 header fields, 8190 bytes per field.
5. **systemd sandbox** (`deploy/landlordpms.service`): no kernel tunables, modules, logs, clock or cgroup changes; no Linux capabilities; no SUID, realtime or namespaces; only unix, ipv4, ipv6 sockets; files created private (umask 0077).
6. **Server** (`deploy/setup.sh`): fail2ban jails for ssh (4 tries, 24 h), nginx rate-limit hits and nginx bot probes; ssh keeps key-only login with root by key only, 3 tries, 20-second grace, no forwarding.
7. **Tests:** `core/tests/test_security.py` checks the headers, no-store, own-cache-rule, oversized forms and that the deploy files carry the defences.
8. **Not done / needs you:** run `deploy/setup.sh` again (or copy the three files and `nginx -t && systemctl reload nginx`, `daemon-reload` and restart the service) on the Droplet; put the domain behind Cloudflare or DigitalOcean's firewall for DDoS if attacks appear; HSTS preload after the domain is final; a penetration test before many clients; turn on DigitalOcean Droplet backups and 2FA on the DO, GitHub, email and domain accounts, since stolen host or registrar logins bypass everything in the app.

### D-140 — Charts on the property, prospects and repair performance pages — ACCEPTED (2026-10-12, open to change before merge)
The owner asked for the whole app's statistics to be presented to a professional standard. A sweep of the templates showed that D-110 to D-114 already cover the reports, money pages, payments list, lease account, portals and the developer console. Three working pages that held numbers only in text or tables were left. Nothing new is stored; each chart reads figures the page already computes and the same figures stay in a table, drawn by `static/js/charts.js` from `core/charts.py` specs.
1. **Property page.** For members who see the money, a 12-month chart of rent expected against rent collected for that property (`metrics.trend` on the property's scope), with the figures under "Show as a table". Hidden when the property has billed no rent.
2. **Prospects list.** Open prospects by step (bars), where the last 90 days' enquiries came from (doughnut, `services.pipeline`) and why prospects were lost (bars). Each chart is left out when it has nothing to show.
3. **Repair performance.** Jobs by kind (doughnut) and the share started and fixed on time by priority, with the existing tables below.
4. **Not changed:** the public vacancy page is a listing, not statistics. Pages with nothing to count (forms, settings, help) were left alone.

### D-141 — Whole-app look and accessibility pass (WCAG 2.1 AA) — ACCEPTED (2026-10-12, open to change before merge)
The owner asked for one consistent, modern, accessible interface on phones, tablets and computers, with no change to business logic, data, addresses or integrations, and only free assets. This builds on D-111 (flat colour tokens, light/dark, self-hosted Inter). Bootstrap 5.3, Bootstrap Icons and Chart.js stay the only libraries. No new scripts run inline, so the CSP (D-107) is unchanged.
1. **App shell** (`templates/base.html`). The four menu groups (Property, Tenants, Money, More) are one sidebar that is fixed on screens 992px and wider and becomes a slide-in drawer (Bootstrap `offcanvas-lg`) on smaller ones. The drawer traps focus, closes with Esc and returns focus to the menu button. A slim top bar holds the menu button, search (the "/" shortcut is shown), offline badge, notifications with an unread count read aloud, and one account menu (avatar, help, security, console, install app, theme, log out). The top bar stays at the top from 768px up. On phones the search wraps to its own row and scrolls away. The footer is now a labelled list of links. `<html lang>` follows the active language.
2. **Back to top.** A floating round button appears on long pages (lists, reports, statements) after scrolling past about one and a fifth screens. It scrolls smoothly, or instantly when the device asks for reduced motion, then moves keyboard focus to the main content. It respects the iPhone safe area and is hidden when printing. ("Course listings" in the brief map to the long list and report pages; the app has no courses.)
3. **Loading feedback.** A thin progress bar at the top runs while a link or form takes the person to another page. It skips downloads (CSV, PDF, XLSX, ZIP, ICS and export links), new tabs, phone/SMS/mail links and same-page anchors, and clears itself after 10 seconds or on Back.
4. **Forms** (`partials/form.html`, `partials/_field_aria.html`). Every error and help text is tied to its field (`aria-describedby`). Fields with an error are `aria-invalid` and required fields are `aria-required`. When fields have errors, a summary at the top lists each one with a link to the field and takes focus. Multi-choice tick lists are a `fieldset` with a `legend`. Field borders are darker to meet the 3:1 non-text contrast rule. Labels keep the existing "(optional)" convention rather than adding asterisks. Unlabelled inputs on the meter approve, reject and round pages gained labels.
5. **Contrast.** Success, danger, warning and info text, muted text and field borders were retuned so text is at least 4.5:1 and borders and focus rings at least 3:1, in both themes. Each pair was checked with a script.
6. **Focus and motion.** One visible focus outline on every control, link, tab and menu item. Subtle fades on pages and menus and a lift on tiles all stop when reduced motion is requested.
7. **Tables.** Wide tables show a soft shadow on the side that scrolls. When they overflow they become a named, keyboard-scrollable region.
8. **Shared pieces.** One pager (previous/next with "Page N of M") used by the tenants, leases and properties lists and matched on the audit log. Empty states have an icon disc and a "good" tone. Touch targets are at least 44px on touch screens.
9. **Icons.** 154 decorative icons in 65 templates are hidden from screen readers. The one icon that carries meaning alone (receipt attached) has a text label.
10. **Public site bar.** It stays at the top, collapses below 1200px (seven links and two buttons do not fit on one row below that; the menu button is hidden at full width), has a full-size menu toggle and marks the current page.
11. **No stale styles after an update.** The app's offline cache (D-080) answered `app.css` from its copy first, so the first visit after a change showed new pages with old styles and a broken layout. Every local CSS and JS address now ends in `?v=` taken from the file's size and time (`core/storage.py`, set in `STORAGES`), so a changed file is a new address and is fetched fresh. Vendored libraries, fonts and images keep plain addresses.
12. **Footer.** One aligned band under the content with the brand, the links and "© year NyumbaPMS". It wraps to rows on phones.
13. **Tests.** `accounts/tests/test_navigation.py` checks the drawer and the single dropdown. Pages were rendered from a throwaway database and checked in Edge at 1440px and 375px wide in light and dark.
14. **Whole-site sweep (pass 2).** All pages were rendered from a throwaway database: 214 addresses, 150 distinct after merging repeats like help articles. Each was shot at 1366px and 375px and checked by eye, and a script checked the HTML. Changes:
    - **Buttons.** Black buttons are gone. Filter, Apply, Find and Search are outlined; the main action on a page is green.
    - **Tabs.** Money and M-Pesa tabs use the same soft green pill as the reports.
    - **Headings.** Chart cards are `h2` under the page's `h1`, and plan names are `h2`. The payment page has an `h1` (the amount, styled `.hero-amount`).
    - **Labels.** Maintenance targets, the call list filter and the permission selects on a staff member's page have spoken labels.
    - **Unique field IDs.** On the lease page each form has its own field IDs. The posted names are unchanged.
    - **Table totals.** Total rows use `.table-total`.
    - **Notices.** The email and new-terms notices sit on one row with an icon. On a phone the text and button share the row, so the two notices take half the height.
    - **Stat figures.** On a phone, figures use a smaller size and break only after "KES", never inside the number.
    - **Arrears header.** Its links wrap under the title on a phone.
    - **Lease account rings.** The rings on a lease's account stack above the chart instead of squeezing their text.
    - **Chart colours.** Charts read the colour tokens straight from the CSS if `theme.js` has not loaded, so a chart is never drawn in Chart.js's default black.
    - **Rechecked.** After the fixes the scan found no duplicate IDs, unlabelled fields, heading skips or pages without an `h1`.
15. **Not done / limits.** No automated axe or Lighthouse run in CI. Screen-reader checks (NVDA, VoiceOver, TalkBack) and Safari/iOS still need a person. Chart colours keep D-114's palette and each chart has its table. The "← back" links are left as they are. The PWA offline pages were not redrawn.

### D-140 — Paystack is only for subscription payments to us; landlords' rent no longer goes through it — ACCEPTED (2026-10-13, open to change before merge)
Reverses D-117. Rent and other tenant payments reach a landlord only through their own Paybill or Till on Daraja (D-045, D-100). A landlord without a Paybill records cash, bank and cheque payments by hand. Paystack stays for one purpose: landlords paying their NyumbaPMS subscription to our own account (D-126).

1. **Why.** A tenant cannot pay a Paystack account by hand from the M-Pesa menu, so with Paystack every payment has to start from our side (a staff request, a pay link, USSD or the assistant). A Paybill takes payment at any time with the unit reference, with no fee from us or Paystack. Keeping two collection routes for rent also doubled what had to be tested and supported.
2. **Removed.** The `PaystackCredentials` model and its admin ("Paystack accounts"), the `/hooks/ps/<token>/` route and `PaystackHookView`, the Paystack branch of `stk.send`, `record_paystack` and "Check", the Paystack panel on the M-Pesa settings page, the `PAYSTACK` payment-account type and the `PSK` transaction source, `charge_mpesa` and `environment_of` in `mpesa/paystack.py`, `mpesa/tests/test_paystack.py`, the launch-check count of landlords on Paystack, and the FAQ line. The Terms and Privacy pages now name Paystack only for the subscription.
3. **Kept.** `mpesa/paystack.py` (the client, `signature_ok`, `FakePaystackClient`), `PAYSTACK_CLIENT`, `PAYSTACK_ALLOWED_IPS`, `PAYSTACK_EMAIL_DOMAIN`, `PLATFORM_PAYSTACK_SECRET_KEY`, and the whole subscription flow in `subscriptions/` with its webhook `/hooks/plat/<token>/ps/`.
4. **Migrations.** `mpesa/0011_remove_landlord_paystack` deletes the table and drops `PSK` from the source choices; `payments/0004_remove_landlord_paystack` drops `PAYSTACK` from the account types. Migrations 0010 and payments 0003 stay, because they may already have run on the pilot server. Any `PAYSTACK` payment account or `PSK` transaction row would stay in the database as plain data; none are expected, since landlord Paystack was never tried live.
5. **Still to prove.** One test and one live subscription payment through our Paystack account (doc 17 §6); the `[VERIFY]` notes on the checkout reply and webhook fields stand.

### D-142 — Who pays for each landlord's SMS: sponsored, prepaid or bundled, with usage on both sides — ACCEPTED (2026-10-13, open to change before merge)

1. **Mode per organization**, on its `SmsWallet`, set by a Platform Admin in Django admin (SMS wallets list, editable in the list): **Sponsored** (we pay; a monthly cap of parts, empty for none; the default for every landlord, cap 300), **Prepaid** (the landlord's wallet pays every part) and **Bundled** (a monthly allowance of parts, then the wallet pays; the wallet may go slightly negative on the message that crosses the line). One flat price, `SMS_PRICE` per part, for everyone; no per-landlord price.
2. **Every sent SMS is recorded**, whoever pays: `SmsWalletEntry.segments` holds the parts, `amount` is zero when sponsored or inside the allowance. The monthly figure is the sum of those parts since the 1st (Nairobi time). Money is taken only when `SMS_WALLET_ENFORCED` is on (on in production), so development is unchanged.
3. **Stopping.** `sms_available` is false when the sponsored cap or bundled allowance is used and, for bundled, the wallet is empty; prepaid, when the balance is zero or less. `send_one` re-checks before sending, so a queued announcement cannot run past the cap: the message is SKIPPED as "channel not available" and the notification falls back to email where it can.
4. **Login codes, security alerts and invitations are free and not counted**: they call `send_sms` directly, not through `Message`.
5. **Landlord side**: the subscription page shows parts used this month, what is left, a bar, the split by kind of message, and the wallet. **Our side**: the SMS wallets list shows, for the month, parts, state, our cost (Africa's Talking's `Message.cost`), what we billed, the balance, a header total including what we absorb for sponsored landlords, and a CSV export. Mode or cap changes are audited (`sms_wallet.terms`).
6. **Topping up online.** Landlords on prepaid or bundled top up from the subscription page (KES 100 to 50,000, whole shillings) by M-Pesa prompt to our Paybill (account reference `SMS`) or Paystack, through the same `PlatformCheckout` and `PlatformTransaction` flow as invoices (D-126): a checkout with no invoice is a top-up, and the confirmed amount goes to the wallet with a receipt (`apply_sms_top_up`). Paying the Paybill by hand with account SMS still waits for a Platform Admin, who records it as before.
7. **Warnings by email, once each a month**: 80% of the month's parts used, the parts used up, the wallet below 20 parts' worth, and the wallet empty. They go to the organization's billing email, else to members who can manage the subscription. When a sponsored landlord reaches its cap, the operators (`OPS_ALERT_EMAILS`) hear too. A top-up re-arms the wallet warnings; changing the mode or cap in admin re-arms them all.
8. **Not in this step**: a postpaid mode that adds SMS to the next subscription invoice. Wallets that existed before this change become sponsored.

### D-143 — Meter reading sheet, pasted readings and shared meters — ACCEPTED (2026-10-13, open to change before merge)

1. **Reading sheet** at `/meters/round/<property>/`: every room with its tenant, the previous reading and a box for the current one (or the units used). Use and amount fill in as you type, at the meter's rate and minimum charge. Rooms left blank are skipped.
2. **Paste or CSV**: lines such as `A1 1234` or `room,reading` are matched to the sheet's meters by room code. Unmatched or unreadable lines are listed so they can be fixed. Nothing is saved until the sheet is saved.
3. **Shared meters**: one meter can serve several rooms (`MeterUnit`). When its reading is approved, the use or the typed-in bill is divided among the rooms, adjusted for the days each was let. It can be divided equally, by weight, or by each room's own sub-meter (`split`). By default it is divided among the rooms that are let; otherwise the landlord pays the empty rooms' share (`vacant_share`). The payer is the tenants or the landlord. The "who pays what" page shows each tenant's part before approval, and approval makes the `MeterCharge` lines. The settings used are stored on the reading.
4. **Not in this step**: smart meters and tenants reading their own meters.

### D-144 — Reading notebook pages and utility bills from a photo — ACCEPTED (2026-10-13, open to change before merge)

1. A photo of a notebook page fills the reading sheet, matched like pasted lines. A photo of a water or Kenya Power bill fills the amount to divide on the "who pays what" page. Nothing is saved from a scan: the person checks what was read, then saves or approves.
2. Read by OpenAI vision on the assistant's key (`OPENAI_API_KEY`, `SCAN_MODEL`) with a fixed JSON schema. The photo is shrunk to a small JPEG with EXIF data removed and is not kept. Unreadable digits are returned as unclear, never guessed.
3. Each scan is logged (`Scan`: kind, status, tokens, cost in KES). The limit is `SCAN_MONTHLY_LIMIT` a month (default 100), which `Organization.scan_limit` overrides; 0 turns scans off for that landlord. Failed scans and scans refused over the limit are free and not counted.

### D-145 — Usage reports: what each tenant used, and what each landlord cost us — ACCEPTED (2026-10-13, open to change before merge)

1. **Landlord: `/meters/usage/`** ("Used this month" on Meters). For a month and optionally one property, it shows each room's water (m³) and electricity (kWh) and what the tenant was charged. On a shared meter, units are each lease's fraction of the division before any minimum charge, and amounts are the uncancelled `MeterCharge` lines. What no tenant covers (empty rooms, meters the landlord pays) is a landlord line per property. It has totals and a CSV export, with cells escaped against formulas. It needs `meters.view` and is limited to the member's properties.
2. **Landlord: Subscription page**: photo scans this month (notebooks and bills), the limit, what is left and a bar. It also shows assistant questions this month, split into staff and tenants. Questions refused over the daily limit are not counted. Together with D-142's SMS block, a landlord sees SMS, scans and the assistant in one place.
3. **Us: Console → Costs by landlord** (`/console/money/costs/`, capability `finance`): for a month and per organization, SMS parts, what Africa's Talking charged and what we billed back; scans and their cost; questions, tokens and their cost (`AI_PRICE_INPUT_PER_M`, `AI_PRICE_OUTPUT_PER_M`, `USD_TO_KES`). The most expensive landlord comes first, with a total row. The CSV export is audited (`console.export`).
4. These are our estimates from tokens; the OpenAI invoice under Money is the real figure.

### D-146 — The Bill a month page checks that the meters are read — ACCEPTED (2026-10-13, open to change before merge)

1. A reading taken in a month is billed on the next month's invoices (D-057), so the invoices of month M need approved readings dated from the first of M−1. `meters/readiness.py` sorts each metered (postpaid) meter that serves a unit let during M into **read and approved**, **waiting for approval** or **not read**. Prepaid meters and empty rooms are not listed.
2. `/billing/generate/` shows one card for each month billed now (and the month just billed): "All read", or the meters not read and waiting, grouped by property, with links to the approvals and meters pages. It is shown only to people with `meters.view`, and only for the properties they can see.
3. Nothing is held back. A meter without an approved reading is left off those invoices and its charge follows on a separate invoice once approved, as before; the card tells the person before they bill.
4. Flat fees such as garbage stay lease charges billed with the rent (D-069), not meter lines.
5. Free water or power: a meter whose payer is "Landlord pays" bills nothing, so the card never waits for it; it only says "n meters are paid by you, so they are free to the tenants". A property with free water everywhere shows no card. The meter form says to choose "Landlord pays" for utilities included in the rent. Rooms without a meter are not checked, and an archived meter drops out. No per-meter "skip this month" switch was added.

### D-147 — Reports and payments pages share one look — ACCEPTED (2026-10-13, open to change before merge)

1. Cards are flat (D-111): a leftover `shadow-sm` no longer lifts them. Figure tiles (`.card.stat`) have one padding, a small upper-case label and an optional coloured left edge (`stat-accent`).
2. Table headings are small upper-case on every report and payment table; footer totals are tinted; numbers line up.
3. Every report starts with an "All reports" back link above its title (`.back-link`), like the other detail pages (D-142).
4. Payments use the shared page parts: `.page-head`, `.detail-head`/`.detail-grid` on a payment, `.panel` + `.kv` for details, `.item-list` rows on a phone and when picking a lease, filter presets as pills. A rejected or reversed amount is struck through.

### D-148 — Read the reports one after another in the report centre — ACCEPTED (2026-10-13, open to change before merge)

1. `/reports/centre/` is a reader: the reports the member may open are listed on the left (a drop-down on a phone), and the chosen one is shown beside the list. Previous/Next, the "x of y" count and the left/right arrow keys step through them; `?r=<report>` keeps the choice, so Back, reload and a shared link land on the same report. "Full page" opens the report on its own.
2. The report is drawn in a frame from its own page with `?pane=1` (`reports/pane.py`): no sidebar, top bar, banners or footer. Its charts, filters, exports and print work unchanged. Links to other reports stay in the frame; anything else (a lease, a payment, a POST) opens in the whole window (`report_pane.js`, and `window.appNavigate` for chart clicks).
3. Security: only a GET under `/reports/` (not the centre) with `pane=1` that returns HTML may be framed, and only by our own pages (`frame-ancestors 'self'`, `X-Frame-Options: SAMEORIGIN`). Every other page keeps `frame-ancestors 'none'` and `DENY` (D-107). `reports/tests/test_reader.py` checks both.

### D-149 — Owners shape their rent invoices — ACCEPTED (2026-10-13, open to change before merge)

1. The Settings page (`/organization/`) now holds what the invoice PDF prints: logo (PNG/JPG, up to 1 MB, `services.set_logo`), invoice colour (`brand_color`, blank = standard blue), address, how to pay, terms, and whether the tenant's phone and email are printed. Office phone and email, KRA PIN and the footer were already there and now also appear in the invoice header.
2. "How to pay" is the owner's own words, printed above the unit's account number. It is not filled in from the Paybill or Paystack settings.
3. The same PDF serves staff, the portal and the invoice email, so the three match. Nothing is stored: changes show on invoices opened or sent from then on, including ones already issued.
4. Earlier unpaid invoices (due on or before this one) and a "total owing now" print under the totals, switchable (`invoice_show_previous_balance`, on by default). A signature or stamp picture (`stamp`) prints above "Authorised signature". Metered lines already carry the readings, rate and shared-meter split in their text (`meters/split.describe`), so nothing was added for them.

### D-150 — Owners set their own deposit rules — ACCEPTED (2026-10-13, open to change before merge)

1. Kenya has no statute fixing a deposit refund deadline, the allowed deductions or the proof needed (research, 2026-10-13; the Rent Restriction Act and the proposed 2021 Landlord and Tenant Bill do not settle it, and tribunals decide on paper evidence). So the owner chooses, on the Settings page (`organization.manage`, shown only when the organization holds deposits): days to settle after the lease ends (`deposit_refund_days`, default 30), which headings a deduction may use (`deposit_deduction_headings`, none ticked = any, heading optional), whether a deduction needs proof (`deposit_proof_required`), and whether a deduction may be put towards rent owed (`deposit_allow_offset`, on by default).
2. A deduction now carries an optional `heading` and `evidence` (receipt, quote or photo; JPEG/PNG re-encoded or PDF, via `expenses.services.process_receipt`). With proof required, either a file or a cited move-out report is needed. Evidence is served only to staff who may see the lease (`billing:deposit_evidence`), never public. Entries stay append-only; the file is written with the row.
3. The refund deadline is `lease.ended_on + deposit_refund_days` (`deposits.settle_by`). It shows on the lease account, as a "Due by / Overdue" column in the deposits report's "To settle" list, and to the tenant in the portal with the deposit statement (held, each deduction with its heading and reason).
4. Not done: reminders by SMS or email as the deadline nears, a move-in checklist change, a tenant "dispute" button (tenants can use portal messages), and a deposit receipt tied to the M-Pesa payment.

### D-150 — Owner account pages in charts — ACCEPTED (2026-10-14, open to change before merge)

The owner's account page (staff at `/reports/owners/<id>/`, the owner at `/owner/`) now opens with two charts built by `reports.owners.account_specs`: rent collected against paid to the owner by month, and where the 12 months' rent went (paid, still to pay, management fee). The table stays the source of every figure; no chart shows a number the page does not. Nothing is drawn when nothing was collected.

### D-151 — Each landlord connects their own WhatsApp number in the app — ACCEPTED (2026-10-14, open to change before merge)

Messages > WhatsApp (`/messages/whatsapp/`, `organization.manage`) takes the landlord's own Meta Cloud API details: phone number ID, access token and app secret (the last two encrypted with `core.crypto`, never shown back or written to the audit log). `notifications.WhatsAppAccount`, one per organization.

1. Sending: `get_whatsapp_sender(org)` uses the organization's own number when it has a switched-on account with a token, else the platform's (`WHATSAPP_BACKEND`, unchanged). `whatsapp_available(org)` is true for either, so a landlord can use WhatsApp even when the platform has no number.
2. Webhook: each account has its own `/hooks/whatsapp/<token>/` and verify token, signed with the landlord's own app secret. Delivery reports, STOP/START and tenants' questions on that webhook only reach that landlord's messages and tenants. The shared `/hooks/whatsapp/` stays for the platform number.
3. Templates: Meta approves templates per business account, so the page lists the templates (name, language, wording) the landlord must create in their own WhatsApp Manager. Until one is approved, that message cannot go by WhatsApp.
4. Not done: Meta embedded signup (landlords would log in with Facebook instead of pasting keys), creating the templates for them through the API, and token-expiry warnings.

### D-152 — Home page redrawn: product first, features as tabs — ACCEPTED (2026-10-14, open to change before merge)

The home page (`/`, `templates/growth/landing.html`) followed common SaaS landing practice: short outcome headline, a real picture of the product, a trust row, problem then answer, features grouped compactly, pricing, FAQ, one closing call to action.

1. Hero: the same headline with "Know who owes" in green, three ticks under the buttons (no card, own Paybill, every feature), and a small "M-Pesa matched" card over the overview picture.
2. "Works with" row in plain words with icons (M-Pesa, SMS, WhatsApp, bank statements, Excel, short code); never other companies' logos.
3. "Today / With NyumbaPMS" side by side (`LANDING_SWAPS`).
4. The four long alternating sections became four tabs (Collect, Follow up, Manage, See and prove), each with its own small sample screen (`growth/_landing_mock.html`, marked "Sample data").
5. No invented customers, logos, testimonials or numbers; to be added only when pilot landlords agree to be named.
6. Closing band uses a fixed deep green in both themes so white text keeps its contrast in dark mode. `bi-keypad` (not in Bootstrap Icons 1.11.3) replaced by `bi-telephone`.

### D-153 — Signed-in home made a working page — ACCEPTED (2026-10-14, open to change before merge)

The signed-in home (`templates/accounts/home.html`, data in `reports/home.py`) now answers "what needs me and how is money doing" without opening the dashboard. Every part is gated by capability as before; summary-only roles still see no amounts and no call-list or review links.

1. Date, greeting and quick actions (record a payment, send invoices, add a tenant, message a tenant, report a repair, add an expense), each shown only to those allowed.
2. Four figures: collected this month (ring, last month's rate under it), still to collect (meter), overdue today (aging bar, link to arrears), occupancy (ring, empty units). A 12-month bar strip of collection rate.
3. "Today" lists the waiting work with icons; urgent kinds (payments to match, tenants to call, promises due, late repairs) come first.
4. New cards: who owes the most (top 5, unit, days late, call button), latest 5 confirmed payments, this month's collection by property (lowest first, only with more than one property), leases ending soon.
5. Removed: the page's own "add your email" warning (the site-wide banner already shows it) and the "Go to" tiles (the sidebar has them).

### D-154 — Optional own email and SMS per landlord; the owner's WhatsApp number set in Django admin — ACCEPTED (2026-10-14, open to change before merge)

NyumbaPMS stays the default for everything; each of these is optional.

1. **Own email.** Messages > Email: a landlord can enter their own mail server (host, port, username, password, TLS, send-from address and name). Invoices, receipts and notices then go out through that server from their address. Without it, mail goes through the platform account (Resend or Brevo, chosen in admin) under the landlord's company name. "Send a test to my email" keeps the outcome on the page.
2. **Own SMS.** Messages > SMS: a landlord can enter their own Africa's Talking username, API key and sender name. Their SMS are then sent and paid for by that account, so they skip the platform SMS wallet (no balance check, no charge). Login codes, security alerts and invitations always use the platform account.
3. **Platform WhatsApp number.** The owner sets one number in Django admin (Notifications > Platform WhatsApp number): phone number ID, token, app secret, verify token, on/off. When on and complete it replaces the `WA_*` settings for sending and for the shared webhook. Order used for each landlord: own number, then this number, then the settings, else SMS.
4. **Secrets.** Passwords, API keys and tokens are encrypted at rest, never shown back (blank keeps the saved value) and never written to the audit log.
5. **Superuser control.** Django admin lists and edits every landlord's WhatsApp, email and SMS account too (write-only secret boxes, audited as a platform change).
6. **Not built.** Per-landlord Paystack/Daraja are separate (D-117, D-126). Invoice PDF as a WhatsApp document is still open.
6. Small and new landlords: until anything is billed, paid or let (`Home.started`), the figures, bars, "Who owes the most" and "Latest payments" are hidden so a new account sees "Today" and its setup steps, not rows of KES 0.00. The bars start at the first month with rent in them (at least six months), so a landlord who joined in July does not see a blank year. The by-property card only shows with two or more properties.

### D-155 — The reading sheet marks meters already read, and staff are nudged to read them — ACCEPTED (2026-10-14, open to change before merge)

1. The reading sheet (`/meters/round/<property>/`) marks a meter with a reading dated this month ("Read 3 Oct"; one waiting for approval counts), says how many were read, and "Only rows not read yet" also hides them.
2. From the 25th to the last day of the month, the daily job (`billing_daily`) sends an in-app alert `meters_unread` to each `meters.record` holder of a property that has meters with no reading this month, once a day. Meters the landlord pays, prepaid and archived meters and empty rooms are not counted (as in D-146). Code: `meters/reminders.py`.
3. Not in this step: estimates for missed meters, tenants reading their own meters.

### D-156 — Bring in existing tenants from Excel, a printed form or a photo — ACCEPTED (2026-10-14, open to change before merge)

A landlord who already keeps tenants in a book or a spreadsheet no longer retypes them. Everything goes through the existing import flow (check every row, preview, apply, undo for 24 hours), now on `/imports/`.

1. **Excel as well as CSV.** `.xlsx` is read (first sheet; dates, whole numbers and phones that lost their leading 0 are repaired). Old `.xls` is refused with a hint. 3 MB and 2,000 rows at most.
2. **New kind "Tenants moving in".** One line = tenant + unit + rent + date moved in (+ deposit, email, due day). It makes the tenant (or reuses the one with that phone) and a lease. Leases stay drafts to check and activate, unless "Issue the leases now" was ticked at upload (needs `leases.activate`). Undo deletes drafts and the tenants made for them; an issued lease is kept. Existing units that already have a draft or active lease are refused per row.
3. **Sheets we pre-fill** (`imports/sheets.py`): the Excel sheet lists every vacant unit with its asking rent (move-ins) or every active lease (opening balances), dropdowns for choices, phones kept as text, and a "How to fill in" sheet. Unit lines left without a tenant are skipped on upload.
4. **Printable form.** A landscape PDF with the same columns and the known units printed in; no ID number or KRA PIN columns because a photo of it goes to the model.
5. **Photo of the filled form** (`imports/scan.py`): read by the same model as meter photos (D-144), counts against the monthly scan limit as kind "Filled-in form", not kept. The rows go through the same preview, which is marked as read by a computer, and the person must tick "I checked these rows against the paper" before applying.
6. Tenants who moved in long ago: use the first day of the month they start on NyumbaPMS (or the real date) and enter what they still owe under Opening balances.
7. Not built: updating existing tenants from a file, several tenants per unit on one line, export of current tenants in the same layout.
