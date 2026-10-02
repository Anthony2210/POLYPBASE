"""Institution-scoped operations for normalized local strain identities."""

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max

from apps.organizations.models import Organization

from .models import (
    BiologicalProvenance,
    LocalStrainIdentity,
    LocalStrainNumberCounter,
    OrganizationProvenanceCode,
    OrganizationSpeciesCode,
    Species,
    Strain,
)


@transaction.atomic
def allocate_next_local_strain_number(*, organization, species, biological_provenance):
    """Reserve one X in an explicit, caller-authorized local namespace.

    PostgreSQL unique indexes arbitrate first-use races. get_or_create uses a
    savepoint to recover a competing insert, then SELECT FOR UPDATE holds the
    namespace row until the enclosing transaction ends. Other scopes do not
    share this lock. Use the normal READ COMMITTED isolation level.

    This creates only counter state, not a strain or any identity/assignment.
    An outer rollback undoes the allocation; committed allocations can leave
    gaps. This is not a durable reservation outside the database transaction.
    Callers remain responsible for authorization and must use this primitive
    for coordinated allocation. Concurrent manual number/identity writers do
    not take this lock and are not protected by this service.
    """
    for value, model, label in (
        (organization, Organization, "organization"),
        (species, Species, "species"),
        (biological_provenance, BiologicalProvenance, "biological_provenance"),
    ):
        if label == "biological_provenance" and value is None:
            continue
        if (not isinstance(value, model) or value.pk is None
                or value._state.adding or not model.objects.filter(pk=value.pk).exists()):
            raise ValidationError(f"An existing {label} is required.", code="invalid_scope")

    counter, _ = LocalStrainNumberCounter.objects.select_for_update().get_or_create(
        organization=organization, species=species,
        biological_provenance=biological_provenance,
    )
    # Require an actual local identity and consistent explicit relationships.
    # A missing identity must not be mistaken for explicit NULL provenance.
    strains = Strain.objects.filter(
        organization=organization, species=species,
        local_identity__species_code_assignment__organization=organization,
        local_identity__species_code_assignment__species=species,
        number__isnull=False,
    )
    if biological_provenance is None:
        strains = strains.filter(local_identity__provenance_code_assignment__isnull=True)
    else:
        strains = strains.filter(
            local_identity__provenance_code_assignment__organization=organization,
            local_identity__provenance_code_assignment__biological_provenance=biological_provenance,
        )
    maximum = strains.aggregate(maximum=Max("number"))["maximum"]
    # Refresh the floor even after initialization for sequential explicit imports.
    floor = max(counter.last_number, maximum if maximum is not None else 0)
    if floor >= 2147483647:
        raise ValidationError("The local strain number range is exhausted.", code="number_exhausted")
    counter.last_number = floor + 1
    counter.save(update_fields=["last_number"])
    return counter.last_number


@transaction.atomic
def create_local_strain_identity(
    *, strain, organization, species_code_assignment,
    provenance_code_assignment=None, biological_provenance=None,
):
    """Create an identity within a caller-authorized organization context.

    The caller must resolve and authorize the active organization before calling.
    Re-read objects so stale or client-supplied instances cannot bypass validation.
    """
    locked_strain = Strain.objects.select_for_update().get(pk=strain.pk)
    if locked_strain.organization_id is None:
        raise ValidationError(
            "An unowned strain cannot receive a local identity.", code="unowned_strain"
        )
    if organization is None or locked_strain.organization_id != organization.pk:
        raise ValidationError(
            "The strain does not belong to the active organization.", code="foreign_organization"
        )

    assignment = OrganizationSpeciesCode.objects.select_for_update().get(
        pk=species_code_assignment.pk
    )
    if assignment.organization_id != locked_strain.organization_id:
        raise ValidationError(
            "The species code assignment does not belong to the active organization.",
            code="foreign_assignment",
        )
    if assignment.species_id != locked_strain.species_id:
        raise ValidationError(
            "The species code assignment does not match the strain species.",
            code="species_mismatch",
        )
    if biological_provenance is not None and provenance_code_assignment is None:
        raise ValidationError(
            "Known biological provenance requires a local provenance code assignment.",
            code="missing_provenance_assignment",
        )

    provenance_assignment = None
    if provenance_code_assignment is not None:
        provenance_assignment = OrganizationProvenanceCode.objects.select_for_update().get(
            pk=provenance_code_assignment.pk
        )
        if provenance_assignment.organization_id != locked_strain.organization_id:
            raise ValidationError(
                "The provenance code assignment does not belong to the active organization.",
                code="foreign_provenance_assignment",
            )
        if (biological_provenance is not None
                and provenance_assignment.biological_provenance_id != biological_provenance.pk):
            raise ValidationError(
                "The provenance code assignment does not match the biological provenance.",
                code="provenance_mismatch",
            )

    if LocalStrainIdentity.objects.filter(strain=locked_strain).exists():
        raise ValidationError(
            "The strain already has a local identity.", code="identity_exists"
        )

    return LocalStrainIdentity.objects.create(
        strain=locked_strain, species_code_assignment=assignment,
        provenance_code_assignment=provenance_assignment,
    )
