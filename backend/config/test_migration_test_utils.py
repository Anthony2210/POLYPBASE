"""The historical rehearsal helper must never weaken the real capability."""

from importlib import import_module
from types import SimpleNamespace
from unittest.mock import patch

from django.db.migrations.executor import MigrationExecutor as DjangoMigrationExecutor
from django.db.migrations.loader import MigrationLoader
from django.test import SimpleTestCase

from .migration_test_utils import BOX_1902_CAPABILITY, MigrationExecutor


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
