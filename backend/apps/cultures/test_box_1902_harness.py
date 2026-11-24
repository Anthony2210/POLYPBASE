"""Database-free regressions for the Box 1902 disposable setup and cleanup."""

from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, call, patch

from django.db.migrations.graph import MigrationGraph
from django.db.migrations.loader import MigrationLoader
from django.test import SimpleTestCase

from . import test_box_1902_correction_v2_postgres as harness
from . import test_box_1902_preservation_postgres as preservation


class Box1902HarnessTests(SimpleTestCase):
    def test_inherited_preservation_test_uses_runtime_login(self):
        suite = harness.Box1902CorrectionV2PostgreSQLTests
        case = suite("test_ordinary_identity_change_still_invalidates")
        db = MagicMock(in_atomic_block=False)
        db.settings_dict = {"USER": harness.contract.EXECUTOR}

        def switch(user):
            db.settings_dict["USER"] = user

        def reading_statuses():
            self.assertEqual(db.settings_dict["USER"], harness.contract.RUNTIME)
            return {1: "pending_review"}

        with patch.object(harness, "connection", db), \
                patch.object(preservation, "connection", db), \
                patch.object(case, "use_django_role", side_effect=switch) as use_role, \
                patch.object(case, "reading_ids", [1], create=True), \
                patch.object(case, "_reading_statuses", side_effect=reading_statuses):
            case.test_ordinary_identity_change_still_invalidates()

        self.assertEqual(use_role.call_args_list, [call(harness.contract.RUNTIME), call(harness.contract.EXECUTOR)])
        db.cursor.return_value.__enter__.return_value.execute.assert_called_once()

    def test_role_restores_previous_login_after_failure(self):
        case = harness.Box1902CorrectionV2PostgreSQLTests()
        db = SimpleNamespace(settings_dict={"USER": harness.contract.EXECUTOR}, in_atomic_block=False)
        with patch.object(harness, "connection", db), patch.object(case, "use_django_role") as use_role:
            with self.assertRaisesMessage(RuntimeError, "fixture failed"), case.role(harness.contract.RUNTIME):
                raise RuntimeError("fixture failed")
        self.assertEqual(use_role.call_args_list, [call(harness.contract.RUNTIME), call(harness.contract.EXECUTOR)])

    def test_role_refuses_login_switch_in_atomic_block(self):
        case = harness.Box1902CorrectionV2PostgreSQLTests()
        db = SimpleNamespace(settings_dict={"USER": harness.contract.EXECUTOR}, in_atomic_block=True)
        with patch.object(harness, "connection", db), patch.object(case, "use_django_role") as use_role:
            with self.assertRaisesMessage(AssertionError, "before switching genuine logins"):
                with case.role(harness.contract.RUNTIME):
                    self.fail("Fixture body must not run")
            with case.role(harness.contract.EXECUTOR):
                use_role.assert_not_called()
        use_role.assert_not_called()

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
        self.assertIn(("cultures", "0025_merge_local_id_hardening_and_donations"), targets)
        for name in ("0019_global_box_identity", "0020_box_donation_proposal",
                     "0019_box_dna_validation", "0019_history_revision_storage",
                     "0020_history_revision_guards", "0021_merge_dna_and_history",
                     "0022_box_global_identity", "0023_box_local_id_alias",
                                          "0024_merge_donation_and_local_identity", "0024_local_id_guard_search_path"):
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

    def test_privileged_install_alone_leaves_initial_origin_unapplied(self):
        loader = MigrationLoader(None)
        _, baseline = self.baseline_plan(loader)
        installed = baseline | set(loader.graph.forwards_plan(harness.MIGRATION))
        self.assertEqual(set(loader.disk_migrations) - installed,
                         {("measurements", "0019_box_creation_initial_origin")})
        complete = set().union(*(set(loader.graph.forwards_plan(node))
                                 for node in loader.graph.leaf_nodes()))
        self.assertEqual(complete, set(loader.disk_migrations))

    def privileged_setup(self, *, missing=False, backwards=False):
        loader = MigrationLoader(None)
        leaves = loader.graph.leaf_nodes()
        events = []
        capability = Mock()
        capability.migrate.side_effect = lambda targets: events.append(("capability", targets))
        current = Mock(loader=loader)
        current.migration_plan.return_value = [(loader.disk_migrations[harness.MIGRATION], backwards)]
        current.migrate.side_effect = lambda targets: events.append(("current", targets))
        applied = set(loader.disk_migrations)
        if missing:
            applied.remove(("measurements", "0019_box_creation_initial_origin"))
        final = Mock(applied_migrations=applied, disk_migrations=loader.disk_migrations)
        with patch.object(harness, "MigrationExecutor", side_effect=[capability, current]) as factory, \
                patch.object(harness, "MigrationLoader", return_value=final) as load, \
                patch.object(harness.Box1902CorrectionV2PostgreSQLTests, "use_django_role") as role:
            if backwards or missing:
                with self.assertRaisesRegex(AssertionError, "reverse historical|complete disk"):
                    harness.Box1902CorrectionV2PostgreSQLTests.migrate_privileged_schema()
            else:
                harness.Box1902CorrectionV2PostgreSQLTests.migrate_privileged_schema()
            self.assertEqual(factory.call_count, 2)
            role.assert_not_called()  # Keep the already verified migrator/SET ROLE session.
            current.migration_plan.assert_called_once_with(leaves)
            if backwards:
                current.migrate.assert_not_called()
                load.assert_not_called()
            else:
                self.assertEqual(events, [("capability", [harness.MIGRATION]), ("current", leaves)])
                load.assert_called_once_with(harness.connection)
                final.check_consistent_history.assert_called_once_with(harness.connection)

    def test_privileged_setup_finishes_and_attests_full_graph(self):
        self.privileged_setup()

    def test_privileged_setup_rejects_missing_migration_before_template_creation(self):
        self.privileged_setup(missing=True)

    def test_privileged_setup_never_reverses_history(self):
        self.privileged_setup(backwards=True)

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
