"""Read-only validation of the disposable GitHub-hosted PostgreSQL 18 service.

Run preflight BEFORE migrate, then schema AFTER migrate. This script never runs
migrations, creates databases, repairs schema, or accepts historical movements.
"""

import argparse
import json
import os
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
SETTINGS_MODULE = "config.historical_movements_ci_settings"
DATABASE_NAME = "historical_movements_ci"
TEST_DATABASE_NAME = "test_historical_movements_ci"
REQUIRED_ENVIRONMENT = {
    "GITHUB_ACTIONS": "true",
    "RUNNER_ENVIRONMENT": "github-hosted",
    "RUNNER_OS": "Linux",
    "HM_CI_POSTGRES": "1",
}
REQUIRED_MIGRATIONS = {
    ("cultures", "0018_historicalboxmovement"),
    ("audit", "0004_auditlog_audit_box_numeric_idx_and_more"),
}
INDEX_NAMES = ("audit_box_numeric_idx", "audit_box_code_idx")


class ValidationFailure(RuntimeError):
    """An isolation or schema assertion failed; never fall back to another DB."""


def require(condition, message):
    if not condition:
        raise ValidationFailure(message)


def write_evidence(name, findings):
    directory = Path(os.environ.get("HISTORICAL_MOVEMENTS_EVIDENCE_DIR") or ROOT / "ci-evidence")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"historical-movements-{name}.json"
    path.write_text(json.dumps(findings, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    return path


def check_environment():
    for key, expected in REQUIRED_ENVIRONMENT.items():
        require(os.environ.get(key) == expected, f"Set {key}={expected} on the dedicated GitHub-hosted Linux job only.")
    require(sys.version_info[:2] == (3, 12), "Use Python 3.12 for this CI validation.")
    require(sys.platform == "linux", "This validation only runs on the GitHub-hosted Linux runner.")
    require(not (ROOT / "backend" / ".env").exists(), "Remove backend/.env from the disposable CI checkout; no dotenv configuration is allowed.")
    require(os.environ.get("DJANGO_SETTINGS_MODULE", SETTINGS_MODULE) == SETTINGS_MODULE,
            f"Use only DJANGO_SETTINGS_MODULE={SETTINGS_MODULE}; no fallback settings are allowed.")


def check_database_config(connection, *, test_database=False):
    from django.conf import settings

    require(settings.SETTINGS_MODULE == SETTINGS_MODULE, f"Expected exactly {SETTINGS_MODULE}.")
    require(set(settings.DATABASES) == {"default"}, "Only the fixed default CI database is allowed.")
    require(connection.vendor == "postgresql", "PostgreSQL is mandatory; SQLite fallback is forbidden.")
    expected = {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": TEST_DATABASE_NAME if test_database else DATABASE_NAME,
        "USER": DATABASE_NAME,
        "PASSWORD": "",
        "HOST": "127.0.0.1",
        "PORT": "55439",
    }
    for key, value in expected.items():
        # Never include actual configuration values: they could contain secrets.
        require(connection.settings_dict.get(key) == value, f"Unexpected database {key}; use the fixed ephemeral CI configuration.")
    require(connection.settings_dict.get("TEST", {}).get("NAME") == TEST_DATABASE_NAME,
            f"TEST.NAME must be {TEST_DATABASE_NAME}.")
    options = connection.settings_dict.get("OPTIONS", {})
    require(not (set(options) - {"options"}), "Only timeout OPTIONS are allowed; no service, DSN or alternate connection parameters.")
    require(re.fullmatch(r"(?:\s*-c\s+(?:lock_timeout|statement_timeout)=\d+\s*)*", options.get("options", "")) is not None,
            "Database OPTIONS may contain numeric lock_timeout/statement_timeout only.")


def server_identity(connection, *, test_database=False):
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_setting('server_version_num')::integer, current_database(), current_user, session_user, inet_server_addr()::text, inet_server_port()")
        version, database, user, session_user, address, port = cursor.fetchone()
    require(180000 <= version < 190000, "Expected PostgreSQL 18; recreate the pinned ephemeral service.")
    require(database == (TEST_DATABASE_NAME if test_database else DATABASE_NAME), "Unexpected current_database(); stop rather than inspect another database.")
    require(user == session_user == DATABASE_NAME, "Unexpected database user; role switching is forbidden.")
    # A Docker-published connection reaches the container's bridge address, not
    # necessarily its loopback address. The client endpoint is checked above.
    require(port == 5432, "Expected PostgreSQL service internal port 5432 (published as 55439).")
    return {"server_version_num": version, "database": database, "user": user,
            "server_address": address, "server_port": port, "client_port": 55439}


def preflight(connection):
    identity = server_identity(connection)
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT nspname FROM pg_catalog.pg_namespace
            WHERE nspname NOT LIKE 'pg_%' AND nspname <> 'information_schema'
            ORDER BY nspname
        """)
        schemas = [row[0] for row in cursor.fetchall()]
        require(schemas == ["public"], "Unexpected user schema; provision a NEW empty ephemeral database, do not clean this one.")
        cursor.execute("""
            SELECT c.relname, c.relkind FROM pg_catalog.pg_class c
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' ORDER BY c.relname
        """)
        relations = cursor.fetchall()
        require(not relations, "Unexpected database contents: public must have NO tables, sequences, views or indexes before migrate. Recreate the ephemeral service; do not flush or reuse it.")
        cursor.execute("""
            SELECT count(*) FROM pg_catalog.pg_proc p
            JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
            WHERE n.nspname = 'public'
        """)
        require(cursor.fetchone()[0] == 0, "Unexpected public routines; recreate the empty CI service.")
        cursor.execute("""
            SELECT count(*) FROM pg_catalog.pg_type t
            JOIN pg_catalog.pg_namespace n ON n.oid = t.typnamespace
            WHERE n.nspname = 'public'
        """)
        require(cursor.fetchone()[0] == 0, "Unexpected public types; recreate the empty CI service.")
        cursor.execute("SELECT extname FROM pg_catalog.pg_extension ORDER BY extname")
        require(cursor.fetchall() == [("plpgsql",)], "Unexpected extensions; use a fresh PostgreSQL 18 service.")
    return {"phase": "preflight", "identity": identity, "schemas": schemas,
            "public_relations": [], "unexpected_database_contents": False}


def normalize_pg_type(sql_type):
    """Normalize built-in type aliases, retaining all widths and precision.

    Only complete, unqualified built-in names are eligible. Quoted names,
    domains and other custom types are left untouched, not treated as aliases.
    """
    aliases = {
        "varchar": "character varying", "character varying": "character varying",
        "char": "character", "character": "character",
        "int2": "smallint", "smallint": "smallint",
        "int4": "integer", "integer": "integer",
        "int8": "bigint", "bigint": "bigint",
        "bool": "boolean", "boolean": "boolean",
        "float4": "real", "real": "real",
        "float8": "double precision", "double precision": "double precision",
        "decimal": "numeric", "numeric": "numeric",
    }
    match = re.fullmatch(
        r"\s*([a-zA-Z][a-zA-Z0-9]*(?:\s+[a-zA-Z][a-zA-Z0-9]*)*)(\(\s*\d+(?:\s*,\s*\d+)?\s*\))?((?:\[\])*)\s*",
        sql_type,
    )
    if match is None:
        return sql_type
    name, modifier, arrays = match.groups()
    name = " ".join(name.lower().split())
    if name not in aliases:
        return sql_type
    return aliases[name] + re.sub(r"\s+", "", modifier or "") + arrays


def canonical_sql(sql):
    # pg_get_indexdef renders a text literal's type, not a cast of the JSON value.
    sql = sql.replace("'box_id'::text", "'box_id'")
    return re.sub(r'[\s"()]', "", sql).lower()


def audit_indexes(connection):
    from apps.audit.expressions import AuditBoxId
    from apps.audit.models import AuditLog

    query = AuditLog.objects.alias(identity=AuditBoxId("metadata")).filter(identity=1).query
    compiler = query.get_compiler(connection=connection)
    expression_sql, params = compiler.compile(query.annotations["identity"])
    require(tuple(params) == ("box_id",), "Unexpected typed JSON lookup parameters.")
    require("->>" not in expression_sql and "->" in expression_sql, "The lookup must use typed JSON ->, never text extraction/casts.")
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT ci.relname, i.indisvalid, i.indisready, i.indisunique,
                   pg_get_indexdef(i.indexrelid), pg_get_expr(i.indexprs, i.indrelid),
                   pg_get_expr(i.indpred, i.indrelid), pg_relation_size(i.indexrelid),
                   ARRAY(SELECT pg_get_indexdef(i.indexrelid, k, true)
                         FROM generate_series(1, i.indnatts) k), am.amname
            FROM pg_catalog.pg_index i
            JOIN pg_catalog.pg_class ci ON ci.oid = i.indexrelid
            JOIN pg_catalog.pg_class ct ON ct.oid = i.indrelid
            JOIN pg_catalog.pg_namespace n ON n.oid = ct.relnamespace
            JOIN pg_catalog.pg_am am ON am.oid = ci.relam
            WHERE n.nspname = 'public' AND ct.relname = %s AND ci.relname = ANY(%s)
            ORDER BY ci.relname
        """, [AuditLog._meta.db_table, list(INDEX_NAMES)])
        rows = cursor.fetchall()
    require({row[0] for row in rows} == set(INDEX_NAMES), "Missing audit indexes; apply audit migration 0004.")
    findings = []
    for name, valid, ready, unique, definition, expression, predicate, size, keys, method in rows:
        require(valid and ready, f"Index {name} is not valid and ready.")
        require(not unique and predicate is None and method == "btree", f"Unexpected index properties for {name}.")
        expected = ["organization_id", "object_type", "metadata -> 'box_id'" if name == "audit_box_numeric_idx" else "object_id"]
        require([canonical_sql(key) for key in keys] == [canonical_sql(key) for key in expected],
                f"Unexpected index keys for {name}; typed metadata->box_id must match the lookup without a value cast.")
        if name == "audit_box_numeric_idx":
            require(canonical_sql(expression) == canonical_sql("metadata -> 'box_id'"), "Numeric audit index expression mismatch.")
            rendered = expression_sql.replace('"audit_auditlog".', "").replace("%s", "'box_id'")
            require(canonical_sql(rendered) == canonical_sql(expression), "Lookup and index expressions do not match.")
        else:
            require(expression is None, "Legacy audit index must use plain columns.")
        findings.append({"name": name, "valid": valid, "ready": ready, "definition": definition,
                         "expression": expression, "bytes": size, "keys": keys})
    return findings


def schema_findings(connection):
    from django.db.migrations.executor import MigrationExecutor
    from apps.cultures.models import HistoricalBoxMovement

    executor = MigrationExecutor(connection)
    executor.loader.check_consistent_history(connection)
    require(not executor.migration_plan(executor.loader.graph.leaf_nodes()), "Pending migrations; run migrate with the dedicated CI settings first.")
    require(REQUIRED_MIGRATIONS <= set(executor.loader.applied_migrations), "cultures 0018 and audit 0004 must both be applied.")
    migration_model = executor.loader.project_state([("cultures", "0018_historicalboxmovement")]).apps.get_model("cultures", "HistoricalBoxMovement")
    expected_columns = {field.column for field in migration_model._meta.local_fields}
    require(expected_columns == {field.column for field in HistoricalBoxMovement._meta.local_fields}, "Current historical model differs from cultures 0018.")
    table = HistoricalBoxMovement._meta.db_table
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull
            FROM pg_catalog.pg_attribute a
            WHERE a.attrelid = %s::regclass AND a.attnum > 0 AND NOT a.attisdropped
            ORDER BY a.attnum
        """, [f"public.{table}"])
        columns = cursor.fetchall()
        require({row[0] for row in columns} == expected_columns, "Historical movement columns differ from cultures 0018; occurrence timestamps are forbidden.")
        column_info = {name: (kind, required) for name, kind, required in columns}
        require(column_info["iso_year"] == ("smallint", True) and column_info["iso_week"] == ("smallint", True), "Expected non-null smallint ISO week fields.")
        for field in migration_model._meta.local_fields:
            require(normalize_pg_type(column_info[field.column][0]) == normalize_pg_type(field.db_type(connection)),
                    f"Unexpected SQL type for {field.column}.")
            require(column_info[field.column][1] == (not field.null), f"Unexpected nullability for {field.column}.")
        cursor.execute("""
            SELECT c.conname, c.contype, c.convalidated, pg_get_constraintdef(c.oid),
                   ARRAY(SELECT a.attname FROM unnest(c.conkey) WITH ORDINALITY k(num, ord)
                         JOIN pg_catalog.pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=k.num ORDER BY k.ord),
                   CASE WHEN referenced.oid IS NOT NULL THEN
                       quote_ident(referenced_schema.nspname) || '.' || quote_ident(referenced.relname)
                   END,
                   ARRAY(SELECT a.attname FROM unnest(c.confkey) WITH ORDINALITY k(num, ord)
                         JOIN pg_catalog.pg_attribute a ON a.attrelid=c.confrelid AND a.attnum=k.num ORDER BY k.ord),
                   c.condeferrable, c.condeferred, c.confupdtype, c.confdeltype, c.confmatchtype
            FROM pg_catalog.pg_constraint c
            LEFT JOIN pg_catalog.pg_class referenced ON referenced.oid = c.confrelid
            LEFT JOIN pg_catalog.pg_namespace referenced_schema ON referenced_schema.oid = referenced.relnamespace
            WHERE c.conrelid = %s::regclass ORDER BY c.conname
        """, [f"public.{table}"])
        constraints = cursor.fetchall()
        require(all(row[2] for row in constraints), "Historical movement constraints must all be validated.")
        by_name = {row[0]: row for row in constraints}
        expected_checks = {
            "historical_movement_year_valid": "CHECK (iso_year >= 1 AND iso_year <= 9999)",
            "historical_movement_week_valid": "CHECK (iso_week >= 1 AND iso_week <= 53)",
            "historical_movement_distinct_zones": "CHECK (NOT (from_thermal_zone_id = to_thermal_zone_id))",
        }
        for name, definition in expected_checks.items():
            require(name in by_name and by_name[name][1] == "c", f"Missing named CHECK {name}.")
            require(canonical_sql(by_name[name][3]) == canonical_sql(definition), f"Unexpected definition of {name}.")
        name = "unique_historical_box_movement_week"
        require(name in by_name and by_name[name][1] == "u" and by_name[name][4] == ["box_id", "iso_year", "iso_week"], "Missing exact unique box/week constraint.")
        require(any(row[1] == "u" and row[4] == ["candidate_key"] for row in constraints), "Missing candidate_key uniqueness.")
        require(any(row[1] == "p" and row[4] == ["id"] for row in constraints), "Missing historical movement primary key.")
        expected_fks = {
            field.column: (f"public.{field.related_model._meta.db_table}", field.target_field.column)
            for field in migration_model._meta.local_fields if field.many_to_one
        }
        foreign_keys = [row for row in constraints if row[1] == "f"]
        actual_fks = {row[4][0]: (row[5], row[6][0])
                      for row in foreign_keys if len(row[4]) == len(row[6]) == 1}
        require(len(foreign_keys) == len(expected_fks) and actual_fks == expected_fks,
                "Historical movement foreign keys differ from cultures 0018.")
        for row in constraints:
            if row[1] == "f":
                require(tuple(row[7:]) == (True, True, "a", "a", "s"),
                        f"Unexpected FK deferral or referential actions for {row[0]}.")
        cursor.execute(f'SELECT count(*) FROM "{table}"')
        count = cursor.fetchone()[0]
        require(count == 0, "Historical reconstruction rows already exist; this validation never creates or accepts them.")
    return {"columns": columns, "constraints": [{"name": row[0], "type": row[1], "validated": row[2], "definition": row[3]} for row in constraints],
            "applied_required_migrations": sorted(REQUIRED_MIGRATIONS), "pending_migrations": [],
            "historical_reconstruction_rows": count, "audit_indexes": audit_indexes(connection)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("preflight", "schema"))
    args = parser.parse_args(argv)
    try:
        check_environment()
        os.environ["DJANGO_SETTINGS_MODULE"] = SETTINGS_MODULE
        sys.path.insert(0, str(ROOT / "backend"))
        import django
        django.setup()
        from django.db import connection

        check_database_config(connection)
        if args.phase == "preflight":
            findings = preflight(connection)
        else:
            findings = {"phase": "schema", "identity": server_identity(connection), **schema_findings(connection)}
        findings["status"] = "passed"
        path = write_evidence(args.phase, findings)
        print(json.dumps(findings, sort_keys=True, default=str))
        print(f"Historical movements {args.phase}: passed; evidence: {path}")
        connection.close()
        return 0
    except Exception as exc:
        # Connection exceptions can embed a DSN. Do not echo them or settings.
        message = str(exc) if isinstance(exc, ValidationFailure) else (
            f"{type(exc).__name__}: CI initialization/database inspection failed. Verify the fixed PostgreSQL 18 service and dedicated settings; no fallback was attempted."
        )
        findings = {"phase": args.phase, "status": "failed", "error": message}
        print(json.dumps(findings, sort_keys=True), file=sys.stderr)
        try:
            write_evidence(args.phase, findings)
        except OSError:
            print("Could not write failure evidence; make HISTORICAL_MOVEMENTS_EVIDENCE_DIR writable.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
