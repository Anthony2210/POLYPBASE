"""Read-only adaptation of the stored history of one Box into source events.

Every query is bounded to the Box and its institution. Nothing is written:
movements, periods, audits, observations, receipts and the current location
stay untouched. Callers remain responsible for permissions; this module grants
none and is not wired into any endpoint.

One business event is emitted once, with every stored proof attached to it:
a movement, its receipt, its lifecycle audit and the period boundaries it
wrote are one event, not several. Proofs that cannot be reconciled are kept
as separate events or findings so the projection reports them.

Merged identities are out of scope: only the canonical Box is read.
"""

from datetime import date, datetime, time, timedelta

from apps.audit.box_lookup import box_audits, numeric_box_audit_values
from apps.audit.models import AuditLog

from .box_history import _aware_audit_datetime, _service_recorded_movement_ids
from .effective_history import (
    CurrentOccupancy, EventIdentity, EventKind, EventOrigin, HistoryEvent, IdentityStability, OccupancyEvidence,
    SourceFinding, SourceHistory, SourceRef, TemporalPosition,
)
from .historical_location_transitions import (
    ALREADY_REPRESENTED, ESTABLISHED, POLICY as TRANSITION_POLICY, PROVENANCE as TRANSITION_PROVENANCE,
    project_historical_transitions, project_starting_location,
)
from .historical_movements import PROVENANCE as RECONSTRUCTION_PROVENANCE, _plan, fingerprint
from .location_history_diagnostics import _read_only_snapshot
from .models import MOVEMENT_WEEK_TIME_ZONE, Box, BoxMovement, ThermalZone

_MOVEMENT_KINDS = {
    BoxMovement.Kind.ORDINARY: EventKind.MOVEMENT,
    BoxMovement.Kind.INITIAL_LOCATION: EventKind.LOCATION_ASSIGNMENT,
    BoxMovement.Kind.QUALIFICATION: EventKind.LOCATION_ASSIGNMENT,
    BoxMovement.Kind.REACTIVATION: EventKind.REACTIVATION,
}
# Lifecycle transitions that open a location through a movement of this kind.
_OPENING_TRANSITIONS = {
    "inactive->active": BoxMovement.Kind.REACTIVATION,
    "pending_review->active": BoxMovement.Kind.QUALIFICATION,
}
STARTING_LOCATION_KEY = "starting-location"


class HistorySourceNotFound(LookupError):
    """No canonical Box with this identifier exists in this institution."""


def load_box_source_history(*, organization_id, box_id):
    """Return the ``SourceHistory`` of one canonical Box of one institution."""
    with _read_only_snapshot():
        return _load(organization_id=organization_id, box_id=box_id)


def _local_midnight(day):
    return datetime.combine(day, time.min, tzinfo=MOVEMENT_WEEK_TIME_ZONE)


class _Builder:
    def __init__(self):
        self.events, self.findings, self.claimed = {}, [], set()

    def add(self, key, **values):
        values["sources"] = list(values.get("sources", ()))
        self.events[key] = values

    def attach(self, key, ref):
        self.events[key]["sources"].append(ref)

    def claim(self, key, period, side, basis):
        self.attach(key, SourceRef("box_location", period.pk, f"period_{side}", basis=basis))
        self.claimed.add((period.pk, side))

    def finding(self, code, **details):
        self.findings.append(SourceFinding(code, details))

    def build(self):
        return tuple(
            HistoryEvent(identity=values.pop("identity", None) or EventIdentity(key, IdentityStability.PERSISTED),
                         sources=tuple(values.pop("sources")), **values)
            for key, values in sorted(self.events.items())
        )


