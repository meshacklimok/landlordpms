from django.contrib import admin

from .models import Lease, LeaseCharge, LeasePayer, LeaseRentChange, LeaseTenant


class LeaseTenantInline(admin.TabularInline):
    model = LeaseTenant
    extra = 0
    raw_id_fields = ["tenant"]


class LeaseRentChangeInline(admin.TabularInline):
    model = LeaseRentChange
    extra = 0
    readonly_fields = ["created_by", "created_at"]


class LeaseChargeInline(admin.TabularInline):
    model = LeaseCharge
    extra = 0


class LeasePayerInline(admin.TabularInline):
    model = LeasePayer
    extra = 0


@admin.register(Lease)
class LeaseAdmin(admin.ModelAdmin):
    list_display = ["__str__", "unit", "status", "start_date", "end_date", "organization"]
    list_filter = ["status"]
    search_fields = ["number", "unit__payment_reference", "organization__name"]
    raw_id_fields = ["unit"]
    readonly_fields = ["public_id", "number", "status", "activated_at", "notice_given_on", "ended_on", "end_reason",
                       "previous_lease", "created_at", "updated_at", "archived_at", "archived_by"]
    inlines = [LeaseTenantInline, LeaseRentChangeInline, LeaseChargeInline, LeasePayerInline]

    def get_queryset(self, request):
        return Lease.all_objects.select_related("organization", "unit__property")

    def has_delete_permission(self, request, obj=None):
        # Drafts are deleted in the app, where it is audited (doc 11 §23).
        return False
