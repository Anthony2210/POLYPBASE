from django.contrib import admin

from .models import (
    AppartenanceUtilisateur,
    Boite,
    Espece,
    Provenance,
    ReleveBiologique,
    Role,
    Souche,
    Structure,
    Taxon,
    ZoneThermique,
)


@admin.register(Structure)
class StructureAdmin(admin.ModelAdmin):
    list_display = ("nom",)
    search_fields = ("nom",)


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ("nom",)
    search_fields = ("nom",)


@admin.register(AppartenanceUtilisateur)
class AppartenanceUtilisateurAdmin(admin.ModelAdmin):
    list_display = ("utilisateur", "structure", "role")
    list_filter = ("structure", "role")
    search_fields = ("utilisateur__username", "structure__nom", "role__nom")


@admin.register(Taxon)
class TaxonAdmin(admin.ModelAdmin):
    list_display = ("nom", "rang")
    search_fields = ("nom", "rang")


@admin.register(Espece)
class EspeceAdmin(admin.ModelAdmin):
    list_display = ("nom_scientifique", "nom_commun", "taxon")
    search_fields = ("nom_scientifique", "nom_commun")


@admin.register(Provenance)
class ProvenanceAdmin(admin.ModelAdmin):
    list_display = ("type_provenance", "institution_origine")
    list_filter = ("type_provenance",)
    search_fields = ("institution_origine", "description")


@admin.register(Souche)
class SoucheAdmin(admin.ModelAdmin):
    list_display = ("code", "espece", "provenance")
    list_filter = ("espece",)
    search_fields = ("code", "espece__nom_scientifique")


@admin.register(ZoneThermique)
class ZoneThermiqueAdmin(admin.ModelAdmin):
    list_display = ("nom", "structure", "type_zone", "temperature_consigne")
    list_filter = ("structure", "type_zone")
    search_fields = ("nom",)


@admin.register(Boite)
class BoiteAdmin(admin.ModelAdmin):
    list_display = ("global_code", "code_local", "numero_boite", "souche", "zone_thermique", "statut")
    list_filter = ("statut", "structure", "zone_thermique")
    search_fields = ("global_code", "code_local", "numero_boite", "souche__code")


@admin.register(ReleveBiologique)
class ReleveBiologiqueAdmin(admin.ModelAdmin):
    list_display = (
        "boite",
        "date_releve",
        "nombre_polypes",
        "nombre_ephyres",
        "etat_culture",
        "utilisateur",
    )
    list_filter = ("etat_culture", "date_releve")
    search_fields = ("boite__global_code", "boite__numero_boite")