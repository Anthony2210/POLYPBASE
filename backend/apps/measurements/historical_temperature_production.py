"""Operator workflow for a partial historical temperature import on production.

Four separate actions: plan, backup, apply and postcheck. The scientific work is the
existing pipeline (extract_sources, plan, select_partial); this module only binds it
to the attested production target, a fresh live snapshot, a fresh backup and an
explicit authorization. Nothing here runs by itself and importing it never connects.

A plan is a pure function of the seven fingerprinted workbooks and the live
baseline. Apply recomputes it before taking locks, then compares the baseline again
under locks. Any difference is a stale plan and nothing is written.
"""

import hashlib
import io
import re
import zipfile
from collections import Counter, defaultdict
from contextlib import AbstractContextManager, ExitStack, contextmanager, nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

from config import database_backup, pca_protected_artifacts
from django.core.exceptions import PermissionDenied
from django.db import connection, transaction

from apps.audit.models import AuditLog
from apps.cultures import pca_identity_unification as merge
from . import historical_temperature_target as production
from .historical_temperature_backup_timeouts import check_backup_timeouts
from apps.cultures.models import Box
from apps.organizations.models import Organization

from .historical_temperature_apply import (
    _EVIDENCE_FIELDS, _archive_sources, _current_snapshot,
)
from .historical_temperature_partial import POLICY, select_partial, snapshot_baseline
from .historical_temperature_dry_run import build_report
from .historical_temperature_plan import _canonical_box
from .historical_temperature_source import SOURCES, fingerprint as source_fingerprint
from .import_planner import authorize_measurement_import
from .temperature_series import temperature_decimal
from .models import (
    BiologicalMeasurement, HistoricalTemperatureEvidence,
    HistoricalTemperatureImportReceipt, HistoricalTemperatureReading,
)

SCHEMA = "historical_temperature_production_plan_v1"
OPERATION = "historical_temperature_partial_import_production_v1"
ORGANIZATION_ID = 1
# State left by the production PCA merge of 2026-10-19 (see docs/pca-production-execution.md).
POST_PCA = {"receipt_audit_id": 2834, "actor_id": 6,
            "counts": {"duplicates": 18, "moved": 92, "untouched": 2}}
DOCUMENT_KEYS = {"schema", "operation", "organization_id", "target", "generated_at", "ready",
                 "blockers", "post_pca", "sources", "baseline", "touched_biology_fingerprint",
                 "plan", "review"}
PLAN_KEYS = {"policy", "plan_fingerprint", "report_fingerprint", "baseline_fingerprint",
             "global_ready", "summary", "payloads", "excluded", "source_exclusions", "header_issues"}
LOCKED_TABLES = ("cultures_box", "measurements_biologicalmeasurement",
                 "measurements_historicaltemperaturereading",
                 "measurements_historicaltemperatureevidence",
                 "measurements_historicaltemperatureimportreceipt")
LOCK_TIMEOUT = "10s"
STATEMENT_TIMEOUT = "120s"
CHUNK = 5000


class ProductionImportError(ValueError):
    pass


class StalePlanError(ProductionImportError):
    pass


def authorization_text(approved_hash):
    return f"APPLY_HISTORICAL_TEMPERATURES_ORGANIZATION_1:{approved_hash}"


def plan_hash(document):
    return merge.fingerprint(document)


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def _protected_sources(raw_directory):
    """Bind the private original workbooks while the existing pipeline reads them."""
    with ExitStack() as stack:
        if not production._is_disposable_qa():
            stack.enter_context(pca_protected_artifacts.protected_directory(raw_directory))
            for name in SOURCES:
                stack.enter_context(pca_protected_artifacts.protected_file(Path(raw_directory) / name))
        yield


def _chunks(values, size=CHUNK):
    values = list(values)
    for start in range(0, len(values), size):
        yield values[start:start + size]


def _require_scope(organization_id, actor):
    if type(organization_id) is not int or organization_id != ORGANIZATION_ID:
        raise ProductionImportError("This import requires explicit organization 1.")
    actor_id = getattr(actor, "pk", None)
    if actor_id is None:
        raise PermissionDenied("An explicit active administrator is required.")
    return actor_id


