"""Offline catalog mutation tests; no Django settings, drivers or sockets."""

from contextlib import contextmanager
from copy import deepcopy
import importlib.util
from pathlib import Path
import subprocess
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch


UTILITY_PATH = Path(__file__).resolve().with_name("validate_history_revision_ci.py")
SPEC = importlib.util.spec_from_file_location("history_revision_catalog_utility", UTILITY_PATH)
utility = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(utility)


class CheckConstraint:
    def __init__(self, name, sql):
        self.name, self.sql = name, sql

    def _get_check_sql(self, model, editor):
        return self.sql


class UniqueConstraint:
    expressions = include = opclasses = ()
    deferrable = nulls_distinct = None

    def __init__(self, name, fields, condition=None):
        self.name, self.fields, self.condition = name, fields, condition

    def _get_condition_sql(self, model, editor):
        return self.condition


def field(name, *, column=None, text=False, related=None):
    return SimpleNamespace(
        name=name, column=column or name, is_relation=related is not None,
        related_model=related, get_internal_type=lambda: "CharField" if text else "IntegerField",
    )


def model(table, fields, constraints):
    return SimpleNamespace(_meta=SimpleNamespace(
        db_table=table, local_fields=fields, constraints=constraints,
        get_field=lambda name: next(item for item in fields if item.name == name),
    ))


def fixture_models():
    stream, revision, identity = utility.TABLES
    return {
        "BoxHistoryStream": model(stream, [field("organization", column="organization_id"), field("original_box_id")], [
            UniqueConstraint("history_stream_original_unique", ("organization", "original_box_id")),
        ]),
        "BoxHistoryRevision": model(revision, [
            field("stream", column="stream_id"), field("sequence"), field("schema_version"),
            field("predecessor", column="predecessor_id"), field("idempotency_key"), field("reason", text=True),
        ], [
            UniqueConstraint("history_revision_sequence_unique", ("stream", "sequence")),
            UniqueConstraint("history_revision_request_unique", ("stream", "idempotency_key")),
            UniqueConstraint("history_revision_no_branch", ("predecessor",), '"predecessor_id" IS NOT NULL'),
            CheckConstraint("history_revision_positive", '("schema_version" >= 1 AND "sequence" >= 1)'),
            CheckConstraint("history_revision_root_shape", '(("predecessor_id" IS NULL AND "sequence" = 1) OR ("predecessor_id" IS NOT NULL AND "sequence" > 1))'),
            CheckConstraint("history_revision_reason_required", 'NOT ("reason" = \'\')'),
        ]),
        "BoxHistoryEventIdentity": model(identity, [
            field("stream", column="stream_id"), field("kind", text=True), field("source_key", text=True),
            field("derived_source_key", text=True), field("source_fingerprint", text=True), field("locator"),
        ], [
            UniqueConstraint("history_identity_source_unique", ("stream", "source_key"), 'NOT ("source_key" = \'\')'),
            UniqueConstraint("history_identity_derived_unique", ("stream", "derived_source_key"), 'NOT ("derived_source_key" = \'\')'),
            CheckConstraint("history_identity_shape", """(
                ("derived_source_key" = '' AND "kind" = 'persisted' AND NOT ("source_key" = '')
                 AND "locator" = '{}'::jsonb AND "source_fingerprint" = '')
                OR ("kind" = 'derived' AND "source_key" = '' AND NOT ("derived_source_key" = '')
                    AND NOT ("source_fingerprint" = '') AND NOT ("locator" = '{}'::jsonb))
                OR ("derived_source_key" = '' AND "kind" = 'inserted' AND "locator" = '{}'::jsonb
                    AND "source_fingerprint" = '' AND "source_key" = '')
            )"""),
        ]),
    }


