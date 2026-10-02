import datetime

from django import forms
from django.utils.translation import gettext_lazy as _

from accounts.forms import PhoneField

from .models import Lease


class DateInput(forms.DateInput):
    input_type = "date"


def _iso(initial: dict) -> dict:
    # The form partial prints values as-is; a date input needs YYYY-MM-DD.
    return {k: v.isoformat() if isinstance(v, datetime.date) else v for k, v in initial.items()}


class LeaseForm(forms.Form):
    """Lease terms. Services validate; the form only parses input."""

    tenant = forms.ModelChoiceField(label=_("Primary tenant"), queryset=None,
                                    help_text=_("Invoices and receipts are addressed to this tenant."))
    co_tenants = forms.ModelMultipleChoiceField(label=_("Co-tenants"), queryset=None, required=False,
                                                widget=forms.CheckboxSelectMultiple)
    start_date = forms.DateField(label=_("Start date"), widget=DateInput)
    end_date = forms.DateField(label=_("End date"), widget=DateInput, required=False,
                               help_text=_("Leave empty for a periodic (month-to-month) lease."))
    rent = forms.DecimalField(label=_("Monthly rent (KES)"), max_digits=14, decimal_places=2, min_value=0)
    deposit_amount = forms.DecimalField(label=_("Deposit (KES)"), max_digits=14, decimal_places=2, min_value=0,
                                        initial=0)
    billing_frequency = forms.ChoiceField(
        label=_("Billing"), choices=Lease.Frequency.choices, initial=Lease.Frequency.MONTHLY, required=False,
        help_text=_("Quarterly and yearly leases get one invoice for 3 or 12 months, counted from the start "
                    "month. Rent and charges are still entered per month."))
    due_day = forms.IntegerField(label=_("Rent due day"), min_value=1, max_value=28, initial=1,
                                 help_text=_("Day of the month rent is due (1–28)."))
    grace_days = forms.IntegerField(label=_("Grace days"), min_value=0, max_value=31, initial=3,
                                    help_text=_("Days after the due date before rent counts as late."))
    notice_days = forms.IntegerField(label=_("Notice period (days)"), min_value=0, max_value=365, initial=30)
    terms = forms.CharField(label=_("Terms and notes"), required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, tenants, with_tenants=True, **kwargs):
        if "initial" in kwargs:
            kwargs["initial"] = _iso(kwargs["initial"])
        super().__init__(*args, **kwargs)
        if with_tenants:
            self.fields["tenant"].queryset = tenants
            self.fields["co_tenants"].queryset = tenants
        else:
            del self.fields["tenant"]
            del self.fields["co_tenants"]

    def clean_billing_frequency(self):
        return self.cleaned_data["billing_frequency"] or Lease.Frequency.MONTHLY

    def clean(self):
        data = super().clean()
        tenant = data.get("tenant")
        if tenant and tenant in (data.get("co_tenants") or []):
            data["co_tenants"] = [t for t in data["co_tenants"] if t != tenant]
        return data

    @staticmethod
    def initial_for(lease: Lease) -> dict:
        return {
            "start_date": lease.start_date, "end_date": lease.end_date, "rent": lease.rent_on(lease.start_date),
            "deposit_amount": lease.deposit_amount, "billing_frequency": lease.billing_frequency,
            "due_day": lease.due_day, "grace_days": lease.grace_days,
            "notice_days": lease.notice_days, "terms": lease.terms,
        }


class AddTenantForm(forms.Form):
    tenant = forms.ModelChoiceField(label=_("Tenant"), queryset=None)

    def __init__(self, *args, tenants, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["tenant"].queryset = tenants


class RentChangeForm(forms.Form):
    effective_from = forms.DateField(label=_("From"), widget=DateInput)
    amount = forms.DecimalField(label=_("New monthly rent (KES)"), max_digits=14, decimal_places=2, min_value=0)
    reason = forms.CharField(label=_("Reason"), max_length=200, required=False)


class ChargeForm(forms.Form):
    charge_type = forms.ModelChoiceField(label=_("Charge"), queryset=None)
    amount = forms.DecimalField(label=_("Amount per month (KES)"), max_digits=14, decimal_places=2, min_value=0)
    active_from = forms.DateField(label=_("From"), widget=DateInput, required=False,
                                  help_text=_("Defaults to the lease start date."))

    def __init__(self, *args, charge_types, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["charge_type"].queryset = charge_types


class PropertyChargeForm(forms.Form):
    """D-069: one recurring charge for every open lease at a property."""

    charge_type = forms.ModelChoiceField(label=_("Charge"), queryset=None)
    amount = forms.DecimalField(label=_("Amount per month (KES)"), max_digits=14, decimal_places=2, min_value=0)
    active_from = forms.DateField(label=_("From"), widget=DateInput,
                                  help_text=_("A lease that starts later gets it from its own start date."))

    def __init__(self, *args, charge_types, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["charge_type"].queryset = charge_types


class EndChargeForm(forms.Form):
    active_to = forms.DateField(label=_("Last day"), widget=DateInput)


class PayerForm(forms.Form):
    phone = PhoneField()
    name = forms.CharField(label=_("Name"), max_length=150, required=False, help_text=_("e.g. employer, spouse"))


class NoticeForm(forms.Form):
    given_on = forms.DateField(label=_("Notice given on"), widget=DateInput)


class EndLeaseForm(forms.Form):
    ended_on = forms.DateField(label=_("Moved out on"), widget=DateInput,
                               help_text=_("The last day of the lease. Charges stop after this day."))
    terminate = forms.BooleanField(label=_("Ended early (termination)"), required=False)
    reason = forms.CharField(label=_("Reason"), required=False, widget=forms.Textarea(attrs={"rows": 2}),
                             help_text=_("Required for a termination."))


class RenewForm(forms.Form):
    start_date = forms.DateField(label=_("Starts"), widget=DateInput, required=False,
                                 help_text=_("Defaults to the day after this lease ends."))
    end_date = forms.DateField(label=_("Ends"), widget=DateInput, required=False,
                               help_text=_("Leave empty for a periodic (month-to-month) lease."))
    rent = forms.DecimalField(label=_("Monthly rent (KES)"), max_digits=14, decimal_places=2, min_value=0,
                              required=False, help_text=_("Defaults to the current rent."))


class TransferForm(forms.Form):
    unit = forms.ModelChoiceField(label=_("Move to unit"), queryset=None)
    start_date = forms.DateField(label=_("Moves in on"), widget=DateInput,
                                 help_text=_("This lease ends the day before."))
    end_date = forms.DateField(label=_("Ends"), widget=DateInput, required=False,
                               help_text=_("Defaults to this lease's end date."))
    rent = forms.DecimalField(label=_("Monthly rent (KES)"), max_digits=14, decimal_places=2, min_value=0,
                              required=False, help_text=_("Defaults to the new unit's listed rent."))

    def __init__(self, *args, units, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["unit"].queryset = units
        self.fields["unit"].label_from_instance = lambda u: f"{u.property.name} · {u.code}"


class LeaseSearchForm(forms.Form):
    q = forms.CharField(required=False, max_length=100)
    status = forms.ChoiceField(required=False, choices=[("", _("All"))] + list(Lease.Status.choices))
