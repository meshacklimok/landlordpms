"""Fill a development organization with demo data: tenants, properties, units, leases,
deposits, invoices and payments, so every page has something to show.

Everything goes through the real services as the organization's owner, so it is validated
and audited like data entered in the app. Invoices and payments are back-dated from the
earliest lease start to today, with a mix of prompt, late, partial and stopped payers.

Safe to re-run: tenants already present are skipped, and the property step is skipped once
the demo properties exist. Refuses to run with DEBUG off or with a real SMS/WhatsApp backend,
because confirming a payment queues a receipt message; those receipts are marked skipped.
"""

import datetime
import random
from decimal import ROUND_DOWN, Decimal

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from accounts.models import Membership, Organization
from billing import deposits
from billing.invoicing import generate_lease_period, month_start, months_to_bill, next_month
from billing.models import ChargeType, Invoice
from billing.services import ensure_default_charge_types
from core.kenya import County
from leases.models import Lease
from leases.services import activate_lease, add_charge, add_payer, create_lease, end_lease
from notifications.models import Message
from payments.models import Payment, PaymentAccount, PropertyPaymentAccount
from payments.services import record_payment
from properties.models import Property, Unit
from properties.services import create_building, create_property, create_unit, set_unit_status, update_unit
from tenants.models import Tenant
from tenants.services import create_tenant

D = datetime.date
DAY = datetime.timedelta(days=1)
SAFE_SMS_BACKENDS = ("core.sms.ConsoleSmsSender", "core.sms.MemorySmsSender")

IND, CO = Tenant.Kind.INDIVIDUAL, Tenant.Kind.COMPANY
NID, PASS, ALIEN, REG = (Tenant.IdType.NATIONAL_ID, Tenant.IdType.PASSPORT, Tenant.IdType.ALIEN_ID,
                         Tenant.IdType.COMPANY_REG)

TENANTS = [
    dict(kind=IND, name="Wanjiku Kamau", phone="+254711000101", email="wanjiku.kamau@example.com",
         id_type=NID, id_number="28451937", kra_pin="A004512378K",
         emergency_contact_name="Peter Kamau", emergency_contact_phone="+254722000101"),
    dict(kind=IND, name="Otieno Odhiambo", phone="+254711000102", alt_phone="+254733000102",
         email="otieno.odhiambo@example.com", id_type=NID, id_number="31207845", kra_pin="A007834512M",
         emergency_contact_name="Akinyi Odhiambo", emergency_contact_phone="+254722000102"),
    dict(kind=IND, name="Achieng Atieno", phone="+254711000103", language="sw",
         id_type=NID, id_number="33918264", emergency_contact_name="Mary Atieno",
         emergency_contact_phone="+254722000103"),
    dict(kind=IND, name="Kiprotich Kiplagat", phone="+254711000104", email="kiprotich.k@example.com",
         id_type=NID, id_number="27654390", kra_pin="A003298765L",
         emergency_contact_name="Chebet Kiplagat", emergency_contact_phone="+254722000104"),
    dict(kind=IND, name="Mwangi Njoroge", phone="+254711000105", language="sw",
         id_type=NID, id_number="29873015", kra_pin="A005673412P",
         emergency_contact_name="Grace Njoroge", emergency_contact_phone="+254722000105",
         notes="Prefers calls after 6pm."),
    dict(kind=IND, name="Fatuma Hassan", phone="+254711000106", email="fatuma.hassan@example.com",
         id_type=NID, id_number="34520871", emergency_contact_name="Ali Hassan",
         emergency_contact_phone="+254722000106"),
    dict(kind=IND, name="Mutua Musyoka", phone="+254711000107", alt_phone="+254733000107",
         id_type=NID, id_number="26789123", kra_pin="A002187654Q",
         emergency_contact_name="Ndinda Musyoka", emergency_contact_phone="+254722000107"),
    dict(kind=IND, name="Chebet Rotich", phone="+254711000108", email="chebet.rotich@example.com",
         id_type=NID, id_number="35012467", emergency_contact_name="Kibet Rotich",
         emergency_contact_phone="+254722000108"),
    dict(kind=IND, name="Nekesa Wafula", phone="+254711000109", language="sw",
         id_type=NID, id_number="30456782", emergency_contact_name="Simiyu Wafula",
         emergency_contact_phone="+254722000109"),
    dict(kind=IND, name="Daniel Omondi", phone="+254711000110", email="daniel.omondi@example.com",
         id_type=NID, id_number="32698104", kra_pin="A006745123R",
         emergency_contact_name="Lucy Omondi", emergency_contact_phone="+254722000110"),
    dict(kind=IND, name="Aisha Mohamed", phone="+254711000111", email="aisha.mohamed@example.com",
         id_type=PASS, id_number="AK0493817", emergency_contact_name="Yusuf Mohamed",
         emergency_contact_phone="+254722000111"),
    dict(kind=IND, name="Joseph Mugisha", phone="+254711000112", email="j.mugisha@example.com",
         id_type=ALIEN, id_number="100234567", emergency_contact_name="Ruth Mugisha",
         emergency_contact_phone="+254722000112", notes="Ugandan national; work permit on file."),
    dict(kind=CO, name="Savannah Traders Ltd", contact_person="Grace Wambui", phone="+254711000113",
         alt_phone="+254733000113", email="accounts@savannahtraders.example.com",
         id_type=REG, id_number="PVT-7LU2XQ8", kra_pin="P051234567A",
         notes="Ground-floor shop. Invoices to accounts email."),
    dict(kind=CO, name="Jiko Bora Kitchens Ltd", contact_person="Samuel Kariuki", phone="+254711000114",
         email="info@jikobora.example.com", id_type=REG, id_number="PVT-AAB9K21", kra_pin="P052345678B"),
    dict(kind=CO, name="Upendo Medical Clinic", contact_person="Dr. Esther Njeri", phone="+254711000115",
         alt_phone="+254733000115", email="admin@upendoclinic.example.com",
         id_type=REG, id_number="BN-3X7PQ4R", kra_pin="P053456789C"),
]

