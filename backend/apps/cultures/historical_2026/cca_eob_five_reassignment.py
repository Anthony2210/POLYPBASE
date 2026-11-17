"""Exceptional reassignment of five reviewed CCA-EOB 2026 measurements.

A researcher confirmed that five measurements recorded on CCA-EOB-2.006 (Box 1837)
belong to CCA-EOB-2.004 (Box 1835). This module moves exactly those rows, changing
only ``box_id``. It is not a generic reassignment tool: every identifier, date and
count below is part of the approval and any difference stops the operation.

The older CCA additive contract (``config.cca_eob_additive``) kept measurement
104689 on Box 1837. That contract, its audits and its documents are not edited.
This separate operation records the later researcher correction as its basis.
"""

from datetime import date

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import connection, transaction

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.cultures.models import (
    AutomaticBoxCreationRequest, Box, BoxLineage, BoxLocation, BoxMovement, BoxTransfer,
    BoxTransferImport, HistoricalBoxMovement, PortableLineageNode, SubcultureAllocation,
    SubcultureEvent, ThermalZone, TransferItem,
)
from apps.cultures.polyp_state import resolve_current_polyp_state
from apps.exports import services as export_services
from apps.measurements.models import BiologicalMeasurement, DailyTemperature
from apps.measurements.services import _measurement_audit_values
from apps.organizations.models import Organization
from config import cca_eob_additive

from .cca_eob_correction import _check_location_context, _json, _location_context
from .species_correction import fingerprint, snapshot

OPERATION = "cca_eob_2026_five_measurement_reassignment_v1"
RECEIPT_OBJECT_TYPE = "historical_correction"
ORGANIZATION_ID = 1
SOURCE_BOX_ID = 1837
SOURCE_CODE = "CCA-EOB-2.006"
TARGET_BOX_ID = 1835
TARGET_CODE = "CCA-EOB-2.004"
STRAIN_ID = 50
ZONE_ID = 3
EXPECTED_COUNT = 5
EXACT = "exact_date"
LEGACY = "legacy_unverified"

# pk -> (measured_on, ISO week, polyps, ephyrae, temporal precision, polyp_state_sequence)
APPROVED = {
    104358: (date(2026, 10, 7), 41, 50, 0, LEGACY, 23),
    104529: (date(2026, 10, 14), 42, 40, 0, LEGACY, 24),
    104689: (date(2026, 10, 21), 43, 69, 0, EXACT, 25),
    104849: (date(2026, 10, 28), 44, 75, 0, EXACT, 66),
    105036: (date(2026, 11, 4), 45, 75, 0, EXACT, 67),
}
PRODUCT_DECISION = {
    "basis": "researcher_confirmation",
    "statement": "Five biological measurements recorded on CCA-EOB-2.006 belong to CCA-EOB-2.004.",
    "scope": "the five explicitly listed measurements only",
    "changed_field": "box_id",
    "lifecycle_unchanged": True,
    "location_and_movement_history_unchanged": True,
    "thermal_zones_and_temperatures_unchanged": True,
    "environment_source_box_not_used": True,
    "weekly_precision_and_zero_values_preserved": True,
}
PRIOR_CONTRACT = {
    "module": "config.cca_eob_additive",
    "contract": "post-plan additions 104688/104689 of the CCA-EOB V2 transition",
    "measurement_id": 104689,
    "retained_box_id": SOURCE_BOX_ID,
    "documents": ["docs/cca-eob-v2-additive-transition-runtime.md",
                  "docs/cca-eob-additive-baseline-review.md",
                  "docs/cca-eob-unlinked-72-handoff.md"],
    "status": "preserved_unmodified",
    "relationship": ("The researcher correction supersedes the Box attribution of the listed "
                     "measurement only. The historical contract and its audits are not rewritten."),
}
# Only these guards protect box_id changes of biology with historical temperatures.
REQUIRED_TRIGGERS = (
    ("measurements_biologicalmeasurement", "historical_biology_guard"),
    ("cultures_box", "historical_box_guard"),
    ("measurements_historicaltemperaturereading", "historical_reading_immutable"),
    ("measurements_historicaltemperaturereading", "historical_reading_valid"),
    ("measurements_historicaltemperatureevidence", "historical_evidence_immutable"),
)
# Reconstructions derived from adjacent weeks of biology must not exist near the move.
RECONSTRUCTION_WEEKS = range(40, 47)


