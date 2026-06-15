"""Business logic for cultures, boxes, transfers, and subculture events."""

from datetime import datetime, time

from django.db import transaction
from django.utils import timezone

from apps.audit.models import AuditLog

from .models import Box, BoxLineage, BoxLocation, SubcultureEvent


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
