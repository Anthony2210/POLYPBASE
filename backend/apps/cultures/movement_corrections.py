"""Explicit correction of one persisted BoxMovement.

A correction keeps the movement identity (primary key, kind, original author)
and never creates, deletes or merges movements. It edits only the periods the
movement itself wrote, and refuses whenever later history would have to be
rewritten or the movement-period relation cannot be proven.

Lock order is the same as every movement and lifecycle writer: the Box row
first, then the movement, then its linked periods by primary key. The Box lock
therefore serializes corrections with new movements, lifecycle changes and
other corrections of the same box.
"""

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.accounts.permissions import user_can_write_lab_data
from apps.audit.models import AuditLog

from .models import Box, BoxLocation, BoxMovement, ThermalZone
from .services import BoxMovementWeekConflict, check_ordinary_movement_week

# Kinds whose date is bounded by a lifecycle event (first placement,
# qualification, reactivation) or whose provenance is unknown.
DATE_CORRECTABLE_KINDS = {BoxMovement.Kind.ORDINARY}
# Unclassified rows qualify only when the destination period link is proven below.
DESTINATION_CORRECTABLE_KINDS = {
    BoxMovement.Kind.ORDINARY,
    BoxMovement.Kind.UNCLASSIFIED,
    BoxMovement.Kind.INITIAL_LOCATION,
    BoxMovement.Kind.QUALIFICATION,
    BoxMovement.Kind.REACTIVATION,
}
CORRECTABLE_FIELDS = ("moved_at", "to_thermal_zone", "notes")


class BoxMovementWriteError(Exception):
    """A rejected movement correction, independent of its HTTP representation."""


class BoxMovementNotFound(BoxMovementWriteError):
    def __init__(self, object_name):
        self.detail = f"No {object_name} matches the given query."
        super().__init__(self.detail)


class BoxMovementForbidden(BoxMovementWriteError):
    error_code = "movement_correction_forbidden"

    def __init__(self, detail):
        self.detail = detail
        super().__init__(detail)


class BoxMovementInvalid(BoxMovementWriteError):
    """Malformed or self-contradictory request (HTTP 400)."""

    def __init__(self, detail, *, error_code=None):
        self.detail = detail
        self.error_code = error_code
        super().__init__(detail)


class BoxMovementConflict(BoxMovementWriteError):
    """The request is incompatible with the persisted history (HTTP 409)."""

    def __init__(self, detail, *, error_code, **extra):
        self.detail = detail
        self.error_code = error_code
        self.extra = extra
        super().__init__(detail)


class BoxMovementCorrectionWeekConflict(BoxMovementConflict):
    def __init__(self, *, movement_id, iso_week_start):
        super().__init__(
            "An ordinary movement already exists for this box in this ISO week.",
            error_code="box_movement_week_conflict",
            conflicting_movement_id=movement_id,
            iso_week_start=iso_week_start,
        )
        self.movement_id = movement_id
        self.iso_week_start = iso_week_start


def _scoped_box(*, box_id, organization_ids, for_update=False):
    queryset = Box.objects.select_related("organization").filter(
        organization_id__in=organization_ids,
    )
    if for_update:
        queryset = queryset.select_for_update(of=("self",))
    try:
        return queryset.get(pk=box_id)
    except Box.DoesNotExist as exc:
        raise BoxMovementNotFound("Box") from exc


def _require_correction_permission(*, user, box):
    if (
        user is None
        or not user.is_authenticated
        or not user.is_active
        or not user_can_write_lab_data(user, box.organization)
    ):
        raise BoxMovementForbidden("This account cannot correct box movements.")


def _validated_correction_data(*, data, box):
    from .serializers import BoxMovementCorrectionSerializer

    # HTTP callers defer body parsing until scope and permission checks pass.
    if callable(data):
        data = data()
    serializer = BoxMovementCorrectionSerializer(data=data, context={"box": box})
    if not serializer.is_valid():
        raise BoxMovementInvalid(serializer.errors)
    return serializer.validated_data


def _zone_ref(zone):
    if zone is None:
        return None
    return {"id": zone.id, "name": zone.name}


def _movement_values(movement):
    return {
        "moved_at": movement.moved_at.isoformat(),
        "iso_week_start": movement.iso_week_start.isoformat() if movement.iso_week_start else None,
        "from_thermal_zone": _zone_ref(movement.from_thermal_zone),
        "to_thermal_zone": _zone_ref(movement.to_thermal_zone),
        "notes": movement.notes,
    }


