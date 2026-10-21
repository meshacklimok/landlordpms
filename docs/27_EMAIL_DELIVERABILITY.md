# 27. Email deliverability (SPF, DKIM, DMARC)

Why: without these three DNS records for the sending domain, Gmail and Outlook may put NyumbaPMS mail in spam,
or show "via resend.com" or "via brevo.com" beside the sender. Since 2024 Gmail and Yahoo also reject bulk mail
from domains that have no SPF/DKIM and DMARC. This is set up in DNS and in each mail provider, not in code.
It is done once per sending domain, by the owner. [OWNER TASK]

## What we send, and from where

| Mail | Provider (default) | Env vars |
|---|---|---|
| Sign-in codes, alerts, support, demo requests | Resend ("system", alias `default`) | `EMAIL_HOST`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` |
| Tenant mail (invoices, receipts, reminders), owner invites and statements | Brevo ("messages") | `EMAIL_MESSAGES_HOST`, `EMAIL_MESSAGES_USER`, `EMAIL_MESSAGES_PASSWORD` |

The From address is `DEFAULT_FROM_EMAIL` (`NyumbaPMS <no-reply@nyumbapms.com>`). Mail for a landlord shows the
landlord's name before that same address; a landlord who sets up their own mail server (Settings > Email) sends from
their own domain and must do these steps for that domain.

Which provider handles which kind is chosen in the console (Control > Email).

## Steps, for nyumbapms.com

1. **Verify the domain in each provider.** In Resend: Domains > Add domain `nyumbapms.com`. In Brevo: Senders,
   Domains & Dedicated IPs > Domains > Add a domain. Each shows the DNS records to add.
2. **SPF** (one TXT record on the root domain, covering both providers). Providers differ, so copy the
   `include:` values they show you and keep a single record, for example:
   `v=spf1 include:amazonses.com include:spf.brevo.com ~all`
   (Resend sends through Amazon SES, so the include it shows is usually `amazonses.com`, often on a
   `send.` subdomain. Follow what the provider shows.) Two separate SPF records break SPF.
3. **DKIM.** Add the CNAME/TXT records each provider shows (Resend `resend._domainkey`, Brevo `brevo1._domainkey` and
   `brevo2._domainkey`). Wait for the provider to show "Verified".
4. **DMARC** (TXT on `_dmarc.nyumbapms.com`). Start by watching, then tighten:
   - Week 1 to 4: `v=DMARC1; p=none; rua=mailto:dmarc@nyumbapms.com; fo=1`
   - After the reports show only your two providers passing: `p=quarantine`, later `p=reject`.
5. **Return path / custom tracking domain.** Turn on the provider's custom return path (Resend: automatic with a
   verified domain; Brevo: "authenticate your domain" with the brevo code) so the "via" label disappears.
6. **Reverse check.** Send a test to a Gmail address, open "Show original" and confirm `SPF: PASS`, `DKIM: PASS`,
   `DMARC: PASS`. Repeat for an Outlook.com address.
7. **Site address for the logo.** `SITE_URL` must be the public address (https://app.nyumbapms.com). The NyumbaPMS
   logo in our own emails loads from `SITE_URL/static/icons/apple-touch-icon.png`.

## Keep it healthy

- Keep complaints under 0.1% and bounces under 2% (both providers show this). The List-Unsubscribe headers are
  already sent on tenant mail.
- Never send from a landlord's own domain unless they have completed the steps above for it.
- Check DMARC reports weekly for the first month, then monthly (put it in `deploy/monthly_check.sh` notes).
- Reply-To on tenant mail is the landlord's reply address (Settings), so replies reach them and not us.
