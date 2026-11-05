"""Week-only reconstruction under the approved adjacent-period protocol.

The last origin observation week W owns the transition to destination week W+1.
Stored period boundaries are evidence, never fabricated occurrence timestamps.
"""

import hashlib
import json
from datetime import datetime, time, timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import connection, transaction
from django.utils import timezone

from apps.accounts.models import OrganizationMembership
from apps.accounts.permissions import user_can_administer_organization
from apps.audit.box_lookup import box_audits
from apps.audit.models import AuditLog
from apps.measurements.models import BiologicalMeasurement
from apps.organizations.models import Organization

from .location_history_diagnostics import _read_only_snapshot
from .models import MOVEMENT_WEEK_TIME_ZONE, Box, BoxLocation, BoxMovement, HistoricalBoxMovement, ThermalZone

VERSION = "historical_movements_v1"
POLICY = "last_origin_measurement_week_full_periods_v3"
PROVENANCE = "persisted_location_periods_and_canonical_measurement_weeks"


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, cls=DjangoJSONEncoder, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _scope(organization_id, box_ids):
    requested = sorted(set(box_ids))
    if not requested or any(type(pk) is not int or pk <= 0 for pk in requested):
        raise ValidationError("An explicit nonempty box selection is required.")
    boxes = list(Box.objects.filter(organization_id=organization_id, pk__in=requested, merged_into__isnull=True).order_by("pk"))
    if len(boxes) != len(requested):
        raise ValidationError("The box selection is not entirely within the institution scope.")
    return boxes


def _week_instant(week):
    return datetime.combine(week, time.min, tzinfo=MOVEMENT_WEEK_TIME_ZONE)


def _week_aligned(instant):
    return instant == _week_instant(BoxMovement.week_start_for(instant))


def _recorded_matches(movement, origin, destination):
    """Read correspondence without creating or changing any period link."""
    same_zones = (movement["from_thermal_zone_id"] == origin["thermal_zone_id"]
                  and movement["to_thermal_zone_id"] == destination["thermal_zone_id"])
    linked = (movement["origin_location_id"] == origin["id"]
              or movement["destination_location_id"] == destination["id"])
    consistent_links = (movement["origin_location_id"] in (None, origin["id"])
                        and movement["destination_location_id"] in (None, destination["id"]))
    matches = (same_zones and movement["moved_at"] == destination["starts_at"]
               and consistent_links)
    return matches, linked and not matches


def plan_historical_movements(*, organization_id, box_ids):
    """Read only, deterministic, exact selection; no permission granted here."""
    with _read_only_snapshot():
        return _plan(organization_id=organization_id, box_ids=box_ids)