def _period_values(location):
    return {
        "thermal_zone_id": location.thermal_zone_id,
        "starts_at": location.starts_at.isoformat(),
        "ends_at": location.ends_at.isoformat() if location.ends_at else None,
        "end_date_unknown": location.end_date_unknown,
        "notes": location.notes,
    }


def _mismatch(movement, detail):
    return BoxMovementConflict(
        detail, error_code="movement_period_mismatch", movement_id=movement.pk,
    )


def _locked_linked_periods(*, box, movement):
    """Lock the periods written with the movement and prove they still match it."""
    linked_ids = sorted(
        location_id
        for location_id in (movement.origin_location_id, movement.destination_location_id)
        if location_id is not None
    )
    locations = {
        location.pk: location
        for location in BoxLocation.objects.select_for_update()
        .filter(pk__in=linked_ids, box=box)
        .order_by("pk")
    }
    if len(locations) != len(linked_ids):
        raise _mismatch(movement, "A period linked to this movement no longer belongs to this box.")
    origin = locations.get(movement.origin_location_id)
    destination = locations.get(movement.destination_location_id)
    if destination is None:
        raise BoxMovementConflict(
            "This movement has no recorded destination period; it cannot be safely corrected.",
            error_code="movement_period_link_missing",
            movement_id=movement.pk,
        )
    if (
        destination.starts_at != movement.moved_at
        or destination.thermal_zone_id != movement.to_thermal_zone_id
    ):
        raise _mismatch(movement, "The destination period no longer matches this movement.")
    if origin is not None and (
        origin.ends_at != movement.moved_at
        or origin.end_date_unknown
        or origin.thermal_zone_id != movement.from_thermal_zone_id
    ):
        raise _mismatch(movement, "The origin period no longer matches this movement.")
    return origin, destination


def _chronology_conflict(movement, detail):
    return BoxMovementConflict(
        detail, error_code="movement_chronology_conflict", movement_id=movement.pk,
    )


def _validate_date_correction(*, box, movement, origin, destination, moved_at):
    if movement.kind not in DATE_CORRECTABLE_KINDS:
        raise BoxMovementConflict(
            "Only the date of an ordinary movement can be corrected; this movement is bound "
            "to a lifecycle event or has no established provenance.",
            error_code="movement_date_not_correctable",
            movement_id=movement.pk,
            movement_kind=movement.kind,
        )
    # The recorded event stays in its original Paris ISO week (Monday to Sunday).
    original_week = BoxMovement.week_start_for(movement.moved_at)
    if BoxMovement.week_start_for(moved_at) != original_week:
        raise BoxMovementInvalid(
            {"moved_at": ["The corrected date must stay within the ISO week of the original movement."]},
            error_code="movement_date_outside_week",
        )
    if origin is None:
        raise BoxMovementConflict(
            "This movement has no recorded origin period; its date cannot be safely corrected.",
            error_code="movement_period_link_missing",
            movement_id=movement.pk,
        )
    if moved_at > timezone.now():
        raise BoxMovementInvalid(
            {"moved_at": ["A recorded movement cannot be dated in the future."]},
            error_code="movement_date_in_future",
        )
    if moved_at <= origin.starts_at:
        raise _chronology_conflict(
            movement, "The corrected date must be after the start of the origin period.",
        )
    if destination.ends_at is not None and moved_at >= destination.ends_at:
        raise _chronology_conflict(
            movement, "The corrected date must be before the end of the destination period.",
        )
    if destination.end_date_unknown and moved_at > movement.moved_at:
        raise _chronology_conflict(
            movement,
            "The destination period ended at an unknown date; the movement cannot be moved later.",
        )

    # The movements opening the origin period and closing the destination
    # period sit exactly on the span bounds; anything strictly inside is foreign.
    span_start = origin.starts_at
    span_end = destination.ends_at
    other_movements = BoxMovement.objects.filter(box=box, moved_at__gt=span_start).exclude(pk=movement.pk)
    if span_end is not None:
        other_movements = other_movements.filter(moved_at__lt=span_end)
    if other_movements.exists():
        raise _chronology_conflict(
            movement, "Another movement of this box lies within the affected periods.",
        )
    # Periods overlapping the affected span make the history ambiguous. A period
    # with an unknown end that started before the span stays as ambiguous with or
    # without this correction, so it does not block it.
    others = BoxLocation.objects.filter(box=box).exclude(pk__in=[origin.pk, destination.pk])
    if span_end is not None:
        others = others.filter(starts_at__lt=span_end)
    overlapping = others.filter(
        Q(ends_at__gt=span_start)
        | Q(ends_at__isnull=True, end_date_unknown=False)
        | Q(end_date_unknown=True, starts_at__gte=span_start)
    )
    if overlapping.exists():
        raise BoxMovementConflict(
            "Another location period of this box overlaps the affected periods.",
            error_code="movement_period_overlap",
            movement_id=movement.pk,
        )


