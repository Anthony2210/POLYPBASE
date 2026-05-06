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


class Strain(models.Model):
    species = models.ForeignKey(Species, on_delete=models.PROTECT, related_name="strains")
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
            )
        ]

    def __str__(self):
        return f"{self.species} - {self.code}"
