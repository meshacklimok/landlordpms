from django import forms
from django.utils.translation import gettext_lazy as _

from billing.forms import DateInput

from .models import Expense, Supplier


class ExpenseForm(forms.Form):
    """Querysets are set per request: the member's properties, the organization's categories and suppliers."""

    property = forms.ModelChoiceField(label=_("Property"), queryset=None, to_field_name="public_id",
                                      empty_label=_("Choose…"))
    category = forms.ModelChoiceField(label=_("Category"), queryset=None, to_field_name="public_id",
                                      empty_label=_("Choose…"))
    description = forms.CharField(label=_("What was paid for"), max_length=200,
                                  widget=forms.TextInput(attrs={"placeholder": _("e.g. Fixed the gate lock, B2")}))
    amount = forms.CharField(label=_("Amount (KES)"), max_length=20,
                             widget=forms.TextInput(attrs={"inputmode": "decimal", "autocomplete": "off"}))
    paid_on = forms.DateField(label=_("Date paid"), widget=DateInput)
    method = forms.ChoiceField(label=_("Paid by"), choices=Expense.Method.choices, initial=Expense.Method.MPESA)
    reference = forms.CharField(label=_("Reference"), max_length=60, required=False,
                                help_text=_("The M-Pesa code, cheque number or bank reference."))
    supplier = forms.ModelChoiceField(label=_("Paid to (supplier)"), queryset=None, required=False,
                                      to_field_name="public_id", empty_label=_("Not a listed supplier"))
    # A plain file field: services.process_receipt checks it and re-encodes photos.
    receipt = forms.FileField(label=_("Receipt photo or PDF (optional)"), required=False,
                              widget=forms.ClearableFileInput(attrs={"accept": "image/*,application/pdf"}))

    def __init__(self, *args, properties, categories, suppliers, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["property"].queryset = properties
        self.fields["category"].queryset = categories
        self.fields["supplier"].queryset = suppliers


class ReceiptForm(forms.Form):
    receipt = forms.FileField(label=_("Receipt photo or PDF"),
                              widget=forms.ClearableFileInput(attrs={"accept": "image/*,application/pdf"}))


class ReasonForm(forms.Form):
    reason = forms.CharField(label=_("Reason"), max_length=300)


class SupplierForm(forms.ModelForm):
    class Meta:
        model = Supplier
        fields = ["name", "phone", "email", "kra_pin", "payment_details", "note"]
        help_texts = {"payment_details": _("e.g. Till 123456, or the bank and account number.")}


class CategoryForm(forms.Form):
    name = forms.CharField(label=_("Name"), max_length=60)
