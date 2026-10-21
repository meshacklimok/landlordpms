"""Builds the public "Try the demo" organization (D-210): a sample landlord with 74 units in 4 properties.

Everything goes through the real services, like `seed_demo`, so the figures, charts and reports are the ones a landlord
would see. Dates are worked out from today, so the demo always shows the last ten months, with a mix of prompt, late,
partial and stopped payers, tenants who moved out, repairs and expenses.

Each run makes a fresh organization, then archives the old one: visitors keep browsing the old data until the new one is
complete, and a failed run leaves the old demo in place. Run weekly (deploy/crontab) so the dates never go stale.
Safe on a server with real SMS: the organization is marked `is_demo`, and `notify` sends nothing for it.
"""

import datetime
import random
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from accounts import demo
from accounts import services as account_services
from accounts.models import Membership, Organization, Role
from accounts.permissions import can
from billing.models import Invoice
from core.jobs import JobCommand
from core.kenya import County
from core.money import format_money
from expenses import services as expense_services
from expenses.models import Expense
from leases.models import Lease
from maintenance import services as repair_services
from maintenance.models import MaintenanceRequest
from meters import services as meter_services
from meters.models import Meter
from mpesa.models import MpesaTransaction
from notifications import catalog
from notifications.models import Message
from payments.models import Payment
from payments.services import record_payment
from portal.models import TenantAccount
from properties.models import Property, Unit
from prospects import services as prospect_services
from prospects.models import Prospect, Viewing
from subscriptions.services import subscription_for
from tenants.models import Tenant
from tenants.services import create_tenant

from .seed_demo import APT, BED, OFF, SHOP, STU
from .seed_demo import Command as SeedDemo

DAY = datetime.timedelta(days=1)
PROMPT, LATE, PARTIAL, STOPPED = "prompt", "late", "partial", "stopped"
HABITS = (PROMPT,) * 62 + (LATE,) * 22 + (PARTIAL,) * 8 + (STOPPED,) * 8

FIRST = ["Wanjiku", "Otieno", "Achieng", "Kiprotich", "Mwangi", "Fatuma", "Mutua", "Chebet", "Nekesa", "Daniel", "Aisha",
         "Njeri", "Kamau", "Akinyi", "Barasa", "Wairimu", "Kibet", "Atieno", "Mohamed", "Wambui", "Juma", "Naliaka",
         "Kariuki", "Awino", "Cheruiyot", "Muthoni", "Odhiambo", "Zawadi", "Maina", "Adhiambo", "Ochieng", "Faith",
         "Brian", "Mercy", "Kevin", "Joyce", "Samuel", "Esther", "Peter", "Lucy", "Hassan", "Halima", "Collins", "Gladys"]
LAST = ["Kamau", "Odhiambo", "Atieno", "Kiplagat", "Njoroge", "Hassan", "Musyoka", "Rotich", "Wafula", "Omondi",
        "Mohamed", "Mwangi", "Wekesa", "Otieno", "Kimani", "Chebet", "Mutiso", "Ndungu", "Korir", "Waweru", "Barasa",
        "Mburu", "Onyango", "Kosgei", "Gitau", "Wanyama", "Karanja", "Ouma", "Langat", "Macharia", "Simiyu", "Kilonzo"]
COMPANIES = [("Savannah Traders Ltd", "Grace Wambui"), ("Jiko Bora Kitchens Ltd", "Samuel Kariuki"),
             ("Upendo Medical Clinic", "Dr. Esther Njeri"), ("Mwangaza Pharmacy Ltd", "Hassan Abdi"),
             ("Kilele Tailors", "Mary Achieng"), ("Tumaini Cyber & Printing", "John Kiprop"),
             ("Bidii Hardware Ltd", "Peter Mwaura"), ("Zuri Beauty Salon", "Faith Wanjiru")]

