from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone


class Structure(models.Model):
    nom = models.CharField(max_length=150, unique=True)
    slug = models.SlugField(max_length=120, unique=True, null=True, blank=True)
    ville = models.CharField(max_length=120, blank=True)
    pays = models.CharField(max_length=120, blank=True)
    contact_email = models.EmailField(blank=True)
    est_active = models.BooleanField(default=True)
    commentaire = models.TextField(blank=True)

    def __str__(self):
        return self.nom


class Role(models.Model):
    nom = models.CharField(max_length=50, unique=True)
    description = models.TextField(blank=True)

    def __str__(self):
        return self.nom


class AppartenanceUtilisateur(models.Model):
    utilisateur = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    structure = models.ForeignKey(Structure, on_delete=models.CASCADE)
    role = models.ForeignKey(Role, on_delete=models.PROTECT)
    est_active = models.BooleanField(default=True)
    date_debut = models.DateField(default=timezone.localdate)
    date_fin = models.DateField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["utilisateur", "structure"],
                name="unique_utilisateur_structure",
            )
        ]

    def __str__(self):
        return f"{self.utilisateur} - {self.structure} - {self.role}"


class Taxon(models.Model):
    nom = models.CharField(max_length=150, unique=True)
    rang = models.CharField(max_length=80, blank=True)
    parent = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="enfants",
    )
    worms_aphia_id = models.PositiveIntegerField(null=True, blank=True, unique=True)

    def __str__(self):
        return self.nom


class Espece(models.Model):
    nom_scientifique = models.CharField(max_length=150, unique=True)
    nom_commun = models.CharField(max_length=150, blank=True)
    code_genre_espece = models.CharField(max_length=12, blank=True)
    taxon = models.ForeignKey(Taxon, on_delete=models.SET_NULL, null=True, blank=True)
    worms_aphia_id = models.PositiveIntegerField(null=True, blank=True, unique=True)
    est_decrite = models.BooleanField(default=True)
    commentaire = models.TextField(blank=True)

    def __str__(self):
        return self.nom_scientifique


class InstitutionPartenaire(models.Model):
    nom = models.CharField(max_length=180, unique=True)
    ville = models.CharField(max_length=120, blank=True)
    pays = models.CharField(max_length=120, blank=True)
    contact_nom = models.CharField(max_length=150, blank=True)
    contact_email = models.EmailField(blank=True)
    commentaire = models.TextField(blank=True)

    def __str__(self):
        return self.nom


class Provenance(models.Model):
    TYPE_PROVENANCE_CHOICES = [
        ("prelevement", "Prélèvement"),
        ("reproduction", "Reproduction"),
        ("don", "Don"),
        ("echange", "Échange"),
        ("inconnue", "Inconnue"),
    ]

    type_provenance = models.CharField(
        max_length=30,
        choices=TYPE_PROVENANCE_CHOICES,
        default="inconnue",
    )
    date_evenement = models.DateField(null=True, blank=True)
    description = models.TextField(blank=True)
    institution_origine = models.CharField(max_length=150, blank=True)
    institution_partenaire = models.ForeignKey(
        InstitutionPartenaire,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    techniciens = models.CharField(max_length=250, blank=True)
    technique = models.CharField(max_length=250, blank=True)

    def __str__(self):
        return f"{self.get_type_provenance_display()} - {self.institution_origine or 'origine non précisée'}"


class Souche(models.Model):
    espece = models.ForeignKey(Espece, on_delete=models.PROTECT)
    code = models.CharField(max_length=80)
    numero = models.PositiveIntegerField(null=True, blank=True)
    code_provenance = models.CharField(max_length=12, blank=True)
    provenance = models.ForeignKey(Provenance, on_delete=models.SET_NULL, null=True, blank=True)
    commentaire = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["espece", "code"],
                name="unique_souche_par_espece",
            )
        ]

    def __str__(self):
        return f"{self.espece} - {self.code}"


