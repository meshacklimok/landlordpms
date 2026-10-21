from django.contrib import admin

from .models import StarterPrompt


@admin.register(StarterPrompt)
class StarterPromptAdmin(admin.ModelAdmin):
    """The suggested questions in an empty assistant chat. Edit the wording, reorder or hide them here."""

    list_display = ("text", "sort_order", "is_active")
    list_editable = ("sort_order", "is_active")
    ordering = ("sort_order", "pk")
