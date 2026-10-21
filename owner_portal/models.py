"""Owner portal accounts and invitations (D-128).

A PropertyOwner is a record, not a login. An OwnerAccount links one login to one owner record so the owner
can see their own statements and payments; like the tenant portal (D-055), revoking keeps the row.
"""

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.models import PublicIdModel, ScopedQuerySet, TimeStampedModel


class OwnerAccount(PublicIdModel, TimeStampedModel):
    """Lets a login see one owner record's statements, payments and properties."""

    organization = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT, related_name="+")
    owner = models.ForeignKey("properties.PropertyOwner", on_delete=models.PROTECT, related_name="portal_accounts")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="owner_accounts")
    invited_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                   related_name="+")
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                   related_name="+")
    # A person the owner chose to share the portal with (D-217): sees the same figures, changes nothing.
    is_delegate = models.BooleanField(default=False)
    delegate_name = models.CharField(max_length=120, blank=True)

    objects = ScopedQuerySet.as_manager()

    class Meta:
        verbose_name = _("owner portal account")
        constraints = [
            models.UniqueConstraint("owner", condition=Q(revoked_at__isnull=True, is_delegate=False),
                                    name="owner_portal_account_one_live_per_owner"),
            models.UniqueConstraint("owner", "user", condition=Q(revoked_at__isnull=True),
                                    name="owner_portal_account_one_live_per_user"),
        ]

    def __str__(self):
        return f"{self.user_id} → owner {self.owner_id}"

    @property
    def is_live(self) -> bool:
        return self.revoked_at is None


class OwnerInvitation(PublicIdModel, TimeStampedModel):
    """A link sent by SMS and/or email. Claiming it needs a login with the invited, verified phone.
    Only the token's hash is kept."""

    organization = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT, related_name="+")
    owner = models.ForeignKey("properties.PropertyOwner", on_delete=models.PROTECT, related_name="portal_invitations")
    phone = models.CharField(max_length=16)
    email = models.EmailField(blank=True)
    sent_by_sms = models.BooleanField(default=False)
    sent_by_email = models.BooleanField(default=False)
    # Sent by the owner to a person they chose, to their own phone, not the owner's (D-217).
    is_delegate = models.BooleanField(default=False)
    delegate_name = models.CharField(max_length=120, blank=True)
    token_hash = models.CharField(max_length=64, unique=True)
    invited_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True, blank=True)
    accepted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                    related_name="+")
    revoked_at = models.DateTimeField(null=True, blank=True)

    objects = ScopedQuerySet.as_manager()

    class Meta:
        verbose_name = _("owner portal invitation")
        indexes = [models.Index(fields=["owner", "created_at"])]

    def __str__(self):
        return f"Owner portal invite {self.phone}"

    @property
    def is_pending(self) -> bool:
        return self.accepted_at is None and self.revoked_at is None and self.expires_at > timezone.now()


class OwnerMessage(PublicIdModel):
    """One note in the thread between a property owner and the office (D-217). Each owner has one thread."""

    class Side(models.TextChoices):
        OWNER = "OWNER", _("Owner")
        STAFF = "STAFF", _("Office")

    MAX_LENGTH = 1000

    organization = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT, related_name="+")
    owner = models.ForeignKey("properties.PropertyOwner", on_delete=models.PROTECT, related_name="messages")
    side = models.CharField(max_length=5, choices=Side.choices)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    body = models.TextField(max_length=MAX_LENGTH)
    # When the other side first opened it.
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    objects = ScopedQuerySet.as_manager()

    class Meta:
        ordering = ["created_at", "pk"]
        indexes = [models.Index(fields=["owner", "side", "read_at"])]

    def __str__(self):
        return f"{self.side} · {self.body[:40]}"


