"""Read-only consistency check of the AAA-BBB-X prefix used for new Box identifiers.

The Box namespace shared by ordinary creation, subculture and Transfer v1 is
the established ``Strain.code``. This module never builds or rewrites a prefix.

"usable" means only: the saved code has the approved ``AAA-BBB-X`` shape and no
recorded reference field contradicts it. It is not a biological certification:
AAA, BBB and X are read from the saved code, and missing reference records are
not contradictions. Reference fields checked when present:

- ``Strain.number`` (X) and ``Strain.origin_code`` (BBB);
- the active institution's ``OrganizationSpeciesCode`` for the species (AAA);
- the strain's ``LocalStrainIdentity``. Local code assignments are evidence only
  for the institution that owns them: an identity on a strain the active
  institution does not own, or using another institution's assignment or another
  species' code, is treated as a contradiction and never named in the result.
"""

import re

from django.utils.translation import get_language

from apps.taxonomy.models import LocalStrainIdentity, OrganizationSpeciesCode

# Same strict shape as the reviewed historical importer (STRICT_IDENTIFIER).
CANONICAL_STRAIN_CODE = re.compile(r"^([A-Z]{3})-([A-Z]{3})-([0-9]+)$")

USABLE = "usable"
UNUSABLE = "unusable"

# Stable reasons exposed to the client; the UI translates them.
REASON_CODE_FORMAT = "code_format"
REASON_NUMBER_MISMATCH = "number_mismatch"
REASON_ORIGIN_MISMATCH = "origin_mismatch"
REASON_SPECIES_CODE_MISMATCH = "species_code_mismatch"
REASON_LOCAL_IDENTITY_MISMATCH = "local_identity_mismatch"


def _local_identity(strain):
    try:
        return strain.local_identity
    except LocalStrainIdentity.DoesNotExist:
        return None


def institution_species_codes(organization, species_ids):
    """Map species ID -> AAA assigned by the active institution."""
    if organization is None:
        return {}
    return dict(
        OrganizationSpeciesCode.objects.filter(
            organization=organization, species_id__in=species_ids,
        ).values_list("species_id", "code")
    )


def _local_identity_belongs_to(identity, strain, organization):
    """Local assignments created by the service share the strain owner's institution."""
    species_assignment = identity.species_code_assignment
    provenance = identity.provenance_code_assignment
    return (
        organization is not None
        and strain.organization_id == organization.pk
        and species_assignment.organization_id == organization.pk
        and species_assignment.species_id == strain.species_id
        and (provenance is None or provenance.organization_id == organization.pk)
    )


def strain_box_identity(strain, organization, *, species_codes=None):
    """Return the saved prefix and its parsed components, or why it cannot be used.

    ``species_codes`` may be pre-fetched with institution_species_codes() for lists.
    """
    match = CANONICAL_STRAIN_CODE.fullmatch(strain.code or "")
    if match is None:
        return {"status": UNUSABLE, "reason": REASON_CODE_FORMAT}
    species_code, origin_code, strain_number = match.groups()

    if strain.number is not None and strain.number != int(strain_number):
        return {"status": UNUSABLE, "reason": REASON_NUMBER_MISMATCH}
    if strain.origin_code and strain.origin_code != origin_code:
        return {"status": UNUSABLE, "reason": REASON_ORIGIN_MISMATCH}

    if species_codes is None:
        species_codes = institution_species_codes(organization, [strain.species_id])
    assigned = species_codes.get(strain.species_id)
    if assigned is not None and assigned != species_code:
        return {"status": UNUSABLE, "reason": REASON_SPECIES_CODE_MISMATCH}

    provenance_name = None
    identity = _local_identity(strain)
    if identity is not None:
        provenance = identity.provenance_code_assignment
        # A foreign assignment is never evidence, even when its literal code matches.
        if not _local_identity_belongs_to(identity, strain, organization) or (
            identity.species_code_assignment.code != species_code
            or (provenance is not None and provenance.code != origin_code)
        ):
            return {"status": UNUSABLE, "reason": REASON_LOCAL_IDENTITY_MISMATCH}
        if provenance is not None:
            provenance_name = provenance.biological_provenance.name or None

    return {
        "status": USABLE,
        "prefix": strain.code,
        "species_code": species_code,
        "origin_code": origin_code,
        "strain_number": strain_number,
        # Optional reference metadata: the name of the active institution's recorded
        # biological provenance. Never a provider, place or acquisition event.
        "recorded_provenance_name": provenance_name,
    }


def strain_display_name(strain):
    """Localized strain name when one was recorded, otherwise an empty string."""
    translations = {item.language_code: item.name for item in strain.translations.all()}
    language = (get_language() or "").split("-")[0]
    return translations.get(language) or next((name for name in translations.values() if name), "")
