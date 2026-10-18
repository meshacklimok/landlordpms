import datetime
from decimal import Decimal

from django import forms
from django.utils.translation import gettext_lazy as _

from . import deposits
from .models import ChargeType, DepositEntry, FollowUp


class ChargeTypeForm(forms.Form):
    name = forms.CharField(label=_("Name"), max_length=60)
    category = forms.ChoiceField(
        label=_("Category"),
        choices=[c for c in ChargeType.Category.choices if c[0] not in ChargeType.NOT_RECURRING],
        initial=ChargeType.Category.OTHER,
    )


class DateInput(forms.DateInput):
    input_type = "date"


class MonthField(forms.CharField):
    """A month picker; cleans to the first day of the month."""

    widget = forms.TextInput(attrs={"type": "month"})

    def to_python(self, value):
        value = super().to_python(value)
        if not value:
            return None
        try:
            return datetime.datetime.strptime(value, "%Y-%m").date()
        except ValueError:
            raise forms.ValidationError(_("Choose a month.")) from None


class GenerateForm(forms.Form):
    """Which invoices to issue: for what, for whom and with which dates (D-176)."""

    SCOPE_ALL, SCOPE_PROPERTY, SCOPE_LEASES = "all", "property", "leases"

    month = MonthField(label=_("Month to bill"), help_text=_("The rent period that holds this month. "
                       "Quarterly and yearly leases are billed for their whole period."))
    include = forms.ChoiceField(
        label=_("What to bill"), initial="all", widget=forms.RadioSelect,
        choices=[("all", _("Rent, charges and metered utilities")),
                 ("charges", _("Rent and recurring charges only")),
                 ("meters", _("Metered water and electricity only"))])
    scope = forms.ChoiceField(
        label=_("Who to bill"), initial="all", widget=forms.RadioSelect,
        choices=[("all", _("Every lease I manage")), ("property", _("All leases in one property")),
                 ("leases", _("Only the leases I pick"))])
    property = forms.ChoiceField(label=_("Property"), required=False)
    leases = forms.MultipleChoiceField(label=_("Leases"), required=False)
    due_date = forms.DateField(
        label=_("Due date"), required=False, widget=DateInput,
        help_text=_("Leave empty to use each lease's own due day. A date before today is not allowed."))
    notify = forms.BooleanField(label=_("Tell the tenants now (SMS, email or WhatsApp as they prefer)"),
                                required=False, initial=True)

    def __init__(self, *args, properties=(), leases=(), **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("month", "due_date", "property"):
            self.fields[name].widget.attrs["class"] = "form-control" if name != "property" else "form-select"
        self.fields["leases"].widget.attrs.update({"class": "form-select", "size": 8})
        for name in ("include", "scope"):
            self.fields[name].widget.attrs["class"] = "form-check-input"
        self.fields["notify"].widget.attrs["class"] = "form-check-input"
        self._leases = {str(ls.public_id): ls for ls in leases}
        self.fields["property"].choices = [("", "---------")] + [(str(p.public_id), p.name) for p in properties]
        self.fields["leases"].choices = [(k, self.lease_label(ls)) for k, ls in self._leases.items()]

    @staticmethod
    def lease_label(lease):
        tenant = lease.primary_tenant
        return f"{lease.unit.property.name} · {lease.unit.code} · {tenant.name if tenant else lease.number}"

    def clean_due_date(self):
        due = self.cleaned_data.get("due_date")
        if due and due < datetime.date.today():
            raise forms.ValidationError(_("The due date can't be in the past."))
        return due

    def clean(self):
        data = super().clean()
        scope = data.get("scope")
        if scope == self.SCOPE_PROPERTY and not data.get("property"):
            self.add_error("property", _("Choose a property."))
        if scope == self.SCOPE_LEASES and not data.get("leases"):
            self.add_error("leases", _("Pick at least one lease."))
        return data

    def chosen_leases(self):
        """The leases in scope, or None for every lease."""
        scope = self.cleaned_data["scope"]
        if scope == self.SCOPE_LEASES:
            return [self._leases[k] for k in self.cleaned_data["leases"] if k in self._leases]
        if scope == self.SCOPE_PROPERTY:
            return [ls for ls in self._leases.values()
                    if str(ls.unit.property.public_id) == self.cleaned_data["property"]]
        return None


class VoidForm(forms.Form):
    reason = forms.CharField(label=_("Why is it being voided?"), max_length=300)


class OpeningBalanceForm(forms.Form):
    amount = forms.CharField(label=_("Balance brought forward"), max_length=20,
                             help_text=_("What the tenant owed on that day. Negative if they had paid ahead; "
                                         "0 clears it."))
    as_of = forms.DateField(label=_("As of"), widget=DateInput)
    reason = forms.CharField(label=_("Note"), max_length=300, required=False)


class StatementForm(forms.Form):
    start = forms.DateField(label=_("From"), required=False, widget=DateInput)
    end = forms.DateField(label=_("To"), required=False, widget=DateInput)


class DepositForm(forms.Form):
    """Receive, deduct or refund; the view says which."""

    amount = forms.CharField(label=_("Amount"), max_length=20,
                             widget=forms.TextInput(attrs={"inputmode": "decimal", "autocomplete": "off"}))
    deposit_type = forms.ChoiceField(label=_("Deposit"), choices=DepositEntry.DepositType.choices)
    entry_date = forms.DateField(label=_("Date"), widget=DateInput)
    reference = forms.CharField(label=_("Reference"), max_length=60, required=False,
                                help_text=_("M-Pesa code, cheque or bank reference."))
    reason = forms.CharField(label=_("Reason"), max_length=300, required=False)


class DeductForm(DepositForm):
    """A deduction, shaped by the owner's deposit rules (D-150)."""

    reference = None
    heading = forms.ChoiceField(label=_("Deducted for"), choices=[], required=False)
    evidence = forms.FileField(label=_("Receipt, quote or photo"), required=False,
                               widget=forms.FileInput(attrs={"accept": "image/jpeg,image/png,application/pdf"}),
                               help_text=_("A photo (JPEG or PNG) or a PDF, up to 10 MB."))
    apply_to_balance = forms.BooleanField(label=_("Put it towards what the tenant owes"), required=False)
    field_order = ["amount", "deposit_type", "entry_date", "heading", "reason", "evidence", "apply_to_balance"]

    def __init__(self, *args, organization=None, cites_report=False, **kwargs):
        super().__init__(*args, **kwargs)
        if cites_report:  # the move-out report is the proof
            del self.fields["evidence"]
        allowed = deposits.allowed_headings(organization) if organization is not None else []
        headings = [(c, l) for c, l in DepositEntry.Heading.choices if not allowed or c in allowed]
        self.fields["heading"].choices = headings if allowed else [("", "---------"), *headings]
        self.fields["heading"].required = bool(allowed)
        if organization is not None:
            if not organization.deposit_allow_offset:
                del self.fields["apply_to_balance"]
            if organization.deposit_proof_required and "evidence" in self.fields:
                self.fields["evidence"].help_text = _(
                    "Required unless the move-out report is cited. A photo (JPEG or PNG) or a PDF, up to 10 MB.")


class ReverseDepositForm(forms.Form):
    reason = forms.CharField(label=_("Why is it being corrected?"), max_length=300)


class FollowUpForm(forms.Form):
    outcome = forms.ChoiceField(label=_("What happened"), choices=FollowUp.Outcome.choices)
    promised_on = forms.DateField(label=_("Will pay by"), required=False, widget=DateInput)
    promised_amount = forms.DecimalField(label=_("Amount promised"), required=False, max_digits=14, decimal_places=2,
                                         min_value=Decimal("0.01"))
    note = forms.CharField(label=_("Note"), required=False, max_length=300)

    def clean(self):
        data = super().clean()
        if data.get("outcome") == FollowUp.Outcome.PROMISED and not data.get("promised_on"):
            self.add_error("promised_on", _("Enter the date they will pay by."))
        return data


class CallListFilterForm(forms.Form):
    property = forms.ModelChoiceField(label=_("Property"), queryset=None, required=False, to_field_name="public_id",
                                      empty_label=_("All properties"))
    grade = forms.ChoiceField(label=_("Grade"), required=False,
                              choices=[("", _("All grades")), *[(g, g) for g in "ABCDE"], ("new", _("New"))])

    def __init__(self, *args, properties, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["property"].queryset = properties
        for f in self.fields.values():
            f.widget.attrs["class"] = "form-select form-select-sm w-auto"
            f.widget.attrs["aria-label"] = f.label

    def value(self, name):
        return self.cleaned_data.get(name) if self.is_valid() else None
