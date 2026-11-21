# 21 — Setting up the server on Hetzner

How to put NyumbaPMS live at `https://app.nyumbapms.com` on one Hetzner server (D-122). Written to be followed step by step from a Windows computer. The first time takes about 2–3 hours, most of it waiting for Hetzner, DNS or your email provider.

Doc 18 explains how the server runs (jobs, settings, backups, alerts). This doc is the setup itself.

## What you need first

- A card for Hetzner, and ID if they ask for it (sign up a few days early; checks can take hours).
- Your domain's DNS page (wherever you bought nyumbapms.com).
- An email provider account for sending (Brevo's free plan is enough to start), with nyumbapms.com added.
- Your **live** Africa's Talking username and API key.
- A password manager. Everything marked 🔑 below goes in it.

## Step 1 — Make your login key (on your computer, once)

Open PowerShell and run:

```powershell
ssh-keygen -t ed25519 -C "nyumbapms"
```

Press Enter for the default file, and give it a passphrase. 🔑 Save the passphrase. Then show the public half:

```powershell
Get-Content $env:USERPROFILE\.ssh\id_ed25519.pub
```

Copy that line (it starts with `ssh-ed25519`). The file without `.pub` is your private key: never send it to anyone.

## Step 2 — Create the server and point the domain at it

1. At [console.hetzner.com](https://console.hetzner.com), create a project called **NyumbaPMS**, then **Add Server**:
   - **Location:** Falkenstein or Nuremberg (Germany).
   - **Image:** Ubuntu 24.04.
   - **Type:** Shared vCPU, x86, the plan with **2 vCPU and 4 GB RAM** (named CX22 or CX23 in Hetzner's current list). The 4 vCPU / 8 GB plan also works if the price difference is small.
   - **SSH key:** paste the line from step 1.
   - **Backups:** tick it (about 20% extra).
   - **Name:** `nyumbapms-1`.
2. Note the server's **IPv4 address**.
3. In your domain's DNS page, add an **A record**: name `app`, value the IPv4 address, TTL 300 (or the lowest offered).

## Step 3 — Log in

```powershell
ssh root@<IP>
```

Type `yes` the first time. You are now typing on the server; `exit` brings you back.

## Step 4 — Upload the code (on your computer)

In a **new** PowerShell window, from the project folder, pack the code without the virtual environment, uploads, backups or your local `.env`:

```powershell
cd C:\Users\user\OneDrive\Desktop\webdev2025\landlordpms
tar -czf $env:USERPROFILE\nyumbapms.tar.gz --exclude=venv --exclude=node_modules --exclude=.next --exclude=media --exclude=backups --exclude=staticfiles --exclude=.env --exclude=.env.production --exclude=.git --exclude=__pycache__ --exclude=.pytest_cache --exclude=.vscode --exclude=*.dump .
scp $env:USERPROFILE\nyumbapms.tar.gz deploy\setup.sh root@<IP>:/root/
```

Instead of uploading, the server can pull from GitHub once the code is pushed: run step 5 with `REPO_URL=git@github.com:meshacklimok/landlordpms.git` in front, and it prints a key to add under the repository's **Settings › Deploy keys**.

## Step 5 — Run the setup (on the server)

```bash
bash /root/setup.sh
```

It installs everything (PostgreSQL, Redis, nginx, Python packages), sets the time zone to Nairobi, turns on the firewall and automatic security updates, creates the database and its two users, and writes the settings file with new random secrets. Then it **stops** and asks you to fill in the details only you have.

## Step 6 — Fill in the settings

```bash
nano /etc/landlordpms.env
```

Replace every `FILL_ME`:

| Setting | What to put |
|---|---|
| `EMAIL_HOST`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` | From your email provider's SMTP page (Brevo: `smtp-relay.brevo.com`) |
| `OPS_ALERT_EMAILS` | Your own email: job and backup alerts go here |
| `AT_USERNAME`, `AT_API_KEY` | Your live Africa's Talking account |

Everything else (secrets, database password, encryption key, phone-alert keys, callback tokens) is already generated. The optional lines are labelled in the file; fill any you have now, or later:

| Setting | What to put | Without it |
|---|---|---|
| `OPENAI_API_KEY` | From platform.openai.com › API keys | The assistant is not offered |
| `LEGAL_NAME`, `DATA_PROTECTION_EMAIL`, `SUPPORT_WHATSAPP` | Your registered company name and contacts | Terms and privacy pages say "NyumbaPMS" and the support email |
| `PLATFORM_PAYBILL`, `PLATFORM_KRA_PIN` | Where landlords pay your subscription; your KRA PIN | Subscription invoices leave them out |
| `USSD_CODE` | The code Africa's Talking assigns | USSD is hidden |
| `WHATSAPP_BACKEND` + `WA_*` | From Meta's WhatsApp Cloud API | Tenants cannot ask on WhatsApp |
| `AT_SENDER_ID` | `NYUMBAPMS` once Africa's Talking approves it | SMS come from the shared sender |

Save with Ctrl+O, Enter, then Ctrl+X. After any later change: `systemctl restart landlordpms`.

M-Pesa keys are **not** in this file: each landlord's Daraja keys go in the app.

🔑 Copy `FIELD_ENCRYPTION_KEYS` and `SECRET_KEY` from this file into your password manager. Without `FIELD_ENCRYPTION_KEYS`, saved M-Pesa keys cannot be read after a rebuild.

## Step 7 — Run the setup again, with your email

```bash
CERT_EMAIL=you@example.com bash /root/setup.sh
```

It creates the tables, starts the site, installs the scheduled jobs, gets the https certificate and takes the first backup. If it stops at the certificate saying the domain points elsewhere, the DNS from step 2 has not spread yet: wait 10 minutes and run the same line again.

At the end it runs the launch check. Some lines will say `TODO` on day one (for example that every job has run, including the monthly restore test); they clear by themselves within a day, except the restore test, which you can run now:

```bash
sudo -u landlordpms sh /srv/landlordpms/deploy/run.sh restore_test
```

## Step 8 — Create your login

```bash
sudo -u landlordpms sh /srv/landlordpms/deploy/run.sh createsuperuser
```

Open `https://app.nyumbapms.com`, log in with that phone number and password, and turn on two-step login.

## Step 9 — Off-site copy of the backups

Backups are written every night to `/var/backups/landlordpms` on the server. A second copy must live elsewhere, in case the server is lost.

1. In the Hetzner console, order a **Storage Box** (the smallest, BX11) in a different location from the server (for example Helsinki). In its settings, turn on **SSH support** and **automatic snapshots** (daily), so a copy deleted by mistake can still be recovered. Note its username (`uXXXXXX`) and set a password. 🔑
2. On the server, connect it, with the backups encrypted before they leave:

```bash
sudo -u landlordpms rclone config create offsite sftp host uXXXXXX.your-storagebox.de user uXXXXXX port 23 pass 'STORAGE-BOX-PASSWORD' --obscure
sudo -u landlordpms rclone config create offsite-crypt crypt remote offsite:nyumbapms-backups password 'A-LONG-RANDOM-PASSWORD' --obscure
sudo -u landlordpms sh /srv/landlordpms/deploy/offsite.sh
sudo -u landlordpms rclone ls offsite-crypt:nyumbapms
```

🔑 Save `A-LONG-RANDOM-PASSWORD`: without it the off-site backups cannot be opened. The last command should list the backup files. From now on the copy runs every night at 03:40.

## Step 10 — Outside services

- **Uptime monitor:** at a free service such as UptimeRobot, add an https monitor for `https://app.nyumbapms.com/healthz` every 5 minutes, alerting your email and phone.
- **Email DNS:** add the SPF, DKIM and DMARC records your email provider shows for nyumbapms.com, so mail does not go to spam.
- **Africa's Talking callbacks** (the token is `AT_CALLBACK_TOKEN` in `/etc/landlordpms.env`):
  - SMS delivery reports: `https://app.nyumbapms.com/hooks/sms/africastalking/<token>/delivery/`
  - Incoming SMS: `https://app.nyumbapms.com/hooks/sms/africastalking/<token>/inbound/`
  - Opt-outs: `https://app.nyumbapms.com/hooks/sms/africastalking/<token>/optout/`
  - USSD (when you have a code): `https://app.nyumbapms.com/hooks/ussd/africastalking/<token>/`
- **Sender ID:** ask Africa's Talking for `NYUMBAPMS`; when approved, set `AT_SENDER_ID=NYUMBAPMS` and run `systemctl restart landlordpms`.
- **M-Pesa (Daraja):** for each landlord, log in as them (or have them log in) and open **M-Pesa › Settings**: add the Paybill or Till, choose **Live**, paste the consumer key, consumer secret and passkey from their Daraja go-live app, and save. The app registers the callback URLs with Safaricom; the account shows when they were accepted. `daraja_sandbox_check` (doc 17) checks the callback address first with sandbox keys.
- **Check:** `sudo -u landlordpms sh /srv/landlordpms/deploy/run.sh launch_check` now also says whether SMS is live and how many landlords can take live M-Pesa payments.

## Updating the app

On your computer, pack and upload as in step 4. Then on the server:

```bash
bash /srv/landlordpms/deploy/update.sh
```

It takes a backup, swaps in the new code (keeping uploads), installs packages, updates the tables, and reloads the site without dropping requests. It ends with "Updated; the site answers", or tells you where to look.

## Every month (about 30 minutes)

```bash
bash /srv/landlordpms/deploy/monthly_check.sh
```

It shows whether the services run, how many updates are waiting, disk space, the certificate's expiry, the newest off-site backup, database health and the launch check. Then:

1. `apt update && apt upgrade` to install updates (security updates already install by themselves each night).
2. If it said a restart is needed: `reboot`, in the evening. The site is back in about a minute.
3. If the disk is over 80% full, resize the server in the Hetzner console.

## When something breaks

| You see | Do |
|---|---|
| An email "[NyumbaPMS] Problem: …" | It names the job or check. Doc 18 §6–7. |
| The site does not open | `systemctl status landlordpms nginx`, then `journalctl -u landlordpms -n 50` |
| "502 Bad Gateway" | The web service stopped: `systemctl restart landlordpms` and read the journal |
| Locked out of ssh | Hetzner console › the server › **Console** gives a screen without ssh |
| The server is lost | Rebuild with steps 2–8 and restore the newest off-site backup (doc 18 §5) |

## Useful commands

```bash
systemctl status landlordpms          # is the site running
journalctl -u landlordpms -f          # live log (Ctrl+C to stop)
sudo -u landlordpms sh /srv/landlordpms/deploy/run.sh <command>   # any manage.py command, with the live settings
crontab -u landlordpms -l             # the scheduled jobs
ls -lh /var/backups/landlordpms       # local backups
```
