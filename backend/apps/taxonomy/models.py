import uuid

from django.conf import settings
from django.db import models
from django.db.models.functions import Lower
from django.db.models.lookups import Exact

from .species_names import ScientificNameKey


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

    class Meta:
        constraints = [
            # Scientific identity ignores letter case; exact uniqueness stays on the field.
            models.UniqueConstraint(
                Lower("scientific_name"),
                name="unique_species_scientific_name_ci",
            ),
            models.UniqueConstraint(
                ScientificNameKey("scientific_name"),
                name="unique_species_scientific_name_normalized",
            ),
        ]

    def __str__(self):
        return self.scientific_name


class InstitutionSpeciesQuerySet(models.QuerySet):
    def for_organization(self, organization):
        if organization is None or organization.pk is None:
            return self.none()
        return self.filter(organization_id=organization.pk)

    def compare_and_swap(self, *, organization, pk, expected_revision, scientific_name, worms_aphia_id):
        """Internal persistence primitive, not an authorized editing workflow.

        The future caller must authorize, validate WoRMS and audit in its own
        transaction. A stale revision or foreign institution changes zero rows.
        No reference, code assignment or historical identity is modified.
        """
        if type(expected_revision) is not int or expected_revision < 1:
            raise ValueError("A positive expected revision is required.")
        return self.for_organization(organization).filter(pk=pk, revision=expected_revision).update(
            scientific_name=scientific_name,
            worms_aphia_id=worms_aphia_id,
            revision=models.F("revision") + 1,
        )


class InstitutionSpecies(models.Model):
    """Inactive foundation for institution-owned information, not a new identity."""

    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.PROTECT,
        related_name="institution_species",
    )
    species = models.ForeignKey(
        Species, on_delete=models.PROTECT, related_name="institution_information",
    )
    scientific_name = models.CharField(max_length=150)
    worms_aphia_id = models.PositiveIntegerField(null=True, blank=True)
    revision = models.PositiveBigIntegerField(default=1, editable=False)

    objects = InstitutionSpeciesQuerySet.as_manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "species"], name="unique_institution_species_reference",
            ),
            models.UniqueConstraint(
                "organization", ScientificNameKey("scientific_name"),
                name="unique_institution_species_name",
            ),
            models.CheckConstraint(
                condition=~Exact(ScientificNameKey("scientific_name"), models.Value("")),
                name="institution_species_name_not_empty",
            ),
            models.CheckConstraint(
                condition=(models.Q(worms_aphia_id__isnull=True)
                           | models.Q(worms_aphia_id__gte=1, worms_aphia_id__lte=2147483647)),
                name="institution_species_aphia_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(revision__gte=1), name="institution_species_revision_positive",
            ),
        ]

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
    # ISO 3166-1 alpha-2 country of the collection location. Within one
    # institution a country has exactly one BBB and a BBB exactly one country
    # (with the two constraints above). NULL: no country recorded (legacy rows
    # are never assigned one implicitly).
    country_code = models.CharField(max_length=2, null=True, blank=True, editable=False)

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
            models.UniqueConstraint(
                fields=["organization", "country_code"],
                condition=models.Q(country_code__isnull=False),
                name="unique_provenance_code_per_organization_country",
            ),
            models.CheckConstraint(
                condition=models.Q(country_code__isnull=True) | models.Q(country_code__regex=r"^[A-Z]{2}$"),
                name="provenance_country_code_format",
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
        ARTIFICIAL_FERTILIZATION_WILD = "artificial_fertilization_wild", "Artificial fertilization from wild individuals"
        ARTIFICIAL_FERTILIZATION_CAPTIVE = (
            "artificial_fertilization_captive", "Artificial fertilization from captive individuals",
        )

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


class LocalStrainNumberCounter(models.Model):
    """Internal transaction-scoped allocation state; not a biological identity."""

    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.PROTECT,
        related_name="local_strain_number_counters",
    )
    species = models.ForeignKey(
        Species, on_delete=models.PROTECT,
        related_name="local_strain_number_counters",
    )
    biological_provenance = models.ForeignKey(
        BiologicalProvenance, on_delete=models.PROTECT, null=True, blank=True,
        related_name="local_strain_number_counters",
    )
    last_number = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "species", "biological_provenance"],
                condition=models.Q(biological_provenance__isnull=False),
                name="unique_local_x_scope",
            ),
            models.UniqueConstraint(
                fields=["organization", "species"],
                condition=models.Q(biological_provenance__isnull=True),
                name="unique_local_x_null_scope",
            ),
        ]


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


class InstitutionStrainCreationRequest(models.Model):
    """Durable idempotency receipt for institutional Strain creation.

    One row records one successful creation, scoped to the institution, the actor
    and the client request key. A replay with the same key and the same normalized
    intent returns the recorded Strain; a different intent is an explicit conflict.
    The row is written in the creation transaction, so a rollback leaves no false
    success and no counter, location, identity or audit is duplicated on replay.
    """

    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.PROTECT,
        related_name="institution_strain_creation_requests",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="institution_strain_creation_requests",
    )
    request_id = models.UUIDField()
    intent = models.JSONField()
    strain = models.OneToOneField(
        Strain, on_delete=models.PROTECT, related_name="institution_creation_request",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "actor", "request_id"],
                name="unique_institution_strain_request",
            ),
        ]


class SpeciesCodeChangeRequest(models.Model):
    """Successful code-change receipt; intent and result survive later renames.

    LocalStrainIdentity references the same assignment row, not its old literal
    code. Historical literals belong in aliases and this operation's result.
    """

    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.PROTECT,
        related_name="species_code_change_requests",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="species_code_change_requests",
    )
    profile = models.ForeignKey(
        InstitutionSpecies, on_delete=models.PROTECT, related_name="code_change_requests",
    )
    request_id = models.UUIDField()
    intent = models.JSONField()
    result = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "actor", "request_id"],
                name="unique_species_code_change_request",
            ),
        ]


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