def _load(*, organization_id, box_id):
    box = Box.objects.filter(pk=box_id, organization_id=organization_id, merged_into__isnull=True).first()
    if box is None:
        raise HistorySourceNotFound("No such Box in this institution.")
    zones = {zone.pk: {"id": zone.pk, "name": zone.name}
             for zone in ThermalZone.objects.filter(organization_id=organization_id)}
    all_periods = list(box.locations.order_by("starts_at", "pk"))
    periods = [period for period in all_periods if period.thermal_zone_id in zones]
    by_period = {period.pk: period for period in periods}
    movements = list(box.movements.order_by("moved_at", "pk"))
    by_movement = {movement.pk: movement for movement in movements}
    codes = [code for code in (box.global_code, box.previous_global_code) if code]
    audits = list(box_audits(organization_id=organization_id, box_ids=[box.pk], codes=codes)
                  .order_by("created_at", "pk"))
    recorded_ids = _service_recorded_movement_ids(box=box, audits=audits, by_movement=by_movement,
                                                  zones=zones, codes=codes)
    builder = _Builder()

    for period in all_periods:
        if period.pk not in by_period:
            builder.finding("period_zone_outside_institution", location_id=period.pk)

    def zone(zone_id, *, required=False, **context):
        if zone_id is None:
            if required:
                builder.finding("zone_missing", **context)
            return None
        if zone_id in zones:
            return zone_id
        builder.finding("zone_outside_institution", **context)
        return None

    _add_movements(builder, box, movements, periods, by_period, audits, recorded_ids, codes, zone)
    _add_lifecycle(builder, box, audits, by_movement, by_period, recorded_ids)
    _add_reconstructions(builder, box, audits, by_period, organization_id, zone)
    evidences = _add_transitions(builder, box, by_period, organization_id)
    _add_starting_location(builder, box, all_periods, zones)
    _add_period_boundaries(builder, periods)

    current_zone = zone(box.thermal_zone_id, box_id=box.pk, side="current")
    return SourceHistory(
        organization_id=organization_id, box_id=box.pk, zone_ids=frozenset(zones),
        events=builder.build(), current=CurrentOccupancy(box.status, current_zone),
        occupancy_evidence=tuple(evidences), findings=tuple(builder.findings),
    )


def _boundary_period(movement, linked_id, zone_id, side, periods, by_period):
    """Return (period, basis) for the boundary this movement wrote, if provable.

    A recorded link is used when it agrees with the period. Without a link,
    exactly one same-zone period at the exact movement instant corresponds;
    no link is ever created or repaired.
    """
    def agrees(period):
        if period.thermal_zone_id != zone_id:
            return False
        if side == "start":
            return period.starts_at == movement.moved_at
        return period.ends_at == movement.moved_at and not period.end_date_unknown

    if linked_id is not None:
        period = by_period.get(linked_id)
        return (period, "recorded_link") if period is not None and agrees(period) else (None, "inconsistent_link")
    if zone_id is None:
        return None, None
    matches = [period for period in periods if agrees(period)]
    return (matches[0], "exact_boundary_match") if len(matches) == 1 else (None, None)


