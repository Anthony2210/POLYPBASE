"""Read-only institution-scoped previews, not an application authorization or lock."""

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal
import hashlib
import json

from django.contrib.auth import get_user_model
from django.db.models import Q
from rest_framework.exceptions import PermissionDenied

from apps.accounts.models import OrganizationMembership
from apps.cultures.models import Box, SubcultureAllocation, SubcultureEvent
from apps.organizations.models import Organization

from .canonical import PAYLOAD_FIELDS, SCHEMA, VERSION, Issue, RowResult, SourceCoordinate
from .csv_import import CsvResult
from .models import BiologicalMeasurement
from .services import get_active_measurement_role

BATCH_SIZE = 200


@dataclass(frozen=True)
class BoxBaseline:
    id: int
    organization_id: int
    global_code: str
    status: str
    polyp_state_revision: int


@dataclass(frozen=True)
class MeasurementBaseline:
    id: int
    box_id: int
    measured_on: str
    week_start: str
    values: tuple[tuple[str, str | int | bool | None], ...]
    observer_id: int | None
    created_at: str
    polyp_state_sequence: int | None


@dataclass(frozen=True)
class SourceSlotGroup:
    id: int
    code: str
    sources: tuple[SourceCoordinate, ...]


@dataclass(frozen=True)
class SubcultureEvidence:
    event_id: int
    event_date: str
    role: str
    allocation_id: int | None = None


@dataclass(frozen=True)
class ChronologyConflict:
    id: int
    box_id: int
    iso_year: int
    iso_week: int
    week_start: str
    week_end: str
    operations: tuple[SubcultureEvidence, ...]


@dataclass(frozen=True)
class PlannedRow:
    source: RowResult
    outcome: str
    box: BoxBaseline | None = None
    week_start: str | None = None  # Slot key only, never a source measurement date.
    existing: MeasurementBaseline | None = None
    issues: tuple[Issue, ...] = ()
    duplicate_group_id: int | None = None
    chronology_conflict_id: int | None = None

    @property
    def blocked(self):
        return bool(self.issues) or self.outcome in {"CONFLICT", "UNKNOWN_BOX", "INVALID", "BLOCKED"}


@dataclass(frozen=True)
class PlanSummary:
    total_source_rows: int
    ignored_rows: int
    create_candidates: int
    already_matching: int
    conflicts: int
    unknown_boxes: int
    invalid_or_blocked: int
    blocked_rows: int
    ready: bool


@dataclass(frozen=True)
class MeasurementImportPlan:
    organization_id: int
    importing_actor_id: int
    preparation_fingerprint: str
    source_complete: bool
    rows: tuple[PlannedRow, ...]
    issues: tuple[Issue, ...]
    duplicate_groups: tuple[SourceSlotGroup, ...] = ()
    schema: str = "polypbase.biological-measurement-plan"
    schema_version: str = "1.1"
    chronology_conflicts: tuple[ChronologyConflict, ...] = ()

    @property
    def summary(self):
        counts = Counter(row.outcome for row in self.rows)
        blocked = sum(row.blocked for row in self.rows)
        return PlanSummary(
            total_source_rows=len(self.rows), ignored_rows=counts["IGNORED"],
            create_candidates=counts["CREATE"], already_matching=counts["ALREADY_MATCHES"],
            conflicts=counts["CONFLICT"], unknown_boxes=counts["UNKNOWN_BOX"],
            invalid_or_blocked=sum(
                row.outcome in {"INVALID", "BLOCKED"} or (row.outcome == "CREATE" and row.blocked)
                for row in self.rows
            ),
            blocked_rows=blocked, ready=self.source_complete and not self.issues and not blocked,
        )

    @property
    def ready(self):
        return self.summary.ready

    def to_dict(self):
        return {**asdict(self), "summary": asdict(self.summary)}

    def canonical_json(self):
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    @property
    def fingerprint(self):
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


def _batches(items):
    for start in range(0, len(items), BATCH_SIZE):
        yield items[start:start + BATCH_SIZE]


def authorize_measurement_import(actor, organization):
    """Reload and authorize the explicit institution's active laboratory member."""
    if not actor or not actor.is_authenticated or not actor.pk:
        raise PermissionDenied(code="measurement_import_forbidden")
    # Reload security state: an object passed by a caller is not authority.
    user = get_user_model().objects.filter(pk=actor.pk, is_active=True).first()
    institution = Organization.objects.filter(pk=organization.pk, is_active=True).first()
    if user is None or institution is None or get_active_measurement_role(
        user=user, organization=institution,
    ) not in {OrganizationMembership.Role.ADMIN, OrganizationMembership.Role.LAB_TECHNICIAN}:
        raise PermissionDenied(code="measurement_import_forbidden")
    return user, institution


def _baseline(measurement):
    values = []
    for field in PAYLOAD_FIELDS:
        value = getattr(measurement, field)
        if field == "salinity_psu" and value is not None:
            value = format(value, ".2f")
        values.append((field, value))
    return MeasurementBaseline(
        measurement.pk, measurement.box_id, measurement.measured_on.isoformat(),
        measurement.week_start.isoformat(), tuple(values), measurement.user_id,
        measurement.created_at.isoformat(), measurement.polyp_state_sequence,
    )


