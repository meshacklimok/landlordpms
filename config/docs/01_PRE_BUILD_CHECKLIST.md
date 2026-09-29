# 01 — Pre-Build Checklist

Do these before the first model is written. Cheap now, expensive to change later.

## A. Decisions to lock (record each in 08_DECISIONS_LOG.md)

| # | Decision | Recommendation |
|---|----------|----------------|
| 1 | Custom User model | Yes. Before the first migration. Login by **phone number or email**, since many Kenyan users have no email. |
| 2 | Multi-tenancy style | One database, `organization` FK on every business table, plus a scoped manager that filters automatically. Do not use schema-per-tenant. |
| 3 | Money storage | `DecimalField(max_digits=12, decimal_places=2)`, currency `KES`. Never floats. |
| 4 | Balances | Derive from an append-only **ledger**. Never overwrite a stored balance. |
| 5 | Primary keys | UUID for externally exposed objects (invoices, payments, callbacks). Integer keys are fine internally. |
| 6 | Time zone | `Africa/Nairobi`. Store UTC, display EAT. |
| 7 | Database | Move to PostgreSQL **now**, not later. SQLite hides concurrency and decimal bugs that matter for payments. |
| 8 | API | Build HTML views first, but keep business logic in `services.py` so a REST API (Django REST Framework) can be added for mobile apps. |
| 9 | Languages | English first, all strings wrapped for translation so Swahili can be added. |
| 10 | M-Pesa collection model | See section C. This is the most important business decision. |

## B. Fix the PostgreSQL blocker immediately
The notes say the Postgres password is lost and this was "postponed". Recommendation: don't postpone.
- Either reset the local `postgres` password (edit `pg_hba.conf` to `trust`, reset, revert), or
- Run Postgres in Docker, or use a free hosted instance (Neon, Supabase) for development.
- Then use `DATABASE_URL` from `.env`.

## C. Business questions only the owner can answer

1. **Who receives the rent money?**
   - **Option 1 — Landlord's own Paybill/Till.** Money goes straight to the landlord. We register C2B URLs and read confirmations. Lower regulatory risk, but each landlord needs a shortcode and onboarding is slower.
   - **Option 2 — Platform aggregator Paybill.** Tenants pay our shortcode with account number = unit code, and we pay landlords out. Fastest for landlords, but it means holding customer funds, which may need a payments licence or a licensed partner **[VERIFY with a Kenyan fintech lawyer / CBK]**.
   - **Option 3 — Record-only.** Tenant pays the landlord's personal M-Pesa. Landlord pastes the SMS, or we parse it. Zero regulatory burden and works from day 1.
   - **Recommendation:** ship Option 3 in the MVP, add Option 1 next, and treat Option 2 as a later decision made with legal advice.
2. **Who is the first customer?** One small landlord with 5–20 units, or a caretaker-run block? Pick a real pilot user (see 05).
3. **Who pays for the software?** Landlord, agent, or per-unit fee? (See 05, pricing.)
4. **Commercial or residential?** Start residential. Commercial leases have different law.
5. **Legal entity.** Register a company, and get a domain, before taking any money or personal data.

## D. Legal and compliance to-do (start now, takes weeks)
- Register with the **Office of the Data Protection Commissioner (ODPC)** as a data controller/processor under the Data Protection Act 2019 **[VERIFY]**.
- Draft Terms of Service and a Privacy Policy. Get a lawyer to review them.
- Confirm Safaricom **Daraja** developer account and the process for production go-live (Paybill/Till, Go-Live approval) **[VERIFY]**.
- Confirm current KRA **Monthly Rental Income (MRI)** rules so reports can support them **[VERIFY]**.

## E. Accounts and tooling to set up
- GitHub repo (private) with branch protection.
- Domain name and a business email.
- Daraja sandbox account.
- SMS provider account (Africa's Talking) in sandbox.
- WhatsApp Business Platform application (approval takes time).
- Error monitoring (Sentry free tier) and an uptime monitor.
- A password manager for shared secrets.

## F. Repo housekeeping
1. Pin dependency versions in `requirements.txt`.
2. Add `.env.example` (no real secrets).
3. Move `static/css/js/script.js` to `static/js/script.js`.
4. Set `TIME_ZONE = "Africa/Nairobi"` and give `SECRET_KEY` no insecure fallback.
5. Add a linter/formatter (ruff), `pytest-django`, and pre-commit hooks.
6. Set up CI (GitHub Actions) to run tests on every push.

## G. Validate before building too much
Talk to **10 landlords/caretakers** in one area. Ask:
- How do you track rent today (notebook, Excel, WhatsApp)?
- What is your biggest headache?
- How do tenants pay? Do you have a Paybill/Till?
- Would you pay, and how much?

Write the answers in 02_PRODUCT_AND_USERS.md. If the pain isn't rent tracking and arrears, adjust the MVP.

## Exit criteria for "ready to build"
- [ ] Decisions 1–10 recorded
- [ ] PostgreSQL working locally
- [ ] Collection model chosen for MVP
- [ ] At least 5 landlord conversations done
- [ ] Repo housekeeping (F) done
- [ ] Legal to-dos started