class OwnerApproval(PublicIdModel, TimeStampedModel):
    """The office asks the owner to approve a repair or an expense and the owner answers in the portal (D-217).

    The answer is a record, not an action: it changes nothing on the repair or expense. The office reads it and
    approves or rejects the expense in the usual way.
    """

    class Kind(models.TextChoices):
        REPAIR = "REPAIR", _("Repair")
        EXPENSE = "EXPENSE", _("Expense")

    class Status(models.TextChoices):
        ASKED = "ASKED", _("Waiting for the owner")
        APPROVED = "APPROVED", _("Approved")
        DECLINED = "DECLINED", _("Declined")
        WITHDRAWN = "WITHDRAWN", _("Withdrawn")

    organization = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT, related_name="+")
    owner = models.ForeignKey("properties.PropertyOwner", on_delete=models.PROTECT, related_name="approvals")
    property = models.ForeignKey("properties.Property", on_delete=models.PROTECT, related_name="+")
    kind = models.CharField(max_length=7, choices=Kind.choices)
    maintenance_request = models.ForeignKey("maintenance.MaintenanceRequest", on_delete=models.PROTECT, null=True,
                                            blank=True, related_name="+")
    expense = models.ForeignKey("expenses.Expense", on_delete=models.PROTECT, null=True, blank=True,
                                related_name="+")
    # What the owner is shown: copied when asked, so a later edit to the repair cannot change what they agreed to.
    title = models.CharField(max_length=200)
    amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    note = models.CharField(max_length=300, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ASKED)
    asked_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    answered_at = models.DateTimeField(null=True, blank=True)
    answered_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                    related_name="+")
    answer_note = models.CharField(max_length=300, blank=True)

    objects = ScopedQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.CheckConstraint(
                condition=(Q(kind="REPAIR", maintenance_request__isnull=False, expense__isnull=True)
                           | Q(kind="EXPENSE", expense__isnull=False, maintenance_request__isnull=True)),
                name="owner_approval_has_one_subject"),
            models.UniqueConstraint("maintenance_request", condition=Q(status="ASKED"),
                                    name="owner_approval_one_open_per_repair"),
            models.UniqueConstraint("expense", condition=Q(status="ASKED"),
                                    name="owner_approval_one_open_per_expense"),
        ]
        indexes = [models.Index(fields=["owner", "status"])]

    def __str__(self):
        return f"{self.kind} · {self.title}"


class OwnerPayoutDetail(PublicIdModel, TimeStampedModel):
    """Where the owner wants their money sent (D-217). The owner enters it; staff confirm it with the owner by
    phone before it counts, and a new one replaces the confirmed one only once confirmed."""

    class Method(models.TextChoices):
        MPESA = "MPESA", _("M-Pesa")
        BANK = "BANK", _("Bank transfer")

    class Status(models.TextChoices):
        PENDING = "PENDING", _("Waiting to be confirmed")
        CONFIRMED = "CONFIRMED", _("Confirmed")
        REPLACED = "REPLACED", _("Replaced")
        REJECTED = "REJECTED", _("Not accepted")

    organization = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT, related_name="+")
    owner = models.ForeignKey("properties.PropertyOwner", on_delete=models.PROTECT, related_name="payout_details")
    method = models.CharField(max_length=5, choices=Method.choices)
    account_name = models.CharField(max_length=120)
    # A phone number for M-Pesa, an account number for a bank.
    account_number = models.CharField(max_length=40)
    bank_name = models.CharField(max_length=80, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                   related_name="+")
    reject_reason = models.CharField(max_length=200, blank=True)

    objects = ScopedQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.UniqueConstraint("owner", condition=Q(status="CONFIRMED"),
                                    name="owner_payout_one_confirmed"),
            models.UniqueConstraint("owner", condition=Q(status="PENDING"), name="owner_payout_one_pending"),
        ]

    def __str__(self):
        return f"{self.method} · {self.account_name}"

    @property
    def masked(self) -> str:
        number = self.account_number
        return f"…{number[-4:]}" if len(number) > 4 else number


class OwnerPayoutRequest(PublicIdModel, TimeStampedModel):
    """The owner asks the office to send what is due to them (D-217). Paying is still recorded by staff as a
    remittance on the owner's account; this only asks."""

    class Status(models.TextChoices):
        OPEN = "OPEN", _("Waiting for the office")
        DONE = "DONE", _("Paid")
        DECLINED = "DECLINED", _("Not paid")
        WITHDRAWN = "WITHDRAWN", _("Withdrawn")

    organization = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT, related_name="+")
    owner = models.ForeignKey("properties.PropertyOwner", on_delete=models.PROTECT, related_name="payout_requests")
    amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    note = models.CharField(max_length=300, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    handled_at = models.DateTimeField(null=True, blank=True)
    handled_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                   related_name="+")
    answer_note = models.CharField(max_length=300, blank=True)

    objects = ScopedQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.CheckConstraint(condition=Q(amount__isnull=True) | Q(amount__gt=0),
                                   name="owner_payout_request_amount_positive"),
            models.UniqueConstraint("owner", condition=Q(status="OPEN"), name="owner_payout_request_one_open"),
        ]

    def __str__(self):
        return f"Payout request · {self.owner_id}"