def _issue(source, code, field=None):
    column = next((index + 1 for index, (_, mapped) in enumerate(source.provenance.mapping)
                   if mapped == field), None) if field else None
    return Issue(code, field, column)


def _comparison_value(field, value):
    if field == "salinity_psu" and value is not None:
        return Decimal(value)
    return value


def _differences(source, baseline):
    current = dict(baseline.values)
    issues = []
    if source.row.time.precision == "exact_date" and source.row.time.measured_on != baseline.measured_on:
        issues.append(_issue(source, "exact_date_conflict", "measured_on"))
    for supplied in source.row.values:
        if supplied.field not in PAYLOAD_FIELDS or supplied.state not in {"value", "null"}:
            continue
        # A nullable supplied blank is explicit unknown, not zero. Absent and
        # nonnullable blank fields have no supplied value and impose no default.
        if _comparison_value(supplied.field, supplied.value) != _comparison_value(
            supplied.field, current[supplied.field],
        ):
            issues.append(_issue(source, "supplied_value_conflict", supplied.field))
    return tuple(issues)


def _source_slot_conflicts(group):
    supplied = {}
    for source in group:
        values = {value.field: _comparison_value(value.field, value.value) for value in source.row.values
                  if value.field in PAYLOAD_FIELDS and value.state in {"value", "null"}}
        if source.row.time.measured_on is not None:
            values["measured_on"] = source.row.time.measured_on
        for field, value in values.items():
            if field in supplied and supplied[field] != value:
                return True
            supplied[field] = value
    return False


def _chronology_conflicts(keys, organization_id):
    """Read exact slot ranges for known parent occurrences and child allocations.

    Week boundaries are search keys only. Neither sequences nor ingestion times
    establish when a week-only observation happened relative to an operation.
    """
    evidence = defaultdict(list)
    for batch in _batches(keys):
        parent_lookup = Q()
        child_lookup = Q()
        for box_id, week in batch:
            iso = week.isocalendar()
            end = date.fromisocalendar(iso.year, iso.week, 7)
            parent_lookup |= Q(parent_box_id=box_id, event_date__range=(week, end))
            child_lookup |= Q(child_box_id=box_id, event__event_date__range=(week, end))
        # Partial counts do not erase occurrence evidence. Unlike absolute-state
        # resolution, parent chronology does not require a known numeric result.
        events = SubcultureEvent.objects.filter(
            parent_lookup, parent_box__organization_id=organization_id,
            occurred_at__isnull=False,
        ).order_by("event_date", "pk").values_list("pk", "parent_box_id", "event_date")
        for event_id, box_id, day in events:
            iso = day.isocalendar()
            key = (box_id, date.fromisocalendar(iso.year, iso.week, 1))
            evidence[key].append(SubcultureEvidence(event_id, day.isoformat(), "parent"))
        allocations = SubcultureAllocation.objects.filter(
            child_lookup, child_box__organization_id=organization_id,
            event__parent_box__organization_id=organization_id,
            event__occurred_at__isnull=False, allocated_polyps__isnull=False,
        ).order_by("event__event_date", "event_id", "pk").values_list(
            "pk", "event_id", "child_box_id", "event__event_date",
        )
        for allocation_id, event_id, box_id, day in allocations:
            iso = day.isocalendar()
            key = (box_id, date.fromisocalendar(iso.year, iso.week, 1))
            evidence[key].append(SubcultureEvidence(event_id, day.isoformat(), "child", allocation_id))
    conflicts = {}
    for key in sorted(evidence):
        box_id, week = key
        iso = week.isocalendar()
        operations = tuple(sorted(evidence[key], key=lambda item: (
            item.event_date, item.event_id, item.role, item.allocation_id or 0,
        )))
        conflicts[key] = ChronologyConflict(
            len(conflicts) + 1, box_id, iso.year, iso.week, week.isoformat(),
            date.fromisocalendar(iso.year, iso.week, 7).isoformat(), operations,
        )
    return conflicts


