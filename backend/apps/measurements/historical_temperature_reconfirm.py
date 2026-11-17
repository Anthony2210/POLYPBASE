"""Exceptional org-1 maintenance; no persistent reconfirmation capability.

The schema owner is required for apply. The original immutable function is
restored before commit; PostgreSQL rolls back the replacement on any failure.
All protected tables remain locked until the restoration and audit commit.
"""

import hashlib
import json
from importlib import import_module

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import connection, transaction

from apps.audit.models import AuditLog
from .models import HistoricalTemperatureReading
from .temperature_series import temperature_decimal

ATTESTATION = "I attest owner approval for exceptional batch reconfirmation of evidence-matching historical temperatures in organization 1 without individual review"
OBJECT_TYPE = "historical_temperature_reconfirmation"
TABLES = (
    "organizations_organization", "auth_user", "cultures_box",
    "measurements_biologicalmeasurement", "measurements_historicaltemperaturereading",
    "measurements_historicaltemperatureevidence", "measurements_historicaltemperatureimportreceipt",
    "audit_auditlog",
)
FUNCTION = "public.historical_temperature_reading_immutable()"
_ORIGINAL_SQL = import_module("apps.measurements.migrations.0012_historical_temperature_guards").SQL
_start = _ORIGINAL_SQL.index("CREATE FUNCTION historical_temperature_reading_immutable()")
_ORIGINAL_BODY = _ORIGINAL_SQL[_start:].split("AS $$", 1)[1].split("$$;", 1)[0]


