from django import forms
from django.utils.translation import gettext_lazy as _

from .models import ChargeType


class ChargeTypeForm(forms.Form):
    name = forms.CharField(label=_("Name"), max_length=60)
    category = forms.ChoiceField(
        label=_("Category"),
        choices=[c for c in ChargeType.Category.choices if c[0] not in ChargeType.NOT_RECURRING],
        initial=ChargeType.Category.OTHER,
    )
