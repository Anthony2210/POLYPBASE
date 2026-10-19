"""Bounded, local-only PCA identity merge. Independent of historical corrections."""

import base64
import hashlib
import json
import os
import re
from datetime import date
from zoneinfo import ZoneInfo

from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import connection, transaction
from django.db.models import BooleanField, Q
from django.db.models.expressions import RawSQL
from django.utils import timezone

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.measurements.models import BiologicalMeasurement, MeasurementImportReceipt
from apps.measurements.services import _measurement_audit_values
from apps.organizations.models import Organization
from apps.taxonomy.models import Strain

from .box_codes import register_box_code
from .models import Box, BoxCodeNamespace, BoxLocation, BoxMovement, ThermalZone

OPERATION = "pca_identity_unification_v1"
RECEIPT_TYPE = "culture_identity_merge"
ORGANIZATION_ID = 1
CANONICAL_ID = 2135
DONOR_ID = 2141
BOX_IDS = (CANONICAL_ID, DONOR_ID)
OLD_CODE = "PCA-CHO-1.005"
OFFICIAL_CODE = "PCA-JKA-1.005"
RETIRED_CODE = "MERGED:2141:PCA-JKA-1.005"

STRAIN_CODES = {140: "PCA-CHO-1", 54: "PCA-JKA-1"}
SPECIES_ID = 78
NAMESPACES = tuple(sorted(STRAIN_CODES.values()))
PARIS = ZoneInfo("Europe/Paris")
QA_TIMEOUT_OPTIONS = "-c lock_timeout=10000 -c statement_timeout=30000"


def require_local_database(*, apply=False):
    """Validate configuration before connecting, including libpq redirections."""
    config = connection.settings_dict
    if (getattr(settings, "HISTORICAL_TEMPERATURE_PGQA", False)
            and settings.SETTINGS_MODULE == "config.historical_temperature_pgqa_settings"):
        # The parent provisions this cluster. Its dedicated backend verifies the
        # explicit target, cluster identity, restricted roles and QA lease.
        from config.historical_temperature_pgqa import assert_config

        assert_config(config)
        return config["HOST"], str(config["PORT"]), config["NAME"]
    raise ValueError("Only the independently verified disposable PostgreSQL QA harness is allowed.")


def _verify_connection(*, apply=False):
    target = require_local_database(apply=apply)
    if (getattr(settings, "HISTORICAL_TEMPERATURE_PGQA", False)
            and settings.SETTINGS_MODULE == "config.historical_temperature_pgqa_settings"):
        from config.historical_temperature_pgqa import assert_disposable

        assert_disposable(connection)
    connection.ensure_connection()
    info = connection.connection.info
    if (info.hostaddr, str(info.port), info.dbname) != target:
        raise ValueError("The effective PostgreSQL connection differs from the approved target.")


def _encode(value):
    if isinstance(value, (bytes, memoryview)):
        return {"base64": base64.b64encode(bytes(value)).decode("ascii")}
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def json_value(value):
    return json.loads(json.dumps(value, default=_encode, sort_keys=True))


