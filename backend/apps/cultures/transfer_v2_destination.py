"""Read-only destination observation for Transfer v2, not acceptance or authorization."""

from copy import deepcopy
from dataclasses import dataclass
from enum import Enum

from rest_framework.exceptions import ValidationError

from apps.organizations.models import Organization
from apps.taxonomy.models import GlobalStrainIdentity, Strain

from .transfer_v2_protocol import StrictSerializer, TransferItemSerializer


class DestinationStrainStatus(str, Enum):
    REUSE = "REUSE"
    NEW_LOCAL_REPRESENTATION_REQUIRED = "NEW_LOCAL_REPRESENTATION_REQUIRED"
    CONFLICT_MULTIPLE_LOCAL_REPRESENTATIONS = "CONFLICT_MULTIPLE_LOCAL_REPRESENTATIONS"
    CONFLICT_SPECIES_SNAPSHOT_MISMATCH = "CONFLICT_SPECIES_SNAPSHOT_MISMATCH"
    CONFLICT_LOCAL_IDENTITY_INCONSISTENT = "CONFLICT_LOCAL_IDENTITY_INCONSISTENT"


class GlobalIdentityState(str, Enum):
    KNOWN = "KNOWN"
    UNKNOWN_LOCALLY = "UNKNOWN_LOCALLY"


@dataclass(frozen=True)
class DestinationStrainResolution:
    status: DestinationStrainStatus
    identity_state: GlobalIdentityState
    destination_strain_id: int | None = None
    missing_local_identity: bool = False
    legacy_unowned_representation_present: bool = False


class DestinationStrainInputSerializer(StrictSerializer):
    # Use the protocol's exact validation, including preserved whitespace.
    global_strain_id = deepcopy(TransferItemSerializer().fields["global_strain_id"])
    species_scientific_name = deepcopy(
        TransferItemSerializer().fields["species_scientific_name"]
    )


def resolve_destination_strain(
    *, destination_organization, incoming_global_strain_uuid,
    incoming_species_scientific_name,
) -> DestinationStrainResolution:
    """Observe a caller-authorized destination using Global ID and owned rows only.

    The caller must resolve and authorize the destination before calling, as for
    taxonomy's internal identity service. UUIDs never authorize access. Results
    are observations, not reservations; future acceptance must recheck state.
    Multiple candidates take precedence over consistency checks. For a single
    candidate, Species snapshot mismatch precedes local identity inconsistency.
    """
    if not isinstance(destination_organization, Organization) or destination_organization.pk is None:
        raise ValidationError({"destination_organization": "A persisted destination context is required."})

    serializer = DestinationStrainInputSerializer(data={
        "global_strain_id": incoming_global_strain_uuid,
        "species_scientific_name": incoming_species_scientific_name,
    })
    serializer.is_valid(raise_exception=True)
    global_id = serializer.validated_data["global_strain_id"]
    species_name = serializer.validated_data["species_scientific_name"]
    organization_id = destination_organization.pk

    # Species must not hide a conflicting representation of the same Global ID.
    candidates = list(Strain.objects.filter(
        organization_id=organization_id, global_identity__global_id=global_id,
    ).values(
        "pk", "species_id", "species__scientific_name", "local_identity__pk",
        "local_identity__species_code_assignment__organization_id",
        "local_identity__species_code_assignment__species_id",
        "local_identity__provenance_code_assignment__organization_id",
    )[:2])

    if not candidates:
        identity_state = (
            GlobalIdentityState.KNOWN
            if GlobalStrainIdentity.objects.filter(global_id=global_id).exists()
            else GlobalIdentityState.UNKNOWN_LOCALLY
        )
        return DestinationStrainResolution(
            status=DestinationStrainStatus.NEW_LOCAL_REPRESENTATION_REQUIRED,
            identity_state=identity_state,
            legacy_unowned_representation_present=Strain.objects.filter(
                organization__isnull=True, global_identity__global_id=global_id,
            ).exists(),
        )

    if len(candidates) > 1:
        return DestinationStrainResolution(
            status=DestinationStrainStatus.CONFLICT_MULTIPLE_LOCAL_REPRESENTATIONS,
            identity_state=GlobalIdentityState.KNOWN,
        )

    candidate = candidates[0]
    if candidate["species__scientific_name"] != species_name:
        return DestinationStrainResolution(
            status=DestinationStrainStatus.CONFLICT_SPECIES_SNAPSHOT_MISMATCH,
            identity_state=GlobalIdentityState.KNOWN,
        )

    missing_local_identity = candidate["local_identity__pk"] is None
    if not missing_local_identity and (
        candidate["local_identity__species_code_assignment__organization_id"] != organization_id
        or candidate["local_identity__species_code_assignment__species_id"] != candidate["species_id"]
        or candidate["local_identity__provenance_code_assignment__organization_id"]
        not in (None, organization_id)
    ):
        return DestinationStrainResolution(
            status=DestinationStrainStatus.CONFLICT_LOCAL_IDENTITY_INCONSISTENT,
            identity_state=GlobalIdentityState.KNOWN,
        )

    return DestinationStrainResolution(
        status=DestinationStrainStatus.REUSE,
        identity_state=GlobalIdentityState.KNOWN,
        destination_strain_id=candidate["pk"],
        missing_local_identity=missing_local_identity,
    )