# (code, name, county, sub county, area, street, category, [(building, [(code prefix, count, type, label, rent)])])
Block = tuple[str, list[tuple[str, int, str, str, int]]]
SITES = [
    dict(code="RVS", name="Riverside Apartments", county=County.NAIROBI, sub_county="Dagoretti North", area="Kilimani",
         street="Riverside Drive", category=Property.Category.APARTMENT_BLOCK, market_value=210_000_000,
         blocks=[("Block A", "A", [(5, APT, "2 bedroom", 35000), (3, APT, "3 bedroom", 42000)]),
                 ("Block B", "B", [(5, APT, "1 bedroom", 26000), (3, STU, "Studio", 18000)]),
                 ("Block C", "C", [(4, APT, "1 bedroom", 24000), (2, APT, "2 bedroom", 33000)])]),
    dict(code="KHW", name="Kahawa Court", county=County.KIAMBU, sub_county="Ruiru", area="Kahawa Wendani",
         street="Off Thika Road", category=Property.Category.APARTMENT_BLOCK, market_value=95_000_000,
         blocks=[(None, "K", [(10, BED, "Bedsitter", 9500), (10, APT, "1 bedroom", 12500), (2, APT, "2 bedroom", 16500)])]),
    dict(code="MOI", name="Moi Avenue Plaza", county=County.NAIROBI, sub_county="Starehe", area="CBD",
         street="Moi Avenue", category=Property.Category.COMMERCIAL, market_value=260_000_000,
         blocks=[(None, "S", [(1, SHOP, "Ground-floor shop", 85000), (1, SHOP, "Ground-floor shop", 60000),
                              (1, SHOP, "Ground-floor shop", 55000), (1, SHOP, "Ground-floor shop", 45000), (1, SHOP, "Ground-floor shop", 52000)]),
                 (None, "O", [(1, OFF, "1st-floor office suite", 120000), (1, OFF, "1st-floor office", 70000),
                              (1, OFF, "2nd-floor office", 65000), (1, OFF, "2nd-floor office", 50000), (1, OFF, "3rd-floor office", 58000)])]),
    dict(code="GRN", name="Greenview Gardens", county=County.NAKURU, sub_county="Nakuru Town East", area="Milimani",
         street="Milimani Road", category=Property.Category.APARTMENT_BLOCK, market_value=120_000_000,
         blocks=[(None, "G", [(10, APT, "1 bedroom", 14000), (8, APT, "2 bedroom", 20000), (2, APT, "3 bedroom", 28000)])]),
]
VACANT = ["RVS-A8", "RVS-C6", "KHW-K17", "KHW-K18", "MOI-O4", "MOI-S5", "GRN-G13", "GRN-G14"]
UNDER_MAINTENANCE = ["RVS-B8"]
TURNOVER = ["RVS-A3", "RVS-B2", "KHW-K5", "KHW-K11", "GRN-G4"]  # a tenant left, a new one moved in

REPAIRS = [
    ("Kitchen tap leaking", "PLUMBING", "NORMAL"), ("Blocked bathroom drain", "PLUMBING", "HIGH"),
    ("No power in the bedroom sockets", "ELECTRICAL", "HIGH"), ("Gate lock broken", "SECURITY", "HIGH"),
    ("Water tank not filling", "WATER", "EMERGENCY"), ("Ceiling stain after the rains", "STRUCTURE", "NORMAL"),
    ("Repaint the stairwell", "PAINTING", "LOW"), ("Security light out at the parking", "ELECTRICAL", "NORMAL"),
    ("Front door will not lock", "DOORS", "NORMAL"), ("Cockroaches in the kitchen", "PESTS", "NORMAL"),
    ("Garbage not collected this week", "CLEANING", "HIGH"), ("Window pane cracked", "STRUCTURE", "LOW"),
]
EXPENSES = [  # (category, description, low, high, how often: every N months)
    ("Caretaker and staff wages", "Caretaker wages", 14000, 16000, 1), ("Security", "Night guard company", 18000, 22000, 1),
    ("Cleaning and garbage", "Garbage collection", 4000, 6500, 1), ("Water", "Water bill", 9000, 26000, 1),
    ("Electricity", "KPLC common areas", 3500, 9500, 1), ("Repairs and maintenance", "Repairs and maintenance", 2500, 38000, 2),
    ("Insurance", "Building insurance", 28000, 42000, 6), ("Professional and legal fees", "Accountant fee", 6000, 12000, 3),
]


# (property, label, utility, rate, minimum charge, units it serves, usual use per month, who pays)
WATER, ELEC = Meter.Utility.WATER, Meter.Utility.ELECTRICITY
TENANTS_PAY, LANDLORD_PAYS = Meter.Payer.TENANTS, Meter.Payer.LANDLORD
METERS = (
    [("RVS", f"A{i} electricity", ELEC, 30, 0, [f"RVS-A{i}"], (60, 220), TENANTS_PAY) for i in range(1, 9)]
    + [("RVS", "Block B water (shared)", WATER, 160, 0, [f"RVS-B{i}" for i in range(1, 9)], (70, 110), TENANTS_PAY),
       ("RVS", "Block C water (shared)", WATER, 160, 0, [f"RVS-C{i}" for i in range(1, 7)], (40, 70), TENANTS_PAY),
       ("KHW", "Ground floor water (shared)", WATER, 150, 0, [f"KHW-K{i}" for i in range(1, 11)], (60, 90), TENANTS_PAY),
       ("KHW", "Upper floors water (shared)", WATER, 150, 0, [f"KHW-K{i}" for i in range(11, 23)], (70, 100),
        TENANTS_PAY),
       ("KHW", "Common-area electricity", ELEC, 28, 0, ["KHW-K1"], (300, 500), LANDLORD_PAYS)]
    + [("MOI", f"{code} electricity", ELEC, 32, 500, [f"MOI-{code}"], (150, 600), TENANTS_PAY)
       for code in ("S1", "S2", "S3", "S4", "S5", "O1", "O2", "O3", "O4", "O5")]
    + [("GRN", f"G{i} water", WATER, 150, 300, [f"GRN-G{i}"], (5, 14), TENANTS_PAY) for i in range(1, 21)]
)
# Properties whose latest readings are left waiting for approval, so the approvals page has work in it.
PENDING_PROPERTIES = ("RVS", "KHW")

