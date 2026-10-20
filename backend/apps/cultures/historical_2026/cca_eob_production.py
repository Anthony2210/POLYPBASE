"""One-off CCA-EOB production boundary; never used by QA commands or deployment."""

import json
import os
import re
from decimal import Decimal
from contextlib import nullcontext
from pathlib import Path
from importlib import import_module

from django.conf import settings

from django.db import connection, transaction
from django.db.models import DecimalField

from config import cca_eob_database_role as role_contract
from config import cca_eob_socket as socket_artifacts
from config import database_backup, pca_protected_artifacts as artifacts
from apps.cultures.models import Box, BoxLocation, BoxMovement
from apps.measurements.models import BiologicalMeasurement
from . import cca_eob_correction as correction

TARGET_PATH = "/etc/polypbase/cca-eob-production-target.json"
RELEASE_PATH = "/etc/polypbase/cca-eob-production-authorization.json"
SCHEMA = "cca_eob_production_plan_v2"
BOXES = {1835: "CCA-EOB-2.004", 1836: "CCA-EOB-2.005", 1837: "CCA-EOB-2.006"}
TARGET_SCHEMA = "cca_eob_target_v2"
TARGET_KEYS = {"schema", "machine_id", "configured_host", "configured_port", "database", "role",
               "server_address", "server_port", "database_oid", "role_oid",
               "postmaster_started_at", "server_version_num", "socket_uid", "socket_gid"}
# Locks are held while the release archive is re-verified (an external tool bounded by
# database_backup.TIMEOUT_SECONDS), so the whole transaction gets that plus a margin.
# Exceeding it aborts and rolls back; it never terminates another session.
APPLY_TRANSACTION_TIMEOUT_SECONDS = database_backup.TIMEOUT_SECONDS + 300
IDENTITY_SQL = """
SELECT current_database(), d.oid::bigint, session_user::text, current_user::text,
       r.oid::bigint, host(inet_server_addr()), inet_server_port(),
       to_char(pg_postmaster_start_time() AT TIME ZONE 'UTC',
               'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'), current_setting('server_version_num')::integer,
       current_setting('session_replication_role'), current_schemas(false), pg_is_in_recovery()
FROM pg_database d, pg_roles r
WHERE d.datname = current_database() AND r.rolname = current_user
"""


def machine_id():
    return Path("/etc/machine-id").read_text(encoding="ascii").strip()


def runtime_identity():
    import pwd
    return pwd.getpwuid(os.getuid()).pw_name


def _qa():
    """No configurable production bypass: only the leased synthetic PG18 harness."""
    if settings.SETTINGS_MODULE == "config.historical_temperature_pgqa_settings":
        from config import historical_temperature_pgqa as guard
        guard.assert_config(connection.settings_dict)
        guard.require_run_lease()
        return True
    return False


def _root_document(path):
    artifacts.assert_unprivileged_runtime()
    return artifacts._read_json(path, {0}, False)


def read_target(path):
    if os.fspath(path) != TARGET_PATH:
        raise ValueError("Only the pinned root-owned CCA-EOB target is accepted.")
    return _root_document(TARGET_PATH)


