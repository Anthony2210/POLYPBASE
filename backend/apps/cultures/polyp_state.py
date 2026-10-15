"""Resolve absolute polyp sources without inventing weekly measurements."""

from dataclasses import dataclass
from datetime import date
from typing import ClassVar, Literal

from django.db.models import Case, F, FilteredRelation, OuterRef, Prefetch, Q, Subquery, Value, When
from django.db.models.functions import Coalesce

from apps.measurements.models import BiologicalMeasurement
from apps.measurements.scientific_time import ObservationTime, TemporalRelation

from .models import SubcultureAllocation, SubcultureEvent


@dataclass(frozen=True, slots=True)
class KnownPolypState:
    polyp_count: int
    source: dict
    status: ClassVar[Literal["known"]] = "known"

    def __post_init__(self):
        if type(self.polyp_count) is not int or self.polyp_count < 0:
            raise ValueError("A known state requires a nonnegative integer count.")
        if not isinstance(self.source, dict) or not self.source:
            raise ValueError("A known state requires its selected absolute source.")


@dataclass(frozen=True, slots=True)
class NoEligiblePolypSource:
    """No selected absolute source; historical evidence may still exist."""

    status: ClassVar[Literal["no_eligible_source"]] = "no_eligible_source"


@dataclass(frozen=True, slots=True)
class UnresolvedPolypState:
    """Unresolved pair evidence, not a conclusion about the whole Box state."""

    observation: ObservationTime
    operation_day: date | None
    status: ClassVar[Literal["unresolved"]] = "unresolved"

    def __post_init__(self):
        if not isinstance(self.observation, ObservationTime):
            raise ValueError("Unresolved chronology requires an observation time.")
        if self.observation.compare_operation_day(self.operation_day) != TemporalRelation.UNRESOLVED:
            raise ValueError("The provided observation chronology is not unresolved.")


ScientificPolypState = KnownPolypState | NoEligiblePolypSource | UnresolvedPolypState


def unresolved_observation(observation: ObservationTime, operation_day: date | None) -> UnresolvedPolypState | None:
    """Represent uncertainty for this pair only, without source selection.

    None means chronology is resolved, not that a source is eligible or absent.
    Pass None for an unconfirmed occurrence; never substitute a legacy date.
    No count, sequence or older authority is accepted or selected here.
    """
    if observation.compare_operation_day(operation_day) == TemporalRelation.UNRESOLVED:
        return UnresolvedPolypState(observation, operation_day)
    return None


def _legacy_polyp_state(result: ScientificPolypState, revision):
    if isinstance(result, KnownPolypState):
        return {"polyp_count": result.polyp_count, "source": result.source, "revision": revision}
    if isinstance(result, NoEligiblePolypSource):
        return {"polyp_count": None, "source": None, "revision": revision}
    raise RuntimeError("Scientific state contract violation: the legacy facade requires a resolved state.")


MEASUREMENT_STATE_ORDER = ("-measured_on", "-state_order", "-created_at", "-pk")


def _measurement_candidates():
    events = SubcultureEvent.objects.filter(
        parent_box_id=OuterRef("box_id"), occurred_at__isnull=False,
                allocated_polyp_count__isnull=False, parent_polyp_count_after__isnull=False,
    ).order_by("-parent_state_sequence", "-pk")
    known_initialization = Q(
        box__subculture_initialization__event__occurred_at__isnull=False,
        box__subculture_initialization__allocated_polyps__isnull=False,
        box__subculture_initialization__event__parent_box__organization_id=F("box__organization_id"),
    )
    return BiologicalMeasurement.objects.alias(
        # Join the selected operation once, instead of independently looking up
        # its sequence/date in every eligibility expression. Initialization is
        # one-to-one, so its joins cannot multiply measurement candidates.
        latest_operation=FilteredRelation(
            "box__source_subculture_events",
            condition=Q(box__source_subculture_events__pk=Subquery(events.values("pk")[:1])),
        ),
    ).alias(
        state_order=Coalesce("polyp_state_sequence", Value(0)),
        operation_sequence=Coalesce(
            "latest_operation__parent_state_sequence",
            Case(When(known_initialization, then=F("box__subculture_initialization__child_state_sequence"))),
            Value(0),
        ),
        operation_day=Coalesce(
            "latest_operation__event_date",
            Case(When(known_initialization, then=F("box__subculture_initialization__event__event_date"))),
        ),
    ).filter(
        Q(operation_sequence=0)
        | Q(polyp_state_sequence__gt=F("operation_sequence"), measured_on__gte=F("operation_day"))
    )