def _add_movements(builder, box, movements, periods, by_period, audits, recorded_ids, codes, zone):
    for movement in movements:
        key = f"movement:{movement.pk}"
        kind = _MOVEMENT_KINDS.get(movement.kind)
        origin_zone = zone(movement.from_thermal_zone_id, required=kind == EventKind.MOVEMENT,
                           movement_id=movement.pk, side="origin")
        destination_zone = zone(movement.to_thermal_zone_id, required=kind is not None,
                                movement_id=movement.pk, side="destination")
        zones_verified = (origin_zone == movement.from_thermal_zone_id
                          and destination_zone == movement.to_thermal_zone_id
                          and destination_zone is not None
                          and (kind != EventKind.MOVEMENT or origin_zone is not None))
        builder.add(
            key, kind=kind or EventKind.UNCLASSIFIED_MOVEMENT, origin=EventOrigin.BOX_MOVEMENT,
            temporal=TemporalPosition.exact(movement.moved_at),
            origin_zone_id=origin_zone, destination_zone_id=destination_zone,
            sources=[SourceRef("box_movement", movement.pk, "event")], provenance="box_movement",
            # An unclassified nature is kept as it is; it is never inferred.
            established=kind is not None and zones_verified,
            details={"native_kind": movement.kind, "revision": movement.revision,
                     "recorded_via_polypbase": movement.pk in recorded_ids},
        )
        for side, linked_id, zone_id in (("end", movement.origin_location_id, movement.from_thermal_zone_id),
                                         ("start", movement.destination_location_id, movement.to_thermal_zone_id)):
            period, basis = _boundary_period(movement, linked_id, zone_id, side, periods, by_period)
            if period is not None:
                builder.claim(key, period, side, basis)
            elif basis == "inconsistent_link":
                builder.finding("movement_period_link_inconsistent", movement_id=movement.pk, side=side)
    for audit in audits:
        metadata = audit.metadata
        if not isinstance(metadata, dict) or "transition" in metadata:
            continue
        movement_id = metadata.get("movement_id")
        if type(movement_id) is not int or f"movement:{movement_id}" not in builder.events:
            continue
        if "box_id" in metadata:
            if type(metadata["box_id"]) is not int or metadata["box_id"] != box.pk:
                continue
        elif audit.object_id not in codes:
            continue
        role = "correction" if metadata.get("event") == "box_movement_correction" else "receipt"
        builder.attach(f"movement:{movement_id}", SourceRef("audit_log", audit.pk, role, exclusive=False))


def _add_lifecycle(builder, box, audits, by_movement, by_period, recorded_ids):
    lifecycle = [audit for audit in audits if isinstance(audit.metadata, dict)
                 and type(audit.metadata.get("box_id")) is int and audit.metadata["box_id"] == box.pk
                 and isinstance(audit.metadata.get("transition"), str)]
    movement_claims, closure_claims = {}, {}
    for audit in lifecycle:
        movement_id = audit.metadata.get("movement_id")
        if type(movement_id) is int:
            movement_claims[movement_id] = movement_claims.get(movement_id, 0) + 1
        closed_ids = audit.metadata.get("closed_location_ids")
        if isinstance(closed_ids, list):
            for location_id in closed_ids:
                if type(location_id) is int:
                    closure_claims[location_id] = closure_claims.get(location_id, 0) + 1

    for audit in lifecycle:
        metadata, transition = audit.metadata, audit.metadata["transition"]
        key = f"lifecycle:{audit.pk}"
        if transition in _OPENING_TRANSITIONS:
            movement_id = metadata.get("movement_id")
            movement = by_movement.get(movement_id) if type(movement_id) is int else None
            paired = bool(
                movement and movement.kind == _OPENING_TRANSITIONS[transition]
                and movement.user_id == audit.user_id and audit.action == AuditLog.Action.UPDATE
                and movement_claims[movement.pk] == 1 and movement.pk in recorded_ids
            )
            if paired:
                # Same business event as the movement it recorded.
                builder.attach(f"movement:{movement.pk}", SourceRef("audit_log", audit.pk, "lifecycle"))
            elif transition == "inactive->active" or "movement_id" in metadata:
                sources = [SourceRef("audit_log", audit.pk, "lifecycle")]
                if movement is not None:
                    # The claimed movement stays its own event: both are reported.
                    sources.append(SourceRef("box_movement", movement.pk, "event"))
                builder.finding("lifecycle_movement_unverified", audit_id=audit.pk)
                builder.add(key, kind=(EventKind.REACTIVATION if transition == "inactive->active"
                                       else EventKind.LOCATION_ASSIGNMENT), origin=EventOrigin.LIFECYCLE_AUDIT,
                            temporal=TemporalPosition.unknown(), sources=sources, provenance="lifecycle_audit",
                            established=False,
                            details={"transition": transition, "recorded_at": audit.created_at.isoformat()})
            # A qualification to active without a paired movement assigns no location.
            continue
        if transition not in ("active->inactive", "pending_review->inactive"):
            continue
        closed_ids = metadata.get("closed_location_ids")
        valid_ids = isinstance(closed_ids, list) and all(type(value) is int for value in closed_ids)
        closed_ids = closed_ids if valid_ids else []
        unique = (valid_ids and len(set(closed_ids)) == len(closed_ids)
                  and all(closure_claims.get(value) == 1 for value in closed_ids))
        if transition == "active->inactive":
            changed_at = _aware_audit_datetime(metadata.get("changed_at")) if audit.action == AuditLog.Action.UPDATE else None
            closed = [by_period[value] for value in closed_ids if value in by_period and changed_at is not None
                      and by_period[value].ends_at == changed_at and not by_period[value].end_date_unknown]
            temporal = TemporalPosition.exact(changed_at) if changed_at is not None else TemporalPosition.unknown()
        else:
            # Qualified as inactive from history: the end of the stay is unknown.
            closed = [by_period[value] for value in closed_ids if value in by_period and by_period[value].end_date_unknown]
            temporal = TemporalPosition.unknown(
                not_before=max((period.starts_at for period in closed), default=None)
                if unique and len(closed) == len(closed_ids) else None)
        locations_verified = unique and len(closed) == len(closed_ids)
        if not locations_verified:
            builder.finding("lifecycle_locations_unverified", audit_id=audit.pk)
            closed = []
        builder.add(key, kind=EventKind.DEACTIVATION, origin=EventOrigin.LIFECYCLE_AUDIT, temporal=temporal,
                    origin_zone_id=closed[0].thermal_zone_id if len(closed) == 1 else None,
                    sources=[SourceRef("audit_log", audit.pk, "lifecycle")], provenance="lifecycle_audit",
                    established=locations_verified,
                    details={"transition": transition, "recorded_at": audit.created_at.isoformat()})
        for period in closed:
            builder.claim(key, period, "end", "lifecycle_audit")


