# 22 — Setting up the server on DigitalOcean

The pilot server runs on DigitalOcean, not Hetzner (D-134). This doc is the record of what was actually done on 2026-10-11, in order, with the problems met and how to move from the small test server to the first client's server. It is a living file: new steps and fixes are added at the bottom as they happen.

Doc 21 is the same setup written for Hetzner. Its steps 1 and 3–10 (login key, upload, `setup.sh`, settings, certificate, superuser, off-site copy, outside services, updating, monthly check) work on DigitalOcean unchanged; only the provider parts differ, and those are here. Doc 18 explains how the server runs.

## What the server is

| | |
|---|---|
| Provider | DigitalOcean, project `first-project` |
| Droplet name | `nyumbapms-1` (shows as `nyumbapms` on the server prompt) |
| Region | Frankfurt, **FRA1** (no African region exists; closest with good routes to Nairobi) |
| Image | Ubuntu 24.04 (LTS) x64 |
| Test size | Basic, Regular, **$6/mo**: 1 vCPU, 1 GB RAM, 25 GB SSD (swap of 2 GB is added by `setup.sh`) |
| First-client size | **$12/mo**: 1 vCPU, 2 GB RAM, 50 GB SSD (or $24: 2 vCPU, 4 GB, for several clients) |
| IPv4 | `64.226.127.102` |
| Domain | `app.nyumbapms.com` (A record to the IPv4 above) |
| Login | `ssh root@64.226.127.102`, with the key made in step 1 |
| Extras | Monitoring on (free). Backups off while testing. No managed database: PostgreSQL runs on the Droplet. |

## Why Frankfurt, and why these sizes

- Latency from Kenya is roughly 140–180 ms to Frankfurt, 150–190 ms to London, 150–200 ms to Bangalore, 250 ms or more to the US. Callbacks from Daraja, Paystack and SMS come from servers, so distance does not matter for them; the PWA caches assets and works offline for field screens.
- $4 (512 MB) is too small: PostgreSQL, gunicorn, Redis and nginx together need about 1.5–2 GB, so installs and migrations crash. $6 (1 GB) with the 2 GB swap that `setup.sh` adds is enough for testing with no clients. $12 (2 GB) is the least that runs one paying client comfortably.
- DigitalOcean bills **by the hour** (the $6 Droplet is about $0.009/hour), charged at month end. Destroying a Droplet stops billing for it.
- Hosting abroad: the Data Protection Act needs safeguards for the transfer (a data processing agreement with DigitalOcean, named on the Privacy page). `setup.sh` now writes `HOSTING_LOCATION="DigitalOcean, Frankfurt, Germany (EU)"`. **[VERIFY]** with a Kenyan advocate (same open item as D-122).

## Step 1 — Make your login key (on your computer, once)

In PowerShell:

```powershell
ssh-keygen -t ed25519 -C "nyumbapms"
Get-Content $env:USERPROFILE\.ssh\id_ed25519.pub
```

Press Enter for the default file (a passphrase is optional but better). Copy the line that starts with `ssh-ed25519`: that is the public half, safe to paste anywhere. The file without `.pub` is the private key: never share it.

## Step 2 — Create the account and the Droplet

