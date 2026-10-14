from django import forms
from django.utils.translation import gettext_lazy as _

from billing.forms import DateInput

from .models import NoticeLetter


class WithdrawForm(forms.Form):
    reason = forms.CharField(label=_("Why is it being withdrawn?"), max_length=300)


class SettingsForm(forms.Form):
    letter_show_payment_record = forms.BooleanField(
        label=_("On-time payment record"), required=False,
        help_text=_("How many invoices fell due, and how many were paid on time, late or not yet in full."))
    letter_show_balance = forms.BooleanField(
        label=_("Balance owed"), required=False, help_text=_("What the tenant owes, or is in credit, that day."))
    letter_show_deposit = forms.BooleanField(
        label=_("Deposit status"), required=False,
        help_text=_("Amount held, or once the tenancy ends, what was refunded and deducted. Never the reasons."))
    letter_show_rent = forms.BooleanField(label=_("Monthly rent"), required=False,
                                          help_text=_("The rent now, or at the end of the tenancy."))


class VacateLetterForm(forms.Form):
    print_reason = forms.BooleanField(label=_("Print the notice's reason on the letter"), required=False,
                                      initial=True)
    note = forms.CharField(label=_("Note to add (optional)"), max_length=500, required=False,
                           widget=forms.Textarea(attrs={"rows": 2}))


class DemandLetterForm(forms.Form):
    pay_within_days = forms.IntegerField(label=_("Days to pay after receiving it"), min_value=1, max_value=60,
                                         initial=7)
    note = forms.CharField(label=_("Note to add (optional)"), max_length=500, required=False,
                           widget=forms.Textarea(attrs={"rows": 2}))


class ServiceForm(forms.Form):
    served_on = forms.DateField(label=_("Served on"), widget=DateInput)
    served_via = forms.ChoiceField(label=_("How"), choices=[("", "")] + NoticeLetter.ServedVia.choices)
    served_by_name = forms.CharField(label=_("Served by"), max_length=150, required=False)
    received_by = forms.CharField(label=_("Received by"), max_length=150, required=False,
                                  help_text=_("Who took it, if it was handed over."))
    service_note = forms.CharField(label=_("Note"), max_length=300, required=False)
    proof = forms.FileField(label=_("Signed copy (scan or photo)"), required=False,
                            help_text=_("Kept with the lease's documents."))

    def __init__(self, *args, can_upload=True, **kwargs):
        super().__init__(*args, **kwargs)
        if not can_upload:
            del self.fields["proof"]
        else:
            from documents.services import ACCEPT

            self.fields["proof"].widget.attrs["accept"] = ACCEPT


class HandoverForm(forms.Form):
    purpose = forms.ChoiceField(label=_("What is it for?"), choices=[
        ("ARREARS", _("Rent arrears")), ("MOVE_OUT", _("Disputed move-out"))], initial="ARREARS")
    advocate = forms.CharField(label=_("Advocate or firm (optional)"), max_length=150, required=False,
                               help_text=_("Printed on the brief as who it was prepared for."))
    note = forms.CharField(label=_("Note for the advocate (optional)"), max_length=1000, required=False,
                           widget=forms.Textarea(attrs={"rows": 3}),
                           help_text=_("What happened that the records do not show, in your own words."))
