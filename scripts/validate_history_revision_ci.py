"""Synthetic Phase 5A rehearsal; executable only against the hosted CI service.

Preflight identifies an empty database before adding a disposable-service marker.
Run applies the graph, rehearses 0020 only, then tests a separate Django database.
No migration repair, publisher, production endpoint or environment dump is provided.
"""

import argparse
from contextlib import redirect_stdout
from io import StringIO
from importlib import import_module
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
SETTINGS = "config.history_revision_ci_settings"
M19 = ("cultures", "0019_history_revision_storage")
M20 = ("cultures", "0020_history_revision_guards")
TABLES = (
    "cultures_boxhistorystream", "cultures_boxhistoryrevision",
    "cultures_boxhistoryeventidentity",
)
GUARDS = {
    f"history_{key}_{action}": TABLES[index]
    for index, key in enumerate(("stream", "revision", "identity"))
    for action in ("insert", "update", "delete")
}
GUARDS["history_revision_shape_insert"] = TABLES[1]
CONSTRAINTS = {
    "history_stream_original_unique", "history_revision_sequence_unique",
    "history_revision_request_unique", "history_revision_positive",
    "history_revision_root_shape", "history_revision_reason_required", "history_identity_shape",
}
UNIQUE_INDEXES = {
    "history_revision_no_branch", "history_identity_source_unique", "history_identity_derived_unique",
}
TEST_LABELS = (
    "apps.cultures.test_history_revision_models",
    "apps.cultures.test_history_revision_codec",
    "apps.cultures.test_history_revision_composition",
    "apps.cultures.test_history_revision_postgres_ci",
)


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def evidence(name, data):
    directory = Path(os.environ["HR_CI_EVIDENCE_DIR"])
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.json").write_text(
        json.dumps(data, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8",
    )


def bootstrap():
    require(sys.platform == "linux", "This harness runs only on hosted Linux, never on Windows.")
    require(sys.version_info[:2] == (3, 12), "Use the workflow's Python 3.12.")
    require(os.environ.get("DJANGO_SETTINGS_MODULE") == SETTINGS, "Explicit dedicated CI settings required.")
    require(bool(os.environ.get("HR_CI_EVIDENCE_DIR")), "An explicit synthetic evidence directory is required.")
    sys.path.insert(0, str(ROOT / "backend"))
    import django
    django.setup()
    from config import history_revision_ci_settings as guards
    from django.db import connection
    guards.check_database_config(connection)
    return connection, guards


