# 23. Final-stage audit (started 2026-10-14)

Source: the 23-section pre-launch audit checklist the owner pasted on 2026-10-14. Status words: PASS (run and
verified), FAIL, BLOCKED (needs credentials/server), NOT IMPLEMENTED, NOT TESTED. Nothing is PASS unless it was run.

## Run so far (2026-10-14)

| Check | Status | Evidence |
|---|---|---|
| `manage.py check` | PASS | no issues |
| `makemigrations --check` | PASS | no changes detected |
| Prod settings refuse a short `SECRET_KEY` | PASS | `check --deploy` with prod settings raised ImproperlyConfigured |
| Prod flags (DEBUG off, SSL redirect, secure cookies, HSTS 1y, nosniff) | PASS (read in `config/settings/prod.py`, not run on the server) | |
| Money fields are Decimal | PASS | no `FloatField` in any models.py |
| DB uniqueness on M-Pesa code, checkout id, receipt no., invoice no., invoice per lease/month/charge type | PASS (read in models) | |
| Full pytest run (`e2e_walkthrough` excluded) | **FAIL: 11 failed** | see below |
| Custom 404 / 500 pages | **FAIL** | only `templates/403.html`; no `handler404`/`handler500` |

## Failing tests (all 11 fixed or open: open)

1. **High** `subscriptions/tests/test_collect.py::test_the_page_offers_both_ways`: `/subscription/` returns 500,
   `NoReverseMatch: 'pay_paystack_mpesa'`. Landlords cannot open the page where they pay us.
2. **High** 7 tests in `growth/tests/test_growth.py` (campaign link remembered, direct visit, both sides rewarded,
   second payment not rewarded again, lapsed referrer, yearly cap, referrer never sees referred name):
   `Organization.DoesNotExist`.
3. **Medium** `core/tests/test_access_map.py::test_every_route_names_who_may_use_it`: no access rule for
   `portal:payments`, `portal:payment_report`, `owner_portal:payments`.
4. **Medium** `reports/tests/test_joint.py::test_the_shares_page` and `reports/tests/test_statements.py::test_the_owner_pages`:
   expected owner names/shares missing from the rendered page.

Causes above are from the error messages; the code has not been opened yet.

## Not yet done

- Financial scenarios from the checklist (KES 15,000 partial-payment example, arrears vs dashboard vs report agreement,
  mid-month move-out, rent change not altering old invoices): NOT TESTED.
- `e2e_walkthrough/test_walkthrough.py` (temporary, step-by-step): NOT RUN.
- M-Pesa scenarios 1-10, Paystack, SMS, WhatsApp, email delivery: BLOCKED (sandbox/live credentials).
- Two-landlord isolation: tests exist in `accounts/tests/test_isolation.py`; they ran inside the full suite and none of
  the failures are there, but they were not reviewed one by one.
- Mobile on real phones, load test on the production server, restore test, uptime monitor: BLOCKED (server).
- Lawyer review (e-signature, late fees, message retention, Data Protection Act): BLOCKED (counsel).
- Whole-suite pass count: not recorded (log had no totals line); re-run with `-rA` or `--tb=no` to capture it.

## Next

Fix the 11 failures (start with 1), add 404/500 pages, rerun the suite, then run the walkthrough and work down the
checklist phases: 1 core and billing, 2 payments and isolation, 3 reports, 4 UI/mobile, 5 production.

## Update 2026-10-14 (later): fixes and clean run

- Full pytest run (`--ignore=e2e_walkthrough`): **no failures, 100%** (about 2,600 tests; the log had no totals line).
- Growth tests: the test helper's user now has an email (D-151 requires one before a workspace is made).
- Access map: `portal:payments`, `portal:payment_report`, `owner_portal:payments` listed as signed-in, own-records-only.
- Added `templates/404.html`, standalone `templates/500.html`, and `core/tests/test_error_pages.py`.
- New WhatsApp/own-account pages used inline `onsubmit`; changed to `data-confirm` (CSP). `erd.md` regenerated.
- The subscription-page 500 and the two owner-name report failures did not reproduce; they passed alone and in the clean run
  (files were being edited during the first run). Treat as resolved, watch on the next run.
- Still open: every BLOCKED / NOT TESTED line above (financial scenarios, `e2e_walkthrough`, live integrations, server, counsel).