def require_target(target):
    if (type(target) is not dict or set(target) != TARGET_KEYS
            or target["schema"] != TARGET_SCHEMA
            or not isinstance(target["machine_id"], str)
            or not re.fullmatch(r"[0-9a-f]{32}", target["machine_id"])
            or any(type(target[k]) is not int or target[k] <= 0 for k in
                   ("database_oid", "role_oid", "server_version_num"))
            or not isinstance(target["postmaster_started_at"], str)
            or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z", target["postmaster_started_at"])):
        raise ValueError("A complete independent CCA-EOB target attestation is required.")
    qa = _qa()
    # Root attests the PostgreSQL socket directory owner. The disposable QA backend is
    # TCP-only, so there the fields must be explicitly absent instead of ignored.
    ids = (target["socket_uid"], target["socket_gid"])
    if (any(value is not None for value in ids) if qa
            else any(type(value) is not int or value <= 0 for value in ids)):
        raise ValueError("A root-attested PostgreSQL socket directory owner (uid and gid) is required.")
    config = connection.settings_dict
    if not qa:
        if any(key.upper().startswith("PG") for key in os.environ):
            raise ValueError("Remove inherited PG* libpq variables.")
        if (target != _root_document(TARGET_PATH) or machine_id() != target["machine_id"]
                or settings.SETTINGS_MODULE != role_contract.SETTINGS or settings.DEBUG
                or config.get("ENGINE") != "django.db.backends.postgresql"
                or config.get("OPTIONS") != {"sslmode": "disable"}
                or (target["configured_host"], target["configured_port"], target["database"], target["role"])
                != ("/var/run/postgresql", "5433", "polypbase", role_contract.ROLE)
                                or runtime_identity() != role_contract.ROLE
                or target["server_address"] is not None or target["server_port"] is not None
                or not 180000 <= target["server_version_num"] < 190000):
            raise ValueError("Production requires the root-attested local PostgreSQL 18 peer socket target.")
    if (settings.DATABASE_ROUTERS or set(settings.DATABASES) != {"default"}
            or (config.get("HOST"), str(config.get("PORT")), config.get("NAME"), config.get("USER"))
            != (target["configured_host"], target["configured_port"], target["database"], target["role"])):
        raise ValueError("Configured connection differs from the attested target.")
    return qa


def socket_guard(target):
    """Bind the full sensitive window to the attested Debian PostgreSQL socket.

    The protection (owner, group, no runtime write access, ancestors and replacement
    detection before and after the guarded work) is shared with PCA. It only
    understands the pinned Debian directory, so any other host fails closed there.
    """
    if _qa():
        return nullcontext()
    return socket_artifacts.protected_postgresql_socket(
        target["configured_host"], target["configured_port"], target["socket_uid"], target["socket_gid"])


def verify_target(target):
    # A malformed or unattested target is refused before any socket access; the
    # window then repeats the attestation check from the root-owned source.
    require_target(target)
    with socket_guard(target):
        _verify_target(target)


def _verify_target(target):
    qa = require_target(target)
    if qa and not connection.in_atomic_block:
        from config import historical_temperature_pgqa as guard
        guard.assert_disposable(connection)
    # The QA backend verifies cluster identity on connection creation. Its outer
    # preflight requires READ COMMITTED; our owned read-only snapshot is stricter.
    connection.ensure_connection()
    info = connection.connection.info
    if (info.host, str(info.port), info.dbname, info.user) != (
            target["configured_host"], target["configured_port"], target["database"], target["role"]):
        raise ValueError("Effective PostgreSQL connection differs from the approved target.")
    if not qa and info.hostaddr not in (None, ""):
        raise ValueError("Production must use the local peer socket, never TCP or a tunnel.")
    with connection.cursor() as cursor:
        cursor.execute(IDENTITY_SQL)
        expected = (target["database"], target["database_oid"], target["role"], target["role"],
                    target["role_oid"], target["server_address"], target["server_port"],
                    target["postmaster_started_at"], target["server_version_num"], "origin", ["public"], False)
        if tuple(cursor.fetchone()) != expected:
            raise ValueError("Live PostgreSQL identity or session differs from the attestation.")
        cursor.execute("""SELECT rolname FROM pg_roles WHERE pg_has_role(current_user, oid, 'MEMBER')
            AND (rolsuper OR rolcreaterole OR rolcreatedb OR rolreplication OR rolbypassrls
                 OR (left(rolname, 3) = 'pg_' AND rolname <> 'pg_database_owner'))""")
        if cursor.fetchall():
            raise ValueError("Unsafe PostgreSQL privileges; do not escalate the execution role.")
        cursor.execute("""
            SELECT 'database/schema authority' WHERE
                has_database_privilege(current_user, current_database(), 'CREATE')
                OR has_schema_privilege(current_user, 'public', 'CREATE')
                OR EXISTS (SELECT 1 FROM pg_roles WHERE pg_has_role(current_user, oid, 'SET')
                    AND (has_database_privilege(oid, current_database(), 'CREATE')
                         OR has_schema_privilege(oid, 'public', 'CREATE')))
                OR EXISTS (SELECT 1 FROM pg_database WHERE datname=current_database()
                    AND (pg_has_role(current_user, datdba, 'MEMBER') OR pg_has_role(current_user, datdba, 'SET')))
                OR EXISTS (SELECT 1 FROM pg_namespace WHERE nspname='public'
                    AND (pg_has_role(current_user, nspowner, 'MEMBER') OR pg_has_role(current_user, nspowner, 'SET')))
            UNION ALL
            SELECT 'relation ownership' FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname='public' AND
                  (pg_has_role(current_user, c.relowner, 'MEMBER') OR pg_has_role(current_user, c.relowner, 'SET'))
            UNION ALL
            SELECT 'function ownership' FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                WHERE n.nspname='public' AND
                  (pg_has_role(current_user, p.proowner, 'MEMBER') OR pg_has_role(current_user, p.proowner, 'SET'))
        """)
        if cursor.fetchall():
            raise ValueError("Runtime must not own or assume ownership of the database, schema, relations or functions.")
        cursor.execute(role_contract.authority_sql("(SELECT oid FROM pg_roles WHERE rolname = current_user)"))
        if cursor.fetchall():
            raise ValueError("Runtime has role-level settings, extra schema/function/large-object authority.")
        runtime_role = role_contract.ROLE
        if qa:
            from config import historical_temperature_pgqa as guard
            runtime_role = guard.RUNTIME
        cursor.execute("DO $runtime$ " + role_contract.runtime_capability_sql(runtime_role) + " $runtime$;")


def _scope(organization_id, actor):
    if type(organization_id) is not int or organization_id != 1 or actor is None or not actor.pk:
        raise ValueError("Only organization 1 and an explicit admin actor are accepted.")


def _read_scope(actor):
    """Only the capability can inspect business tables; arguments confer no write authority."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT public.cca_eob_read_v1(%s)", [actor.pk])
        payload = cursor.fetchone()[0]
    # Django's PostgreSQL JSON loader intentionally returns strings for raw SQL.
    return json.loads(payload) if isinstance(payload, str) else payload


def _authorize(actor):
    _read_scope(actor)


def _model(model, row):
    fields, values = [], []
    for field in model._meta.concrete_fields:
        value = field.to_python(row[field.attname])
        if value is not None and isinstance(field, DecimalField):
            value = value.quantize(Decimal(10) ** -field.decimal_places)
        fields.append(field.attname)
        values.append(value)
    return model.from_db("default", fields, values)


def _planner_data(payload):
    return {
        "organization": True,
        "boxes": [_model(Box, row) for row in payload["boxes"]],
        "measurements": [_model(BiologicalMeasurement, row) for row in payload["measurements"]],
        "histories": payload["histories"],
        "attribution_user_ids": set(payload["attribution_user_ids"]),
        "location_context": {
            "policy": "target_box_context_preserve_location_history_v2",
            "locations": [correction.snapshot(_model(BoxLocation, row)) for row in payload["locations"]],
            "movements": [correction.snapshot(_model(BoxMovement, row)) for row in payload["movements"]],
            "external_movement_links": payload["external_movement_links"],
            "zones": payload["zones"],
        },
    }


def _schema_state():
    """Read deployed guards, including their definitions, into the reviewed hash."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT app, name FROM django_migrations WHERE app IN ('cultures', 'measurements') ORDER BY app, name")
        migrations = cursor.fetchall()
        if not any(a == "cultures" and n.startswith("0015_") for a, n in migrations) or not any(
                a == "measurements" and n.startswith("0014_") for a, n in migrations):
            raise ValueError("Required cultures.0015 and measurements.0014 are not deployed.")
        cursor.execute("""SELECT t.tgname, t.tgenabled, c.relname, pg_get_triggerdef(t.oid),
                       pg_get_functiondef(t.tgfoid)
            FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND NOT t.tgisinternal AND t.tgname LIKE 'historical_%'
            ORDER BY t.tgname""")
        triggers = cursor.fetchall()
        required = {"historical_biology_guard", "historical_box_guard", "historical_reading_valid",
                    "historical_reading_immutable", "historical_evidence_immutable", "historical_receipt_immutable",
                    "historical_reading_nonempty", "historical_evidence_valid", "historical_reading_no_truncate",
                    "historical_evidence_no_truncate", "historical_receipt_no_truncate"}
        if {row[0] for row in triggers} != required or any(row[1] != "O" for row in triggers):
            raise ValueError("Historical temperature guards are missing, changed or disabled.")
        expected_bodies = {}
        for module in ("0012_historical_temperature_guards", "0014_measurement_canonical_evidence"):
            sql = import_module(f"apps.measurements.migrations.{module}").SQL
            for name, body in re.findall(
                    r"CREATE (?:OR REPLACE )?FUNCTION (historical_temperature_\w+)\(.*?AS \$\$(.*?)\$\$;",
                    sql, re.DOTALL):
                expected_bodies[name] = body
        cursor.execute("""SELECT proname, prosrc, prosecdef, proconfig FROM pg_proc p
            JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname='public' AND proname LIKE 'historical_temperature_%' ORDER BY proname""")
        functions = cursor.fetchall()
        if ({r[0] for r in functions} != set(expected_bodies)
                or any(r[1] != expected_bodies[r[0]] or r[2]
                       or r[3] != ['search_path=pg_catalog, public, pg_temp'] for r in functions)):
            raise ValueError("Historical guard function definitions differ from the reviewed migrations.")
        cursor.execute("""SELECT t.tgname, c.relname, p.proname, t.tgtype, t.tgdeferrable, t.tginitdeferred,
                   t.tgattr::text, t.tgqual IS NULL, pn.nspname, p.pronargs,
                   t.tgnargs, octet_length(t.tgargs), t.tgoldtable, t.tgnewtable,
                   t.tgconstraint <> 0
            FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_proc p ON p.oid=t.tgfoid
            JOIN pg_namespace pn ON pn.oid=p.pronamespace
            WHERE n.nspname='public' AND NOT t.tgisinternal AND t.tgname LIKE 'historical_%'""")
        bindings = cursor.fetchall()
        expected_bindings = {
            'historical_biology_guard': ('measurements_biologicalmeasurement', 'historical_temperature_biology_guard', 19),
            'historical_box_guard': ('cultures_box', 'historical_temperature_box_guard', 19),
            'historical_reading_valid': ('measurements_historicaltemperaturereading', 'historical_temperature_reading_valid', 7),
            'historical_evidence_valid': ('measurements_historicaltemperatureevidence', 'historical_temperature_evidence_valid', 7),
            'historical_reading_nonempty': ('measurements_historicaltemperaturereading', 'historical_temperature_nonempty', 5),
        }
        for (name, table, function, kind, deferred, initially, columns, unconditional,
             namespace, arity, nargs, argbytes, oldtable, newtable, constraint) in bindings:
            expected = expected_bindings.get(name)
            if expected is None:
                table_suffix = ('historicaltemperaturereading' if name.startswith('historical_reading_')
                                else 'historicaltemperatureevidence' if name.startswith('historical_evidence_')
                                else 'historicaltemperatureimportreceipt')
                function_name = ('historical_temperature_reading_immutable' if name == 'historical_reading_immutable'
                                 else 'historical_temperature_immutable')
                expected = ('measurements_' + table_suffix, function_name, 34 if name.endswith('no_truncate') else 27)
            nonempty = name == 'historical_reading_nonempty'
            if ((table, function, kind) != expected or (deferred, initially) != (nonempty, nonempty)
                    or (columns, unconditional, namespace, arity, nargs, argbytes, oldtable, newtable, constraint)
                    != ('', True, 'public', 0, 0, 0, None, None, nonempty)):
                raise ValueError("Historical trigger binding differs from the reviewed contract.")
        helper_role = role_contract.ROLE
        if _qa():
            from config import historical_temperature_pgqa as guard
            helper_role = guard.RUNTIME
        helpers = []
        for signature, body, result, names in role_contract.helper_specs(helper_role):
            cursor.execute("""SELECT p.prosrc, p.prosecdef, p.proconfig,
                    p.proowner = c.relowner, l.lanname, p.provolatile, p.prorettype = to_regtype(%s),
                    p.prokind, p.pronargs, p.proargtypes::text, p.proargnames,
                    p.proparallel, p.proisstrict, p.proleakproof, p.proretset, p.pronargdefaults,
                    p.prosupport::int, p.provariadic::int,
                    NOT EXISTS (SELECT 1 FROM aclexplode(coalesce(p.proacl, acldefault('f',p.proowner))) a
                        WHERE a.privilege_type <> 'EXECUTE' OR a.is_grantable
                          OR a.grantor <> p.proowner OR a.grantee NOT IN
                            (p.proowner,(SELECT oid FROM pg_roles WHERE rolname = %s))),
                    has_function_privilege(%s,p.oid,'EXECUTE'),
                    p.proallargtypes IS NULL AND p.proargmodes IS NULL
                        AND p.proargdefaults IS NULL AND p.probin IS NULL AND p.prosqlbody IS NULL
                        AND p.protrftypes IS NULL AND p.procost=100 AND p.prorows=0
                FROM pg_proc p JOIN pg_language l ON l.oid=p.prolang
                JOIN pg_class c ON c.oid='public.cultures_box'::regclass
                WHERE p.oid=to_regprocedure(%s)""", [result, helper_role, helper_role, signature])
            helper = cursor.fetchone()
            config = ['search_path=pg_catalog, pg_temp']
            if signature != role_contract.FUNCTION:
                config += ['TimeZone=UTC', 'DateStyle=ISO, YMD']
            expected = (body, True, config, True, 'plpgsql', 'v', True, 'f', 1,
                        '25' if signature == role_contract.WRITER else '20', list(names),
                        'u', False, False, False, 0, 0, 0, True, True, True)
            if helper != expected:
                raise ValueError("CCA-EOB helper is missing or differs from the reviewed contract.")
            helpers.append(helper)
    return {"migrations": migrations, "triggers": triggers, "functions": functions, "helpers": helpers}