def _validate_destination_correction(*, box, movement, origin, destination, thermal_zone):
    if movement.kind not in DESTINATION_CORRECTABLE_KINDS:
        raise BoxMovementConflict(
            "The destination of a movement without established provenance cannot be corrected.",
            error_code="movement_provenance_unresolved",
            movement_id=movement.pk,
        )
    if thermal_zone.pk == movement.from_thermal_zone_id:
        raise BoxMovementInvalid(
            {"to_thermal_zone_id": ["The destination must differ from the origin zone."]},
            error_code="movement_same_zone",
        )
    later_movements = BoxMovement.objects.filter(box=box, moved_at__gte=movement.moved_at).exclude(
        pk=movement.pk,
    )
    other_open = BoxLocation.objects.filter(
        box=box, ends_at__isnull=True, end_date_unknown=False,
    ).exclude(pk=destination.pk)
    if (
        destination.ends_at is not None
        or destination.end_date_unknown
        or box.status != Box.Status.ACTIVE
        or box.thermal_zone_id != movement.to_thermal_zone_id
        or later_movements.exists()
        or other_open.exists()
    ):
        raise BoxMovementConflict(
            "Only the destination of the box's latest movement, while it is still its current "
            "location, can be corrected; later history would otherwise be rewritten.",
            error_code="movement_not_terminal",
            movement_id=movement.pk,
        )


