"""Fail-closed interlocks; real PostgreSQL acceptance lives in the v2 module."""

from importlib import import_module
from io import StringIO
from types import SimpleNamespace
from unittest import skipUnless

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.db.migrations.exceptions import IrreversibleError
from django.test import SimpleTestCase, TestCase

from apps.measurements import box_1902_sql as capability

from .historical_2026 import cla_box_1902 as correction

migration = import_module("apps.measurements.migrations.0018_box_1902_preservation")
SCOPE = {"organization_id": 1, "box_id": 1902, "source_strain_id": 213,
         "source_species_id": 131, "target_strain_id": 285}


class Box1902RegressionDiscoveryTests(SimpleTestCase):
    """Keep the assertion-level v1-to-v2 mapping executable in SQLite CI too."""

    def test_all_legacy_methods_are_executed_by_the_genuine_role_class(self):
        from .test_box_1902_correction_v2_postgres import Box1902CorrectionV2PostgreSQLTests
        from .test_cla_box_1902 import ClaBox1902Tests
        from .test_cla_box_1902_postgres import ClaBox1902PostgreSQLTests
        from .test_box_1902_preservation_postgres import Box1902PreservationPostgreSQLTests

        mapped = set()
        for mixin, expected_count in ((ClaBox1902Tests, 33), (ClaBox1902PostgreSQLTests, 13),
                                      (Box1902PreservationPostgreSQLTests, 5)):
            names = {name for name in dir(mixin) if name.startswith("test_")}
            self.assertEqual(len(names), expected_count, mixin.__name__)
            for name in names:
                with self.subTest(origin=mixin.__name__, method=name):
                    method = getattr(mixin, name)
                    self.assertIs(getattr(Box1902CorrectionV2PostgreSQLTests, name), method)
                    self.assertFalse(getattr(method, "__unittest_skip__", False))
            self.assertFalse(mapped & names, "Legacy scenarios must not override one another")
            mapped.update(names)
        self.assertEqual(len(mapped), 51)

    def test_only_the_concrete_role_class_requests_the_postgresql_harness(self):
        from unittest import TestCase as UnitTestCase
        from .test_cla_box_1902 import ClaBox1902Tests
        from .test_cla_box_1902_postgres import ClaBox1902PostgreSQLTests
        from .test_box_1902_preservation_postgres import Box1902PreservationPostgreSQLTests

        for mixin in (ClaBox1902Tests, ClaBox1902PostgreSQLTests, Box1902PreservationPostgreSQLTests):
            self.assertFalse(issubclass(mixin, UnitTestCase))


class Box1902SafetyInterlockTests(SimpleTestCase):
    def test_exact_scope_is_required_before_any_database_access(self):
        for change in ({"organization_id": 2}, {"box_id": 1903}, {"source_strain_id": 214},
                       {"source_species_id": 132}, {"target_strain_id": 214},
                       {"target_strain_id": True}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                correction.build_plan(**(SCOPE | change))
            with self.subTest(apply=change), self.assertRaises(ValueError):
                correction.apply_plan(**(SCOPE | change), actor=SimpleNamespace(pk=1),
                                      expected_plan_hash="a" * 64)

    def test_apply_requires_explicit_actor_and_review_before_database_access(self):
        for actor, digest in ((None, "a" * 64), (SimpleNamespace(pk=None), "a" * 64),
                              (SimpleNamespace(pk=1), "")):
            with self.subTest(actor=actor, digest=digest):
                with self.assertRaisesMessage(ValueError, "An explicit actor and reviewed plan hash are required"):
                    correction.apply_plan(**SCOPE, actor=actor, expected_plan_hash=digest)

    def test_command_without_actor_or_hash_cannot_reach_a_writer(self):
        for options in ({"expected_plan_hash": "a" * 64}, {"actor_id": 1}):
            with self.subTest(options=options):
                with self.assertRaisesMessage(CommandError, "--apply requires a positive --actor-id"):
                    call_command("correct_cla_box_1902", **SCOPE, **options,
                                 apply=True, stdout=StringIO())

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

    def test_dry_run_cannot_claim_readiness_without_postgresql_capability(self):
        with self.assertRaisesMessage(ValueError, "independently provisioned PostgreSQL capability"):
            correction.build_plan(**SCOPE)

    def test_apply_and_legacy_review_hash_cannot_bypass_missing_capability(self):
        for digest in ("a" * 64, correction.fingerprint({"operation": "cla_box_1902_identity_20261103_v1"})):
            with self.subTest(digest=digest):
                with self.assertRaisesMessage(capability.ContractError, "Box 1902 requires PostgreSQL"):
                    correction.apply_plan(**SCOPE, actor=SimpleNamespace(pk=1), expected_plan_hash=digest)

    def test_command_reports_missing_capability_instead_of_applying(self):
        with self.assertRaisesMessage(CommandError, "Box 1902 requires PostgreSQL"):
            call_command("correct_cla_box_1902", **SCOPE, actor_id=1,
                         expected_plan_hash="a" * 64, apply=True, stdout=StringIO())