def _blockers_for_rows(label, rows, blockers):
    if rows:
        blockers.append(f"{label}: {len(rows)} row(s) block this reassignment; resolve separately.")


def _json_rows(queryset):
    return [snapshot(row) for row in queryset.order_by("pk")]


def _trigger_state():
    """Names and enabled flags of the SQL guards; empty on non-PostgreSQL databases."""
    if connection.vendor != "postgresql":
        return {}
    with connection.cursor() as cursor:
        cursor.execute(
            """SELECT c.relname, t.tgname, t.tgenabled::text
               FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_class c ON c.oid = t.tgrelid
               WHERE NOT t.tgisinternal AND c.relname = ANY(%s) ORDER BY 1, 2""",
            [sorted({table for table, _ in REQUIRED_TRIGGERS})])
        return {f"{table}.{name}": enabled for table, name, enabled in cursor.fetchall()}


def _guard_issues(state):
    if connection.vendor != "postgresql":
        return ["PostgreSQL is required: the historical temperature SQL guards cannot be verified."]
    issues = [f"SQL guard {table}.{name} is missing or not enabled."
              for table, name in REQUIRED_TRIGGERS if state.get(f"{table}.{name}") != "O"]
    with connection.cursor() as cursor:
        cursor.execute("SHOW transaction_isolation")
        isolation = cursor.fetchone()[0]
    if isolation != "read committed":
        issues.append("READ COMMITTED is required by the historical temperature guards.")
    return issues


def _locations_covering(box_locations, measured_on):
    """Recorded periods that the export consumer would consider for one date."""
    result = []
    for location in box_locations:
        starts_on = export_services._local_date(location.starts_at)
        ends_on = export_services._local_date(location.ends_at) if location.ends_at else None
        if location.end_date_unknown:
            if starts_on == measured_on:
                result.append(location)
        elif starts_on <= measured_on and (ends_on is None or measured_on <= ends_on):
            result.append(location)
    return result


def _environment(boxes, measurements):
    """Resolve each date through the real export logic for source and target Box.

    The same zone id is evidence, not proof: the zone, the temperature value and the
    absence of ambiguity are all compared using the consumers' own functions.
    """
    zone_ids = {zone for box in boxes.values() for zone in export_services._box_zone_ids(box)}
    daily = {
        (row.thermal_zone_id, row.date): row.average_temperature_c
        for row in DailyTemperature.objects.filter(
            thermal_zone_id__in=zone_ids, date__in={m.measured_on for m in measurements})
    }
    rows, issues = [], []
    for measurement in measurements:
        row = {"measurement_id": measurement.pk, "measured_on": measurement.measured_on.isoformat(),
               "environment_source_box_id": measurement.environment_source_box_id}
        if measurement.environment_source_box_id is not None:
            issues.append(f"Measurement {measurement.pk} has an environment_source_box override.")
        resolved = {}
        for side, box in boxes.items():
            locations = list(box.locations.all())
            zone = export_services._thermal_zone_for_date(box, measurement.measured_on)
            covering = _locations_covering(locations, measurement.measured_on)
            temperature = export_services._measurement_temperature(
                box=box, measured_on=measurement.measured_on, daily_temperatures=daily)
            row[side] = {
                "box_id": box.pk, "zone_id": zone.pk if zone else None,
                "zone_organization_id": zone.organization_id if zone else None,
                "covering_location_ids": sorted(location.pk for location in covering),
                "covering_zone_ids": sorted({location.thermal_zone_id for location in covering}),
                "has_location_history": bool(locations),
                "temperature_c": None if temperature is None else str(temperature),
                "daily_temperature_recorded": (zone.pk, measurement.measured_on) in daily if zone else False,
            }
            resolved[side] = row[side]
            if zone is None:
                issues.append(f"Measurement {measurement.pk}: {side} Box resolves no thermal zone for its date.")
            elif zone.organization_id != ORGANIZATION_ID:
                issues.append(f"Measurement {measurement.pk}: {side} zone belongs to another institution.")
            if len(row[side]["covering_zone_ids"]) > 1:
                issues.append(f"Measurement {measurement.pk}: {side} Box has several zones for its date.")
        if resolved["source"]["zone_id"] != resolved["target"]["zone_id"]:
            issues.append(
                f"Measurement {measurement.pk}: environmental zone would change from "
                f"{resolved['source']['zone_id']} to {resolved['target']['zone_id']}.")
        if resolved["source"]["temperature_c"] != resolved["target"]["temperature_c"]:
            issues.append(
                f"Measurement {measurement.pk}: resolved temperature would change from "
                f"{resolved['source']['temperature_c']} to {resolved['target']['temperature_c']}.")
        rows.append(row)
    return rows, issues


