from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from . import services
from .models import SupportRequest


class SupportRequestForm(forms.Form):
    kind = forms.ChoiceField(label=_("What do you need?"), choices=SupportRequest.Kind.choices)
    subject = forms.CharField(label=_("Subject"), max_length=150)
    message = forms.CharField(label=_("Tell us more"), max_length=5000, widget=forms.Textarea(attrs={"rows": 6}))
    attachment = forms.FileField(label=_("Attach a file (optional)"), required=False,
                                 help_text=_("Up to 10 MB: a spreadsheet, CSV, PDF, text file or picture."))
    page = forms.CharField(required=False, widget=forms.HiddenInput)

    def clean_attachment(self):
        upload = self.cleaned_data.get("attachment")
        try:
            services.check_attachment(upload)
        except ValidationError as exc:
            raise forms.ValidationError(exc.messages) from exc
        return upload
