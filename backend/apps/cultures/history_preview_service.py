"""Authorized, bounded, read-only preparation of internal history previews.

Sessions are short-lived trusted backend/request-local state. Never serialize,
persist or share one as an authorization capability: every new request must
reload the actor, check authority and prepare fresh evidence.

The outer snapshot covers permission checks, budgets and both unchanged source
loaders. Only an outermost PostgreSQL transaction owned by the existing wrapper
provides REPEATABLE READ, READ ONLY and stable count-then-load reads. Nested
calls inherit the caller's isolation; SQLite has no equivalent guarantee. Loaded
output counts are checked too, but cannot retroactively bound allocation or
promise cross-loader coherence under inherited/unsupported isolation.
"""

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied

from apps.accounts.models import OrganizationMembership
from apps.audit.box_lookup import box_audits
from apps.measurements.models import (
    BiologicalMeasurement, DailyTemperature, HistoricalTemperatureEvidence,
    HistoricalTemperatureReading, Probe, TemperatureMeasurement,
)
from apps.measurements.probe_readings import overlapping_probe_buckets
from apps.organizations.models import Organization

from .effective_history_sources import HistorySourceNotFound, load_box_source_history
from .effective_temperature_sources import load_box_thermal_sources
from .history_preview import HistoryPreview, HistoryPreviewSession
from .history_preview_validation import PreviewInputError, validate_draft, validate_scope_ids, validate_window
from .location_history_diagnostics import _read_only_snapshot
from .models import MOVEMENT_WEEK_TIME_ZONE, Box, BoxLocation, BoxMovement, HistoricalBoxMovement, ThermalZone

# Technical resource limits, not scientific validation or publication policy.
MAX_HISTORY_ROWS = 5000
MAX_REFERENCE_ROWS = 10000
MAX_THERMAL_EVIDENCE = 50000


def _authorized_box(*, actor, organization_id, box_id):
    """Require fresh product Admin membership, not shared superuser break-glass."""
    user_model = get_user_model()
    if (not isinstance(actor, user_model) or not actor.is_authenticated
            or actor._state.adding or type(actor.pk) is not int or actor.pk <= 0):
        raise PermissionDenied("An active institution administrator is required.")
    fresh_actor = user_model.objects.filter(pk=actor.pk, is_active=True).first()
    organization = Organization.objects.filter(pk=organization_id, is_active=True).first()
    if (fresh_actor is None or organization is None
            or not OrganizationMembership.objects.filter(
                user=fresh_actor, organization=organization, is_active=True,
                role=OrganizationMembership.Role.ADMIN,
            ).exists()):
        raise PermissionDenied("An active institution administrator is required.")
    box = Box.objects.filter(
        pk=box_id, organization_id=organization_id, merged_into__isnull=True,
    ).first()
    if box is None:
        raise HistorySourceNotFound("No such Box in this institution.")
    return box


def _count_budget(querysets, limit, code):
    total = 0
    for queryset in querysets:
        total += queryset.count()
        if total > limit:
            raise PreviewInputError(code)
    return total