# ---------------------------------------------------------------- live state (DB reads)

def _readings_snapshot(organization_id):
    """Existing readings with their evidence, in the planner's baseline shape."""
    scope = {"biological_measurement__box__organization_id": organization_id}
    readings = list(HistoricalTemperatureReading.objects.filter(**scope).order_by("pk").values(
        "id", "biological_measurement_id", "association_status", "iso_year", "iso_week", "temperature_c"))
    evidences = defaultdict(list)
    rows = HistoricalTemperatureEvidence.objects.filter(
        reading__biological_measurement__box__organization_id=organization_id,
    ).order_by("pk").values("reading_id", *_EVIDENCE_FIELDS)
    for row in rows:
        evidences[row.pop("reading_id")].append(row)
    return [{**row, "evidences": evidences[row["id"]]} for row in readings]


def verify_post_pca(organization):
    """The reviewed PCA merge must be present and intact before any association.

    Structural fields of the 110 rows it touched are compared with its own receipt;
    scientific values and later weekly entries are deliberately not part of this check.
    """
    boxes = {box.pk: box for box in Box.objects.filter(
        pk__in=merge.BOX_IDS, organization=organization)}
    canonical, donor = boxes.get(merge.CANONICAL_ID), boxes.get(merge.DONOR_ID)
    if canonical is None or donor is None:
        raise ProductionImportError("post_pca_boxes_missing")
    if ((canonical.global_code, canonical.merged_into_id, canonical.previous_global_code)
            != (merge.OFFICIAL_CODE, None, merge.OLD_CODE)
            or (donor.global_code, donor.merged_into_id, donor.previous_global_code)
            != (merge.RETIRED_CODE, merge.CANONICAL_ID, merge.OFFICIAL_CODE)):
        raise ProductionImportError("post_pca_box_identity_differs")
    receipts = list(AuditLog.objects.filter(
        organization=organization, object_type=merge.RECEIPT_TYPE, object_id=merge.OPERATION).order_by("pk"))
    if len(receipts) != 1 or receipts[0].pk != POST_PCA["receipt_audit_id"]:
        raise ProductionImportError("post_pca_receipt_missing_or_ambiguous")
    receipt = receipts[0]
    try:
        merge._validate_receipt(receipt, receipt.metadata.get("plan_hash"))
    except ValueError:
        raise ProductionImportError("post_pca_receipt_integrity_failed") from None
    if (receipt.metadata.get("actor_id") != POST_PCA["actor_id"]
            or receipt.metadata.get("counts") != POST_PCA["counts"]):
        raise ProductionImportError("post_pca_receipt_differs")
    changes = [c for c in receipt.metadata["changes"] if c["model"] == "measurement"]
    current = {m.pk: m for m in BiologicalMeasurement.objects.filter(
        pk__in=[c["id"] for c in changes], box__organization=organization)}
    for change in changes:
        row, after = current.get(change["id"]), change["after"]
        if row is None or (row.box_id, row.duplicate_of_id, row.environment_source_box_id,
                           row.week_start.isoformat()) != (
                after["box_id"], after["duplicate_of_id"], after["environment_source_box_id"], after["week_start"]):
            raise ProductionImportError("post_pca_measurement_differs")
    if BiologicalMeasurement.objects.filter(box_id=merge.DONOR_ID).exists():
        raise ProductionImportError("post_pca_biology_left_on_retired_box")
    return {"receipt_audit_id": receipt.pk, "receipt_plan_hash": receipt.metadata["plan_hash"],
            "canonical_box_id": merge.CANONICAL_ID, "retired_box_id": merge.DONOR_ID,
            "checked_measurements": len(changes), "counts": receipt.metadata["counts"]}


