"""Guarded local-only reassignment of the reviewed CCA-EOB 2026 observations."""

import json
import os
from collections import Counter
from datetime import date
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import connection, transaction
from django.db.models import Q

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.cultures.models import (
    Box, BoxLineage, BoxLocation, BoxMovement, SubcultureAllocation, SubcultureEvent,
    ThermalZone, TransferItem,
)
from apps.measurements.models import BiologicalMeasurement
from apps.measurements.services import _measurement_audit_values
from apps.organizations.models import Organization

from .cca_eob_source import V2_SHA256, load_source
from .manifest import load_manifest
from .species_correction import fingerprint, snapshot

OPERATION = "cca_eob_v2_20261016_v3"
CODES = ("CCA-EOB-2.004", "CCA-EOB-2.005", "CCA-EOB-2.006")
MATCH = "ALREADY_MATCHES"
MOVE = "REASSIGN_REQUIRED"
CONFLICT = "CONFLICT"
UNKNOWN = "INDETERMINATE"
OUTSIDE = "OUT_OF_SOURCE"
REASON = (
    "CCA-EOB 2026 biological identities corrected against the reviewed V2 workbook. "
    "Environmental context now resolves through the target box under Anthony's approved product decision. "
    "No location or movement history is modified."
)
PRODUCT_DECISION = {
    "approved_by": "Anthony",
    "biological_identity_source": "reviewed_excel_v2",
    "environmental_context": "target_box_existing_consumers",
    "environmental_context_change_accepted": True,
    "location_history_unchanged": True,
    "movement_history_unchanged": True,
    "thermal_zones_unchanged": True,
    "scientific_temperatures_unchanged": True,
    "no_environmental_records_inferred_from_source": True,
}


def require_local_database():
    """Do not inherit a shared database from the operator's default settings."""
    config = connection.settings_dict
    name = str(config["NAME"])
    if config["ENGINE"] == "django.db.backends.sqlite3":
        uri = urlsplit(name)
        if not config.get("OPTIONS") and (name == ":memory:" or (
            uri.scheme == "file" and not uri.netloc and not uri.fragment
            and uri.path.startswith("memorydb_")
            and parse_qs(uri.query).get("mode") == ["memory"]
        )):
            return
    if config["ENGINE"] == "django.db.backends.postgresql" and config.get("HOST") == "127.0.0.1":
        # libpq/Django options and service files can override an apparently safe
        # NAME/HOST. Reject redirection before opening any connection.
        options = config.get("OPTIONS", {})
        safe_options = {"options", "sslmode", "connect_timeout"}
        redirected = (set(options) - safe_options or os.environ.get("PGSERVICE")
                      or os.environ.get("PGSERVICEFILE")
                      or os.environ.get("PGHOSTADDR", "127.0.0.1") != "127.0.0.1")
        if not redirected and (str(config.get("PORT")), name) in {
            ("55438", "polypbase_anomalies_test_20261016"),
            ("55439", "test_subculture_qa"),
        }:
            return
    raise ValueError("Only the approved loopback test copy or isolated automated test database is allowed.")


def _verify_connection():
    connection.ensure_connection()
    if connection.vendor == "postgresql":
        info = connection.connection.info
        if info.hostaddr != "127.0.0.1" or (str(info.port), info.dbname) not in {
            ("55438", "polypbase_anomalies_test_20261016"),
            ("55439", "test_subculture_qa"),
        }:
            raise ValueError("The effective PostgreSQL connection is not an approved local test database.")


def _json(value):
    return json.loads(json.dumps(value, default=str))


def _pair(item):
    return item["polyps"], item["ephyrae"]


def _counts(measurement):
    return measurement.polyp_count, measurement.ephyrae_count


def _histories(box_ids):
    # A reassignment must not invalidate an immutable quantitative source or
    # silently reinterpret lineage. These cases need a separate reviewed plan.
    events = SubcultureEvent.objects.filter(parent_box_id__in=box_ids)
    allocations = SubcultureAllocation.objects.filter(child_box_id__in=box_ids)
    lineages = BoxLineage.objects.filter(Q(parent_box_id__in=box_ids) | Q(child_box_id__in=box_ids))
    transfers = TransferItem.objects.filter(source_box_id__in=box_ids)
    return {
        "subculture_events": [snapshot(obj) for obj in events.order_by("pk")],
        "subculture_allocations": [snapshot(obj) for obj in allocations.order_by("pk")],
        "lineages": [snapshot(obj) for obj in lineages.order_by("pk")],
        "transfer_items": [snapshot(obj) for obj in transfers.order_by("pk")],
    }