def _add_reconstructions(builder, box, audits, by_period, organization_id, zone):
    receipts = list(box.historical_movements.order_by("iso_year", "iso_week", "pk"))
    if not receipts:
        return
    plan = _plan(organization_id=organization_id, box_ids=[box.pk], audit_rows=numeric_box_audit_values(audits))
    candidates = {candidate["id"]: candidate for candidate in plan["candidates"]}
    for receipt in receipts:
        origin_zone = zone(receipt.from_thermal_zone_id, required=True,
                           receipt_id=receipt.pk, side="origin")
        destination_zone = zone(receipt.to_thermal_zone_id, required=True,
                                receipt_id=receipt.pk, side="destination")
        candidate = candidates.get(receipt.candidate_key)
        established = bool(origin_zone is not None and destination_zone is not None and candidate and candidate["reconciliation_status"] == "accepted"
                           and not candidate["blockers"] and candidate["evidence"]["basis"] == RECONSTRUCTION_PROVENANCE)
        temporal = TemporalPosition.unknown()
        if established:
            try:
                temporal = TemporalPosition.week(receipt.iso_year, receipt.iso_week, MOVEMENT_WEEK_TIME_ZONE)
            except ValueError:
                established = False
        key = f"historical:{receipt.pk}"
        builder.add(
            key, kind=EventKind.RECONSTRUCTED_MOVEMENT, origin=EventOrigin.HISTORICAL_BOX_MOVEMENT, temporal=temporal,
            origin_zone_id=origin_zone, destination_zone_id=destination_zone,
            sources=[SourceRef("historical_box_movement", receipt.pk, "event")],
            provenance=RECONSTRUCTION_PROVENANCE if established else "unverified_week_reconstruction",
            established=established,
            details={"stored_iso_year": receipt.iso_year, "stored_iso_week": receipt.iso_week},
        )
        # The stored boundary stays raw evidence of this same transition.
        for period_id, side in ((receipt.origin_location_id, "end"), (receipt.destination_location_id, "start")):
            if period_id in by_period:
                builder.claim(key, by_period[period_id], side, "reconstruction_receipt")


