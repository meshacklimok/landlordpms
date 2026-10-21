from django import forms
from django.utils.translation import gettext_lazy as _

from .models import Document
from .services import ACCEPT


class DetailsForm(forms.Form):
    title = forms.CharField(label=_("Title"), max_length=120,
                            widget=forms.TextInput(attrs={"placeholder": _("e.g. Signed lease agreement")}))
    category = forms.ChoiceField(label=_("Category"), choices=Document.Category.choices,
                                 initial=Document.Category.OTHER)
    expires_on = forms.DateField(label=_("Expires on (optional)"), required=False,
                                 widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
                                 help_text=_("Insurance, permits: you are reminded 30 days before."))
    note = forms.CharField(label=_("Note (optional)"), max_length=300, required=False)


class UploadForm(DetailsForm):
    """`properties` is given only when the page was opened without a place: then a property is chosen."""

    property = forms.ModelChoiceField(label=_("Property"), queryset=None, to_field_name="public_id",
                                      empty_label=_("Choose…"))
    # A plain file field: services.process_file checks it and re-encodes photos.
    file = forms.FileField(label=_("File"), widget=forms.ClearableFileInput(attrs={"accept": ACCEPT}),
                           help_text=_("PDF, photo, Word or Excel, up to 10 MB."))
    share = forms.BooleanField(label=_("Share with the tenant in the portal"), required=False)

    field_order = ["property", "file", "title", "category", "expires_on", "note", "share"]

    def __init__(self, *args, properties=None, can_share=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["title"].required = False
        self.fields["title"].help_text = _("Leave empty to use the file name.")
        if properties is None:
            del self.fields["property"]
        else:
            self.fields["property"].queryset = properties
        if not can_share:
            del self.fields["share"]
