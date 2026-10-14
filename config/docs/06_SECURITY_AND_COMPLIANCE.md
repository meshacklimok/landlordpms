# 06 — Security and Compliance

## Application security checklist
- [x] `DEBUG=False`, strong random `SECRET_KEY`, restricted `ALLOWED_HOSTS` in production (`check --deploy` test, D-097)
- [x] HTTPS only: `SECURE_SSL_REDIRECT`, HSTS, `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE` (preload waits for the domain)
- [x] Argon2 password hashing; PBKDF2 hashes from before are re-saved with Argon2 at the next login; password validators on (D-107)
- [x] Login rate limiting: 10 tries per phone or email and 50 per IP per 15 minutes; codes limited per phone; sign-ups and reset codes limited per IP (D-107)
- [x] Two-step login with an authenticator app (D-059); an organization can require it for every member (D-107)
- [x] Sessions end after 60 minutes idle or 12 hours for staff, 14 days idle or 30 days for tenants in the portal (D-107)
- [x] Every view requires login + org membership + role check (sweep of every route, D-097); every route names a capability or is on a reviewed list with the reason it is open, and a member whose role has no capabilities is refused on every capability route (`core/tests/test_access_map.py`, D-108)
- [x] Every queryset org-scoped; automated cross-tenant tests (every route, as another organization and as staff kept to one property, D-097)
- [x] Object-level access: never trust IDs in URLs or forms; look up within the active org; junk ids are not found (D-097)
- [ ] Server-side validation of all money fields
- [ ] Use Django ORM (no raw SQL with string formatting); template auto-escaping on
- [x] File uploads: size limits, and the contents must match the kind (PDF header, Office zip or signature, no binary in text); photos are decoded and saved again as JPEG, which drops EXIF data such as the location; stored under random names in `MEDIA_ROOT`, which the proxy must not serve, and served only by permission-checked views (D-047, D-067, D-108)
- [x] Secrets only in environment variables; never in Git (`.env` is ignored; checked 2026-10-01)
- [x] M-Pesa consumer keys, secrets and passkeys encrypted at rest (`core.crypto`, keys in `FIELD_ENCRYPTION_KEYS`)
- [x] Dependency scanning: `pip-audit` on requirement changes and weekly (`.github/workflows/audit.yml`), Dependabot for pip and Actions, ruff's security rules (`S`) in CI (D-107)
- [x] Security headers: Content-Security-Policy with no inline scripts and CDN files pinned by version and hash, reports to `/csp-report/`; X-Content-Type-Options, Referrer-Policy, Cross-Origin-Opener-Policy and X-Frame-Options from Django (D-107)
- [x] Logs must not contain passwords, tokens, or full ID numbers; error reports leave out request bodies and local variables (D-097)

## M-Pesa callback safety
1. Unguessable per-organization token in the callback URL path.
2. Optionally restrict to Safaricom's published IP ranges **[VERIFY current list]**.
3. Store the raw payload first, then process.
4. `unique` on `TransID`; processing is idempotent.
5. Validate amount/shortcode against what you expect. Never trust the payload blindly.
6. Never expose Daraja credentials to the browser.
7. Alert on unmatched payments and repeated failures.
8. Reconcile daily against a Daraja statement/report.

## Data protection (Kenya Data Protection Act, 2019) **[VERIFY with counsel]**
We process personal data of tenants (names, phone numbers, national ID, payment history). Requirements to plan for:
- **Register with the ODPC** as a data controller/processor.
- **Lawful basis and consent:** a landlord (controller or joint controller) needs a basis to hold tenant data; our ToS must make roles clear (likely we are a processor for the landlord).
- **Data minimisation:** collect only what is needed. Do not require ID number unless the landlord needs it.
- **Purpose limitation** and **retention policy:** delete or anonymise data after leases end plus a defined period, subject to tax/legal record keeping.
- **Subject rights:** access, correction, deletion, portability. Provide an export and delete process.
- **Breach notification:** have an incident plan (the Act requires notifying the ODPC within a short window; confirm exact timing).
- **Cross-border transfers:** where servers are hosted matters. Check the Act's rules on transfers **[VERIFY]**.
- **Privacy notice** shown at sign-up and to tenants when their data is added. Drafts are at `/terms/` and `/privacy/` (D-095), linked at sign-up and in the footer; still to be checked by counsel.
- **Do not send** marketing SMS without consent. Include opt-out.

## Tax and rental law touchpoints **[VERIFY all]**
- **Monthly Rental Income (MRI) tax:** residential landlords earning within the KRA thresholds pay a flat percentage of gross rent, filed monthly through iTax. Reports should give the monthly gross rent figure. Confirm current rate and thresholds with KRA.
- **Landlord and tenant law:** Rent Restriction Act and Landlord and Tenant (Shops, Hotels and Catering Establishments) Act cover disputes and notices. The Rent Restriction Tribunal handles some residential disputes. Do not present the software as legal advice; add disclaimers. Notice to vacate and demand letters (D-109) use fixed wording marked **[VERIFY with counsel]**: what they must say, when a notice takes effect (from its date or from service), the usual days to pay a demand, and whether business tenancies need the Cap. 301 prescribed form instead. Staff are warned on commercial and mixed-use property.
- **Deposits:** track clearly, since refund disputes are common.
- **Electronic records and signatures:** the Kenya Information and Communications Act recognises electronic records. Agreements and move-out statements can be signed in the app (D-118): an SMS code to the tenant's phone, a typed name and consent, the PDF's SHA-256 and a signing record. **[VERIFY with counsel]** that this is enough, and which documents need an advanced signature from a licensed provider or a witness.
- **Financial regulation:** holding or moving tenants' money may bring the platform under CBK/payment-service-provider rules. Avoid until advised.

## Backups and recovery
- Daily automated Postgres backups, retained 30 days, plus weekly to a second location.
- Test a restore every quarter. A backup you haven't restored is not a backup.
- Target: RPO ≤ 24h at launch (improve later with point-in-time recovery), RTO ≤ 4h.

## Incident response (minimum)
1. Detect (monitoring/alerts: operators are emailed when an hour has unusually many failed sign-ins, wrong two-step codes or CSP reports; organization managers are told of role, payment-account, M-Pesa, API key and webhook changes; account holders are texted when two-step login is switched off or a recovery code is used, D-108). 2. Contain (rotate keys, disable accounts). 3. Assess what data was affected. 4. Notify ODPC/users as required. 5. Fix and write a post-mortem.

## Access to production
- Least privilege. No shared accounts. MFA on GitHub, hosting, email, domain, Daraja, SMS accounts.
- Record who has access to what.