def correct_box_movement(*, box_id, movement_id, organization_ids, user, data):
    """Correct notes, date or destination of one movement of a scoped box.

    ``organization_ids`` is the server-resolved active institution scope.
    ``data`` is a field mapping, or a callable returning it after the scope
    and permission checks. Permission is rechecked under the Box lock.
    """
    organization_ids = tuple(organization_ids)
    box = _scoped_box(box_id=box_id, organization_ids=organization_ids)
    _require_correction_permission(user=user, box=box)
    values = _validated_correction_data(data=data, box=box)

    with transaction.atomic():
        box = _scoped_box(box_id=box_id, organization_ids=organization_ids, for_update=True)
        _require_correction_permission(user=user, box=box)
        try:
            movement = (
                BoxMovement.objects.select_for_update(of=("self",))
                .select_related("from_thermal_zone", "to_thermal_zone")
                .get(pk=movement_id, box=box)
            )
        except BoxMovement.DoesNotExist as exc:
            raise BoxMovementNotFound("BoxMovement") from exc

        if movement.revision != values["expected_revision"]:
            raise BoxMovementConflict(
                "This movement changed since the correction was prepared. Refresh the box.",
                error_code="box_movement_changed",
                movement_id=movement.pk,
                expected_revision=values["expected_revision"],
                current_revision=movement.revision,
            )

        changes = {}
        if "moved_at" in values and values["moved_at"] != movement.moved_at:
            changes["moved_at"] = values["moved_at"]
        if "to_thermal_zone" in values and values["to_thermal_zone"].pk != movement.to_thermal_zone_id:
            # Revalidate inside the transaction: the zone may have changed since parsing.
            zone = ThermalZone.objects.filter(
                pk=values["to_thermal_zone"].pk,
                organization_id=box.organization_id,
                is_active=True,
            ).first()
            if zone is None:
                raise BoxMovementInvalid(
                    {"to_thermal_zone_id": ["The zone must be active and belong to the box organization."]},
                )
            changes["to_thermal_zone"] = zone
        if "notes" in values and values["notes"] != movement.notes:
            changes["notes"] = values["notes"]
        if not changes:
            raise BoxMovementInvalid(
                {"non_field_errors": ["The correction does not change this movement."]},
                error_code="movement_correction_no_change",
            )

        origin = destination = None
        if "moved_at" in changes or "to_thermal_zone" in changes:
            if "moved_at" in changes and movement.kind == BoxMovement.Kind.UNCLASSIFIED:
                raise BoxMovementConflict(
                    "The provenance of this movement is not established; its date cannot be corrected.",
                    error_code="movement_provenance_unresolved",
                    movement_id=movement.pk,
                )
            origin, destination = _locked_linked_periods(box=box, movement=movement)
        elif movement.destination_location_id is not None:
            destination = BoxLocation.objects.select_for_update().filter(
                pk=movement.destination_location_id, box=box,
            ).first()

        if "moved_at" in changes:
            _validate_date_correction(
                box=box, movement=movement, origin=origin, destination=destination,
                moved_at=changes["moved_at"],
            )
            if movement.kind == BoxMovement.Kind.ORDINARY:
                try:
                    check_ordinary_movement_week(
                        box=box, moved_at=changes["moved_at"], movement=movement,
                    )
                except BoxMovementWeekConflict as exc:
                    raise BoxMovementCorrectionWeekConflict(
                        movement_id=exc.movement_id, iso_week_start=exc.iso_week_start,
                    ) from exc
        if "to_thermal_zone" in changes:
            _validate_destination_correction(
                box=box, movement=movement, origin=origin, destination=destination,
                thermal_zone=changes["to_thermal_zone"],
            )

        corrected_at = timezone.now()
        revision_before = movement.revision
        movement_before = _movement_values(movement)
        periods = [location for location in (origin, destination) if location is not None]
        periods_before = {location.pk: _period_values(location) for location in periods}
        zone_before = box.thermal_zone

        movement_fields = {"revision"}
        if "moved_at" in changes:
            origin.ends_at = changes["moved_at"]
            origin.save(update_fields=["ends_at"])
            destination.starts_at = changes["moved_at"]
            destination.save(update_fields=["starts_at"])
            movement.moved_at = changes["moved_at"]
            movement_fields.add("moved_at")
        if "to_thermal_zone" in changes:
            destination.thermal_zone = changes["to_thermal_zone"]
            destination.save(update_fields=["thermal_zone"])
            movement.to_thermal_zone = changes["to_thermal_zone"]
            movement_fields.add("to_thermal_zone")
            box.thermal_zone = changes["to_thermal_zone"]
            box.save(update_fields=["thermal_zone"])
        if "notes" in changes:
            # The movement service copies its notes onto the destination period;
            # only that proven copy follows the correction.
            if destination is not None and destination.notes == movement.notes:
                destination.notes = changes["notes"]
                destination.save(update_fields=["notes"])
            movement.notes = changes["notes"]
            movement_fields.add("notes")
        movement.revision = revision_before + 1
        movement.save(update_fields=movement_fields)

        movement_after = _movement_values(movement)
        period_changes = []
        for location in periods:
            after = _period_values(location)
            if after != periods_before[location.pk]:
                period_changes.append({
                    "location_id": location.pk,
                    "role": "origin" if location is origin else "destination",
                    "before": periods_before[location.pk],
                    "after": after,
                })
        current_zone_change = (
            {"before": _zone_ref(zone_before), "after": _zone_ref(box.thermal_zone)}
            if "to_thermal_zone" in changes
            else None
        )
        legacy_values = {
            "ancienne_zone": movement_after["from_thermal_zone"]["name"] if movement.from_thermal_zone else None,
            "nouvelle_zone": movement_after["to_thermal_zone"]["name"],
            "emplacement": movement_after["to_thermal_zone"]["name"],
            "date_deplacement": movement_after["moved_at"],
            "note": movement_after["notes"],
        }
        legacy_before = {
            "nouvelle_zone": movement_before["to_thermal_zone"]["name"],
            "emplacement": movement_before["to_thermal_zone"]["name"],
            "date_deplacement": movement_before["moved_at"],
            "note": movement_before["notes"],
        }
        AuditLog.objects.create(
            organization=box.organization,
            user=user,
            action=AuditLog.Action.UPDATE,
            object_type="box",
            object_id=box.global_code,
            description=f"Box movement corrected: {box.global_code}",
            metadata={
                "event": "box_movement_correction",
                "box_id": box.pk,
                "movement_id": movement.pk,
                "movement_kind": movement.kind,
                "original_author_id": movement.user_id,
                "corrected_at": corrected_at.isoformat(),
                "revision": {"before": revision_before, "after": movement.revision},
                "changed_fields": [field for field in CORRECTABLE_FIELDS if field in changes],
                "movement_before": movement_before,
                "movement_after": movement_after,
                "period_changes": period_changes,
                "current_thermal_zone_change": current_zone_change,
                "valeurs": legacy_values,
                "modifications": {
                    key: {"avant": legacy_before[key], "apres": legacy_values[key]}
                    for key in legacy_before
                    if legacy_before[key] != legacy_values[key]
                },
            },
        )
    return movement