def _export_zone_filter_ids(measurement_ids, zone_id):
    """Run the export zone filter itself; returns the measurement ids it keeps."""
    queryset = BiologicalMeasurement.objects.canonical().filter(pk__in=measurement_ids)
    kept = export_services._filter_measurements_by_zones(queryset, zone_ids=[zone_id])
    return sorted(kept.values_list("pk", flat=True))


def _dependencies(box_ids, measurement_ids):
    """Rows that could be affected by a changed Box attribution; any row blocks."""
    from apps.measurements.models import HistoricalTemperatureReading

    iso_weeks = [(2026, week) for week in RECONSTRUCTION_WEEKS]
    reconstructions = HistoricalBoxMovement.objects.filter(
        box_id__in=box_ids, iso_year=2026, iso_week__in=[week for _, week in iso_weeks])
    return {
        "historical_temperature_readings": _json_rows(
            HistoricalTemperatureReading.objects.filter(biological_measurement_id__in=measurement_ids)),
        "incoming_duplicates": _json_rows(
            BiologicalMeasurement.objects.filter(duplicate_of_id__in=measurement_ids)),
        "environment_overrides_to_boxes": _json_rows(
            BiologicalMeasurement.objects.filter(environment_source_box_id__in=box_ids)),
        "subculture_events": _json_rows(SubcultureEvent.objects.filter(parent_box_id__in=box_ids)),
        "subculture_allocations": _json_rows(SubcultureAllocation.objects.filter(child_box_id__in=box_ids)),
        "lineages": _json_rows(BoxLineage.objects.filter(parent_box_id__in=box_ids)
                               | BoxLineage.objects.filter(child_box_id__in=box_ids)),
        "portable_lineage_nodes": _json_rows(PortableLineageNode.objects.filter(local_box_id__in=box_ids)),
        "transfer_items": _json_rows(TransferItem.objects.filter(source_box_id__in=box_ids)),
        "box_transfers": _json_rows(BoxTransfer.objects.filter(box_id__in=box_ids)),
        "transfer_imports": _json_rows(BoxTransferImport.objects.filter(created_box_id__in=box_ids)),
        "automatic_creation_requests": _json_rows(AutomaticBoxCreationRequest.objects.filter(box_id__in=box_ids)),
        "merged_into": _json_rows(Box.objects.filter(merged_into_id__in=box_ids)),
        "historical_movement_reconstructions": _json_rows(reconstructions),
    }


def _digest(rows):
    return fingerprint(rows)


def _audit_rows(measurement_ids):
    """Previous audits that mention the five measurements; proved unchanged after apply."""
    ids = {str(pk) for pk in measurement_ids}
    rows = [snapshot(audit) for audit in AuditLog.objects.filter(organization_id=ORGANIZATION_ID).order_by("pk")
            if isinstance(audit.metadata, dict) and str(audit.metadata.get("measurement_id")) in ids]
    return rows


def _replay_rows():
    return _json_rows(AuditLog.objects.filter(object_type=RECEIPT_OBJECT_TYPE, object_id=OPERATION))


def _state_summary(box):
    return _json(resolve_current_polyp_state(box))


