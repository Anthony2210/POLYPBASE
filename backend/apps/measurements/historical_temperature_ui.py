"""Staged, fail-closed checks for the one-time local-UI historical temperature import.

Importing this module never connects. Stage A verifies, B migrates, C imports and
D verifies again; each is a separate command run in its own process. Nothing here
widens the approved 40,291-association plan.
"""

import hashlib
import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from django.conf import settings
from django.db import connection, transaction
from django.db.migrations.executor import MigrationExecutor

from .historical_temperature_apply import (
    local_ui_target, _archive_sources, _current_snapshot, _validate_current,
)
from .historical_temperature_partial import (
    APPROVED_PLAN_FINGERPRINT, APPROVED_SNAPSHOT_SHA256, compare_approval, prepare_approved_partial,
)
from .historical_temperature_source import canonical_json
from .historical_temperature_ui_sql import (
    FORBIDDEN_PRIVILEGES, HISTORICAL_TABLES, WATCHED_TABLES, contract,
)

ORGANIZATION_ID = 1
EXPECTED_PENDING = [
    ("measurements", "0011_historical_temperature_storage"),
    ("measurements", "0012_historical_temperature_guards"),
    ("measurements", "0013_historical_temperature_receipt"),
    ("cultures", "0015_box_merged_into_box_previous_global_code_and_more"),
    ("measurements", "0014_measurement_canonical_evidence"),
]
EXPECTED_READINGS, EXPECTED_EVIDENCES = 40291, 40292
MIGRATE_CONFIRMATION = "MIGRATE_LOCAL_UI_FOR_HISTORICAL_TEMPERATURES"
PREFLIGHT_MAX_AGE = timedelta(hours=2)
# Milliseconds; 0 means disabled. A role/database default below these would kill the long transaction.
MIN_TIMEOUTS = {
    "statement_timeout": 120_000,
    "idle_in_transaction_session_timeout": 600_000,
    "transaction_timeout": 3_600_000,
}
PROTECTED_TRIGGERS = {
    "historical_reading_immutable", "historical_evidence_immutable",
    "historical_reading_no_truncate", "historical_evidence_no_truncate",
    "historical_reading_valid", "historical_evidence_valid", "historical_reading_nonempty",
    "historical_biology_guard", "historical_box_guard",
    "historical_receipt_immutable", "historical_receipt_no_truncate",
}


class UiOperationError(RuntimeError):
    pass


def require_stage(stage, *, switch=None):
    """Exact settings, stage and optional write switch, before any connection."""
    config = connection.settings_dict
    if (getattr(settings, "HISTORICAL_TEMPERATURE_UI_STAGE", None) != stage
            or settings.SETTINGS_MODULE != "config.historical_temperature_ui_local_settings"
            or connection.vendor != "postgresql"):
        raise UiOperationError("wrong_stage_or_target")
    host, port, database, _system_identifier, owner, runtime = _configured_target()
    if ((config.get("HOST"), str(config.get("PORT")), config.get("NAME")) != (host, port, database)
            or config.get("USER") not in {owner, runtime}
            or config.get("OPTIONS") != {"sslmode": "disable", "connect_timeout": 5}
            or settings.DATABASE_ROUTERS):
        raise UiOperationError("wrong_stage_or_target")
    if switch and not getattr(settings, switch, False):
        raise UiOperationError(f"write_switch_disabled:{switch}")


@contextmanager
def read_only():
    """One read-only transaction; a write attempt fails in PostgreSQL itself."""
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION READ ONLY")
        yield


def _all(sql, params=None):
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        return cursor.fetchall()


def _one(sql, params=None):
    return _all(sql, params)[0]


def _configured_target():
    try:
        return local_ui_target()
    except ValueError:
        raise UiOperationError("wrong_stage_or_target") from None


def server_identity():
    host, configured_port, database, system_identifier, _owner, _runtime = _configured_target()
    name, address, port, version, replication, schema = _one(
        "SELECT current_database(), host(inet_server_addr()), inet_server_port(), "
        "current_setting('server_version_num')::integer, current_setting('session_replication_role'), "
        "current_schema()")
    if ((name, address, str(port)) != (database, host, configured_port)
            or replication != "origin" or schema != "public" or not 180000 <= version < 190000):
        raise UiOperationError("unexpected_server_identity")
    if _one("SELECT system_identifier::text FROM pg_catalog.pg_control_system()") != (system_identifier,):
        raise UiOperationError("unexpected_server_identity")
    return {"database": name, "address": address, "port": port, "server_version_num": version,
            "system_identifier": system_identifier}