APT, BED, STU, SHOP, OFF = Unit.Type.APARTMENT, Unit.Type.BEDSITTER, Unit.Type.STUDIO, Unit.Type.SHOP, Unit.Type.OFFICE

# Units as (code, building, type, label, list rent). A single house gets its MAIN unit automatically.
PROPERTIES = [
    dict(code="RVS", name="Riverside Apartments", category=Property.Category.APARTMENT_BLOCK,
         county=County.NAIROBI, sub_county="Dagoretti North", area="Kilimani", street="Riverside Drive",
         buildings=["Block A", "Block B"], units=[
             ("A1", "Block A", APT, "2 bedroom", 35000), ("A2", "Block A", APT, "2 bedroom", 35000),
             ("A3", "Block A", APT, "2 bedroom", 35000), ("A4", "Block A", APT, "3 bedroom", 42000),
             ("B1", "Block B", APT, "1 bedroom", 26000), ("B2", "Block B", APT, "1 bedroom", 26000),
             ("B3", "Block B", STU, "Studio", 18000), ("B4", "Block B", APT, "3 bedroom", 45000),
         ]),
    dict(code="KHW", name="Kahawa Court", category=Property.Category.APARTMENT_BLOCK,
         county=County.KIAMBU, sub_county="Ruiru", area="Kahawa Wendani", street="Off Thika Road",
         buildings=[], units=[
             ("K1", None, BED, "Bedsitter", 9500), ("K2", None, APT, "1 bedroom", 12500),
             ("K3", None, BED, "Bedsitter", 9500), ("K4", None, APT, "1 bedroom", 12500),
             ("K5", None, BED, "Bedsitter", 9500), ("K6", None, APT, "1 bedroom", 13000),
         ]),
    dict(code="MOI", name="Moi Avenue Plaza", category=Property.Category.COMMERCIAL,
         county=County.NAIROBI, sub_county="Starehe", area="CBD", street="Moi Avenue",
         buildings=[], units=[
             ("S1", None, SHOP, "Ground-floor shop", 85000), ("S2", None, SHOP, "Ground-floor shop", 60000),
             ("O1", None, OFF, "1st-floor office suite", 120000), ("O2", None, OFF, "1st-floor office", 70000),
         ]),
    dict(code="KRN", name="Karen Villa", category=Property.Category.SINGLE_HOUSE,
         county=County.NAIROBI, sub_county="Langata", area="Karen", street="Miotoni Road",
         buildings=[], units=[], main_house_rent=150000),
]

