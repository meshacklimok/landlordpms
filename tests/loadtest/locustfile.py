"""Many people using the site at once (D-105, doc 20 §30).

First build the data on a throwaway database:

    python manage.py loadtest_setup --leases 200 --staff 10 --tenants 50

then start the site there, and in another terminal:

    locust -f tests/loadtest/locustfile.py --host http://127.0.0.1:8005 --users 60 --spawn-rate 5 --run-time 5m --headless

Three kinds of visitor, in proportion:
- staff (3): the home page, the lists and a lease's account, a few seconds apart;
- tenants (5): their portal page and lease, less often;
- Safaricom (1): an M-Pesa payment every few seconds, each a new receipt to a real unit's reference,
  so every one is matched, allocated, receipted and posted to the ledger.

Locust prints response times by page and the failures. This file does not import Django.
"""

import json
import os
import random
import string
from pathlib import Path

from locust import HttpUser, between, task

SETUP = json.loads(Path(os.getenv("LOADTEST_SETUP", "tests/loadtest/setup.json")).read_text(encoding="utf-8"))


class _SignedIn(HttpUser):
    abstract = True

    def sign_in(self, session: str):
        self.client.cookies.set(SETUP["session_cookie"], session)

    def page(self, path: str, name: str | None = None):
        with self.client.get(path, name=name or path, allow_redirects=False, catch_response=True) as response:
            if response.status_code != 200:
                response.failure(f"{response.status_code} (a redirect usually means the session was not accepted)")


class Staff(_SignedIn):
    weight = 3
    wait_time = between(2, 8)

    def on_start(self):
        self.sign_in(random.choice(SETUP["staff_sessions"]))

    @task(3)
    def home(self):
        self.page("/")

    @task(6)
    def a_list(self):
        self.page(random.choice(SETUP["staff_pages"]))

    @task(2)
    def a_lease_account(self):
        self.page(random.choice(SETUP["lease_pages"]), name="/billing/leases/[id]/")


class Tenant(_SignedIn):
    weight = 5
    wait_time = between(5, 20)

    def on_start(self):
        self.me = random.choice(SETUP["tenants"])
        self.sign_in(self.me["session"])

    @task(2)
    def portal(self):
        self.page("/my/")

    @task(1)
    def lease(self):
        self.page(f"/my/leases/{self.me['lease']}/", name="/my/leases/[id]/")


class Safaricom(HttpUser):
    weight = 1
    wait_time = between(1, 5)

    @task
    def confirmation(self):
        receipt = "LT" + "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
        payload = {"TransactionType": "Pay Bill", "TransID": receipt, "TransTime": "20260203101500",
                   "TransAmount": f"{random.randint(5, 50) * 100}.00", "BusinessShortCode": SETUP["shortcode"],
                   "BillRefNumber": random.choice(SETUP["references"]), "MSISDN": "254700000000",
                   "FirstName": "Load", "MiddleName": "", "LastName": "Test"}
        with self.client.post(SETUP["callback"], json=payload, name="M-Pesa confirmation",
                              catch_response=True) as response:
            if response.status_code != 200 or response.json().get("ResultCode") not in (0, "0"):
                response.failure(f"{response.status_code} {response.text[:100]}")
