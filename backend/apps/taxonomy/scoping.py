"""Institution eligibility for operational strain references."""

from django.db.models import Exists, OuterRef, Q

from apps.cultures.models import Box

from .models import Strain


def eligible_strains(organization):
    """Owned strains and unresolved strains previously used by this institution."""
    if organization is None:
        return Strain.objects.none()
    prior_box = Box.objects.filter(
        organization_id=organization.pk,
        strain_id=OuterRef("pk"),
    )
    return Strain.objects.annotate(_institution_has_box=Exists(prior_box)).filter(
        Q(organization_id=organization.pk)
        | Q(organization__isnull=True, _institution_has_box=True)
    )