def _related_movements(box_ids):
    # Include incoming links: a movement on another box must not claim one of
    # these periods. Such links are integrity failures, not legacy missing links.
    return BoxMovement.objects.filter(
        Q(box_id__in=box_ids) | Q(origin_location__box_id__in=box_ids)
        | Q(destination_location__box_id__in=box_ids)
    )


def _location_context(boxes):
    box_ids = [box.pk for box in boxes]
    locations = list(BoxLocation.objects.filter(box_id__in=box_ids).order_by("pk"))
    movements = list(BoxMovement.objects.filter(box_id__in=box_ids).order_by("pk"))
    external_links = list(_related_movements(box_ids).exclude(box_id__in=box_ids).order_by("pk")
                          .values("id", "box_id", "origin_location_id", "destination_location_id"))
    zone_ids = {box.thermal_zone_id for box in boxes}
    zone_ids.update(location.thermal_zone_id for location in locations)
    zone_ids.update(movement.from_thermal_zone_id for movement in movements)
    zone_ids.update(movement.to_thermal_zone_id for movement in movements)
    return {
        "policy": "target_box_context_preserve_location_history_v2",
                "external_movement_links": external_links,
        "locations": [snapshot(obj) for obj in locations],
        "movements": [snapshot(obj) for obj in movements],
        # Only identity and tenancy are used. No temperature or location is inferred.
        "zones": list(ThermalZone.objects.filter(pk__in=zone_ids - {None})
                      .order_by("pk").values("id", "organization_id")),
    }


def _check_location_context(entry, boxes, context, organization_id):
    source = boxes[entry["before"]["box_id"]]
    target = boxes[entry["target_box_pk"]]
    pair_ids = {source.pk, target.pk}
    locations = [row for row in context["locations"] if row["box_id"] in pair_ids]
    movements = [row for row in context["movements"] if row["box_id"] in pair_ids]
    zones = {row["id"]: row["organization_id"] for row in context["zones"]}
    evidence = {
        "policy": context["policy"],
                "location_ids": [row["id"] for row in locations],
                "movement_ids": [row["id"] for row in movements],
                "resolution": "target_box_existing_consumers",
                "context_change_accepted": True,
        "previous_current_zone_id": source.thermal_zone_id,
        "target_current_zone_id": target.thermal_zone_id,
        "safe": False,
    }
    entry["location_context"] = evidence
    issues = []
    zone_ids = {source.thermal_zone_id, target.thermal_zone_id}
    zone_ids.update(row["thermal_zone_id"] for row in locations)
    zone_ids.update(row["from_thermal_zone_id"] for row in movements)
    zone_ids.update(row["to_thermal_zone_id"] for row in movements)
    if any(zones.get(zone_id) != organization_id for zone_id in zone_ids - {None}):
        issues.append("Referenced environmental zone is missing or outside this institution.")

    for row in locations:
        if row["ends_at"] is not None and (
            row["ends_at"] <= row["starts_at"] or row["end_date_unknown"]
        ):
            issues.append(f"Invalid historical location interval #{row['id']}.")
    for box_id in sorted(pair_ids):
        periods = sorted((row for row in locations if row["box_id"] == box_id),
                         key=lambda row: (row["starts_at"], row["id"]))
        for index, first in enumerate(periods):
            for second in periods[index + 1:]:
                # An unknown end is not evidence of overlap. Touching boundaries
                # are valid, even when a consumer includes both zones that day.
                if first["starts_at"] == second["starts_at"] or (
                    not first["end_date_unknown"] and (
                        first["ends_at"] is None or second["starts_at"] < first["ends_at"]
                    )
                ):
                    issues.append(f"Overlapping historical locations #{first['id']} and #{second['id']}.")

    locations_by_id = {row["id"]: row for row in context["locations"]}
    for movement in movements:
        for side, time_field, zone_field in (
            ("origin_location_id", "ends_at", "from_thermal_zone_id"),
            ("destination_location_id", "starts_at", "to_thermal_zone_id"),
        ):
            location_id = movement[side]
            if location_id is None:
                continue  # Legacy missing links are not reconstructed or rejected.
            location = locations_by_id.get(location_id)
            if location is None or location["box_id"] != movement["box_id"]:
                issues.append(f"Movement #{movement['id']} links a period of another box.")
            elif (location[time_field] != movement["moved_at"]
                  or location["thermal_zone_id"] != movement[zone_field]
                  or (side == "origin_location_id" and location["end_date_unknown"])):
                issues.append(f"Movement #{movement['id']} contradicts its linked period #{location_id}.")
    location_ids = {row["id"] for row in locations}
    for movement in context["external_movement_links"]:
        if location_ids.intersection({movement["origin_location_id"], movement["destination_location_id"]}):
            issues.append(f"External movement #{movement['id']} links a period of another box.")

    entry["issues"].extend(issues)
    evidence["safe"] = not issues
    return evidence["safe"]


