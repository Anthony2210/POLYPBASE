from django.conf import settings
from django.db import models


class Structure(models.Model):
    nom = models.CharField(max_length=150, unique=True)
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

    def __str__(self):
        return self.nom


class Espece(models.Model):
    nom_scientifique = models.CharField(max_length=150, unique=True)
    nom_commun = models.CharField(max_length=150, blank=True)
    taxon = models.ForeignKey(Taxon, on_delete=models.SET_NULL, null=True, blank=True)

    def __str__(self):
        return self.nom_scientifique


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
    description = models.TextField(blank=True)
    institution_origine = models.CharField(max_length=150, blank=True)

    def __str__(self):
        return f"{self.get_type_provenance_display()} - {self.institution_origine or 'origine non précisée'}"


class Souche(models.Model):
    espece = models.ForeignKey(Espece, on_delete=models.PROTECT)
    code = models.CharField(max_length=80)
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
    zone_thermique = models.ForeignKey(
        ZoneThermique,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    statut = models.CharField(max_length=30, choices=STATUT_CHOICES, default="active")
    date_creation = models.DateField(auto_now_add=True)
    commentaire = models.TextField(blank=True)

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
    etat_culture = models.CharField(
        max_length=30,
        choices=ETAT_CULTURE_CHOICES,
        default="non_precise",
    )
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

    def __str__(self):
        return f"{self.boite} - {self.date_releve}"