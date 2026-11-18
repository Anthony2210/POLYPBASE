"""Database-free regressions for the Box 1902 disposable setup and cleanup."""

from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.db.migrations.graph import MigrationGraph
from django.db.migrations.loader import MigrationLoader
from django.test import SimpleTestCase

from . import test_box_1902_correction_v2_postgres as harness


class Box1902HarnessTests(SimpleTestCase):
    def baseline_plan(self, loader):
        suite = harness.Box1902CorrectionV2PostgreSQLTests
        executor = Mock(loader=loader)
        with patch.object(harness, "MigrationExecutor", return_value=executor) as factory, \
                patch.object(suite, "use_django_role") as role:
            suite.migrate_baseline_schema()
        factory.assert_called_once_with(harness.connection)
        role.assert_called_once_with(harness.contract.RUNTIME)
        targets = executor.migrate.call_args.args[0]
        executor.migrate.assert_called_once_with(targets)
        plan = set().union(*(set(loader.graph.forwards_plan(target)) for target in targets))
        self.assertIn(harness.PREVIOUS, plan)
        self.assertNotIn(harness.MIGRATION, plan)
        self.assertFalse(plan & set(loader.graph.backwards_plan(harness.MIGRATION)))
        return targets, plan

    def test_real_disk_graph_stops_before_capability_but_keeps_cultures_branches(self):
        loader = MigrationLoader(None)
        later = ("measurements", "0019_box_creation_initial_origin")
        self.assertIn(later, loader.graph.leaf_nodes())
        # Reproduce why replacing only the exact 0018 leaf no longer works.
        old_targets = [harness.PREVIOUS if node == harness.MIGRATION else node
                       for node in loader.graph.leaf_nodes()]
        old_plan = set().union(*(set(loader.graph.forwards_plan(node)) for node in old_targets))
        self.assertIn(harness.MIGRATION, old_plan)
        targets, plan = self.baseline_plan(loader)
        self.assertNotIn(later, plan)
        self.assertIn(("cultures", "0024_merge_donation_and_local_identity"), targets)
        for name in ("0019_global_box_identity", "0020_box_donation_proposal",
                     "0019_box_dna_validation", "0019_history_revision_storage",
                     "0020_history_revision_guards", "0021_merge_dna_and_history",
                     "0022_box_global_identity", "0023_box_local_id_alias"):
            with self.subTest(migration=name):
                self.assertIn(("cultures", name), plan)

    def test_future_cross_app_leaf_cannot_pull_capability_into_baseline(self):
        graph = MigrationGraph()
        dependent = ("other", "0001_depends_on_capability")
        independent = ("independent", "0001_initial")
        for node in (harness.PREVIOUS, harness.MIGRATION, dependent, independent):
            graph.add_node(node, Mock())
        graph.add_dependency(None, harness.MIGRATION, harness.PREVIOUS)
        graph.add_dependency(None, dependent, harness.MIGRATION)
        targets, plan = self.baseline_plan(SimpleNamespace(graph=graph))
        self.assertNotIn(dependent, targets)
        self.assertIn(independent, plan)

    def cleanup(self, provisioned, *, failure=None):
        suite = harness.Box1902CorrectionV2PostgreSQLTests
        original = {"NAME": "subculture_qa", "USER": "subculture_qa"}
        original_connection = original | {"OPTIONS": {"options": "original"}}
        config = {"NAME": "test_subculture_qa", "USER": "polypbase"}
        connection = SimpleNamespace(close=Mock(), settings_dict=dict(config))
        settings = SimpleNamespace(DATABASES={"default": dict(config)})
        with patch.object(harness, "connection", connection), \
                patch.object(harness, "settings", settings), \
                patch.multiple(suite, roles_provisioned=provisioned,
                               created_databases={harness.DATABASE},
                               original_settings=original,
                               original_connection_settings=original_connection, create=True), \
                patch.object(suite, "execute", side_effect=failure) as execute:
            if failure is None:
                suite.cleanup_cluster()
            else:
                with self.assertRaisesRegex(RuntimeError, "cleanup failed"):
                    suite.cleanup_cluster()
        connection.close.assert_called_once_with()
        self.assertEqual(settings.DATABASES["default"], original)
        self.assertEqual(connection.settings_dict, original_connection)
        return execute

    def test_cleanup_before_provisioning_drops_database_without_touching_roles(self):
        execute = self.cleanup(False)
        self.assertEqual(execute.call_count, 1)
        self.assertIn("DROP DATABASE", execute.call_args.args[0].as_string())
        self.assertEqual(execute.call_args.kwargs, {"database": "subculture_qa"})

    def test_cleanup_after_provisioning_restores_executor_login(self):
        execute = self.cleanup(True)
        self.assertEqual(execute.call_count, 2)
        execute.assert_called_with("ALTER ROLE box1902_executor LOGIN", database="subculture_qa")

    def test_cleanup_restores_settings_even_if_database_drop_fails(self):
        self.cleanup(False, failure=RuntimeError("cleanup failed"))

    def test_cleanup_restores_settings_even_if_role_reset_fails(self):
        self.cleanup(True, failure=[None, RuntimeError("cleanup failed")])
