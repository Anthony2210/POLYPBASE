"""Subprocess-isolated real Django migration tests, SQLite memory only.

Run with Python 3.12 and Django 5.2.17. No project settings, dotenv, database
service, production identity or files are used by the child test process.
"""

from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]


def run_offline_tests():
    from copy import deepcopy
    from io import StringIO
    import socket
    from unittest.mock import patch

    def forbidden_socket(*args, **kwargs):
        raise AssertionError("Offline migration tests must never open sockets")

    socket.socket = forbidden_socket
    sys.path.insert(0, str(ROOT / "backend"))
    from django.conf import settings

    settings.configure(
        SECRET_KEY="offline-migration-tests",
        DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
        INSTALLED_APPS=[
            "apps.accounts", "apps.organizations", "apps.taxonomy", "apps.cultures",
            "apps.measurements", "apps.exports", "apps.audit", "django.contrib.admin",
            "django.contrib.auth", "django.contrib.contenttypes", "django.contrib.sessions",
        ],
        USE_TZ=True,
        DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
    )
    from django.db.backends.base.base import BaseDatabaseWrapper

    original_ensure = BaseDatabaseWrapper.ensure_connection

    def memory_only(connection):
        if connection.vendor != "sqlite" or connection.settings_dict["NAME"] != ":memory:":
            raise AssertionError("Offline tests permit only SQLite :memory:")
        return original_ensure(connection)

    BaseDatabaseWrapper.ensure_connection = memory_only
    import django

    django.setup()
    from django.apps import apps
    from django.core.exceptions import ImproperlyConfigured
    from django.core.management import call_command
    from django.core.management.commands import migrate as real_command
    from django.db import connections, migrations
    from django.db.migrations.executor import MigrationExecutor
    from django.db.migrations.loader import MigrationLoader
    from django.db.migrations.recorder import MigrationRecorder
    from django.db.models import AutoField, BigAutoField, SmallAutoField
    from django.db.models.signals import post_migrate, pre_migrate
    from django.test import override_settings
    from config import history_revision_ci_migrations as scoped

    connection = connections["default"]

    def reset_database():
        if connection.connection is not None:
            connection.connection.close()
        connection.connection = None
        connection.errors_occurred = False

    class ScopeTests(unittest.TestCase):
        def test_frozen_graph_exact_targets_counts_and_evidence(self):
            evidence = scoped.migration_scope(MigrationLoader(None))
            self.assertEqual(len(evidence["closure"]), 80)
            self.assertEqual(evidence["counts"], {
                "accounts": 8, "admin": 3, "audit": 5, "auth": 12, "contenttypes": 2,
                "cultures": 20, "exports": 1, "measurements": 17, "organizations": 2,
                "sessions": 1, "taxonomy": 9,
            })
            self.assertEqual(len(evidence["targets"]), 11)
            self.assertEqual(evidence["excluded"], ["measurements.0018_box_1902_preservation"])
            self.assertEqual(set(evidence["closure"]), set(scoped._names(scoped.APPROVED)))

        def test_missing_target(self):
            loader = MigrationLoader(None)
            loader.graph.nodes.pop(scoped.TARGETS[-1])
            with self.assertRaisesRegex(ImproperlyConfigured, "target is missing"):
                scoped.migration_scope(loader)

        def test_new_migration_cannot_expand_scope(self):
            loader = MigrationLoader(None)
            node = ("exports", "0002_unreviewed")
            loader.graph.add_node(node, migrations.Migration(node[1], node[0]))
            with self.assertRaisesRegex(ImproperlyConfigured, "inventory"):
                scoped.migration_scope(loader)

        def test_excluded_dependency(self):
            loader = MigrationLoader(None)
            loader.graph.add_dependency("offline-test", scoped.TARGETS[5], next(iter(scoped.EXCLUDED)))
            with self.assertRaisesRegex(ImproperlyConfigured, "excluded migration"):
                scoped.migration_scope(loader)

        def test_missing_dependency_changes_closure(self):
            loader = MigrationLoader(None)
            loader.graph.node_map[scoped.TARGETS[7]].parents.clear()
            with self.assertRaisesRegex(ImproperlyConfigured, "closure"):
                scoped.migration_scope(loader)

        def test_replacements_refused(self):
            loader = MigrationLoader(None)
            loader.replacements[scoped.TARGETS[0]] = object()
            with self.assertRaisesRegex(ImproperlyConfigured, "replacement"):
                scoped.migration_scope(loader)

        def test_historical_and_runtime_fields_match_including_m2m(self):
            historical = scoped._validate_models(MigrationLoader(None), connection)
            for name in ("Group_permissions", "User_groups", "User_user_permissions"):
                with self.subTest(model=name):
                    old_pk = historical.get_model("auth", name)._meta.pk
                    current_pk = apps.get_model("auth", name)._meta.pk
                    self.assertIs(type(old_pk), BigAutoField)
                    self.assertIs(type(current_pk), AutoField)
                    # SQLite normally gives both fields the same SQL type. Also
                    # exercise the distinct integer/bigint types PostgreSQL uses.
                    types = {**connection.data_types, "AutoField": "integer", "BigAutoField": "bigint"}
                    with patch.object(connection, "data_types", types):
                        self.assertEqual(old_pk.db_parameters(connection)["type"], "bigint")
                        self.assertEqual(current_pk.db_parameters(connection)["type"], "integer")
                        self.assertEqual(scoped._field_shape(old_pk, connection),
                                         scoped._field_shape(current_pk, connection))
            self.assertIn("auth.user_groups", scoped._model_shapes(apps, connection))

        def test_regular_pk_type_drift_is_not_normalized(self):
            field = apps.get_model("auth", "User")._meta.pk
            with patch.object(field, "get_internal_type", return_value="BigAutoField"):
                with self.assertRaisesRegex(ImproperlyConfigured, "fields differ"):
                    scoped._validate_models(MigrationLoader(None), connection)

        def test_implicit_m2m_pk_normalization_is_narrow(self):
            field = apps.get_model("auth", "User_groups")._meta.pk
            for attribute, value in (("auto_created", False), ("primary_key", False),
                                     ("null", True), ("db_index", True)):
                with self.subTest(attribute=attribute), patch.object(field, attribute, value):
                    with self.assertRaisesRegex(ImproperlyConfigured, "fields differ"):
                        scoped._validate_models(MigrationLoader(None), connection)
            small_pk = SmallAutoField(primary_key=True, auto_created=True)
            small_pk.set_attributes_from_name(field.name)
            small_pk.model = field.model
            self.assertNotEqual(scoped._field_shape(small_pk, connection),
                                scoped._field_shape(field, connection))

        def test_implicit_m2m_fk_target_drift_is_not_normalized(self):
            field = apps.get_model("auth", "User_groups")._meta.get_field("user")
            wrong_target = apps.get_model("auth", "Group")._meta.pk
            with patch.object(field, "foreign_related_fields", (wrong_target,)):
                with self.assertRaisesRegex(ImproperlyConfigured, "fields differ"):
                    scoped._validate_models(MigrationLoader(None), connection)

        def test_runtime_scalar_and_relationship_column_drift(self):
            fields = (
                apps.get_model("auth", "User")._meta.get_field("username"),
                apps.get_model("cultures", "Box")._meta.get_field("organization"),
                apps.get_model("auth", "User").groups.through._meta.get_field("user"),
                apps.get_model("auth", "User").groups.through._meta.pk,
            )
            for field in fields:
                with self.subTest(field=field.model._meta.label_lower + "." + field.name):
                    with patch.object(field, "column", "unreviewed_column"):
                        with self.assertRaisesRegex(ImproperlyConfigured, "fields differ"):
                            scoped._validate_models(MigrationLoader(None), connection)

        def test_runtime_field_definition_drift(self):
            field = apps.get_model("auth", "User")._meta.get_field("username")
            with patch.object(field, "max_length", 999):
                with self.assertRaisesRegex(ImproperlyConfigured, "fields differ"):
                    scoped._validate_models(MigrationLoader(None), connection)

        def test_m2m_table_drift(self):
            opts = apps.get_model("auth", "User").groups.through._meta
            with patch.object(opts, "db_table", "unreviewed_through_table"):
                with self.assertRaisesRegex(ImproperlyConfigured, "fields differ"):
                    scoped._validate_models(MigrationLoader(None), connection)

        def test_configuration_guards(self):
            for options in ({"MIGRATION_MODULES": {"exports": None}}, {"DATABASE_ROUTERS": [object()]}):
                with self.subTest(options=options), override_settings(**options):
                    with self.assertRaises(ImproperlyConfigured):
                        scoped.scoped_plan(connection)
            with patch.dict(connection.settings_dict["TEST"], {"MIGRATE": False}):
                with self.assertRaisesRegex(ImproperlyConfigured, "TEST.MIGRATE"):
                    scoped.scoped_plan(connection)
            with patch.dict(connection.settings_dict, {"NAME": "not-memory.sqlite3"}):
                with self.assertRaisesRegex(ImproperlyConfigured, "in memory"):
                    scoped.scoped_plan(connection)
            with patch.object(connection, "alias", "other"):
                with self.assertRaisesRegex(ImproperlyConfigured, "default connection"):
                    scoped.scoped_plan(connection)
            with patch.object(connection, "vendor", "mysql"):
                with self.assertRaisesRegex(ImproperlyConfigured, "backend"):
                    scoped.scoped_plan(connection)
            with patch.object(scoped.django, "VERSION", (5, 2, 18, "final", 0)):
                with self.assertRaisesRegex(ImproperlyConfigured, "5.2.17"):
                    scoped.scoped_plan(connection)

    class ExecutionTests(unittest.TestCase):
        def setUp(self):
            reset_database()

        def tearDown(self):
            reset_database()

        def test_real_fresh_command_signals_state_recorder_and_flush(self):
            signals = []

            def capture(sender, **kwargs):
                signals.append((kwargs.get("apps"), kwargs.get("plan"), kwargs["using"]))

            pre_migrate.connect(capture, weak=False)
            post_migrate.connect(capture, weak=False)
            original_executor = real_command.MigrationExecutor
            try:
                plan = scoped.scoped_plan(connection)
                self.assertFalse(MigrationRecorder(connection).has_table())
                self.assertEqual(len(plan), 80)
                evidence = scoped.migrate_scoped(connection, verbosity=0, fresh=True)
            finally:
                pre_migrate.disconnect(capture)
                post_migrate.disconnect(capture)
            self.assertIs(real_command.MigrationExecutor, original_executor)
            self.assertEqual(evidence["before"], [])
            self.assertEqual(set(evidence["after"]), set(evidence["closure"]))
            self.assertEqual(evidence["started"], evidence["completed"])
            self.assertEqual(set(evidence["planned"]), set(evidence["completed"]))
            self.assertEqual(len(evidence["completed"]), 80)
            self.assertTrue(signals)
            self.assertTrue(all(registry is not None and len(plan) == 80 and alias == "default"
                                for registry, plan, alias in signals))
            final_registry = signals[-1][0]
            self.assertIsNotNone(final_registry.get_model("auth", "User"))
            self.assertEqual(scoped._model_shapes(final_registry, connection),
                             scoped._model_shapes(apps, connection))
            self.assertIn("auth_user_groups", evidence["schema"])
            self.assertTrue(apps.get_model("auth", "Permission").objects.exists())
            self.assertEqual(scoped.scoped_plan(connection), [])
            with self.assertRaisesRegex(ImproperlyConfigured, "preapplied"):
                scoped.migrate_scoped(connection, verbosity=0, fresh=True)
            no_op = scoped.migrate_scoped(connection, verbosity=0, fresh=False)
            self.assertEqual(no_op["completed"], [])
            call_command("flush", interactive=False, verbosity=0)
            self.assertEqual(set(MigrationRecorder(connection).applied_migrations()), scoped.APPROVED)
            self.assertTrue(apps.get_model("auth", "Permission").objects.exists())
            scoped.verify_schema(connection)

        def test_clean_start_stays_full_but_ordinary_requests_are_scoped(self):
            executor = scoped._ScopedExecutor(connection, None, fresh=True)
            leaves = executor.loader.graph.leaf_nodes()
            full = executor.migration_plan(leaves, clean_start=True)
            self.assertEqual({(m.app_label, m.name) for m, _ in full}, scoped.APPROVED | scoped.EXCLUDED)
            selected = executor.migration_plan(leaves)
            self.assertEqual({(m.app_label, m.name) for m, _ in selected}, scoped.APPROVED)
            with self.assertRaisesRegex(ImproperlyConfigured, "unexpected migration target"):
                executor.migration_plan([scoped.TARGETS[0]])
            with self.assertRaisesRegex(ImproperlyConfigured, "supplied plan"):
                executor.migrate(leaves, plan=full)
            self.assertFalse(MigrationRecorder(connection).has_table())

        def test_fake_and_command_options_refused(self):
            executor = scoped._ScopedExecutor(connection, None, fresh=True)
            for option in ("fake", "fake_initial"):
                with self.subTest(option=option):
                    with self.assertRaisesRegex(ImproperlyConfigured, "fake execution"):
                        executor.migrate(list(scoped.TARGETS), **{option: True})
            for options in ({"fake": True}, {"fake_initial": True}, {"run_syncdb": True},
                            {"plan": True}, {"check_unapplied": True}, {"prune": True},
                            {"app_label": "cultures"}, {"interactive": True}):
                defaults = {"interactive": False, "verbosity": 0, **options}
                with self.subTest(options=options):
                    with self.assertRaises(ImproperlyConfigured):
                        call_command(scoped._ScopedCommand(fresh=True), **defaults)
            self.assertFalse(MigrationRecorder(connection).has_table())

        def test_unapproved_preapplied_history_refused(self):
            recorder = MigrationRecorder(connection)
            recorder.record_applied(*next(iter(scoped.EXCLUDED)))
            with self.assertRaisesRegex(ImproperlyConfigured, "unapproved"):
                scoped.scoped_plan(connection)

        def test_recorder_only_execution_cannot_pass_without_callbacks(self):
            def recorder_only(executor, targets, plan=None, state=None, **kwargs):
                for migration, _ in plan:
                    executor.recorder.record_applied(migration.app_label, migration.name)
                return state

            original_executor = real_command.MigrationExecutor
            with patch.object(MigrationExecutor, "migrate", recorder_only):
                with self.assertRaisesRegex(ImproperlyConfigured, "callbacks"):
                    scoped.migrate_scoped(connection, verbosity=0, fresh=True)
            self.assertIs(real_command.MigrationExecutor, original_executor)
            self.assertFalse(scoped._COMMAND_LOCK.locked())

        def test_fake_callback_refused(self):
            executor = scoped._ScopedExecutor(connection, None, fresh=True)
            migration = executor.loader.graph.nodes[scoped.TARGETS[0]]
            with self.assertRaisesRegex(ImproperlyConfigured, "fake migration callback"):
                executor._progress("apply_start", migration, fake=True)
            with self.assertRaisesRegex(ImproperlyConfigured, "reverse"):
                executor._progress("unapply_start", migration)

        def test_exact_recorder_after_execution_is_required(self):
            original = MigrationExecutor.migrate

            def incomplete(executor, *args, **kwargs):
                state = original(executor, *args, **kwargs)
                executor.recorder.record_unapplied(*scoped.TARGETS[0])
                return state

            with patch.object(MigrationExecutor, "migrate", incomplete):
                with self.assertRaisesRegex(ImproperlyConfigured, "final recorder"):
                    scoped.migrate_scoped(connection, verbosity=0, fresh=True)

        def test_separate_reverse_then_only_0020_reapplied(self):
            scoped.migrate_scoped(connection, verbosity=0, fresh=True)
            call_command("migrate", "cultures", "0019_history_revision_storage",
                         interactive=False, verbosity=0, stdout=StringIO())
            plan = scoped.scoped_plan(connection)
            self.assertEqual([(m.app_label, m.name, backwards) for m, backwards in plan],
                             [("cultures", "0020_history_revision_guards", False)])
            evidence = scoped.migrate_scoped(connection, verbosity=0, fresh=False)
            self.assertEqual(evidence["completed"], ["cultures.0020_history_revision_guards"])
            self.assertFalse(scoped.EXCLUDED & set(MigrationRecorder(connection).applied_migrations()))

    class SchemaTests(unittest.TestCase):
        @classmethod
        def setUpClass(cls):
            reset_database()
            scoped.migrate_scoped(connection, verbosity=0, fresh=True)

        @classmethod
        def tearDownClass(cls):
            reset_database()

        def test_missing_table(self):
            original = connection.introspection.table_names
            with patch.object(connection.introspection, "table_names",
                              side_effect=lambda *a, **k: [t for t in original(*a, **k) if t != "auth_user_groups"]):
                with self.assertRaisesRegex(ImproperlyConfigured, "missing managed table"):
                    scoped.verify_schema(connection)

        def test_missing_scalar_fk_and_m2m_columns(self):
            original = connection.introspection.get_table_description
            for table, column in (("auth_user", "username"), ("cultures_box", "organization_id"),
                                  ("auth_user_groups", "user_id")):
                def description(cursor, name):
                    return [item for item in original(cursor, name)
                            if name != table or item.name != column]

                with self.subTest(table=table), patch.object(
                    connection.introspection, "get_table_description", side_effect=description,
                ):
                    with self.assertRaisesRegex(ImproperlyConfigured, "live columns"):
                        scoped.verify_schema(connection)

        def test_wrong_fk_target_columns_and_missing_m2m_fk(self):
            original = connection.introspection.get_constraints
            for table, remove in (("cultures_box", False), ("auth_user_groups", False),
                                              ("auth_user_groups", True)):
                def constraints(cursor, name):
                    result = deepcopy(original(cursor, name))
                    if name == table:
                        key = next(key for key, value in result.items() if value.get("foreign_key"))
                        if remove:
                            del result[key]
                        else:
                            target, _ = result[key]["foreign_key"]
                            result[key]["foreign_key"] = (target, "wrong_target_column")
                    return result

                with self.subTest(table=table), patch.object(
                    connection.introspection, "get_constraints", side_effect=constraints,
                ):
                    with self.assertRaisesRegex(ImproperlyConfigured, "live foreign keys"):
                        scoped.verify_schema(connection)

        def test_extra_column_refused(self):
            original = connection.introspection.get_table_description

            def description(cursor, table):
                result = original(cursor, table)
                if table == "auth_user_groups":
                    result.append(result[0]._replace(name="unreviewed_column"))
                return result

            with patch.object(connection.introspection, "get_table_description", side_effect=description):
                with self.assertRaisesRegex(ImproperlyConfigured, "live columns"):
                    scoped.verify_schema(connection)

    suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromTestCase(cls)
                               for cls in (ScopeTests, ExecutionTests, SchemaTests))
    return unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful()


class OfflineMigrationTests(unittest.TestCase):
    def test_real_django_scoped_migrations_offline(self):
        result = subprocess.run(
            [sys.executable, "-B", str(Path(__file__).resolve()), "--offline-child"],
            cwd=ROOT, capture_output=True, text=True, timeout=420,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        print(result.stdout + result.stderr, end="")


if __name__ == "__main__":
    if sys.argv[1:] == ["--offline-child"]:
        raise SystemExit(0 if run_offline_tests() else 1)
    unittest.main()
