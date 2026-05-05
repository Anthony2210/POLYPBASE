from django.contrib import admin

from .models import (
    Alerte,
    AnomalieThermique,
    AppartenanceUtilisateur,
    Boite,
    Espece,
    ExportDonnees,
    ImportExcel,
    InstitutionPartenaire,
    JournalAction,
    LigneImportExcel,
    LocalisationBoite,
    MesureTemperature,
    MouvementBoite,
    Observation,
    ParenteBoite,
    Provenance,
    ReleveBiologique,
    ReleveSalinite,
    Repiquage,
    Role,
    Sonde,
    Souche,
    Structure,
    TagIdentification,
    Taxon,
    TemperatureJournaliere,
    ZoneThermique,
)


@admin.register(Structure)
class StructureAdmin(admin.ModelAdmin):
    list_display = ("nom", "slug", "ville", "pays", "est_active")
    list_filter = ("est_active", "pays")
    search_fields = ("nom", "slug", "ville", "pays")


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ("nom",)
    search_fields = ("nom",)


@admin.register(AppartenanceUtilisateur)
class AppartenanceUtilisateurAdmin(admin.ModelAdmin):
    list_display = ("utilisateur", "structure", "role", "est_active", "date_debut", "date_fin")
    list_filter = ("structure", "role", "est_active")
    search_fields = ("utilisateur__username", "structure__nom", "role__nom")


@admin.register(Taxon)
class TaxonAdmin(admin.ModelAdmin):
    list_display = ("nom", "rang", "parent", "worms_aphia_id")
    list_filter = ("rang",)
    search_fields = ("nom", "rang", "worms_aphia_id")


@admin.register(Espece)
class EspeceAdmin(admin.ModelAdmin):
    list_display = ("nom_scientifique", "nom_commun", "code_genre_espece", "taxon", "worms_aphia_id", "est_decrite")
    list_filter = ("est_decrite",)
    search_fields = ("nom_scientifique", "nom_commun", "code_genre_espece", "worms_aphia_id")


@admin.register(InstitutionPartenaire)
class InstitutionPartenaireAdmin(admin.ModelAdmin):
    list_display = ("nom", "ville", "pays", "contact_nom", "contact_email")
    search_fields = ("nom", "ville", "pays", "contact_nom", "contact_email")


@admin.register(Provenance)
class ProvenanceAdmin(admin.ModelAdmin):
    list_display = ("type_provenance", "institution_origine", "institution_partenaire", "date_evenement")
    list_filter = ("type_provenance", "date_evenement")
    search_fields = ("institution_origine", "institution_partenaire__nom", "description", "techniciens")


@admin.register(Souche)
class SoucheAdmin(admin.ModelAdmin):
    list_display = ("code", "numero", "code_provenance", "espece", "provenance")
    list_filter = ("espece",)
    search_fields = ("code", "code_provenance", "espece__nom_scientifique")


@admin.register(ZoneThermique)
class ZoneThermiqueAdmin(admin.ModelAdmin):
    list_display = ("nom", "structure", "type_zone", "temperature_consigne", "est_active")
    list_filter = ("structure", "type_zone", "est_active")
    search_fields = ("nom",)


@admin.register(Boite)
class BoiteAdmin(admin.ModelAdmin):
    list_display = ("global_code", "code_local", "numero_boite", "souche", "zone_thermique", "statut", "date_entree")
    list_filter = ("statut", "structure", "zone_thermique")
    search_fields = ("global_code", "code_local", "numero_boite", "souche__code", "souche__espece__nom_scientifique")


@admin.register(ReleveBiologique)
class ReleveBiologiqueAdmin(admin.ModelAdmin):
    list_display = (
        "boite",
        "date_releve",
        "nombre_polypes",
        "nombre_ephyres",
        "nombre_strobiles",
        "etat_culture",
        "vigilance",
        "utilisateur",
    )
    list_filter = ("etat_culture", "vigilance", "date_releve")
    search_fields = ("boite__global_code", "boite__numero_boite")


@admin.register(Observation)
class ObservationAdmin(admin.ModelAdmin):
    list_display = ("boite", "date_observation", "type_observation", "utilisateur")
    list_filter = ("type_observation", "date_observation")
    search_fields = ("boite__global_code", "commentaire")


@admin.register(Sonde)
class SondeAdmin(admin.ModelAdmin):
    list_display = ("code", "structure", "zone_thermique", "type_sonde", "est_active")
    list_filter = ("structure", "type_sonde", "est_active")
    search_fields = ("code", "zone_thermique__nom")