def build_plan(*, _boxes=None):
    """Read-only, deterministic plan. Fails closed with explicit blockers."""
    blockers = []
    organization = Organization.objects.filter(pk=ORGANIZATION_ID).first()
    box_rows = {box.pk: box for box in Box.objects.filter(
        pk__in=(SOURCE_BOX_ID, TARGET_BOX_ID)).select_related("strain").order_by("pk")}
    source, target = box_rows.get(SOURCE_BOX_ID), box_rows.get(TARGET_BOX_ID)
    if organization is None:
        blockers.append("Organization 1 is missing.")
    if source is None or target is None:
        blockers.append("Both exact Boxes 1837 and 1835 are required.")
        return _json({"operation": OPERATION, "ready": False, "blockers": blockers,
                      "organization_id": ORGANIZATION_ID, "entries": [], "move_order": []})
    for box, code in ((source, SOURCE_CODE), (target, TARGET_CODE)):
        if (box.organization_id, box.global_code, box.strain_id) != (ORGANIZATION_ID, code, STRAIN_ID):
            blockers.append(f"Box {box.pk} must be organization 1, {code}, strain {STRAIN_ID}.")
        if box.merged_into_id is not None:
            blockers.append(f"Box {box.pk} is merged; separate review required.")
    if (source.status, target.status) != (Box.Status.INACTIVE, Box.Status.ACTIVE):
        blockers.append("Source Box must be inactive and target Box active; lifecycle is not modified here.")

    box_ids = [SOURCE_BOX_ID, TARGET_BOX_ID]
    all_measurements = list(BiologicalMeasurement.objects.filter(box_id__in=box_ids).order_by("pk"))
    by_pk = {measurement.pk: measurement for measurement in all_measurements}
    selected = [by_pk[pk] for pk in sorted(APPROVED) if pk in by_pk]
    entries = []
    for pk in sorted(APPROVED):
        measured_on, iso_week, polyps, ephyrae, precision, sequence = APPROVED[pk]
        measurement = by_pk.get(pk)
        entry = {"measurement_id": pk, "approved": {
            "measured_on": measured_on.isoformat(), "iso_week": iso_week, "polyp_count": polyps,
            "ephyrae_count": ephyrae, "temporal_precision": precision, "polyp_state_sequence": sequence,
        }, "issues": [], "before": None}
        entries.append(entry)
        if measurement is None or measurement.box_id != SOURCE_BOX_ID:
            entry["issues"].append("Measurement is not currently on source Box 1837.")
            continue
        entry["before"] = _json(snapshot(measurement))
        actual = (measurement.measured_on, measurement.week_start.isocalendar().week,
                  measurement.polyp_count, measurement.ephyrae_count,
                  measurement.temporal_precision, measurement.polyp_state_sequence)
        if actual != (measured_on, iso_week, polyps, ephyrae, precision, sequence):
            entry["issues"].append("Persisted values differ from the approved allowlist.")
        if measurement.week_start != BiologicalMeasurement.week_start_for(measurement.measured_on):
            entry["issues"].append("Stored date and week disagree.")
        if measurement.week_start.isocalendar().year != 2026:
            entry["issues"].append("Only 2026 measurements are in scope.")
        if measurement.duplicate_of_id is not None:
            entry["issues"].append("Duplicate evidence cannot be reassigned.")
        for field in ("original_observer_id", "imported_by_id"):
            account = getattr(measurement, field)
            if account is not None and not OrganizationMembership.objects.filter(
                    organization_id=ORGANIZATION_ID, user_id=account).exists():
                entry["issues"].append("Historical attribution account is outside this institution.")
        if any(
                other.box_id == TARGET_BOX_ID and other.week_start == measurement.week_start
                for other in all_measurements):
            entry["issues"].append("Destination Box already has a row for this ISO week.")
        if sequence is not None and any(
                other.box_id == TARGET_BOX_ID and other.polyp_state_sequence == sequence
                for other in all_measurements):
            entry["issues"].append("Destination Box already uses this polyp state sequence.")
    if len(selected) != EXPECTED_COUNT:
        blockers.append("Exactly the five approved measurements must exist on the two Boxes.")
    for entry in entries:
        blockers.extend(f"Measurement {entry['measurement_id']}: {issue}" for issue in entry["issues"])

    dependencies = _dependencies(box_ids, list(APPROVED))
    for label, rows in dependencies.items():
        _blockers_for_rows(label, rows, blockers)

    boxes = sorted([source, target], key=lambda box: box.pk)
    location_context = _location_context(boxes)
    structure = {"before": {"box_id": SOURCE_BOX_ID}, "target_box_pk": TARGET_BOX_ID, "issues": []}
    _check_location_context(structure, {box.pk: box for box in boxes}, location_context, ORGANIZATION_ID)
    for issue in structure["issues"]:
        blockers.append(f"Location history: {issue}")

    environment, environment_issues = ([], [])
    if len(selected) == EXPECTED_COUNT:
        prefetched = {
            "source": Box.objects.prefetch_related("locations__thermal_zone").get(pk=SOURCE_BOX_ID),
            "target": Box.objects.prefetch_related("locations__thermal_zone").get(pk=TARGET_BOX_ID),
        }
        environment, environment_issues = _environment(prefetched, selected)
        kept = _export_zone_filter_ids([m.pk for m in selected], ZONE_ID)
        if kept != sorted(APPROVED):
            environment_issues.append(f"Export zone filter for zone {ZONE_ID} keeps {kept} on the source Box.")
    blockers.extend(f"Environment: {issue}" for issue in environment_issues)

    triggers = _trigger_state()
    blockers.extend(_guard_issues(triggers))
    if _replay_rows():
        blockers.append("This operation already has a receipt; replay is refused.")
    if cca_eob_additive.ADDITIONS.get(104689, (None,))[0] != SOURCE_BOX_ID:
        blockers.append("The preserved CCA additive contract no longer names Box 1837 for 104689.")

    moves = [e["measurement_id"] for e in entries if e["before"] is not None]
    revisions = []
    if not blockers:
        for box in boxes:
            sequences = [m.polyp_state_sequence for m in all_measurements if m.polyp_state_sequence is not None
                         and (m.box_id == box.pk and m.pk not in APPROVED
                              or box.pk == TARGET_BOX_ID and m.pk in APPROVED)]
            touched = len(moves)
            revisions.append({"box_id": box.pk, "before": box.polyp_state_revision,
                              "after": max([box.polyp_state_revision + touched, *sequences])})
    return _json({
        "operation": OPERATION, "organization_id": ORGANIZATION_ID,
        "source_box_id": SOURCE_BOX_ID, "target_box_id": TARGET_BOX_ID,
        "product_decision": PRODUCT_DECISION,
        "prior_contract": {**PRIOR_CONTRACT, "module_value": list(cca_eob_additive.ADDITIONS[104689])},
        "entries": entries, "move_order": moves,
        "baseline": {
            "boxes": [snapshot(box) for box in boxes],
            "box_measurement_digests": {
                str(box.pk): _digest([snapshot(m) for m in all_measurements if m.box_id == box.pk])
                for box in boxes},
            "location_context": location_context,
            "previous_audits": _audit_rows(APPROVED),
            "current_polyp_state": {str(box.pk): _state_summary(box) for box in boxes},
        },
        "dependencies": dependencies, "environment": environment,
        "guards": triggers, "revision_changes": revisions,
        "blockers": blockers, "ready": not blockers,
    })


