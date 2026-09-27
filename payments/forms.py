from django import forms
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from billing.forms import DateInput
from core.money import format_money

from .models import Payment, PaymentAccount
from .services import open_invoices


def accounts_for(lease):
    """The property's payment accounts, or every live account if none are linked to it yet."""
    qs = PaymentAccount.objects.for_org(lease.organization)
    linked = qs.filter(properties__property=lease.unit.property)
    return linked if linked.exists() else qs


class _Fields:
    def __init__(self, form, fields):
        self.form, self.fields = form, fields

    def __iter__(self):
        return iter(self.fields)

    def non_field_errors(self):
        return self.form.non_field_errors()


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

    def allocations(self):
        chosen = [(i, self.cleaned_data.get(f"alloc_{i.public_id}", "").strip())
                  for i in getattr(self, "invoices", [])]
        return [(i, amount) for i, amount in chosen if amount] or None


class PaymentForm(AllocationFieldsMixin, forms.Form):
    amount = forms.CharField(label=_("Amount received"), max_length=20)
    method = forms.ChoiceField(label=_("Paid by"), choices=Payment.Method.choices)
    paid_at = forms.DateField(label=_("Date paid"), widget=DateInput)
    reference = forms.CharField(label=_("Reference"), max_length=60, required=False,
                                help_text=_("M-Pesa code, cheque number or bank reference."))
    payment_account = forms.ModelChoiceField(label=_("Received into"), queryset=PaymentAccount.objects.none(),
                                             required=False)
    tenant = forms.ChoiceField(label=_("Paid by tenant"), required=False)

    def __init__(self, *args, lease, can_allocate=False, **kwargs):
        kwargs.setdefault("initial", {}).setdefault("paid_at", timezone.localdate().isoformat())
        super().__init__(*args, **kwargs)
        self.lease = lease
        self.fields["payment_account"].queryset = accounts_for(lease)
        if not self.fields["payment_account"].queryset.exists():
            del self.fields["payment_account"]
        tenants = [lt.tenant for lt in lease.lease_tenants.select_related("tenant").order_by("-is_primary", "pk")]
        if len(tenants) > 1:
            self.fields["tenant"].choices = [(t.public_id, t.name) for t in tenants]
        else:
            del self.fields["tenant"]
        self._tenants = {str(t.public_id): t for t in tenants}
        if can_allocate:
            self.add_allocation_fields(lease)

    def main_form(self):
        """This form without the allocation fields, for partials/form.html."""
        return _Fields(self, [f for f in self if not f.name.startswith("alloc_")])

    def service_kwargs(self) -> dict:
        d = self.cleaned_data
        return {"amount": d["amount"], "method": d["method"], "paid_at": d["paid_at"], "reference": d["reference"],
                "payment_account": d.get("payment_account"), "tenant": self._tenants.get(d.get("tenant") or ""),
                "allocations": self.allocations()}


class ConfirmForm(AllocationFieldsMixin, forms.Form):
    def __init__(self, *args, lease, can_allocate=False, **kwargs):
        super().__init__(*args, **kwargs)
        if can_allocate:
            self.add_allocation_fields(lease)


class ReasonForm(forms.Form):
    reason = forms.CharField(label=_("Reason"), max_length=300)