@admin.register(MesureTemperature)
class MesureTemperatureAdmin(admin.ModelAdmin):
    list_display = ("sonde", "date_mesure", "temperature_c", "source")
    list_filter = ("sonde__zone_thermique", "source")
    date_hierarchy = "date_mesure"
    search_fields = ("sonde__code",)


@admin.register(TemperatureJournaliere)
class TemperatureJournaliereAdmin(admin.ModelAdmin):
    list_display = ("zone_thermique", "date", "temperature_min", "temperature_moyenne", "temperature_max", "nombre_mesures")
    list_filter = ("zone_thermique",)
    date_hierarchy = "date"


@admin.register(ReleveSalinite)
class ReleveSaliniteAdmin(admin.ModelAdmin):
    list_display = ("zone_thermique", "date_releve", "salinite_psu", "utilisateur")
    list_filter = ("zone_thermique", "date_releve")
    search_fields = ("zone_thermique__nom",)


@admin.register(LocalisationBoite)
class LocalisationBoiteAdmin(admin.ModelAdmin):
    list_display = ("boite", "zone_thermique", "date_debut", "date_fin")
    list_filter = ("zone_thermique",)
    search_fields = ("boite__global_code", "zone_thermique__nom")


@admin.register(MouvementBoite)
class MouvementBoiteAdmin(admin.ModelAdmin):
    list_display = ("boite", "zone_depart", "zone_arrivee", "date_mouvement", "utilisateur")
    list_filter = ("zone_depart", "zone_arrivee")
    search_fields = ("boite__global_code", "commentaire")


class ParenteBoiteInline(admin.TabularInline):
    model = ParenteBoite
    fk_name = "repiquage"
    extra = 0


@admin.register(Repiquage)
class RepiquageAdmin(admin.ModelAdmin):
    list_display = ("boite_parent", "date_repiquage", "utilisateur", "motif")
    list_filter = ("date_repiquage",)
    search_fields = ("boite_parent__global_code", "motif", "commentaire")
    inlines = (ParenteBoiteInline,)


@admin.register(ParenteBoite)
class ParenteBoiteAdmin(admin.ModelAdmin):
    list_display = ("boite_parent", "boite_enfant", "type_lien", "repiquage")
    list_filter = ("type_lien",)
    search_fields = ("boite_parent__global_code", "boite_enfant__global_code")


@admin.register(TagIdentification)
class TagIdentificationAdmin(admin.ModelAdmin):
    list_display = ("code", "type_tag", "boite", "zone_thermique", "est_actif")
    list_filter = ("type_tag", "est_actif")
    search_fields = ("code", "boite__global_code", "zone_thermique__nom")


@admin.register(Alerte)
class AlerteAdmin(admin.ModelAdmin):
    list_display = ("message", "structure", "type_alerte", "niveau", "boite", "zone_thermique", "date_creation", "date_resolution")
    list_filter = ("structure", "type_alerte", "niveau", "date_resolution")
    search_fields = ("message", "boite__global_code", "zone_thermique__nom")


@admin.register(AnomalieThermique)
class AnomalieThermiqueAdmin(admin.ModelAdmin):
    list_display = ("zone_thermique", "date_debut", "date_fin", "temperature_reference", "ecart_max", "niveau")
    list_filter = ("zone_thermique", "niveau")
    date_hierarchy = "date_debut"


@admin.register(JournalAction)
class JournalActionAdmin(admin.ModelAdmin):
    list_display = ("date_action", "structure", "utilisateur", "action", "objet_type", "objet_id")
    list_filter = ("structure", "action")
    search_fields = ("description", "objet_type", "objet_id", "utilisateur__username")
    date_hierarchy = "date_action"


class LigneImportExcelInline(admin.TabularInline):
    model = LigneImportExcel
    extra = 0
    readonly_fields = ("numero_ligne", "donnees_brutes", "est_valide", "erreurs")


@admin.register(ImportExcel)
class ImportExcelAdmin(admin.ModelAdmin):
    list_display = ("nom_fichier", "structure", "statut", "utilisateur", "date_import")
    list_filter = ("structure", "statut")
    search_fields = ("nom_fichier", "commentaire")
    inlines = (LigneImportExcelInline,)


@admin.register(LigneImportExcel)
class LigneImportExcelAdmin(admin.ModelAdmin):
    list_display = ("import_excel", "numero_ligne", "est_valide")
    list_filter = ("est_valide",)
    search_fields = ("import_excel__nom_fichier",)


@admin.register(ExportDonnees)
class ExportDonneesAdmin(admin.ModelAdmin):
    list_display = ("type_export", "format_export", "structure", "utilisateur", "date_export", "nom_fichier")
    list_filter = ("structure", "type_export", "format_export")
    search_fields = ("nom_fichier", "utilisateur__username")