def _per_measurement_audit(plan, entry, measurement, actor_id, plan_hash, reason):
    return {
        "organization_id": ORGANIZATION_ID, "user_id": actor_id, "action": AuditLog.Action.UPDATE,
        "object_type": "box", "object_id": TARGET_CODE,
        "description": f"{reason} {SOURCE_CODE} -> {TARGET_CODE}; measurement {entry['measurement_id']}.",
        "metadata": {
            "operation": OPERATION, "measurement_id": entry["measurement_id"], "plan_hash": plan_hash,
            "product_decision": plan["product_decision"], "prior_contract": plan["prior_contract"],
            "previous_box": SOURCE_CODE, "new_box": TARGET_CODE,
            "valeurs": _measurement_audit_values(measurement),
            "modifications": {"boite": {"avant": SOURCE_CODE, "apres": TARGET_CODE}},
            "before": entry["before"], "after": {**entry["before"], "box_id": TARGET_BOX_ID},
            "environment": next(row for row in plan["environment"]
                                if row["measurement_id"] == entry["measurement_id"]),
        },
    }


def _receipt(plan, actor_id, plan_hash, reason, audit_ids, after):
    return {
        "organization_id": ORGANIZATION_ID, "user_id": actor_id, "action": AuditLog.Action.IMPORT,
        "object_type": RECEIPT_OBJECT_TYPE, "object_id": OPERATION, "description": reason,
        "metadata": {
            "operation": OPERATION, "plan_hash": plan_hash, "reason": reason, "moved": plan["move_order"],
            "audit_ids": audit_ids, "revisions": plan["revision_changes"],
            "product_decision": plan["product_decision"], "prior_contract": plan["prior_contract"],
            "reviewed_plan": plan, "after": after,
        },
    }


