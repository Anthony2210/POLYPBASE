"""Persist synthetic movement evidence for history-reader tests."""

from apps.audit.models import AuditLog

from .models import HistoricalBoxMovement


def record_reconstruction(*, box, actor, plan, legacy=False, reviewed=False):
    candidate = plan["candidates"][0]
    evidence = candidate["evidence"]
    event = HistoricalBoxMovement.objects.create(
        box=box, iso_year=candidate["iso_year"], iso_week=candidate["iso_week"],
        from_thermal_zone_id=candidate["from_thermal_zone_id"],
        to_thermal_zone_id=candidate["to_thermal_zone_id"],
        origin_location_id=evidence["origin_location_id"],
        destination_location_id=evidence["destination_location_id"],
        candidate_key=candidate["id"], plan_fingerprint=plan["fingerprint"],
        evidence={"basis": "legacy_unestablished"} if legacy else evidence,
        accepted_by=actor, notes="Synthetic reconstruction",
    )
    if reviewed:
        AuditLog.objects.create(
            organization_id=box.organization_id, user=actor, action=AuditLog.Action.CREATION,
            object_type="box", object_id=box.global_code,
            metadata={"event": plan["version"], "policy": plan["policy"], "box_id": box.pk,
                      "historical_movement_id": event.pk, "candidate_id": candidate["id"],
                      "plan_fingerprint": plan["fingerprint"], "evidence": event.evidence,
                      "notes": event.notes},
        )
    return event
