"""Institution-scoped operations for normalized local strain identities."""

from django.core.exceptions import ValidationError
from django.db import transaction

from .models import (
    LocalStrainIdentity,
    OrganizationProvenanceCode,
    OrganizationSpeciesCode,
    Strain,
)


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
