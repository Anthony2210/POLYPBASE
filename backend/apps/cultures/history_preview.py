"""Read-only before/after comparison of the reviewed location/thermal engines.

This pure layer accepts trusted, already scoped snapshots. Permission checking
and resource budgets live in history_preview_service. A prepared session is
request-local backend state, not a capability, a durable revision or approval.
No publication policy is inferred from the presence or absence of conflicts.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, fields, is_dataclass, replace
import heapq
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum
from typing import Mapping

from .effective_history import (
    UNSET, Conflict, ConflictCertainty, ConflictCode, EffectiveEvent, HistoryProjection, InsertEvent, LocationSide,
    OccupancyInterval, SourceHistory, TemporalPosition, TemporalPrecision, ThermalSourceKind, project_history,
)
from .effective_temperature import (
    ThermalGap, ThermalPreview, ThermalSegment, ThermalSourceHistory, ThermalStay,
    project_temperature, utc,
)
from .history_preview_validation import (
    OperationJustification, PreviewInputError, validate_draft, validate_window,
)


@dataclass(frozen=True)
class PreviewSafety:
    """Descriptive simulation metadata, never permission to publish."""

    has_conflicts: bool
    has_impossible_conflicts: bool
    has_provisional_occupancy: bool
    has_temporal_uncertainty: bool
    has_unassigned_thermal_evidence: bool
    has_source_findings: bool
    publication_evaluated: bool = False


@dataclass(frozen=True)
class PreviewState:
    history: HistoryProjection
    thermal: ThermalPreview
    safety: PreviewSafety


@dataclass(frozen=True)
class EventChange:
    event_key: str
    before: EffectiveEvent | None
    after: EffectiveEvent
    changed_fields: tuple[str, ...]
    before_position: int | None
    after_position: int | None


@dataclass(frozen=True)
class IntervalChange:
    started_by: str
    before: OccupancyInterval | None
    after: OccupancyInterval | None


@dataclass(frozen=True)
class SideChange:
    event_key: str
    side: str
    before: LocationSide | None
    after: LocationSide | None


@dataclass(frozen=True)
class ThermalChange:
    source_key: str
    before: ThermalSegment
    after: ThermalSegment
    before_assigned: bool
    after_assigned: bool


@dataclass(frozen=True)
class StayChange:
    started_by: str
    before: ThermalStay | None
    after: ThermalStay | None


@dataclass(frozen=True)
class GapChange:
    before: ThermalGap | None
    after: ThermalGap | None


class ConflictEvolution(str, Enum):
    INTRODUCED = "introduced"
    # Only the existing UNCERTAIN -> IMPOSSIBLE classification is aggravated;
    # changed details are not ranked using an invented scientific severity.
    AGGRAVATED = "aggravated"
    CHANGED = "changed"
    PREEXISTING_AFFECTED = "preexisting_affected"
    PREEXISTING_INDEPENDENT = "preexisting_independent"
    NOT_REPORTED_AFTER = "not_reported_after"


@dataclass(frozen=True)
class ConflictChange:
    evolution: ConflictEvolution
    before: Conflict | None
    after: Conflict | None


@dataclass(frozen=True)
class HistoryPreview:
    organization_id: int
    box_id: int
    before: PreviewState
    after: PreviewState
    event_changes: tuple[EventChange, ...]
    interval_changes: tuple[IntervalChange, ...]
    side_changes: tuple[SideChange, ...]
    thermal_changes: tuple[ThermalChange, ...]
    stay_changes: tuple[StayChange, ...]
    gap_changes: tuple[GapChange, ...]
    conflict_changes: tuple[ConflictChange, ...]
    affected_event_keys: tuple[str, ...]
    justifications: tuple[OperationJustification, ...]
    mode: str = "simulation"


def _plain(value):
    """Canonical comparison, not a presentation/API serializer.

    UTC normalization distinguishes repeated local DST instants that Python's
    same-zone wall-clock equality would otherwise conflate.
    """
    if isinstance(value, datetime):
        return utc(value).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        return str(value)
    if is_dataclass(value):
        return {item.name: _plain(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_plain(item) for item in value), key=repr)
    return value


def _signature(value):
    return json.dumps(_plain(value), sort_keys=True, separators=(",", ":"))


def _state(history, thermal):
    boundaries = [position for interval in history.intervals for position in (interval.start, interval.end)
                  if position is not None]
    return PreviewState(history, thermal, PreviewSafety(
        has_conflicts=bool(history.conflicts),
        has_impossible_conflicts=any(item.certainty == ConflictCertainty.IMPOSSIBLE for item in history.conflicts),
        has_provisional_occupancy=any(item.provisional for item in history.intervals),
        has_temporal_uncertainty=(bool(history.unplaced_events)
                                  or any(item.precision != TemporalPrecision.EXACT for item in boundaries)
                                  or any(item.ordering_ambiguous_with for item in history.events)),
        has_unassigned_thermal_evidence=bool(thermal.unassigned),
        has_source_findings=bool(history.findings or thermal.findings),
    ))


@dataclass(frozen=True, init=False)
class HistoryPreviewSession:
    """Prepared immutable sources and baseline for zero-query repeated drafts.

    Construct only with authorized snapshots. Database callers must use
    prepare_box_history_preview; do not persist/share sessions across requests,
    actors or active institutions. A new request must recheck authority and
    reload evidence. This is not a save or freshness token.
    """

    source: SourceHistory
    thermal_source: ThermalSourceHistory
    anchors: tuple
    before: PreviewState

    def __init__(self, source: SourceHistory, thermal_source: ThermalSourceHistory, *, anchors=()):
        validate_window(thermal_source.start, thermal_source.end)
        _, anchors, _ = validate_draft(anchors=anchors, organization_id=source.organization_id, box_id=source.box_id)
        if (source.organization_id, source.box_id) != (thermal_source.organization_id, thermal_source.box_id):
            raise PreviewInputError("source_scope_mismatch")
        if source.zone_ids != thermal_source.zone_ids:
            raise PreviewInputError("source_zone_scope_mismatch")
        _check_work_budget(source, thermal_source)
        history = _project(source, anchors=anchors)
        _check_attribution_budget(history, thermal_source)
        thermal = project_temperature(history, thermal_source)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "thermal_source", thermal_source)
        object.__setattr__(self, "anchors", anchors)
        object.__setattr__(self, "before", _state(history, thermal))

    def preview(self, *, operations=(), justifications=()) -> HistoryPreview:
        operations, _, justifications = validate_draft(
            operations, self.anchors, justifications,
            organization_id=self.source.organization_id, box_id=self.source.box_id,
        )
        if not operations:
            return _unchanged(self.before, justifications)
        # Phase 1 validates operation references and shapes. Reject the complete
        # draft if any operation is rejected: never expose a partial batch as
        # the caller's proposed history. Conflicting valid batches remain usable.
        _check_event_budget(len(self.source.events) + sum(isinstance(item, InsertEvent) for item in operations))
        history = _project(self.source, operations=operations, anchors=self.anchors)
        if history.rejected_operations:
            raise PreviewInputError("draft_operations_rejected", rejections=history.rejected_operations)
        _check_attribution_budget(history, self.thermal_source)
        thermal = project_temperature(history, self.thermal_source)
        return _compare(self.before, _state(history, thermal), justifications)


# Technical in-memory work limits, not historical publication rules.
MAX_EVENT_COMPARISONS = 1_000_000
MAX_ATTRIBUTION_WORK = 2_000_000
MAX_STORAGE_OVERLAP_REFERENCES = 100_000


def _check_event_budget(count):
    if count * (count - 1) // 2 > MAX_EVENT_COMPARISONS:
        raise PreviewInputError("history_work_limit_exceeded")


def _check_work_budget(source, thermal_source):
    _check_event_budget(len(source.events))
    by_probe = defaultdict(list)
    for item in thermal_source.evidence:
        if item.source_kind in (ThermalSourceKind.RAW_PROBE_MEASUREMENT, ThermalSourceKind.CONDENSED_BUCKET):
            start = utc(item.temporal.earliest)
            end = utc(item.temporal.before) if item.temporal.instant is None else None
            by_probe[item.probe_id].append((start, end))
    references = 0
    for items in by_probe.values():
        active = []
        for start, end in sorted(items, key=lambda item: (item[0], item[1] is None, item[1] or item[0])):
            while active and active[0] <= start:
                heapq.heappop(active)
            references += 2 * len(active)
            if references > MAX_STORAGE_OVERLAP_REFERENCES:
                raise PreviewInputError("thermal_overlap_limit_exceeded")
            if end is not None:
                heapq.heappush(active, end)


def _check_attribution_budget(history, thermal_source):
    if len(history.intervals) * len(thermal_source.evidence) > MAX_ATTRIBUTION_WORK:
        raise PreviewInputError("thermal_attribution_work_limit_exceeded")


def _absolute_temporal(position):
    names = ("instant", "not_before", "before", "ordering_anchor")
    if all(getattr(position, name) is None or getattr(position, name).tzinfo is UTC for name in names):
        return position
    return replace(position, **{name: utc(getattr(position, name)) for name in names
                                if getattr(position, name) is not None})


def _project(source, *, operations=(), anchors=()):
    """Use absolute instants at the engine boundary; retain original evidence.

    Phase 1 uses datetime equality and sorting. A same-zone fold=0/fold=1 pair
    compares equal in Python despite being an hour apart. UTC surrogates avoid
    that ambiguity without changing either reviewed engine or source objects.
    Returned time representations are restored from the original evidence and
    accepted draft operations, including their precision and ordering convention.
    """
    events = tuple(item if (position := _absolute_temporal(item.temporal)) is item.temporal
                   else replace(item, temporal=position) for item in source.events)
    evidence = tuple(item if (position := _absolute_temporal(item.within)) is item.within
                     else replace(item, within=position) for item in source.occupancy_evidence)
    normalized = (source if all(first is second for first, second in zip(events, source.events))
                  and all(first is second for first, second in zip(evidence, source.occupancy_evidence))
                  else replace(source, events=events, occupancy_evidence=evidence))
    normalized_operations = tuple(replace(operation, temporal=_absolute_temporal(operation.temporal))
                                  if isinstance(getattr(operation, "temporal", None), TemporalPosition) else operation
                                  for operation in operations)
    projected = project_history(normalized, operations=normalized_operations, anchors=anchors)
    originals = {item.identity.key: item for item in source.events}
    restored = {}
    for item in (*projected.events, *projected.unplaced_events):
        original = originals.get(item.key) or originals.get(item.identity.derived_key)
        position = original.temporal if original is not None else item.temporal
        if original is not None and item.original.identity != original.identity:
            original = replace(original, identity=item.original.identity)
        for index in item.applied_operations:
            proposed = getattr(operations[index], "temporal", UNSET)
            if proposed is not UNSET:
                position = proposed
        restored[item.key] = replace(item, original=original, temporal=position)
    return replace(projected,
                   events=tuple(restored[item.key] for item in projected.events),
                   unplaced_events=tuple(restored[item.key] for item in projected.unplaced_events),
                   intervals=tuple(replace(item, start=restored[item.started_by].temporal,
                                           end=restored[item.ended_by].temporal if item.ended_by is not None else None)
                                   for item in projected.intervals),
                   sides=tuple(replace(item, temporal=restored[item.event_key].temporal) for item in projected.sides))


def _unchanged(state, justifications):
    conflicts = tuple(ConflictChange(ConflictEvolution.PREEXISTING_INDEPENDENT, item, item)
                      for item in state.history.conflicts)
    return HistoryPreview(state.history.organization_id, state.history.box_id, state, state,
                          (), (), (), (), (), (), conflicts, (), justifications)


def _event_changes(before, after):
    old = {item.key: item for item in (*before.events, *before.unplaced_events)}
    new = {item.key: item for item in (*after.events, *after.unplaced_events)}
    old_positions = {item.key: index for index, item in enumerate(before.events)}
    new_positions = {item.key: index for index, item in enumerate(after.events)}
    changes = []
    names = ("kind", "temporal", "origin_zone_id", "destination_zone_id", "status", "established",
             "ordering_ambiguous_with")
    for key in sorted(new):
        first, second = old.get(key), new[key]
        changed = tuple(name for name in names if first is None or _signature(getattr(first, name)) != _signature(getattr(second, name)))
        if first is not None and ((key in old_positions) != (key in new_positions)):
            changed += ("placement",)
        if changed:
            changes.append(EventChange(key, first, second, changed, old_positions.get(key), new_positions.get(key)))
    return tuple(changes)


def _keyed_changes(before, after, key, build, *, signature=_signature):
    old, new = {key(item): item for item in before}, {key(item): item for item in after}
    return tuple(build(identity, old.get(identity), new.get(identity)) for identity in sorted(old.keys() | new.keys())
                 if signature(old.get(identity)) != signature(new.get(identity)))


def _thermal_signature(segment, history):
    # Both projections share the same immutable evidence snapshot. Compare
    # attribution, not repeated serialization of unchanged source statistics.
    # Stable start-event keys also avoid false changes from shifted indices.
    attributions = tuple(sorted((
        history.intervals[item.occupancy_index].started_by, item.zone_id, item.extent.value,
        utc(item.overlap_start), utc(item.overlap_end) if item.overlap_end is not None else None,
        item.established, item.provisional_reasons, item.uncertainty_reasons,
    ) for item in segment.attributions))
    return (segment.evidence.key, attributions, segment.overlapping_source_keys, segment.uncertainty_reasons)


def _conflict_identity(conflict):
    """Identify known diagnostic causes, never their severity or list position.

    Replay diagnostics identify an ordered pair of events and a specific rule.
    State/zone values are mutable facts of that cause, not its identity. Source
    claims instead identify their stored proof. Observation contradictions from
    Phase 1 omit the observation reference: their residuals cannot be matched
    safely, even if zone and provenance happen to agree. Unknown detail schemas
    likewise support exact equality only, not a guessed evolution.
    """
    code, keys, details = conflict.code, conflict.event_keys, conflict.details
    if len(set(keys)) != len(keys) or any(not isinstance(key, str) or not key for key in keys):
        return None
    shape = set(details)
    replay_shapes = {
        ConflictCode.ORIGIN_DESTINATION_DISCONTINUITY: {"state_before", "zone_before", "claimed_origin"},
        ConflictCode.MOVEMENT_DURING_INACTIVE_PERIOD: {"state_before", "zone_before"},
        ConflictCode.OVERLAPPING_LOCATION_ASSIGNMENT: {"state_before", "zone_before", "assigned_zone"},
    }
    if code in replay_shapes and len(keys) == 2 and shape == replay_shapes[code]:
        return _signature((code, keys))
    if (code == ConflictCode.INCOMPATIBLE_LIFECYCLE_TRANSITION and len(keys) == 2
            and shape == {"state_before", "zone_before", "reason"}
            and details["reason"] in ("already_inactive", "not_inactive")):
        return _signature((code, keys, details["reason"]))
    if (code == ConflictCode.CONTRADICTS_ESTABLISHED_EVIDENCE and len(keys) == 2
            and shape == {"state_before", "zone_before", "observed_zone"}):
        # The second event is the state anchor; unlike occupancy observations,
        # its stable event identity is present in this diagnostic.
        return _signature((code, keys, "state_anchor"))
    if (code == ConflictCode.COMPETING_EVENT_REPRESENTATIONS and len(keys) >= 2
            and shape == {"source", "source_id", "role"}):
        return _signature((code, sorted(keys), details))
    if (code == ConflictCode.INSUFFICIENT_TEMPORAL_PRECISION and shape == {"reason"}
            and ((len(keys) == 1 and details["reason"] == "event_cannot_be_placed")
                 or (len(keys) >= 2 and details["reason"] == "events_cannot_be_ordered"))):
        return _signature((code, sorted(keys), details["reason"]))
    if (code == ConflictCode.CURRENT_OCCUPANCY_UNRECONCILED and len(keys) <= 1
            and shape == {"projected_state", "projected_zone", "current_status", "current_zone"}):
        return _signature((code, keys, details["current_status"], details["current_zone"]))
    return None


def _conflict_changes(before, after, changed_events, changed_intervals):
    def depends_on_draft(conflict):
        if conflict.code == ConflictCode.COMPETING_EVENT_REPRESENTATIONS:
            # Drafts cannot change exclusive source claims.
            return False
        dependencies = changed_events
        if conflict.code == ConflictCode.CONTRADICTS_ESTABLISHED_EVIDENCE and "evidence_zone" in conflict.details:
            # Observation contradictions also depend on interval coverage. A
            # replay contradiction, in contrast, does not depend on the end of
            # the interval it creates or on a later event changing that end.
            dependencies = changed_events | changed_intervals
        return bool(dependencies.intersection(conflict.event_keys))
    old, new = defaultdict(list), defaultdict(list)
    for conflict in before:
        old[_signature(conflict)].append(conflict)
    for conflict in after:
        new[_signature(conflict)].append(conflict)
    changes, previous, proposed = [], defaultdict(list), defaultdict(list)
    # First consume identical diagnostics as multisets, including duplicates.
    # Sorting below determines output order only, never evidence of identity.
    for signature in sorted(old.keys() | new.keys()):
        matched = min(len(old[signature]), len(new[signature]))
        for index in range(matched):
            first, second = old[signature][index], new[signature][index]
            evolution = (ConflictEvolution.PREEXISTING_AFFECTED if depends_on_draft(first)
                         else ConflictEvolution.PREEXISTING_INDEPENDENT)
            changes.append(ConflictChange(evolution, first, second))
        for conflict in old[signature][matched:]:
            identity = _conflict_identity(conflict)
            if identity is None:
                changes.append(ConflictChange(ConflictEvolution.NOT_REPORTED_AFTER, conflict, None))
            else:
                previous[identity].append(conflict)
        for conflict in new[signature][matched:]:
            identity = _conflict_identity(conflict)
            if identity is None:
                changes.append(ConflictChange(ConflictEvolution.INTRODUCED, None, conflict))
            else:
                proposed[identity].append(conflict)
    for identity in sorted(previous.keys() | proposed.keys()):
        old_items, new_items = previous[identity], proposed[identity]
        # Multiple distinct residual versions of one cause do not establish
        # which version evolved into which. Identical repetitions, in contrast,
        # are interchangeable and retain their original multiplicity.
        unambiguous = (len({_signature(item) for item in old_items}) == 1
                       and len({_signature(item) for item in new_items}) == 1)
        matched = min(len(old_items), len(new_items)) if unambiguous else 0
        for index in range(matched):
            first, second = old_items[index], new_items[index]
            evolution = (ConflictEvolution.AGGRAVATED
                         if first.certainty == ConflictCertainty.UNCERTAIN and second.certainty == ConflictCertainty.IMPOSSIBLE
                         else ConflictEvolution.CHANGED)
            changes.append(ConflictChange(evolution, first, second))
        changes.extend(ConflictChange(ConflictEvolution.NOT_REPORTED_AFTER, item, None) for item in old_items[matched:])
        changes.extend(ConflictChange(ConflictEvolution.INTRODUCED, None, item) for item in new_items[matched:])
    return tuple(sorted(changes, key=_signature))


def _compare(before, after, justifications):
    first, second = before.history, after.history
    events = _event_changes(first, second)
    intervals = _keyed_changes(first.intervals, second.intervals, lambda item: item.started_by,
                               lambda key, old, new: IntervalChange(key, old, new))
    sides = _keyed_changes(first.sides, second.sides, lambda item: (item.event_key, item.side.value),
                           lambda key, old, new: SideChange(*key, old, new))
    stays = _keyed_changes(before.thermal.stays, after.thermal.stays, lambda item: item.occupancy.started_by,
                           lambda key, old, new: StayChange(key, old, new),
                           signature=lambda item: _signature(None if item is None else
                                                            {field.name: getattr(item, field.name) for field in fields(item)
                                                             if field.name != "occupancy_index"}))
    old_segments = {item.evidence.key: item for item in (*before.thermal.segments, *before.thermal.unassigned)}
    new_segments = {item.evidence.key: item for item in (*after.thermal.segments, *after.thermal.unassigned)}
    thermal = tuple(ThermalChange(key, old_segments[key], new_segments[key],
                                 bool(old_segments[key].attributions), bool(new_segments[key].attributions))
                    for key in sorted(old_segments) if _thermal_signature(old_segments[key], first)
                    != _thermal_signature(new_segments[key], second))
    def gap_key(item, history):
        owner = history.intervals[item.occupancy_index].started_by if item.occupancy_index is not None else None
        return (owner, item.reason, _signature(item.start), _signature(item.end))

    old_gaps = {gap_key(item, first): item for item in before.thermal.gaps}
    new_gaps = {gap_key(item, second): item for item in after.thermal.gaps}
    def gap_signature(item):
        return _signature(None if item is None else {field.name: getattr(item, field.name) for field in fields(item)
                                                     if field.name != "occupancy_index"})

    gaps = tuple(GapChange(old_gaps.get(key), new_gaps.get(key)) for key in sorted(old_gaps.keys() | new_gaps.keys(), key=repr)
                 if gap_signature(old_gaps.get(key)) != gap_signature(new_gaps.get(key)))
    affected = {item.event_key for item in events} | {item.event_key for item in sides}
    for item in intervals:
        for interval in (item.before, item.after):
            if interval is not None:
                affected.add(interval.started_by)
                if interval.ended_by is not None:
                    affected.add(interval.ended_by)
    conflicts = _conflict_changes(first.conflicts, second.conflicts,
                                  {item.event_key for item in events}, {item.started_by for item in intervals})
    return HistoryPreview(first.organization_id, first.box_id, before, after, events, intervals, sides, thermal,
                          stays, gaps, conflicts, tuple(sorted(affected)), justifications)