def current_state_prefetches():
    measurement = _measurement_candidates()

    events = SubcultureEvent.objects.filter(
            occurred_at__isnull=False, allocated_polyp_count__isnull=False,
            parent_polyp_count_after__isnull=False,
        )

    initialization = SubcultureAllocation.objects.select_related("event", "event__parent_box").filter(
        event__occurred_at__isnull=False, allocated_polyps__isnull=False,
        event__parent_box__organization_id=F("child_box__organization_id"),
    )
    return [
        # Django applies these slices per box with ROW_NUMBER(), after filtering
        # eligible sources. Avoid re-running a latest-ID subquery for each row
        # in a box's history (and PostgreSQL JIT on the inflated query cost).
        Prefetch(
            "biological_measurements",
            queryset=measurement.order_by(*MEASUREMENT_STATE_ORDER)[:1],
            to_attr="polyp_state_measurements",
        ),
        Prefetch(
            "source_subculture_events",
            queryset=events.order_by("-parent_state_sequence", "-pk")[:1],
            to_attr="polyp_state_events",
        ),
        Prefetch("subculture_initialization", queryset=initialization, to_attr="polyp_state_initialization"),
    ]


def state_revision(box):
    return f"box:{box.pk}:{box.polyp_state_revision}"


def resolve_scientific_polyp_state(box) -> ScientificPolypState:
    """A transition supersedes every source that existed when it committed.

    Only a newly created measurement on/after that operation's day can supersede
    it. Corrections retain source sequence and cannot rebase a consumed source,
    even if its measured_on was future-dated or is later corrected forward.
    Eligible measurements retain biological-date ordering and legacy time ties.
    Partial operations invalidate revisions but never supersede absolute sources;
    unknown child allocations are evidence, not initialization sources.
    """
    measurements = getattr(box, "polyp_state_measurements", None)
    if measurements is None:
        measurements = _measurement_candidates().filter(box=box).order_by(*MEASUREMENT_STATE_ORDER)[:1]
    measurement = next(iter(measurements), None)
    if measurement is not None:
        return KnownPolypState(
            measurement.polyp_count,
            {"kind": "measurement", "id": measurement.pk,
             "timestamp": measurement.created_at.isoformat(),
             "measured_on": measurement.measured_on.isoformat()},
        )

    events = getattr(box, "polyp_state_events", None)
    if events is None:
        events = box.source_subculture_events.filter(
                    occurred_at__isnull=False, allocated_polyp_count__isnull=False,
                    parent_polyp_count_after__isnull=False,
                ).order_by("-parent_state_sequence", "-pk")[:1]
    event = next(iter(events), None)
    if event is not None:
        return KnownPolypState(
            event.parent_polyp_count_after,
            {"kind": "subculture", "id": event.pk, "timestamp": event.occurred_at.isoformat()},
        )

    if hasattr(box, "polyp_state_initialization"):
        allocation = box.polyp_state_initialization
    else:
        allocation = SubcultureAllocation.objects.select_related("event").filter(
            child_box=box, event__occurred_at__isnull=False, allocated_polyps__isnull=False,
            event__parent_box__organization_id=box.organization_id,
        ).first()
    if allocation:
        return KnownPolypState(
            allocation.allocated_polyps,
            {"kind": "subculture_initialization", "id": allocation.pk,
             "timestamp": allocation.event.occurred_at.isoformat()},
        )
    return NoEligiblePolypSource()


def resolve_current_polyp_state(box):
    """Keep the dated public contract; unresolved evidence must never escape it."""
    return _legacy_polyp_state(resolve_scientific_polyp_state(box), state_revision(box))


def current_polyp_total(boxes):
    states = [resolve_current_polyp_state(box)["polyp_count"] for box in boxes]
    return {"polyp_count": sum(value for value in states if value is not None), "unknown_box_count": sum(value is None for value in states)}
