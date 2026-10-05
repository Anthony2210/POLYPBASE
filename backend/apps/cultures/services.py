"""Business logic for cultures, boxes, transfers, and subculture events."""

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.accounts.identity import serialize_user_identity
from apps.accounts.permissions import user_can_write_lab_data
from apps.audit.models import AuditLog
from apps.taxonomy.scoping import eligible_strains

from .box_codes import allocate_box_codes
from .polyp_state import resolve_current_polyp_state
from .models import Box, BoxLineage, BoxLocation, BoxMovement, SubcultureAllocation, SubcultureEvent, ThermalZone

LINEAGE_GRAPH_MAX_NODES = 250
_EXPECTED_THERMAL_ZONE_UNSET = object()


class StaleBoxLocationError(ValidationError):
    """The requested move was based on a location that is no longer current."""

    def __init__(self, *, expected_thermal_zone_id, current_thermal_zone):
        self.expected_thermal_zone_id = expected_thermal_zone_id
        self.current_thermal_zone_id = (
            current_thermal_zone.id if current_thermal_zone is not None else None
        )
        self.current_thermal_zone_name = (
            current_thermal_zone.name if current_thermal_zone is not None else None
        )
        super().__init__(
            "The box location changed since this movement was prepared. "
            "Refresh the box before moving it."
        )


