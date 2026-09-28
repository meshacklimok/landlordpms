# 17 — M-Pesa Go-Live Checklist

Steps to move an organization's Paybill or Till from the Daraja sandbox to live payments (D-045 item 11, step 5). Items marked **[VERIFY]** depend on Safaricom's current process and must be confirmed with Safaricom or in the sandbox.

## 1. Before Safaricom

- [ ] The site runs on **https** and `SITE_URL` is set to the https address. Live callback URLs are refused on http, and Safaricom refuses URLs containing words such as "mpesa" (the app checks both).
- [ ] `FIELD_ENCRYPTION_KEYS` is set in production and backed up outside the server. Losing it makes the stored Daraja keys unreadable.
- [ ] `MPESA_CLIENT` is unset (the real `mpesa.daraja.DarajaClient`), never the fake client.
- [ ] Optional: `MPESA_ALLOWED_IPS` lists Safaricom's callback IPs **[VERIFY the current list with Safaricom]**. If a proxy sits in front of the app, `TRUSTED_PROXY_COUNT` is right, or every callback is refused.
- [ ] `mpesa_daily` is scheduled once a day (early morning, after midnight Nairobi time), next to `billing_daily`, `send_due_messages` and `purge_import_previews`.
- [ ] Sandbox checks done and recorded in D-045 (see TODO.md, Phase 6):
  - [ ] the MSISDN hash format (item 5)
  - [ ] whether a C2B confirmation also arrives for STK payments
  - [ ] the STK query "still processing" error code (`500.001.1001` assumed)
  - [ ] the STK amount limit (250,000 KES assumed)

## 2. With Safaricom

- [ ] The organization owns the Paybill or Till and has an M-Pesa org portal admin **[VERIFY]**.
- [ ] A Daraja production app is created and approved through Go-Live on the Daraja portal, with the products needed: C2B, and Lipa na M-Pesa Online (STK push) for a Paybill **[VERIFY the current approval steps and timing]**.
- [ ] The production consumer key, consumer secret and (for STK push) passkey are received. The passkey comes from Safaricom by email after Go-Live **[VERIFY]**.

## 3. In the app

- [ ] M-Pesa settings → the account → Environment **Production**; enter the keys (and the passkey for a Paybill). A Till uses its store number as the shortcode.
- [ ] Assign the account to the properties it collects for, and mark the default (Django admin, payment accounts). An account that serves no property is seen only by members with every property.
- [ ] Every unit has the payment reference tenants will type as the account number, and tenants have been told it.
- [ ] **Register URLs.** C2B URLs can be registered once per shortcode in production **[VERIFY]**; if they must change later, ask Safaricom to remove the old registration first. Use "New callback link" only when a link leaks, and register again after.

## 4. First live day

- [ ] Pay a small amount with the right account number: it should be confirmed on the lease and receipted within seconds.
- [ ] Pay with a wrong account number: it should wait in the M-Pesa inbox with an alert, and the payer gets one SMS.
- [ ] Paybill only: send a payment request from a lease to a test phone, approve it, and check the payment lands on that lease. Cancel a second one and check it shows "Not paid".
- [ ] The next morning, check the M-Pesa daily summary arrived in-app and its totals match the M-Pesa org portal statement for the day.

## 5. If something goes wrong

- Callbacks not arriving: check the registered URLs on the settings page, https, `MPESA_ALLOWED_IPS`, and the server logs for "Daraja callback ... refused".
- A payment is missing: find it in the M-Pesa org portal statement; until the statement import is built (D-043 item 7), record it by hand as an M-Pesa payment with its M-Pesa code as the reference.
- Keys leaked: rotate them on the Daraja portal, save the new ones, and use "New callback link", then register the URLs again.