# (unit, name, phone, source, budget, wants, where they got to, days since the first contact)
PROSPECTS = [
    ("RVS-A8", "Wanjiru Mwita", "+254733000101", Prospect.Source.WHATSAPP, 36000, "2 bedroom near the main road", "reserved", 9),
    ("RVS-C6", "James Mutiso", "+254733000102", Prospect.Source.VACANCY_LINK, 34000, "", "new", 1),
    ("KHW-K17", "Lilian Chepkoech", "+254733000103", Prospect.Source.WALK_IN, 13000, "1 bedroom, ground floor", "contacted", 4),
    ("KHW-K18", "Brian Ochola", "+254733000104", Prospect.Source.PHONE, 12500, "", "viewing", 3),
    ("GRN-G13", "Rehema Said", "+254733000105", Prospect.Source.ONLINE, 20000, "2 bedroom with parking", "viewed", 6),
    ("GRN-G14", "Dennis Kibet", "+254733000106", Prospect.Source.REFERRAL, 21000, "", "new", 0),
    ("GRN-G13", "Mary Wangari", "+254733000107", Prospect.Source.SIGN_BOARD, 19500, "Moving from Nairobi in November", "viewing", 2),
    ("MOI-S5", "Tomas Mbogo", "+254733000108", Prospect.Source.PHONE, 55000, "Shop for a phone accessories business", "contacted", 5),
    ("MOI-O4", "Kelvin Oduor", "+254733000109", Prospect.Source.ONLINE, 52000, "Office for an accounting firm", "reserved", 11),
    ("KHW-K17", "Naomi Cherono", "+254733000110", Prospect.Source.WALK_IN, 10000, "", "lost:PRICE", 21),
    ("RVS-A8", "Isaac Mugo", "+254733000111", Prospect.Source.VACANCY_LINK, 33000, "", "lost:ELSEWHERE", 26),
    ("GRN-G14", "Priscilla Awuor", "+254733000112", Prospect.Source.WHATSAPP, 20000, "", "lost:NO_RESPONSE", 30),
    ("KHW-K18", "Hillary Tanui", "+254733000113", Prospect.Source.PHONE, 12500, "Starting a job at Ruiru in November", "converted", 14),
    ("RVS-C6", "Joan Njeri", "+254733000114", Prospect.Source.REFERRAL, 33000, "", "noshow", 8),
]
# Paybill messages whose account number could not be read: (what was typed, status, why)
ODD_PAYMENTS = [("RVS A5", "UNMATCHED", "Account number did not match a unit."),
                ("A12", "UNMATCHED", "More than one property has a unit A12."),
                ("RENT", "UNMATCHED", "No account number was given."),
                ("KHW-K99", "FLAGGED", "Paid more than three times the monthly rent."),
                ("", "UNMATCHED", "No account number was given.")]


