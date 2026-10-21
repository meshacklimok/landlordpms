from django import forms
from django.utils.translation import gettext_lazy as _

from . import knowledge
from .models import KnowledgeEntry


class KnowledgeForm(forms.ModelForm):
    class Meta:
        model = KnowledgeEntry
        fields = ["title", "answer", "property", "for_tenants"]
        widgets = {"answer": forms.Textarea(attrs={"rows": 5})}
        help_texts = {
            "title": _("What people ask, e.g. \"When are the office hours?\" or \"How is the deposit refunded?\""),
            "answer": _("What the assistant should say. Plain and complete, as you would tell a new member of staff. "
                        "Leave out phone numbers and emails: they are never passed on."),
            "property": _("Leave empty if it applies to all your properties."),
            "for_tenants": _("Tenants asking in the portal or on WhatsApp can be told this. Leave off for notes "
                             "meant for staff only."),
        }

    def __init__(self, *args, membership, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["property"].queryset = knowledge.property_choices(membership)
        self.fields["property"].empty_label = _("All properties")