def check_runtime_privileges():
    """The importer must not be able to replace or disable scientific guards."""
    checks = (
        ("""SELECT EXISTS (
            SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relname = ANY(%s)
              AND pg_has_role(current_user, c.relowner, 'MEMBER'))""", [list(LOCKED_TABLES)]),
        ("""SELECT EXISTS (
            SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE n.nspname = 'public' AND p.proname LIKE 'historical_temperature_%%'
              AND pg_has_role(current_user, p.proowner, 'MEMBER'))""", []),
        ("SELECT has_schema_privilege(current_user, 'public', 'CREATE')", []),
        ("SELECT has_database_privilege(current_user, current_database(), 'CREATE')", []),
    )
    with connection.cursor() as cursor:
        for sql, params in checks:
            cursor.execute(sql, params)
            if cursor.fetchone()[0]:
                raise ProductionImportError("Unsafe database ownership or DDL privileges; stop without privilege escalation.")


def check_schema():
    """Storage migrations and scientific guard triggers must be present and enabled."""
    from django.db.migrations.executor import MigrationExecutor

    from .historical_temperature_ui import EXPECTED_PENDING, UiOperationError, check_guards

    check_runtime_privileges()
    applied = MigrationExecutor(connection).loader.applied_migrations
    missing = [name for name in EXPECTED_PENDING if name not in applied]
    if missing:
        raise ProductionImportError("storage_migrations_not_applied: " + ", ".join(f"{a}.{n}" for a, n in missing))
    try:
        check_guards()
    except UiOperationError as error:
        raise ProductionImportError(str(error)) from None


def read_state(organization):
    """Boxes, biology and readings of the explicit institution, as one snapshot."""
    check_schema()
    base = _current_snapshot(organization.pk)
    readings = _readings_snapshot(organization.pk)
    pending = sorted(row["id"] for row in readings
                     if row["association_status"] != HistoricalTemperatureReading.AssociationStatus.CONFIRMED)
    snapshot = {
        "identity": {"organization_id": organization.pk}, **base,
        "historical_temperatures": [
            {key: value for key, value in row.items() if key != "association_status"}
            for row in readings],
    }
    return {"snapshot": snapshot, "fingerprint": source_fingerprint(snapshot), "pending": pending,
            "counts": {"boxes": len(base["boxes"]), "biological_measurements": len(base["biological_measurements"]),
                       "historical_temperatures": len(readings),
                       "historical_evidences": sum(len(row["evidences"]) for row in readings)},
            "post_pca": verify_post_pca(organization)}


def touched_fingerprint(organization, measurement_ids):
    """Every column (dates, counts, authors) of the biology the import attaches to."""
    wanted = set(measurement_ids)
    rows = [merge.snapshot(row) for row in BiologicalMeasurement.objects.filter(
        box__organization=organization).order_by("pk").iterator(chunk_size=CHUNK) if row.pk in wanted]
    if len(rows) != len(wanted):
        raise ProductionImportError("touched_biology_missing")
    return merge.fingerprint(rows)


# ---------------------------------------------------------------- plan (pure + reads)

def _review(plan, report, state):
    """Reviewer aids only; none of this is read back to decide anything."""
    excluded = plan["excluded"]
    issue_counts = Counter(issue for row in excluded for issue in row["issues"])
    mappings = Counter()
    zero_temperatures = zero_counts = 0
    for payload in plan["payloads"]:
        zero_temperatures += temperature_decimal(payload["temperature_c"]) == 0
        for evidence in payload["evidences"]:
            zero_counts += sum(temperature_decimal(evidence[field]) == 0 for field in ("polyp_value", "ephyrae_value"))
            canonical = _canonical_box(evidence["source_box"])
            if evidence["source_box"] != canonical:
                mappings[(evidence["source_box"], canonical)] += 1
    pca = [{"iso_year": row["iso_year"], "iso_week": row["iso_week"], "status": row["status"],
            "issues": row["issues"]}
           for row in report["association_plan"]["associations"]
           if row["canonical_box"] == merge.OFFICIAL_CODE]
    return {
        "association_status_counts": report["association_plan"]["summary"]["status_counts"],
        "excluded_issue_counts": dict(sorted(issue_counts.items())),
        "source_label_differs_from_canonical": [
            {"source_box": source, "canonical_box": canonical, "evidences": count}
            for (source, canonical), count in sorted(mappings.items(), key=lambda item: (item[0][0], item[0][1] or ""))],
        "pca_canonical_box_associations": pca,
        "zero_temperature_readings": zero_temperatures,
        "zero_biological_count_evidences": zero_counts,
        "existing_readings_in_baseline": state["counts"]["historical_temperatures"],
    }


