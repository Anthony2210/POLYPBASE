from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.cultures.models import Box

from .models import BiologicalMeasurement
from .serializers import BiologicalMeasurementCreateSerializer

MEASUREMENT_EDIT_WINDOW = timedelta(hours=24)
_ROLE_UNSET = object()


def get_active_measurement_role(*, user, organization):
    """Return the user's active product role for one organization."""
    if not user or not user.is_authenticated:
        return None
    return (
        OrganizationMembership.objects.filter(
            user=user,
            organization=organization,
            is_active=True,
        )
        .values_list("role", flat=True)
        .first()
    )


def get_measurement_editability(*, user, measurement, now=None, role=_ROLE_UNSET):
    """Return server-authoritative correction state for one measurement."""
    if role is _ROLE_UNSET:
        role = get_active_measurement_role(
            user=user,
            organization=measurement.box.organization,
        )

    if role == OrganizationMembership.Role.ADMIN:
        return {
            "can_edit": True,
            "edit_deadline": None,
            "edit_restriction": None,
        }

    if role == OrganizationMembership.Role.LAB_TECHNICIAN:
        deadline = measurement.created_at + MEASUREMENT_EDIT_WINDOW
        can_edit = (now or timezone.now()) < deadline
        return {
            "can_edit": can_edit,
            "edit_deadline": deadline,
            "edit_restriction": None if can_edit else "edit_window_expired",
        }

    return {
        "can_edit": False,
        "edit_deadline": None,
        "edit_restriction": "role_read_only",
    }


class MeasurementWriteError(Exception):
    """A rejected measurement write, independent of its HTTP representation."""


class MeasurementNotFound(MeasurementWriteError):
    def __init__(self, object_name):
        self.detail = f"No {object_name} matches the given query."
        super().__init__(self.detail)


class MeasurementWriteForbidden(MeasurementWriteError):
    def __init__(self, detail, *, error_code):
        self.detail = detail
        self.error_code = error_code
        super().__init__(detail)


class MeasurementWriteInvalid(MeasurementWriteError):
    def __init__(self, detail):
        self.detail = detail
        super().__init__(detail)


class MeasurementWeekConflict(MeasurementWriteError):
    def __init__(self, *, measurement_id, week_start):
        self.measurement_id = measurement_id
        self.week_start = week_start
        super().__init__("A biological measurement already exists for this box and ISO week.")