def build_measurement_import_plan(preparation: CsvResult, *, organization, actor) -> MeasurementImportPlan:
    """Compare a Phase 2 result under current lab-write authority, without writes.

    Valid rows in incomplete/invalid preparations are diagnostic candidates only.
    All members of a repeated Box/week group are conflicts, including identical
    repeats. A CREATE with issues is a non-executable candidate. No row is an
    instruction to overwrite data, and readiness never permits partial application.
    """
    user, institution = authorize_measurement_import(actor, organization)
    if not isinstance(preparation, CsvResult):
        raise TypeError("Expected a canonical CSV preparation result.")
    plan_issues = list(preparation.issues)
    supported = preparation.schema == SCHEMA and preparation.schema_version == VERSION
    if not supported:
        plan_issues.append(Issue("unsupported_preparation_version"))
    if not preparation.complete:
        plan_issues.append(Issue("incomplete_preparation"))

    valid = [record for record in preparation.records if supported and record.status == "valid"]
    references = sorted({next(value.value for value in record.row.values if value.field == "box_reference")
                         for record in valid})
    boxes = {}
    for batch in _batches(references):
        for box in Box.objects.filter(organization_id=institution.pk, global_code__in=batch).order_by("pk"):
            boxes[box.global_code] = BoxBaseline(
                box.pk, box.organization_id, box.global_code, box.status, box.polyp_state_revision,
            )

    resolved = {}
    groups = defaultdict(list)
    for record in valid:
        reference = next(value.value for value in record.row.values if value.field == "box_reference")
        box = boxes.get(reference)
        # Monday is the existing uniqueness lookup key, not an invented date.
        week = date.fromisocalendar(record.row.time.iso_year, record.row.time.iso_week, 1)
        key = (box.id if box else reference, week)
        resolved[record.provenance.coordinate] = (box, week, key)
        groups[key].append(record)

    # Store group coordinates once, including in JSON; repeating them on each
    # row would turn a bounded 10,000-row input into quadratic diagnostics.
    duplicate_groups = {}
    for key, group in groups.items():
        if len(group) > 1:
            code = "contradictory_source_slot" if _source_slot_conflicts(group) else "duplicate_source_slot"
            duplicate_groups[key] = SourceSlotGroup(
                len(duplicate_groups) + 1, code,
                tuple(item.provenance.coordinate for item in group),
            )

    keys = sorted({(box.id, week) for box, week, _ in resolved.values() if box is not None})
    existing = {}
    for batch in _batches(keys):
        weeks = defaultdict(list)
        for box_id, week in batch:
            weeks[week].append(box_id)
        lookup = Q()
        for week, box_ids in weeks.items():
            lookup |= Q(box_id__in=box_ids, week_start=week)
        for measurement in BiologicalMeasurement.objects.filter(
            lookup, box__organization_id=institution.pk,
        ).order_by("pk"):
            existing[(measurement.box_id, measurement.week_start)] = _baseline(measurement)

    # A satisfied slot is not a new observation or a precision upgrade. Preserve
    # ALREADY_MATCHES; analyze unsatisfied week-only rows, including duplicates.
    chronology_keys = set()
    for source in valid:
        box, week, key = resolved[source.provenance.coordinate]
        if box is None or source.row.time.precision != "iso_week":
            continue
        baseline = existing.get((box.id, week))
        if key in duplicate_groups or baseline is None or _differences(source, baseline):
            chronology_keys.add((box.id, week))
    chronology = _chronology_conflicts(sorted(chronology_keys), institution.pk)

    rows = []
    for source in preparation.records:
        if source.status == "blank":
            rows.append(PlannedRow(source, "IGNORED"))
            continue
        if source.status != "valid":
            rows.append(PlannedRow(source, "INVALID", issues=source.issues or (Issue("invalid_source_row"),)))
            continue
        if not supported:
            rows.append(PlannedRow(source, "BLOCKED", issues=(Issue("unsupported_preparation_version"),)))
            continue
        box, week, key = resolved[source.provenance.coordinate]
        baseline = existing.get((box.id, week)) if box else None
        issues = []
        duplicate_group = duplicate_groups.get(key)
        if duplicate_group is not None:
            issues.append(Issue(duplicate_group.code))
        if box is None:
            issues.append(_issue(source, "unknown_box", "box_reference"))
            outcome = "UNKNOWN_BOX"
        elif duplicate_group is not None:
            if baseline is not None:
                issues.extend(_differences(source, baseline))
            outcome = "CONFLICT"
        elif baseline is not None:
            issues.extend(_differences(source, baseline))
            outcome = "CONFLICT" if issues else "ALREADY_MATCHES"
        else:
            outcome = "CREATE"
            if box.status == Box.Status.INACTIVE:
                issues.append(Issue("inactive_box"))
            if source.row.time.precision == "iso_week":
                issues.append(Issue("week_precision_persistence_required"))
        conflict = chronology.get((box.id, week)) if box and source.row.time.precision == "iso_week" else None
        if conflict is not None:
            issues.append(Issue("week_subculture_chronology_conflict"))
            outcome = "CONFLICT"
        observer = next(value for value in source.row.values if value.field == "observer_reference")
        if observer.state == "value":
            issues.append(_issue(source, "observer_resolution_unsupported", "observer_reference"))
            if outcome == "ALREADY_MATCHES":
                outcome = "BLOCKED"
        rows.append(PlannedRow(
            source, outcome, box, week.isoformat(), baseline, tuple(issues),
            duplicate_group.id if duplicate_group else None,
            conflict.id if conflict else None,
        ))
    return MeasurementImportPlan(
        institution.pk, user.pk, preparation.fingerprint, preparation.complete,
        tuple(rows), tuple(plan_issues), tuple(duplicate_groups.values()),
        chronology_conflicts=tuple(chronology.values()),
    )