def _ordered_moves(entries, measurements):
    """Topological order for the immediate box/week unique constraint; no staging."""
    pending = {e["measurement_pk"]: e for e in entries if e["classification"] == MOVE}
    occupied = {(m.box_id, m.week_start): m.pk for m in measurements}
    order = []
    while pending:
        ready = [e for e in pending.values()
                 if (e["target_box_pk"], date.fromisoformat(e["before"]["week_start"])) not in occupied]
        if not ready:
            raise ValueError("Occupied destination or cyclic reassignment; no deletion or merge is permitted.")
        for entry in sorted(ready, key=lambda e: e["measurement_pk"]):
            week = date.fromisoformat(entry["before"]["week_start"])
            occupied.pop((entry["before"]["box_id"], week))
            occupied[(entry["target_box_pk"], week)] = entry["measurement_pk"]
            order.append(entry["measurement_pk"])
            del pending[entry["measurement_pk"]]
    return order


def build_plan(organization_id, *, workbook_path):
    """Read only: exact source cells, exact legacy witness, full persisted baseline."""
    require_local_database()
    _verify_connection()
    source = load_source(workbook_path)
    manifest = load_manifest()
    organization = Organization.objects.filter(pk=organization_id).first()
    boxes = list(Box.objects.filter(organization_id=organization_id, global_code__in=CODES).order_by("pk"))
    by_code = {box.global_code: box for box in boxes}
    by_id = {box.pk: box.global_code for box in boxes}
    measurements = list(BiologicalMeasurement.objects.filter(box_id__in=by_id).order_by("pk"))
    histories = _histories(list(by_id))
    location_context = _location_context(boxes)
    blockers = []
    if organization is None or set(by_code) != set(CODES):
        blockers.append("All three exact Box codes must exist in the explicit organization.")
    legacy = [row for row in manifest["measurements"] if row["box"] in CODES and row["year"] == 2026]
    entries, selected = [], []
    for item in source["entries"]:
        week = date.fromisocalendar(2026, item["iso_week"], 1)  # ISO key, never an observation date.
        target = by_code.get(item["target_code"])
        witnesses = [row for row in legacy if row["iso_week"] == item["iso_week"] and _pair(row) == _pair(item)]
        entry = {**item, "classification": UNKNOWN, "measurement_pk": None,
                 "current_box": None, "target_box": item["target_code"],
                 "target_box_pk": target.pk if target else None, "current_value": None,
                 "source_value": list(_pair(item)), "before": None, "issues": [],
                 "legacy_witnesses": witnesses}
        entries.append(entry)
        # Counts alone are never a global search key. A single manifest witness
        # establishes the only permitted old box, within this exact ISO week.
        if len(witnesses) != 1 or target is None:
            entry["issues"].append("A unique exact historical witness and target Box are required.")
            continue
        allowed = {item["target_code"], witnesses[0]["box"]}
        candidates = [m for m in measurements if m.week_start == week
                      and by_id[m.box_id] in allowed and _counts(m) == _pair(item)]
        entry["candidate_pks"] = [m.pk for m in candidates]
        if len(candidates) != 1:
            entry["classification"] = CONFLICT if candidates else UNKNOWN
            entry["issues"].append("Expected exactly one current measurement matching the reviewed counts and old/target code.")
            continue
        measurement = candidates[0]
        selected.append(measurement.pk)
        entry.update(measurement_pk=measurement.pk, current_box=by_id[measurement.box_id],
                     current_value=list(_counts(measurement)), before=_json(snapshot(measurement)),
                     classification=MATCH if measurement.box_id == target.pk else MOVE)
        if BiologicalMeasurement.week_start_for(measurement.measured_on) != week:
            entry["classification"] = CONFLICT
            entry["issues"].append("Stored observation date and ISO key disagree.")
        if entry["classification"] == MOVE:
            for field in ("original_observer_id", "imported_by_id"):
                account_id = getattr(measurement, field)
                if account_id is not None and not OrganizationMembership.objects.filter(
                    organization_id=organization_id, user_id=account_id,
                ).exists():
                    entry["classification"] = CONFLICT
                    entry["issues"].append("Historical attribution account is outside this institution.")
    duplicate_pks = {pk for pk, count in Counter(selected).items() if count > 1}
    for entry in entries:
        if entry["measurement_pk"] in duplicate_pks:
            entry["classification"] = CONFLICT
            entry["issues"].append("One persisted measurement cannot satisfy two source observations.")
    covered_weeks = {e["iso_week"] for e in source["entries"]}
    for measurement in measurements:
        iso = measurement.week_start.isocalendar()
        if measurement.pk in selected or iso.year != 2026:
            continue
        covered = iso.week in covered_weeks
        entries.append({"classification": CONFLICT if covered else OUTSIDE,
                        "measurement_pk": measurement.pk, "current_box": by_id[measurement.box_id],
                        "target_box": None, "iso_year": iso.year, "iso_week": iso.week,
                        "current_value": list(_counts(measurement)), "source_value": None,
                        "before": _json(snapshot(measurement)),
                        "issues": ["Unmatched measurement in a source-covered week."] if covered else []})
    unsafe_locations = [entry for entry in entries if entry["classification"] == MOVE
                        and not _check_location_context(
                            entry, {box.pk: box for box in boxes}, location_context, organization_id)]
    if unsafe_locations:
        blockers.append("Environmental history integrity or institution check failed; see per-measurement location_context and issues.")
    if any(e["classification"] == MOVE for e in entries) and any(histories.values()):
        blockers.append("Subculture, lineage or transfer history touches the selected boxes; independent resolution required.")
    if any(e["classification"] in {CONFLICT, UNKNOWN} for e in entries):
        blockers.append("Every source observation must have an unambiguous, non-conflicting correspondence.")
    order = []
    if not blockers:
        try:
            order = _ordered_moves(entries, measurements)
        except ValueError as error:
            blockers.append(str(error))
            for entry in entries:
                if entry["classification"] == MOVE:
                    entry["classification"] = CONFLICT
                    entry["issues"].append(str(error))
    destinations = {e["measurement_pk"]: e["target_box_pk"] for e in entries if e["classification"] == MOVE}
    touched = Counter()
    for measurement in measurements:
        if measurement.pk in destinations:
            touched[measurement.box_id] += 1
            touched[destinations[measurement.pk]] += 1
    revisions = []
    for box in boxes:
        if touched[box.pk]:
            sequences = [m.polyp_state_sequence for m in measurements
                         if destinations.get(m.pk, m.box_id) == box.pk and m.polyp_state_sequence is not None]
            revisions.append({"box_id": box.pk, "before": box.polyp_state_revision,
                              "after": max([box.polyp_state_revision + touched[box.pk], *sequences])})
    return _json({"operation": OPERATION, "product_decision": PRODUCT_DECISION,
                      "organization_id": organization_id,
                  "v2_sha256": source["sha256"], "legacy_manifest_fingerprint": manifest["fingerprint"],
                  "entries": entries, "uncovered": source["uncovered"],
                  "excluded_2025": source["excluded_2025"],
                  "baseline": {"boxes": [snapshot(b) for b in boxes],
                               "measurements": [snapshot(m) for m in measurements], "histories": histories,
                                                              "location_context": location_context},
                  "move_order": order, "revision_changes": revisions,
                  "blockers": blockers, "ready": not blockers,
                  "counts": dict(Counter(e["classification"] for e in entries))})


