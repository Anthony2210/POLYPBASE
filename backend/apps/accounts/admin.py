from django.contrib import admin

from .models import OrganizationMembership, UserPreference


@admin.register(OrganizationMembership)
class OrganizationMembershipAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "organization",
        "role",
        "is_responsable",
        "is_active",
        "starts_on",
        "ends_on",
    )
    list_filter = ("organization", "role", "is_responsable", "is_active")
    readonly_fields = (
        "user",
        "organization",
        "role",
        "is_responsable",
        "is_active",
        "starts_on",
        "ends_on",
    )
    search_fields = ("user__email", "user__first_name", "user__last_name", "organization__name")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(UserPreference)
class UserPreferenceAdmin(admin.ModelAdmin):
    list_display = ("user", "interface_language", "updated_at")
    list_filter = ("interface_language",)
    search_fields = ("user__email", "user__first_name", "user__last_name")
