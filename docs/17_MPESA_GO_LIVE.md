# 17 — M-Pesa Go-Live Checklist

Steps to move an organization's Paybill or Till from the Daraja sandbox to live payments (D-045 item 11, step 5). Items marked **[VERIFY]** depend on Safaricom's current process and must be confirmed with Safaricom or in the sandbox.

## 1. Before Safaricom

- [ ] The site runs on **https** and `SITE_URL` is set to the https address. Live callback URLs are refused on http, and Safaricom refuses URLs containing words such as "mpesa" (the app checks both).
- [ ] `FIELD_ENCRYPTION_KEYS` is set in production and backed up outside the server. Losing it makes the stored Daraja keys unreadable.
- [ ] `MPESA_CLIENT` is unset (the real `mpesa.daraja.DarajaClient`), never the fake client.
- [ ] Optional: `MPESA_ALLOWED_IPS` lists Safaricom's callback IPs **[VERIFY the current list with Safaricom]**. If a proxy sits in front of the app, `TRUSTED_PROXY_COUNT` is right, or every callback is refused.
- [ ] `mpesa_daily` is scheduled once a day (early morning, after midnight Nairobi time), next to `billing_daily`, `send_due_messages` and `purge_import_previews`.
- [ ] Sandbox checks done and recorded in D-045 (see TODO.md, Phase 6). `python manage.py daraja_sandbox_check --account <id> --phone <test phone> --out answers.md` answers all four and the Till account number question (D-104):
  - [ ] the MSISDN hash format (item 5)
  - [ ] whether a C2B confirmation also arrives for STK payments
  - [ ] the STK query "still processing" error code (`500.001.1001` assumed)
  - [ ] the STK amount limit (250,000 KES assumed)

## 2. With Safaricom

- [ ] The organization owns the Paybill or Till and has an M-Pesa org portal admin **[VERIFY]**.
- [ ] A Daraja production app is created and approved through Go-Live on the Daraja portal, with the products needed: C2B, and Lipa na M-Pesa Online (STK push) for a Paybill **[VERIFY the current approval steps and timing]**.
- [ ] The production consumer key, consumer secret and (for STK push) passkey are received. The passkey comes from Safaricom by email after Go-Live **[VERIFY]**.

## 3. In the app

- [ ] M-Pesa settings → the account → Environment **Production**; enter the keys (and the passkey for a Paybill). A Till uses its store number as the shortcode. A Paybill is recommended: Till payments have no account number and are matched by hand (D-100).
- [ ] Assign the account to the properties it collects for, and mark the default (Django admin, payment accounts). An account that serves no property is seen only by members with every property.
- [ ] Every unit has the payment reference tenants will type as the account number, and tenants have been told it.
- [ ] **Register URLs.** C2B URLs can be registered once per shortcode in production **[VERIFY]**; if they must change later, ask Safaricom to remove the old registration first. Use "New callback link" only when a link leaks, and register again after.

## 4. First live day

- [ ] Pay a small amount with the right account number: it should be confirmed on the lease and receipted within seconds.
- [ ] Pay with a wrong account number: it should wait in the M-Pesa inbox with an alert, and the payer gets one SMS.
- [ ] Paybill only: send a payment request from a lease to a test phone, approve it, and check the payment lands on that lease. Cancel a second one and check it shows "Not paid".
- [ ] Paybill only: open the lease's payment link (on its "Request M-Pesa payment" page) on a phone, pay a small amount from it, and check the status page turns to "Paid" with a receipt. Then send one rent reminder and check the SMS carries the link and fits the segment count you expect.
- [ ] The next morning, check the M-Pesa daily summary arrived in-app and its totals match the M-Pesa org portal statement for the day.

## 5. If something goes wrong

- Callbacks not arriving: check the registered URLs on the settings page, https, `MPESA_ALLOWED_IPS`, and the server logs for "Daraja callback ... refused".
- A payment is missing: find it in the M-Pesa org portal statement; until the statement import is built (D-043 item 7), record it by hand as an M-Pesa payment with its M-Pesa code as the reference.
- Keys leaked: rotate them on the Daraja portal, save the new ones, and use "New callback link", then register the URLs again.

## 6. Our own subscription payments (D-126)

This is for money landlords pay **us** for NyumbaPMS, not rent. Set up either or both ways; each can be switched off in Django admin (Subscriptions → Online payment switch).

**Common:**
- [ ] Set `PLATFORM_CALLBACK_TOKEN` to 32+ random characters (`python -c "import secrets; print(secrets.token_urlsafe(32))"`), and `OPS_ALERT_EMAILS` so money that needs a person reaches you.

**Our Paybill through Daraja:**
- [ ] On the Daraja portal, make an app for our Paybill (sandbox first) with M-Pesa Express (STK) and C2B. Ask Safaricom for the **passkey** for the go-live.
- [ ] Set `PLATFORM_PAYBILL`, `PLATFORM_DARAJA_CONSUMER_KEY`, `PLATFORM_DARAJA_CONSUMER_SECRET`, `PLATFORM_DARAJA_PASSKEY` and `PLATFORM_DARAJA_ENVIRONMENT` (`SANDBOX`, then `PRODUCTION`). `PLATFORM_DARAJA_SHORTCODE` defaults to the Paybill.
- [ ] Run `manage.py platform_payments` (checks the keys) and then `manage.py platform_payments --register`. The site must be on https for live.
- [ ] Choose a plan in a test organization, press "Pay with M-Pesa", pay the prompt, and check that the invoice turns paid. Then pay the Paybill by hand with the account number shown and check that it is matched.

**Our Paystack:**
- [ ] In our Paystack dashboard (activated as a Kenyan business), turn on card and M-Pesa, and copy the secret key into `PLATFORM_PAYSTACK_SECRET_KEY` (`sk_test_…` first).
- [ ] Paste `<SITE_URL>/hooks/plat/<PLATFORM_CALLBACK_TOKEN>/ps/` (printed by `manage.py platform_payments`) as the Webhook URL. Use Test and Live as the key changes.
- [ ] Pay a test invoice with "Pay with card or M-Pesa (Paystack)" and check that it turns paid.

**If something goes wrong:** money not matched to an invoice comes as an email "Subscription payment … needs a person". Record it in admin (Subscription payments, or an SMS top-up) with the same code, and it is cleared. Attempts and codes received are listed read-only in admin under Subscriptions.