class ZoneThermique(models.Model):
    TYPE_ZONE_CHOICES = [
        ("armoire", "Armoire"),
        ("etuve", "Étuve"),
        ("bac", "Bac"),
        ("autre", "Autre"),
    ]

    structure = models.ForeignKey(Structure, on_delete=models.CASCADE)
    nom = models.CharField(max_length=120)
    type_zone = models.CharField(max_length=30, choices=TYPE_ZONE_CHOICES, default="armoire")
    temperature_consigne = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    est_active = models.BooleanField(default=True)
    commentaire = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["structure", "nom"],
                name="unique_zone_par_structure",
            )
        ]

    def __str__(self):
        return f"{self.nom} ({self.structure})"


class Boite(models.Model):
    STATUT_CHOICES = [
        ("active", "Active"),
        ("archivee", "Archivée"),
        ("perdue", "Perdue"),
        ("arretee", "Arrêtée"),
    ]

    structure = models.ForeignKey(Structure, on_delete=models.PROTECT)
    global_code = models.CharField(max_length=100, unique=True)
    code_local = models.CharField(max_length=100, blank=True)
    numero_boite = models.CharField(max_length=80)
    souche = models.ForeignKey(Souche, on_delete=models.PROTECT)
    provenance = models.ForeignKey(Provenance, on_delete=models.SET_NULL, null=True, blank=True)
    zone_thermique = models.ForeignKey(
        ZoneThermique,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    statut = models.CharField(max_length=30, choices=STATUT_CHOICES, default="active")
    date_creation = models.DateField(auto_now_add=True)
    date_entree = models.DateField(null=True, blank=True)
    volume_litres = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    raison_arret = models.CharField(max_length=250, blank=True)
    commentaire = models.TextField(blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["structure", "statut"]),
            models.Index(fields=["global_code"]),
            models.Index(fields=["code_local"]),
        ]

    def __str__(self):
        return f"{self.global_code} - {self.numero_boite}"


class ReleveBiologique(models.Model):
    ETAT_CULTURE_CHOICES = [
        ("bon", "Bon"),
        ("moyen", "Moyen"),
        ("mauvais", "Mauvais"),
        ("mort", "Mort"),
        ("non_precise", "Non précisé"),
    ]

    boite = models.ForeignKey(Boite, on_delete=models.CASCADE, related_name="releves_biologiques")
    date_releve = models.DateField()
    nombre_polypes = models.PositiveIntegerField(default=0)
    nombre_ephyres = models.PositiveIntegerField(default=0)
    nombre_strobiles = models.PositiveIntegerField(default=0)
    etat_culture = models.CharField(
        max_length=30,
        choices=ETAT_CULTURE_CHOICES,
        default="non_precise",
    )
    vigilance = models.BooleanField(default=False)
    commentaire = models.TextField(blank=True)
    utilisateur = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    date_saisie = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date_releve", "-date_saisie"]
        indexes = [
            models.Index(fields=["boite", "date_releve"]),
        ]

    def __str__(self):
        return f"{self.boite} - {self.date_releve}"


class Observation(models.Model):
    TYPE_OBSERVATION_CHOICES = [
        ("generale", "Générale"),
        ("contamination", "Contamination"),
        ("repiquage_a_prevoir", "Repiquage à prévoir"),
        ("mortalite", "Mortalité"),
        ("autre", "Autre"),
    ]

    boite = models.ForeignKey(Boite, on_delete=models.CASCADE, related_name="observations")
    date_observation = models.DateField(default=timezone.localdate)
    type_observation = models.CharField(
        max_length=40,
        choices=TYPE_OBSERVATION_CHOICES,
        default="generale",
    )
    commentaire = models.TextField()
    utilisateur = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    date_saisie = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date_observation", "-date_saisie"]

    def __str__(self):
        return f"{self.boite} - {self.get_type_observation_display()}"