def _json_value(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def _measurement_audit_values(measurement):
    return {
        "date": _json_value(measurement.measured_on),
        "polypes": measurement.polyp_count,
        "ephyrules": measurement.ephyrae_count,
        "strobiles": measurement.strobila_count,
        "salinite_psu": _json_value(measurement.salinity_psu),
        "statut_culture": measurement.culture_status,
        "a_verifier": measurement.needs_attention,
        "note": measurement.notes,
    }


def _changed_values(before, after):
    return {
        key: {"avant": before.get(key), "apres": after.get(key)}
        for key in after
        if before.get(key) != after.get(key)
    }


def _record_measurement_audit(*, box, measurement, user, action, metadata):
    """Record one immutable business event for a measurement write."""
    return AuditLog.objects.create(
        organization=box.organization,
        user=user,
        action=action,
        object_type="box",
        object_id=box.global_code,
        description=f"Biological measurement for {measurement.measured_on}",
        metadata=metadata,
    )


def _get_measurement_box(*, box_id, organization_ids, for_update=False):
    queryset = Box.objects.select_related("organization").filter(
        organization_id__in=organization_ids,
    )
    if for_update:
        queryset = queryset.select_for_update()
    try:
        return queryset.get(pk=box_id)
    except Box.DoesNotExist as exc:
        raise MeasurementNotFound("Box") from exc


def _validate_measurement_create_permission(*, user, box):
    role = get_active_measurement_role(user=user, organization=box.organization)
    if role not in {
        OrganizationMembership.Role.ADMIN,
        OrganizationMembership.Role.LAB_TECHNICIAN,
    }:
        raise MeasurementWriteForbidden(
            "This account cannot create biological measurements.",
            error_code="measurement_write_forbidden",
        )


def _validated_measurement_data(*, data, measurement=None):
    # HTTP callers defer body parsing until scope and permission checks pass.
    if callable(data):
        data = data()
    serializer = BiologicalMeasurementCreateSerializer(
        measurement, data=data, partial=measurement is not None,
    )
    if not serializer.is_valid():
        raise MeasurementWriteInvalid(serializer.errors)
    return serializer.validated_data


def _check_measurement_week(*, box, measured_on, measurement=None):
    week_start = BiologicalMeasurement.week_start_for(measured_on)
    queryset = BiologicalMeasurement.objects.select_for_update().filter(
        box=box, week_start=week_start,
    )
    if measurement is not None:
        queryset = queryset.exclude(pk=measurement.pk)
    conflict = queryset.first()
    if conflict is not None:
        raise MeasurementWeekConflict(measurement_id=conflict.pk, week_start=week_start)


def create_biological_measurement(*, box_id, organization_ids, user, data):
    """Create one ordinary measurement with the existing entry rules.

    The caller supplies its server-resolved institution scope, not client IDs.
    Active membership is checked independently, then rechecked under the Box
    lock. Values use the live-entry validation contract, not historical import
    defaults. Data is a field mapping, or a callable returning that mapping
    after authorization. No HTTP request or response is needed.
    """
    organization_ids = tuple(organization_ids)
    box = _get_measurement_box(box_id=box_id, organization_ids=organization_ids)
    _validate_measurement_create_permission(user=user, box=box)
    values = _validated_measurement_data(data=data)

    with transaction.atomic():
        box = _get_measurement_box(
            box_id=box_id, organization_ids=organization_ids, for_update=True,
        )
        _validate_measurement_create_permission(user=user, box=box)
        _check_measurement_week(box=box, measured_on=values["measured_on"])
        if box.status == Box.Status.INACTIVE:
            raise MeasurementWriteInvalid("An inactive box cannot receive a new measurement.")
        measurement = BiologicalMeasurement.objects.create(
            box=box, user=user, original_observer=user,
            observer_status=BiologicalMeasurement.ObserverStatus.KNOWN,
            origin=BiologicalMeasurement.Origin.DIRECT_ENTRY, **values,
        )
        _record_measurement_audit(
            box=box,
            measurement=measurement,
            user=user,
            action=AuditLog.Action.ENTRY,
            metadata={
                "measurement_id": measurement.id,
                "valeurs": _measurement_audit_values(measurement),
            },
        )
    return measurement


@transaction.atomic
def correct_biological_measurement(*, box_id, measurement_id, organization_ids, user, data):
    """Correct one locked row without changing its identity or source sequence.

    Historical rows on inactive boxes remain correctable under the existing
    role/window rules. As in interactive PATCH, the correcting actor becomes
    measurement.user; created_at and the original source order are retained.
    """
    box = _get_measurement_box(
        box_id=box_id, organization_ids=organization_ids, for_update=True,
    )
    role = get_active_measurement_role(user=user, organization=box.organization)
    try:
        measurement = BiologicalMeasurement.objects.select_for_update().get(
            box=box, pk=measurement_id,
        )
    except BiologicalMeasurement.DoesNotExist as exc:
        raise MeasurementNotFound("BiologicalMeasurement") from exc
    editability = get_measurement_editability(user=user, measurement=measurement, role=role)
    if not editability["can_edit"]:
        raise MeasurementWriteForbidden(
            "This biological measurement can no longer be corrected by this account.",
            error_code=editability["edit_restriction"],
        )

    values = _validated_measurement_data(data=data, measurement=measurement)
    _check_measurement_week(
        box=box,
        measured_on=values.get("measured_on", measurement.measured_on),
        measurement=measurement,
    )
    # Future reviewed-plan guards belong here, while both rows are locked and
    # before taking the audit snapshot or assigning any corrected values.
    before_values = _measurement_audit_values(measurement)
    for field, value in values.items():
        setattr(measurement, field, value)
    measurement.user = user
    measurement.save()
    after_values = _measurement_audit_values(measurement)
    _record_measurement_audit(
        box=box,
        measurement=measurement,
        user=user,
        action=AuditLog.Action.UPDATE,
        metadata={
            "measurement_id": measurement.id,
            "before": before_values,
            "after": after_values,
            "valeurs": after_values,
            "modifications": _changed_values(before_values, after_values),
        },
    )
    return measurement