def build_document(organization, raw_directory, target, generated_at):
    """Recompute a plan from the sources and the live baseline. Call inside a read snapshot."""
    state = read_state(organization)
    blockers = []
    if state["pending"]:
        blockers.append("PENDING_REVIEW_ASSOCIATION_PRESENT")
    baseline = snapshot_baseline(state["snapshot"])
    with _protected_sources(raw_directory):
        report = build_report(raw_directory, organization_id=organization.pk, baseline=baseline)
    sources = [{"filename": row["filename"], "iso_year": row["iso_year"], "sha256": row["sha256"]}
               for row in report["source_extraction"]["sources"]]
    if sources != [{"filename": name, "iso_year": year, "sha256": digest}
                   for name, (year, digest) in SOURCES.items()]:
        raise ProductionImportError("sources_differ_from_allowlist")
    plan = touched = None
    if report["association_plan"]["baseline_status"] != "VALID":
        blockers.append("BASELINE_INVALID")
        blockers.extend(sorted({row["code"] for row in report["association_plan"]["baseline_errors"]}))
    else:
        partial = select_partial(report)
        plan = {key: partial[key] for key in PLAN_KEYS}
        if not plan["payloads"]:
            blockers.append("NOTHING_TO_IMPORT")
        ids = [payload["biological_measurement_id"] for payload in plan["payloads"]]
        touched = touched_fingerprint(organization, ids)
        canonical_ids = set(BiologicalMeasurement.objects.filter(
            box__organization=organization, duplicate_of__isnull=True, box__merged_into__isnull=True,
        ).values_list("pk", flat=True))
        if set(ids) - canonical_ids:
            blockers.append("PAYLOAD_TARGETS_NON_CANONICAL_BIOLOGY")
    document = {
        "schema": SCHEMA, "operation": OPERATION, "organization_id": organization.pk, "target": target,
        "generated_at": generated_at, "ready": not blockers, "blockers": blockers,
        "post_pca": state["post_pca"], "sources": sources,
        "baseline": {"fingerprint": state["fingerprint"], "planner_fingerprint": report["baseline_fingerprint"],
                     "counts": state["counts"]},
        "touched_biology_fingerprint": touched, "plan": plan,
        "review": _review(plan, report, state) if plan else None,
    }
    return document


def _snapshot_transaction():
    if connection.in_atomic_block:
        raise ProductionImportError("Run outside an existing transaction.")
    return transaction.atomic()


def build_plan(organization_id, *, actor, target, raw_directory):
    production.require_target(target)
    actor_id = _require_scope(organization_id, actor)
    production.verify_target(target)
    with _snapshot_transaction():
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        production.verify_target(target, in_snapshot=True)
        organization, _user = merge._authorize(organization_id, actor_id)
        return build_document(organization, raw_directory, target, _utc_now())


# ---------------------------------------------------------------- plan validation

def _content(document):
    return {key: value for key, value in document.items() if key != "generated_at"}


