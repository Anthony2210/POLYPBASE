"""The historical rehearsal helper must never weaken the real capability."""

from importlib import import_module
from types import SimpleNamespace
from unittest.mock import patch

from django.db.migrations.executor import MigrationExecutor as DjangoMigrationExecutor
from django.db.migrations.loader import MigrationLoader
from django.test import SimpleTestCase

from .migration_test_utils import BOX_1902_CAPABILITY, MigrationExecutor, historical_targets


class HistoricalTargetsTests(SimpleTestCase):
    def setUp(self):
        self.loader = MigrationLoader(None)

    def test_incompatible_direct_dependency_is_rejected(self):
        historical = [
            ("cultures", "0015_box_merged_into_box_previous_global_code_and_more"),
            ("organizations", "0001_initial"),
        ]
        for targets in (historical, historical[::-1]):
            with self.subTest(targets=targets), self.assertRaisesRegex(
                ValueError, r"organizations\.0001_initial.*organizations\.0002_organization_portable_id"
            ):
                historical_targets(self.loader, {"cultures", "organizations"}, targets)

    def test_incompatible_transitive_dependency_is_rejected(self):
        cultures = ("cultures", "0018_historicalboxmovement")
        required = ("taxonomy", "0009_localstrainnumbercounter")
        self.assertNotIn(required, self.loader.graph.node_map[cultures].parents)
        self.assertIn(required, self.loader.graph.forwards_plan(cultures))
        with self.assertRaisesRegex(
            ValueError, r"taxonomy\.0008_strain_canonical_global_identity.*taxonomy\.0009_localstrainnumbercounter"
        ):
            historical_targets(self.loader, {"cultures", "taxonomy"}, [
                cultures, ("taxonomy", "0008_strain_canonical_global_identity"),
            ])

    def test_compatible_multi_app_dependencies_are_accepted(self):
        historical = [
            ("cultures", "0018_historicalboxmovement"),
            ("organizations", "0002_organization_portable_id"),
            ("taxonomy", "0009_localstrainnumbercounter"),
        ]
        targets = historical_targets(self.loader, {node[0] for node in historical}, historical)
        self.assertTrue(set(historical) <= set(targets))
        self.assertIn(("sharing", "0001_initial"), targets)

    def test_older_dependency_does_not_conflict_with_a_newer_explicit_target(self):
        historical = [
            ("cultures", "0009_portable_lineage"),
            ("organizations", "0002_organization_portable_id"),
        ]
        targets = historical_targets(self.loader, {node[0] for node in historical}, historical)
        self.assertTrue(set(historical) <= set(targets))
        self.assertNotIn(("sharing", "0001_initial"), targets)

    def test_existing_caller_targets_are_preserved(self):
        cultures_14 = ("cultures", "0014_box_movement_week_matches_moved_at")
        measurements_13 = ("measurements", "0013_historical_temperature_receipt")
        measurements_5 = ("measurements", "0005_biologicalmeasurement_week_start")
        organizations_1 = ("organizations", "0001_initial")
        taxonomy_nodes = sorted(node for node in self.loader.graph.nodes if node[0] == "taxonomy")
        cases = [
            [("cultures", "0013_box_movement_kind_week_links"), measurements_13],
            [("cultures", "0011_protect_subculture_history"), measurements_13],
            [("cultures", "0009_portable_lineage"), measurements_5],
            [organizations_1, cultures_14, measurements_13],
            [("taxonomy", "0007_localstrainidentity_provenance_code_assignment"), cultures_14, measurements_13],
        ]
        # Portable lineage visits both culture states and every taxonomy target.
        for cultures in ("0008_transfer_v2_source_package", "0009_portable_lineage"):
            for taxonomy in taxonomy_nodes:
                cases.append([measurements_5, ("cultures", cultures), organizations_1, taxonomy])
        # The three taxonomy modules exercise before/after states from 0003 to 0007.
        for taxonomy in taxonomy_nodes[2:7]:
            cases.append([cultures_14, measurements_13, taxonomy, organizations_1])
        for historical in cases:
            with self.subTest(historical=historical):
                apps = {node[0] for node in historical}
                targets = historical_targets(self.loader, apps, historical)
                expected = [node for node in self.loader.graph.leaf_nodes() if node[0] not in apps | {"sharing"}]
                self.assertEqual(targets, expected + historical)
                closure = set().union(*(set(self.loader.graph.forwards_plan(node)) for node in targets))
                for target in historical:
                    self.assertEqual(
                        {node for node in closure if node[0] == target[0]},
                        {node for node in self.loader.graph.forwards_plan(target) if node[0] == target[0]},
                    )


class HistoricalMigrationExecutorTests(SimpleTestCase):
    def executor(self, vendor):
        loader = MigrationLoader(None)
        originals = dict(loader.graph.nodes)

        def initialize(executor, connection, progress_callback):
            executor.loader = loader

        with patch.object(DjangoMigrationExecutor, "__init__", initialize):
            executor = MigrationExecutor(SimpleNamespace(vendor=vendor))
        return executor, originals

    def test_only_the_sqlite_capability_operations_are_excluded(self):
        executor, originals = self.executor("sqlite")
        self.assertEqual(set(executor.loader.graph.nodes), set(originals))
        for node, migration in originals.items():
            actual = executor.loader.graph.nodes[node]
            if node == BOX_1902_CAPABILITY:
                self.assertIsNot(actual, migration)
                self.assertEqual(actual.operations, [])
                self.assertEqual(actual.dependencies, migration.dependencies)
                self.assertEqual(len(migration.operations), 1)
                self.assertIs(executor.loader.disk_migrations[node], actual)
            else:
                self.assertIs(actual, migration)

    def test_postgresql_keeps_the_real_irreversible_capability(self):
        executor, originals = self.executor("postgresql")
        for node, migration in originals.items():
            self.assertIs(executor.loader.graph.nodes[node], migration)
        migration = import_module("apps.measurements.migrations.0018_box_1902_preservation")
        operation = executor.loader.graph.nodes[BOX_1902_CAPABILITY].operations[0]
        self.assertIs(operation.code, migration.forwards)
        self.assertIs(operation.reverse_code, migration.backwards)

    def test_new_loaders_still_have_the_real_operations_after_sqlite_rehearsal(self):
        self.executor("sqlite")
        loader = MigrationLoader(None)
        self.assertEqual(len(loader.graph.nodes[BOX_1902_CAPABILITY].operations), 1)
