"""Institution-scoped operations for normalized local strain identities."""

from django.core.exceptions import ValidationError
from django.db import transaction

from .models import LocalStrainIdentity, OrganizationSpeciesCode, Strain


@transaction.atomic
def create_local_strain_identity(*, strain, organization, species_code_assignment):
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
    if LocalStrainIdentity.objects.filter(strain=locked_strain).exists():
        raise ValidationError(
            "The strain already has a local identity.", code="identity_exists"
        )

    return LocalStrainIdentity.objects.create(
        strain=locked_strain, species_code_assignment=assignment
    )