def validate_plan(document, approved_hash):
    """Offline checks of the reviewed artifact. Freshness is checked separately."""
    if (not isinstance(approved_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", approved_hash)
            or type(document) is not dict or set(document) != DOCUMENT_KEYS
            or document["schema"] != SCHEMA or document["operation"] != OPERATION
            or plan_hash(document) != approved_hash):
        raise ProductionImportError("The production plan does not match the exact approved SHA-256 hash.")
    plan = document["plan"]
    if (document["organization_id"] != ORGANIZATION_ID or document["ready"] is not True
            or document["blockers"] or not isinstance(plan, dict) or set(plan) != PLAN_KEYS
            or plan["policy"] != POLICY or not plan["payloads"]):
        raise ProductionImportError("Only a ready, conflict-free plan with accepted readings can be applied.")
    ids = [payload["biological_measurement_id"] for payload in plan["payloads"]]
    if (len(set(ids)) != len(ids) or plan["summary"]["accepted_readings"] != len(ids)
            or plan["summary"]["accepted_evidences"] != sum(len(p["evidences"]) for p in plan["payloads"])
            or plan["baseline_fingerprint"] != document["baseline"]["planner_fingerprint"]):
        raise ProductionImportError("The production plan is internally inconsistent.")
    if document["sources"] != [{"filename": name, "iso_year": year, "sha256": digest}
                               for name, (year, digest) in SOURCES.items()]:
        raise ProductionImportError("The plan does not name the seven fingerprinted sources.")
    return plan


def _recompute(organization_id, actor_id, document, raw_directory):
    """Refuse unless sources, post-PCA state and live baseline reproduce this exact plan."""
    with _snapshot_transaction():
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        organization, _user = merge._authorize(organization_id, actor_id)
        fresh = build_document(organization, raw_directory, document["target"], document["generated_at"])
    if plan_hash(fresh) != plan_hash(document):
        differing = sorted(key for key in DOCUMENT_KEYS
                           if merge.fingerprint(fresh.get(key)) != merge.fingerprint(document.get(key)))
        raise StalePlanError("Stale plan, a new read-only plan and review are required. Differs: "
                             + ", ".join(differing))


# ---------------------------------------------------------------- backup

def backup_plan(organization_id, *, actor, document, approved_hash, raw_directory, output_dir):
    validate_plan(document, approved_hash)
    target = document["target"]
    production.require_target(target)
    actor_id = _require_scope(organization_id, actor)
    production.verify_target(target)
    _recompute(organization_id, actor_id, document, raw_directory)
    if connection.in_atomic_block:
        raise ProductionImportError("Backup must start outside an existing transaction.")
    check_backup_timeouts(connection)
    directory_guard = (nullcontext() if production._is_disposable_qa()
                       else pca_protected_artifacts.protected_directory(output_dir))
    with production.socket_guard(target), directory_guard, cast(
            AbstractContextManager[None], transaction.atomic(using="default")):
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            production.verify_target(target, in_snapshot=True)
            organization, _user = merge._authorize(organization_id, actor_id)
            state = read_state(organization)
            ids = [p["biological_measurement_id"] for p in document["plan"]["payloads"]]
            if (state["fingerprint"] != document["baseline"]["fingerprint"]
                    or state["post_pca"] != document["post_pca"]
                    or touched_fingerprint(organization, ids) != document["touched_biology_fingerprint"]):
                raise StalePlanError("Stale plan: no backup evidence can be issued for a different live snapshot.")
            cursor.execute("SELECT pg_export_snapshot()")
            snapshot = cursor.fetchone()[0]
        # The restricted import connection keeps exporting the approved snapshot.
        # Only the independently attested backup login reads the complete database.
        with production.backup_database(target, snapshot) as database:
            evidence = database_backup.create_evidence(
                database, target, approved_hash, output_dir, snapshot=snapshot,
                hostaddr=target["server_address"])
        if not production._is_disposable_qa():
            pca_protected_artifacts.validate_file(evidence["archive"]["path"])
        production.verify_target(target, in_snapshot=True)
        return evidence


# ---------------------------------------------------------------- apply

def _check_timeouts():
    from .historical_temperature_ui import UiOperationError, timeout_report

    try:
        return timeout_report()
    except UiOperationError as error:
        raise ProductionImportError(str(error)) from None


def _verify_readings(organization, payloads, results):
    """Stored readings and evidence equal the accepted payloads exactly."""
    wanted = {payload["biological_measurement_id"]: payload for payload in payloads}
    found = {row["biological_measurement_id"]: row for row in _readings_snapshot(organization.pk)
             if row["biological_measurement_id"] in wanted}
    if set(found) != set(wanted):
        raise ProductionImportError("receipt_reading_set_differs")
    for measurement_id, payload in wanted.items():
        row = found[measurement_id]
        if (row["association_status"] != HistoricalTemperatureReading.AssociationStatus.CONFIRMED
                or any(row[field] != payload[field] for field in ("iso_year", "iso_week", "temperature_c"))
                or sorted(row["evidences"], key=source_fingerprint)
                != sorted(payload["evidences"], key=source_fingerprint)):
            raise ProductionImportError("receipt_reading_differs")
    if results != sorted(({"biological_measurement_id": m, "reading_id": found[m]["id"]} for m in found),
                         key=lambda item: item["biological_measurement_id"]):
        raise ProductionImportError("receipt_results_differ")


def _receipt_for(plan):
    return HistoricalTemperatureImportReceipt.objects.filter(plan_fingerprint=plan["plan_fingerprint"]).first()


def _accepted_plan(document, approved_hash):
    plan = document["plan"]
    return {
        "schema": SCHEMA, "approved_plan_hash": approved_hash, "policy": plan["policy"],
        "plan_fingerprint": plan["plan_fingerprint"], "report_fingerprint": plan["report_fingerprint"],
        "baseline_fingerprint": plan["baseline_fingerprint"], "sources": document["sources"],
        "summary": plan["summary"], "payloads": plan["payloads"]}


def _check_receipt(receipt, organization, document, approved_hash, archive):
    plan = document["plan"]
    stored = receipt.accepted_plan
    if (receipt.organization_id != organization.pk or stored != _accepted_plan(document, approved_hash)
            or receipt.snapshot_sha256 != document["baseline"]["fingerprint"]
            or (archive is not None and bytes(receipt.source_archive) != archive)):
        raise ProductionImportError("A different or inconsistent receipt already exists for this plan.")
    _verify_readings(organization, plan["payloads"], receipt.results)
    _verify_preserved_baseline(organization, document)
    audit = AuditLog.objects.filter(
        organization=organization, object_type="historical_temperature_import", object_id=str(receipt.pk))
    expected_audit = {
        "operation": OPERATION, "receipt_id": receipt.pk, "approved_plan_hash": approved_hash,
        "plan_fingerprint": plan["plan_fingerprint"], "report_fingerprint": plan["report_fingerprint"],
        "baseline_fingerprint": plan["baseline_fingerprint"], "target": document["target"],
        "post_pca": document["post_pca"], "sources": document["sources"],
        "touched_biology_fingerprint": document["touched_biology_fingerprint"],
        "results_sha256": source_fingerprint(receipt.results), "actor_id": receipt.actor_id}
    entry = audit.first()
    if (audit.count() != 1 or entry.user_id != receipt.actor_id or entry.action != AuditLog.Action.IMPORT
            or any(entry.metadata.get(key) != value for key, value in expected_audit.items())):
        raise ProductionImportError("audit_missing_or_inconsistent")


def _verify_preserved_baseline(organization, document):
    ids = {payload["biological_measurement_id"] for payload in document["plan"]["payloads"]}
    state = read_state(organization)
    if state["pending"] or state["post_pca"] != document["post_pca"]:
        raise ProductionImportError("post_import_state_differs")
    if touched_fingerprint(organization, ids) != document["touched_biology_fingerprint"]:
        raise ProductionImportError("touched_biology_changed")
    snapshot = state["snapshot"]
    snapshot["historical_temperatures"] = [
        row for row in snapshot["historical_temperatures"] if row["biological_measurement_id"] not in ids]
    if source_fingerprint(snapshot) != document["baseline"]["fingerprint"]:
        raise ProductionImportError("baseline_changed_after_import")


def _insert(organization, payloads):
    results = []
    for chunk in _chunks(payloads):
        biology = {row.pk: row for row in BiologicalMeasurement.objects.filter(
            pk__in=[p["biological_measurement_id"] for p in chunk], box__organization=organization,
            duplicate_of__isnull=True, box__merged_into__isnull=True)}
        for payload in chunk:
            row = biology.get(payload["biological_measurement_id"])
            if row is None:
                raise ProductionImportError("accepted_biology_not_canonical_in_organization")
            reading = HistoricalTemperatureReading.objects.create(
                biological_measurement=row,
                **{key: payload[key] for key in ("iso_year", "iso_week", "temperature_c")})
            for evidence in payload["evidences"]:
                HistoricalTemperatureEvidence.objects.create(reading=reading, **evidence)
            results.append({"biological_measurement_id": row.pk, "reading_id": reading.pk})
    return sorted(results, key=lambda item: item["biological_measurement_id"])


def apply_plan(organization_id, *, actor, document, approved_hash, backup_evidence, review_ref,
               authorization, raw_directory):
    """Returns (receipt, replayed). Every write is in one transaction."""
    plan = validate_plan(document, approved_hash)
    target = document["target"]
    production.require_target(target)
    actor_id = _require_scope(organization_id, actor)
    if (authorization != authorization_text(approved_hash)
            or not isinstance(review_ref, str) or not review_ref.strip()):
        raise ProductionImportError("Apply requires exact authorization and an independent review reference.")
    if connection.in_atomic_block:
        raise ProductionImportError("Production apply must start outside an existing transaction.")
    # Invalid or missing evidence fails before any connection or ORM lookup.
    production._validate_backup(backup_evidence, target, approved_hash)
    production.verify_target(target)
    try:
        with _protected_sources(raw_directory):
            archive = _archive_sources(raw_directory)
    except ValueError as error:
        raise ProductionImportError(str(error)) from None
    with _snapshot_transaction():
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        production.verify_target(target, in_snapshot=True)
        organization, _user = merge._authorize(organization_id, actor_id)
        existing = _receipt_for(plan)
        if existing is not None:
            _check_receipt(existing, organization, document, approved_hash, archive)
            return existing, True
    _recompute(organization_id, actor_id, document, raw_directory)
    ids = [payload["biological_measurement_id"] for payload in plan["payloads"]]
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
            cursor.execute(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT}'")
            # Blocks competing importers and direct writers for the whole window.
            cursor.execute("LOCK TABLE " + ", ".join(LOCKED_TABLES) + " IN SHARE ROW EXCLUSIVE MODE")
        # Locks may have waited: recheck target and fresh evidence.
        production.verify_target(target)
        production._validate_backup(backup_evidence, target, approved_hash)
        organization, user = merge._authorize(organization_id, actor_id, lock=True)
        user, organization = authorize_measurement_import(user, organization)
        existing = _receipt_for(plan)
        if existing is not None:
            _check_receipt(existing, organization, document, approved_hash, archive)
            return existing, True
        timeouts = _check_timeouts()
        state = read_state(organization)
        if (state["pending"] or state["fingerprint"] != document["baseline"]["fingerprint"]
                or state["post_pca"] != document["post_pca"]
                or touched_fingerprint(organization, ids) != document["touched_biology_fingerprint"]):
            raise StalePlanError("Stale plan: the live baseline changed. Nothing was written.")
        results = _insert(organization, plan["payloads"])
        accepted_plan = _accepted_plan(document, approved_hash)
        receipt = HistoricalTemperatureImportReceipt.objects.create(
            organization=organization, actor_id=user.pk, plan_fingerprint=plan["plan_fingerprint"],
            snapshot_sha256=document["baseline"]["fingerprint"], accepted_plan=accepted_plan,
            source_archive=archive, results=results)
        AuditLog.objects.create(
            organization=organization, user=user, action=AuditLog.Action.IMPORT,
            object_type="historical_temperature_import", object_id=str(receipt.pk),
            description="Production partial historical temperature import",
            metadata={
                "operation": OPERATION, "receipt_id": receipt.pk, "approved_plan_hash": approved_hash,
                "plan_fingerprint": plan["plan_fingerprint"], "report_fingerprint": plan["report_fingerprint"],
                "baseline_fingerprint": plan["baseline_fingerprint"], "baseline_counts": document["baseline"]["counts"],
                "touched_biology_fingerprint": document["touched_biology_fingerprint"],
                "post_pca": document["post_pca"], "sources": document["sources"], "target": target,
                "backup": backup_evidence, "authorization": authorization, "review_ref": review_ref,
                "actor_id": user.pk, "timeouts_ms": timeouts, "policy": plan["policy"],
                "global_ready": plan["global_ready"], **plan["summary"],
                "review": document["review"],
                "results_sha256": source_fingerprint(results)})
        with connection.cursor() as cursor:
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
    return receipt, False


# ---------------------------------------------------------------- postcheck

def postcheck(organization_id, *, actor, document, approved_hash):
    """Read-only comparison of the committed result with the approved plan."""
    plan = validate_plan(document, approved_hash)
    target = document["target"]
    production.require_target(target)
    actor_id = _require_scope(organization_id, actor)
    production.verify_target(target)
    with _snapshot_transaction():
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        production.verify_target(target, in_snapshot=True)
        organization, _user = merge._authorize(organization_id, actor_id)
        receipt = _receipt_for(plan)
        if receipt is None:
            raise ProductionImportError("receipt_missing")
        stored = receipt.accepted_plan
        if (receipt.organization_id != organization.pk or stored.get("approved_plan_hash") != approved_hash
                or stored.get("payloads") != plan["payloads"] or stored.get("sources") != document["sources"]
                or receipt.snapshot_sha256 != document["baseline"]["fingerprint"]):
            raise ProductionImportError("receipt_differs_from_plan")
        with zipfile.ZipFile(io.BytesIO(bytes(receipt.source_archive))) as archive:
            held = {name: hashlib.sha256(archive.read(name)).hexdigest() for name in archive.namelist()}
        if held != {row["filename"]: row["sha256"] for row in document["sources"]}:
            raise ProductionImportError("receipt_source_archive_differs")
        _check_receipt(receipt, organization, document, approved_hash, None)
        ids = [payload["biological_measurement_id"] for payload in plan["payloads"]]
        if touched_fingerprint(organization, ids) != document["touched_biology_fingerprint"]:
            raise ProductionImportError("touched_biology_changed")
        counts = {"historical_temperatures": HistoricalTemperatureReading.objects.filter(
                      biological_measurement__box__organization=organization).count(),
                  "historical_evidences": HistoricalTemperatureEvidence.objects.filter(
                      reading__biological_measurement__box__organization=organization).count()}
        expected = {
            "historical_temperatures": document["baseline"]["counts"]["historical_temperatures"] + len(ids),
            "historical_evidences": document["baseline"]["counts"]["historical_evidences"]
            + plan["summary"]["accepted_evidences"]}
        if counts != expected:
            raise ProductionImportError("counts_differ: " + str({"expected": expected, "actual": counts}))
        audits = AuditLog.objects.filter(
            organization=organization, object_type="historical_temperature_import", object_id=str(receipt.pk))
        if audits.count() != 1 or audits.get().metadata.get("approved_plan_hash") != approved_hash:
            raise ProductionImportError("audit_missing_or_duplicated")
        if audits.get().user_id != receipt.actor_id:
            raise ProductionImportError("audit_actor_differs")
        if HistoricalTemperatureReading.objects.filter(
                biological_measurement__box__organization=organization).exclude(
                association_status=HistoricalTemperatureReading.AssociationStatus.CONFIRMED).exists():
            raise ProductionImportError("pending_association_present")
        verify_post_pca(organization)
        from .historical_temperature_ui import PROTECTED_TRIGGERS, check_guards
        check_guards()
        return {"receipt_id": receipt.pk, "actor_id": receipt.actor_id, "readings": len(ids),
                "evidences": plan["summary"]["accepted_evidences"], "counts": counts,
                "triggers_checked": len(PROTECTED_TRIGGERS), "approved_plan_hash": approved_hash}