def _locked_box(box):
    return (
        # Lock the box, not the nullable location side of the outer join.
        Box.objects.select_for_update(of=("self",))
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
    thermal_zone=None,
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
        if box.thermal_zone_id is not None and thermal_zone is not None:
            raise ValidationError(
                "A pending box that already has a location cannot be moved during qualification."
            )
        if thermal_zone is not None:
            move_box_to_thermal_zone(
                box=box,
                thermal_zone=thermal_zone,
                moved_at=timezone.now(),
                user=user,
                notes="Initial location assigned during historical qualification.",
            )
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
def assign_unlocated_active_box(*, box, thermal_zone, user, notes=""):
    """Assign the first known location without turning the action into a transfer."""
    box = _locked_box(box)
    if box.status != Box.Status.ACTIVE:
        raise ValidationError("Only an active box can receive an initial location.")
    if box.thermal_zone_id is not None:
        raise ValidationError("This box already has a current location.")

    return move_box_to_thermal_zone(
        box=box,
        thermal_zone=thermal_zone,
        moved_at=timezone.now(),
        user=user,
        notes=notes,
    )


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


class SubcultureStateChanged(ValidationError):
    def __init__(self, current_state):
        self.current_state = current_state
        super().__init__(_("The current polyp state changed. Refresh the parent box."))


class SubcultureInvalid(ValidationError):
    def __init__(self, message, *, error_code):
        self.error_code = error_code
        super().__init__(message)


@transaction.atomic
def create_subculture(*, parent_box, user, organization, expected_current_state_revision,
                      children, reason="", notes=""):
    """Persist ordered allocations; only complete counts transition the parent."""
    parent_box = _locked_box(parent_box)
    if parent_box.organization_id != organization.pk:
        raise PermissionDenied(_("The parent box is outside the active organization."))
    if (user is None or not user.is_authenticated or not user.is_active
            or not user_can_write_lab_data(user, parent_box.organization)):
        raise PermissionDenied(_("This account cannot create subculture events."))
    if parent_box.status != Box.Status.ACTIVE:
        raise SubcultureInvalid(_("Only an active box can be subcultured."), error_code="subculture_parent_ineligible")
    if not eligible_strains(parent_box.organization).filter(pk=parent_box.strain_id).exists():
        raise SubcultureInvalid(_("The parent strain is not eligible for its organization."), error_code="subculture_parent_ineligible")
    state = resolve_current_polyp_state(parent_box)
    if expected_current_state_revision != state["revision"]:
        raise SubcultureStateChanged(state)
    if state["polyp_count"] is None:
        raise SubcultureInvalid(_("The parent polyp count is unknown."), error_code="subculture_parent_count_unknown")
    if not 1 <= len(children) <= 20:
        raise SubcultureInvalid(_("Provide between one and twenty children."), error_code="subculture_invalid_children")
    for child in children:
        value = child.get("allocated_polyps")
        if value is not None and (type(value) is not int or not 0 <= value <= 2147483647):
            raise SubcultureInvalid(_("Each child requires a nonnegative integer allocation."), error_code="subculture_invalid_allocation")
    known_total = sum(child.get("allocated_polyps") for child in children if child.get("allocated_polyps") is not None)
    complete = all(child.get("allocated_polyps") is not None for child in children)
    total = known_total if complete else None
    if known_total > state["polyp_count"]:
        raise SubcultureInvalid(_("The allocation exceeds the current parent count."), error_code="subculture_allocation_exceeds_parent")

    zone_ids = {child["thermal_zone"].pk for child in children}
    zones = {zone.pk: zone for zone in ThermalZone.objects.select_for_update().filter(
        pk__in=zone_ids, organization=organization, is_active=True,
    ).order_by("pk")}
    if len(zones) != len(zone_ids):
        raise SubcultureInvalid(_("Each child requires an active zone in the parent organization."), error_code="subculture_invalid_zone")
    identities = allocate_box_codes(parent_box.strain.code, len(children))
    occurred_at = timezone.now()
    event_date = timezone.localdate(occurred_at)
    parent_box.polyp_state_revision += 1
    parent_box.save(update_fields=["polyp_state_revision"])
    event = SubcultureEvent.objects.create(
        parent_box=parent_box, event_date=event_date, occurred_at=occurred_at,
        parent_state_sequence=parent_box.polyp_state_revision,
        parent_polyp_count_before=state["polyp_count"], allocated_polyp_count=total,
        parent_polyp_count_after=state["polyp_count"] - total if complete else None,
        parent_state_snapshot=state, author_name=user.get_username(),
        user=user, reason=reason, notes=notes,
    )
    child_boxes = []
    allocations = []
    for position, (child_data, (code, number)) in enumerate(zip(children, identities, strict=True)):
        thermal_zone = zones[child_data["thermal_zone"].pk]
        child_box = Box.objects.create(
            organization=organization, global_code=code,
            local_code=child_data.get("local_code", ""), box_number=number,
            strain=parent_box.strain,
            origin=parent_box.origin if child_data.get("copy_origin", True) else None,
            volume_liters=parent_box.volume_liters if child_data.get("copy_volume_liters", True) else None,
            thermal_zone=thermal_zone, entered_on=event_date,
            notes=child_data.get("notes", ""), polyp_state_revision=1,
        )
        allocation = SubcultureAllocation.objects.create(
            event=event, child_box=child_box, position=position,
            allocated_polyps=child_data.get("allocated_polyps"), child_global_code=code,
        )
        allocations.append({"id": allocation.pk, "position": position, "child_box_id": child_box.pk,
                            "child_global_code": code, "allocated_polyps": allocation.allocated_polyps})
        BoxLineage.objects.create(parent_box=parent_box, child_box=child_box, subculture_event=event,
                                 relationship_type=BoxLineage.RelationshipType.SUBCULTURE)
        BoxLocation.objects.create(box=child_box, thermal_zone=thermal_zone, starts_at=occurred_at,
                                   notes="Initial location after subculture.")
        child_boxes.append(child_box)
    AuditLog.objects.create(
        organization=organization, user=user, action=AuditLog.Action.SUBCULTURE,
        object_type="box", object_id=parent_box.global_code,
        description=f"Subculture created from {parent_box.global_code}",
        metadata={
            "subculture_event_id": event.pk, "occurred_at": occurred_at.isoformat(),
            "parent_state_snapshot": state, "parent_polyp_count_before": state["polyp_count"],
            "allocated_polyp_count": total, "parent_polyp_count_after": event.parent_polyp_count_after,
            "child_box_ids": [box.pk for box in child_boxes],
            "child_global_codes": [box.global_code for box in child_boxes], "allocations": allocations,
        },
    )
    return event, child_boxes


@transaction.atomic
def move_box_to_thermal_zone(
    *,
    box,
    thermal_zone,
    moved_at,
    user,
    notes,
    expected_thermal_zone_id=_EXPECTED_THERMAL_ZONE_UNSET,
):
    """Move a box to another thermal zone and keep a location history."""
    requested_box = box
    box = _locked_box(box)
    current_thermal_zone = (
        ThermalZone.objects.get(pk=box.thermal_zone_id)
        if box.thermal_zone_id is not None
        else None
    )
    if thermal_zone.organization_id != box.organization_id:
        raise ValidationError("The thermal zone must belong to the box organization.")
    if (
        expected_thermal_zone_id is not _EXPECTED_THERMAL_ZONE_UNSET
        and box.thermal_zone_id != expected_thermal_zone_id
    ):
        raise StaleBoxLocationError(
            expected_thermal_zone_id=expected_thermal_zone_id,
            current_thermal_zone=current_thermal_zone,
        )
    if box.thermal_zone_id == thermal_zone.id:
        raise ValidationError("The box is already in this thermal zone.")

    moved_at = moved_at or timezone.now()
    from_thermal_zone = current_thermal_zone
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
    requested_box.thermal_zone = thermal_zone

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
                "user_identity": serialize_user_identity(event.user),
            }
            if event
            else None
        ),
    }