def apply_plan(*, actor, reason, expected_count, expected_plan_hash):
    """Rebuild under locks, verify, move five rows and write audits in one transaction."""
    if actor is None or not getattr(actor, "pk", None):
        raise ValueError("An explicit authorized actor is required.")
    if not isinstance(reason, str) or len(reason.strip()) < 20:
        raise ValueError("An explicit reason (at least 20 characters, citing the researcher confirmation) is required.")
    if expected_count != EXPECTED_COUNT:
        raise ValueError(f"--expected-count must be exactly {EXPECTED_COUNT}.")
    if not expected_plan_hash:
        raise ValueError("The approved plan fingerprint is required.")
    if connection.vendor != "postgresql":
        raise ValueError("PostgreSQL is required to apply this reassignment.")
    reason = reason.strip()
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SHOW transaction_isolation")
            if cursor.fetchone()[0] != "read committed":
                raise ValueError("READ COMMITTED is required; do not run this inside another isolation level.")
        # Same order as ordinary measurement writes: Boxes, Organization, accounts, history, rows.
        boxes = list(Box.objects.select_for_update().filter(
            pk__in=(SOURCE_BOX_ID, TARGET_BOX_ID), organization_id=ORGANIZATION_ID).order_by("pk"))
        Organization.objects.only("id").select_for_update(no_key=True).get(pk=ORGANIZATION_ID)
        memberships = list(OrganizationMembership.objects.select_for_update().filter(
            organization_id=ORGANIZATION_ID, user_id=actor.pk).only("id", "is_active", "role"))
        user = get_user_model().objects.only("id", "is_active").select_for_update(no_key=True).get(pk=actor.pk)
        if not user.is_active or not any(m.is_active and m.role == "admin" for m in memberships):
            raise PermissionDenied("An active administrator membership in organization 1 is required.")
        ids = [box.pk for box in boxes]
        list(BoxMovement.objects.select_for_update(of=("self",)).filter(box_id__in=ids).order_by("pk"))
        list(BoxLocation.objects.select_for_update().filter(box_id__in=ids).order_by("pk"))
        list(ThermalZone.objects.select_for_update(no_key=True).filter(
            organization_id=ORGANIZATION_ID, pk=ZONE_ID))
        measurements = list(BiologicalMeasurement.objects.select_for_update().filter(
            box_id__in=ids).order_by("pk"))
        guards_before = _trigger_state()

        plan = build_plan()
        plan_hash = fingerprint(plan)
        if plan_hash != expected_plan_hash:
            raise ValueError("Stale plan: the database changed or differs from the approved dry-run.")
        if not plan["ready"]:
            raise ValueError("Unresolved blockers prevent the reassignment; nothing was changed.")
        if len(plan["move_order"]) != expected_count or sorted(plan["move_order"]) != sorted(APPROVED):
            raise ValueError("The plan does not resolve to exactly the five approved measurements.")

        by_pk = {m.pk: m for m in measurements}
        outside = {box_id: [m.pk for m in measurements if m.box_id == box_id and m.pk not in APPROVED]
                   for box_id in ids}
        audits_before = _audit_rows(APPROVED)
        entries = {entry["measurement_id"]: entry for entry in plan["entries"]}
        audit_ids = []
        for pk in plan["move_order"]:
            measurement = by_pk[pk]
            if _json(snapshot(measurement)) != entries[pk]["before"]:
                raise ValueError("The exact previous measurement state no longer matches.")
            # No save(): it would assign a new sequence and bump revisions by itself.
            changed = BiologicalMeasurement.objects.filter(
                pk=pk, box_id=SOURCE_BOX_ID, week_start=measurement.week_start,
                box__organization_id=ORGANIZATION_ID, duplicate_of__isnull=True,
            ).update(box_id=TARGET_BOX_ID)
            if changed != 1:
                raise ValueError("Measurement changed during application.")
            audit = AuditLog.objects.create(**_per_measurement_audit(
                plan, entries[pk], measurement, user.pk, plan_hash, reason))
            audit_ids.append(audit.pk)
        for revision in plan["revision_changes"]:
            changed = Box.objects.filter(
                pk=revision["box_id"], organization_id=ORGANIZATION_ID,
                polyp_state_revision=revision["before"],
            ).update(polyp_state_revision=revision["after"])
            if changed != 1:
                raise ValueError("The exact previous Box revision no longer matches.")

        after_measurements = list(BiologicalMeasurement.objects.filter(box_id__in=ids).order_by("pk"))
        for pk in plan["move_order"]:
            now = next(m for m in after_measurements if m.pk == pk)
            expected = {**entries[pk]["before"], "box_id": TARGET_BOX_ID}
            if _json(snapshot(now)) != expected:
                raise ValueError("A moved measurement differs from its approved after-image.")
        if {box: [m.pk for m in after_measurements if m.box_id == box and m.pk not in APPROVED]
                for box in ids} != outside:
            raise ValueError("Measurements outside the allowlist changed.")
        for box_id in ids:
            if _digest([snapshot(m) for m in after_measurements if m.box_id == box_id
                        and m.pk not in APPROVED]) != _digest([
                            snapshot(m) for m in measurements if m.box_id == box_id and m.pk not in APPROVED]):
                raise ValueError("A measurement outside the allowlist was modified.")
        after_boxes = list(Box.objects.filter(pk__in=ids).order_by("pk"))
        before_boxes = {row["id"]: row for row in plan["baseline"]["boxes"]}
        revisions = {r["box_id"]: r["after"] for r in plan["revision_changes"]}
        for box in after_boxes:
            if _json(snapshot(box)) != {**before_boxes[box.pk], "polyp_state_revision": revisions[box.pk]}:
                raise ValueError("A Box field other than its revision token changed.")
        if _json(_location_context(after_boxes)) != plan["baseline"]["location_context"]:
            raise ValueError("Location or movement history changed.")
        if _audit_rows(APPROVED)[:len(audits_before)] != audits_before:
            raise ValueError("A previous audit changed.")
        if _trigger_state() != guards_before:
            raise ValueError("A SQL guard changed state.")

        # Real consumers, now through the target Box.
        target = Box.objects.prefetch_related("locations__thermal_zone").get(pk=TARGET_BOX_ID)
        moved = [by_pk[pk] for pk in plan["move_order"]]
        for row in moved:
            row.refresh_from_db()
            zone = export_services._thermal_zone_for_date(target, row.measured_on)
            planned = next(r for r in plan["environment"] if r["measurement_id"] == row.pk)
            if zone is None or zone.pk != planned["target"]["zone_id"] or row.box_id != TARGET_BOX_ID:
                raise ValueError("The export zone resolution differs after the move.")
        if _export_zone_filter_ids(plan["move_order"], ZONE_ID) != sorted(APPROVED):
            raise ValueError("The export zone filter differs after the move.")
        _, exported = export_services.select_measurement_export_data(
            boxes=Box.objects.filter(pk=TARGET_BOX_ID), date_from=date(2026, 10, 5),
            date_to=date(2026, 11, 8), zone_ids=[ZONE_ID])
        if not set(APPROVED) <= {m.pk for m in exported}:
            raise ValueError("The export no longer includes the moved measurements on the target Box.")

        after_summary = {
            "boxes": {str(b.pk): {"status": b.status, "polyp_state_revision": b.polyp_state_revision}
                      for b in after_boxes},
            "current_polyp_state": {str(b.pk): _state_summary(b) for b in after_boxes},
        }
        receipt = AuditLog.objects.create(**_receipt(
            plan, user.pk, plan_hash, reason, audit_ids, _json(after_summary)))
        # Closes the replay window even if two sessions raced past the earlier check.
        if AuditLog.objects.filter(object_type=RECEIPT_OBJECT_TYPE, object_id=OPERATION).count() != 1:
            raise ValueError("Replay detected: more than one receipt for this operation.")
        plan["receipt_id"] = receipt.pk
        return plan
