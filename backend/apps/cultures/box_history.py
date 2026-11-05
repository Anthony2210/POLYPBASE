"""Authorized callers project facts without rewriting or deduplicating sources."""

from datetime import datetime, time, timedelta

from django.db.models import Q
from django.utils.dateparse import parse_datetime

from apps.accounts.identity import serialize_user_identity
from apps.accounts.models import OrganizationMembership
from apps.accounts.permissions import user_can_write_lab_data
from apps.audit.box_lookup import box_audits, numeric_box_audit_values
from apps.audit.models import AuditLog
from apps.measurements.models import BiologicalMeasurement

from .historical_movements import PROVENANCE, _plan
from .location_history_diagnostics import _read_only_snapshot
from .models import MOVEMENT_WEEK_TIME_ZONE, BoxMovement, ThermalZone


def box_history(*, box, user):
    with _read_only_snapshot():
        return _box_history(box=box, user=user)


def _box_history(*, box, user):
    organization_id = box.organization_id
    zones = {zone.pk: {"id": zone.pk, "name": zone.name} for zone in ThermalZone.objects.filter(organization_id=organization_id)}
    member_ids = set(OrganizationMembership.objects.filter(organization_id=organization_id).values_list("user_id", flat=True))
    periods = list(box.locations.order_by("starts_at", "pk"))
    by_period = {p.pk: p for p in periods if p.thermal_zone_id in zones}
    movements = list(box.movements.select_related("user").order_by("moved_at", "pk"))
    by_movement = {m.pk: m for m in movements}
    events = []
    writable = user.is_active and user_can_write_lab_data(user, box.organization)

    def identity(actor):
        return serialize_user_identity(actor) if actor and actor.pk in member_ids else None

    def event(event_id, kind, *, occurred_at=None, year=None, week=None, origin=None, destination=None,
              actor=None, provenance, evidence=None, status="recorded", movement_id=None, can_correct=False, notes=""):
        return {"id": event_id, "kind": kind, "temporal_precision": "exact" if occurred_at else "iso_week" if year else "unresolved",
                "occurred_at": occurred_at.isoformat() if occurred_at else None, "iso_year": year, "iso_week": week,
                "from_thermal_zone": zones.get(origin), "to_thermal_zone": zones.get(destination), "actor": identity(actor),
                "provenance": provenance, "evidence": evidence or {}, "reconciliation_status": status,
                "recorded_movement_id": movement_id, "can_correct": bool(can_correct), "notes": notes}

    for movement in movements:
        origin = by_period.get(movement.origin_location_id)
        destination = by_period.get(movement.destination_location_id)
        origin_matches = bool(origin and origin.ends_at == movement.moved_at and not origin.end_date_unknown and origin.thermal_zone_id == movement.from_thermal_zone_id)
        destination_matches = bool(destination and destination.starts_at == movement.moved_at and destination.thermal_zone_id == movement.to_thermal_zone_id)
        reliable = destination_matches and (movement.from_thermal_zone_id is None or origin_matches)
        year, week, _ = BoxMovement.week_start_for(movement.moved_at).isocalendar()
        events.append(event(f"movement:{movement.pk}", movement.kind, occurred_at=movement.moved_at, year=year, week=week,
                            origin=movement.from_thermal_zone_id, destination=movement.to_thermal_zone_id, actor=movement.user,
                            provenance="box_movement", evidence={"revision": movement.revision,
                            "origin_location_id": origin.pk if origin_matches else None,
                            "destination_location_id": destination.pk if destination_matches else None,
                            "period_pairing_reliable": reliable,
                            "has_origin_link": origin_matches, "has_destination_link": destination_matches},
                            movement_id=movement.pk, can_correct=writable, notes=movement.notes))

    codes = [code for code in (box.global_code, box.previous_global_code) if code]
    audits = list(box_audits(organization_id=organization_id, box_ids=[box.pk], codes=codes)
                  .select_related("user").order_by("created_at", "pk"))
    plan = _plan(organization_id=organization_id, box_ids=[box.pk],
                 audit_rows=numeric_box_audit_values(audits))
    candidates_by_id = {candidate["id"]: candidate for candidate in plan["candidates"]}
    for historical in box.historical_movements.select_related("accepted_by").order_by("iso_year", "iso_week", "pk"):
        candidate = candidates_by_id.get(historical.candidate_key)
        established = bool(candidate and candidate["reconciliation_status"] == "accepted"
                           and not candidate["blockers"] and candidate["evidence"]["basis"] == PROVENANCE)
        events.append(event(
            f"historical:{historical.pk}", "historical_reconstruction",
            year=historical.iso_year if established else None,
            week=historical.iso_week if established else None,
            origin=historical.from_thermal_zone_id, destination=historical.to_thermal_zone_id,
            provenance=PROVENANCE if established else "unverified_week_reconstruction",
            evidence={**historical.evidence, "stored_iso_year": historical.iso_year,
                      "stored_iso_week": historical.iso_week, "period_pairing_reliable": False,
                      "accepted_by": identity(historical.accepted_by), "accepted_at": historical.accepted_at.isoformat()},
            status="accepted" if established else "unresolved", notes=historical.notes,
        ))

    # Numeric identity establishes lifecycle facts; legacy codes remain barriers.
    audits = [audit for audit in audits if not isinstance(audit.metadata, dict)
              or audit.metadata.get("box_id", box.pk) == box.pk]
    lifecycle_period_pairs = {}
    for audit in audits:
        metadata = audit.metadata
        if not isinstance(metadata, dict) or type(metadata.get("box_id")) is not int or metadata["box_id"] != box.pk:
            continue
        transition = metadata.get("transition")
        if not isinstance(transition, str):
            continue
        kind = {"active->inactive": "deactivation", "inactive->active": "reactivation",
                "pending_review->active": "qualification", "pending_review->inactive": "qualification"}.get(transition)
        if kind is None:
            continue
        movement_id = metadata.get("movement_id")
        movement = by_movement.get(movement_id) if type(movement_id) is int else None
        paired = bool(movement and movement.kind == kind and movement.user_id == audit.user_id)
        closed = []
        changed_at = None
        if kind == "deactivation" and isinstance(metadata.get("changed_at"), str):
            try:
                changed_at = parse_datetime(metadata["changed_at"])
            except ValueError:
                pass
            if changed_at is not None and changed_at.utcoffset() is None:
                changed_at = None
            values = metadata.get("closed_location_ids", [])
            if isinstance(values, list) and all(type(value) is int for value in values):
                closed = [p for p in by_period.values() if p.pk in values and changed_at is not None
                          and p.ends_at == changed_at and p.starts_at < changed_at and not p.end_date_unknown]
                if len(closed) != len(values) or len(set(values)) != len(values):
                    closed = []
        if len(closed) == 1:
            lifecycle_period_pairs.setdefault(closed[0].pk, []).append((audit.pk, changed_at))
        at = movement.moved_at if paired else changed_at
        events.append(event(f"lifecycle:{audit.pk}", kind, occurred_at=at,
                            origin=closed[0].thermal_zone_id if len(closed) == 1 else None,
                            destination=movement.to_thermal_zone_id if paired else None, actor=audit.user,
                            provenance="lifecycle_audit", evidence={"audit_id": audit.pk, "transition": transition,
                            "recorded_at": audit.created_at.isoformat(), "paired_movement_id": movement.pk if paired else None,
                            "lifecycle_pairing_reliable": paired, "closed_location_ids": [p.pk for p in closed],
                            "period_pairing_reliable": len(closed) == 1,
                            "inactive_pairing_reliable": False, "inactive_interval": None},
                            status="paired" if paired else "recorded"))

    _pair_inactive_intervals(box=box, events=events, audits=audits, periods=periods,
                             by_period=by_period, movements=movements)

    # Period boundaries are evidence, not additional physical moves. Keep them
    # even when paired; the consumer decides whether to collapse reliable pairs.
    for period in periods:
        if period.pk not in by_period:
            continue
        for side, at in (("start", period.starts_at), ("end", period.ends_at)):
            if side == "end" and at is None and not period.end_date_unknown:
                continue
            matches = [m for m in movements if (m.destination_location_id == period.pk if side == "start" else m.origin_location_id == period.pk)
                       and m.moved_at == at and (m.to_thermal_zone_id if side == "start" else m.from_thermal_zone_id) == period.thermal_zone_id]
            reliable = len(matches) == 1 and not (side == "end" and period.end_date_unknown)
            lifecycle_pairs = lifecycle_period_pairs.get(period.pk, []) if side == "end" else []
            lifecycle_reliable = len(lifecycle_pairs) == 1 and lifecycle_pairs[0][1] == at
            events.append(event(f"location:{period.pk}:{side}", "location_evidence", occurred_at=at if reliable or lifecycle_reliable else None,
                                # Unproven legacy boundaries are not certified as weeks.
                                origin=period.thermal_zone_id if side == "end" else None,
                                destination=period.thermal_zone_id if side == "start" else None,
                                provenance="box_location", evidence={"location_id": period.pk, "boundary": side,
                                "stored_boundary": at.isoformat() if at else None, "end_date_unknown": period.end_date_unknown,
                                "paired_movement_id": matches[0].pk if reliable else None,
                                "paired_lifecycle_event_id": f"lifecycle:{lifecycle_pairs[0][0]}" if lifecycle_reliable else None,
                                "lifecycle_pairing_reliable": lifecycle_reliable,
                                "period_pairing_reliable": reliable or lifecycle_reliable},
                                status="paired" if reliable or lifecycle_reliable else "unresolved", notes=period.notes))

    events.sort(key=lambda e: (e["occurred_at"] or "", e["iso_year"] or 0, e["iso_week"] or 0, e["id"]), reverse=True)
    return {"events": events, "candidates": plan["candidates"], "assessments": plan["assessments"]}