def _add_transitions(builder, box, by_period, organization_id):
    """Institution-specific observed changes; empty wherever the rule does not apply."""
    evidences = {}
    for transition in project_historical_transitions(organization_id=organization_id, box_ids=[box.pk]):
        if transition["status"] not in (ESTABLISHED, ALREADY_REPRESENTED):
            builder.finding("historical_transition_unresolved", transition_id=transition["id"],
                            status=transition["status"], blockers=transition["blockers"])
            continue
        origin, destination = transition["origin_observation"], transition["destination_observation"]
        origin_zone, destination_zone = transition["from_thermal_zone"]["id"], transition["to_thermal_zone"]["id"]
        for observation, zone_id in ((origin, origin_zone), (destination, destination_zone)):
            week = (zone_id, observation["iso_year"], observation["iso_week"])
            evidences.setdefault(week, OccupancyEvidence(
                zone_id=zone_id,
                within=TemporalPosition.week(observation["iso_year"], observation["iso_week"], MOVEMENT_WEEK_TIME_ZONE),
                sources=(SourceRef("historical_temperature_reading", observation["reading_id"], "observation",
                                   exclusive=False),),
                provenance=TRANSITION_PROVENANCE,
            ))
        representation = transition["representation"] or {}
        corroboration = SourceRef("historical_transition", transition["id"], "corroboration", exclusive=False)
        if representation.get("kind") in ("box_movement", "historical_box_movement"):
            if representation["event_id"] in builder.events:
                builder.attach(representation["event_id"], corroboration)
            continue
        descriptor = {
            "policy": transition["policy"], "box_id": box.pk, "from_zone_id": origin_zone, "to_zone_id": destination_zone,
            "from_environment_c": transition["from_environment_c"], "to_environment_c": transition["to_environment_c"],
            "origin_observation": origin, "destination_observation": destination, "representation": transition["representation"],
        }
        builder.add(
            transition["id"],
            # The key is a hash of today's evidence: valid for this read only.
            identity=EventIdentity(
                transition["id"], IdentityStability.DERIVED, evidence_fingerprint=fingerprint(descriptor),
                locator=(("policy", TRANSITION_POLICY), ("origin_reading_id", origin["reading_id"]),
                         ("destination_reading_id", destination["reading_id"])),
            ),
            kind=EventKind.RECONSTRUCTED_MOVEMENT, origin=EventOrigin.HISTORICAL_TRANSITION,
            temporal=TemporalPosition.observation_interval(
                _local_midnight(date.fromisoformat(transition["interval"]["starts_on"])),
                _local_midnight(date.fromisoformat(transition["interval"]["ends_before"])),
                # Existing position convention of the location history; never a date.
                ordering_anchor=_local_midnight(date.fromisoformat(destination["week_start"])),
            ),
            origin_zone_id=origin_zone, destination_zone_id=destination_zone,
            sources=[SourceRef("historical_transition", transition["id"], "event"),
                     SourceRef("historical_temperature_reading", origin["reading_id"], "observation", exclusive=False),
                     SourceRef("historical_temperature_reading", destination["reading_id"], "observation", exclusive=False)],
            provenance=TRANSITION_PROVENANCE,
            details={"policy": transition["policy"], "from_environment_c": transition["from_environment_c"],
                     "to_environment_c": transition["to_environment_c"],
                     "from_iso_week": [origin["iso_year"], origin["iso_week"]],
                     "to_iso_week": [destination["iso_year"], destination["iso_week"]]},
        )
        if representation.get("kind") == "location_periods":
            for period_id, side in ((representation["origin_location_id"], "end"),
                                    (representation["destination_location_id"], "start")):
                if period_id in by_period:
                    builder.claim(transition["id"], by_period[period_id], side, "location_periods")
    return [evidences[week] for week in sorted(evidences)]


