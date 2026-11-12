"""Pure projection of the effective location history of one Box.

Original evidence -> in-memory correction overlay -> one effective history.

This module has no database, Django or clock dependency. It consumes source
events already adapted for one Box of one institution (see
``effective_history_sources``), applies in-memory draft operations, and
returns one deterministic interpretation: ordered events, occupancy intervals,
location entry/exit sides and structured conflicts.

It never writes, never repairs a contradiction and never turns an uncertain
interval into a timestamp. It is not wired into any endpoint.

Undecided on purpose (product decisions, see the stage report):
- no publication policy: conflicts are reported, nothing is accepted or refused;
- no weekly movement limit on the historical contract;
- no removal operation.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, fields, is_dataclass, replace
from datetime import date, datetime, time, timedelta, timezone as datetime_timezone
from enum import Enum
from types import MappingProxyType
from typing import Mapping, Protocol

_FAR_FUTURE = datetime.max.replace(tzinfo=datetime_timezone.utc)


# --------------------------------------------------------------------------
# Temporal contract
# --------------------------------------------------------------------------

class TemporalPrecision(str, Enum):
    # A recorded physical instant.
    EXACT = "exact"
    # A stored period boundary: usable as a position, not a certified instant.
    RECORDED_BOUNDARY = "recorded_boundary"
    # One ISO week of the laboratory calendar; no day or time is known.
    ISO_WEEK = "iso_week"
    # Bounded by two observations; the occurrence lies somewhere inside.
    OBSERVATION_INTERVAL = "observation_interval"
    # No occurrence time; optional one-sided or two-sided known bounds.
    UNKNOWN = "unknown"


def _require_aware(value, name):
    if value is not None and (not isinstance(value, datetime) or value.utcoffset() is None):
        raise ValueError(f"{name} must be a timezone-aware datetime.")


@dataclass(frozen=True)
class TemporalPosition:
    """When an event happened, with its real precision.

    ``not_before`` is inclusive and ``before`` exclusive. ``ordering_anchor``
    is only a deterministic position convention inside the sequence; it is
    never an occurrence date and must never be displayed as one.
    """

    precision: TemporalPrecision
    instant: datetime | None = None
    iso_year: int | None = None
    iso_week: int | None = None
    not_before: datetime | None = None
    before: datetime | None = None
    ordering_anchor: datetime | None = None

    def __post_init__(self):
        for name in ("instant", "not_before", "before", "ordering_anchor"):
            _require_aware(getattr(self, name), name)
        pointlike = self.precision in (TemporalPrecision.EXACT, TemporalPrecision.RECORDED_BOUNDARY)
        if pointlike != (self.instant is not None):
            raise ValueError("Only exact and recorded-boundary positions carry an instant.")
        if pointlike and (self.not_before is not None or self.before is not None):
            raise ValueError("An instant has no interval bounds.")
        if (self.precision == TemporalPrecision.ISO_WEEK) != (self.iso_year is not None or self.iso_week is not None):
            raise ValueError("Only an ISO week position carries a year and a week.")
        if self.precision in (TemporalPrecision.ISO_WEEK, TemporalPrecision.OBSERVATION_INTERVAL):
            if self.not_before is None or self.before is None:
                raise ValueError("Week and observation-interval positions need both bounds.")
        if self.not_before is not None and self.before is not None and self.not_before >= self.before:
            raise ValueError("The lower bound must be before the upper bound.")

    @classmethod
    def exact(cls, instant):
        return cls(TemporalPrecision.EXACT, instant=instant)

    @classmethod
    def recorded_boundary(cls, instant):
        return cls(TemporalPrecision.RECORDED_BOUNDARY, instant=instant)

    @classmethod
    def week(cls, iso_year, iso_week, tz):
        """ISO week of the calendar ``tz``; raises ``ValueError`` for an invalid week."""
        monday = datetime.combine(date.fromisocalendar(iso_year, iso_week, 1), time.min, tzinfo=tz)
        return cls(TemporalPrecision.ISO_WEEK, iso_year=iso_year, iso_week=iso_week,
                   not_before=monday, before=_next_local_week(monday))

    @classmethod
    def observation_interval(cls, not_before, before, *, ordering_anchor=None):
        return cls(TemporalPrecision.OBSERVATION_INTERVAL, not_before=not_before, before=before,
                   ordering_anchor=ordering_anchor)

    @classmethod
    def unknown(cls, *, not_before=None, before=None):
        return cls(TemporalPrecision.UNKNOWN, not_before=not_before, before=before)

    @property
    def earliest(self):
        return self.instant if self.instant is not None else self.not_before

    @property
    def latest(self):
        return self.instant if self.instant is not None else self.before

    @property
    def latest_inclusive(self):
        return self.instant is not None

    @property
    def position(self):
        """Sequence position, or ``None`` when the event cannot be placed."""
        return self.ordering_anchor if self.ordering_anchor is not None else self.earliest


def _next_local_week(monday):
    # Wall-clock arithmetic keeps a local Monday midnight across DST changes.
    return datetime.combine(monday.date() + timedelta(days=7), time.min, tzinfo=monday.tzinfo)


def definitely_before(first, second):
    """True only when ``first`` is proven to precede ``second``."""
    upper, lower = first.latest, second.earliest
    if upper is None or lower is None:
        return False
    return upper < lower or (upper == lower and not first.latest_inclusive)


# --------------------------------------------------------------------------
# Event contract
# --------------------------------------------------------------------------

class EventKind(str, Enum):
    MOVEMENT = "movement"
    DEACTIVATION = "deactivation"
    REACTIVATION = "reactivation"
    LOCATION_ASSIGNMENT = "location_assignment"
    RECONSTRUCTED_MOVEMENT = "reconstructed_movement"
    INITIAL_HISTORICAL_LOCATION = "initial_historical_location"
    # Evidence-only kinds: recorded facts whose business nature is not
    # established. They are never inferred into another kind.
    UNCLASSIFIED_MOVEMENT = "unclassified_movement"
    PERIOD_BOUNDARY = "period_boundary"


# Kinds an administrator draft may assert. Reconstructions and the initial
# historical location are established by evidence, not by a draft.
DRAFT_KINDS = frozenset({
    EventKind.MOVEMENT, EventKind.DEACTIVATION, EventKind.REACTIVATION, EventKind.LOCATION_ASSIGNMENT,
})
_TRANSFER_KINDS = frozenset({
    EventKind.MOVEMENT, EventKind.RECONSTRUCTED_MOVEMENT, EventKind.UNCLASSIFIED_MOVEMENT, EventKind.PERIOD_BOUNDARY,
})


class EventOrigin(str, Enum):
    BOX_MOVEMENT = "box_movement"
    LIFECYCLE_AUDIT = "lifecycle_audit"
    HISTORICAL_BOX_MOVEMENT = "historical_box_movement"
    HISTORICAL_TRANSITION = "historical_transition"
    STARTING_LOCATION = "starting_location"
    BOX_LOCATION = "box_location"
    DRAFT = "draft"


class IdentityStability(str, Enum):
    # Typed primary key of a persisted row.
    PERSISTED = "persisted"
    # Recomputed from evidence on every read; may change when evidence changes.
    DERIVED = "derived"
    # A derived event matched to a durable anchor supplied by the caller.
    ANCHORED = "anchored"
    # In-memory draft insertion.
    DRAFT = "draft"


@dataclass(frozen=True)
class EventIdentity:
    """Identity of an event, kept separate from the evidence that derives it.

    For a derived event, ``key`` is only valid inside one projection:
    ``evidence_fingerprint`` states which evidence produced it and ``locator``
    names that evidence by stable references.
    """

    key: str
    stability: IdentityStability
    evidence_fingerprint: str | None = None
    locator: tuple = ()
    derived_key: str | None = None

    def __post_init__(self):
        if self.stability in (IdentityStability.DERIVED, IdentityStability.ANCHORED) and not self.evidence_fingerprint:
            raise ValueError("A derived identity must state its evidence fingerprint.")


@dataclass(frozen=True)
class SourceRef:
    """One stored proof of an event.

    An exclusive reference can prove only one business event: two events
    holding the same exclusive reference are competing representations.
    """

    source: str
    source_id: int | str
    role: str
    basis: str = "recorded"
    exclusive: bool = True


def _frozen_mapping(value):
    return MappingProxyType(dict(value or {}))


@dataclass(frozen=True)
class HistoryEvent:
    """One business event as established by its sources; never mutated."""

    identity: EventIdentity
    kind: EventKind
    origin: EventOrigin
    temporal: TemporalPosition
    origin_zone_id: int | None = None
    destination_zone_id: int | None = None
    sources: tuple = ()
    provenance: str = ""
    # False when the business nature or the attribution is not established.
    established: bool = True
    details: Mapping = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "details", _frozen_mapping(self.details))
        object.__setattr__(self, "sources", tuple(self.sources))


@dataclass(frozen=True)
class CurrentOccupancy:
    """Operational state, read only: it is compared, never corrected."""

    status: str
    thermal_zone_id: int | None


@dataclass(frozen=True)
class OccupancyEvidence:
    """Established proof that the Box was in a zone at some moment of ``within``."""

    zone_id: int
    within: TemporalPosition
    sources: tuple = ()
    provenance: str = ""


@dataclass(frozen=True)
class SourceFinding:
    """Evidence the adapter could not turn into an event; kept, never hidden."""

    code: str
    details: Mapping = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "details", _frozen_mapping(self.details))


@dataclass(frozen=True)
class SourceHistory:
    """Everything known about one Box, bounded to its institution."""

    organization_id: int
    box_id: int
    zone_ids: frozenset
    events: tuple = ()
    current: CurrentOccupancy | None = None
    occupancy_evidence: tuple = ()
    findings: tuple = ()

    def __post_init__(self):
        object.__setattr__(self, "zone_ids", frozenset(self.zone_ids))
        for name in ("events", "occupancy_evidence", "findings"):
            object.__setattr__(self, name, tuple(getattr(self, name)))


# --------------------------------------------------------------------------
# Durable anchoring contract for corrected reconstructed events
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class ReconstructionAnchor:
    """Durable identity a later stage may persist for a reconstructed event.

    The anchor owns ``anchor_id``. The evidence it was created against is kept
    beside it and is never its identity. Nothing is persisted here: callers
    pass anchors in memory.
    """

    anchor_id: str
    organization_id: int
    box_id: int
    evidence_fingerprint: str
    locator: tuple = ()


class AnchorStatus(str, Enum):
    MATCHED = "matched"
    # Same evidence locator, different evidence: needs an explicit review.
    EVIDENCE_CHANGED = "evidence_changed"
    # No derived event corresponds any more.
    ORPHANED = "orphaned"
    # The fingerprint exists, but not at the explicitly designated evidence.
    LOCATOR_MISMATCH = "locator_mismatch"
    # Anchor of another Box or institution: ignored.
    OUT_OF_SCOPE = "out_of_scope"
    # Several anchors or several events claim the same match.
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class AnchorResolution:
    anchor: ReconstructionAnchor
    status: AnchorStatus
    event_key: str | None = None


def anchor_key(anchor_id):
    return f"anchor:{anchor_id}"


# --------------------------------------------------------------------------
# Draft operations
# --------------------------------------------------------------------------

class _Unset(Enum):
    UNSET = "unset"


UNSET = _Unset.UNSET


@dataclass(frozen=True)
class EventTarget:
    """Designates one event; a derived event must name the evidence it expects."""

    key: str
    expected_evidence_fingerprint: str | None = None


@dataclass(frozen=True)
class ModifyEvent:
    target: EventTarget
    kind: EventKind | _Unset = UNSET
    temporal: TemporalPosition | _Unset = UNSET
    origin_zone_id: int | None | _Unset = UNSET
    destination_zone_id: int | None | _Unset = UNSET


@dataclass(frozen=True)
class AdjustEventKind:
    target: EventTarget
    kind: EventKind


@dataclass(frozen=True)
class AdjustEventTemporal:
    target: EventTarget
    temporal: TemporalPosition


@dataclass(frozen=True)
class AdjustEventZones:
    target: EventTarget
    origin_zone_id: int | None | _Unset = UNSET
    destination_zone_id: int | None | _Unset = UNSET


@dataclass(frozen=True)
class InsertEvent:
    draft_id: str
    kind: EventKind
    temporal: TemporalPosition
    origin_zone_id: int | None = None
    destination_zone_id: int | None = None


def draft_key(draft_id):
    return f"draft:{draft_id}"


class RejectionCode(str, Enum):
    UNKNOWN_OPERATION = "unknown_operation"
    TARGET_NOT_FOUND = "target_not_found"
    DERIVED_TARGET_REQUIRES_EVIDENCE = "derived_target_requires_evidence_fingerprint"
    TARGET_EVIDENCE_CHANGED = "target_evidence_changed"
    NO_CHANGE = "no_change"
    DUPLICATE_DRAFT_ID = "duplicate_draft_id"
    KIND_NOT_ALLOWED = "kind_not_allowed"
    ZONE_OUTSIDE_INSTITUTION = "zone_outside_institution"
    INVALID_SHAPE = "invalid_shape"
    INVALID_TEMPORAL = "invalid_temporal"


@dataclass(frozen=True)
class RejectedOperation:
    """A draft operation that could not be simulated; it changed nothing."""

    index: int
    code: RejectionCode
    details: Mapping = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "details", _frozen_mapping(self.details))


# --------------------------------------------------------------------------
# Projection output
# --------------------------------------------------------------------------

class EffectiveStatus(str, Enum):
    ORIGINAL = "original"
    MODIFIED = "modified"
    INSERTED = "inserted"


@dataclass(frozen=True)
class EffectiveEvent:
    """An event after the overlay. ``original`` keeps the untouched source.

    ``established`` describes the effective assertion, not administrator trust.
    Draft assertions are provisional; source evidence remains in ``original``.
    """

    identity: EventIdentity
    kind: EventKind
    origin: EventOrigin
    temporal: TemporalPosition
    origin_zone_id: int | None
    destination_zone_id: int | None
    status: EffectiveStatus
    established: bool
    original: HistoryEvent | None = None
    applied_operations: tuple = ()
    ordering_ambiguous_with: tuple = ()

    @property
    def key(self):
        return self.identity.key

    @property
    def sources(self):
        return self.original.sources if self.original is not None else ()

    @property
    def provenance(self):
        return self.original.provenance if self.original is not None else "draft"


class OccupancyState(str, Enum):
    LOCATED = "located"
    INACTIVE = "inactive"
    # No recorded location; the activity status is not asserted.
    UNASSIGNED = "unassigned"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class OccupancyInterval:
    """A period of one state. A missing ``end`` means no later event ends it."""

    state: OccupancyState
    zone_id: int | None
    start: TemporalPosition
    started_by: str
    end: TemporalPosition | None = None
    ended_by: str | None = None
    # True when a boundary is not an exact, unambiguously ordered instant.
    boundary_uncertain: bool = False
    # Cumulative unresolved premises, separate from temporal precision.
    provisional_reasons: tuple = ()

    @property
    def provisional(self):
        return bool(self.provisional_reasons)


class SideType(str, Enum):
    ENTRY = "entry"
    EXIT = "exit"


@dataclass(frozen=True)
class LocationSide:
    """One side of an event for one zone. Both sides share ``event_key``.

    ``zone_basis`` is ``event`` when the event names the zone, and
    ``projected_state`` when a deactivation without a recorded origin leaves
    the zone the projection had reached.
    """

    zone_id: int
    side: SideType
    event_key: str
    kind: EventKind
    temporal: TemporalPosition
    related_zone_id: int | None = None
    zone_basis: str = "event"
    provisional_reasons: tuple = ()

    @property
    def provisional(self):
        return bool(self.provisional_reasons)


class ConflictCode(str, Enum):
    ORIGIN_DESTINATION_DISCONTINUITY = "origin_destination_discontinuity"
    MOVEMENT_DURING_INACTIVE_PERIOD = "movement_during_inactive_period"
    INCOMPATIBLE_LIFECYCLE_TRANSITION = "incompatible_lifecycle_transition"
    OVERLAPPING_LOCATION_ASSIGNMENT = "overlapping_location_assignment"
    COMPETING_EVENT_REPRESENTATIONS = "competing_event_representations"
    INSUFFICIENT_TEMPORAL_PRECISION = "insufficient_temporal_precision"
    CONTRADICTS_ESTABLISHED_EVIDENCE = "contradicts_established_evidence"
    CURRENT_OCCUPANCY_UNRECONCILED = "current_occupancy_unreconciled"


class ConflictCertainty(str, Enum):
    # The described history cannot have happened.
    IMPOSSIBLE = "impossible"
    # The ordering or the source is uncertain; it may or may not be wrong.
    UNCERTAIN = "uncertain"


@dataclass(frozen=True)
class Conflict:
    code: ConflictCode
    certainty: ConflictCertainty
    event_keys: tuple = ()
    uncertainty_reasons: tuple = ()
    details: Mapping = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "details", _frozen_mapping(self.details))


@dataclass(frozen=True)
class HistoryProjection:
    organization_id: int
    box_id: int
    events: tuple
    unplaced_events: tuple
    intervals: tuple
    sides: tuple
    conflicts: tuple
    rejected_operations: tuple
    anchor_resolutions: tuple
    findings: tuple


# --------------------------------------------------------------------------
# Thermal boundary (extension point only)
# --------------------------------------------------------------------------

class ThermalSourceKind(str, Enum):
    """Sources a later temperature preview must keep distinct, never merged."""

    HISTORICAL_SETPOINT = "historical_setpoint"
    RAW_PROBE_MEASUREMENT = "raw_probe_measurement"
    CONDENSED_BUCKET = "condensed_bucket"
    MIXED_DAILY_AGGREGATE = "mixed_daily_aggregate"


class TemperaturePreviewStage(Protocol):
    """Later stage fed by ``HistoryProjection.intervals``.

    Consumers must not treat provisional intervals or sides as validated
    occupancy. Their reasons accumulate without an automatic recovery policy.
    Temporal boundary uncertainty remains a separate constraint.

    Not implemented here: no probe attribution, no aggregation and no
    synthetic value. A missing temperature stays missing; zero stays zero.
    """

    def preview(self, projection: HistoryProjection): ...


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def evidence_fingerprint(value):
    """Stable hash of a JSON-like evidence description."""
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def to_payload(value):
    """Plain JSON-ready structure; ``0`` and ``None`` are kept as they are."""
    if is_dataclass(value) and not isinstance(value, type):
        return {item.name: to_payload(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): to_payload(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, (frozenset, set)):
        return sorted(to_payload(item) for item in value)
    if isinstance(value, (list, tuple)):
        return [to_payload(item) for item in value]
    return value


def _shape_issue(kind, origin_zone_id, destination_zone_id):
    """Zone shape each draft kind requires, from the existing lifecycle rules."""
    if kind in (EventKind.MOVEMENT, EventKind.RECONSTRUCTED_MOVEMENT):
        if origin_zone_id is None or destination_zone_id is None:
            return "movement_requires_origin_and_destination"
        if origin_zone_id == destination_zone_id:
            return "movement_requires_distinct_zones"
    elif kind == EventKind.DEACTIVATION:
        if destination_zone_id is not None:
            return "deactivation_has_no_destination"
    elif kind in (EventKind.REACTIVATION, EventKind.LOCATION_ASSIGNMENT, EventKind.INITIAL_HISTORICAL_LOCATION):
        if destination_zone_id is None:
            return "destination_required"
        if origin_zone_id is not None:
            return "origin_not_applicable"
    return None


# --------------------------------------------------------------------------
# Projection
# --------------------------------------------------------------------------

def project_history(source, *, operations=(), anchors=()):
    """Return the effective history of ``source`` under the draft overlay.

    Pure and deterministic: same inputs, same output, independent of the
    order of ``source.events``. Nothing in ``source`` is modified.
    """
    keys = [event.identity.key for event in source.events]
    if len(set(keys)) != len(keys):
        raise ValueError("Source events must have unique identity keys.")

    events, anchor_resolutions = _resolve_anchors(source, anchors)
    effective = {
        event.identity.key: EffectiveEvent(
            identity=event.identity, kind=event.kind, origin=event.origin, temporal=event.temporal,
            origin_zone_id=event.origin_zone_id, destination_zone_id=event.destination_zone_id,
            status=EffectiveStatus.ORIGINAL, established=event.established, original=event,
        )
        for event in events
    }
    rejected = []
    for index, operation in enumerate(operations):
        rejection = _apply_operation(effective, source, index, operation)
        if rejection is not None:
            rejected.append(rejection)

    placed = [event for event in effective.values() if event.temporal.position is not None]
    unplaced = sorted((event for event in effective.values() if event.temporal.position is None),
                      key=lambda event: event.key)
    placed.sort(key=lambda event: (
        event.temporal.position, event.temporal.latest or _FAR_FUTURE,
        0 if event.temporal.latest_inclusive else 1, event.key,
    ))
    placed, clusters = _mark_ambiguity(placed)

    conflicts = []
    for cluster in clusters:
        conflicts.append(Conflict(
            ConflictCode.INSUFFICIENT_TEMPORAL_PRECISION, ConflictCertainty.UNCERTAIN,
            event_keys=tuple(cluster), uncertainty_reasons=("ambiguous_ordering",),
            details={"reason": "events_cannot_be_ordered"},
        ))
    for event in unplaced:
        conflicts.append(Conflict(
            ConflictCode.INSUFFICIENT_TEMPORAL_PRECISION, ConflictCertainty.UNCERTAIN,
            event_keys=(event.key,), uncertainty_reasons=("unplaced_events",),
            details={"reason": "event_cannot_be_placed"},
        ))
    conflicts.extend(_competing_representations([*placed, *unplaced]))

    walk = _Walk(has_unplaced=bool(unplaced))
    for event in placed:
        walk.apply(event)
    walk.finish()
    conflicts.extend(walk.conflicts)
    premise_intervals, _ = _mark_provisional(placed, unplaced, walk.intervals, walk.sides, conflicts)
    conflicts.extend(_evidence_conflicts(source.occupancy_evidence, premise_intervals, has_unplaced=bool(unplaced)))
    current = _current_conflict(source.current, walk)
    if current is not None:
        conflicts.append(current)

    intervals, sides = _mark_provisional(placed, unplaced, walk.intervals, walk.sides, conflicts)
    return HistoryProjection(
        organization_id=source.organization_id, box_id=source.box_id,
        events=tuple(placed), unplaced_events=tuple(unplaced),
        intervals=tuple(intervals), sides=tuple(sides), conflicts=tuple(conflicts),
        rejected_operations=tuple(rejected), anchor_resolutions=tuple(anchor_resolutions),
        findings=source.findings,
    )


def _mark_provisional(events, unplaced, intervals, sides, conflicts):
    """Annotate simulation dependencies without discarding any diagnostics.

    A conflicting end also makes the preceding interval's exit provisional.
    No later event silently validates an unresolved earlier premise. Unplaced
    events have no safe cutoff, so they affect the entire simulation.
    """
    positions = {event.key: index for index, event in enumerate(events)}
    reasons = [set() for event in events]
    for index, event in enumerate(events):
        if not event.established:
            reasons[index].add("unestablished_provenance")
        if event.status != EffectiveStatus.ORIGINAL:
            reasons[index].add("draft_assertion")
    for conflict in conflicts:
        affected = [positions[key] for key in conflict.event_keys if key in positions]
        if affected:
            # Both competing claims (and ambiguous orderings) are unresolved;
            # a chain contradiction begins at the event that exposes it.
            start = (min(affected) if conflict.code in (
                ConflictCode.COMPETING_EVENT_REPRESENTATIONS, ConflictCode.INSUFFICIENT_TEMPORAL_PRECISION,
            ) else max(affected))
            reasons[start].add(f"conflict:{conflict.code.value}")
    cumulative, pending = {}, {"unplaced_events"} if unplaced else set()
    for index, event in enumerate(events):
        pending.update(reasons[index])
        cumulative[event.key] = tuple(sorted(pending))
    return (
        [replace(interval, provisional_reasons=cumulative[interval.ended_by or events[-1].key])
         for interval in intervals],
        [replace(side, provisional_reasons=cumulative[side.event_key]) for side in sides],
    )


def _resolve_anchors(source, anchors):
    derived = [event for event in source.events if event.identity.stability == IdentityStability.DERIVED]
    resolutions, matches = [], {}
    for anchor in sorted(anchors, key=lambda item: item.anchor_id):
        if (anchor.organization_id, anchor.box_id) != (source.organization_id, source.box_id):
            resolutions.append(AnchorResolution(anchor, AnchorStatus.OUT_OF_SCOPE))
            continue
        same = [event for event in derived if event.identity.evidence_fingerprint == anchor.evidence_fingerprint]
        located = [event for event in derived if anchor.locator and event.identity.locator == anchor.locator]
        candidates = located if anchor.locator else same
        if len(candidates) > 1:
            resolutions.append(AnchorResolution(anchor, AnchorStatus.AMBIGUOUS))
        elif candidates:
            candidate = candidates[0]
            if candidate.identity.evidence_fingerprint != anchor.evidence_fingerprint:
                resolutions.append(AnchorResolution(anchor, AnchorStatus.EVIDENCE_CHANGED, candidate.identity.key))
            else:
                matches.setdefault(candidate.identity.key, []).append(anchor)
                resolutions.append(AnchorResolution(anchor, AnchorStatus.MATCHED, candidate.identity.key))
        elif anchor.locator and same:
            resolutions.append(AnchorResolution(anchor, AnchorStatus.LOCATOR_MISMATCH))
        else:
            resolutions.append(AnchorResolution(anchor, AnchorStatus.ORPHANED))
    contested = {key for key, claimed in matches.items() if len(claimed) > 1}
    resolutions = [
        replace(item, status=AnchorStatus.AMBIGUOUS, event_key=None)
        if item.status == AnchorStatus.MATCHED and item.event_key in contested else item
        for item in resolutions
    ]
    events = []
    for event in source.events:
        claimed = matches.get(event.identity.key)
        if claimed and event.identity.key not in contested:
            identity = replace(event.identity, key=anchor_key(claimed[0].anchor_id),
                               stability=IdentityStability.ANCHORED, derived_key=event.identity.key)
            event = replace(event, identity=identity)
        events.append(event)
    resolutions = [
        replace(item, event_key=anchor_key(item.anchor.anchor_id)) if item.status == AnchorStatus.MATCHED else item
        for item in resolutions
    ]
    keys = [event.identity.key for event in events]
    if len(set(keys)) != len(keys):
        raise ValueError("An anchor identity collides with another event key.")
    return events, resolutions


def _apply_operation(effective, source, index, operation):
    def reject(code, **details):
        return RejectedOperation(index, code, details)

    if isinstance(operation, InsertEvent):
        key = draft_key(operation.draft_id)
        if key in effective:
            return reject(RejectionCode.DUPLICATE_DRAFT_ID, draft_id=operation.draft_id)
        issue = _draft_values_issue(source, operation.kind, operation.temporal,
                                    operation.origin_zone_id, operation.destination_zone_id, check_shape=True)
        if issue is not None:
            return reject(issue[0], **issue[1])
        effective[key] = EffectiveEvent(
            identity=EventIdentity(key, IdentityStability.DRAFT), kind=operation.kind, origin=EventOrigin.DRAFT,
            temporal=operation.temporal, origin_zone_id=operation.origin_zone_id,
            destination_zone_id=operation.destination_zone_id, status=EffectiveStatus.INSERTED,
            established=False, applied_operations=(index,),
        )
        return None

    if isinstance(operation, AdjustEventKind):
        operation = ModifyEvent(operation.target, kind=operation.kind)
    elif isinstance(operation, AdjustEventTemporal):
        operation = ModifyEvent(operation.target, temporal=operation.temporal)
    elif isinstance(operation, AdjustEventZones):
        operation = ModifyEvent(operation.target, origin_zone_id=operation.origin_zone_id,
                                destination_zone_id=operation.destination_zone_id)
    if not isinstance(operation, ModifyEvent):
        return reject(RejectionCode.UNKNOWN_OPERATION, type=type(operation).__name__)

    target = operation.target
    event = effective.get(target.key)
    if event is None:
        return reject(RejectionCode.TARGET_NOT_FOUND, key=target.key)
    if event.identity.stability == IdentityStability.DERIVED:
        # A derived key alone is not a durable designation of the event.
        if target.expected_evidence_fingerprint is None:
            return reject(RejectionCode.DERIVED_TARGET_REQUIRES_EVIDENCE, key=target.key)
    if (target.expected_evidence_fingerprint is not None
            and target.expected_evidence_fingerprint != event.identity.evidence_fingerprint):
        return reject(RejectionCode.TARGET_EVIDENCE_CHANGED, key=target.key)

    kind = event.kind if operation.kind is UNSET else operation.kind
    temporal = event.temporal if operation.temporal is UNSET else operation.temporal
    origin_zone_id = event.origin_zone_id if operation.origin_zone_id is UNSET else operation.origin_zone_id
    destination_zone_id = (event.destination_zone_id if operation.destination_zone_id is UNSET
                           else operation.destination_zone_id)
    kind_changed = kind != event.kind
    zones_changed = (origin_zone_id, destination_zone_id) != (event.origin_zone_id, event.destination_zone_id)
    if not kind_changed and not zones_changed and temporal == event.temporal:
        return reject(RejectionCode.NO_CHANGE, key=target.key)
    issue = _draft_values_issue(
        source, kind, temporal,
        origin_zone_id if operation.origin_zone_id is not UNSET else None,
        destination_zone_id if operation.destination_zone_id is not UNSET else None,
        check_shape=False, check_kind=kind_changed,
    )
    if issue is None and (kind_changed or zones_changed):
        shape = _shape_issue(kind, origin_zone_id, destination_zone_id)
        if shape is not None:
            issue = (RejectionCode.INVALID_SHAPE, {"reason": shape})
    if issue is not None:
        return reject(issue[0], key=target.key, **issue[1])
    effective[target.key] = replace(
        event, kind=kind, temporal=temporal, origin_zone_id=origin_zone_id,
        destination_zone_id=destination_zone_id,
        status=EffectiveStatus.INSERTED if event.status == EffectiveStatus.INSERTED else EffectiveStatus.MODIFIED,
        established=False,
        applied_operations=(*event.applied_operations, index),
    )
    return None


def _draft_values_issue(source, kind, temporal, origin_zone_id, destination_zone_id, *, check_shape, check_kind=True):
    if not isinstance(temporal, TemporalPosition):
        return RejectionCode.INVALID_TEMPORAL, {}
    if check_kind and kind not in DRAFT_KINDS:
        return RejectionCode.KIND_NOT_ALLOWED, {"kind": getattr(kind, "value", str(kind))}
    for zone_id in (origin_zone_id, destination_zone_id):
        if zone_id is not None and (type(zone_id) is not int or zone_id not in source.zone_ids):
            return RejectionCode.ZONE_OUTSIDE_INSTITUTION, {"zone_id": zone_id}
    if check_shape:
        shape = _shape_issue(kind, origin_zone_id, destination_zone_id)
        if shape is not None:
            return RejectionCode.INVALID_SHAPE, {"reason": shape}
    return None


def _mark_ambiguity(placed):
    """Cluster physical events only; a state observation is not a movement.

    Contradictory anchors are still checked during replay, with their actual
    temporal precision, rather than asserting an independent physical order.
    """
    neighbours = {event.key: set() for event in placed}
    for index, first in enumerate(placed):
        for second in placed[index + 1:]:
            if EventKind.INITIAL_HISTORICAL_LOCATION in (first.kind, second.kind):
                continue
            if not definitely_before(first.temporal, second.temporal) and not definitely_before(second.temporal, first.temporal):
                neighbours[first.key].add(second.key)
                neighbours[second.key].add(first.key)
    order = {event.key: index for index, event in enumerate(placed)}
    marked = [replace(event, ordering_ambiguous_with=tuple(sorted(neighbours[event.key], key=order.get)))
              for event in placed]
    clusters, seen = [], set()
    for event in placed:
        if event.key in seen or not neighbours[event.key]:
            continue
        cluster, stack = set(), [event.key]
        while stack:
            key = stack.pop()
            if key not in cluster:
                cluster.add(key)
                stack.extend(neighbours[key] - cluster)
        seen |= cluster
        clusters.append(sorted(cluster, key=order.get))
    return marked, clusters


def _competing_representations(events):
    claims = {}
    for event in events:
        for ref in event.sources:
            if ref.exclusive:
                claims.setdefault((ref.source, str(ref.source_id), ref.role), []).append(event.key)
    conflicts = []
    for (source, source_id, role), keys in sorted(claims.items()):
        if len(set(keys)) > 1:
            conflicts.append(Conflict(
                ConflictCode.COMPETING_EVENT_REPRESENTATIONS, ConflictCertainty.UNCERTAIN,
                event_keys=tuple(sorted(set(keys))), uncertainty_reasons=("competing_sources",),
                details={"source": source, "source_id": source_id, "role": role},
            ))
    return conflicts


class _Walk:
    """Replay the ordered events; report every contradiction, repair none."""

    def __init__(self, *, has_unplaced):
        self.has_unplaced = has_unplaced
        self.state, self.zone_id, self.setter = OccupancyState.UNKNOWN, None, None
        self.intervals, self.sides, self.conflicts = [], [], []
        self._open = None

    def _conflict(self, code, event, **details):
        reasons = []
        if self.has_unplaced:
            reasons.append("unplaced_events")
        if event.ordering_ambiguous_with or (self.setter is not None and self.setter.ordering_ambiguous_with):
            reasons.append("ambiguous_ordering")
        if (self.setter is not None
                and EventKind.INITIAL_HISTORICAL_LOCATION in (event.kind, self.setter.kind)
                and not definitely_before(self.setter.temporal, event.temporal)
                and not definitely_before(event.temporal, self.setter.temporal)):
            reasons.append("overlapping_state_evidence")
        if not event.established or (self.setter is not None and not self.setter.established):
            reasons.append("unestablished_provenance")
        keys = (self.setter.key, event.key) if self.setter is not None else (event.key,)
        self.conflicts.append(Conflict(
            code, ConflictCertainty.UNCERTAIN if reasons else ConflictCertainty.IMPOSSIBLE,
            event_keys=keys, uncertainty_reasons=tuple(reasons),
            details={"state_before": self.state.value, "zone_before": self.zone_id, **details},
        ))

    def _uncertain_boundary(self, event):
        return bool(event.ordering_ambiguous_with) or event.temporal.precision != TemporalPrecision.EXACT

    def _set(self, state, zone_id, event):
        if self._open is not None:
            self.intervals.append(replace(
                self._open, end=event.temporal, ended_by=event.key,
                boundary_uncertain=self._open.boundary_uncertain or self._uncertain_boundary(event),
            ))
        self._open = OccupancyInterval(state, zone_id, event.temporal, event.key,
                                       boundary_uncertain=self._uncertain_boundary(event))
        self.state, self.zone_id, self.setter = state, zone_id, event

    def _side(self, zone_id, side, event, related_zone_id=None, zone_basis="event"):
        self.sides.append(LocationSide(zone_id, side, event.key, event.kind, event.temporal,
                                       related_zone_id=related_zone_id, zone_basis=zone_basis))

    def apply(self, event):
        kind, origin, destination = event.kind, event.origin_zone_id, event.destination_zone_id
        located = self.state == OccupancyState.LOCATED
        transfer = kind in _TRANSFER_KINDS

        if kind == EventKind.INITIAL_HISTORICAL_LOCATION:
            # An observed anchor: it confirms or contradicts, it is not an entry.
            if self.state == OccupancyState.UNKNOWN:
                self._set(OccupancyState.LOCATED, destination, event)
            elif not (located and self.zone_id == destination):
                self._conflict(ConflictCode.CONTRADICTS_ESTABLISHED_EVIDENCE, event, observed_zone=destination)
            return

        if kind == EventKind.DEACTIVATION:
            if self.state == OccupancyState.INACTIVE:
                self._conflict(ConflictCode.INCOMPATIBLE_LIFECYCLE_TRANSITION, event, reason="already_inactive")
            elif origin is not None and self.state != OccupancyState.UNKNOWN and not (located and self.zone_id == origin):
                self._conflict(ConflictCode.ORIGIN_DESTINATION_DISCONTINUITY, event, claimed_origin=origin)
            if origin is not None:
                self._side(origin, SideType.EXIT, event)
            elif located:
                self._side(self.zone_id, SideType.EXIT, event, zone_basis="projected_state")
            self._set(OccupancyState.INACTIVE, None, event)
            return

        if kind == EventKind.REACTIVATION:
            if self.state in (OccupancyState.LOCATED, OccupancyState.UNASSIGNED):
                self._conflict(ConflictCode.INCOMPATIBLE_LIFECYCLE_TRANSITION, event, reason="not_inactive")
            if destination is not None:
                self._side(destination, SideType.ENTRY, event)
                self._set(OccupancyState.LOCATED, destination, event)
            else:
                self._set(OccupancyState.UNASSIGNED, None, event)
            return

        # Movements, assignments and bare period boundaries.
        if origin is not None:
            self._side(origin, SideType.EXIT, event, related_zone_id=destination if transfer else None)
        if destination is None:
            # Exit only: it ends the matching occupancy and nothing else.
            if self.state == OccupancyState.UNKNOWN or (located and self.zone_id == origin):
                self._set(OccupancyState.UNASSIGNED, None, event)
            else:
                self._conflict(ConflictCode.ORIGIN_DESTINATION_DISCONTINUITY, event, claimed_origin=origin)
            return
        if self.state == OccupancyState.INACTIVE:
            self._conflict(ConflictCode.MOVEMENT_DURING_INACTIVE_PERIOD, event)
        elif origin is not None:
            if self.state != OccupancyState.UNKNOWN and not (located and self.zone_id == origin):
                self._conflict(ConflictCode.ORIGIN_DESTINATION_DISCONTINUITY, event, claimed_origin=origin)
        elif located and kind != EventKind.UNCLASSIFIED_MOVEMENT:
            # An unclassified row without origin states no origin at all.
            self._conflict(ConflictCode.OVERLAPPING_LOCATION_ASSIGNMENT, event, assigned_zone=destination)
        self._side(destination, SideType.ENTRY, event, related_zone_id=origin if transfer else None)
        self._set(OccupancyState.LOCATED, destination, event)

    def finish(self):
        if self._open is not None:
            self.intervals.append(self._open)
            self._open = None


def _possible_window(interval):
    return interval.start.earliest, (interval.end.latest if interval.end is not None else None)


def _definite_window(interval):
    return interval.start.latest, (interval.end.earliest if interval.end is not None else None)


def _overlaps(window, lower, upper):
    start, end = window
    return (start is None or start < upper) and (end is None or end > lower)


def _evidence_conflicts(evidences, intervals, *, has_unplaced):
    conflicts = []
    for evidence in evidences:
        lower, upper = evidence.within.earliest, evidence.within.latest
        if lower is None or upper is None:
            continue
        if upper == lower:
            upper = lower + timedelta(microseconds=1)
        touching = [interval for interval in intervals if _overlaps(_possible_window(interval), lower, upper)]
        if not touching or any(interval.state == OccupancyState.LOCATED and interval.zone_id == evidence.zone_id
                               for interval in touching):
            continue
        covering = [interval for interval in touching
                    if (start := _definite_window(interval)[0]) is not None and start <= lower
                    and ((end := _definite_window(interval)[1]) is None or end >= upper)]
        reasons = set()
        if not covering or any(interval.boundary_uncertain for interval in covering):
            reasons.add("uncertain_interval_boundaries")
        if has_unplaced:
            reasons.add("unplaced_events")
        for interval in covering or touching:
            reasons.update(interval.provisional_reasons)
        conflicts.append(Conflict(
            ConflictCode.CONTRADICTS_ESTABLISHED_EVIDENCE,
            ConflictCertainty.UNCERTAIN if reasons else ConflictCertainty.IMPOSSIBLE,
            event_keys=tuple(dict.fromkeys(interval.started_by for interval in touching)),
            uncertainty_reasons=tuple(sorted(reasons)),
            details={"evidence_zone": evidence.zone_id, "provenance": evidence.provenance,
                     "projected": [[interval.state.value, interval.zone_id] for interval in touching]},
        ))
    return conflicts


def _current_conflict(current, walk):
    if current is None:
        return None
    state, zone_id, setter = walk.state, walk.zone_id, walk.setter
    if state == OccupancyState.UNKNOWN:
        matches = current.thermal_zone_id is None
    elif state == OccupancyState.LOCATED:
        matches = current.thermal_zone_id == zone_id and current.status != "inactive"
    elif state == OccupancyState.INACTIVE:
        matches = current.thermal_zone_id is None and current.status == "inactive"
    else:
        matches = current.thermal_zone_id is None
    if matches:
        return None
    reasons = []
    if setter is None:
        reasons.append("no_history")
    else:
        if walk.has_unplaced:
            reasons.append("unplaced_events")
        if setter.ordering_ambiguous_with:
            reasons.append("ambiguous_ordering")
        if not setter.established:
            reasons.append("unestablished_provenance")
    if current.status not in ("active", "inactive"):
        reasons.append("status_not_qualified")
    return Conflict(
        ConflictCode.CURRENT_OCCUPANCY_UNRECONCILED,
        ConflictCertainty.UNCERTAIN if reasons else ConflictCertainty.IMPOSSIBLE,
        event_keys=(setter.key,) if setter is not None else (), uncertainty_reasons=tuple(reasons),
        details={"projected_state": state.value, "projected_zone": zone_id,
                 "current_status": current.status, "current_zone": current.thermal_zone_id},
    )