def _plan(workbook_path, actor):
    payload = _read_scope(actor)
    data = _planner_data(payload)
    plan = correction._build_plan(1, workbook_path=workbook_path, data=data)
    boxes = plan["baseline"]["boxes"]
    blockers = plan["blockers"]
    if {b["id"]: b["global_code"] for b in boxes} != BOXES:
        blockers.append("Exact approved Box identities 1835/1836/1837 are required.")
    if any(b["merged_into_id"] is not None or b["previous_global_code"] for b in boxes):
        blockers.append("Merged or renamed Box identities require a separate review.")
    if any(m["duplicate_of_id"] is not None or m["environment_source_box_id"] is not None
           for m in plan["baseline"]["measurements"]):
        blockers.append("Duplicate evidence or explicit environmental source is outside this correction.")
    if payload["incoming_evidence"]:
        blockers.append("Incoming evidence relations require independent resolution.")
    # Production inspection found zero readings. Never invalidate, detach or rewrite one.
    if payload["historical_readings_exist"]:
        blockers.append("Historical temperature state changed: stop; no associations may be rewritten.")
    if plan["v2_sha256"] != correction.V2_SHA256:
        blockers.append("Only the pinned reviewed Excel V2 fingerprint is accepted.")
    plan["ready"] = not blockers
    return correction._json({
        "correction": plan, "deployed_guards": _schema_state(), "database_snapshot": payload,
        "write_recipe": correction.write_recipe(plan, data["measurements"], actor.pk),
    })


