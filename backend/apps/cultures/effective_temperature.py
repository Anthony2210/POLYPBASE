"""Pure thermal evidence attribution over a proposed effective Box history.

No source selection hierarchy, interpolation, inter-probe averaging or exposure
integration is defined here. A segment is ONE original evidence item, not a
continuous curve. Its candidate attributions never duplicate its statistics.
All request and support ranges are half-open and compared in UTC. An ordering
anchor from Phase 1 is never used as a physical boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum

from .effective_history import (
    HistoryProjection, OccupancyInterval, OccupancyState, SourceFinding, SourceRef,
    TemporalPosition, TemporalPrecision, ThermalSourceKind,
)


def utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("Thermal timestamps must be timezone-aware.")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class ThermalEvidence:
    """An immutable observation or WHOLE statistic, never a clipped statistic.

    For exact readings temporal.instant is the actual measured timestamp. For
    buckets/days temporal bounds are statistical support, not sample instants;
    for historical setpoints they retain the source's observation precision.
    box_id is mandatory for Box-specific historical evidence. Probe identity is
    its database id, not the mutable code or external channel. Daily False does
    not distinguish manual, historical and mixed contributions.
    """

    key: str
    organization_id: int
    zone_id: int | None
    source_kind: ThermalSourceKind
    temporal: TemporalPosition
    temperature_c: Decimal | None = None
    minimum_c: Decimal | None = None
    maximum_c: Decimal | None = None
    temperature_sum_c: Decimal | None = None
    measurement_count: int | None = None
    probe_id: int | None = None
    box_id: int | None = None
    last_measured_at: datetime | None = None
    last_temperature_c: Decimal | None = None
    daily_computed_from_probes: bool | None = None
    association_basis: str = ""
    sources: tuple[SourceRef, ...] = ()
    established: bool = True
    uncertainty_reasons: tuple[str, ...] = ()

    def __post_init__(self):
        if self.temporal.instant is not None:
            utc(self.temporal.instant)
        elif self.temporal.not_before is None or self.temporal.before is None:
            raise ValueError("Thermal interval evidence needs finite support bounds.")
        for name in ("temperature_c", "minimum_c", "maximum_c", "temperature_sum_c", "last_temperature_c"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, Decimal) or not value.is_finite()):
                raise ValueError("Thermal values must be finite Decimals or None.")
        if self.last_measured_at is not None:
            utc(self.last_measured_at)
        object.__setattr__(self, "sources", tuple(self.sources))
        object.__setattr__(self, "uncertainty_reasons", tuple(self.uncertainty_reasons))


@dataclass(frozen=True)
class ThermalSourceHistory:
    """Authorized, finite evidence snapshot, reusable for successive drafts.

    The read adapter is the authorization boundary for zone/probe membership.
    Pure callers must provide the same trusted scope, not client assertions.
    """

    organization_id: int
    box_id: int
    start: datetime
    end: datetime
    zone_ids: frozenset[int]
    probe_ids: frozenset[int] = frozenset()
    evidence: tuple[ThermalEvidence, ...] = ()
    findings: tuple[SourceFinding, ...] = ()

    def __post_init__(self):
        if utc(self.start) >= utc(self.end):
            raise ValueError("The thermal preview window must be nonempty.")
        object.__setattr__(self, "zone_ids", frozenset(self.zone_ids))
        object.__setattr__(self, "probe_ids", frozenset(self.probe_ids))
        object.__setattr__(self, "evidence", tuple(self.evidence))
        object.__setattr__(self, "findings", tuple(self.findings))


class AttributionExtent(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    POSSIBLE = "possible"


@dataclass(frozen=True)
class ThermalAttribution:
    occupancy_index: int
    zone_id: int
    extent: AttributionExtent
    # Display intersection only: never use this to prorate evidence statistics.
    overlap_start: datetime
    overlap_end: datetime | None
    established: bool
    provisional_reasons: tuple[str, ...] = ()
    uncertainty_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class ThermalSegment:
    evidence: ThermalEvidence
    attributions: tuple[ThermalAttribution, ...]
    # Same-probe raw/bucket or bucket/bucket overlap cannot be decomposed.
    # These records MUST NOT be counted as independent observations.
    overlapping_source_keys: tuple[str, ...] = ()
    uncertainty_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class ThermalStay:
    occupancy_index: int
    occupancy: OccupancyInterval
    possible_start: datetime
    possible_end: datetime
    definite_start: datetime | None
    definite_end: datetime | None
    source_keys: tuple[str, ...]
    missing_reasons: tuple[str, ...]


@dataclass(frozen=True)
class ThermalGap:
    """Missing support, with separate occupancy certainty and draft premises.

    An uncertain inactive gap may overlap possible location evidence. It must
    not be rendered as an established absence of assignment.
    """

    start: datetime
    end: datetime
    reason: str
    occupancy_index: int | None = None
    provisional_reasons: tuple[str, ...] = ()
    established: bool = False
    uncertainty_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class ThermalPreview:
    history: HistoryProjection
    start: datetime
    end: datetime
    stays: tuple[ThermalStay, ...]
    segments: tuple[ThermalSegment, ...]
    unassigned: tuple[ThermalSegment, ...]
    gaps: tuple[ThermalGap, ...]
    findings: tuple[SourceFinding, ...]
    # No continuity or additive combination between sources is implied.
    continuity: str = "not_inferred"


@dataclass(frozen=True)
class HistoricalTemperaturePreview:
    """Structural implementation of Phase 1's TemperaturePreviewStage protocol."""

    source: ThermalSourceHistory

    def preview(self, projection: HistoryProjection) -> ThermalPreview:
        return project_temperature(projection, self.source)