def _measurement_overlap_possible(measurements, start, end):
    """Observation days/weeks are uncertainty intervals, never midnight samples."""
    for measurement in measurements:
        if measurement.temporal_precision == BiologicalMeasurement.TemporalPrecision.EXACT_DATE:
            first = measurement.measured_on
            days = 1
        elif measurement.temporal_precision == BiologicalMeasurement.TemporalPrecision.LEGACY_UNVERIFIED:
            first = measurement.week_start
            days = 7
        else:
            return True
        try:
            lower = datetime.combine(first, time.min, tzinfo=MOVEMENT_WEEK_TIME_ZONE)
            upper = lower + timedelta(days=days)
        except (ValueError, OverflowError):
            return True
        if lower <= end and upper > start:
            return True
    return False


def _pair_inactive_intervals(*, box, events, audits, periods, by_period, movements):
    """Pair only proven recorded inactive gaps, not short active location periods."""
    lifecycle = [audit for audit in audits if isinstance(audit.metadata, dict) and "transition" in audit.metadata]
    lifecycle_events = {event["evidence"]["audit_id"]: event for event in events
                        if event["provenance"] == "lifecycle_audit"}
    by_movement = {movement.pk: movement for movement in movements}
    measurements = list(BiologicalMeasurement.objects.filter(
        Q(box_id=box.pk) | Q(environment_source_box_id=box.pk),
        box__organization_id=box.organization_id,
    ))
    historical = list(box.historical_movements.all())

    def state(audit, field, status, zone_id):
        value = audit.metadata.get(field)
        return (isinstance(value, dict) and value.get("status") == status
                and "thermal_zone_id" in value and type(value["thermal_zone_id"]) is type(zone_id)
                                and value["thermal_zone_id"] == zone_id)

    for deactivation, reactivation in zip(lifecycle, lifecycle[1:]):
        stopped = lifecycle_events.get(deactivation.pk)
        resumed = lifecycle_events.get(reactivation.pk)
        if not stopped or not resumed or stopped["kind"] != "deactivation" or resumed["kind"] != "reactivation":
            continue
        if (deactivation.action != AuditLog.Action.UPDATE or reactivation.action != AuditLog.Action.UPDATE
                or stopped["temporal_precision"] != "exact"
                or not stopped["evidence"]["period_pairing_reliable"]
                or not resumed["evidence"]["lifecycle_pairing_reliable"]):
            continue
        closed_ids = deactivation.metadata.get("closed_location_ids")
        if not isinstance(closed_ids, list) or len(closed_ids) != 1 or type(closed_ids[0]) is not int:
            continue
        if stopped["evidence"]["closed_location_ids"] != closed_ids:
            continue
        previous = by_period.get(closed_ids[0])
        movement = by_movement.get(resumed["evidence"]["paired_movement_id"])
        following = by_period.get(movement.destination_location_id) if movement else None
        if not previous or not following or not movement:
            continue
        start, end = previous.ends_at, movement.moved_at
        if (start is None or previous.end_date_unknown or previous.starts_at >= start or end <= start
                or following.starts_at != end or following.end_date_unknown
                or (following.ends_at is not None and following.ends_at <= end)
                or following.thermal_zone_id != movement.to_thermal_zone_id
                or movement.from_thermal_zone_id is not None or movement.origin_location_id is not None
                or reactivation.metadata.get("legacy_closed_location_ids") != []):
            continue
        if not (start <= deactivation.created_at < end <= reactivation.created_at):
            continue
        if not (state(deactivation, "before", "active", previous.thermal_zone_id)
                and state(deactivation, "after", "inactive", None)
                and state(reactivation, "before", "inactive", None)
                and state(reactivation, "after", "active", following.thermal_zone_id)):
            continue
        # A second audit claiming either endpoint makes the association ambiguous.
        closures = [audit for audit in lifecycle if isinstance(audit.metadata.get("closed_location_ids"), list)
                    and previous.pk in audit.metadata["closed_location_ids"]]
        openings = [audit for audit in lifecycle if audit.metadata.get("movement_id") == movement.pk]
        if len(closures) != 1 or len(openings) != 1:
            continue
        if any(other.pk != movement.pk and start <= other.moved_at <= end for other in movements):
            continue
        if any(period.pk not in (previous.pk, following.pk) and period.starts_at < end
               and (period.ends_at is None or period.ends_at > start) for period in periods):
            continue
        # Exact-ID reactivation's own movement audit is part of that same action.
        # Every other intervening scoped audit remains a conservative barrier.
        between = [audit for audit in audits
                   if (deactivation.created_at, deactivation.pk) < (audit.created_at, audit.pk)
                   < (reactivation.created_at, reactivation.pk)]
        if any(not (isinstance(audit.metadata, dict)
                            and audit.action == AuditLog.Action.UPDATE and audit.user_id == reactivation.user_id
                    and audit.metadata.get("movement_id") == movement.pk
                    and audit.metadata.get("movement_kind") == "reactivation"
                    and audit.metadata.get("destination_location_id") == following.pk
                    and "transition" not in audit.metadata) for audit in between):
            continue
        historical_overlap = False
        for reconstruction in historical:
            try:
                first = datetime.fromisocalendar(reconstruction.iso_year, reconstruction.iso_week, 1).replace(tzinfo=MOVEMENT_WEEK_TIME_ZONE)
                last = first + timedelta(days=7)
            except (ValueError, OverflowError):
                historical_overlap = True
                break
            if first <= end and last > start:
                historical_overlap = True
                break
        if historical_overlap:
            continue
        overlap = _measurement_overlap_possible(measurements, start, end)
        duration = (end - start).total_seconds()
        interval = {"deactivation_event_id": stopped["id"], "reactivation_event_id": resumed["id"],
                    "previous_location_id": previous.pk, "next_location_id": following.pk,
                    "reactivation_movement_id": movement.pk, "starts_at": start.isoformat(),
                    "ends_at": end.isoformat(), "duration_seconds": duration,
                    "measurement_overlap_possible": overlap,
                    "masking_eligible": duration < 86400 and not overlap}
        for event in (stopped, resumed):
            event["evidence"]["inactive_pairing_reliable"] = True
            event["evidence"]["inactive_interval"] = interval.copy()