def preflight(connection, guards):
    # The marker does not yet exist: check the complete identity read-only first.
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT current_database(), current_user, session_user, inet_server_port(),
                   current_setting('server_version_num')::integer,
                   shobj_description(oid, 'pg_database')
            FROM pg_catalog.pg_database WHERE datname = current_database()
        """)
        row = cursor.fetchone()
        require(row is not None and row[:4] == (guards.DATABASE_NAME, guards.DATABASE_NAME,
                guards.DATABASE_NAME, 5432), "Unexpected service database, role or port; refusing mutation.")
        require(180000 <= row[4] < 190000 and row[5] is None,
                "Expected a new, unmarked PostgreSQL 18 service database.")
        cursor.execute("SELECT nspname FROM pg_namespace WHERE nspname NOT LIKE 'pg_%' AND nspname <> 'information_schema' ORDER BY nspname")
        require(cursor.fetchall() == [("public",)], "Unexpected user schemas; provision a fresh service.")
        for catalog, namespace in (("pg_class", "relnamespace"), ("pg_proc", "pronamespace"), ("pg_type", "typnamespace")):
            cursor.execute(f"SELECT count(*) FROM {catalog} c JOIN pg_namespace n ON n.oid = c.{namespace} WHERE n.nspname = 'public'")
            require(cursor.fetchone()[0] == 0, "Service public schema is not empty; refusing mutation.")
        cursor.execute("SELECT extname FROM pg_extension ORDER BY extname")
        require(cursor.fetchall() == [("plpgsql",)], "Unexpected service extensions.")
        cursor.execute("SELECT datname FROM pg_database WHERE NOT datistemplate ORDER BY datname")
        require(cursor.fetchall() == [(guards.DATABASE_NAME,), ("postgres",)],
                "Unexpected database inventory; do not reuse a service.")
        # Fixed identifiers and a nonsecret synthetic comment, never caller input.
        cursor.execute("COMMENT ON DATABASE history_revision_ci IS 'history-revision-actions-disposable'")
    import psycopg
    evidence("preflight", {"identity": guards.server_identity(connection),
                           "empty_before_marker": True, "driver": psycopg.__version__,
                           "libpq": psycopg.pq.version(), "commit": os.environ["GITHUB_SHA"]})


def sql_tokens(sql):
    """Tokenize only the SQL subset emitted by the Phase 5A constraints."""
    pattern = re.compile(r"\s+|'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|::|>=|<=|<>|!=|[=><(),]|[A-Za-z_][A-Za-z_0-9]*|[0-9]+")
    tokens, offset = [], 0
    for match in pattern.finditer(sql):
        require(match.start() == offset, "Unsupported SQL in history catalog expression.")
        offset = match.end()
        token = match.group()
        if not token.isspace():
            tokens.append(token)
    require(offset == len(sql), "Unsupported SQL in history catalog expression.")
    return tokens


def expression_tree(sql, text_columns):
    """Normalize grouping, not semantics; unknown syntax fails closed.

    PostgreSQL deparses varchar comparisons with text casts. Only those casts
    on known text columns/string literals are redundant here. JSON casts and
    boolean grouping remain part of the comparison.
    """
    tokens = sql_tokens(sql)
    position = 0

    def accept(token):
        nonlocal position
        if position < len(tokens) and tokens[position].upper() == token:
            position += 1
            return True
        return False

    def atom():
        nonlocal position
        if accept("("):
            value = boolean("OR")
            require(accept(")"), "Unbalanced history catalog expression.")
        else:
            require(position < len(tokens), "Incomplete history catalog expression.")
            token = tokens[position]
            position += 1
            if token.startswith("'"):
                value = ("string", token[1:-1].replace("''", "'"))
            elif token.isdigit():
                value = ("number", int(token))
            else:
                require(token.startswith('"') or re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", token),
                        "Unsupported history catalog operand.")
                value = ("column", token[1:-1].replace('""', '"') if token.startswith('"') else token.lower())
        while accept("::"):
            require(position < len(tokens), "Missing history catalog cast type.")
            cast = tokens[position].lower()
            position += 1
            redundant = cast == "text" and (
                value[0] == "string" or (value[0] == "column" and value[1] in text_columns)
            )
            if not redundant:
                value = ("cast", cast, value)
        return value

    def comparison():
        left = atom()
        if accept("IS"):
            negate = accept("NOT")
            require(accept("NULL"), "Unsupported history catalog IS expression.")
            return ("is_not_null" if negate else "is_null", left)
        for operator in ("=", ">=", "<=", "<>", "!=", ">", "<"):
            if accept(operator):
                return ("<>" if operator == "!=" else operator, left, atom())
        return left

    def negation():
        if accept("NOT"):
            return ("not", negation())
        return comparison()

    def boolean(operator):
        operand = (lambda: boolean("AND")) if operator == "OR" else negation
        values = [operand()]
        while accept(operator):
            values.append(operand())
        # Parenthesized associative groups have the same meaning, unlike a
        # mixture of AND/OR or a change in NOT scope.
        flattened = []
        for value in values:
            flattened.extend(value[1:] if value[0] == operator.lower() else [value])
        return (operator.lower(), *flattened) if len(flattened) > 1 else flattened[0]

    result = boolean("OR")
    require(position == len(tokens), "Unsupported trailing history catalog SQL.")
    return result


def expression_columns(tree):
    if tree[0] == "column":
        return {tree[1]}
    return set().union(*(expression_columns(item) for item in tree[1:] if isinstance(item, tuple)))


def expected_catalog(connection):
    """Compile model conditions without executing schema-editor DDL."""
    from django.apps import apps
    from django.db.models import CheckConstraint, UniqueConstraint

    constraints, indexes, foreign_keys = {}, {}, set()
    editor = connection.schema_editor(collect_sql=True)
    for name in ("BoxHistoryStream", "BoxHistoryRevision", "BoxHistoryEventIdentity"):
        model = apps.get_model("cultures", name)
        table = model._meta.db_table
        text_columns = {
            field.column for field in model._meta.local_fields
            if field.get_internal_type() in {"CharField", "TextField"}
        }
        for field in model._meta.local_fields:
            if field.is_relation:
                foreign_keys.add((table, field.column, field.related_model._meta.db_table))
        for constraint in model._meta.constraints:
            if isinstance(constraint, CheckConstraint):
                tree = expression_tree(constraint._get_check_sql(model, editor), text_columns)
                constraints[constraint.name] = (table, "c", expression_columns(tree), tree, text_columns)
            elif isinstance(constraint, UniqueConstraint):
                require(not constraint.expressions and not constraint.include and not constraint.opclasses
                        and constraint.deferrable is None and constraint.nulls_distinct is None,
                        "Unsupported history model uniqueness options.")
                columns = tuple(model._meta.get_field(field).column for field in constraint.fields)
                if constraint.condition is None:
                    constraints[constraint.name] = (table, "u", columns, None, text_columns)
                else:
                    tree = expression_tree(constraint._get_condition_sql(model, editor), text_columns)
                    indexes[constraint.name] = (table, columns, tree, text_columns)
            else:
                raise RuntimeError("Unsupported history model constraint.")
    require(set(constraints) == CONSTRAINTS and set(indexes) == UNIQUE_INDEXES,
            "History model named constraints differ from Phase 5A expectations.")
    return constraints, indexes, foreign_keys


def validate_catalog(connection, constraints, indexes):
    expected_constraints, expected_indexes, expected_fks = expected_catalog(connection)
    for name, (table, kind, columns, tree, text_columns) in expected_constraints.items():
        matches = [row for row in constraints if row[1] == name]
        require(len(matches) == 1, f"Missing or duplicate history constraint: {name}.")
        row = matches[0]
        require(row[0] == table and row[2] == kind and not row[3] and not row[4] and row[5],
                f"Unexpected table, type or flags for history constraint: {name}.")
        actual_columns = set(row[7]) if kind == "c" else tuple(row[7])
        require(actual_columns == columns, f"Unexpected columns for history constraint: {name}.")
        if kind == "c":
            require(row[9] is not None and expression_tree(row[9], text_columns) == tree,
                    f"Unexpected check expression for history constraint: {name}.")
        else:
            expected_sql = f"UNIQUE ({', '.join(columns)})"
            unquote = lambda token: token[1:-1] if token.startswith('"') else token
            require([unquote(token) for token in sql_tokens(row[6])] == sql_tokens(expected_sql),
                    f"Unexpected unique definition for history constraint: {name}.")
    require(all(row[5] for row in constraints), "Unvalidated history constraint.")
    actual_fks = {(r[0], r[7][0], r[8]) for r in constraints if r[2] == "f" and len(r[7]) == 1}
    require(actual_fks == expected_fks, "History foreign-key columns or targets differ from models.")
    require(all(r[3] and r[4] for r in constraints if r[2] == "f"), "History FKs must be initially deferred.")
    for name, (table, columns, tree, text_columns) in expected_indexes.items():
        matches = [row for row in indexes if row[0] == name]
        require(len(matches) == 1, f"Missing or duplicate history partial unique index: {name}.")
        row = matches[0]
        require(row[1] and row[2] and row[3] and row[6] == table and tuple(row[7]) == columns
                and row[8] == "btree" and row[9] == len(columns) and not row[10]
                and row[11] and row[12], f"Unexpected definition or flags for history index: {name}.")
        require(row[5] is not None and expression_tree(row[5], text_columns) == tree,
                f"Unexpected predicate for history index: {name}.")


def expected_function_bodies(connection):
    """Render 0020 with its historical models, without executing any SQL.

    PostgreSQL stores a PL/pgSQL AS string verbatim in pg_proc.prosrc. Compare
    that source, not pg_get_functiondef's normalized CREATE OR REPLACE wrapper.
    """
    from django.db.migrations.loader import MigrationLoader

    statements = []

    class CaptureEditor:
        def __init__(self):
            self.connection = connection
            self.quote_name = connection.ops.quote_name

        def execute(self, sql):
            statements.append(sql)

    historical_apps = MigrationLoader(None).project_state([M20]).apps
    migration = import_module("apps.cultures.migrations." + M20[1])
    migration.install_guards(historical_apps, CaptureEditor())
    bodies = {}
    for statement in statements:
        match = re.fullmatch(
            r"\s*CREATE FUNCTION (\w+)\(\) RETURNS trigger LANGUAGE plpgsql AS \$\$(.*?)\$\$;"
            r"\s*CREATE TRIGGER \1 BEFORE (INSERT|UPDATE|DELETE) ON (\w+)"
            r"\s*FOR EACH ROW EXECUTE FUNCTION \1\(\);\s*",
            statement, re.DOTALL,
        )
        require(match is not None, "Unexpected authoritative 0020 function SQL; review the QA renderer.")
        name, body, action, table = match.groups()
        require(name not in bodies and GUARDS.get(name) == table and name.endswith("_" + action.lower()),
                "Unexpected authoritative 0020 guard identity.")
        bodies[name] = body.strip()
    require(set(bodies) == set(GUARDS), "Authoritative 0020 function set is incomplete.")
    return bodies


def validate_functions(connection, functions, triggers, *, guarded):
    expected = expected_function_bodies(connection) if guarded else {}
    require(len(functions) == len(expected) and {row[0] for row in functions} == set(expected),
            "History guard functions are missing, replaced or overloaded.")
    by_name = {row[0]: row for row in functions}
    for name, body in expected.items():
        row = by_name[name]
        # Language/signature and execution attributes are separate from prosrc.
        require(tuple(row[2:13]) == ("plpgsql", 0, True, "f", False, False, "v", "u", None, False, False),
                f"Unexpected execution attributes for history function: {name}.")
        require(row[1].strip() == body, f"Installed history function body differs from migration 0020: {name}.")
    for trigger in triggers:
        require(trigger[4] in by_name and trigger[6] == by_name[trigger[4]][13],
                "History trigger does not call the expected public function OID.")


def installed_schema(connection, *, guarded):
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT t.tgname, c.relname, pg_get_triggerdef(t.oid), t.tgenabled,
                   p.proname, pg_get_functiondef(p.oid), p.oid,
                                      t.tgtype, t.tgqual IS NULL, cardinality(t.tgattr) = 0,
                                      t.tgnargs = 0, t.tgconstraint = 0
            FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            JOIN pg_proc p ON p.oid = t.tgfoid
            WHERE n.nspname = 'public' AND NOT t.tgisinternal
              AND c.relname = ANY(%s) ORDER BY t.tgname
        """, [list(TABLES)])
        triggers = cursor.fetchall()
        require({r[0]: r[1] for r in triggers} == (GUARDS if guarded else {}),
                "History trigger catalog does not match the expected guard state.")
        require(all(r[3] == "O" and r[4] == r[0] and "RAISE EXCEPTION" in r[5]
                    and f"BEFORE {r[0].rsplit('_', 1)[1].upper()}" in r[2] for r in triggers),
                "A history trigger/function is disabled or has an unexpected definition.")
        for trigger in triggers:
            # ROW | BEFORE | exactly one event; no WHEN, UPDATE OF, arguments or
            # constraint-trigger substitution may suppress normal enforcement.
            event = trigger[0].rsplit("_", 1)[1]
            trigger_type = 1 | 2 | {"insert": 4, "delete": 8, "update": 16}[event]
            require(tuple(trigger[7:]) == (trigger_type, True, True, True, True),
                    f"Unexpected firing semantics for history trigger: {trigger[0]}.")
        cursor.execute("""
            SELECT p.proname, p.prosrc, l.lanname, p.pronargs,
                   p.prorettype = 'pg_catalog.trigger'::regtype, p.prokind,
                   p.prosecdef, p.proisstrict, p.provolatile, p.proparallel,
                   p.proconfig, p.proleakproof, p.proretset, p.oid,
                   pg_get_functiondef(p.oid)
            FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
            JOIN pg_language l ON l.oid = p.prolang
            WHERE n.nspname = 'public' AND p.proname = ANY(%s) ORDER BY p.proname, p.oid
        """, [list(GUARDS)])
        functions = cursor.fetchall()
        validate_functions(connection, functions, triggers, guarded=guarded)
        cursor.execute("""
            SELECT c.relname, k.conname, k.contype, k.condeferrable, k.condeferred,
                   k.convalidated, pg_get_constraintdef(k.oid),
                   ARRAY(SELECT a.attname FROM unnest(k.conkey) WITH ORDINALITY x(num, ord)
                         JOIN pg_attribute a ON a.attrelid = k.conrelid AND a.attnum = x.num ORDER BY x.ord),
                   ref.relname, pg_get_expr(k.conbin, k.conrelid)
            FROM pg_constraint k JOIN pg_class c ON c.oid = k.conrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            LEFT JOIN pg_class ref ON ref.oid = k.confrelid
            WHERE n.nspname = 'public' AND c.relname = ANY(%s)
            ORDER BY c.relname, k.conname
        """, [list(TABLES)])
        constraints = cursor.fetchall()

        cursor.execute("""
            SELECT i.relname, x.indisunique, x.indisvalid, x.indisready,
                   pg_get_indexdef(i.oid), pg_get_expr(x.indpred, x.indrelid), t.relname,
                   ARRAY(SELECT a.attname FROM unnest(x.indkey) WITH ORDINALITY k(num, ord)
                         LEFT JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = k.num
                         WHERE k.ord <= x.indnkeyatts ORDER BY k.ord),
                   am.amname, x.indnatts, x.indnullsnotdistinct, x.indimmediate,
                   (x.indexprs IS NULL AND NOT EXISTS (
                       SELECT 1 FROM unnest(x.indoption) opt WHERE opt <> 0)
                    AND NOT EXISTS (
                       SELECT 1 FROM unnest(x.indclass) op JOIN pg_opclass oc ON oc.oid = op
                       WHERE NOT oc.opcdefault)
                    AND NOT EXISTS (
                       SELECT 1 FROM unnest(x.indkey, x.indcollation) k(num, coll)
                       JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = k.num
                       WHERE k.coll <> a.attcollation))
            FROM pg_index x JOIN pg_class i ON i.oid = x.indexrelid
            JOIN pg_class t ON t.oid = x.indrelid
            JOIN pg_am am ON am.oid = i.relam
            JOIN pg_namespace n ON n.oid = t.relnamespace
            WHERE n.nspname = 'public' AND t.relname = ANY(%s) ORDER BY i.relname
        """, [list(TABLES)])
        indexes = cursor.fetchall()
        validate_catalog(connection, constraints, indexes)
    # OIDs prove the live association above, but change when 0020 is reapplied.
        # Keep the rehearsal comparison about definitions, not ephemeral object IDs.
        return {"guarded": guarded,
                "triggers_and_functions": [tuple(row[:6]) + tuple(row[7:]) for row in triggers],
                "function_definitions": [tuple(row[:13]) + tuple(row[14:]) for row in functions],
                "constraints": constraints, "indexes": indexes}