def role_report(*, migrator=False):
    row = _one("SELECT current_user::text, rolsuper, rolcreaterole, rolcreatedb, rolreplication, rolbypassrls "
               "FROM pg_roles WHERE rolname = current_user")
    owner_member = _one(
        "SELECT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relname = ANY(%s) AND pg_has_role(current_user, c.relowner, 'MEMBER'))",
        [list(WATCHED_TABLES)])[0]
    create = _one("SELECT has_schema_privilege(current_user, 'public', 'CREATE')")[0]
    _host, _port, _database, _system_identifier, owner, runtime = _configured_target()
    expected_role = owner if migrator else runtime
    if row[0] != expected_role or _one("SELECT session_user::text")[0] != expected_role:
        raise UiOperationError("unexpected_database_role")
    report = {"role": row[0], "superuser": row[1], "createrole": row[2], "createdb": row[3],
              "replication": row[4], "bypassrls": row[5], "owner_member": owner_member, "schema_create": create}
    if migrator:
        # One-time DDL needs the table owner (or an administrator); never the runtime role.
        if not owner_member and not row[1]:
            raise UiOperationError("migration_role_cannot_alter_tables")
    elif any(report[key] for key in ("superuser", "createrole", "createdb", "replication", "bypassrls",
                                     "owner_member", "schema_create")):
        raise UiOperationError("runtime_role_not_restricted")
    return report


def privilege_report(*, migrated):
    """Compare the role's EFFECTIVE privileges (grants, inheritance, PUBLIC) with the exact contract.

    Both missing and extra privileges, on every table of schema public, are a STOP.
    Needs no privilege of its own: the has_*_privilege functions only inspect catalogs.
    """
    wanted = contract(migrated)
    existing = {row[0] for row in _all(
        "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')")}
    if all(table in existing for table in HISTORICAL_TABLES) != migrated or (
            not migrated and any(table in existing for table in HISTORICAL_TABLES)):
        raise UiOperationError("migration_state_mismatch")
    difference = {}
    for privilege in ("SELECT", "INSERT", "MAINTAIN", *FORBIDDEN_PRIVILEGES):
        actual = {row[0] for row in _all(
            "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') AND has_table_privilege(current_user, c.oid, %s)",
            [privilege])}
        expected = wanted.get(privilege, set())
        if privilege == "SELECT":
            actual &= set(wanted["SELECT"])  # extra read access elsewhere is not a write risk
        if actual != expected:
            difference[f"table {privilege}"] = {"missing": sorted(expected - actual), "extra": sorted(actual - expected)}
    # Column level: UPDATE only on the one inert row-lock column per table, REFERENCES nowhere,
    # and INSERT columns are the full tables of the table-level contract.
    for privilege in ("UPDATE", "REFERENCES"):
        actual = {}
        for table, column in _all(
                "SELECT c.relname, a.attname FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid "
                "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public' "
                "AND c.relkind IN ('r', 'p') AND a.attnum > 0 AND NOT a.attisdropped "
                "AND has_column_privilege(current_user, c.oid, a.attnum, %s)", [privilege]):
            actual.setdefault(table, set()).add(column)
        expected = {t: {c} for t, c in wanted["UPDATE_COLUMNS"].items()} if privilege == "UPDATE" else {}
        if actual != expected:
            difference[f"column {privilege}"] = {
                "missing": sorted(f"{t}.{c}" for t, cs in expected.items() for c in cs - actual.get(t, set())),
                "extra": sorted(f"{t}.{c}" for t, cs in actual.items() for c in cs - expected.get(t, set())),
            }
    insert_elsewhere = sorted({table for table, in _all(
        "SELECT DISTINCT c.relname FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid "
        "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public' "
        "AND c.relkind IN ('r', 'p') AND a.attnum > 0 AND NOT a.attisdropped "
        "AND has_column_privilege(current_user, c.oid, a.attnum, 'INSERT')")} - wanted["INSERT"])
    if insert_elsewhere:
        difference["column INSERT"] = {"missing": [], "extra": insert_elsewhere}
    if difference:
        raise UiOperationError("privileges_differ:" + json.dumps(difference, sort_keys=True))
    return {"exact_contract": True, "update_columns": wanted["UPDATE_COLUMNS"],
            "maintain": sorted(wanted["MAINTAIN"])}


def migration_plan():
    executor = MigrationExecutor(connection)
    return [(m.app_label, m.name) for m, backwards in
            executor.migration_plan(executor.loader.graph.leaf_nodes()) if not backwards]


def check_migrations(*, migrated):
    pending = migration_plan()
    if pending != ([] if migrated else EXPECTED_PENDING):
        raise UiOperationError("unexpected_migration_state:" + json.dumps(pending))
    return pending


