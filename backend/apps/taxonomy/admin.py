from django.contrib import admin

from .models import (
    Origin,
    Species,
    SpeciesTranslation,
    Strain,
    StrainTranslation,
    Taxon,
)


class SuperuserOnlyWriteMixin:
    """Shared references are visible to every institution; staff status is not authority."""

    def has_add_permission(self, request, *args):
        return request.user.is_superuser and super().has_add_permission(request, *args)

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser and super().has_delete_permission(request, obj)


class SpeciesTranslationInline(SuperuserOnlyWriteMixin, admin.TabularInline):
    model = SpeciesTranslation
    extra = 0


class StrainTranslationInline(admin.TabularInline):
    model = StrainTranslation
    extra = 0


@admin.register(Taxon)
class TaxonAdmin(admin.ModelAdmin):
    list_display = ("name", "rank", "parent", "worms_aphia_id")
    list_filter = ("rank",)
    search_fields = ("name", "rank", "worms_aphia_id")


@admin.register(Species)
class SpeciesAdmin(SuperuserOnlyWriteMixin, admin.ModelAdmin):
    list_display = ("scientific_name", "common_name", "genus_species_code", "taxon", "worms_aphia_id", "is_described")
    list_filter = ("is_described",)
    search_fields = ("scientific_name", "common_name", "genus_species_code", "worms_aphia_id")
    inlines = (SpeciesTranslationInline,)

    def get_readonly_fields(self, request, obj=None):
        # The technical reference of existing Strains, Boxes and transfers keeps
        # its name and AphiaID; institutional corrections use their own service.
        readonly = tuple(super().get_readonly_fields(request, obj))
        return readonly + ("scientific_name", "worms_aphia_id") if obj is not None else readonly


@admin.register(Origin)
class OriginAdmin(admin.ModelAdmin):
    list_display = ("source_type", "origin_institution_name", "partner_institution", "event_date")
    list_filter = ("source_type", "event_date")
    search_fields = ("origin_institution_name", "partner_institution__name", "description", "technicians")


@admin.register(Strain)
class StrainAdmin(admin.ModelAdmin):
    # Institution ownership is not inferred from Django staff membership.
    def has_add_permission(self, request):
        return request.user.is_superuser and super().has_add_permission(request)

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser and super().has_delete_permission(request, obj)

    list_display = ("code", "number", "origin_code", "species", "origin")
    list_filter = ("species",)
    search_fields = ("code", "origin_code", "species__scientific_name")
    inlines = (StrainTranslationInline,)