def run(connection, guards):
    from django.core.management import call_command
    from django.db.migrations.executor import MigrationExecutor
    from django.db.migrations.recorder import MigrationRecorder

    guards.server_identity(connection)
    require(not MigrationRecorder(connection).has_table(), "Full graph must start without migration history.")
    executor = MigrationExecutor(connection)
    plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
    require({M19, M20} <= {(m.app_label, m.name) for m, backwards in plan if not backwards},
            "0019 and 0020 must really execute, not be faked or pre-applied.")
    evidence("migration-plan", {"forward": [f"{m.app_label}.{m.name}" for m, backwards in plan]})
    call_command("check")
    call_command("migrate", plan=True, interactive=False)
    call_command("migrate", interactive=False, verbosity=2)
    call_command("migrate", check_unapplied=True, interactive=False)
    call_command("makemigrations", check=True, dry_run=True, interactive=False)
    require({M19, M20} <= set(MigrationRecorder(connection).applied_migrations()), "Phase 5A not recorded as applied.")
    initial = installed_schema(connection, guarded=True)
    evidence("schema-initial", initial)
    # Rehearse the exact RunPython reverse, never reverse storage migration 0019.
    executor = MigrationExecutor(connection)
    reverse = executor.migration_plan([M19])
    require([(m.app_label, m.name, backwards) for m, backwards in reverse] == [(*M20, True)],
            "Reverse plan must remove only 0020, never 0019 or another dependent migration.")
    call_command("migrate", "cultures", M19[1], interactive=False, verbosity=2)
    require(M19 in MigrationRecorder(connection).applied_migrations()
            and M20 not in MigrationRecorder(connection).applied_migrations(), "Unexpected reverse migration state.")
    evidence("schema-reversed", installed_schema(connection, guarded=False))
    call_command("migrate", interactive=False, verbosity=2)
    restored = installed_schema(connection, guarded=True)
    require(initial == restored, "Restored catalogs differ after 0020 reapplication.")
    evidence("schema-restored", restored)
    evidence("migration-results", {"full_graph_applied": True, "0019_executed": True,
                                   "0020_executed": True, "drift": False,
                                   "0020_reversed": True, "guards_removed": True,
                                   "0020_reapplied": True, "catalogs_restored": True})
    for migration in (M19, M20):
        sql = StringIO()
        with redirect_stdout(sql):
            call_command("sqlmigrate", *migration)
        # RunPython is intentionally opaque to sqlmigrate; pg_get_functiondef above
        # is the actual installed multi-statement execution evidence.
        (Path(os.environ["HR_CI_EVIDENCE_DIR"]) / f"{migration[1]}.sql").write_text(sql.getvalue(), encoding="utf-8")
    call_command("test", *TEST_LABELS, interactive=False, verbosity=2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("preflight", "run"))
    phase = parser.parse_args().phase
    connection, guards = bootstrap()
    try:
        if phase == "preflight":
            preflight(connection, guards)
        else:
            run(connection, guards)
    finally:
        connection.close()


if __name__ == "__main__":
    main()