def _plan(*, organization_id, box_ids, audit_rows=None):
    boxes = _scope(organization_id, box_ids)
    ids = [box.pk for box in boxes]
    periods = list(BoxLocation.objects.filter(box_id__in=ids).order_by("box_id", "starts_at", "pk").values())
    movements = list(BoxMovement.objects.filter(box_id__in=ids).order_by("pk").values())
    measurements = list(BiologicalMeasurement.objects.filter(box_id__in=ids).order_by("pk").values())
    accepted = list(HistoricalBoxMovement.objects.filter(box_id__in=ids).order_by("pk").values())
    zone_ids = {period["thermal_zone_id"] for period in periods}
    zones = list(ThermalZone.objects.filter(pk__in=zone_ids, organization_id=organization_id).order_by("pk").values())
    local_zone_ids = {zone["id"] for zone in zones}
    audits = (list(box_audits(organization_id=organization_id, box_ids=ids).order_by("pk")
                   .values(*(field.attname for field in AuditLog._meta.concrete_fields)))
              if audit_rows is None else audit_rows)
    snapshot = {
        "version": VERSION, "policy": POLICY, "organization_id": organization_id,
        "boxes": list(Box.objects.filter(pk__in=ids).order_by("pk").values()),
        "periods": periods, "movements": movements, "measurements": measurements,
        "zones": zones,
        # Receipts are checked independently below; inserting one must not stale
        # an otherwise unchanged preview on an idempotent replay.
        "audits": [audit for audit in audits if audit["metadata"].get("event") != VERSION],
    }
    candidates, assessments = [], []
    for box in boxes:
        locations = [period for period in periods if period["box_id"] == box.pk]
        biology = [row for row in measurements if row["box_id"] == box.pk and row["duplicate_of_id"] is None]
        recorded = [row for row in movements if row["box_id"] == box.pk]
        receipts = [row for row in accepted if row["box_id"] == box.pk]
        malformed = any(period["thermal_zone_id"] not in local_zone_ids
                        or (period["ends_at"] is not None and period["ends_at"] <= period["starts_at"])
                        for period in locations)
        overlapping = any(left["ends_at"] is None or left["ends_at"] > right["starts_at"]
                          for left, right in zip(locations, locations[1:]))
        if not locations:
            assessments.append({
                "id": fingerprint([POLICY, box.pk, "missing_locations"]),
                "box_id": box.pk, "classification": "insufficient_evidence",
                "reconciliation_status": "blocked", "blockers": ["location_history_missing"],
                "evidence": {},
            })
        if locations:
            initial = locations[0]
            assessments.append({
                "id": fingerprint([POLICY, box.pk, "initial", initial["id"]]),
                "box_id": box.pk, "classification": "initial_assignment",
                "reconciliation_status": "not_applicable", "blockers": [],
                "evidence": {"location_id": initial["id"], "stored_start": initial["starts_at"].isoformat(),
                             "physical_occurrence_proven": False},
            })
        transitions = []
        for origin, destination in zip(locations, locations[1:]):
            if origin["thermal_zone_id"] == destination["thermal_zone_id"]:
                continue
            boundary = destination["starts_at"]
            destination_week = BoxMovement.week_start_for(boundary)
            expected_origin_week = destination_week - timedelta(days=7)
            before = [row for row in biology if row["week_start"] == expected_origin_week]
            after = [row for row in biology if row["week_start"] == destination_week]
            # For a complete pair the attribution is taken from the origin
            # measurement itself, not the week containing the stored boundary.
            week = before[0]["week_start"] if len(before) == 1 else expected_origin_week
            year, number, _ = week.isocalendar()
            key = fingerprint([POLICY, box.pk, origin["id"], destination["id"], year, number])
            blockers = []
            if boundary > timezone.now():
                blockers.append("future_period_boundary")
            if (not _week_aligned(boundary) or not _week_aligned(origin["starts_at"])
                    or origin["starts_at"] > _week_instant(week)):
                blockers.append("whole_week_period_evidence_required")
            if malformed:
                blockers.append("invalid_or_foreign_period")
            if overlapping or origin["end_date_unknown"] or origin["ends_at"] != boundary:
                blockers.append("period_continuity_unresolved")
            if (destination["end_date_unknown"] or (destination["ends_at"] is not None
                    and (not _week_aligned(destination["ends_at"])
                         or destination["ends_at"] < _week_instant(destination_week + timedelta(days=7))))):
                blockers.append("destination_period_does_not_cover_week")
            if len(before) != 1 or len(after) != 1:
                blockers.append("adjacent_measurement_evidence_missing")
            if any(row["environment_source_box_id"] not in (None, box.pk) for row in before + after):
                blockers.append("measurement_environment_attribution_unresolved")
            if any(BiologicalMeasurement.week_start_for(row["measured_on"]) != row["week_start"]
                   for row in before + after):
                blockers.append("measurement_week_inconsistent")

            represented, mismatched = [], []
            for movement in recorded:
                matches, mismatch = _recorded_matches(movement, origin, destination)
                if matches:
                    represented.append(movement["id"])
                if mismatch:
                    mismatched.append(movement["id"])
            if mismatched:
                blockers.append("recorded_period_link_mismatch")
            if len(represented) > 1:
                blockers.append("multiple_recorded_representations")
            # Boundary rows belong to W+1 under BoxMovement's exact invariant;
            # unrelated rows in either W or W+1 make reconstruction unsafe.
            conflicts = [row["id"] for row in recorded if row["id"] not in represented
                         and BoxMovement.week_start_for(row["moved_at"]) in (week, destination_week)]
            if conflicts:
                blockers.append("recorded_event_in_week")
            lifecycle = [audit["id"] for audit in audits if audit["metadata"].get("box_id") == box.pk
                         and audit["metadata"].get("transition")
                         and BoxMovement.week_start_for(audit["created_at"]) in (week, destination_week)]
            if lifecycle:
                blockers.append("lifecycle_event_in_week")
            established = not blockers
            evidence = {
                "policy": POLICY, "basis": PROVENANCE,
                "source_location_provenance": "established_by_period_identity" if established else "unresolved",
                "origin_location_id": origin["id"], "destination_location_id": destination["id"],
                "measurement_ids": [row["id"] for row in before + after],
                "measurement_weeks": [row["week_start"].isoformat() for row in before + after],
                "attributed_week_start": week.isoformat(),
                "destination_week_start": destination_week.isoformat(),
                "stored_period_boundary": boundary.isoformat(),
                "represented_movement_ids": represented, "conflicting_movement_ids": conflicts,
                "mismatched_movement_ids": mismatched, "lifecycle_audit_ids": lifecycle,
                "physical_occurrence_proven": False, "exact_time_proven": False,
                "period_pairing_reliable": False,
            }
            existing = next((row for row in receipts if row["candidate_key"] == key), None)
            others = [row for row in receipts if row["candidate_key"] != key and (
                (row["iso_year"], row["iso_week"]) == (year, number)
                or (row["origin_location_id"], row["destination_location_id"]) == (origin["id"], destination["id"]))]
            if others:
                blockers.append("historical_event_in_week")
            if existing and (existing["evidence"] != evidence
                             or existing["origin_location_id"] != origin["id"]
                             or existing["destination_location_id"] != destination["id"]
                             or existing["from_thermal_zone_id"] != origin["thermal_zone_id"]
                             or existing["to_thermal_zone_id"] != destination["thermal_zone_id"]
                             or (existing["iso_year"], existing["iso_week"]) != (year, number)):
                blockers.append("historical_receipt_evidence_changed")
            if existing:
                receipt_audits = [audit for audit in audits
                                  if audit["metadata"].get("event") == VERSION
                                  and audit["metadata"].get("policy") == POLICY
                                  and audit["metadata"].get("historical_movement_id") == existing["id"]
                                  and audit["metadata"].get("candidate_id") == key
                                  and audit["metadata"].get("plan_fingerprint") == existing["plan_fingerprint"]
                                  and audit["metadata"].get("evidence") == existing["evidence"]
                                  and audit["action"] == AuditLog.Action.CREATION
                                  and audit["user_id"] == existing["accepted_by_id"]]
                if len(receipt_audits) != 1:
                    blockers.append("historical_receipt_audit_unresolved")
            if existing and represented:
                blockers.append("historical_and_recorded_event_coexist")
            row = {
                "id": key, "box_id": box.pk, "iso_year": year, "iso_week": number,
                "from_thermal_zone_id": origin["thermal_zone_id"] if origin["thermal_zone_id"] in local_zone_ids else None,
                "to_thermal_zone_id": destination["thermal_zone_id"] if destination["thermal_zone_id"] in local_zone_ids else None,
                "evidence": evidence, "blockers": sorted(set(blockers)),
            }
            transitions.append(row)
            if len(before) != 1 or len(after) != 1:
                assessments.append(row)
            else:
                candidates.append(row)
        for row in transitions:
            same_week = [other for other in transitions
                         if (other["iso_year"], other["iso_week"]) == (row["iso_year"], row["iso_week"])]
            if len(same_week) > 1:
                row["blockers"] = sorted(set(row["blockers"] + ["multiple_period_transitions_in_week"]))
            if row["blockers"]:
                insufficient = row["blockers"] == ["adjacent_measurement_evidence_missing"]
                row["classification"] = "insufficient_evidence" if insufficient else "ambiguous"
                row["reconciliation_status"] = "blocked"
            elif row["evidence"]["represented_movement_ids"]:
                row["classification"] = row["reconciliation_status"] = "already_represented"
            elif any(receipt["candidate_key"] == row["id"] for receipt in receipts):
                row["classification"] = row["reconciliation_status"] = "accepted"
            else:
                row["classification"] = "eligible"
                row["reconciliation_status"] = "review_required"
    return {"version": VERSION, "policy": POLICY, "organization_id": organization_id, "box_ids": ids,
            "fingerprint": fingerprint(snapshot), "candidates": candidates, "assessments": assessments}