def catalog_rows(models):
    constraints, indexes = [], []
    for item in models.values():
        for constraint in item._meta.constraints:
            table = item._meta.db_table
            if isinstance(constraint, CheckConstraint):
                # Deliberately independent catalog column lists, not the parser
                # being tested. PostgreSQL conkey ordering is not semantic.
                columns = {
                    "history_revision_positive": ["schema_version", "sequence"],
                    "history_revision_root_shape": ["predecessor_id", "sequence"],
                    "history_revision_reason_required": ["reason"],
                    "history_identity_shape": ["derived_source_key", "kind", "source_key", "locator", "source_fingerprint"],
                }[constraint.name]
                sql = constraint.sql
                if constraint.name == "history_revision_reason_required":
                    sql = "(NOT (((reason)::text = ''::text)))"
                constraints.append([table, constraint.name, "c", False, False, True,
                                    "CHECK (" + sql + ")", columns, None, sql])
            else:
                columns = [item._meta.get_field(name).column for name in constraint.fields]
                if constraint.condition is None:
                    definition = 'UNIQUE (' + ', '.join('"' + name + '"' for name in columns) + ')'
                    constraints.append([table, constraint.name, "u", False, False, True,
                                        definition, columns, None, None])
                else:
                    predicate = constraint.condition
                    if constraint.name == "history_identity_source_unique":
                        predicate = "(NOT ((source_key)::text = ''::text))"
                    indexes.append([constraint.name, True, True, True, "recorded index DDL", predicate,
                                    table, columns, "btree", len(columns), False, True, True])
    return constraints, indexes


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.socket_guard = patch("socket.socket", side_effect=AssertionError("Offline tests must not open sockets"))
        self.socket_guard.start()
        self.addCleanup(self.socket_guard.stop)
        self.models = fixture_models()
        self.constraints, self.indexes = catalog_rows(self.models)
        self.queries = []
        self.cursor = Mock()
        self.cursor.__enter__ = Mock(return_value=self.cursor)
        self.cursor.__exit__ = Mock(return_value=False)
        self.cursor.execute.side_effect = self.execute
        self.connection = SimpleNamespace(cursor=Mock(return_value=self.cursor), schema_editor=Mock())
        apps_module, models_module = ModuleType("django.apps"), ModuleType("django.db.models")
        apps_module.apps = SimpleNamespace(get_model=lambda app, name: self.models[name])
        models_module.CheckConstraint, models_module.UniqueConstraint = CheckConstraint, UniqueConstraint
        modules = {"django": ModuleType("django"), "django.apps": apps_module,
                   "django.db": ModuleType("django.db"), "django.db.models": models_module}
        self.modules = patch.dict(sys.modules, modules)
        self.modules.start()
        self.addCleanup(self.modules.stop)
        self.guarded = False
        self.oid_offset = 0
        self.trigger_mutation = None
        self.function_bodies = {
            name: f"BEGIN IF NEW.sequence < 1 OR NEW.author_id IS NULL THEN "
                  f"RAISE EXCEPTION '{name}' USING ERRCODE = '23514'; END IF; RETURN NEW; END;"
            for name in utility.GUARDS
        }
        self.functions = [
            [name, body, "plpgsql", 0, True, "f", False, False, "v", "u", None, False, False,
             index + 1, "CREATE OR REPLACE FUNCTION public." + name + "() ..."]
            for index, (name, body) in enumerate(self.function_bodies.items())
        ]
        self.expected_functions = patch.object(utility, "expected_function_bodies", return_value=self.function_bodies)
        self.expected_functions.start()
        self.addCleanup(self.expected_functions.stop)

    def execute(self, sql, params):
        self.assertTrue(sql.lstrip().startswith("SELECT "), "Catalog inspection must be read-only")
        self.queries.append((sql, params))
        if "FROM pg_trigger" in sql:
            rows = [(name, table, "BEFORE " + name.rsplit("_", 1)[1].upper(), "O", name, "RAISE EXCEPTION", index + 1 + self.oid_offset,
                     3 | {"insert": 4, "delete": 8, "update": 16}[name.rsplit("_", 1)[1]],
                     True, True, True, True)
                    for index, (name, table) in enumerate(utility.GUARDS.items())] if self.guarded else []
            if self.trigger_mutation and rows:
                rows = [list(row) for row in rows]
                index, value = self.trigger_mutation
                rows[0][index] = value
        elif "FROM pg_proc" in sql:
            self.assertIn("p.prosrc", sql)
            rows = self.functions if self.guarded else []
        elif "FROM pg_constraint" in sql:
            rows = self.constraints
        elif "FROM pg_index" in sql:
            rows = self.indexes
        else:
            self.fail("Unexpected catalog query")
        self.cursor.fetchall.return_value = rows

    def inspect(self):
        return utility.installed_schema(self.connection, guarded=self.guarded)

    def constraint(self, name="history_revision_positive"):
        return next(row for row in self.constraints if row[1] == name)

    def index(self, name="history_revision_no_branch"):
        return next(row for row in self.indexes if row[0] == name)

    def test_accepts_guarded_and_reversed_catalogs_and_records_definitions(self):
        for guarded in (False, True):
            self.guarded = guarded
            evidence = self.inspect()
            self.assertEqual(evidence["guarded"], guarded)
            self.assertEqual(evidence["constraints"], self.constraints)
            self.assertEqual(evidence["indexes"], self.indexes)
        self.connection.schema_editor.assert_called_with(collect_sql=True)
        self.assertEqual(len(self.queries), 8)

    def test_rejects_triggers_that_suppress_or_change_enforcement(self):
        self.guarded = True
        for index, value in ((7, 6), (7, 23), (8, False), (9, False), (10, False), (11, False)):
            with self.subTest(attribute=index, value=value):
                self.trigger_mutation = index, value
                with self.assertRaisesRegex(RuntimeError, "firing semantics"):
                    self.inspect()

    def test_reapply_comparison_ignores_only_ephemeral_function_oids(self):
        self.guarded = True
        before = self.inspect()
        self.oid_offset = 1000
        for row in self.functions:
            row[13] += self.oid_offset
        self.assertEqual(self.inspect(), before)

    def test_rejects_altered_function_bodies_even_with_raise_exception(self):
        self.guarded = True
        row = self.functions[0]
        original = row[1]
        for body in (
            original.replace("NEW.sequence < 1", "FALSE"),
            original.replace(" OR NEW.author_id IS NULL", ""),
            original.replace("RETURN NEW", "RETURN NULL"),
            original.replace("'23514'", "'23505'"),
            "BEGIN RETURN NEW; " + original.removeprefix("BEGIN "),
        ):
            with self.subTest(body=body):
                self.assertIn("RAISE EXCEPTION", body)
                row[1] = body
                with self.assertRaisesRegex(RuntimeError, "body differs from migration 0020"):
                    self.inspect()
        row[1] = original
        # The declaration formatter is deliberately irrelevant to prosrc equality.
        row[14] = "CREATE OR REPLACE FUNCTION public.reformatted() ..."
        row[1] = "\n" + original + "\n"
        self.inspect()

    def test_rejects_missing_replaced_overloaded_or_rebound_functions(self):
        self.guarded = True
        original = deepcopy(self.functions)
        mutations = (
            lambda rows: rows.pop(),
            lambda rows: rows[0].__setitem__(0, "replacement_function"),
            lambda rows: rows.append(deepcopy(rows[0])),
            lambda rows: rows[0].__setitem__(13, 9999),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                self.functions = deepcopy(original)
                mutate(self.functions)
                with self.assertRaises(RuntimeError):
                    self.inspect()
        self.functions = original
        for index, value in ((2, "sql"), (3, 1), (4, False), (5, "p"), (6, True),
                             (7, True), (8, "i"), (9, "s"), (10, ["search_path=other"]),
                             (11, True), (12, True)):
            with self.subTest(attribute=index):
                self.functions = deepcopy(original)
                self.functions[0][index] = value
                with self.assertRaisesRegex(RuntimeError, "execution attributes"):
                    self.inspect()

    def test_rejects_wrong_constraint_table_type_columns_and_flags(self):
        original = deepcopy(self.constraints)
        for index, value in ((0, utility.TABLES[0]), (2, "u"), (3, True), (4, True),
                             (5, False), (7, ["sequence"])):
            with self.subTest(index=index):
                self.constraints = deepcopy(original)
                self.constraint()[index] = value
                with self.assertRaises(RuntimeError):
                    self.inspect()

    def test_rejects_wrong_check_operator_literal_grouping_and_json_cast(self):
        cases = [
            ("history_revision_positive", "schema_version >= 1 OR sequence >= 1"),
            ("history_revision_positive", "schema_version >= 0 AND sequence >= 1"),
            ("history_revision_positive", "schema_version > 1 AND sequence >= 1"),
            ("history_revision_root_shape", "predecessor_id IS NULL AND (sequence = 1 OR predecessor_id IS NOT NULL) AND sequence > 1"),
            ("history_revision_reason_required", "reason = ''::text"),
            ("history_identity_shape", self.constraint("history_identity_shape")[9].replace("::jsonb", "::text")),
            ("history_identity_shape", self.constraint("history_identity_shape")[9].replace("'persisted'", "'Persisted'")),
        ]
        original = deepcopy(self.constraints)
        for name, expression in cases:
            with self.subTest(name=name, expression=expression):
                self.constraints = deepcopy(original)
                self.constraint(name)[9] = expression
                with self.assertRaises(RuntimeError):
                    self.inspect()

    def test_rejects_wrong_unique_key_order_and_null_semantics(self):
        row = self.constraint("history_revision_sequence_unique")
        row[7].reverse()
        with self.assertRaises(RuntimeError):
            self.inspect()
        row[7].reverse()
        row[6] = "UNIQUE NULLS NOT DISTINCT (stream_id, sequence)"
        with self.assertRaises(RuntimeError):
            self.inspect()

    def test_rejects_missing_and_duplicate_constraints(self):
        row = self.constraints.pop()
        with self.assertRaises(RuntimeError):
            self.inspect()
        self.constraints.extend([row, row])
        with self.assertRaises(RuntimeError):
            self.inspect()

    def test_rejects_wrong_index_table_key_predicate_and_flags(self):
        original = deepcopy(self.indexes)
        for index, value in ((1, False), (2, False), (3, False),
                             (5, "predecessor_id IS NULL"), (5, None), (5, "id IS NOT NULL"),
                             (6, utility.TABLES[0]), (7, ["id"]), (7, [None]), (8, "hash"),
                             (9, 2), (10, True), (11, False), (12, False)):
            with self.subTest(index=index, value=value):
                self.indexes = deepcopy(original)
                self.index()[index] = value
                with self.assertRaises(RuntimeError):
                    self.inspect()

    def test_rejects_unique_id_index_even_with_expected_predicate(self):
        self.index()[7] = ["id"]
        with self.assertRaisesRegex(RuntimeError, "history_revision_no_branch"):
            self.inspect()

    def test_rejects_wrong_identity_partial_predicates(self):
        original = deepcopy(self.indexes)
        for name in ("history_identity_source_unique", "history_identity_derived_unique"):
            with self.subTest(name=name):
                self.indexes = deepcopy(original)
                self.index(name)[5] = self.index(name)[5].replace("NOT ", "")
                with self.assertRaises(RuntimeError):
                    self.inspect()

    def test_rejects_missing_and_duplicate_indexes(self):
        row = self.indexes.pop()
        with self.assertRaises(RuntimeError):
            self.inspect()
        self.indexes.extend([row, row])
        with self.assertRaises(RuntimeError):
            self.inspect()

    def test_accepts_reordered_check_catalog_columns_but_not_extra_columns(self):
        self.constraint()[7].reverse()
        self.inspect()
        self.constraint()[7].append("reason")
        with self.assertRaises(RuntimeError):
            self.inspect()

    def test_foreign_keys_remain_required_and_initially_deferred(self):
        related = SimpleNamespace(_meta=SimpleNamespace(db_table="organizations_organization"))
        self.models["BoxHistoryStream"]._meta.local_fields[0].is_relation = True
        self.models["BoxHistoryStream"]._meta.local_fields[0].related_model = related
        row = [utility.TABLES[0], "synthetic_fk", "f", True, True, True, "recorded FK DDL",
               ["organization_id"], "organizations_organization", None]
        self.constraints.append(row)
        self.inspect()
        for index, value in ((4, False), (8, "other_table"), (7, ["original_box_id"])):
            with self.subTest(index=index):
                saved = row[index]
                row[index] = value
                with self.assertRaises(RuntimeError):
                    self.inspect()
                row[index] = saved

    def test_model_constraint_inventory_and_unsupported_options_fail_closed(self):
        constraint = self.models["BoxHistoryRevision"]._meta.constraints[0]
        constraint.include = ("id",)
        with self.assertRaisesRegex(RuntimeError, "uniqueness options"):
            self.inspect()
        constraint.include = ()
        constraint.name = "unexpected_name"
        with self.assertRaisesRegex(RuntimeError, "Phase 5A expectations"):
            self.inspect()


class ExpressionTests(unittest.TestCase):
    def test_normalizes_only_redundant_grouping_and_text_casts(self):
        normalize = lambda sql: utility.expression_tree(sql, {"reason"})
        self.assertEqual(normalize('NOT ("reason" = \'\')'), normalize("(NOT (((reason)::text = ''::text)))"))
        self.assertEqual(normalize("a = 1 AND (b = 2 AND c = 3)"), normalize("(a = 1 AND b = 2) AND c = 3"))
        self.assertNotEqual(normalize("(a = 1 OR b = 2) AND c = 3"), normalize("a = 1 OR (b = 2 AND c = 3)"))
        self.assertNotEqual(normalize("locator = '{}'::jsonb"), normalize("locator = '{}'::text"))
        self.assertNotEqual(normalize("reason = 'a b'"), normalize("reason = 'ab'"))
        self.assertNotEqual(normalize("reason = 'A'"), normalize("reason = 'a'"))
        self.assertEqual(normalize("reason = 'a''b'"), ("=", ("column", "reason"), ("string", "a'b")))

    def test_rejects_unknown_trailing_sql_and_malformed_expressions(self):
        for sql in ("a = 1; SELECT 1", "a = 1 -- comment", "a ~~ 'x'", "a = 1 nonsense",
                    "(a = 1", "a =", "a IS TRUE", "a::", "a = 1 /*comment*/"):
            with self.subTest(sql=sql), self.assertRaises(RuntimeError):
                utility.expression_tree(sql, set())


class DjangoCompilerTests(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec("django"), "Requires installed Django for offline migration rendering")
    def test_real_0020_renders_historical_function_bodies_without_database(self):
        script = r'''
import os
from pathlib import Path
import socket
import sys
from unittest.mock import patch
root = Path(sys.argv[1]).parent
sys.path.insert(0, str(root / "backend"))
sys.path.insert(0, sys.argv[1])
os.environ.update(PYTHON_DOTENV_DISABLED="1", POLYPBASE_TEST_POSTGRES="0",
                  DJANGO_DEBUG="1", DJANGO_SETTINGS_MODULE="config.preflight_settings")
with patch.object(socket, "socket", side_effect=AssertionError("No sockets")):
    import django
    django.setup()
    from django.db.backends.postgresql.base import DatabaseWrapper
    from django.db import connection
    import validate_history_revision_ci as utility
    postgres = DatabaseWrapper({"ENGINE": "django.db.backends.postgresql", "NAME": "offline"})
    with patch.object(postgres, "ensure_connection", side_effect=AssertionError("No PostgreSQL")), \
         patch.object(connection, "ensure_connection", side_effect=AssertionError("No SQLite")):
        bodies = utility.expected_function_bodies(postgres)
        assert set(bodies) == set(utility.GUARDS) and len(bodies) == 10
        assert "NEW.author_id IS NULL" in bodies["history_revision_insert"]
        assert "NEW.justifications IS DISTINCT FROM OLD.justifications" in bodies["history_revision_update"]
        assert "NEW.author_id IS DISTINCT FROM OLD.author_id" in bodies["history_revision_update"]
        assert "p.stream_id = NEW.stream_id" in bodies["history_revision_insert"]
        assert "jsonb_array_length(NEW.operations) > 100" in bodies["history_revision_shape_insert"]
        rows = [(name, body, "plpgsql", 0, True, "f", False, False, "v", "u", None, False, False,
                 index + 1, "normalized declaration") for index, (name, body) in enumerate(bodies.items())]
        triggers = [(name, utility.GUARDS[name], "", "O", name, "", index + 1)
                    for index, name in enumerate(bodies)]
        utility.validate_functions(postgres, rows, triggers, guarded=True)
print("All ten historical migration bodies rendered and compared offline")
'''
        result = subprocess.run(
            [sys.executable, "-I", "-B", "-c", script, str(UTILITY_PATH.parent)],
            capture_output=True, text=True, timeout=30, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("All ten historical migration bodies", result.stdout)

    @unittest.skipUnless(importlib.util.find_spec("django"), "Optional installed Django compiler check")
    def test_actual_model_conditions_compile_offline_and_match_catalog_fixtures(self):
        # Isolate settings.configure() from the test process and never import
        # application settings or models. Extract only the real Meta constraints.
        script = r'''
import ast
from pathlib import Path
import socket
import sys
from unittest.mock import patch
sys.path.insert(0, sys.argv[1])
import test_validate_history_revision_ci as tests
from django.conf import settings
settings.configure(INSTALLED_APPS=[], DATABASES={"default": {
    "ENGINE": "django.db.backends.postgresql", "NAME": "offline_never_connect",
}})
import django
django.setup()
from django.apps import apps
from django.db import connection, models
from django.db.models import Q
source = Path(sys.argv[1]).parent / "backend/apps/cultures/history_revision_models.py"
classes = {node.name: node for node in ast.parse(source.read_text(encoding="utf-8")).body
           if isinstance(node, ast.ClassDef)}
compiled_models = {}
for name, fixture in tests.fixture_models().items():
    meta = next(node for node in classes[name].body if isinstance(node, ast.ClassDef) and node.name == "Meta")
    expression = next(node.value for node in meta.body if isinstance(node, ast.Assign)
                      and any(isinstance(target, ast.Name) and target.id == "constraints" for target in node.targets))
    constraints = eval(compile(ast.Expression(expression), str(source), "eval"), {"models": models, "Q": Q})
    fields = {}
    for field in fixture._meta.local_fields:
        if field.name == "locator":
            value = models.JSONField()
        elif field.get_internal_type() == "CharField":
            value = models.CharField(max_length=1000)
        else:
            value = models.BigIntegerField(null=True)
        value.db_column = field.column
        fields[field.name] = value
    fields.update(__module__=__name__, Meta=type("Meta", (), {
        "app_label": "offline_catalog", "db_table": fixture._meta.db_table, "constraints": constraints,
    }))
    compiled_models[name] = type(name, (models.Model,), fields)
with patch.object(socket, "socket", side_effect=AssertionError("No sockets")), \
     patch.object(connection, "ensure_connection", side_effect=AssertionError("No DB connections")), \
     patch.object(apps, "get_model", side_effect=lambda app, name: compiled_models[name]):
    tests.utility.validate_catalog(connection, *tests.catalog_rows(tests.fixture_models()))
print("Actual Django compiler conditions matched offline catalog fixtures")
'''
        result = subprocess.run(
            [sys.executable, "-I", "-B", "-c", script, str(UTILITY_PATH.parent)],
            capture_output=True, text=True, timeout=30, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("matched offline catalog fixtures", result.stdout)


class HarnessSafetyTests(unittest.TestCase):
    def test_windows_bootstrap_refuses_before_django_import_or_path_change(self):
        original_import = __import__
        imports = []

        def guarded_import(name, *args, **kwargs):
            imports.append(name)
            if name == "django" or name.startswith(("django.", "config", "psycopg")):
                raise AssertionError("Windows bootstrap must not import Django or database code")
            return original_import(name, *args, **kwargs)

        before = list(sys.path)
        with patch.object(utility.sys, "platform", "win32"), patch("builtins.__import__", side_effect=guarded_import):
            with self.assertRaisesRegex(RuntimeError, "never on Windows"):
                utility.bootstrap()
        self.assertEqual(sys.path, before)
        self.assertEqual(imports, [])

    @contextmanager
    def rehearsal(self):
        # Literal identities keep the expected contract independent of utility constants.
        dna = ("cultures", "0019_box_dna_validation")
        storage = ("cultures", "0019_history_revision_storage")
        guards = ("cultures", "0020_history_revision_guards")
        merge = ("cultures", "0021_merge_dna_and_history")
        approved = {dna, storage, guards, merge, ("accounts", "0001_initial")}

        def migration(key):
            return SimpleNamespace(app_label=key[0], name=key[1])

        case = SimpleNamespace(
            state=set(), approved=approved, reverse_state=approved - {guards, merge},
            connection=Mock(), guards=Mock(),
            reapplication={key: ["cultures.0020_history_revision_guards", "cultures.0021_merge_dna_and_history"]
                           for key in ("planned", "started", "completed")},
        )
        management = ModuleType("django.core.management")

        def command(name, *args, **kwargs):
            if name == "migrate":
                self.assertEqual(args, ("cultures", "0019_history_revision_storage"))
                case.state = set(case.reverse_state)

        management.call_command = case.command = Mock(side_effect=command)
        executor_module = ModuleType("django.db.migrations.executor")
        case.executor = Mock()
        case.executor.migration_plan.return_value = [(migration(merge), True), (migration(guards), True)]
        executor_module.MigrationExecutor = Mock(return_value=case.executor)
        scoped = case.scoped = ModuleType("config.history_revision_ci_migrations")
        scoped.APPROVED = approved
        scoped.scoped_plan = Mock(side_effect=[
            [(migration(key), False) for key in (dna, storage, guards, merge)], [], [],
        ])
        scoped.migration_scope = Mock(return_value={"targets": [], "closure": [], "excluded": []})

        def migrate(connection, *, fresh, verbosity):
            case.state = set(approved)
            if not fresh:
                return case.reapplication
            return {"targets": [], "after": [".".join(key) for key in sorted(approved)], "excluded": []}

        scoped.migrate_scoped = Mock(side_effect=migrate)
        recorder_module = ModuleType("django.db.migrations.recorder")
        recorder = Mock()
        recorder.has_table.return_value = False
        recorder.applied_migrations.side_effect = lambda: set(case.state)
        recorder_module.MigrationRecorder = Mock(return_value=recorder)
        modules = {"django": ModuleType("django"), "django.core": ModuleType("django.core"),
                   "django.core.management": management, "django.db": ModuleType("django.db"),
                   "django.db.migrations": ModuleType("django.db.migrations"),
                   "django.db.migrations.executor": executor_module,
                   "django.db.migrations.recorder": recorder_module,
                   "config": ModuleType("config"), "config.history_revision_ci_migrations": scoped}
        with patch.dict(sys.modules, modules), patch.object(utility, "evidence") as evidence, \
                patch.object(utility, "installed_schema", return_value={}) as catalog, \
                patch.object(utility.os, "environ", {"HR_CI_EVIDENCE_DIR": "unused"}), \
                patch.object(Path, "write_text"):
            case.evidence, case.catalog = evidence, catalog
            yield case

    def test_exact_merged_rehearsal_is_accepted(self):
        with self.rehearsal() as case:
            utility.run(case.connection, case.guards)
            self.assertEqual(case.state, case.approved)
            self.assertEqual(case.scoped.migration_scope.call_count, 2)
            self.assertEqual(case.scoped.migrate_scoped.call_count, 2)
            self.assertEqual(case.catalog.call_args_list, [
                unittest.mock.call(case.connection, guarded=True),
                unittest.mock.call(case.connection, guarded=False),
                unittest.mock.call(case.connection, guarded=True),
            ])
            results = next(call.args[1] for call in case.evidence.call_args_list
                           if call.args[0] == "migration-results")
            self.assertTrue(results["empty_merge_reversed"])
            self.assertTrue(results["dna_storage_retained"])
            self.assertEqual(case.command.call_args_list[-1].args, ("test", *utility.TEST_LABELS))

    def test_unsafe_reverse_plans_refused_before_reverse_command(self):
        dna = ("cultures", "0019_box_dna_validation")
        storage = ("cultures", "0019_history_revision_storage")
        guards = ("cultures", "0020_history_revision_guards")
        merge = ("cultures", "0021_merge_dna_and_history")
        valid = [(*merge, True), (*guards, True)]
        for reverse in ([], [(*guards, True)], [(*merge, True)],
                        [(*merge, False), (*guards, True)], list(reversed(valid)),
                        valid + [(*dna, True)], valid + [(*storage, True)]):
            with self.subTest(reverse=reverse), self.rehearsal() as case:
                case.executor.migration_plan.return_value = [
                    (SimpleNamespace(app_label=app, name=name), backwards)
                    for app, name, backwards in reverse
                ]
                with self.assertRaisesRegex(RuntimeError, "Reverse plan must remove only the empty merge then 0020"):
                    utility.run(case.connection, case.guards)
                self.assertFalse(any(call.args[0] == "migrate" for call in case.command.call_args_list))

    def test_nonempty_merge_scope_rejected_before_reverse_command(self):
        with self.rehearsal() as case:
            case.scoped.migration_scope.side_effect = [
                {}, RuntimeError("integration merge must be empty"),
            ]
            with self.assertRaisesRegex(RuntimeError, "merge must be empty"):
                utility.run(case.connection, case.guards)
            case.executor.migration_plan.assert_not_called()
            self.assertFalse(any(call.args[0] == "migrate" for call in case.command.call_args_list))

    def test_reverse_recorder_must_retain_exact_storage_scope(self):
        for key in (("cultures", "0019_box_dna_validation"), ("cultures", "0019_history_revision_storage"),
                    ("accounts", "0001_initial")):
            with self.subTest(key=key), self.rehearsal() as case:
                case.reverse_state.remove(key)
                with self.assertRaisesRegex(RuntimeError, "Unexpected reverse migration state"):
                    utility.run(case.connection, case.guards)
                self.assertEqual(case.scoped.migrate_scoped.call_count, 1)
        with self.rehearsal() as case:
            case.reverse_state.add(("cultures", "0021_merge_dna_and_history"))
            with self.assertRaisesRegex(RuntimeError, "Unexpected reverse migration state"):
                utility.run(case.connection, case.guards)

    def test_reapplication_requires_exact_plan_and_callbacks(self):
        valid = ["cultures.0020_history_revision_guards", "cultures.0021_merge_dna_and_history"]
        for key in ("planned", "started", "completed"):
            for changed in ([], valid[:1], list(reversed(valid)),
                            valid + ["cultures.0019_box_dna_validation"]):
                with self.subTest(key=key, changed=changed), self.rehearsal() as case:
                    case.reapplication[key] = changed
                    with self.assertRaisesRegex(RuntimeError, "Reapplication must execute only 0020 then the empty merge"):
                        utility.run(case.connection, case.guards)
                    self.assertFalse(any(call.args[0] == "test" for call in case.command.call_args_list))


if __name__ == "__main__":
    unittest.main()
