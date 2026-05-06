from django.contrib import admin

from .models import Organization, PartnerInstitution, SharingAgreement


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "city", "country", "is_active")
    list_filter = ("is_active", "country")
    search_fields = ("name", "slug", "city", "country")


@admin.register(PartnerInstitution)
class PartnerInstitutionAdmin(admin.ModelAdmin):
    list_display = ("name", "city", "country", "contact_name", "contact_email")
    search_fields = ("name", "city", "country", "contact_name", "contact_email")


@admin.register(SharingAgreement)
class SharingAgreementAdmin(admin.ModelAdmin):
    list_display = ("owner_organization", "partner_organization", "status", "starts_on", "ends_on")
    list_filter = ("status", "owner_organization", "partner_organization")
    search_fields = ("owner_organization__name", "partner_organization__name")
