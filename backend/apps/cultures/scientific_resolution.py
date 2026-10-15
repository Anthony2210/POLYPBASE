"""Opt-in resolution of one proposed week observation against persisted sources."""

from datetime import date

from django.core.exceptions import PermissionDenied

from apps.measurements.models import BiologicalMeasurement
from apps.measurements.scientific_time import ObservationPrecision, ObservationTime, TemporalRelation

from .polyp_state import KnownPolypState, ScientificPolypState, UnresolvedPolypState, resolve_scientific_polyp_state
from .scientific_evidence import discover_observation_evidence


def resolve_week_observation_state(*, box, organization_id, measurement) -> ScientificPolypState:
    """Resolve a non-persisted observation, not an import or writing permission.

    The persisted dated resolver remains authoritative for the existing sources.
    This read-only comparison adds exactly one proposed source in a free ISO
    week. It is not a batch resolver, historical reconstruction or certification
    of legacy history. No date, sequence, timestamp or revision is manufactured.

    Discovery covers the entire operation history in bounded SQL summaries.
    The dated baseline loads at most one source of each kind, using its existing
    prefetches when supplied. Callers must provide fresh objects/prefetches; these
    reads do not lock or guarantee a concurrent-write snapshot. Never call this
    per Box from a public list, aggregate or writing service.
    """
    if not isinstance(measurement, BiologicalMeasurement) or not measurement._state.adding or measurement.pk is not None:
        raise ValueError("Resolution requires a non-persisted week observation.")
    if measurement.box_id != box.pk:
        raise ValueError("The observation must belong to the supplied Box.")
    observation = ObservationTime.from_measurement(measurement)
    if observation.precision != ObservationPrecision.ISO_WEEK:
        raise ValueError("Use the established resolver for dated measurements.")
    if type(measurement.polyp_count) is not int or measurement.polyp_count < 0:
        raise ValueError("An absolute observation requires a known nonnegative count.")
    evidence = discover_observation_evidence(
        box=box, organization_id=organization_id, observation=observation,
        source_sequence=measurement.polyp_state_sequence,
    )
    if box.organization_id != organization_id:
        raise PermissionDenied("The Box is outside the supplied institution scope.")
    if BiologicalMeasurement.objects.using(box._state.db).filter(
        box_id=box.pk, box__organization_id=organization_id, week_start=observation.week_start,
    ).exists():
        # This is input validation for an isolated hypothetical read, not a new
        # writing guard or a policy for replacing/correcting an existing row.
        raise ValueError("The Box already has an observation in this ISO week.")

    baseline = resolve_scientific_polyp_state(box)
    barriers = [row for row in evidence.summaries if row.consumption is not None]
    if any(row.consumption.consumed_count for row in barriers):
        return baseline

    if isinstance(baseline, KnownPolypState) and baseline.source["kind"] == "measurement":
        baseline_day = date.fromisoformat(baseline.source["measured_on"])
        if baseline_day > observation.interval[1]:
            # Even if eligible, this older observation cannot outrank a later
            # eligible absolute measurement. Its pair ambiguity is irrelevant.
            return baseline

    latest_barrier = max(barriers, key=lambda row: row.consumption.latest_barrier_sequence, default=None)
    if latest_barrier is not None:
        if latest_barrier.relation == TemporalRelation.BEFORE:
            return baseline
        if latest_barrier.relation == TemporalRelation.UNRESOLVED:
            return UnresolvedPolypState(observation, latest_barrier.first_operation_day)
        if evidence.source_sequence is None:
            return UnresolvedPolypState(
                observation, latest_barrier.first_operation_day, reason="unknown_source_sequence",
            )

    # Do not choose an older count around a still-relevant partial/unknown/legacy
    # occurrence. These facts do not become absolute transitions or consumption
    # barriers. A consumed proposal and a later eligible measurement were handled
    # above, so an irrelevant old pair cannot poison that authority.
    for row in evidence.summaries:
        if row.consumption is None and row.relation == TemporalRelation.UNRESOLVED:
            return UnresolvedPolypState(observation, row.first_operation_day)

    return KnownPolypState(measurement.polyp_count, {
        "kind": "week_observation", "week_start": observation.week_start.isoformat(),
        "temporal_precision": observation.precision.value,
    })
