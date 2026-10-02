"""Internal source-only v2 package creation. No acceptance or stock effects."""

from django.db import transaction

from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.accounts.permissions import user_can_administer_organization
from apps.audit.models import AuditLog
from apps.organizations.models import Organization

from .models import Box, TransferEnvelope, TransferItem
from .transfer_v2_protocol import (
    MAX_QUANTITY,
    PROTOCOL_MAJOR,
    PROTOCOL_MINOR,
    StrictIntegerField,
    StrictSerializer,
    StrictStringField,
    StrictUUIDField,
    TransferItemSerializer,
    serialize_transfer_envelope,
)


class SourceSelectionSerializer(StrictSerializer):
    source_box_id = StrictIntegerField(min_value=1)
    declared_polyp_quantity = StrictIntegerField(min_value=0, max_value=MAX_QUANTITY)


class DestinationSnapshotSerializer(StrictSerializer):
    destination_institution_id = StrictUUIDField(allow_null=True)
    destination_institution_name = StrictStringField(
        max_length=150, allow_blank=True, trim_whitespace=False,
    )


@transaction.atomic
def create_source_package(
    *, actor, source_organization, selections,
    destination_institution_id=None, destination_institution_name="",
):
    """Create and audit a package in an explicitly selected source context.

    The context is supplied by the internal caller, not inferred from portable
    IDs. Recheck authority and fetch source records from that context. Snapshots
    are create-only here; technical ORM/SQL writers must preserve them too.
    """
    if (
        actor is None or not actor.is_authenticated or not actor.is_active
        or source_organization.pk is None
    ):
        raise PermissionDenied("Source organization Admin authority is required.")
    try:
        source = Organization.objects.get(pk=source_organization.pk)
    except Organization.DoesNotExist as error:
        raise PermissionDenied("Source organization Admin authority is required.") from error
    if not user_can_administer_organization(actor, source):
        raise PermissionDenied("Source organization Admin authority is required.")

    selection_serializer = SourceSelectionSerializer(data=selections, many=True, allow_empty=False)
    selection_serializer.is_valid(raise_exception=True)
    destination_serializer = DestinationSnapshotSerializer(data={
        "destination_institution_id": destination_institution_id,
        "destination_institution_name": destination_institution_name,
    })
    destination_serializer.is_valid(raise_exception=True)

    selections = selection_serializer.validated_data
    boxes = {
        box.pk: box
        for box in Box.objects.filter(
            organization=source,
            pk__in=[selection["source_box_id"] for selection in selections],
        ).select_related("strain__species", "strain__global_identity")
    }
    snapshots = []
    for index, selection in enumerate(selections):
        box = boxes.get(selection["source_box_id"])
        if box is None:
            raise ValidationError({"items": {index: {"source_box_id": "Box is not in the source organization."}}})
        if box.strain.global_identity_id is None:
            raise ValidationError({"items": {index: {
                "global_strain_id": "Source Strain requires GlobalStrainIdentity.",
                "source_box_id": box.pk,
            }}})
        snapshots.append({
            "source_box": box,
            "source_box_code": box.global_code,
            "source_strain_code": box.strain.code,
            "species_scientific_name": box.strain.species.scientific_name,
            "global_strain_id": box.strain.global_identity.global_id,
            "declared_polyp_quantity": selection["declared_polyp_quantity"],
        })

    # Validate portable values before any write, including historical blank codes.

    items = [TransferItem(**snapshot) for snapshot in snapshots]
    portable_items = [
        {key: getattr(item, key) for key in TransferItemSerializer().fields}
        for item in items
    ]
    item_serializer = TransferItemSerializer(data=portable_items, many=True, allow_empty=False)
    item_serializer.is_valid(raise_exception=True)

    envelope = TransferEnvelope.objects.create(
        source_organization=source,
        source_institution_id=source.portable_id,
        source_institution_name=source.name,
        protocol_major=PROTOCOL_MAJOR,
        protocol_minor=PROTOCOL_MINOR,
        created_by=actor,
        **destination_serializer.validated_data,
    )
    for item in items:
        item.envelope = envelope
        item.save(force_insert=True)
    AuditLog.objects.create(
        organization=source,
        user=actor,
        action=AuditLog.Action.TRANSFER,
        object_type="transfer_envelope",
        object_id=str(envelope.transfer_id),
        description="Transfer v2 source package created",
        metadata={
            "protocol_major": envelope.protocol_major,
            "protocol_minor": envelope.protocol_minor,
            "item_ids": [str(item.item_id) for item in items],
        },
    )
    serialize_transfer_envelope(envelope)
    return envelope