1. Sign up at [cloud.digitalocean.com](https://cloud.digitalocean.com). It asks for a card before it lets you create anything. The $5 signup credit only covers part of the first bills.
2. **Create › Droplets** (or the teal **Create Droplet** button on the Droplets page), then set:
   - **Region:** Frankfurt, FRA1. Pick the region first; the page can reset the plan when you change it.
   - **Image:** Ubuntu, 24.04 (LTS) x64 (`setup.sh` is written for it).
   - **Plan:** Basic, Shared CPU, Regular (SSD), **$6/mo** for testing.
   - **Authentication:** SSH Key › **Add an SSH Key**, paste the line from step 1, name it `my-pc`, save, and make sure it is ticked. Not a password.
   - **Monitoring:** tick it (free; graphs of CPU, memory and disk, and alerts).
   - **Backups:** leave off while testing (about 20% extra). Turn on when the first client starts.
   - **Do not** add the Managed Database ($15) or a startup script; leave Public IPv4 on, Quantity 1.
   - **Hostname:** `nyumbapms-1`.
3. Click **Create Droplet**. About a minute later the Droplets list shows the Droplet and its addresses.
4. Copy the **IPv4** address (four numbers with dots). The long address with colons is IPv6; the DNS A record and the certificate check in `setup.sh` need the IPv4 one.

### Paying with a card: what to expect

- When a card is added, DigitalOcean sends a **verification hold** (a preauthorization) to the bank, usually $1–15. It was $11.02 here. It is not a charge; it falls off the statement in about 5–7 business days. Some accounts report the amount being kept as account credit instead, so check **Billing**.
- **Each retry can add another hold**, which stacks up and can leave too little available, causing "insufficient funds". Do not retry; check the card balance, wait for holds to release, or call the bank to approve DigitalOcean and 3-D Secure.
- **Prepaid and virtual cards** (app-issued dollar cards, M-Pesa cards) are often refused. A bank debit or credit card is safest. Alternative: link the card to PayPal and pay with PayPal.
- Set a billing alert (**Billing › Billing alerts**, for example at $10).

## Step 3 — Log in

```powershell
ssh root@64.226.127.102
```

Type `yes` **at the question the command asks**, not afterwards at the PowerShell prompt (typing it afterwards gives "yes is not recognized" and the login fails; run `ssh` again). You are in when the prompt reads `root@nyumbapms:~#`. `exit` comes back to your computer.

## Step 4 — Point the domain at the server

In the DNS page of wherever `nyumbapms.com` is managed, add an **A record**: name `app`, value `64.226.127.102`, TTL 300. If the DNS is on Cloudflare, set it to **DNS only** (grey cloud) until the certificate is issued. Check from your computer after a few minutes:

```
nslookup app.nyumbapms.com
```

It must show `64.226.127.102`. `setup.sh` stops at the https step until it does.

## Step 5 — Upload the code

On your computer, in the project folder, **in cmd** (the terminal that shows `(venv) C:\...>` after `venv\Scripts\activate.bat`). `$env:TEMP` only works in PowerShell; in cmd it is written `%TEMP%`, and using the wrong one gives "Failed to open '$env:TEMP\...'":

```
tar --exclude=./venv --exclude=./.git --exclude=./.env --exclude=./.env.production --exclude=./.claude --exclude=./media --exclude=./backups --exclude=__pycache__ --exclude=.pytest_cache --exclude=.ruff_cache -czf %TEMP%\nyumbapms.tar.gz .
dir %TEMP%\nyumbapms.tar.gz
scp %TEMP%\nyumbapms.tar.gz root@64.226.127.102:/root/
```

The archive was about 2.2 MB. The two secret files (`.env`, `.env.production`) are left out on purpose. In PowerShell the same command uses `$env:TEMP\nyumbapms.tar.gz`.

Then on the server:

```bash
mkdir -p /srv/landlordpms && tar -xzf /root/nyumbapms.tar.gz -C /srv/landlordpms
ls /srv/landlordpms
```

The many lines "Ignoring unknown extended header keyword 'SCHILY.fflags'" are harmless (Windows file attributes). The list must show `manage.py` and `deploy`.

## Step 6 — Run the setup

```bash
CERT_EMAIL=limomeshk@gmail.com bash /srv/landlordpms/deploy/setup.sh
```

It installs everything (several minutes) and **stops on purpose** after writing `/etc/landlordpms.env`, asking for the `FILL_ME` values. Fill them as in doc 21 step 6 (`nano /etc/landlordpms.env`, then run the same line again). It may also stop at the certificate until the DNS in step 4 has spread; wait, then run the same line again.

## Steps 7 onward

Create the login (`createsuperuser`), the off-site backup copy, outside services (uptime monitor, email DNS, Africa's Talking callbacks, M-Pesa, Paystack), updating and the monthly check: as in doc 21. Two provider differences for the off-site copy and the "disk full" row:

- **Off-site copy:** doc 21 uses a Hetzner Storage Box. On DigitalOcean use **Spaces Object Storage** (S3-compatible, about $5/mo) in a different region, or any other S3/SFTP store, with `rclone` encrypting before upload. Steps (replace the `offsite` lines of doc 21 step 9; the `offsite-crypt` name must stay, `deploy/offsite.sh` uses it):
  1. DigitalOcean › **Spaces** › create a bucket (for example `nyumbapms-backups`) in a region other than the Droplet's, and a **Spaces access key** for it.
  2. `sudo -u landlordpms rclone config create offsite s3 provider DigitalOcean access_key_id 'KEY' secret_access_key 'SECRET' endpoint 'ams3.digitaloceanspaces.com' acl private` (use your bucket region's endpoint).
  3. `sudo -u landlordpms rclone config create offsite-crypt crypt remote offsite:nyumbapms-backups password 'A-LONG-RANDOM-PASSWORD' password2 'ANOTHER-LONG-ONE' --obscure`. Keep both passwords somewhere safe off the server (without them the copies cannot be read).
  4. Test: `sudo -u landlordpms sh /srv/landlordpms/deploy/run.sh backup --offsite`, then `sudo -u landlordpms rclone ls offsite-crypt:nyumbapms`. **[VERIFY: not yet run on a real Space.]**
  5. Turn on versioning for the bucket if the plan offers it, so a deleted file can be recovered.
- **Off-site copy, free option: Backblaze B2** (10 GB free, S3-style, different company from DigitalOcean). Same steps, different provider:
  1. backblaze.com › B2 Cloud Storage › sign up › **Buckets › Create a Bucket** (private, unique name such as `nyumbapms-backups-x7k2`). Lifecycle: keep only the last version.
  2. **Application Keys › Add a New Application Key**, limited to that bucket, read and write. Copy the keyID and applicationKey (shown once) into a password manager.
  3. `sudo -u landlordpms rclone config create offsite b2 account 'KEYID' key 'APPLICATIONKEY'`
  4. `sudo -u landlordpms rclone config create offsite-crypt crypt remote 'offsite:BUCKET-NAME' password 'LONG-1' password2 'LONG-2' --obscure` (keep both passwords off the server).
  5. Test as in step 4 above. **Verified 2026-10-20** on the Droplet (165.245.221.108): `backup --offsite` reported `offsite: copied`, and `rclone ls offsite-crypt:nyumbapms` listed the encrypted db and media files.
- **Backup at any time:** `sudo -u landlordpms sh /srv/landlordpms/deploy/run.sh backup --offsite` makes a fresh dump and copies it off the server. Leave off `--offsite` for a local-only copy. It is recorded like the nightly run and does not clash with it (the lock skips a run while another is going).
- **Disk full or server too small:** resize the Droplet (below).

## Moving to the first client: resize to $12

Do it a day before the client starts, not on the day.

1. Droplets › `nyumbapms-1` › **Power** › **Turn off**.
2. **Resize** › choose the **$12/mo (2 GB)** plan.
3. Choose **CPU and RAM only**. The disk stays the same size, so the change can be undone later. ("Disk, CPU and RAM" makes the disk bigger for good and cannot be shrunk.)
4. Power on. The IP and data stay the same; downtime is about 5 minutes.
5. On the server run `sudo -u landlordpms sh /srv/landlordpms/deploy/run.sh db_health` and then turn on **Backups** (Droplet page › Backups › Enable). The load test (`scale_test`, Locust) is only worth running now, not on the 1 GB test size.
6. Add a **Reserved IP** (Networking) if you want to rebuild the server later without changing DNS or any IP whitelist.

## Costs at a glance

| Item | Per month |
|---|---|
| Droplet $6 (testing) | $6 |
| Droplet $12 (first client) | $12 |
| Droplet $24 (several clients) | $24 |
| Droplet backups | +20% of the Droplet price |
| Spaces for off-site backups | about $5 |
| Managed PostgreSQL (not used) | $15 and up |

## When something breaks (DigitalOcean specific)

| You see | Do |
|---|---|
| "Add Payment Method" will not complete, "insufficient funds" | See *Paying with a card* above: stop retrying, check the card and the holds |
| `yes : The term 'yes' is not recognized` | You typed `yes` at the PowerShell prompt; run `ssh root@64.226.127.102` again and type it at the question |
| `tar: Failed to open '$env:TEMP\...'` | You are in cmd; use `%TEMP%` |
| `ssh` asks for a password | The SSH key was not attached; use the Droplet's **Console** (Droplet page › Access › Launch Droplet Console) and add it to `/root/.ssh/authorized_keys` |
| Locked out, or ssh will not connect | Droplet page › **Access › Launch Droplet Console** gives a terminal without ssh |
| The site is slow or processes die on the $6 size | Memory is full: check the Droplet's graphs, turn off the job runner and assistant, or resize to $12 |
| `mkdir: cannot create directory '/srv/landlordpms/media': Permission denied` | The code was unpacked as root from a Windows archive, so the folder was not writable. Run `chmod -R u+rwX,go+rX /srv/landlordpms` and `chown -R landlordpms:landlordpms /srv/landlordpms`, then run `setup.sh` again (the script now does both) |
| `*** Fill in the FILL_ME values` even though every value is filled | A comment line in `/etc/landlordpms.env` contained the word FILL_ME and tripped the check (script bug, fixed). On an older copy run `sed -i 's/replace each FILL_ME/replace each placeholder/' /etc/landlordpms.env`, check `grep '=FILL_ME' /etc/landlordpms.env` prints nothing, and run `setup.sh` again |
| `setup.sh` ends silently right after the line `==> https certificate for ...` | The DNS A record is not live yet (an older copy of the script exited without a message). Add the record, check `nslookup app.nyumbapms.com` shows the server IP, and run `setup.sh` again |
| Everything else | Doc 21 "When something breaks", and doc 18 |

## Progress log

- 2026-10-11: account created and card added (verification hold of $11.02). Droplet `nyumbapms-1` created at Frankfurt, $6, IPv4 `64.226.127.102`. SSH login works. Code (2.2 MB) uploaded and unpacked to `/srv/landlordpms`.
- 2026-10-11: first `setup.sh` run installed packages, swap and firewall, then stopped on a permissions error (code unpacked as root); `chown` alone did not fix it; `chmod -R u+rwX,go+rX` plus `chown` did, and the script now does both. Second run went through to the settings file (`FILL_ME` stop).
- 2026-10-11: settings filled with stand-ins (email `localhost`, SMS console backend with `ALLOW_CONSOLE_SMS=1`, alerts to `mlcodeprojectsapi@gmail.com`). `setup.sh` kept stopping at the FILL_ME check because of a comment line in the settings file; check fixed to look for `NAME=FILL_ME` only.
- 2026-10-11: `setup.sh` ran through the database tables (all migrations OK), web service and nginx, and scheduled jobs. It then stopped at the https certificate because the DNS A record for `app` is not added yet. Script fixed to print the message.
- 2026-10-11: Cloudflare A record `app` -> `64.226.127.102` added as **DNS only**. `setup.sh` finished: https certificate issued (expires 2027-01-09, renews by itself), first backup taken, launch check ran. 13 launch-check TODOs remain, all expected on day one: live SMS, live M-Pesa, subscription payments, the 10 scheduled jobs that have not run yet, plus the off-site copy and uptime monitor. Note: the SSH window dropped once mid-run (idle `Connection reset`); log in again and re-run `setup.sh`, it skips finished steps. Next: `createsuperuser`, log in at https://app.nyumbapms.com, off-site backup to Spaces, uptime monitor.
- 2026-10-11: superuser created (`createsuperuser`, phone login), logged in at https://app.nyumbapms.com and turned on two-step login. The site is live; automatic scanners already probe `/.env` paths and get 404 (normal). While SMS is in console mode, login codes are in `journalctl -u landlordpms -f | grep --line-buffered "SMS to"`. Next: uptime monitor, Spaces off-site backups, Brevo email, Africa's Talking live SMS, resize to $12.
- 2026-10-11: email now supports two providers (`notifications/providers/mail.py`). `EMAIL_*` is the `default` mailer (sign-in codes, alerts, support, demo requests; Resend); optional `EMAIL_MESSAGES_*` is the `messages` mailer (tenant messages and receipts, owner statements, owner invitations; Brevo). Without `EMAIL_MESSAGES_HOST` everything uses `default`. Both send from `DEFAULT_FROM_EMAIL`, so both need nyumbapms.com verified, with ONE merged SPF record including both providers. In Cloudflare set every email record to DNS only. `manage.py launch_check` now signs in to each mail server (sends nothing) and fails on a wrong password. After editing `/etc/landlordpms.env`: `systemctl restart landlordpms`, then `manage.py sendtestemail you@gmail.com` (tests `default`).
- 2026-10-12: Resend set up as the `default` mailer with `deploy/set_email.sh` (asks only for the API key). DigitalOcean blocks outgoing SMTP on ports 25, 465 and 587 (the app timed out on 587), so the script sets `EMAIL_PORT=2587` (Resend's STARTTLS port; same encryption). Brevo uses 2525 for the same reason. A `535 Authentication credentials invalid` error means the key was wrong; make a new one. Do not re-run an old copy of the script: it deletes the `EMAIL_PORT` line. `sendtestemail` prints nothing when it works; check the inbox. Still to do: confirm SPF/DKIM/DMARC pass in Gmail "Show original", delete `/etc/landlordpms.env.bak`, Brevo as the `messages` mailer.
- 2026-10-12: Brevo set up as the `messages` mailer with `deploy/set_messages_email.sh` (asks for the SMTP login and `xsmtpsib-` key; port 2525 because DigitalOcean blocks 587). Domain authenticated with branded subdomain `em`; sender `no-reply@nyumbapms.com`. Brevo blocks SMTP from unknown IPs: the first attempt gave `525 Unauthorized IP address` and the server's IP appeared under Settings > Security > Authorized IPs > "Unauthorized IP addresses"; authorizing it fixed it. **A new server (or a changed IP) needs this again.** Code reached the server with the tar + `update.sh` flow. `launch_check` (run as the app user: `runuser -u landlordpms -- sh deploy/run.sh launch_check`) now shows both mailers signing in. Test either with `sh deploy/run.sh sendtestemail [--using messages] you@example.com`.

### 2026-10-12 - Swapping the two mail accounts in admin

Django admin > Core > Email routing chooses which account sends which mail: "Sign-in codes, alerts and support" and "Mail to tenants and owners", each Resend or Brevo. The logins stay in `/etc/landlordpms.env` (`EMAIL_*` is Resend, `EMAIL_MESSAGES_*` is Brevo); the page shows whether each is set up. A choice whose account is missing falls back to the first one. Takes effect at once, no restart. Each change is in the audit log (`platform.email_routing`).

### 2026-10-22 - Next.js screens, status check and empty settings

An audit of the live server found `web/.next` missing, nothing on port 3000, and nginx sending every page to Django, so the Next.js screens (D-168 onwards, all hostel screens) were not live. The repo now has the fix; the server needs these steps once (the server folder is not a git checkout, so it comes by tar):

1. On your computer, make the tar as in step 4 (it now leaves out `node_modules` and `.next`), upload it, and run `bash /srv/landlordpms/deploy/update.sh` as root. It installs Node.js 22 if missing, runs `npm ci && npm run build` in `web/`, installs and starts `nyumbapms-web.service` (port 3000, Django on 8000), and reloads Django.
2. nginx is not overwritten by `update.sh` (certbot edited the live copy). Add the routes once: `sed -e "s/DOMAIN/app.nyumbapms.com/g" /srv/landlordpms/deploy/nginx.conf` shows the new file; the pieces to copy into the live https server block are the `/_next/` location, the "Screens Next.js draws" location, and `location = /`, placed before `location / {`. Then `nginx -t && systemctl reload nginx`. Only those screens go to Next.js; webhooks, USSD, static files and sign-in still go straight to Django, so `TRUSTED_PROXY_COUNT=1` stays right.
3. Check: `bash /srv/landlordpms/deploy/status.sh` lists the services, port 3000 and 8000 answers, any pending migrations (run as the owner role, so the environment file is loaded) and every setting still empty.
4. When a new screen is added under `web/src/app`, add its path to the regex in the "Screens Next.js draws" location of `deploy/nginx.conf` and in the live copy. Pages from Next.js carry no Django CSP header yet.

Settings that only you can fill in `/etc/landlordpms.env` (credentials from the provider), then `systemctl restart landlordpms`: Africa's Talking `AT_USERNAME`, `AT_API_KEY`, `AT_SENDER_ID`, `USSD_CODE`; our Paybill `PLATFORM_PAYBILL`, `PLATFORM_DARAJA_*` (then `manage.py platform_payments --register`); `PLATFORM_PAYSTACK_SECRET_KEY`; Meta WhatsApp `WHATSAPP_BACKEND=notifications.providers.whatsapp.CloudApiWhatsAppSender`, `WA_PHONE_NUMBER_ID`, `WA_ACCESS_TOKEN`, `WA_APP_SECRET`; legal text `LEGAL_NAME`, `DATA_PROTECTION_EMAIL`, `PLATFORM_KRA_PIN`; optional `MPESA_ALLOWED_IPS`, `PAYSTACK_ALLOWED_IPS`.