class Sonde(models.Model):
    TYPE_SONDE_CHOICES = [
        ("lorawan", "LoRaWAN"),
        ("iminilide", "iMinilide"),
        ("manuel", "Saisie manuelle"),
        ("autre", "Autre"),
    ]

    structure = models.ForeignKey(Structure, on_delete=models.CASCADE)
    zone_thermique = models.ForeignKey(
        ZoneThermique,
        on_delete=models.CASCADE,
        related_name="sondes",
    )
    code = models.CharField(max_length=120)
    type_sonde = models.CharField(max_length=30, choices=TYPE_SONDE_CHOICES, default="autre")
    emplacement = models.CharField(max_length=180, blank=True)
    est_active = models.BooleanField(default=True)
    commentaire = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["structure", "code"],
                name="unique_sonde_par_structure",
            )
        ]

    def __str__(self):
        return f"{self.code} - {self.zone_thermique}"


class MesureTemperature(models.Model):
    sonde = models.ForeignKey(Sonde, on_delete=models.CASCADE, related_name="mesures")
    date_mesure = models.DateTimeField()
    temperature_c = models.DecimalField(max_digits=5, decimal_places=2)
    source = models.CharField(max_length=80, blank=True)
    donnees_brutes = models.JSONField(default=dict, blank=True)
    utilisateur = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    date_import = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date_mesure"]
        constraints = [
            models.UniqueConstraint(
                fields=["sonde", "date_mesure"],
                name="unique_mesure_temperature_par_sonde_date",
            )
        ]
        indexes = [
            models.Index(fields=["date_mesure"]),
        ]

    def __str__(self):
        return f"{self.sonde} - {self.date_mesure:%Y-%m-%d %H:%M}"


class TemperatureJournaliere(models.Model):
    zone_thermique = models.ForeignKey(
        ZoneThermique,
        on_delete=models.CASCADE,
        related_name="temperatures_journalieres",
    )
    date = models.DateField()
    temperature_min = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    temperature_moyenne = models.DecimalField(max_digits=5, decimal_places=2)
    temperature_max = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    nombre_mesures = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-date"]
        constraints = [
            models.UniqueConstraint(
                fields=["zone_thermique", "date"],
                name="unique_temperature_journaliere_par_zone_date",
            )
        ]

    def __str__(self):
        return f"{self.zone_thermique} - {self.date}"


class ReleveSalinite(models.Model):
    zone_thermique = models.ForeignKey(
        ZoneThermique,
        on_delete=models.CASCADE,
        related_name="releves_salinite",
    )
    date_releve = models.DateField()
    salinite_psu = models.DecimalField(max_digits=5, decimal_places=2)
    utilisateur = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    commentaire = models.TextField(blank=True)
    date_saisie = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date_releve"]
        constraints = [
            models.UniqueConstraint(
                fields=["zone_thermique", "date_releve"],
                name="unique_salinite_par_zone_date",
            )
        ]

    def __str__(self):
        return f"{self.zone_thermique} - {self.date_releve}"


class LocalisationBoite(models.Model):
    boite = models.ForeignKey(Boite, on_delete=models.CASCADE, related_name="localisations")
    zone_thermique = models.ForeignKey(ZoneThermique, on_delete=models.PROTECT)
    date_debut = models.DateTimeField(default=timezone.now)
    date_fin = models.DateTimeField(null=True, blank=True)
    commentaire = models.TextField(blank=True)

    class Meta:
        ordering = ["-date_debut"]
        indexes = [
            models.Index(fields=["boite", "date_debut"]),
            models.Index(fields=["zone_thermique", "date_debut"]),
        ]

    def clean(self):
        if self.date_fin and self.date_fin <= self.date_debut:
            raise ValidationError("La date de fin doit être postérieure à la date de début.")

    def __str__(self):
        return f"{self.boite} dans {self.zone_thermique}"


class MouvementBoite(models.Model):
    boite = models.ForeignKey(Boite, on_delete=models.CASCADE, related_name="mouvements")
    zone_depart = models.ForeignKey(
        ZoneThermique,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="mouvements_depart",
    )
    zone_arrivee = models.ForeignKey(
        ZoneThermique,
        on_delete=models.PROTECT,
        related_name="mouvements_arrivee",
    )
    date_mouvement = models.DateTimeField(default=timezone.now)
    utilisateur = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    commentaire = models.TextField(blank=True)

    class Meta:
        ordering = ["-date_mouvement"]

    def __str__(self):
        return f"{self.boite} vers {self.zone_arrivee}"


