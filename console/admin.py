from django.contrib import admin

from .models import PlatformStaff


@admin.register(PlatformStaff)
class PlatformStaffAdmin(admin.ModelAdmin):
    list_display = ["user", "role", "is_active", "created_at"]
    list_filter = ["role", "is_active"]
    raw_id_fields = ["user", "added_by"]
