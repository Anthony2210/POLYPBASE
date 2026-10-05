"""Resolve absolute polyp sources without inventing weekly measurements."""

from django.db.models import F, OuterRef, Prefetch, Q, Subquery, Value
from django.db.models.functions import Coalesce

from apps.measurements.models import BiologicalMeasurement

from .models import SubcultureAllocation, SubcultureEvent


MEASUREMENT_STATE_ORDER = ("-measured_on", "-state_order", "-created_at", "-pk")


def _measurement_candidates():
    events = SubcultureEvent.objects.filter(
        parent_box_id=OuterRef("box_id"), occurred_at__isnull=False,
                allocated_polyp_count__isnull=False, parent_polyp_count_after__isnull=False,
    ).order_by("-parent_state_sequence", "-pk")
    initialization = SubcultureAllocation.objects.filter(
        child_box_id=OuterRef("box_id"), event__occurred_at__isnull=False,
                allocated_polyps__isnull=False,
        event__parent_box__organization_id=F("child_box__organization_id"),
    )
    return BiologicalMeasurement.objects.annotate(
        state_order=Coalesce("polyp_state_sequence", Value(0)),
        operation_sequence=Coalesce(
            Subquery(events.values("parent_state_sequence")[:1]),
            Subquery(initialization.values("child_state_sequence")[:1]), Value(0),
        ),
        operation_day=Coalesce(
            Subquery(events.values("event_date")[:1]),
            Subquery(initialization.values("event__event_date")[:1]),
        ),
    ).filter(
        Q(operation_sequence=0)
        | Q(polyp_state_sequence__gt=F("operation_sequence"), measured_on__gte=F("operation_day"))
    )


def current_state_prefetches():
    measurement = _measurement_candidates()
    latest_id = measurement.filter(box_id=OuterRef("box_id")).order_by(
        *MEASUREMENT_STATE_ORDER,
    ).values("pk")[:1]
    events = SubcultureEvent.objects.filter(
            occurred_at__isnull=False, allocated_polyp_count__isnull=False,
            parent_polyp_count_after__isnull=False,
        )
    event_id = events.filter(parent_box_id=OuterRef("parent_box_id")).order_by(
        "-parent_state_sequence", "-pk",
    ).values("pk")[:1]
    initialization = SubcultureAllocation.objects.select_related("event", "event__parent_box").filter(
        event__occurred_at__isnull=False, allocated_polyps__isnull=False,
        event__parent_box__organization_id=F("child_box__organization_id"),
    )
    return [
        Prefetch("biological_measurements", queryset=measurement.filter(pk=Subquery(latest_id)), to_attr="polyp_state_measurements"),
        Prefetch("source_subculture_events", queryset=events.filter(pk=Subquery(event_id)), to_attr="polyp_state_events"),
        Prefetch("subculture_initialization", queryset=initialization, to_attr="polyp_state_initialization"),
    ]


def state_revision(box):
    return f"box:{box.pk}:{box.polyp_state_revision}"


def resolve_current_polyp_state(box):
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
        result = {
            "polyp_count": measurement.polyp_count,
            "source": {"kind": "measurement", "id": measurement.pk,
                       "timestamp": measurement.created_at.isoformat(),
                       "measured_on": measurement.measured_on.isoformat()},
        }
        return {**result, "revision": state_revision(box)}

    events = getattr(box, "polyp_state_events", None)
    if events is None:
        events = box.source_subculture_events.filter(
                    occurred_at__isnull=False, allocated_polyp_count__isnull=False,
                    parent_polyp_count_after__isnull=False,
                ).order_by("-parent_state_sequence", "-pk")[:1]
    event = next(iter(events), None)
    if event is not None:
        result = {
            "polyp_count": event.parent_polyp_count_after,
            "source": {"kind": "subculture", "id": event.pk, "timestamp": event.occurred_at.isoformat()},
        }
        return {**result, "revision": state_revision(box)}

    if hasattr(box, "polyp_state_initialization"):
        allocation = box.polyp_state_initialization
    else:
        allocation = SubcultureAllocation.objects.select_related("event").filter(
            child_box=box, event__occurred_at__isnull=False, allocated_polyps__isnull=False,
            event__parent_box__organization_id=box.organization_id,
        ).first()
    if allocation:
        result = {
            "polyp_count": allocation.allocated_polyps,
            "source": {"kind": "subculture_initialization", "id": allocation.pk,
                       "timestamp": allocation.event.occurred_at.isoformat()},
        }
        return {**result, "revision": state_revision(box)}
    return {"polyp_count": None, "source": None, "revision": state_revision(box)}


def current_polyp_total(boxes):
    states = [resolve_current_polyp_state(box)["polyp_count"] for box in boxes]
    return {"polyp_count": sum(value for value in states if value is not None), "unknown_box_count": sum(value is None for value in states)}