class Repiquage(models.Model):
    boite_parent = models.ForeignKey(
        Boite,
        on_delete=models.PROTECT,
        related_name="repiquages_sources",
    )
    date_repiquage = models.DateField(default=timezone.localdate)
    utilisateur = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    motif = models.CharField(max_length=180, blank=True)
    commentaire = models.TextField(blank=True)

    class Meta:
        ordering = ["-date_repiquage"]

    def __str__(self):
        return f"Repiquage de {self.boite_parent} le {self.date_repiquage}"


class ParenteBoite(models.Model):
    TYPE_LIEN_CHOICES = [
        ("repiquage", "Repiquage"),
        ("reproduction", "Reproduction sexuée"),
        ("import_historique", "Import historique"),
        ("autre", "Autre"),
    ]

    boite_parent = models.ForeignKey(
        Boite,
        on_delete=models.PROTECT,
        related_name="liens_enfants",
    )
    boite_enfant = models.ForeignKey(
        Boite,
        on_delete=models.PROTECT,
        related_name="liens_parents",
    )
    repiquage = models.ForeignKey(
        Repiquage,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="liens_parentes",
    )
    type_lien = models.CharField(max_length=40, choices=TYPE_LIEN_CHOICES, default="repiquage")
    commentaire = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["boite_parent", "boite_enfant"],
                name="unique_lien_parente_boite",
            ),
            models.CheckConstraint(
                condition=~Q(boite_parent=models.F("boite_enfant")),
                name="parente_boite_parent_different_enfant",
            ),
        ]

    def __str__(self):
        return f"{self.boite_parent} -> {self.boite_enfant}"


class TagIdentification(models.Model):
    TYPE_TAG_CHOICES = [
        ("qr", "QR code"),
        ("nfc", "NFC"),
        ("rfid", "RFID"),
    ]

    type_tag = models.CharField(max_length=20, choices=TYPE_TAG_CHOICES, default="qr")
    code = models.CharField(max_length=160, unique=True)
    url = models.URLField(blank=True)
    boite = models.ForeignKey(
        Boite,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="tags",
    )
    zone_thermique = models.ForeignKey(
        ZoneThermique,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="tags",
    )
    est_actif = models.BooleanField(default=True)
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(boite__isnull=False, zone_thermique__isnull=True)
                    | Q(boite__isnull=True, zone_thermique__isnull=False)
                ),
                name="tag_cible_unique_boite_ou_zone",
            )
        ]

    def __str__(self):
        return f"{self.get_type_tag_display()} {self.code}"


class Alerte(models.Model):
    TYPE_ALERTE_CHOICES = [
        ("biologique", "Biologique"),
        ("temperature", "Température"),
        ("salinite", "Salinité"),
        ("repiquage", "Repiquage"),
        ("autre", "Autre"),
    ]
    NIVEAU_CHOICES = [
        ("info", "Information"),
        ("warning", "Vigilance"),
        ("critical", "Critique"),
    ]

    structure = models.ForeignKey(Structure, on_delete=models.CASCADE)
    boite = models.ForeignKey(Boite, on_delete=models.CASCADE, null=True, blank=True, related_name="alertes")
    zone_thermique = models.ForeignKey(
        ZoneThermique,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="alertes",
    )
    type_alerte = models.CharField(max_length=40, choices=TYPE_ALERTE_CHOICES)
    niveau = models.CharField(max_length=20, choices=NIVEAU_CHOICES, default="warning")
    message = models.CharField(max_length=250)
    date_creation = models.DateTimeField(auto_now_add=True)
    date_resolution = models.DateTimeField(null=True, blank=True)
    cree_par = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="alertes_creees",
    )
    resolue_par = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="alertes_resolues",
    )

    class Meta:
        ordering = ["-date_creation"]
        indexes = [
            models.Index(fields=["structure", "type_alerte", "date_creation"]),
        ]

    @property
    def est_active(self):
        return self.date_resolution is None

    def __str__(self):
        return self.message


