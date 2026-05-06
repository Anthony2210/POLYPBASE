from django.contrib import admin

from .models import OrganizationMembership, UserPreference


@admin.register(OrganizationMembership)
class OrganizationMembershipAdmin(admin.ModelAdmin):
    list_display = ("user", "organization", "role", "is_active", "starts_on", "ends_on")
    list_filter = ("organization", "role", "is_active")
    search_fields = ("user__username", "organization__name")


@admin.register(UserPreference)
class UserPreferenceAdmin(admin.ModelAdmin):
    list_display = ("user", "interface_language", "updated_at")
    list_filter = ("interface_language",)
    search_fields = ("user__username", "user__email")