def accept_historical_movements(*, organization_id, box_ids, candidate_ids, expected_fingerprint, actor, notes):
    """Accept exact reviewed candidates atomically; never changes operational history."""
    if (settings.SETTINGS_MODULE != "config.test_settings" or connection.vendor != "sqlite"
            or not connection.is_in_memory_db()):
        raise PermissionDenied("Acceptance is restricted to isolated in-memory test settings.")
    return _accept_historical_movements(
        organization_id=organization_id, box_ids=box_ids, candidate_ids=candidate_ids,
        expected_fingerprint=expected_fingerprint, actor=actor, notes=notes,
    )


@transaction.atomic
def _accept_historical_movements(*, organization_id, box_ids, candidate_ids, expected_fingerprint, actor, notes):
    organization = Organization.objects.select_for_update().get(pk=organization_id)
    list(OrganizationMembership.objects.select_for_update().filter(organization=organization, user_id=actor.pk).order_by("pk"))
    actor = get_user_model().objects.select_for_update().get(pk=actor.pk)
    if not actor.is_active or not user_can_administer_organization(actor, organization):
        raise PermissionDenied("An active institution administrator is required.")
    boxes = _scope(organization_id, box_ids)
    ids = [box.pk for box in boxes]
    list(Box.objects.select_for_update().filter(pk__in=ids).order_by("pk"))
    list(BoxLocation.objects.select_for_update().filter(box_id__in=ids).order_by("pk"))
    list(BoxMovement.objects.select_for_update().filter(box_id__in=ids).order_by("pk"))
    list(BiologicalMeasurement.objects.select_for_update().filter(box_id__in=ids).order_by("pk"))
    list(ThermalZone.objects.select_for_update().filter(organization=organization).order_by("pk"))
    list(HistoricalBoxMovement.objects.select_for_update().filter(box_id__in=ids).order_by("pk"))
    plan = _plan(organization_id=organization_id, box_ids=ids)
    if not expected_fingerprint or plan["fingerprint"] != expected_fingerprint:
        raise ValidationError("The historical movement preview has changed.")
    selected = set(candidate_ids)
    available = {candidate["id"]: candidate for candidate in plan["candidates"]}
    if not selected or not selected <= available.keys() or not notes.strip():
        raise ValidationError("Exact candidate IDs and a review note are required.")
    if any(available[key]["blockers"] or available[key]["reconciliation_status"] not in ("review_required", "accepted")
           for key in selected):
        raise ValidationError("Blocked or already represented historical movement candidates cannot be accepted.")
    weeks = [(available[key]["box_id"], available[key]["iso_year"], available[key]["iso_week"]) for key in selected]
    if len(set(weeks)) != len(weeks):
        raise ValidationError("Several candidates target the same box week.")
    results = []
    for key in sorted(selected):
        candidate = available[key]
        existing = HistoricalBoxMovement.objects.filter(candidate_key=key).first()
        if existing:
            results.append(existing.pk)
            continue
        evidence = candidate["evidence"]
        event = HistoricalBoxMovement(
            box_id=candidate["box_id"], iso_year=candidate["iso_year"], iso_week=candidate["iso_week"],
            from_thermal_zone_id=candidate["from_thermal_zone_id"], to_thermal_zone_id=candidate["to_thermal_zone_id"],
            origin_location_id=evidence["origin_location_id"], destination_location_id=evidence["destination_location_id"],
            candidate_key=key, plan_fingerprint=expected_fingerprint, evidence=evidence, accepted_by=actor, notes=notes.strip(),
        )
        event.save(force_insert=True)
        AuditLog.objects.create(
            organization=organization, user=actor, action=AuditLog.Action.CREATION,
            object_type="box", object_id=next(box.global_code for box in boxes if box.pk == event.box_id),
            description="Historical week-only movement reconstruction accepted",
            metadata={"event": VERSION, "policy": POLICY, "box_id": event.box_id,
                      "historical_movement_id": event.pk, "candidate_id": key,
                      "plan_fingerprint": expected_fingerprint, "evidence": evidence, "notes": event.notes},
        )
        results.append(event.pk)
    return results
