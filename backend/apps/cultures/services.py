"""Business logic for cultures, boxes, transfers, and subculture events."""

from datetime import datetime, time

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.audit.models import AuditLog

from .models import Box, BoxLineage, BoxLocation, BoxMovement, SubcultureEvent

LINEAGE_GRAPH_MAX_NODES = 250


@transaction.atomic
def create_subculture(*, parent_box, user, event_date, reason, notes, children):
    """Create one subculture event and all its child boxes atomically."""
    event = SubcultureEvent.objects.create(
        parent_box=parent_box,
        event_date=event_date,
        user=user,
        reason=reason,
        notes=notes,
    )

    location_start = timezone.make_aware(
        datetime.combine(event_date, time.min),
        timezone.get_current_timezone(),
    )
    child_boxes = []

    for child_data in children:
        thermal_zone = child_data["thermal_zone"]
        child_box = Box.objects.create(
            organization=parent_box.organization,
            global_code=child_data["global_code"],
            local_code=child_data.get("local_code", ""),
            box_number=child_data["box_number"],
            strain=parent_box.strain,
            origin=parent_box.origin if child_data.get("copy_origin", True) else None,
            thermal_zone=thermal_zone,
            entered_on=event_date,
            volume_liters=(
                parent_box.volume_liters
                if child_data.get("copy_volume_liters", True)
                else None
            ),
            notes=child_data.get("notes", ""),
        )
        BoxLineage.objects.create(
            parent_box=parent_box,
            child_box=child_box,
            subculture_event=event,
            relationship_type=BoxLineage.RelationshipType.SUBCULTURE,
        )
        BoxLocation.objects.create(
            box=child_box,
            thermal_zone=thermal_zone,
            starts_at=location_start,
            notes="Initial location after subculture.",
        )
        child_boxes.append(child_box)

    AuditLog.objects.create(
        organization=parent_box.organization,
        user=user,
        action=AuditLog.Action.SUBCULTURE,
        object_type="box",
        object_id=parent_box.global_code,
        description=f"Subculture created from {parent_box.global_code}",
        metadata={
            "subculture_event_id": event.id,
            "child_box_ids": [box.id for box in child_boxes],
            "child_global_codes": [box.global_code for box in child_boxes],
        },
    )

    return event, child_boxes


@transaction.atomic
def move_box_to_thermal_zone(*, box, thermal_zone, moved_at, user, notes):
    """Move a box to another thermal zone and keep a location history."""
    if thermal_zone.organization_id != box.organization_id:
        raise ValidationError("The thermal zone must belong to the box organization.")
    if box.thermal_zone_id == thermal_zone.id:
        raise ValidationError("The box is already in this thermal zone.")

    moved_at = moved_at or timezone.now()
    from_thermal_zone = box.thermal_zone
    active_locations = list(
        BoxLocation.objects.select_for_update()
        .filter(box=box, ends_at__isnull=True)
        .order_by("-starts_at")
    )

    if active_locations and active_locations[0].starts_at > moved_at:
        raise ValidationError("The movement date cannot be before the current location start.")

    for location in active_locations:
        location.ends_at = moved_at
        location.save(update_fields=["ends_at"])

    BoxLocation.objects.create(
        box=box,
        thermal_zone=thermal_zone,
        starts_at=moved_at,
        notes=notes,
    )
    movement = BoxMovement.objects.create(
        box=box,
        from_thermal_zone=from_thermal_zone,
        to_thermal_zone=thermal_zone,
        moved_at=moved_at,
        user=user,
        notes=notes,
    )
    box.thermal_zone = thermal_zone
    box.save(update_fields=["thermal_zone"])

    AuditLog.objects.create(
        organization=box.organization,
        user=user,
        action=AuditLog.Action.UPDATE,
        object_type="box",
        object_id=box.global_code,
        description=f"Box moved to {thermal_zone.name}",
        metadata={
            "movement_id": movement.id,
            "from_thermal_zone_id": from_thermal_zone.id if from_thermal_zone else None,
            "from_thermal_zone_name": from_thermal_zone.name if from_thermal_zone else None,
            "to_thermal_zone_id": thermal_zone.id,
            "to_thermal_zone_name": thermal_zone.name,
            "moved_at": moved_at.isoformat(),
            "note": notes,
            "valeurs": {
                "ancienne_zone": from_thermal_zone.name if from_thermal_zone else None,
                "nouvelle_zone": thermal_zone.name,
                "date_deplacement": moved_at.isoformat(),
                "note": notes,
            },
        },
    )

    return movement


def build_lineage_graph(*, root_box, organization_ids, max_nodes=LINEAGE_GRAPH_MAX_NODES):
    """Return the connected lineage graph visible to the current user."""
    visited_box_ids = {root_box.id}
    pending_box_ids = {root_box.id}
    lineages_by_id = {}
    truncated = False

    while pending_box_ids:
        current_box_ids = pending_box_ids
        pending_box_ids = set()
        lineages = BoxLineage.objects.filter(
            Q(parent_box_id__in=current_box_ids) | Q(child_box_id__in=current_box_ids),
            parent_box__organization_id__in=organization_ids,
            child_box__organization_id__in=organization_ids,
        ).select_related(
            "parent_box",
            "parent_box__organization",
            "parent_box__strain",
            "parent_box__strain__species",
            "parent_box__thermal_zone",
            "child_box",
            "child_box__organization",
            "child_box__strain",
            "child_box__strain__species",
            "child_box__thermal_zone",
            "subculture_event",
            "subculture_event__user",
        )

        for lineage in lineages:
            lineages_by_id[lineage.id] = lineage
            for box_id in (lineage.parent_box_id, lineage.child_box_id):
                if box_id in visited_box_ids:
                    continue
                if len(visited_box_ids) >= max_nodes:
                    truncated = True
                    continue
                visited_box_ids.add(box_id)
                pending_box_ids.add(box_id)

    visible_lineages = [
        lineage
        for lineage in lineages_by_id.values()
        if (
            lineage.parent_box_id in visited_box_ids
            and lineage.child_box_id in visited_box_ids
        )
    ]
    boxes_by_id = {root_box.id: root_box}
    for lineage in visible_lineages:
        boxes_by_id[lineage.parent_box_id] = lineage.parent_box
        boxes_by_id[lineage.child_box_id] = lineage.child_box

    return {
        "root_box_id": root_box.id,
        "nodes": [
            _serialize_lineage_graph_box(box, is_root=box.id == root_box.id)
            for box in boxes_by_id.values()
        ],
        "edges": [
            _serialize_lineage_graph_edge(lineage)
            for lineage in visible_lineages
        ],
        "truncated": truncated,
        "max_nodes": max_nodes,
    }


def _serialize_lineage_graph_box(box, *, is_root):
    return {
        "id": box.id,
        "global_code": box.global_code,
        "local_code": box.local_code,
        "status": box.status,
        "species_name": box.strain.species.scientific_name,
        "thermal_zone_name": box.thermal_zone.name if box.thermal_zone else None,
        "organization_name": box.organization.name,
        "is_root": is_root,
    }


def _serialize_lineage_graph_edge(lineage):
    event = lineage.subculture_event
    return {
        "id": lineage.id,
        "source": lineage.parent_box_id,
        "target": lineage.child_box_id,
        "relationship_type": lineage.relationship_type,
        "event": (
            {
                "id": event.id,
                "event_date": event.event_date,
                "reason": event.reason,
                "notes": event.notes,
                "user": event.user.get_username() if event.user else None,
            }
            if event
            else None
        ),
    }