def check_guards():
    rows = _all("SELECT tgname, tgenabled FROM pg_trigger WHERE NOT tgisinternal AND tgname LIKE 'historical_%%'")
    if {row[0] for row in rows} != PROTECTED_TRIGGERS or any(row[1] != "O" for row in rows):
        raise UiOperationError("scientific_guards_missing_or_disabled")
    functions = _all(
        "SELECT p.proname, p.prosecdef, p.proconfig FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
        "WHERE n.nspname = 'public' AND p.proname LIKE 'historical_temperature_%%'")
    if not functions or any(row[1] or "search_path=pg_catalog, public, pg_temp" not in (row[2] or [])
                            for row in functions):
        raise UiOperationError("guard_functions_unsafe")


def timeout_report():
    values = {}
    for name in ("lock_timeout", *MIN_TIMEOUTS):
        rows = _all("SELECT setting::bigint FROM pg_settings WHERE name = %s", [name])
        values[name] = rows[0][0] if rows else None
        minimum = MIN_TIMEOUTS.get(name)
        if minimum and values[name] not in (None, 0) and values[name] < minimum:
            raise UiOperationError(f"timeout_too_short:{name}={values[name]}ms")
    return values


def session_report():
    others, idle = _one(
        "SELECT count(*), count(*) FILTER (WHERE state LIKE 'idle in transaction%%') "
        "FROM pg_stat_activity WHERE datname = current_database() AND pid <> pg_backend_pid()")
    return {"other_sessions": others, "idle_in_transaction": idle}


def table_counts(*, migrated):
    counts = {}
    for table in WATCHED_TABLES:
        if table in HISTORICAL_TABLES and not migrated:
            continue
        counts[table] = _one(f"SELECT count(*) FROM {connection.ops.quote_name(table)}")[0]
    return counts


def box_revision_digest():
    rows = _all("SELECT id, polyp_state_revision FROM cultures_box ORDER BY id")
    return hashlib.sha256(json.dumps(rows).encode()).hexdigest()


def administrators():
    return [row[0] for row in _all(
        "SELECT u.username FROM accounts_organizationmembership m JOIN auth_user u ON u.id = m.user_id "
        "WHERE m.organization_id = %s AND m.is_active AND u.is_active AND m.role = 'admin' ORDER BY 1",
        [ORGANIZATION_ID])]


def check_baseline(snapshot):
    if snapshot["identity"]["organization_id"] != ORGANIZATION_ID:
        raise UiOperationError("snapshot_institution_mismatch")
    current = _current_snapshot(ORGANIZATION_ID)
    for table in ("boxes", "biological_measurements"):
        if current[table] != sorted(snapshot[table], key=lambda row: row["id"]):
            raise UiOperationError(f"baseline_drift:{table}")
    return {"boxes": len(current["boxes"]), "biological_measurements": len(current["biological_measurements"])}


def verify(raw_directory, snapshot_bytes, *, migrated, runtime=True):
    """Stage A body; call inside read_only(). Returns the private preflight record."""
    snapshot, partial = prepare_approved_partial(raw_directory, snapshot_bytes)
    compare_approval(partial, APPROVED_PLAN_FINGERPRINT)
    record = {
        "stage": "A", "generated_at": datetime.now(timezone.utc).isoformat(), "migrated": migrated,
        "identity": server_identity(), "role": role_report(migrator=not runtime),
        "snapshot_sha256": APPROVED_SNAPSHOT_SHA256, "plan_fingerprint": partial["plan_fingerprint"],
        "summary": partial["summary"], "migrations_pending": check_migrations(migrated=migrated),
    }
    if runtime:
        record["privileges"] = privilege_report(migrated=migrated)
        record["timeouts"] = timeout_report()
    record["sessions"] = session_report()
    if record["sessions"]["idle_in_transaction"]:
        raise UiOperationError("idle_in_transaction_session_present")
    record["baseline"] = check_baseline(snapshot)
    if migrated:
        check_guards()
        if _one("SELECT count(*) FROM measurements_historicaltemperaturereading")[0] != 0:
            raise UiOperationError("unexpected_existing_temperature")
    record["counts"] = table_counts(migrated=migrated)
    record["box_revision_digest"] = box_revision_digest()
    record["administrators"] = administrators()
    return record


def write_private_json(directory, name, data):
    target = Path(directory).resolve()
    repository = Path(__file__).resolve().parents[3]
    if not target.is_dir() or target.is_relative_to(repository):
        raise UiOperationError("output_directory_must_exist_outside_repository")
    path = target / name
    if path.exists():
        raise UiOperationError(f"refusing_to_overwrite:{name}")
    path.write_text(canonical_json(data) + "\n", encoding="utf-8", newline="\n")
    return path