def first_of(day: datetime.date, months_back: int) -> datetime.date:
    month = day.year * 12 + day.month - 1 - months_back
    return datetime.date(month // 12, month % 12 + 1, 1)


def last_day_of(day: datetime.date) -> datetime.date:
    return first_of(day, -1) - DAY


class Command(JobCommand, SeedDemo):
    help = "Build the public 'Try the demo' organization: 74 units in 4 properties, ten months of history."
    job_name = "reset_public_demo"

    def add_arguments(self, parser):
        parser.add_argument("--seed", type=int, default=2026, help="Random seed for names, habits and dates.")

    def run(self, *args, **options):
        self.rng = random.Random(options["seed"])  # noqa: S311 - repeatable demo data, not secrets
        self.today = timezone.localdate()
        self.habits = {}  # lease pk -> how the tenant pays
        old = list(Organization.objects.filter(is_demo=True, archived_at__isnull=True))
        self.org = self._new_organization()
        try:
            self.actor = Membership.objects.select_related("user", "organization").get(
                organization=self.org, role__is_owner_role=True)
            self._load()
        except Exception:
            self._archive([self.org])
            raise
        self._archive(old)
        return (f"Demo organization {self.org.name} (id {self.org.pk}): {Unit.objects.for_org(self.org).count()} units, "
                f"{len(self.lease_specs)} leases.")

    # -- the organization -------------------------------------------------------

    def _new_organization(self) -> Organization:
        user = demo.demo_user()
        membership = account_services.create_organization(user=user, name=demo.DEMO_ORG_NAME,
                                                          org_type=Organization.Type.COMPANY)
        org = membership.organization
        org.is_demo = True
        org.brand_name = "Demo Properties"
        org.late_fee_mode = Organization.LateFeeMode.OFF
        org.save(update_fields=["is_demo", "brand_name", "late_fee_mode", "updated_at"])
        sub = subscription_for(org)
        sub.trial_ends_on = self.today + datetime.timedelta(days=3650)  # never lapses
        sub.save(update_fields=["trial_ends_on", "updated_at"])
        return org

    @staticmethod
    def _archive(orgs) -> None:
        now = timezone.now()
        for org in orgs:
            Organization.objects.filter(pk=org.pk).update(archived_at=now)

    # -- the data ---------------------------------------------------------------

    def _load(self) -> None:
        self.properties_data, self.under_maintenance = self._sites(), UNDER_MAINTENANCE
        self.tenants_data, self.lease_specs = self._people_and_leases()
        self.stdout.write(f"Tenants: {len(self.tenants_data)}, leases: {len(self.lease_specs)}.")
        with transaction.atomic():
            for data in self.tenants_data:
                create_tenant(self.actor, **data)
            self._properties()
            self._accounts()
            self._staff()
            self._bill_and_pay()
            self._tenant_login()
            self._meters()
            self._repairs()
            self._expenses()
            self._prospects()
            self._mpesa()
            self._messages()

    def _pay(self, lease, spec, invoice, earlier):
        self.habits[lease.pk] = spec["habit"]
        super()._pay(lease, spec, invoice, earlier)

    # -- the team ---------------------------------------------------------------

    def _staff(self) -> None:
        """The sample people a visitor can look around as: each has a real role and sees what that role would."""
        roles = {r.based_on_template.key: r for r in Role.objects.filter(organization=self.org).select_related(
            "based_on_template") if r.based_on_template_id}
        props = {p.code: p for p in Property.objects.for_org(self.org)}
        self.staff = {"owner": self.actor}
        for key, name, phone, role_key, codes in demo.STAFF:
            if key == "owner":
                continue
            member = Membership.objects.create(user=demo.demo_user(phone, name), organization=self.org,
                                               role=roles[role_key], all_properties=codes is None)
            for code in codes or ():
                member.property_access.create(property=props[code])
            self.staff[key] = member
        self.stdout.write(f"Team: {len(self.staff)}.")

    def _tenant_login(self) -> None:
        """The sample tenant a visitor can look around as: a portal login on one active lease, named after its tenant."""
        tenant = (Tenant.objects.for_org(self.org).filter(kind=Tenant.Kind.INDIVIDUAL,
                                                           lease_links__lease__status=Lease.Status.ACTIVE)
                  .order_by("pk").first())
        if tenant is None:
            return
        TenantAccount.objects.create(organization=self.org, tenant=tenant, user=demo.demo_user(demo.TENANT_PHONE, tenant.name),
                                     invited_by=self.actor.user)
        self.stdout.write(f"Sample tenant: {tenant.name}.")

    def _who(self, prop, capability, *keys):
        """The first of these team members who may do this at the property (the owner when none may)."""
        for key in keys:
            if can(self.staff[key], capability, prop):
                return self.staff[key]
        return self.actor

    # -- meters -----------------------------------------------------------------

    def _meters(self) -> None:
        latest = self.today.replace(day=25)
        if latest > self.today:
            latest = first_of(self.today, 1).replace(day=25)
        dates = [first_of(latest, back).replace(day=25) for back in range(4, -1, -1)]
        before = Invoice.objects.filter(organization=self.org).order_by("-pk").values_list("pk", flat=True).first() or 0
        made = approved = 0
        for code, label, utility, rate, minimum, refs, use, payer in METERS:
            units = [self.units[ref] for ref in refs]
            prop = units[0].property
            meter = meter_services.create_meter(
                self._who(prop, "meters.manage", "manager"), prop, units=[(u, 1) for u in units], utility=utility,
                label=label, serial=str(self.rng.randint(10**7, 10**8 - 1)), kind=Meter.Kind.POSTPAID,
                split=Meter.Split.EQUAL, rate=rate, minimum_charge=minimum, payer=payer)
            made += 1
            recorder = self._who(prop, "meters.record", "caretaker", "manager")
            approver = self._who(prop, "meters.approve", "manager")
            value = self.rng.randint(1500, 9000)
            readings = []
            for index, day in enumerate(dates):
                spike = label == "A3 electricity" and index == len(dates) - 1  # one reading that looks wrong
                value += self.rng.randint(*use) * (5 if spike else 1)
                readings.append(meter_services.record_reading(recorder, meter, read_on=day, value=value,
                                                              note="Read on the monthly round."))
            waiting = 1 if code in PENDING_PROPERTIES else 0
            for reading in readings[:len(readings) - waiting]:
                try:
                    meter_services.approve(approver, reading, today=self.today)
                    approved += 1
                except ValidationError as exc:
                    self.stdout.write(f"Meter {label}: {exc.messages[0]}")
                    break
        # The invoices that approving made are paid the way the tenant pays rent.
        for invoice in Invoice.objects.filter(organization=self.org, pk__gt=before).select_related("lease"):
            habit = self.habits.get(invoice.lease_id, PROMPT)
            if habit in (PARTIAL, STOPPED) or invoice.total <= 0:
                continue
            paid_at = min(invoice.due_date + datetime.timedelta(days=self.rng.randint(-2, 6 if habit == PROMPT else 14)),
                          self.today)
            record_payment(self.actor, invoice.lease, amount=invoice.total, method=Payment.Method.MPESA,
                           paid_at=max(paid_at, invoice.issue_date or paid_at),
                           reference=self._reference(Payment.Method.MPESA),
                           payment_account=self.accounts[Payment.Method.MPESA])
        self.stdout.write(f"Meters: {made}, readings approved: {approved}.")

    # -- prospects --------------------------------------------------------------

    def _prospects(self) -> None:
        now = timezone.now()
        done = 0
        for ref, name, phone, source, budget, wants, state, days in PROSPECTS:
            unit = self.units[ref]
            prop = unit.property
            actor = self._who(prop, "prospects.manage", "leasing_agent", "manager")
            when = now - datetime.timedelta(days=days, hours=self.rng.randint(0, 6))
            try:
                p = prospect_services.create_prospect(
                    actor, prop, assigned_to=actor.user, name=name, phone=phone, source=source, budget=budget,
                    wants=wants, unit=unit, note="Asked about the vacancy.",
                    move_in_by=self.today + datetime.timedelta(days=self.rng.randint(10, 40)))
                Prospect.objects.filter(pk=p.pk).update(created_at=when)
                if state != "new":
                    prospect_services.add_note(
                        actor, p, text="Called back. Told them the rent, the deposit and what is included.",
                        contacted=True, now=when + datetime.timedelta(hours=3),
                        next_contact_on=self.today + datetime.timedelta(days=1))
                if state in ("viewing", "viewed", "reserved", "converted", "noshow"):
                    upcoming = state == "viewing"
                    at = ((now + datetime.timedelta(days=days % 3 + 1)).replace(hour=10, minute=0, second=0, microsecond=0)
                          if upcoming else when + datetime.timedelta(days=1, hours=2))
                    viewing = prospect_services.book_viewing(actor, p, unit=unit, scheduled_at=at, with_user=actor.user,
                                                             now=when + datetime.timedelta(hours=4))
                    if state == "noshow":
                        prospect_services.viewing_outcome(actor, viewing, Viewing.Status.NO_SHOW)
                    elif not upcoming:
                        prospect_services.viewing_outcome(actor, viewing, Viewing.Status.DONE,
                                                          outcome="Liked the unit. Asked about the deposit.")
                if state == "reserved":
                    prospect_services.hold_unit(actor, p, unit=unit, until=self.today + datetime.timedelta(days=5),
                                                today=self.today)
                elif state == "converted":
                    prospect_services.convert(actor, p, now=now - datetime.timedelta(days=1))
                elif state.startswith("lost:"):
                    prospect_services.lose(actor, p, reason=state.split(":")[1], note="Told us on the phone.",
                                           now=when + datetime.timedelta(days=3))
                done += 1
            except (ValidationError, PermissionDenied) as exc:
                self.stdout.write(f"Prospect {name}: {getattr(exc, 'messages', [str(exc)])[0]}")
        self.stdout.write(f"Prospects: {done}.")

    # -- M-Pesa -----------------------------------------------------------------

    def _mpesa(self) -> None:
        """Each M-Pesa rent payment is shown as the Paybill message that matched it, and a few have not been matched."""
        account = self.accounts[Payment.Method.MPESA]
        tz = timezone.get_current_timezone()
        rows = []
        taken = set(MpesaTransaction.objects.values_list("trans_id", flat=True))  # unique across every organization

        def new_trans_id() -> str:
            while True:
                code = "T" + "".join(self.rng.choice("ABCDEFGHJKLMNPQRSTUVWXYZ0123456789") for _ in range(9))
                if code not in taken:
                    taken.add(code)
                    return code

        for payment in Payment.objects.filter(organization=self.org, method=Payment.Method.MPESA).exclude(
                reference="").select_related("lease__unit", "tenant"):
            at = datetime.datetime.combine(
                payment.paid_at, datetime.time(self.rng.randint(6, 21), self.rng.randint(0, 59)), tzinfo=tz)
            tenant = payment.tenant or payment.lease.primary_tenant
            trans_id = payment.reference if payment.reference not in taken else new_trans_id()
            taken.add(trans_id)
            rows.append(MpesaTransaction(
                organization=self.org, payment_account=account, source=MpesaTransaction.Source.C2B,
                trans_id=trans_id, trans_type="Pay Bill", shortcode=account.number,
                bill_ref=payment.lease.unit.payment_reference, amount=payment.amount, paid_at=at,
                payer_phone=tenant.phone if tenant else "", payer_name=tenant.name.upper() if tenant else "",
                status=MpesaTransaction.Status.MATCHED, payment=payment,
                matched_by=MpesaTransaction.MatchedBy.REFERENCE, matched_at=at, processed_at=at, c2b_confirmed_at=at))
        MpesaTransaction.objects.bulk_create(rows)
        leases = self._active_leases()
        for number, (bill_ref, status, note) in enumerate(ODD_PAYMENTS):
            lease = leases[(number * 7) % len(leases)]
            tenant = lease.primary_tenant
            at = timezone.now() - datetime.timedelta(days=number + 1, hours=self.rng.randint(1, 9))
            MpesaTransaction.objects.create(
                organization=self.org, payment_account=account, source=MpesaTransaction.Source.C2B,
                trans_id=new_trans_id(),
                trans_type="Pay Bill", shortcode=account.number, bill_ref=bill_ref,
                amount=lease.current_rent * 3 if status == "FLAGGED" else lease.current_rent, paid_at=at, payer_phone=tenant.phone,
                payer_name=tenant.name.upper(), status=status, note=note, suggested_lease=lease, processed_at=at,
                c2b_confirmed_at=at)
        self.stdout.write(f"M-Pesa messages: {len(rows) + len(ODD_PAYMENTS)}.")

    # -- messages ---------------------------------------------------------------

    def _active_leases(self) -> list:
        return list(Lease.objects.for_org(self.org).filter(status=Lease.Status.ACTIVE).select_related(
            "unit__property").order_by("pk"))

    def _message(self, when, **fields) -> None:
        message = Message.objects.create(organization=self.org, **fields)
        Message.objects.filter(pk=message.pk).update(created_at=when)

    def _messages(self) -> None:
        """The alerts in each person's inbox and what tenants were sent. All of it is history: nothing is queued
        (`send_due_messages` would pick up a queued row), and `notify` sends nothing for the demo."""
        now = timezone.now()
        status = Message.Status
        unmatched = list(MpesaTransaction.objects.filter(organization=self.org).exclude(
            status=MpesaTransaction.Status.MATCHED).select_related("payment_account").order_by("-paid_at"))
        repairs = list(MaintenanceRequest.objects.filter(organization=self.org).select_related(
            "property", "unit").order_by("-created_at")[:5])

        def inbox(key, type_, text, hours_ago, read):
            when = now - datetime.timedelta(hours=hours_ago)
            self._message(when, type=type_, channel=catalog.IN_APP, user=self.staff[key].user, body=text,
                          status=status.DELIVERED, sent_at=when, delivered_at=when,
                          read_at=when + datetime.timedelta(hours=2) if read else None)

        for tx in unmatched:
            text = (f"{format_money(tx.amount)} from {tx.payer_name.title()} (reference “{tx.bill_ref or 'none'}”) on "
                    f"{tx.payment_account.display_name} could not be matched to a unit. Match it in the M-Pesa inbox.")
            hours = (now - tx.paid_at).total_seconds() / 3600
            inbox("owner", "mpesa_unmatched", text, hours, False)
            inbox("accountant", "mpesa_unmatched", text, hours, True)
        for back in range(1, 7):
            day = self.today - datetime.timedelta(days=back)
            paid = list(Payment.objects.filter(organization=self.org, method=Payment.Method.MPESA, paid_at=day))
            if paid:
                inbox("owner", "mpesa_daily_summary", f"M-Pesa on {day:%d %b}. {len(paid)} payments, "
                      f"{format_money(sum(p.amount for p in paid))} received.", back * 24 - 15, back > 2)
        for number, req in enumerate(repairs):
            text = (f"{req.number} ({req.get_priority_display()}) at {req.property.name}"
                    f"{', ' + req.unit.code if req.unit_id else ''}: {req.title}. Reported by the caretaker.")
            inbox("manager", "maintenance_reported", text, 6 + number * 20, number > 1)
            if number < 2:
                inbox("caretaker", "maintenance_reported", text, 6 + number * 20, number > 0)
        for p in Prospect.objects.filter(organization=self.org).exclude(status=Prospect.Status.LOST).select_related(
                "property", "unit")[:6]:
            key = "leasing_agent" if p.property.code in ("KHW", "GRN") else "manager"
            inbox(key, "prospect_new",
                  f"{p.name} ({p.phone}) asked about {p.property.name}, {p.unit.code if p.unit_id else 'a unit'} "
                  f"({p.get_source_display()}). Call them back soon.",
                  (now - p.created_at).total_seconds() / 3600, p.status != Prospect.Status.NEW)

        # What tenants were sent: the latest invoice of some leases, then a receipt or a reminder.
        tz, sent = timezone.get_current_timezone(), 0
        for lease in self._active_leases()[:46]:
            tenant = lease.primary_tenant
            invoice = (Invoice.objects.filter(lease=lease).exclude(status__in=(Invoice.Status.DRAFT, Invoice.Status.VOID))
                       .order_by("-due_date").first())
            if invoice is None or tenant is None:
                continue
            first = tenant.name.split()[0]
            when = datetime.datetime.combine(invoice.issue_date or self.today, datetime.time(8, 0), tzinfo=tz)
            base = dict(tenant=tenant, lease=lease, status=status.DELIVERED, provider="demo",
                        language=tenant.language or catalog.EN, sent_at=when, delivered_at=when)
            self._message(when, type="invoice_issued", channel=catalog.WHATSAPP if sent % 3 == 0 else catalog.SMS,
                          to=tenant.phone, invoice=invoice,
                          body=f"Hello {first}, rent invoice {invoice.number} of {format_money(invoice.total)} for "
                               f"{lease.unit.code}, {lease.unit.property.name} is due on {invoice.due_date:%d %b}. "
                               f"Pay via Paybill 4001234, account {lease.unit.payment_reference}.", **base)
            sent += 1
            if tenant.email:
                self._message(when, type="invoice_issued", channel=catalog.EMAIL, to=tenant.email, invoice=invoice,
                              subject=f"Invoice {invoice.number} from Demo Properties",
                              body=f"Dear {tenant.name}, invoice {invoice.number} for {lease.unit.code} is attached.",
                              **base)
            if self.habits.get(lease.pk, PROMPT) != PROMPT:
                late = when + datetime.timedelta(days=9)
                self._message(late, type="rent_overdue", channel=catalog.SMS, to=tenant.phone, invoice=invoice,
                              body=f"Hello {first}, rent for {lease.unit.code} of {format_money(invoice.total)} was due on "
                                   f"{invoice.due_date:%d %b} and is not paid yet. Please pay via Paybill 4001234, "
                                   f"account {lease.unit.payment_reference}.",
                              **{**base, "sent_at": late, "delivered_at": late})
            else:
                paid = Payment.objects.filter(lease=lease, status=Payment.Status.CONFIRMED).order_by("-paid_at").first()
                if paid is not None:
                    at = datetime.datetime.combine(paid.paid_at, datetime.time(12, 0), tzinfo=tz)
                    self._message(at, type="payment_received", channel=catalog.SMS, to=tenant.phone, payment=paid,
                                  body=f"Hello {first}, we received {format_money(paid.amount)} for {lease.unit.code} on "
                                       f"{paid.paid_at:%d %b}. Thank you. Demo Properties.",
                                  **{**base, "sent_at": at, "delivered_at": at})
        # One that did not go through, so the log shows what a failure looks like.
        tenant = self._active_leases()[0].primary_tenant
        self._message(now - datetime.timedelta(days=2), type="announcement", channel=catalog.SMS, to=tenant.phone,
                      tenant=tenant, body="Water will be off on Saturday from 9am to 1pm for tank cleaning.",
                      status=status.FAILED, attempts=Message.MAX_ATTEMPTS, error="The number could not be reached.",
                      provider="demo")
        self.stdout.write(f"Messages: {Message.objects.filter(organization=self.org).count()}.")

    def _sites(self) -> list[dict]:
        sites = []
        for site in SITES:
            units, buildings = [], []
            for building, prefix, kinds in site["blocks"]:
                if building and building not in buildings:
                    buildings.append(building)
                number = 0
                for count, unit_type, label, rent in kinds:
                    for _ in range(count):
                        number += 1
                        units.append((f"{prefix}{number}", building, unit_type, label, rent))
            fields = {k: v for k, v in site.items() if k != "blocks"}
            sites.append({**fields, "buildings": buildings, "units": units})
        return sites

    def _people_and_leases(self) -> tuple[list[dict], list[dict]]:
        names = [f"{a} {b}" for a in FIRST for b in LAST if a != b]
        self.rng.shuffle(names)
        companies = list(COMPANIES)
        tenants, leases, phone = [], [], 0

        def person(company=None) -> str:
            nonlocal phone
            phone += 1
            if company:
                name, contact = company
                data = dict(kind=Tenant.Kind.COMPANY, name=name, contact_person=contact,
                            id_type=Tenant.IdType.COMPANY_REG, id_number=f"PVT-{self.rng.randint(10**6, 10**7 - 1)}",
                            kra_pin=f"P{self.rng.randint(10**8, 10**9 - 1)}A",
                            email=f"info@{name.split()[0].lower()}.example.com")
            else:
                name = names.pop()
                data = dict(kind=Tenant.Kind.INDIVIDUAL, name=name, id_type=Tenant.IdType.NATIONAL_ID,
                            id_number=str(self.rng.randint(20_000_000, 39_999_999)),
                            emergency_contact_name=f"{self.rng.choice(FIRST)} {name.split()[1]}",
                            emergency_contact_phone=f"+2547220{phone:05d}")
                if self.rng.random() < 0.45:
                    data["email"] = f"{name.lower().replace(' ', '.')}@example.com"
                if self.rng.random() < 0.2:
                    data["language"] = "sw"
            data["phone"] = f"+2547110{phone:05d}"
            tenants.append(data)
            return data["name"]

        for site in self._sites():
            commercial = site["category"] == Property.Category.COMMERCIAL
            for code, _building, _type, _label, list_rent in site["units"]:
                ref = f"{site['code']}-{code}"
                if ref in VACANT or ref in UNDER_MAINTENANCE:
                    continue
                if commercial:
                    first_tenants = lambda: [person(companies.pop(0))]  # noqa: E731
                else:
                    first_tenants = lambda: [person()]  # noqa: E731
                rent = list_rent - (0 if commercial else self.rng.choice([0, 0, 0, 500, 1000]))
                charges = ({"SERVICE_CHARGE": round(rent * 0.06 / 500) * 500} if commercial else
                           {"GARBAGE": 500, **({"WATER": self.rng.choice([800, 1000])}
                                               if site["code"] in ("RVS", "GRN") and self.rng.random() < 0.6 else {})})
                base = dict(unit=ref, rent=rent, deposit=rent * (2 if commercial else 1), charges=charges)
                if ref in TURNOVER:
                    left = self.rng.randint(4, 6)  # months ago the first tenant moved out
                    leases.append({**base, "tenants": first_tenants(), "start": first_of(self.today, 10),
                                   "habit": PROMPT, "ends": last_day_of(first_of(self.today, left))})
                    leases.append({**base, "tenants": first_tenants(), "start": first_of(self.today, left - 1),
                                   "rent": rent + 1000, "habit": self.rng.choice(HABITS)})
                else:
                    leases.append({**base, "tenants": first_tenants(),
                                   "start": first_of(self.today, self.rng.randint(1, 10)),
                                   "habit": self.rng.choice(HABITS)})
        leases[0]["payer"] = "+254722000999"  # one tenant's rent is paid by someone else (an employer)
        return tenants, leases

    def _repairs(self) -> None:
        units = list(self.units.values())
        done = 0
        for i, (title, kind, priority) in enumerate(REPAIRS):
            unit = self.rng.choice(units)
            when = timezone.now() - datetime.timedelta(days=self.rng.randint(2, 70) if i < 9 else self.rng.randint(1, 5))
            req = repair_services.report(self.actor, unit.property, title=title, kind=kind, priority=priority,
                                         unit=unit, now=when)
            steps = ["start", "done", "close"][: self.rng.choice([0, 1, 1, 2, 3, 3, 3])]
            for step in steps:
                when += datetime.timedelta(days=self.rng.randint(1, 4))
                if when > timezone.now():
                    break
                try:
                    req = repair_services.act(self.actor, req, step, now=when)
                except Exception:  # noqa: BLE001 - a step the status will not allow just leaves it where it is
                    break
            done += 1
        self.stdout.write(f"Repairs: {done}.")

    def _expenses(self) -> None:
        categories = {c.name: c for c in expense_services.categories(self.org)}
        count = 0
        for prop in Property.objects.for_org(self.org):
            for category, description, low, high, every in EXPENSES:
                if category == "Water" and prop.category == Property.Category.COMMERCIAL:
                    continue
                for back in range(0, 10):
                    if back % every:
                        continue
                    paid_on = first_of(self.today, back) + datetime.timedelta(days=self.rng.randint(2, 24))
                    if paid_on > self.today:
                        continue
                    expense_services.record_expense(
                        self.actor, prop, category=categories[category], description=description,
                        amount=Decimal(self.rng.randrange(low, high + 1, 100)), paid_on=paid_on,
                        method=self.rng.choice([Expense.Method.MPESA, Expense.Method.BANK, Expense.Method.CASH]),
                        reference=f"EX{self.rng.randint(10**7, 10**8 - 1)}", is_recurring=every == 1,
                        today=self.today)
                    count += 1
        self.stdout.write(f"Expenses: {count}.")
