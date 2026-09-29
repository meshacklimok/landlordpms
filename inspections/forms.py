from django import forms
from django.utils.translation import gettext_lazy as _

from billing.forms import DateInput


class DetailsForm(forms.Form):
    inspected_on = forms.DateField(label=_("Inspection date"), widget=DateInput)
    tenant_present = forms.BooleanField(label=_("The tenant was present"), required=False)
    tenant_comments = forms.CharField(label=_("Tenant's comments"), required=False, max_length=2000,
                                      widget=forms.Textarea(attrs={"rows": 2}))
    keys_handed = forms.IntegerField(label=_("Keys handed over"), required=False, min_value=0, max_value=99)
    notes = forms.CharField(label=_("General notes"), required=False, max_length=4000,
                            widget=forms.Textarea(attrs={"rows": 3}))


class ItemForm(forms.Form):
    area = forms.CharField(label=_("Area"), max_length=60, required=False,
                           widget=forms.TextInput(attrs={"placeholder": _("e.g. Kitchen")}))
    name = forms.CharField(label=_("Item"), max_length=100)
    quantity = forms.IntegerField(label=_("Quantity"), min_value=1, max_value=999, initial=1)


class RegisterItemForm(ItemForm):
    notes = forms.CharField(label=_("Note"), max_length=200, required=False)


class PhotoForm(forms.Form):
    # A plain file field: inspections.photos checks and re-encodes it.
    image = forms.FileField(label=_("Photo"), widget=forms.ClearableFileInput(attrs={"accept": "image/*"}))
    caption = forms.CharField(label=_("Caption"), max_length=200, required=False)


class CancelForm(forms.Form):
    reason = forms.CharField(label=_("Why is it being cancelled?"), max_length=300)