# Taken off the market; the other empty units are simply vacant.
UNDER_MAINTENANCE = ["RVS-B3"]

PROMPT, LATE, PARTIAL, STOPPED = "prompt", "late", "partial", "stopped"

# Recurring charges are {charge category: monthly amount}. `ends` makes a past tenant.
LEASES = [
    dict(unit="RVS-A1", tenants=["Wanjiku Kamau"], start=D(2026, 4, 1), rent=35000, deposit=35000,
         habit=PROMPT, charges={"GARBAGE": 500, "WATER": 800}),
    dict(unit="RVS-A2", tenants=["Otieno Odhiambo"], start=D(2026, 5, 1), rent=35000, deposit=35000,
         habit=LATE, charges={"GARBAGE": 500, "WATER": 800}),
    dict(unit="RVS-A4", tenants=["Kiprotich Kiplagat"], start=D(2026, 4, 1), rent=42000, deposit=42000,
         habit=PROMPT, charges={"GARBAGE": 500, "WATER": 1000}),
    dict(unit="RVS-B1", tenants=["Mutua Musyoka"], start=D(2026, 5, 1), rent=26000, deposit=26000,
         habit=PARTIAL, charges={"GARBAGE": 500}),
    dict(unit="RVS-B2", tenants=["Chebet Rotich"], start=D(2026, 6, 15), rent=26000, deposit=26000,
         habit=PROMPT, charges={"GARBAGE": 500}),
    dict(unit="RVS-B4", tenants=["Daniel Omondi"], start=D(2026, 4, 1), rent=45000, deposit=45000,
         habit=PROMPT, charges={"GARBAGE": 500, "WATER": 1000}, payer="+254722000110"),
    dict(unit="KHW-K1", tenants=["Achieng Atieno"], start=D(2026, 4, 1), rent=9500, deposit=9500, habit=PROMPT),
    dict(unit="KHW-K2", tenants=["Mwangi Njoroge"], start=D(2026, 4, 1), rent=12500, deposit=12500,
         habit=STOPPED),
    dict(unit="KHW-K4", tenants=["Fatuma Hassan"], start=D(2026, 4, 1), rent=12500, deposit=12500,
         habit=PROMPT, ends=D(2026, 7, 31)),
    dict(unit="KHW-K4", tenants=["Aisha Mohamed"], start=D(2026, 8, 1), rent=13000, deposit=13000, habit=PROMPT),
    dict(unit="MOI-S1", tenants=["Savannah Traders Ltd"], start=D(2026, 4, 1), rent=85000, deposit=170000,
         habit=PROMPT, charges={"SERVICE_CHARGE": 5000}),
    dict(unit="MOI-S2", tenants=["Jiko Bora Kitchens Ltd"], start=D(2026, 5, 1), rent=60000, deposit=120000,
         habit=LATE, charges={"SERVICE_CHARGE": 4000}),
    dict(unit="MOI-O1", tenants=["Upendo Medical Clinic"], start=D(2026, 7, 1), rent=120000, deposit=240000,
         habit=PROMPT, charges={"SERVICE_CHARGE": 7000}),
    dict(unit="KRN-MAIN", tenants=["Joseph Mugisha"], start=D(2026, 4, 1), rent=150000, deposit=300000,
         habit=PROMPT),
]