def build_plan(organization_id, *, actor, target, workbook_path):
    _scope(organization_id, actor)
    require_target(target)
    if connection.in_atomic_block:
        raise ValueError("Production dry-run requires its own read-only transaction.")
    # One socket attestation spans the connection, the snapshot and every query.
    with socket_guard(target):
        verify_target(target)
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
                cursor.execute("SET LOCAL statement_timeout = '30s'")
            verify_target(target)
            _authorize(actor)
            return {"schema": SCHEMA, "target": target, "actor_id": actor.pk, "plan": _plan(workbook_path, actor)}


def validate_plan(document, approved_hash):
    if (not isinstance(approved_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", approved_hash)
            or type(document) is not dict or set(document) != {"schema", "target", "actor_id", "plan"}
            or document["schema"] != SCHEMA or correction.fingerprint(document) != approved_hash):
        raise ValueError("The complete production plan must match the reviewed SHA-256.")
    plan = document["plan"]["correction"]
    if (not plan["ready"] or plan["blockers"] or plan["organization_id"] != 1
            or plan["operation"] != correction.OPERATION or plan["v2_sha256"] != correction.V2_SHA256):
        raise ValueError("Only a complete conflict-free CCA-EOB V3 plan may be applied.")
    return plan


def _release(document, approved_hash):
    """Root attests the independently witnessed full restoration, not just --list."""
    evidence = _root_document(RELEASE_PATH)
    try:
        role_contract.validate_release(evidence, document, approved_hash)
    except ValueError as error:
        raise ValueError("Root-approved review and full restoration evidence bound to this plan are required. "
                         + str(error)) from error
    with artifacts.protected_file(evidence["archive"]["path"]):
        database_backup.verify_archive(evidence["archive"])
    return evidence


def apply_plan(organization_id, *, actor, document, approved_hash, workbook_path, authorization):
    _scope(organization_id, actor)
    plan = validate_plan(document, approved_hash)
    if actor.pk != document["actor_id"] or authorization != f"APPLY_CCA_EOB_ORGANIZATION_1:{approved_hash}":
        raise ValueError("Exact actor and deliberate CCA-EOB production authorization are required.")
    target = document["target"]
    require_target(target)
    if connection.in_atomic_block:
        raise ValueError("Production application requires its own outer transaction.")
    release = _release(document, approved_hash)

    def recheck():
        verify_target(target)
        if connection.in_atomic_block:
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL lock_timeout = '10s'")
                cursor.execute("SET LOCAL statement_timeout = '30s'")
                # Bounds how long the helper's locks can be held if this process stalls
                # (for example in the external archive check). It rolls this transaction
                # back; it never terminates another session.
                cursor.execute(f"SET LOCAL transaction_timeout = '{APPLY_TRANSACTION_TIMEOUT_SECONDS}s'")
            _authorize(actor)

    # The same Box-first lock order protects the Python rebuild and the SQL writer.
    # SQL also requires the separately installed, short-lived final authorization.
    # The CLI confirmation is not authority, including for direct SQL calls.
    with socket_guard(target):
        verify_target(target)
        with transaction.atomic():
            recheck()
            _schema_state()
            with connection.cursor() as cursor:
                cursor.execute("SELECT public.cca_eob_lock_v1(%s)", [actor.pk])
            if _plan(workbook_path, actor) != document["plan"]:
                raise ValueError("Stale production plan: review a fresh complete dry-run.")
            if _release(document, approved_hash) != release:
                raise ValueError("Production release evidence changed.")
            recheck()
            with connection.cursor() as cursor:
                cursor.execute("SELECT public.cca_eob_apply_v1(%s)", [approved_hash])
        return plan
