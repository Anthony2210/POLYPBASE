"""Business logic for cultures, boxes, transfers, and subculture events."""

from datetime import datetime, time

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.measurements.models import BiologicalMeasurement

from .models import Box, BoxLineage, BoxLocation, BoxMovement, SubcultureEvent

LINEAGE_GRAPH_MAX_NODES = 250


def _locked_box(box):
    return (
        Box.objects.select_for_update()
        .select_related("organization", "thermal_zone")
        .get(pk=box.pk)
    )


def _close_current_locations(*, box, ended_at=None, end_date_unknown=False):
    current_locations = list(
        BoxLocation.objects.select_for_update()
        .filter(box=box, ends_at__isnull=True, end_date_unknown=False)
        .order_by("-starts_at")
    )
    if ended_at is not None:
        future_location = next(
            (location for location in current_locations if location.starts_at >= ended_at),
            None,
        )
        if future_location is not None:
            raise ValidationError(
                "The deactivation date must be after the current location start."
            )

    for location in current_locations:
        if end_date_unknown:
            location.end_date_unknown = True
            location.save(update_fields=["end_date_unknown"])
        else:
            location.ends_at = ended_at
            location.save(update_fields=["ends_at"])
    return current_locations


def _status_values(box):
    return {
        "status": box.status,
        "thermal_zone_id": box.thermal_zone_id,
        "stop_reason": box.stop_reason,
        "stop_reason_missing_from_history": box.stop_reason_missing_from_history,
        "deactivated_on": box.deactivated_on.isoformat() if box.deactivated_on else None,
    }


@transaction.atomic
def qualify_pending_box(
    *,
    box,
    target_status,
    user,
    reason="",
    reason_missing_from_history=False,
):
    """Qualify one historical box without inventing missing lifecycle data."""
    box = _locked_box(box)
    if box.status != Box.Status.PENDING_REVIEW:
        raise ValidationError("Only a box pending review can be qualified.")
    if target_status not in {Box.Status.ACTIVE, Box.Status.INACTIVE}:
        raise ValidationError("A pending box can only become active or inactive.")

    before_values = _status_values(box)
    closed_location_ids = []
    if target_status == Box.Status.ACTIVE:
        box.status = Box.Status.ACTIVE
        box.stop_reason = ""
        box.stop_reason_missing_from_history = False
        box.deactivated_on = None
    else:
        reason = reason.strip()
        if reason and reason_missing_from_history:
            raise ValidationError(
                "Provide either a reason or indicate that the historical reason is missing."
            )
        if not reason and not reason_missing_from_history:
            raise ValidationError(
                "A reason is required unless it was missing from the historical data."
            )
        closed_locations = _close_current_locations(
            box=box,
            end_date_unknown=True,
        )
        closed_location_ids = [location.id for location in closed_locations]
        box.status = Box.Status.INACTIVE
        box.thermal_zone = None
        box.stop_reason = reason
        box.stop_reason_missing_from_history = reason_missing_from_history
        box.deactivated_on = None

    box.save(
        update_fields=[
            "status",
            "thermal_zone",
            "stop_reason",
            "stop_reason_missing_from_history",
            "deactivated_on",
        ]
    )
    after_values = _status_values(box)
    AuditLog.objects.create(
        organization=box.organization,
        user=user,
        action=AuditLog.Action.UPDATE,
        object_type="box",
        object_id=box.global_code,
        description=f"Historical box qualified as {target_status}: {box.global_code}",
        metadata={
            "box_id": box.id,
            "transition": f"{Box.Status.PENDING_REVIEW}->{target_status}",
            "before": before_values,
            "after": after_values,
            "closed_location_ids": closed_location_ids,
        },
    )
    return box


@transaction.atomic
def deactivate_box(*, box, user, reason):
    """Deactivate an active box and release its current location atomically."""
    box = _locked_box(box)
    if box.status != Box.Status.ACTIVE:
        raise ValidationError("Only an active box can be deactivated.")
    reason = reason.strip()
    if not reason:
        raise ValidationError("A reason is required to deactivate an active box.")

    changed_at = timezone.now()
    before_values = _status_values(box)
    closed_locations = _close_current_locations(box=box, ended_at=changed_at)
    box.status = Box.Status.INACTIVE
    box.thermal_zone = None
    box.stop_reason = reason
    box.stop_reason_missing_from_history = False
    box.deactivated_on = timezone.localdate(changed_at)
    box.save(
        update_fields=[
            "status",
            "thermal_zone",
            "stop_reason",
            "stop_reason_missing_from_history",
            "deactivated_on",
        ]
    )
    after_values = _status_values(box)
    AuditLog.objects.create(
        organization=box.organization,
        user=user,
        action=AuditLog.Action.UPDATE,
        object_type="box",
        object_id=box.global_code,
        description=f"Box deactivated: {box.global_code}",
        metadata={
            "box_id": box.id,
            "transition": f"{Box.Status.ACTIVE}->{Box.Status.INACTIVE}",
            "before": before_values,
            "after": after_values,
            "closed_location_ids": [location.id for location in closed_locations],
            "changed_at": changed_at.isoformat(),
        },
    )
    return box


@transaction.atomic
def reactivate_box(*, box, thermal_zone, user, notes=""):
    """Reactivate an inactive box in a newly selected active location."""
    box = _locked_box(box)
    if box.status != Box.Status.INACTIVE:
        raise ValidationError("Only an inactive box can be reactivated.")
    if thermal_zone.organization_id != box.organization_id:
        raise ValidationError("The thermal zone must belong to the box organization.")
    if not thermal_zone.is_active:
        raise ValidationError("The selected thermal zone must be active.")

    before_values = _status_values(box)
    legacy_open_locations = _close_current_locations(
        box=box,
        end_date_unknown=True,
    )
    if box.thermal_zone_id is not None:
        box.thermal_zone = None
        box.save(update_fields=["thermal_zone"])
    movement = move_box_to_thermal_zone(
        box=box,
        thermal_zone=thermal_zone,
        moved_at=timezone.now(),
        user=user,
        notes=notes,
    )
    box.status = Box.Status.ACTIVE
    box.stop_reason = ""
    box.stop_reason_missing_from_history = False
    box.deactivated_on = None
    box.save(
        update_fields=[
            "status",
            "stop_reason",
            "stop_reason_missing_from_history",
            "deactivated_on",
        ]
    )
    after_values = _status_values(box)
    AuditLog.objects.create(
        organization=box.organization,
        user=user,
        action=AuditLog.Action.UPDATE,
        object_type="box",
        object_id=box.global_code,
        description=f"Box reactivated: {box.global_code}",
        metadata={
            "box_id": box.id,
            "transition": f"{Box.Status.INACTIVE}->{Box.Status.ACTIVE}",
            "before": before_values,
            "after": after_values,
            "movement_id": movement.id,
            "legacy_closed_location_ids": [
                location.id for location in legacy_open_locations
            ],
        },
    )
    return box


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
        if child_data.get("initial_polyp_count") is not None:
            BiologicalMeasurement.objects.create(
                box=child_box,
                measured_on=event_date,
                polyp_count=child_data["initial_polyp_count"],
                ephyrae_count=0,
                user=user,
                notes="Nombre de polypes initial après repiquage.",
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
            "initial_polyp_counts": {
                child_data["global_code"]: child_data.get("initial_polyp_count")
                for child_data in children
                if child_data.get("initial_polyp_count") is not None
            },
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
        .filter(box=box, ends_at__isnull=True, end_date_unknown=False)
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
