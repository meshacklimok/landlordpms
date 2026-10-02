from django import forms
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from billing.forms import DateInput
from core.money import format_money

from . import selectors
from .models import Payment, PaymentAccount
from .services import open_invoices


def accounts_for(lease):
    """The property's payment accounts, or every live account if none are linked to it yet."""
    qs = PaymentAccount.objects.for_org(lease.organization)
    linked = qs.filter(properties__property=lease.unit.property)
    return linked if linked.exists() else qs


class PaymentFilterForm(forms.Form):
    """The list's filters. A bad value is dropped rather than shown as an error."""

    q = forms.CharField(max_length=60, required=False)
    status = forms.ChoiceField(choices=[("", "")] + [(k, v) for k, v in Payment.STATES.items()], required=False)
    method = forms.ChoiceField(choices=[("", _("Any method"))] + Payment.Method.choices, required=False)
    property = forms.ModelChoiceField(queryset=None, required=False, to_field_name="public_id",
                                      empty_label=_("All properties"))
    date_from = forms.DateField(required=False, widget=DateInput)
    date_to = forms.DateField(required=False, widget=DateInput)

    def __init__(self, data, *, properties):
        super().__init__(data)
        self.fields["property"].queryset = properties

    def filters(self) -> dict:
        self.is_valid()
        d = {name: self.cleaned_data.get(name) for name in self.fields if name not in self.errors}
        d["q"] = (d.get("q") or "").strip()
        return {k: v for k, v in d.items() if v not in (None, "")}

    def without_status(self) -> dict:
        return {k: v for k, v in self.filters().items() if k != "status"}


class AllocationFieldsMixin:
    """One optional amount per open invoice. All left blank = oldest invoice first."""

    def add_allocation_fields(self, lease):
        self.invoices = list(open_invoices(lease))
        for invoice in self.invoices:
            self.fields[f"alloc_{invoice.public_id}"] = forms.CharField(
                label=_("%(number)s · %(left)s left") % {
                    "number": invoice.number, "left": format_money(invoice.outstanding, invoice.currency)},
                max_length=20, required=False)

    def allocation_fields(self):
        return [self[f"alloc_{i.public_id}"] for i in getattr(self, "invoices", [])]

    def allocation_rows(self):
        """(invoice, bound field) pairs, oldest due first, for the allocation table."""
        return list(zip(getattr(self, "invoices", []), self.allocation_fields(), strict=True))

    def allocations(self):
        chosen = [(i, self.cleaned_data.get(f"alloc_{i.public_id}", "").strip())
                  for i in getattr(self, "invoices", [])]
        return [(i, amount) for i, amount in chosen if amount] or None


class PaymentForm(AllocationFieldsMixin, forms.Form):
    amount = forms.CharField(label=_("Amount received"), max_length=20)
    method = forms.ChoiceField(label=_("Paid by"), choices=Payment.Method.choices, initial=Payment.Method.MPESA)
    paid_at = forms.DateField(label=_("Date paid"), widget=DateInput)
    reference = forms.CharField(label=_("Reference"), max_length=60, required=False,
                                help_text=_("M-Pesa code, cheque number or bank reference."))
    payment_account = forms.ModelChoiceField(label=_("Received into"), queryset=PaymentAccount.objects.none(),
                                             required=False, empty_label=_("Not recorded"))
    tenant = forms.ChoiceField(label=_("Paid by tenant"), required=False)
    allow_duplicate = forms.BooleanField(label=_("Record it anyway"), required=False)

    def __init__(self, *args, lease, can_allocate=False, visible=None, **kwargs):
        kwargs.setdefault("initial", {}).setdefault("paid_at", timezone.localdate().isoformat())
        super().__init__(*args, **kwargs)
        self.lease = lease
        # Payments the member may open; a duplicate outside them is reported without a link.
        self.visible = visible if visible is not None else Payment.objects.none()
        self.duplicate = self.visible_duplicate = None
        accounts = accounts_for(lease)
        if accounts.exists():
            self.fields["payment_account"].queryset = accounts
            default = accounts.filter(properties__property=lease.unit.property, properties__is_default=True).first()
            if default:
                self.initial.setdefault("payment_account", default.pk)
        else:
            del self.fields["payment_account"]
        tenants = [lt.tenant for lt in lease.lease_tenants.select_related("tenant").order_by("-is_primary", "pk")]
        if len(tenants) > 1:
            self.fields["tenant"].choices = [(t.public_id, t.name) for t in tenants]
        else:
            del self.fields["tenant"]
        self._tenants = {str(t.public_id): t for t in tenants}
        if can_allocate:
            self.add_allocation_fields(lease)

    def clean(self):
        cleaned = super().clean()
        reference = (cleaned.get("reference") or "").strip()
        if reference and cleaned.get("method") == Payment.Method.MPESA and self._waiting_in_inbox(reference):
            # Recording it here as well would count the money twice once someone matches it there.
            self.add_error("reference", _("Safaricom already sent this M-Pesa payment. Match it from the "
                                          "M-Pesa inbox instead."))
        elif reference and not cleaned.get("allow_duplicate"):
            self.duplicate = selectors.same_reference(self.lease.organization, reference).first()
            if self.duplicate:
                self.visible_duplicate = self.visible.filter(pk=self.duplicate.pk).first()
                self.add_error("reference", _("This reference was already used on another payment."))
        return cleaned

    def _waiting_in_inbox(self, reference: str) -> bool:
        from mpesa.models import MpesaTransaction

        waiting = [MpesaTransaction.Status.RECEIVED, MpesaTransaction.Status.UNMATCHED,
                   MpesaTransaction.Status.FLAGGED]
        return MpesaTransaction.objects.filter(organization_id=self.lease.organization_id,
                                               trans_id__iexact=reference, status__in=waiting).exists()

    def main_form(self):
        """This form without the allocation fields, for partials/form.html."""
        return _Fields(self, [f for f in self if not f.name.startswith("alloc_") and f.name != "allow_duplicate"])

    def service_kwargs(self) -> dict:
        d = self.cleaned_data
        return {"amount": d["amount"], "method": d["method"], "paid_at": d["paid_at"], "reference": d["reference"],
                "payment_account": d.get("payment_account"), "tenant": self._tenants.get(d.get("tenant") or ""),
                "allocations": self.allocations()}


class _Fields:
    def __init__(self, form, fields):
        self.form, self.fields = form, fields

    def __iter__(self):
        return iter(self.fields)

    def non_field_errors(self):
        return self.form.non_field_errors()


class ConfirmForm(AllocationFieldsMixin, forms.Form):
    def __init__(self, *args, lease, can_allocate=False, **kwargs):
        super().__init__(*args, **kwargs)
        if can_allocate:
            self.add_allocation_fields(lease)


class ReasonForm(forms.Form):
    reason = forms.CharField(label=_("Reason"), max_length=300, widget=forms.Textarea(attrs={"rows": 2}))