def _add_starting_location(builder, box, periods, zones):
    start = project_starting_location(box=box, periods=periods, zones=zones)
    if start is None:
        return
    corroboration = SourceRef("starting_location", box.pk, "corroboration", exclusive=False)
    represented = f"movement:{start.get('represented_movement_id')}"
    if start.get("represented_movement_id") is not None and represented in builder.events:
        builder.attach(represented, corroboration)
        return
    if start.get("observed_on"):
        day = _local_midnight(date.fromisoformat(start["observed_on"]))
        # An observation day bounds the fact; it is not an occurrence instant.
        temporal = TemporalPosition.observation_interval(
            day, _local_midnight(day.date() + timedelta(days=1)), ordering_anchor=day)
    else:
        temporal = TemporalPosition.week(start["iso_year"], start["iso_week"], MOVEMENT_WEEK_TIME_ZONE)
    descriptor = {"box_id": box.pk, "observed_on": start.get("observed_on"), "iso_year": start.get("iso_year"),
                  "iso_week": start.get("iso_week"), "zone_id": start["thermal_zone"]["id"]}
    builder.add(
        STARTING_LOCATION_KEY,
        identity=EventIdentity(STARTING_LOCATION_KEY, IdentityStability.DERIVED,
                               evidence_fingerprint=fingerprint(descriptor), locator=(("starting_location", box.pk),)),
        kind=EventKind.INITIAL_HISTORICAL_LOCATION, origin=EventOrigin.STARTING_LOCATION, temporal=temporal,
        destination_zone_id=start["thermal_zone"]["id"],
        sources=[SourceRef("starting_location", box.pk, "event")], provenance="starting_location_projection",
    )


def _add_period_boundaries(builder, periods):
    """Emit the boundaries no event explains, as evidence of unknown nature."""
    starts = [period for period in periods if (period.pk, "start") not in builder.claimed]
    ends = [period for period in periods if (period.pk, "end") not in builder.claimed
            and (period.ends_at is not None or period.end_date_unknown)]
    paired = set()
    for destination in starts:
        closing = [period for period in ends if period.pk != destination.pk and not period.end_date_unknown
                   and period.ends_at == destination.starts_at]
        opening = [period for period in starts if period.starts_at == destination.starts_at]
        if len(closing) != 1 or len(opening) != 1:
            continue
        origin = closing[0]
        key = f"location:{origin.pk}:end+location:{destination.pk}:start"
        builder.add(key, kind=EventKind.PERIOD_BOUNDARY, origin=EventOrigin.BOX_LOCATION,
                    temporal=TemporalPosition.recorded_boundary(destination.starts_at),
                    origin_zone_id=origin.thermal_zone_id, destination_zone_id=destination.thermal_zone_id,
                    provenance="box_location", established=False)
        builder.claim(key, origin, "end", "stored_boundary")
        builder.claim(key, destination, "start", "stored_boundary")
        paired |= {(origin.pk, "end"), (destination.pk, "start")}
    for period in starts:
        if (period.pk, "start") in paired:
            continue
        key = f"location:{period.pk}:start"
        builder.add(key, kind=EventKind.PERIOD_BOUNDARY, origin=EventOrigin.BOX_LOCATION,
                    temporal=TemporalPosition.recorded_boundary(period.starts_at),
                    destination_zone_id=period.thermal_zone_id, provenance="box_location", established=False)
        builder.claim(key, period, "start", "stored_boundary")
    for period in ends:
        if (period.pk, "end") in paired:
            continue
        key = f"location:{period.pk}:end"
        temporal = (TemporalPosition.unknown(not_before=period.starts_at) if period.end_date_unknown
                    else TemporalPosition.recorded_boundary(period.ends_at))
        builder.add(key, kind=EventKind.PERIOD_BOUNDARY, origin=EventOrigin.BOX_LOCATION, temporal=temporal,
                    origin_zone_id=period.thermal_zone_id, provenance="box_location", established=False,
                    details={"end_date_unknown": period.end_date_unknown})
        builder.claim(key, period, "end", "stored_boundary")