def _support(evidence):
    temporal = evidence.temporal
    if temporal.instant is not None:
        return utc(temporal.instant), None
    return utc(temporal.not_before), utc(temporal.before)


def _intersects(start, end, lower, upper):
    return lower <= start < upper if end is None else start < upper and end > lower


def _occupancy_window(interval, start, end):
    possible_start = max(start, utc(interval.start.earliest) if interval.start.earliest is not None else start)
    upper = interval.end.latest if interval.end is not None else None
    possible_end = min(end, utc(upper) if upper is not None else end)
    lower = interval.start.latest
    definite_start = max(start, utc(lower)) if lower is not None else None
    if interval.end is None:
        definite_end = end
    else:
        upper = interval.end.earliest
        definite_end = min(end, utc(upper)) if upper is not None else None
    if definite_start is None or definite_end is None or definite_start >= definite_end:
        definite_start = definite_end = None
    return possible_start, possible_end, definite_start, definite_end


def _validate_scope(history, source):
    if (history.organization_id, history.box_id) != (source.organization_id, source.box_id):
        raise ValueError("History and thermal evidence must have the same authorized scope.")
    keys = set()
    for item in source.evidence:
        if item.key in keys:
            raise ValueError("Thermal evidence keys must be unique.")
        keys.add(item.key)
        if item.organization_id != source.organization_id or (
            item.zone_id is not None and item.zone_id not in source.zone_ids
        ):
            raise ValueError("Thermal evidence is outside the authorized institution.")
        if item.box_id is not None and item.box_id != source.box_id:
            raise ValueError("Thermal evidence belongs to another Box.")
        if item.probe_id is not None and item.probe_id not in source.probe_ids:
            raise ValueError("Thermal evidence belongs to an unauthorized probe.")
        if item.source_kind == ThermalSourceKind.HISTORICAL_SETPOINT and item.box_id != source.box_id:
            raise ValueError("Historical evidence must identify its exact Box.")
        if item.source_kind in (ThermalSourceKind.RAW_PROBE_MEASUREMENT, ThermalSourceKind.CONDENSED_BUCKET):
            if item.probe_id is None or item.zone_id is None:
                raise ValueError("Probe evidence must identify its authorized probe and zone.")
        if item.source_kind == ThermalSourceKind.RAW_PROBE_MEASUREMENT and item.temporal.instant is None:
            raise ValueError("Individual probe readings require an actual timestamp.")
        if item.source_kind in (ThermalSourceKind.CONDENSED_BUCKET, ThermalSourceKind.MIXED_DAILY_AGGREGATE):
            if item.temporal.instant is not None:
                raise ValueError("Statistics require their whole support interval.")
    for interval in history.intervals:
        if interval.zone_id is not None and interval.zone_id not in source.zone_ids:
            raise ValueError("Projected occupancy is outside the authorized institution.")
    for event in (*history.events, *history.unplaced_events):
        if any(zone is not None and zone not in source.zone_ids
               for zone in (event.origin_zone_id, event.destination_zone_id)):
            raise ValueError("Projected event is outside the authorized institution.")


