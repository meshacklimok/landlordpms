from django.contrib import admin

from .models import Document


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    """Read only: documents change through the app so every step is checked and audited.

    The file itself is not linked here: tenant ID copies are served only through the app.
    """

    list_display = ["title", "category", "organization", "property", "kind", "shared_with_tenant", "created_at",
                    "archived_at"]
    list_filter = ["category", "kind", "shared_with_tenant"]
    search_fields = ["title", "filename", "property__name"]
    raw_id_fields = ["organization", "property", "unit", "lease", "tenant", "uploaded_by", "archived_by"]
    exclude = ["file"]

    def get_queryset(self, request):
        return Document.all_objects.all()

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
