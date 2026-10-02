import uuid

from django.db import models


class Taxon(models.Model):
    name = models.CharField(max_length=150, unique=True)
    rank = models.CharField(max_length=80, blank=True)
    parent = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="children",
    )
    worms_aphia_id = models.PositiveIntegerField(null=True, blank=True, unique=True)

    def __str__(self):
        return self.name


class Species(models.Model):
    scientific_name = models.CharField(max_length=150, unique=True)
    common_name = models.CharField(max_length=150, blank=True)
    genus_species_code = models.CharField(max_length=12, blank=True)
    taxon = models.ForeignKey(Taxon, on_delete=models.SET_NULL, null=True, blank=True)
    worms_aphia_id = models.PositiveIntegerField(null=True, blank=True, unique=True)
    is_described = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

    def __str__(self):
        return self.scientific_name


class OrganizationSpeciesCode(models.Model):
    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.PROTECT,
        related_name="species_code_assignments",
    )
    species = models.ForeignKey(
        Species,
        on_delete=models.PROTECT,
        related_name="local_code_assignments",
    )
    code = models.CharField(max_length=3)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "species"],
                name="unique_species_code_per_organization_species",
            ),
            models.UniqueConstraint(
                fields=["organization", "code"],
                name="unique_species_code_per_organization_code",
            ),
        ]

    def __str__(self):
        return f"{self.organization} - {self.species}: {self.code}"


class BiologicalProvenance(models.Model):
    name = models.CharField(max_length=150, blank=True)

    def __str__(self):
        return self.name or str(self.pk)


class OrganizationProvenanceCode(models.Model):
    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.PROTECT,
        related_name="provenance_code_assignments",
    )
    biological_provenance = models.ForeignKey(
        BiologicalProvenance,
        on_delete=models.PROTECT,
        related_name="local_code_assignments",
    )
    code = models.CharField(max_length=3)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "biological_provenance"],
                name="unique_provenance_code_per_organization_source",
            ),
            models.UniqueConstraint(
                fields=["organization", "code"],
                name="unique_provenance_code_per_organization_code",
            ),
        ]

    def __str__(self):
        return f"{self.organization} - {self.biological_provenance}: {self.code}"


class SpeciesTranslation(models.Model):
    """Localized display name and description for a species."""

    species = models.ForeignKey(
        Species,
        on_delete=models.CASCADE,
        related_name="translations",
    )
    language_code = models.CharField(max_length=10)
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["species", "language_code"],
                name="unique_species_translation_per_language",
            )
        ]
        ordering = ["language_code"]

    def __str__(self):
        return f"{self.species} [{self.language_code}]"


class Origin(models.Model):
    class SourceType(models.TextChoices):
        FIELD_COLLECTION = "field_collection", "Field collection"
        REPRODUCTION = "reproduction", "Reproduction"
        DONATION = "donation", "Donation"
        EXCHANGE = "exchange", "Exchange"
        UNKNOWN = "unknown", "Unknown"

    source_type = models.CharField(
        max_length=40,
        choices=SourceType.choices,
        default=SourceType.UNKNOWN,
    )
    event_date = models.DateField(null=True, blank=True)
    description = models.TextField(blank=True)
    origin_institution_name = models.CharField(max_length=150, blank=True)
    partner_institution = models.ForeignKey(
        "organizations.PartnerInstitution",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    technicians = models.CharField(max_length=250, blank=True)
    technique = models.CharField(max_length=250, blank=True)

    def __str__(self):
        return f"{self.get_source_type_display()} - {self.origin_institution_name or 'unknown origin'}"


class GlobalStrainIdentity(models.Model):
    global_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)

    def __str__(self):
        return str(self.global_id)


class Strain(models.Model):
    species = models.ForeignKey(Species, on_delete=models.PROTECT, related_name="strains")
    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="strains",
    )
    global_identity = models.ForeignKey(
        GlobalStrainIdentity,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="strains",
    )
    code = models.CharField(max_length=80)
    number = models.PositiveIntegerField(null=True, blank=True)
    origin_code = models.CharField(max_length=12, blank=True)
    origin = models.ForeignKey(Origin, on_delete=models.SET_NULL, null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["species", "code"],
                name="unique_strain_per_species",
            ),
            models.UniqueConstraint(
                fields=["organization", "global_identity"],
                condition=models.Q(
                    organization__isnull=False,
                    global_identity__isnull=False,
                ),
                name="unique_owned_strain_per_global_identity",
            ),
        ]

    def __str__(self):
        return f"{self.species} - {self.code}"


class LocalStrainIdentity(models.Model):
    strain = models.OneToOneField(
        Strain,
        on_delete=models.PROTECT,
        related_name="local_identity",
    )
    species_code_assignment = models.ForeignKey(
        OrganizationSpeciesCode,
        on_delete=models.PROTECT,
        related_name="local_strain_identities",
    )
    provenance_code_assignment = models.ForeignKey(
        OrganizationProvenanceCode,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="local_strain_identities",
    )


class StrainTranslation(models.Model):
    """Localized display name and description for a strain."""

    strain = models.ForeignKey(
        Strain,
        on_delete=models.CASCADE,
        related_name="translations",
    )
    language_code = models.CharField(max_length=10)
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["strain", "language_code"],
                name="unique_strain_translation_per_language",
            )
        ]
        ordering = ["language_code"]

    def __str__(self):
        return f"{self.strain} [{self.language_code}]"