def _check_budgets(box, start, end):
    """Count every potentially loaded row, without materializing source data.

    History is deliberately all-time: the adapters need evidence outside the
    thermal window. Include duplicate biology and malformed foreign-zone links
    that the reviewed adapters diagnose or sanitize, rather than hiding them.
    References include every institutional zone and probe, even inactive ones;
    reusable draft destinations must not depend on the recorded itinerary.
    """
    organization_id = box.organization_id
    scope = {"box_id": box.pk, "box__organization_id": organization_id}
    readings = HistoricalTemperatureReading.objects.filter(**{
        f"biological_measurement__{key}": value for key, value in scope.items()
    })
    codes = tuple(code for code in (box.global_code, box.previous_global_code) if code)
    _count_budget((
        BoxMovement.objects.filter(**scope),
        BoxLocation.objects.filter(**scope),
        HistoricalBoxMovement.objects.filter(**scope),
        BiologicalMeasurement.objects.filter(**scope),
        readings,
        HistoricalTemperatureEvidence.objects.filter(**{
            f"reading__biological_measurement__{key}": value for key, value in scope.items()
        }),
        box_audits(organization_id=organization_id, box_ids=[box.pk], codes=codes),
    ), MAX_HISTORY_ROWS, "history_row_limit_exceeded")
    _count_budget((
        ThermalZone.objects.filter(organization_id=organization_id),
        Probe.objects.filter(organization_id=organization_id),
    ), MAX_REFERENCE_ROWS, "reference_row_limit_exceeded")
    probe_scope = {
        "probe__organization_id": organization_id,
        "probe__thermal_zone__organization_id": organization_id,
    }
    _count_budget((
        TemperatureMeasurement.objects.filter(**probe_scope, measured_at__gte=start, measured_at__lt=end),
        overlapping_probe_buckets(start, end).filter(**probe_scope),
        DailyTemperature.objects.filter(
            thermal_zone__organization_id=organization_id,
            date__gte=start.astimezone(MOVEMENT_WEEK_TIME_ZONE).date(),
            date__lte=end.astimezone(MOVEMENT_WEEK_TIME_ZONE).date(),
        ),
        # Conservative all-time bound also covers malformed weeks and findings.
        readings,
    ), MAX_THERMAL_EVIDENCE, "thermal_evidence_limit_exceeded")


def _check_loaded_budgets(source, thermal_source):
    # Generated events/findings are independently bounded, including expansion
    # of one stored location into two boundary events. No output is truncated.
    if len(source.events) + len(source.occupancy_evidence) + len(source.findings) > MAX_HISTORY_ROWS:
        raise PreviewInputError("history_row_limit_exceeded")
    if max(len(source.zone_ids), len(thermal_source.zone_ids)) + len(thermal_source.probe_ids) > MAX_REFERENCE_ROWS:
        raise PreviewInputError("reference_row_limit_exceeded")
    if len(thermal_source.evidence) + len(thermal_source.findings) > MAX_THERMAL_EVIDENCE:
        raise PreviewInputError("thermal_evidence_limit_exceeded")


def prepare_box_history_preview(*, actor, organization_id, box_id, start, end, anchors=()) -> HistoryPreviewSession:
    """Prepare an authorized immutable baseline for zero-query local drafts.

    Snapshot ownership is intentionally not a session field or freshness token.
    See the module caveat: inherited transactions do not gain stronger isolation.
    """
    validate_scope_ids(organization_id, box_id)
    start, end = validate_window(start, end)
    _, anchors, _ = validate_draft(anchors=anchors, organization_id=organization_id, box_id=box_id)
    with _read_only_snapshot():
        box = _authorized_box(actor=actor, organization_id=organization_id, box_id=box_id)
        _check_budgets(box, start, end)
        source = load_box_source_history(organization_id=organization_id, box_id=box_id)
        thermal_source = load_box_thermal_sources(
            organization_id=organization_id, box_id=box_id, start=start, end=end,
        )
        _check_loaded_budgets(source, thermal_source)
        return HistoryPreviewSession(source, thermal_source, anchors=anchors)


def preview_box_history(*, actor, organization_id, box_id, start, end, operations=(), anchors=(), justifications=()) -> HistoryPreview:
    """Validate the typed batch before any reads, then simulate without writes."""
    validate_scope_ids(organization_id, box_id)
    start, end = validate_window(start, end)
    operations, anchors, justifications = validate_draft(
        operations, anchors, justifications, organization_id=organization_id, box_id=box_id,
    )
    session = prepare_box_history_preview(
        actor=actor, organization_id=organization_id, box_id=box_id, start=start, end=end, anchors=anchors,
    )
    return session.preview(operations=operations, justifications=justifications)
