"""Fail-closed interlocks; real PostgreSQL acceptance lives in the v2 module."""

from importlib import import_module
from types import SimpleNamespace
from unittest import skipUnless

from django.db import connection
from django.db.migrations.exceptions import IrreversibleError
from django.test import SimpleTestCase, TestCase

from apps.measurements import box_1902_sql as capability

migration = import_module("apps.measurements.migrations.0018_box_1902_preservation")


class Box1902RegressionDiscoveryTests(SimpleTestCase):
    def test_preservation_mixin_runs_in_genuine_role_suite(self):
        from .test_box_1902_correction_v2_postgres import Box1902CorrectionV2PostgreSQLTests
        from .test_box_1902_preservation_postgres import Box1902PreservationPostgreSQLTests

        names = [name for name in dir(Box1902PreservationPostgreSQLTests) if name.startswith("test_")]
        self.assertEqual(len(names), 5)
        for name in names:
            with self.subTest(method=name):
                method = getattr(Box1902PreservationPostgreSQLTests, name)
                self.assertIs(getattr(Box1902CorrectionV2PostgreSQLTests, name), method)
                self.assertFalse(getattr(method, "__unittest_skip__", False))

    def test_reverse_refuses_every_backend_before_any_sql(self):
        for vendor in ("postgresql", "sqlite"):
            with self.subTest(vendor=vendor):
                editor = SimpleNamespace(connection=SimpleNamespace(vendor=vendor))
                with self.assertRaisesMessage(IrreversibleError, "provenance and permanent evidence validation"):
                    migration.backwards(None, editor)


@skipUnless(connection.vendor == "sqlite", "SQLite fail-closed checks; PostgreSQL uses the genuine-role v2 suite")
class Box1902SQLiteSafetyTests(TestCase):
    def test_sqlite_forward_migration_installs_no_substitute_capability(self):
        with self.assertNumQueries(0):
            migration.forwards(None, connection.schema_editor(atomic=False))

    def test_sqlite_cannot_attest_or_install_postgresql_topology(self):
        for installation in (False, True):
            with self.subTest(installation=installation), self.assertNumQueries(0):
                with self.assertRaisesMessage(capability.ContractError, "Box 1902 requires PostgreSQL"):
                    capability.assert_topology(connection, installation=installation)
        with self.assertNumQueries(0):
            with self.assertRaisesMessage(capability.ContractError, "Box 1902 requires PostgreSQL"):
                capability.attest(connection)