def _digest(value):
    return hashlib.sha256(json.dumps(value, cls=DjangoJSONEncoder, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _function(cursor):
    cursor.execute("SHOW session_replication_role")
    if cursor.fetchone()[0] != "origin":
        raise ValueError("Historical maintenance requires session_replication_role=origin.")
    cursor.execute("SELECT pg_get_functiondef(p.oid), p.prosrc, p.prosecdef, p.proconfig "
                   "FROM pg_proc p WHERE p.oid = %s::regprocedure", [FUNCTION])
    definition, body, security_definer, config = cursor.fetchone()
    if (body != _ORIGINAL_BODY or security_definer
            or config != ["search_path=pg_catalog, public, pg_temp"]):
        raise ValueError("Unexpected historical immutable function; independent review required.")
    cursor.execute("SELECT tgfoid = %s::regprocedure, tgtype, tgenabled, tgqual IS NULL, tgattr::text "
                   "FROM pg_trigger WHERE tgrelid = 'public.measurements_historicaltemperaturereading'::regclass "
                   "AND tgname = 'historical_reading_immutable'", [FUNCTION])
    if cursor.fetchone() not in ((True, 27, "O", True, ""), (True, 27, "A", True, "")):
        raise ValueError("Missing or altered immutable trigger.")
    return definition


def _maintenance_identity(cursor, expected=None):
    """Attest real authority, never activate a privileged role for the caller."""
    cursor.execute("SELECT session_user::text, current_user::text, current_setting('role'), "
                   "current_setting('session_replication_role'), "
                   "current_setting('server_version_num')::int, pg_backend_pid(), "
                   "pg_current_xact_id()::text, "
                   "(SELECT NOT rolsuper AND NOT rolbypassrls AND NOT rolreplication AND rolcanlogin "
                   " FROM pg_roles WHERE rolname = session_user), "
                   "(SELECT NOT rolsuper AND NOT rolbypassrls AND NOT rolreplication AND NOT rolcanlogin "
                   " FROM pg_roles WHERE rolname = current_user), "
                   "pg_has_role(session_user, current_user, 'SET')")
    session, current, role, replication, version, pid, xid, login_safe, owner_safe, can_set = cursor.fetchone()
    if ((session, current, role, replication) !=
            ('box1902_migrator', 'box1902_owner', 'box1902_owner', 'origin')
            or not 180000 <= version < 190000 or not all((login_safe, owner_safe, can_set))):
        raise ValueError("Apply requires PostgreSQL 18 and the non-superuser migrator session with effective owner role.")
    identity = (connection.connection, pid, xid)
    if expected is not None and (identity[0] is not expected[0] or identity[1:] != expected[1:]):
        raise ValueError("Maintenance connection, backend or transaction changed; refusing apply.")
    return identity


def _maintenance_privileges(cursor):
    # Ownership alone does not establish DML: an owner can revoke its own ACL.
    # Require the existing owner topology and its ordinary SELECT/UPDATE ACLs;
    # UPDATE also authorizes every requested ACCESS EXCLUSIVE table lock.
    cursor.execute("SELECT nspowner = current_user::regrole, "
                   "has_schema_privilege(oid, 'USAGE'), has_schema_privilege(oid, 'CREATE') "
                   "FROM pg_namespace WHERE nspname = 'public'")
    if cursor.fetchone() != (True, True, True):
        raise ValueError("Unexpected public schema ownership or privileges.")
    for table in TABLES:
        cursor.execute("SELECT relowner = current_user::regrole, relkind = 'r', "
                       "NOT relrowsecurity AND NOT relforcerowsecurity, "
                       "has_table_privilege(oid, 'SELECT'), has_table_privilege(oid, 'UPDATE') "
                       "FROM pg_class WHERE oid = %s::regclass", ['public.' + table])
        if cursor.fetchone() != (True, True, True, True, True):
            raise ValueError(f"Unexpected maintenance ownership, RLS or SELECT/UPDATE privileges: {table}.")
    cursor.execute("SELECT proowner = current_user::regrole, "
                   "has_function_privilege(oid, 'EXECUTE') FROM pg_proc WHERE oid = %s::regprocedure",
                   [FUNCTION])
    if cursor.fetchone() != (True, True):
        raise ValueError("Maintenance must own and execute the original immutable function.")
    cursor.execute("SELECT has_table_privilege('public.audit_auditlog', 'INSERT'), "
                   "has_language_privilege('plpgsql', 'USAGE'), "
                   "pg_get_serial_sequence('public.audit_auditlog', 'id')")
    insert, language, sequence = cursor.fetchone()
    if not insert or not language or sequence is None:
        raise ValueError("Missing audit INSERT, trigger language or audit sequence.")
    cursor.execute("SELECT relowner = current_user::regrole, has_sequence_privilege(oid, 'USAGE') "
                   "FROM pg_class WHERE oid = %s::regclass", [sequence])
    if cursor.fetchone() != (True, True):
        raise ValueError("Unexpected audit sequence ownership or USAGE privilege.")


def _snapshot(definition):
    """Fingerprint pending org-1 readings and their linked records, not receipts.

    Existing import receipts are append-only archives, not eligibility inputs.
    Their JSON results do not define a relational reading membership contract.
    """
    readings = list(HistoricalTemperatureReading.objects.filter(
        biological_measurement__box__organization_id=1,
        association_status="pending_review",
    ).select_related("biological_measurement__box").prefetch_related("evidences").order_by("pk"))
    blockers, snapshots, eligible = [], [], []
    for reading in readings:
        biology = reading.biological_measurement
        evidences = sorted(reading.evidences.all(), key=lambda item: item.pk)
        snapshots.append({
            "reading": {f.attname: getattr(reading, f.attname) for f in reading._meta.fields},
            "biology": {f.attname: getattr(biology, f.attname) for f in biology._meta.fields},
            "box": {f.attname: getattr(biology.box, f.attname) for f in biology.box._meta.fields},
            "evidence": [{f.attname: getattr(item, f.attname) for f in item._meta.fields} for item in evidences],
        })
        errors = []
        # Validate hypothetical confirmation without persisting or rewriting evidence.
        reading.association_status = "confirmed"
        try:
            reading.clean()
        except (ValidationError, ValueError, TypeError) as exc:
            errors.append(str(exc))
        if not evidences:
            errors.append("Missing retained source evidence.")
        for item in evidences:
            item.reading = reading
            try:
                item.full_clean(validate_unique=False, validate_constraints=False)
                if not item.source_file.strip() or not item.sheet.strip():
                    raise ValueError("Missing source file or sheet.")
                if temperature_decimal(item.week_value) != reading.iso_week:
                    raise ValueError("Source week value mismatch.")
            except (ValidationError, ValueError, TypeError) as exc:
                errors.append(f"Evidence {item.pk}: {exc}")
        if errors:
            blockers.append({"reading_id": reading.pk, "errors": errors})
        else:
            eligible.append(reading.pk)
    return {
        "organization_id": 1, "pending_count": len(readings), "eligible_count": len(eligible),
        "blockers": blockers, "reading_ids": eligible,
        "fingerprint": _digest({"version": 1, "organization_id": 1,
                                "function": definition, "snapshot": snapshots}),
    }


def run(*, organization_id=1, actor_id=None, reason="", attestation="", apply=False,
        expected_count=None, expected_fingerprint=None):
    if type(organization_id) is not int or organization_id != 1:
        raise ValueError("This exceptional workflow is restricted to organization_id=1.")
    if connection.vendor != "postgresql":
        raise ValueError("PostgreSQL is required; SQLite cannot prove historical integrity.")
    if connection.in_atomic_block or not connection.get_autocommit():
        raise ValueError("Run in a standalone transaction, not inside another transaction.")
    if apply and (not isinstance(reason, str) or not reason.strip() or actor_id is None
                  or attestation != ATTESTATION or type(expected_count) is not int
                  or expected_count <= 0 or not expected_fingerprint):
        raise ValueError("Apply requires operator, reason, exact owner attestation, positive expected count and fingerprint.")
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
        cursor.execute("SET LOCAL lock_timeout = '10s'")
        cursor.execute("SET LOCAL statement_timeout = '120s'")
        cursor.execute("SET LOCAL search_path = pg_catalog, public, pg_temp")
        # SHARE gives a coherent dry-run without mutation. Apply takes exclusive
        # locks up front: no lock upgrade deadlock or unseen concurrent inserts.
        identity = _maintenance_identity(cursor) if apply else None
        if apply:
            _maintenance_privileges(cursor)
        mode = "ACCESS EXCLUSIVE" if apply else "SHARE"
        cursor.execute("LOCK TABLE " + ", ".join("public." + table for table in TABLES) + f" IN {mode} MODE")
        definition = _function(cursor)
        report = _snapshot(definition)
        if not apply:
            return report
        actor = get_user_model().objects.filter(pk=actor_id, is_active=True, is_superuser=True).first()
        if actor is None:
            raise ValueError("An active Django superuser operator is required.")
        if (report["blockers"] or report["pending_count"] != expected_count
                or report["eligible_count"] != expected_count
                or report["fingerprint"] != expected_fingerprint):
            raise ValueError("Blocking anomalies or stale expected count/fingerprint; rerun dry-run. "
                             + json.dumps(report))
        ids = report["reading_ids"]
        # Only validated numeric primary keys enter DDL, never operator text.
        id_list = ",".join(str(int(pk)) for pk in ids)
        exception_body = f"""
        IF TG_OP = 'UPDATE' AND OLD.association_status = 'pending_review'
           AND NEW.association_status = 'confirmed' AND OLD.id IN ({id_list})
           AND (to_jsonb(NEW) - 'association_status') = (to_jsonb(OLD) - 'association_status')
           AND EXISTS (SELECT 1 FROM public.measurements_biologicalmeasurement b
                       JOIN public.cultures_box box ON box.id = b.box_id
                       WHERE b.id = OLD.biological_measurement_id AND box.organization_id = 1) THEN
            RETURN NEW;
        END IF;
"""
        temporary_body = _ORIGINAL_BODY.replace("BEGIN", "BEGIN" + exception_body, 1)
        _maintenance_identity(cursor, identity)
        cursor.execute("CREATE OR REPLACE FUNCTION " + FUNCTION + " RETURNS trigger LANGUAGE plpgsql "
                       "SET search_path = pg_catalog, public, pg_temp AS $reconfirm$"
                       + temporary_body + "$reconfirm$")
        cursor.execute("UPDATE public.measurements_historicaltemperaturereading "
                       "SET association_status = 'confirmed' WHERE id = ANY(%s) "
                       "AND association_status = 'pending_review'", [ids])
        if cursor.rowcount != expected_count:
            raise ValueError("Unexpected update count; rolling back entire batch.")
        cursor.execute(definition)
        _maintenance_identity(cursor, identity)
        if _function(cursor) != definition:
            raise ValueError("Original protection restoration failed.")
        event = AuditLog.objects.create(
            organization_id=1, user=actor, action=AuditLog.Action.UPDATE,
            object_type=OBJECT_TYPE, object_id=report["fingerprint"],
            description="Exceptional evidence-matching historical temperature batch reconfirmation",
            metadata={"report": report, "reason": reason.strip(), "operator_id": actor.pk,
                      "operator_username": actor.get_username(), "owner_attestation": attestation,
                      "previous_status": "pending_review", "new_status": "confirmed"},
        )
        _maintenance_identity(cursor, identity)
        return {**report, "applied_count": len(ids), "audit_id": event.pk}
