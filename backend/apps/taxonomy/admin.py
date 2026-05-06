from django.contrib import admin

from .models import Origin, Species, Strain, Taxon


@admin.register(Taxon)
class TaxonAdmin(admin.ModelAdmin):
    list_display = ("name", "rank", "parent", "worms_aphia_id")
    list_filter = ("rank",)
    search_fields = ("name", "rank", "worms_aphia_id")


@admin.register(Species)
class SpeciesAdmin(admin.ModelAdmin):
    list_display = ("scientific_name", "common_name", "genus_species_code", "taxon", "worms_aphia_id", "is_described")
    list_filter = ("is_described",)
    search_fields = ("scientific_name", "common_name", "genus_species_code", "worms_aphia_id")


@admin.register(Origin)
class OriginAdmin(admin.ModelAdmin):
    list_display = ("source_type", "origin_institution_name", "partner_institution", "event_date")
    list_filter = ("source_type", "event_date")
    search_fields = ("origin_institution_name", "partner_institution__name", "description", "technicians")


@admin.register(Strain)
class StrainAdmin(admin.ModelAdmin):
    list_display = ("code", "number", "origin_code", "species", "origin")
    list_filter = ("species",)
    search_fields = ("code", "origin_code", "species__scientific_name")
