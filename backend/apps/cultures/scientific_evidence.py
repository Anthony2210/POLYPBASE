"""Opt-in factual summaries for one observation; never resolve Box authority."""

from dataclasses import dataclass
from datetime import date
from enum import Enum

from django.core.exceptions import PermissionDenied
from django.db.models import Case, Count, DateField, F, IntegerField, Max, Min, Q, Value, When

from apps.measurements.scientific_time import ObservationTime, TemporalRelation

from .models import Box, SubcultureAllocation, SubcultureEvent


class EvidenceCategory(str, Enum):
    COMPLETE_PARENT_OPERATION = "complete_parent_operation"
    PARTIAL_PARENT_OPERATION = "partial_parent_operation"
    KNOWN_CHILD_INITIALIZATION = "known_child_initialization"
    UNKNOWN_CHILD_ALLOCATION = "unknown_child_allocation"
    LEGACY_PARENT_OPERATION = "legacy_parent_operation"
    LEGACY_CHILD_OPERATION = "legacy_child_operation"


@dataclass(frozen=True, slots=True)
class SequenceConsumption:
    """Sequence facts only, independent of the temporal relation.

    consumed_count is unknown (None) without an observation source sequence.
    Otherwise it counts barriers whose sequence >= that source sequence. Zero
    means none of these barriers consumed it, not that it is an eligible source.
    """

    barrier_count: int
    latest_barrier_sequence: int
    consumed_count: int | None


@dataclass(frozen=True, slots=True)
class EvidenceSummary:
    category: EvidenceCategory
    relation: TemporalRelation
    event_count: int
    first_operation_day: date | None
    last_operation_day: date | None
    consumption: SequenceConsumption | None
    # These two fields describe the single one-to-one child allocation only.
    allocation_id: int | None = None
    allocated_polyps: int | None = None


@dataclass(frozen=True, slots=True)
class ObservationEvidence:
    box_id: int
    organization_id: int
    observation: ObservationTime
    source_sequence: int | None
    summaries: tuple[EvidenceSummary, ...]


def discover_observation_evidence(*, box: Box, organization_id: int,
                                  observation: ObservationTime,
                                  source_sequence: int | None = None) -> ObservationEvidence:
    """Summarize all operation facts for an explicitly supplied observation.

    The organization must be the caller's server-authorized active scope. The
    persisted Box and both ends of child relationships are checked in SQL. This
    is not a user permission API, write guard, stock selector or import planner.
    No sequence is inferred from Box revision, timestamps or a proposed import.

    Three queries: Box scope, grouped parent history, one-to-one allocation.
    Parent history is aggregated in SQL, not materialized or truncated: at most
    seven groups (three temporal partitions per confirmed category, one legacy
    group), plus at most one child summary. Counts cover every event, but this
    is NOT an exhaustive event collection. Bounds describe each group's actual
    operation days, never an effective observation day or a selected source.
    The indexed Box scope bounds the histories read; work in SQL can still grow
    with that Box's history. No measurements or other Boxes' histories are read.

    Reads do not lock or promise a concurrent-write snapshot. A future write
    consumer would need its own authorized transactional consistency contract.
    """
    if not isinstance(observation, ObservationTime):
        raise ValueError("Evidence discovery requires an ObservationTime.")
    if source_sequence is not None and (type(source_sequence) is not int or source_sequence < 0):
        raise ValueError("A source sequence must be a nonnegative integer or unknown.")
    if not isinstance(box, Box) or box.pk is None or box._state.adding:
        raise ValueError("Evidence discovery requires a persisted Box.")
    database = box._state.db
    if not Box.objects.using(database).filter(pk=box.pk, organization_id=organization_id).exists():
        raise PermissionDenied("The Box is outside the supplied institution scope.")

    first, last = observation.interval
    # Only a confirmed occurrence gives event_date its operation-day meaning,
    # as recorded by create_subculture. Legacy event_date is never used here.
    parents = SubcultureEvent.objects.using(database).filter(
        parent_box_id=box.pk, parent_box__organization_id=organization_id,
    ).order_by().annotate(
        evidence_category=Case(
            When(occurred_at__isnull=True, then=Value(EvidenceCategory.LEGACY_PARENT_OPERATION.value)),
            When(allocated_polyp_count__isnull=False, parent_polyp_count_after__isnull=False,
                 then=Value(EvidenceCategory.COMPLETE_PARENT_OPERATION.value)),
            default=Value(EvidenceCategory.PARTIAL_PARENT_OPERATION.value),
        ),
        confirmed_day=Case(
            When(occurred_at__isnull=False, then=F("event_date")),
            default=Value(None), output_field=DateField(),
        ),
    ).annotate(
        # SQL partitions the interval; compare_operation_day below supplies the
        # public relation, in observation-relative-to-operation direction.
        temporal_partition=Case(
            When(confirmed_day__lt=first, then=Value(-1)),
            When(confirmed_day__gt=last, then=Value(1)),
            default=Value(0),
        ),
    ).values("evidence_category", "temporal_partition").annotate(
        event_count=Count("pk"), first_day=Min("confirmed_day"), last_day=Max("confirmed_day"),
        latest_sequence=Max("parent_state_sequence"),
        consumed_count=Count("pk", filter=Q(parent_state_sequence__gte=source_sequence))
        if source_sequence is not None else Value(None, output_field=IntegerField()),
    ).order_by("evidence_category", "temporal_partition")

    summaries = []
    for row in parents:
        category = EvidenceCategory(row["evidence_category"])
        consumption = None
        if category == EvidenceCategory.COMPLETE_PARENT_OPERATION:
            consumption = SequenceConsumption(row["event_count"], row["latest_sequence"], row["consumed_count"])
        summaries.append(EvidenceSummary(
            category, observation.compare_operation_day(row["first_day"]), row["event_count"],
            row["first_day"], row["last_day"], consumption,
        ))

    allocations = SubcultureAllocation.objects.using(database).filter(
        child_box_id=box.pk, child_box__organization_id=organization_id,
        event__parent_box__organization_id=organization_id,
    ).order_by().values(
        "pk", "allocated_polyps", "child_state_sequence", "event__occurred_at", "event__event_date",
    )
    for row in allocations:
        day = None
        consumption = None
        if row["event__occurred_at"] is None:
            category = EvidenceCategory.LEGACY_CHILD_OPERATION
        else:
            day = row["event__event_date"]
            if row["allocated_polyps"] is None:
                category = EvidenceCategory.UNKNOWN_CHILD_ALLOCATION
            else:
                category = EvidenceCategory.KNOWN_CHILD_INITIALIZATION
                sequence = row["child_state_sequence"]
                consumption = SequenceConsumption(
                    1, sequence, None if source_sequence is None else int(source_sequence <= sequence),
                )
        summaries.append(EvidenceSummary(
            category, observation.compare_operation_day(day), 1, day, day,
            consumption, row["pk"], row["allocated_polyps"],
        ))
    return ObservationEvidence(box.pk, organization_id, observation, source_sequence, tuple(summaries))
