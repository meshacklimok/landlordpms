"""Django admin is the Platform Admin console for now (doc 11 §4)."""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.db.models import Count, Q

from .models import (
    Branch,
    Capability,
    Invitation,
    Membership,
    MembershipCapability,
    Organization,
    PropertyAccess,
    Role,
    RoleCapability,
    RoleTemplate,
    User,
)


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    ordering = ["-date_joined"]
    list_display = ["phone", "full_name", "email", "phone_verified_at", "is_active", "is_staff", "date_joined"]
    list_filter = ["is_active", "is_staff", "is_superuser"]
    search_fields = ["phone", "email", "full_name"]
    readonly_fields = ["public_id", "date_joined", "last_login", "password_reset_at"]
    fieldsets = (
        (None, {"fields": ("public_id", "phone", "password")}),
        ("Profile", {"fields": ("full_name", "email", "phone_verified_at", "email_verified_at")}),
        ("Platform access", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Dates", {"fields": ("date_joined", "last_login", "password_reset_at")}),
    )
    add_fieldsets = ((None, {"classes": ("wide",), "fields": ("phone", "full_name", "password1", "password2")}),)


class MembershipInline(admin.TabularInline):
    model = Membership
    fk_name = "organization"
    extra = 0
    fields = ["user", "role", "all_properties", "is_active", "archived_at"]
    readonly_fields = fields
    can_delete = False

    def get_queryset(self, request):
        return Membership.all_objects.select_related("user", "role")

    def has_add_permission(self, request, obj=None):
        return False


class BranchInline(admin.TabularInline):
    """Design-in (D-040): branches have no screens yet."""

    model = Branch
    extra = 0
    fields = ["name", "phone", "email", "archived_at"]
    readonly_fields = ["archived_at"]
    can_delete = False

    def get_queryset(self, request):
        return Branch.all_objects.all()


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ["name", "org_type", "status", "member_count", "property_count", "created_at", "archived_at"]
    list_filter = ["status", "org_type"]
    search_fields = ["name", "kra_pin", "billing_phone", "billing_email"]
    readonly_fields = ["public_id", "created_at", "updated_at", "created_by", "archived_at", "archived_by"]
    inlines = [MembershipInline, BranchInline]
    actions = ["freeze", "unfreeze"]

    def get_queryset(self, request):
        return Organization.all_objects.annotate(
            _members=Count("memberships", filter=Q(memberships__is_active=True), distinct=True),
            _properties=Count("properties", distinct=True),
        )

    @admin.display(ordering="_members", description="Active members")
    def member_count(self, obj):
        return obj._members

    @admin.display(ordering="_properties", description="Properties")
    def property_count(self, obj):
        return obj._properties

    @admin.action(description="Freeze selected organizations")
    def freeze(self, request, queryset):
        from audit.services import record

        for org in queryset:
            org.status = Organization.Status.FROZEN
            org.save(update_fields=["status", "updated_at"])
            record("organization.freeze", organization=org, obj=org, request=request)

    @admin.action(description="Unfreeze (set active)")
    def unfreeze(self, request, queryset):
        from audit.services import record

        for org in queryset:
            org.status = Organization.Status.ACTIVE
            org.save(update_fields=["status", "updated_at"])
            record("organization.unfreeze", organization=org, obj=org, request=request)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Capability)
class CapabilityAdmin(admin.ModelAdmin):
    list_display = ["codename", "module", "description", "sensitive", "org_wide", "read_only_safe", "is_active"]
    list_filter = ["module", "sensitive", "org_wide", "is_active"]
    search_fields = ["codename", "description"]

    # Defined in code; synced by `manage.py sync_access_catalog`.
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(RoleTemplate)
class RoleTemplateAdmin(admin.ModelAdmin):
    list_display = ["name", "key", "is_owner_template", "is_active", "sort_order"]
    filter_horizontal = ["capabilities"]
    # Changes apply only to organizations created afterwards (doc 13).


class RoleCapabilityInline(admin.TabularInline):
    model = RoleCapability
    extra = 0
    autocomplete_fields = ["capability"]


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ["name", "organization", "is_owner_role", "based_on_template", "archived_at"]
    list_filter = ["is_owner_role"]
    search_fields = ["name", "organization__name"]
    readonly_fields = ["public_id", "organization", "is_owner_role", "archived_at", "archived_by"]
    inlines = [RoleCapabilityInline]

    def get_queryset(self, request):
        return Role.all_objects.select_related("organization", "based_on_template")

    def has_delete_permission(self, request, obj=None):
        return False


class OverrideInline(admin.TabularInline):
    model = MembershipCapability
    fk_name = "membership"
    extra = 0
    readonly_fields = ["capability", "granted", "created_by", "created_at"]
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


class PropertyAccessInline(admin.TabularInline):
    model = PropertyAccess
    extra = 0
    readonly_fields = ["property", "created_at"]
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    """Read-only: organizations change memberships through the app, with audit."""

    list_display = ["user", "organization", "role", "all_properties", "is_active", "archived_at"]
    list_filter = ["is_active", "all_properties"]
    search_fields = ["user__phone", "user__full_name", "organization__name"]
    inlines = [OverrideInline, PropertyAccessInline]

    def get_queryset(self, request):
        return Membership.all_objects.select_related("user", "organization", "role")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Invitation)
class InvitationAdmin(admin.ModelAdmin):
    list_display = ["phone", "organization", "role", "invited_by", "expires_at", "accepted_at", "revoked_at"]
    search_fields = ["phone", "organization__name"]
    exclude = ["token_hash"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