def _storage_overlaps(evidence):
    """Detect ambiguous tier overlaps, not deduplicate unknown constituents.

    A new retained reading may legitimately lie in an older bucket's support.
    Without constituent timestamps it cannot be subtracted or certified as an
    independent contribution. Preserve both records, prohibit additive use.
    """
    by_probe = {}
    for item in evidence:
        if item.source_kind in (ThermalSourceKind.RAW_PROBE_MEASUREMENT, ThermalSourceKind.CONDENSED_BUCKET):
            by_probe.setdefault(item.probe_id, []).append(item)
    overlaps = {item.key: set() for item in evidence}
    for items in by_probe.values():
        # Buckets must enter the active set before a raw reading at their
        # inclusive start; their exclusive end still expires before that row.
        items.sort(key=lambda item: (_support(item)[0], _support(item)[1] is None,
                                     _support(item)[1] or _support(item)[0], item.key))
        active = []
        for item in items:
            start, end = _support(item)
            active = [(other, upper) for other, upper in active if upper > start]
            for other, upper in active:
                overlaps[item.key].add(other.key)
                overlaps[other.key].add(item.key)
            if end is not None:
                active.append((item, end))
    return overlaps


def _support_sort(item):
    start, end = _support(item)
    return start, end or start


def _boundary_reasons(interval):
    if any(boundary is not None and boundary.precision == TemporalPrecision.RECORDED_BOUNDARY
           for boundary in (interval.start, interval.end)):
        return {"recorded_boundary_not_physical_instant"}
    return set()


def _attribute(item, index, interval, window, overlaps, request):
    lower, upper, definite_lower, definite_upper = window
    start, end = _support(item)
    if not _intersects(start, end, lower, upper):
        return None
    reasons = set(item.uncertainty_reasons) | _boundary_reasons(interval)
    complete = (definite_lower is not None and definite_upper is not None
                and definite_lower <= start < definite_upper
                and (end is None or end <= definite_upper))
    possible_complete = lower <= start and (end is None or end <= upper)
    if not complete:
        reasons.add("uncertain_occupancy_boundary")
    if not possible_complete:
        reasons.add("partial_statistical_support")
    if start < request[0] or (end is not None and end > request[1]):
        reasons.add("partial_request_support")
    if not item.established:
        reasons.add("unestablished_thermal_evidence")
    if overlaps:
        reasons.add("storage_overlap_unresolved")
    if item.temperature_c is None:
        reasons.add("temperature_value_unknown")
    extent = (AttributionExtent.PARTIAL if not possible_complete
              else AttributionExtent.COMPLETE if complete else AttributionExtent.POSSIBLE)
    return ThermalAttribution(
        occupancy_index=index, zone_id=interval.zone_id, extent=extent,
        overlap_start=max(start, lower), overlap_end=min(end, upper) if end is not None else None,
        established=not reasons and not interval.provisional_reasons,
        provisional_reasons=interval.provisional_reasons, uncertainty_reasons=tuple(sorted(reasons)),
    )


def _gap(index, interval, window, reason):
    lower, upper, definite_lower, definite_upper = window
    certain = (definite_lower is not None and definite_upper is not None
               and definite_lower <= lower and definite_upper >= upper)
    reasons = _boundary_reasons(interval)
    if not certain:
        reasons.add("uncertain_occupancy_boundary")
    return ThermalGap(lower, upper, reason, index, interval.provisional_reasons,
                      established=not reasons and not interval.provisional_reasons,
                      uncertainty_reasons=tuple(sorted(reasons)))