class AnomalieThermique(models.Model):
    zone_thermique = models.ForeignKey(
        ZoneThermique,
        on_delete=models.CASCADE,
        related_name="anomalies_thermiques",
    )
    date_debut = models.DateTimeField()
    date_fin = models.DateTimeField(null=True, blank=True)
    temperature_reference = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    ecart_max = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    niveau = models.CharField(max_length=20, choices=Alerte.NIVEAU_CHOICES, default="warning")
    commentaire = models.TextField(blank=True)

    class Meta:
        ordering = ["-date_debut"]

    def __str__(self):
        return f"{self.zone_thermique} - anomalie {self.date_debut:%Y-%m-%d}"


class JournalAction(models.Model):
    ACTION_CHOICES = [
        ("creation", "Création"),
        ("modification", "Modification"),
        ("archivage", "Archivage"),
        ("saisie", "Saisie"),
        ("repiquage", "Repiquage"),
        ("transfert", "Transfert"),
        ("import", "Import"),
        ("export", "Export"),
        ("scan", "Scan"),
        ("connexion", "Connexion"),
    ]

    structure = models.ForeignKey(Structure, on_delete=models.CASCADE, null=True, blank=True)
    utilisateur = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    action = models.CharField(max_length=40, choices=ACTION_CHOICES)
    objet_type = models.CharField(max_length=80, blank=True)
    objet_id = models.CharField(max_length=120, blank=True)
    description = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    date_action = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date_action"]
        indexes = [
            models.Index(fields=["structure", "date_action"]),
            models.Index(fields=["action", "date_action"]),
        ]

    def __str__(self):
        return f"{self.get_action_display()} - {self.date_action:%Y-%m-%d %H:%M}"


class ImportExcel(models.Model):
    STATUT_CHOICES = [
        ("brouillon", "Brouillon"),
        ("valide", "Validé"),
        ("importe", "Importé"),
        ("erreur", "Erreur"),
    ]

    structure = models.ForeignKey(Structure, on_delete=models.CASCADE)
    nom_fichier = models.CharField(max_length=220)
    statut = models.CharField(max_length=30, choices=STATUT_CHOICES, default="brouillon")
    utilisateur = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    date_import = models.DateTimeField(auto_now_add=True)
    commentaire = models.TextField(blank=True)

    class Meta:
        ordering = ["-date_import"]

    def __str__(self):
        return self.nom_fichier


class LigneImportExcel(models.Model):
    import_excel = models.ForeignKey(ImportExcel, on_delete=models.CASCADE, related_name="lignes")
    numero_ligne = models.PositiveIntegerField()
    donnees_brutes = models.JSONField(default=dict)
    est_valide = models.BooleanField(default=False)
    erreurs = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["numero_ligne"]
        constraints = [
            models.UniqueConstraint(
                fields=["import_excel", "numero_ligne"],
                name="unique_ligne_par_import_excel",
            )
        ]

    def __str__(self):
        return f"{self.import_excel} ligne {self.numero_ligne}"


class ExportDonnees(models.Model):
    FORMAT_CHOICES = [
        ("csv", "CSV"),
        ("xlsx", "XLSX"),
    ]
    TYPE_EXPORT_CHOICES = [
        ("inventaire", "Inventaire"),
        ("releves", "Relevés biologiques"),
        ("temperatures", "Températures"),
        ("reglementaire", "Réglementaire"),
        ("taxonomie", "Taxonomie"),
    ]

    structure = models.ForeignKey(Structure, on_delete=models.CASCADE)
    type_export = models.CharField(max_length=40, choices=TYPE_EXPORT_CHOICES)
    format_export = models.CharField(max_length=10, choices=FORMAT_CHOICES)
    filtres = models.JSONField(default=dict, blank=True)
    nom_fichier = models.CharField(max_length=220, blank=True)
    utilisateur = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    date_export = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date_export"]

    def __str__(self):
        return f"{self.get_type_export_display()} {self.date_export:%Y-%m-%d}"