def fingerprint(value):
    return hashlib.sha256(json.dumps(
        value, default=_encode, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")).hexdigest()


def snapshot(obj):
    return json_value({field.attname: getattr(obj, field.attname)
                       for field in obj._meta.concrete_fields})


def _rows(queryset):
    return [snapshot(row) for row in queryset.order_by("pk")]


def _require_scope(organization_id, actor):
    if type(organization_id) is not int or organization_id != ORGANIZATION_ID:
        raise ValueError("This operation requires explicit organization 1, not a general merge scope.")
    actor_id = getattr(actor, "pk", None)
    if actor_id is None:
        raise PermissionDenied("An explicit active administrator is required.")
    return actor_id


def _authorize(organization_id, actor_id, *, lock=False):
    organizations = Organization.objects
    memberships = OrganizationMembership.objects
    users = get_user_model().objects
    if lock:
        organizations = organizations.select_for_update(no_key=True)
        memberships = memberships.select_for_update()
        users = users.select_for_update(no_key=True)
    organization = organizations.get(pk=organization_id)
    members = list(memberships.filter(organization_id=organization_id, user_id=actor_id).order_by("pk"))
    user = users.get(pk=actor_id)
    if not user.is_active or not any(m.is_active and m.role == "admin" for m in members):
        raise PermissionDenied("An active user and active administrator membership in organization 1 are required.")
    return organization, user


def box_dependencies():
    """Include hidden/through and future model FKs, not a fixed related-name list."""
    return sorted(
        ((model, field) for model in apps.get_models(include_auto_created=True)
         for field in model._meta.concrete_fields
         if field.is_relation and field.remote_field.model is Box),
        key=lambda pair: (pair[0]._meta.label_lower, pair[1].name),
    )


def measurement_dependencies():
    """Models referring to a measurement, such as a future one-to-one temperature reading."""
    return sorted(
        ((model, field) for model in apps.get_models(include_auto_created=True)
         for field in model._meta.concrete_fields
         if field.is_relation and field.remote_field.model is BiologicalMeasurement
         and not (model is BiologicalMeasurement and field.name == "duplicate_of")),
        key=lambda pair: (pair[0]._meta.label_lower, pair[1].name),
    )


def _dependency_inventory(measurement_ids):
    allowed = {(BiologicalMeasurement, "box"), (BoxLocation, "box")}
    inventory = []
    for model, field in box_dependencies():
        # Dependency identities/counts are enough to fail closed; do not expose
        # arbitrary linked payloads (for example a foreign transfer envelope).
        rows = json_value(list(model._base_manager.filter(
            **{f"{field.attname}__in": BOX_IDS},
        ).order_by("pk").values("pk", field.attname)))
        inventory.append({"model": model._meta.label_lower, "field": field.attname,
                          "allowed": (model, field.name) in allowed,
                          "count": len(rows), "rows": rows})
    # Any record attached to the 112 measurements would silently follow a moved or
    # duplicate row, so it blocks the plan until independently reviewed.
    for model, field in measurement_dependencies():
        rows = json_value(list(model._base_manager.filter(
            **{f"{field.attname}__in": measurement_ids},
        ).order_by("pk").values("pk", field.attname)))
        inventory.append({"model": model._meta.label_lower, "field": field.attname,
                          "allowed": False, "count": len(rows), "rows": rows})
    return inventory


def _movements():
    return BoxMovement.objects.filter(
        Q(box_id__in=BOX_IDS) | Q(origin_location__box_id__in=BOX_IDS)
        | Q(destination_location__box_id__in=BOX_IDS),
    )


def _week_set(year, first, last):
    return {date.fromisocalendar(year, week, 1) for week in range(first, last + 1)}


def _evidence_query(fields, measurement_ids):
    """Match typed PCA references even inside nested reviewed JSON plans.

    PostgreSQL evaluates the predicate before returning or locking rows; unrelated
    institution payloads are not loaded into Python or included in the plan.
    """
    box_keys = ("box_id", "box_pk", "parent_box_id", "child_box_id", "source_box_id",
                "target_box_id", "canonical_id", "donor_id", "box.id", "box.pk", "box_ids[*]")
    measurement_keys = ("measurement_id", "measurement_pk", "measurement.id", "measurement_ids[*]")
    code_keys = ("code", "global_code", "box_code", "previous_global_code", "source_box",
                 "current_box", "target_box", "previous_box", "new_box")
    predicates = [f"@.{key} == $box_ids[*]" for key in box_keys]
    predicates += [f"@.{key} == $measurement_ids[*]" for key in measurement_keys]
    predicates += [f"@.{key} == $codes[*]" for key in code_keys]
    path = "$.** ? (" + " || ".join(predicates) + ")"
    variables = json.dumps({"box_ids": [*BOX_IDS, *map(str, BOX_IDS)],
                            "measurement_ids": [*measurement_ids, *map(str, measurement_ids)],
                            "codes": [OLD_CODE, OFFICIAL_CODE, RETIRED_CODE]})
    query = Q()
    for field in fields:
        query |= Q(RawSQL(
            f"jsonb_path_exists({connection.ops.quote_name(field)}, %s::jsonpath, %s::jsonb)",
            (path, variables), output_field=BooleanField(),
        ))
    return query


def _relevant_audits(measurement_ids):
    references = (
        Q(object_type="box", object_id__in=[OLD_CODE, OFFICIAL_CODE, RETIRED_CODE, *map(str, BOX_IDS)])
        | Q(object_type__in=("measurement", "biological_measurement"), object_id__in=list(map(str, measurement_ids)))
        | Q(object_type=RECEIPT_TYPE, object_id=OPERATION)
        | _evidence_query(("metadata", "original_values"), measurement_ids)
    )
    return AuditLog.objects.filter(organization_id=ORGANIZATION_ID).filter(references)


def _relevant_import_receipts(measurement_ids):
    return MeasurementImportReceipt.objects.filter(organization_id=ORGANIZATION_ID).filter(
        _evidence_query(("preparation", "accepted_plan", "result"), measurement_ids),
    )


def _make_plan(organization):
    boxes = list(Box.objects.filter(pk__in=BOX_IDS, organization=organization).order_by("pk"))
    if {box.pk for box in boxes} != set(BOX_IDS):
        # Do not inventory or disclose data on a PK owned by another tenant.
        return {"operation": OPERATION, "organization_id": ORGANIZATION_ID,
                "canonical_id": CANONICAL_ID, "donor_id": DONOR_ID,
                "baseline": {"boxes": [snapshot(box) for box in boxes]}, "changes": [],
                "counts": {"duplicates": 0, "moved": 0, "untouched": 0},
                "blockers": ["Both exact Box PKs must belong to organization 1."], "ready": False}
    measurements = list(BiologicalMeasurement.objects.filter(box_id__in=[b.pk for b in boxes]).order_by("pk"))
    locations = list(BoxLocation.objects.filter(box_id__in=[b.pk for b in boxes]).order_by("pk"))
    zone_ids = {b.thermal_zone_id for b in boxes} | {p.thermal_zone_id for p in locations}
    strains = list(Strain.objects.filter(pk__in=[b.strain_id for b in boxes]).filter(
        Q(organization_id=ORGANIZATION_ID) | Q(organization_id__isnull=True),
    ).order_by("pk"))
    zones = list(ThermalZone.objects.filter(pk__in=zone_ids, organization_id=ORGANIZATION_ID).order_by("pk"))
    measurement_ids = [measurement.pk for measurement in measurements]
    inventory = _dependency_inventory(measurement_ids)
    # Only PCA-related evidence is fingerprinted. No old log or receipt is edited.
    baseline = {"organization": snapshot(organization), "boxes": [snapshot(b) for b in boxes],
                "strains": [snapshot(s) for s in strains], "zones": [snapshot(z) for z in zones],
                "locations": [snapshot(p) for p in locations],
                "movements": _rows(_movements().filter(box__organization_id=ORGANIZATION_ID)),
                "measurements": [snapshot(m) for m in measurements], "dependencies": inventory,
                "namespaces": _rows(BoxCodeNamespace.objects.filter(namespace__in=NAMESPACES)),
                "audit_logs": _rows(_relevant_audits(measurement_ids)),
                "import_receipts": _rows(_relevant_import_receipts(measurement_ids))}
    blockers, changes = [], []
    by_id = {b.pk: b for b in boxes}
    if set(by_id) != set(BOX_IDS):
        blockers.append("Both exact Box PKs must belong to organization 1.")
    expected = {CANONICAL_ID: (OLD_CODE, 140, "active", 2),
                DONOR_ID: (OFFICIAL_CODE, 54, "inactive", None)}
    for box in boxes:
        if (box.global_code, box.strain_id, box.status, box.thermal_zone_id) != expected[box.pk]:
            blockers.append(f"Unexpected identity/status/location for Box {box.pk}.")
        if box.origin_id is not None or box.merged_into_id is not None or box.previous_global_code:
            blockers.append(f"Box {box.pk} requires a null origin and no prior merge identity.")
    if len(strains) != 2 or any(s.organization_id not in (None, ORGANIZATION_ID) for s in strains):
        blockers.append("Both exact Strain references must be shared or owned by organization 1.")
    if {strain.pk: (strain.code, strain.species_id) for strain in strains} != {
        pk: (code, SPECIES_ID) for pk, code in STRAIN_CODES.items()
    }:
        blockers.append("Require exact PCA-CHO-1/PCA-JKA-1 Strain codes and the same reviewed species 78.")
    if not any(zone.pk == 2 and zone.is_active for zone in zones):
        blockers.append("The canonical current zone 2 must be active in organization 1.")
    if any(z.organization_id != ORGANIZATION_ID for z in zones) or {z.pk for z in zones} != {1, 2}:
        blockers.append("The exact historical zones 1 and 2 must belong to organization 1.")
    if len(boxes) == 2 and boxes[0].box_number != boxes[1].box_number:
        blockers.append("The reviewed physical box numbers must agree.")
    if Box.objects.filter(global_code=RETIRED_CODE).exists():
        blockers.append("The unique technical retired code is already occupied.")
    if _movements().exists():
        blockers.append("Any movement linked directly or through a reviewed location requires independent review.")
    for entry in inventory:
        if entry["count"] and not entry["allowed"]:
            blockers.append(f"Unexpected Box or measurement dependency: {entry['model']}.{entry['field']}.")
    location_specs = {
        5979: (CANONICAL_ID, 1, date(2025, 12, 29), date(2026, 1, 12), False),
        5980: (CANONICAL_ID, 2, date(2026, 1, 12), None, False),
        5945: (DONOR_ID, 2, date(2024, 12, 30), None, True),
    }
    if {p.pk for p in locations} != set(location_specs):
        blockers.append("The exact three reviewed location periods are required.")
    for period in locations:
        actual = (period.box_id, period.thermal_zone_id, timezone.localtime(period.starts_at, PARIS).date(),
                  timezone.localtime(period.ends_at, PARIS).date() if period.ends_at else None,
                  period.end_date_unknown)
        if actual != location_specs.get(period.pk):
            blockers.append(f"Unexpected historical location period {period.pk}.")
    cho = [m for m in measurements if m.box_id == CANONICAL_ID]
    jka = [m for m in measurements if m.box_id == DONOR_ID]
    expected_cho = _week_set(2026, 1, 18) | _week_set(2026, 41, 42)
    expected_jka = _week_set(2025, 1, 52) | _week_set(2026, 1, 40)
    if (len(cho) != 20 or {m.week_start for m in cho} != expected_cho
            or len(jka) != 92 or {m.week_start for m in jka} != expected_jka):
        blockers.append("Require exactly 52 + 40 JKA and 18 + 2 CHO measurements in the reviewed ISO weeks.")
    for measurement in measurements:
        if (measurement.duplicate_of_id is not None or measurement.environment_source_box_id is not None
                or BiologicalMeasurement.week_start_for(measurement.measured_on) != measurement.week_start):
            blockers.append(f"Unexpected evidence relation or ISO key on measurement {measurement.pk}.")
    attribution_ids = {account_id for measurement in measurements
                       for account_id in (measurement.original_observer_id, measurement.imported_by_id)
                       if account_id is not None}
    attribution_memberships = list(OrganizationMembership.objects.filter(
        organization_id=ORGANIZATION_ID, user_id__in=attribution_ids,
    ).order_by("pk"))
    baseline["attribution_memberships"] = [snapshot(member) for member in attribution_memberships]
    if attribution_ids - {member.user_id for member in attribution_memberships}:
        blockers.append("Historical observer/importer attribution must belong to organization 1.")
    external_duplicates = list(BiologicalMeasurement.objects.filter(
        duplicate_of_id__in=[m.pk for m in measurements],
    ).order_by("pk").values("pk", "box_id", "duplicate_of_id"))
    baseline["existing_duplicate_references"] = external_duplicates
    if external_duplicates:
        blockers.append("Existing measurement duplicate references require independent review.")
    preserved = {104426: date.fromisocalendar(2026, 41, 1), 104540: date.fromisocalendar(2026, 42, 1)}
    if {m.pk: m.week_start for m in cho if m.week_start in _week_set(2026, 41, 42)} != preserved:
        blockers.append("Exact CHO W41/W42 measurement PKs 104426/104540 must remain untouched.")
    if any(measurement.user_id is None for measurement in cho if measurement.pk in preserved):
        blockers.append("CHO W41/W42 must retain their recorded non-null authors.")
    jka_by_week = {m.week_start: m for m in jka}
    for measurement in cho:
        if measurement.week_start not in _week_set(2026, 1, 18):
            continue
        target = jka_by_week.get(measurement.week_start)
        if target is not None:
            if ((measurement.polyp_count, measurement.ephyrae_count) != (target.polyp_count, target.ephyrae_count)
                    or measurement.strobila_count != 0 or target.strobila_count is not None):
                blockers.append(f"Unconfirmed loader provenance for duplicate measurement {measurement.pk}: require equal polyp/ephyrae counts, CHO strobila 0 and JKA strobila null.")
            changes.append({"model": "measurement", "id": measurement.pk,
                            "before": snapshot(measurement),
                            "after": {**snapshot(measurement), "duplicate_of_id": target.pk}})
    for measurement in jka:
        changes.append({"model": "measurement", "id": measurement.pk,
                        "before": snapshot(measurement),
                        "after": {**snapshot(measurement), "box_id": CANONICAL_ID,
                                  "environment_source_box_id": DONOR_ID}})
    if set(by_id) == set(BOX_IDS):
        canonical, donor = by_id[CANONICAL_ID], by_id[DONOR_ID]
        revision = max([b.polyp_state_revision for b in boxes]
                       + [m.polyp_state_sequence for m in measurements if m.polyp_state_sequence is not None]) + 1
        for box, updates in (
            (donor, {"merged_into_id": CANONICAL_ID, "previous_global_code": donor.global_code,
                     "global_code": RETIRED_CODE, "polyp_state_revision": donor.polyp_state_revision + 1}),
            (canonical, {"previous_global_code": canonical.global_code, "global_code": OFFICIAL_CODE,
                         "strain_id": 54, "local_code": donor.local_code or canonical.local_code,
                         "polyp_state_revision": revision}),
        ):
            changes.append({"model": "box", "id": box.pk, "before": snapshot(box),
                            "after": {**snapshot(box), **updates}})
    return {"operation": OPERATION, "organization_id": ORGANIZATION_ID, "canonical_id": CANONICAL_ID,
            "donor_id": DONOR_ID, "baseline": baseline, "changes": changes,
            "counts": {"duplicates": len([c for c in changes if "duplicate_of_id" in c["after"]
                                          and c["after"]["duplicate_of_id"] is not None]),
                       "moved": len(jka), "untouched": 2},
            "blockers": blockers, "ready": not blockers}


def build_plan(organization_id, *, actor):
    """A coherent read-only snapshot; the historical unmigrated copy is gated out."""
    require_local_database()
    actor_id = _require_scope(organization_id, actor)
    _verify_connection()
    # SET TRANSACTION must precede the first query. Refuse nested callers rather
    # than accidentally changing the isolation/read-only state of their work.
    if connection.in_atomic_block:
        raise ValueError("Build the read-only plan outside an existing transaction.")
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        organization, _ = _authorize(organization_id, actor_id)
        return _make_plan(organization)


def _validate_receipt(receipt, expected_plan_hash):
    metadata = receipt.metadata
    plan = metadata.get("reviewed_plan")
    if (not isinstance(plan, dict) or fingerprint(plan) != expected_plan_hash
            or plan.get("operation") != OPERATION or plan.get("organization_id") != ORGANIZATION_ID
            or not plan.get("ready") or metadata.get("changes") != plan.get("changes")
            or metadata.get("receipt_fingerprint") != fingerprint({
                k: v for k, v in metadata.items() if k != "receipt_fingerprint"})):
        raise ValueError("Merge receipt integrity check failed; independent review required.")
    return receipt


def apply_plan(organization_id, *, actor, expected_plan_hash, backup_ref, review_ref):
    """Rebuild under ordered locks and apply only the reviewed column deltas."""
    require_local_database(apply=True)
    actor_id = _require_scope(organization_id, actor)
    if (not isinstance(expected_plan_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_plan_hash)
            or not isinstance(backup_ref, str) or not backup_ref.strip()
            or not isinstance(review_ref, str) or not review_ref.strip()):
        raise ValueError("Apply requires a reviewed SHA-256 plan hash, backup reference and independent review reference.")
    _verify_connection(apply=True)
    with transaction.atomic():
        boxes = list(Box.objects.select_for_update().filter(
            pk__in=BOX_IDS, organization_id=organization_id).order_by("pk"))
        if {b.pk for b in boxes} != set(BOX_IDS):
            raise ValueError("Both exact Boxes must be present in organization 1.")
        organization, user = _authorize(organization_id, actor_id, lock=True)
        receipts = list(AuditLog.objects.filter(
            organization_id=organization_id, object_type=RECEIPT_TYPE, object_id=OPERATION,
        ).order_by("pk"))
        if receipts:
            if len(receipts) != 1 or receipts[0].metadata.get("plan_hash") != expected_plan_hash:
                raise ValueError("A different or ambiguous merge receipt already exists.")
            # A receipt replays an operation, not the current mutable Box state.
            return _validate_receipt(receipts[0], expected_plan_hash)
        list(_movements().select_for_update(of=("self",)).order_by("pk"))
        locations = list(BoxLocation.objects.select_for_update().filter(box_id__in=BOX_IDS).order_by("pk"))
        zone_ids = {b.thermal_zone_id for b in boxes} | {p.thermal_zone_id for p in locations}
        list(ThermalZone.objects.select_for_update(no_key=True).filter(pk__in=zone_ids).order_by("pk"))
        measurements = list(BiologicalMeasurement.objects.select_for_update().filter(box_id__in=BOX_IDS).order_by("pk"))
        list(Strain.objects.select_for_update(no_key=True).filter(pk__in=[b.strain_id for b in boxes]).order_by("pk"))
        # Lock existing counters only. Creating a missing counter here would
        # change the reviewed baseline before its fingerprint can be checked.
        list(BoxCodeNamespace.objects.select_for_update().filter(
            namespace__in=NAMESPACES).order_by("namespace"))
        # Freeze only PCA evidence, not every audit row in the institution.
        measurement_ids = [measurement.pk for measurement in measurements]
        list(_relevant_audits(measurement_ids).select_for_update(no_key=True).order_by("pk"))
        plan = _make_plan(organization)
        if fingerprint(plan) != expected_plan_hash:
            raise ValueError("Stale plan: review a new read-only plan before applying.")
        if not plan["ready"]:
            raise ValueError("Unresolved conflicts block the entire identity merge.")
        # Registration may now create missing counters, in the same sorted order
        # as the existing-row locks. A concurrent insert uses the allocator's
        # unique-key retry; neither existing source Box code is being allocated.
        for box in sorted(boxes, key=lambda row: STRAIN_CODES[row.strain_id]):
            register_box_code(box.global_code, namespace=STRAIN_CODES[box.strain_id])
        register_box_code(OFFICIAL_CODE, namespace=STRAIN_CODES[54])
        for change in plan["changes"]:
            model = BiologicalMeasurement if change["model"] == "measurement" else Box
            delta = {key: value for key, value in change["after"].items() if change["before"][key] != value}
            # No save(): retain original sequences, dates, authors and metadata;
            # duplicates must release the partial unique key before the moves.
            if model.objects.filter(pk=change["id"]).update(**delta) != 1:
                raise ValueError("A reviewed row disappeared during application.")
            current = model.objects.get(pk=change["id"])
            if snapshot(current) != change["after"]:
                raise ValueError("The persisted change differs from the reviewed exact snapshot.")
            metadata = {"operation": OPERATION, "plan_hash": expected_plan_hash, **change,
                        "box_id": CANONICAL_ID,
                        "source_box_id": change["before"].get("box_id", change["id"])}
            if model is BiologicalMeasurement:
                metadata.update(measurement_id=change["id"], valeurs=_measurement_audit_values(current),
                                modifications={})
            else:
                metadata.update(box_id=change["id"], canonical_box_id=CANONICAL_ID)
            AuditLog.objects.create(
                organization_id=organization_id, user=user, action=AuditLog.Action.UPDATE,
                object_type="box", object_id=OFFICIAL_CODE,
                description="Reviewed PCA culture identity unification",
                metadata=metadata,
            )
        metadata = {"operation": OPERATION, "plan_hash": expected_plan_hash, "actor_id": user.pk,
                    "backup_ref": backup_ref, "review_ref": review_ref, "reviewed_plan": plan,
                    "changes": plan["changes"], "counts": plan["counts"]}
        metadata["receipt_fingerprint"] = fingerprint(metadata)
        return AuditLog.objects.create(
            organization_id=organization_id, user=user, action=AuditLog.Action.UPDATE,
            object_type=RECEIPT_TYPE, object_id=OPERATION,
            description="Atomic PCA identity merge receipt; not a lifecycle or movement event",
            metadata=metadata,
        )