def load_preflight(path):
    record = json.loads(Path(path).read_text(encoding="utf-8"))
    age = datetime.now(timezone.utc) - datetime.fromisoformat(record["generated_at"])
    if (record.get("stage") != "A" or not record.get("migrated") or not timedelta(0) <= age <= PREFLIGHT_MAX_AGE
            or record["plan_fingerprint"] != APPROVED_PLAN_FINGERPRINT
            or record["snapshot_sha256"] != APPROVED_SNAPSHOT_SHA256):
        raise UiOperationError("preflight_missing_stale_or_not_post_migration")
    return record


BACKUP_MAX_AGE = timedelta(days=30)


def check_backup_record(path):
    """Refuse B and C without evidence of a restorable backup of the exact target.

    The record is written by the operator after pg_dump, pg_restore --list and a restore
    into a disposable instance; this checks the dump file against it, not the restore itself.
    """
    path = Path(path)
    record = json.loads(path.read_text(encoding="utf-8"))
    restore = record.get("restore_verified") or {}
    dump = Path(record.get("dump_path", ""))
    created = datetime.fromisoformat(record["created_at"])
    now = datetime.now(timezone.utc)
    repository = Path(__file__).resolve().parents[3]
    host, port, database, _system_identifier, _owner, _runtime = _configured_target()
    if (record.get("database") != database or record.get("host") != host
            or str(record.get("port")) != port or record.get("pg_dump_major") != 18
            or record.get("format") != "custom" or not record.get("pg_restore_list_entries")
            or restore.get("all_table_hashes_identical") is not True or not restore.get("tables_compared")
            or restore.get("instance_port") in (None, int(port))
            or created.tzinfo is None or not timedelta(0) <= now - created <= BACKUP_MAX_AGE
            or not dump.is_absolute() or dump.resolve().is_relative_to(repository)):
        raise UiOperationError("backup_record_invalid_or_stale")
    if not dump.is_file() or dump.stat().st_size != record.get("dump_bytes") or not dump.stat().st_size:
        raise UiOperationError("backup_dump_missing_or_size_differs")
    digest = hashlib.sha256()
    with dump.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    if digest.hexdigest() != record.get("dump_sha256"):
        raise UiOperationError("backup_dump_sha256_differs")
    return {"dump_sha256": record["dump_sha256"], "created_at": record["created_at"]}


def compare_preflight(before, now):
    """Nothing may have changed between the post-migration preflight and the import."""
    for key in ("counts", "box_revision_digest", "baseline", "plan_fingerprint"):
        if before[key] != now[key]:
            raise UiOperationError(f"state_changed_since_preflight:{key}")


def postcheck(raw_directory, snapshot_bytes, before):
    """Stage D body; call inside read_only(). Exact comparison with the preflight."""
    from apps.audit.models import AuditLog

    from .models import (
        HistoricalTemperatureEvidence, HistoricalTemperatureImportReceipt, HistoricalTemperatureReading,
    )

    snapshot, partial = prepare_approved_partial(raw_directory, snapshot_bytes)
    identity = server_identity()
    role_report()
    check_migrations(migrated=True)
    check_guards()
    receipt = HistoricalTemperatureImportReceipt.objects.get(plan_fingerprint=APPROVED_PLAN_FINGERPRINT)
    if bytes(receipt.source_archive) != _archive_sources(raw_directory):
        raise UiOperationError("receipt_source_archive_differs")
    _validate_current(snapshot, partial, receipt)  # biology, boxes, readings, evidence and status
    expected = dict(before["counts"])
    for table, added in (("measurements_historicaltemperaturereading", EXPECTED_READINGS),
                         ("measurements_historicaltemperatureevidence", EXPECTED_EVIDENCES),
                         ("measurements_historicaltemperatureimportreceipt", 1), ("audit_auditlog", 1)):
        expected[table] += added
    counts = table_counts(migrated=True)
    if counts != expected:
        raise UiOperationError("counts_differ:" + json.dumps({"expected": expected, "actual": counts}))
    if box_revision_digest() != before["box_revision_digest"]:
        raise UiOperationError("box_revisions_changed")
    audits = AuditLog.objects.filter(object_type="historical_temperature_import", object_id=str(receipt.pk))
    if audits.count() != 1 or audits.get().metadata.get("plan_fingerprint") != APPROVED_PLAN_FINGERPRINT:
        raise UiOperationError("import_audit_missing_or_duplicated")
    if (HistoricalTemperatureReading.objects.exclude(association_status="confirmed").exists()
            or HistoricalTemperatureEvidence.objects.count() != EXPECTED_EVIDENCES):
        raise UiOperationError("unexpected_status_or_evidence")
    return {"stage": "D", "identity": identity, "receipt_id": receipt.pk, "counts": counts,
            "readings": EXPECTED_READINGS, "evidences": EXPECTED_EVIDENCES,
            "biology_and_boxes": "identical to sealed snapshot"}