class Command(BaseCommand):
    help = "Fill an organization with demo tenants, properties, leases, invoices and payments (development only)."

    def add_arguments(self, parser):
        parser.add_argument("--org", help="Organization id or exact name. Defaults to the only organization.")
        parser.add_argument("--seed", type=int, default=2026, help="Random seed for payment dates and references.")

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("Refusing to seed demo data with DEBUG off.")
        if settings.SMS_BACKEND not in SAFE_SMS_BACKENDS or getattr(settings, "WHATSAPP_BACKEND", ""):
            raise CommandError("Refusing to seed with a real SMS or WhatsApp backend: payment receipts would be sent.")
        self.org = self._organization(options["org"])
        self.actor = (Membership.objects.filter(organization=self.org, is_active=True, role__is_owner_role=True)
                      .select_related("user", "organization").first())
        if self.actor is None:
            raise CommandError(f"{self.org.name} has no active owner to create data as.")
        self.rng = random.Random(options["seed"])
        self.today = timezone.localdate()

        self._tenants()
        if Property.all_objects.for_org(self.org).filter(code__in=[p["code"] for p in PROPERTIES]).exists():
            self.stdout.write("Demo properties already exist; skipping properties, leases and payments.")
            return
        started = timezone.now()
        with transaction.atomic():
            self._properties()
            self._accounts()
            self._bill_and_pay()
        # Receipts for back-dated demo payments must never reach a real phone later.
        Message.objects.filter(organization=self.org, status=Message.Status.QUEUED, created_at__gte=started).update(
            status=Message.Status.SKIPPED, skip_reason=Message.SkipReason.CHANNEL_UNAVAILABLE,
            error="Demo data: not sent")
        self._summary()

    # -- steps ----------------------------------------------------------------

    def _tenants(self):
        existing = set(Tenant.all_objects.for_org(self.org).values_list("name", flat=True))
        created = 0
        for data in TENANTS:
            if data["name"] not in existing:
                create_tenant(self.actor, **data)
                created += 1
        self.stdout.write(f"Tenants: created {created}, skipped {len(TENANTS) - created}.")

    def _properties(self):
        self.units = {}
        for spec in PROPERTIES:
            fields = {k: v for k, v in spec.items() if k not in ("buildings", "units", "main_house_rent")}
            prop = create_property(self.actor, **fields)
            buildings = {name: create_building(self.actor, prop, name=name) for name in spec["buildings"]}
            for code, building, unit_type, label, rent in spec["units"]:
                unit = create_unit(self.actor, prop, code=code, building=buildings.get(building),
                                   unit_type=unit_type, type_label=label, list_rent=Decimal(rent))
                self.units[unit.payment_reference] = unit
            if "main_house_rent" in spec:
                unit = update_unit(self.actor, prop.units.get(), list_rent=Decimal(spec["main_house_rent"]),
                                   type_label="4 bedroom house")
                self.units[unit.payment_reference] = unit
        for ref in UNDER_MAINTENANCE:
            set_unit_status(self.actor, self.units[ref], Unit.ManualStatus.UNDER_MAINTENANCE)
        self.stdout.write(f"Properties: {len(PROPERTIES)}, units: {len(self.units)}.")

    def _accounts(self):
        paybill = PaymentAccount.objects.create(organization=self.org, type=PaymentAccount.Type.PAYBILL,
                                                number="4001234", display_name="Rent Paybill 4001234")
        bank = PaymentAccount.objects.create(organization=self.org, type=PaymentAccount.Type.BANK,
                                             number="0110-2233445566", display_name="Equity Bank (rent)")
        for prop in Property.objects.for_org(self.org):
            default = bank if prop.category == Property.Category.COMMERCIAL else paybill
            for account in (paybill, bank):
                PropertyPaymentAccount.objects.create(organization=self.org, property=prop, payment_account=account,
                                                      is_default=account == default)
        self.accounts = {Payment.Method.MPESA: paybill, Payment.Method.BANK: bank}

    def _leases(self, specs):
        ensure_default_charge_types(self.org)
        charge_types = {ct.category: ct for ct in ChargeType.objects.for_org(self.org).filter(is_system=True)}
        tenants = {t.name: t for t in Tenant.objects.for_org(self.org)}
        leases = []
        for spec in specs:
            start = spec["start"]
            end = start.replace(year=start.year + 1) - DAY
            lease = create_lease(self.actor, unit=self.units[spec["unit"]],
                                 tenants=[tenants[n] for n in spec["tenants"]],
                                 start_date=start, end_date=end,
                                 rent=spec["rent"], deposit_amount=spec["deposit"], due_day=5, grace_days=3)
            for category, amount in spec.get("charges", {}).items():
                add_charge(self.actor, lease, charge_type=charge_types[category], amount=amount)
            if spec.get("payer"):
                add_payer(self.actor, lease, phone=spec["payer"], name="Employer (salary deduction)")
            activate_lease(self.actor, lease)
            deposits.record_received(self.actor, lease, amount=spec["deposit"], entry_date=start,
                                     reference=self._reference(Payment.Method.MPESA))
            leases.append((lease, spec))
        return leases

    def _bill_and_pay(self):
        """Bills month by month from the first lease start and pays each invoice by the tenant's habit.

        A lease on a unit that someone moves out of is only created once that tenant has left.
        """
        moved_out = {spec["unit"] for spec in LEASES if spec.get("ends")}
        later = [s for s in LEASES if s["unit"] in moved_out and not s.get("ends")]
        leases = self._leases([s for s in LEASES if s not in later])
        lead = datetime.timedelta(days=self.org.invoice_lead_days)
        month = month_start(min(spec["start"] for spec in LEASES))
        last = months_to_bill(self.org, self.today)[-1]
        invoices_paid = {}
        while month <= last:
            leases += self._leases([s for s in later if month_start(s["start"]) == month])
            for lease, spec in leases:
                lease.refresh_from_db()
                issue_day = min(max(month, spec["start"]) - lead, self.today)
                invoice = generate_lease_period(lease, month, actor=self.actor, today=issue_day)
                if invoice is None:
                    continue
                self._pay(lease, spec, invoice, invoices_paid.get(lease.pk, 0))
                invoices_paid[lease.pk] = invoices_paid.get(lease.pk, 0) + 1
                if spec.get("ends") and month_start(spec["ends"]) == month:
                    end_lease(self.actor, lease, ended_on=spec["ends"], reason="Moved out at the end of the month")
            month = next_month(month)
        self.stdout.write(f"Leases: {len(leases)}, with deposits. Billed and paid through {last:%B %Y}.")

    def _pay(self, lease, spec, invoice, earlier):
        habit = spec["habit"]
        if habit == STOPPED and earlier >= 2:
            return
        amount = invoice.total
        if habit == PARTIAL:
            amount = (amount * Decimal("0.007")).quantize(Decimal(1), ROUND_DOWN) * 100
        if habit == LATE:
            paid_at = invoice.due_date + datetime.timedelta(days=self.rng.randint(8, 25))
        else:
            paid_at = invoice.due_date - datetime.timedelta(days=self.rng.randint(0, 4))
        paid_at = max(paid_at, invoice.issue_date)
        if paid_at > self.today:
            return  # not paid yet
        method = Payment.Method.BANK if lease.primary_tenant.kind == Tenant.Kind.COMPANY else Payment.Method.MPESA
        record_payment(self.actor, lease, amount=amount, method=method, paid_at=paid_at,
                       reference=self._reference(method), payment_account=self.accounts[method])

    # -- helpers --------------------------------------------------------------

    def _reference(self, method):
        if method == Payment.Method.BANK:
            return f"FT{self.rng.randint(10**9, 10**10 - 1)}"
        return "SK" + "".join(self.rng.choice("ABCDEFGHJKLMNPQRSTUVWXYZ0123456789") for _ in range(8))

    def _summary(self):
        active = Lease.objects.for_org(self.org).filter(status=Lease.Status.ACTIVE).count()
        self.stdout.write(self.style.SUCCESS(
            f"Done: {Invoice.objects.filter(organization=self.org).count()} invoices, "
            f"{Payment.objects.filter(organization=self.org).count()} payments, {active} active leases."))

    def _organization(self, ref):
        orgs = Organization.objects.all()
        if ref:
            org = orgs.filter(pk=ref).first() if ref.isdigit() else orgs.filter(name=ref).first()
            if org is None:
                raise CommandError(f"No organization {ref!r}.")
            return org
        if orgs.count() != 1:
            raise CommandError("Several organizations exist; pass --org.")
        return orgs.get()