def apply_plan(organization_id, *, workbook_path, actor, expected_plan_hash):
    """Rebuild under locks; update only box_id, revision tokens and new audit rows."""
    require_local_database()
    if actor is None or not actor.pk or not expected_plan_hash:
        raise ValueError("An explicit active administrator and reviewed plan hash are required.")
    _verify_connection()
    with transaction.atomic():
        # Boxes first: ordinary measurement writes lock Box then Organization, and
        # an Organization/user FOR UPDATE held before waiting for a Box can deadlock
        # with FK inserts in a concurrent subculture.
        boxes = list(Box.objects.select_for_update().filter(
            organization_id=organization_id, global_code__in=CODES).order_by("pk"))
        box_ids = [box.pk for box in boxes]
        # Then Organization before membership, like member PATCH (Organization,
        # membership). Locking the membership first deadlocks with that PATCH
        # when the deferred audit FK check waits for Organization at commit.
        # NO KEY UPDATE serializes with the PATCH but not with FK KEY SHARE checks.
        Organization.objects.select_for_update(no_key=True).get(pk=organization_id)
        memberships = list(OrganizationMembership.objects.select_for_update().filter(
            organization_id=organization_id, user_id=actor.pk))
        user = get_user_model().objects.select_for_update(no_key=True).get(pk=actor.pk)
        if not user.is_active or not any(m.is_active and m.role == "admin" for m in memberships):
            raise PermissionDenied("An active administrator membership in this organization is required.")
        # All services serialize on Box first. Movement corrections then lock
        # Movement before linked Location; keep that order for full snapshots.
        list(_related_movements(box_ids).select_for_update(of=("self",)).order_by("pk"))
        list(BoxLocation.objects.select_for_update().filter(box_id__in=box_ids).order_by("pk"))
        context = _location_context(boxes)
        # Protect tenancy without blocking ordinary FK references to the zone.
        # A subculture explicitly locking this same zone still serializes here.
        list(ThermalZone.objects.select_for_update(no_key=True).filter(
            pk__in=[zone["id"] for zone in context["zones"]]).order_by("pk"))
        # Same Box-before-measurement protocol as ordinary measurement writes.
        measurements = list(BiologicalMeasurement.objects.select_for_update().filter(
            box_id__in=box_ids).order_by("pk"))
        plan = build_plan(organization_id, workbook_path=workbook_path)
        if fingerprint(plan) != expected_plan_hash:
            raise ValueError("Stale plan: database or source changed; review a new dry-run.")
        if not plan["ready"]:
            raise ValueError("Unresolved conflicts block the entire correction.")
        if {b["id"] for b in plan["baseline"]["boxes"]} != set(box_ids):
            raise ValueError("The plan does not resolve to the exact locked boxes.")
        if {m["id"] for m in plan["baseline"]["measurements"]} != {m.pk for m in measurements}:
            raise ValueError("The plan does not resolve to the exact locked measurements.")
        by_pk = {m.pk: m for m in measurements}
        moves = {e["measurement_pk"]: e for e in plan["entries"] if e["classification"] == MOVE}
        for pk in plan["move_order"]:
            entry = moves[pk]
            measurement = by_pk[pk]
            if _json(snapshot(measurement)) != entry["before"]:
                raise ValueError("The exact previous measurement state no longer matches.")
            # Deliberately avoid save(): it would assign the target box's state
            # order. Historical sequence, date, writer and all metadata survive.
            changed = BiologicalMeasurement.objects.filter(
                pk=pk, box_id=measurement.box_id, box__organization_id=organization_id,
                week_start=measurement.week_start,
            ).update(box_id=entry["target_box_pk"])
            if changed != 1:
                raise ValueError("Measurement changed during application.")
            after = {**entry["before"], "box_id": entry["target_box_pk"]}
            AuditLog.objects.create(
                organization_id=organization_id, user=user, action=AuditLog.Action.UPDATE,
                object_type="box", object_id=entry["target_box"],
                description=(f"{REASON} {entry['current_box']} -> {entry['target_box']}; "
                             f"2026-W{entry['iso_week']:02}."),
                metadata={"operation": OPERATION, "product_decision": plan["product_decision"],
                                          "location_context": entry["location_context"], "measurement_id": pk,
                          "plan_hash": expected_plan_hash, "v2_sha256": V2_SHA256,
                          "legacy_manifest_fingerprint": plan["legacy_manifest_fingerprint"],
                          "source": {k: entry[k] for k in (
                              "sheet", "polyp_cell", "ephyrae_cell", "source_identifier",
                              "target_code", "iso_year", "iso_week", "polyps", "ephyrae")},
                          "previous_box": entry["current_box"], "new_box": entry["target_box"],
                          "valeurs": _measurement_audit_values(measurement),
                          "modifications": {"boite": {"avant": entry["current_box"], "apres": entry["target_box"]}},
                          "before": entry["before"], "after": after},
            )
        revisions = plan["revision_changes"]
        for revision in revisions:
            changed = Box.objects.filter(pk=revision["box_id"], organization_id=organization_id,
                                         polyp_state_revision=revision["before"]).update(
                                             polyp_state_revision=revision["after"])
            if changed != 1:
                raise ValueError("The exact previous Box revision no longer matches.")
        if moves:
            AuditLog.objects.create(
                organization_id=organization_id, user=user, action=AuditLog.Action.IMPORT,
                object_type="historical_correction", object_id=OPERATION, description=REASON,
                metadata={"operation": OPERATION, "plan_hash": expected_plan_hash,
                          "product_decision": plan["product_decision"], "reviewed_plan": plan, "revisions": revisions, "moved": len(moves)},
            )
        return plan