def project_temperature(history: HistoryProjection, source: ThermalSourceHistory) -> ThermalPreview:
    """Recompute attribution from effective intervals, never persisted locations.

    COMPLETE describes temporal containment only. Established attribution also
    requires nonprovisional occupancy and source evidence. Neither field means
    publication approval, physical installation proof or continuous exposure.
    Daily and bucket statistics are never clipped, split, summed or reweighted.
    """
    _validate_scope(history, source)
    start, end = utc(source.start), utc(source.end)
    evidence = sorted((item for item in source.evidence if _intersects(*_support(item), start, end)),
                      key=lambda item: (*_support_sort(item), item.key))
    windows = [(index, interval, _occupancy_window(interval, start, end))
               for index, interval in enumerate(history.intervals)]
    windows = [(index, interval, window) for index, interval, window in windows if window[0] < window[1]]
    overlaps = _storage_overlaps(evidence)
    segments, unassigned = [], []
    source_keys = {index: [] for index, interval, window in windows}
    source_kinds = {index: set() for index, interval, window in windows}
    for item in evidence:
        attributions = []
        for index, interval, window in windows:
            if interval.state != OccupancyState.LOCATED or interval.zone_id != item.zone_id:
                continue
            attribution = _attribute(item, index, interval, window, overlaps[item.key], (start, end))
            if attribution is not None:
                attributions.append(attribution)
                source_keys[index].append(item.key)
                source_kinds[index].add(item.source_kind)
        reasons = set(item.uncertainty_reasons)
        if overlaps[item.key]:
            reasons.add("storage_overlap_unresolved")
        if not attributions:
            reasons.add("zone_unresolved" if item.zone_id is None else "no_matching_effective_occupancy")
        segment = ThermalSegment(item, tuple(attributions), tuple(sorted(overlaps[item.key])), tuple(sorted(reasons)))
        (segments if attributions else unassigned).append(segment)
    stays, gaps = [], []
    for index, interval, window in windows:
        missing = set()
        if interval.state != OccupancyState.LOCATED or interval.zone_id is None:
            reason = "inactive" if interval.state == OccupancyState.INACTIVE else "no_assigned_location"
            missing.add(reason)
            gaps.append(_gap(index, interval, window, reason))
        else:
            kinds = source_kinds[index]
            if not kinds:
                missing.add("temperature_data_unavailable")
                gaps.append(_gap(index, interval, window, "temperature_data_unavailable"))
            if not kinds.intersection((ThermalSourceKind.RAW_PROBE_MEASUREMENT, ThermalSourceKind.CONDENSED_BUCKET)):
                missing.add("probe_data_unavailable")
            if ThermalSourceKind.HISTORICAL_SETPOINT not in kinds:
                missing.add("documented_historical_setpoint_unavailable")
            # Evidence availability is not continuous coverage. Even adjacent
            # statistics are separate supports, not a reconstructed curve.
            missing.add("continuous_exposure_not_established")
        stays.append(ThermalStay(index, interval, *window, tuple(source_keys[index]), tuple(sorted(missing))))
    # Uncovered history (including a completely empty projection) is unknown,
    # not an inferred location from the Box's current pointer.
    cursor = start
    for lower, upper in sorted((window[0], window[1]) for index, interval, window in windows):
        if lower > cursor:
            gaps.append(ThermalGap(cursor, lower, "occupancy_history_unavailable"))
        cursor = max(cursor, upper)
    if cursor < end:
        gaps.append(ThermalGap(cursor, end, "occupancy_history_unavailable"))
    return ThermalPreview(
        history, start, end, tuple(stays), tuple(segments), tuple(unassigned),
        tuple(sorted(gaps, key=lambda gap: (gap.start, gap.end, gap.reason, gap.occupancy_index or 0))), source.findings,
    )
