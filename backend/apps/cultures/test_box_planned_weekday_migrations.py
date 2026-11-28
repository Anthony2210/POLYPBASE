"""Migration 0026 adds an empty planned weekday and reverses only once every value is null."""

from concurrent.futures import ThreadPoolExecutor
from importlib import import_module
from threading import Event
from unittest import skipUnless

from django.apps import apps as django_apps
from django.core.management import call_command
from django.db import (
    DatabaseError,
    IntegrityError,
    close_old_connections,
    connection,
    transaction,
)
from django.db.migrations.exceptions import IrreversibleError
from django.test import TransactionTestCase

from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain
from config import rollback_preflight
from config.disposable_postgres import DisposablePostgresFlushMixin
from config.migration_test_utils import MigrationExecutor, historical_targets

from .models import Box

BEFORE = ("cultures", "0025_merge_local_id_hardening_and_donations")
AFTER = ("cultures", "0026_box_planned_weekday")
MIGRATION = import_module("apps.cultures.migrations.0026_box_planned_weekday")


class BoxPlannedWeekdayMigrationTests(
    DisposablePostgresFlushMixin, TransactionTestCase
):
    def migrate(self, target):
        executor = MigrationExecutor(connection)
        targets = historical_targets(executor.loader, {"cultures"}, [target])
        executor.migrate(targets)
        return MigrationExecutor(connection).loader.project_state(targets).apps

    def box_columns(self):
        with connection.cursor() as cursor:
            return {
                column.name
                for column in connection.introspection.get_table_description(
                    cursor, "cultures_box"
                )
            }

    def triggers(self):
        if connection.vendor != "sqlite":
            return None
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger' ORDER BY name"
            )
            return [row[0] for row in cursor.fetchall()]

    def test_applies_without_data_changes_enforces_range_and_reverses(self):
        latest = MigrationExecutor(connection).loader.graph.leaf_nodes()
        self.addCleanup(lambda: MigrationExecutor(connection).migrate(latest))
        apps = self.migrate(BEFORE)
        self.assertNotIn("planned_weekday", self.box_columns())
        triggers = self.triggers()
        organization = apps.get_model("organizations", "Organization").objects.create(
            name="Weekday migration QA"
        )
        species = apps.get_model("taxonomy", "Species").objects.create(
            scientific_name="Aurelia weekday migration"
        )
        strain = apps.get_model("taxonomy", "Strain").objects.create(
            species=species, code="WDM-QA-1"
        )
        box = apps.get_model("cultures", "Box").objects.create(
            organization=organization,
            strain=strain,
            global_code="WDM-QA-1.001",
            box_number="001",
        )
        apps.get_model("measurements", "BiologicalMeasurement").objects.create(
            box=box,
            measured_on="2026-01-05",
            week_start="2026-01-05",
            polyp_count=0,
            ephyrae_count=0,
            strobila_count=0,
        )
        labels = (("cultures", "Box"), ("measurements", "BiologicalMeasurement"))
        snapshots = {
            label: list(apps.get_model(*label).objects.values()) for label in labels
        }

        apps = self.migrate(AFTER)
        Box = apps.get_model("cultures", "Box")
        self.assertIsNone(Box.objects.get(pk=box.pk).planned_weekday)
        for label, rows in snapshots.items():
            with self.subTest(model=label):
                self.assertEqual(
                    list(apps.get_model(*label).objects.values(*rows[0])), rows
                )
        self.assertEqual(self.triggers(), triggers)
        for value in (0, 8):
            with (
                self.subTest(value=value),
                transaction.atomic(),
                self.assertRaises(IntegrityError),
            ):
                Box.objects.filter(pk=box.pk).update(planned_weekday=value)
        Box.objects.filter(pk=box.pk).update(planned_weekday=7)

        # Direct executor use skips the preflight; the migration's own guard
        # still refuses before dropping anything.
        with self.assertRaisesRegex(
            IrreversibleError,
            r"planned weekday of 1 box\(es\)\..*explicit edits only.*inherited on transplant"
            r".*Export the current assignments",
        ):
            self.migrate(BEFORE)
        self.assertIn("planned_weekday", self.box_columns())
        self.assertIn(AFTER, MigrationExecutor(connection).loader.applied_migrations)
        self.assertEqual(Box.objects.get(pk=box.pk).planned_weekday, 7)

        Box.objects.filter(pk=box.pk).update(planned_weekday=None)
        apps = self.migrate(BEFORE)
        self.assertNotIn("planned_weekday", self.box_columns())
        self.assertEqual(self.triggers(), triggers)
        for label, rows in snapshots.items():
            with self.subTest(model=label, direction="backwards"):
                self.assertEqual(list(apps.get_model(*label).objects.values()), rows)


@skipUnless(
    connection.vendor == "postgresql", "Requires PostgreSQL isolation and table locks"
)
class BoxPlannedWeekdayRollbackGuardPostgresTests(
    DisposablePostgresFlushMixin, TransactionTestCase
):
    """Run in CI by scripts/run_backend_postgres_ci.py on a migrated clone."""

    def setUp(self):
        self.addCleanup(rollback_preflight.release_registry_lock, connection.alias)
        organization = Organization.objects.create(name="Weekday guard laboratory")
        species = Species.objects.create(scientific_name="Aurelia weekday guard")
        self.strain = Strain.objects.create(
            organization=organization, species=species, code="WDG-QA-1"
        )
        self.organization = organization

    def guard(self):
        with connection.schema_editor(atomic=False) as editor:
            MIGRATION.refuse_planned_weekday_loss(django_apps, editor)

    @staticmethod
    def sqlstate(error):
        cause = error.__cause__
        return getattr(cause, "sqlstate", None) or getattr(cause, "pgcode", None)

    def test_guard_requires_an_atomic_read_committed_migration(self):
        with self.assertRaisesRegex(IrreversibleError, "requires an atomic migration"):
            self.guard()
        with (
            self.assertRaisesRegex(IrreversibleError, "requires READ COMMITTED"),
            transaction.atomic(),
        ):
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            self.guard()

    def test_empty_guard_keeps_box_writers_out_until_the_rollback_ends(self):
        def blocked_write():
            close_old_connections()
            try:
                with transaction.atomic():
                    with connection.cursor() as cursor:
                        cursor.execute("SET LOCAL lock_timeout = '200ms'")
                    Box.objects.create(
                        organization=self.organization,
                        strain=self.strain,
                        global_code="WDG-QA-1.001",
                        box_number="001",
                    )
            except DatabaseError as error:
                return self.sqlstate(error)
            finally:
                connection.close()
            return "committed"

        with transaction.atomic():
            self.guard()  # Empty: passes and holds ACCESS EXCLUSIVE on cultures_box.
            with ThreadPoolExecutor(max_workers=1) as pool:
                self.assertEqual(pool.submit(blocked_write).result(timeout=15), "55P03")
        self.assertFalse(Box.objects.exists())

    def test_busy_box_table_is_refused_at_once_without_any_downgrade(self):
        holding, release = Event(), Event()

        def hold_reader():
            close_old_connections()
            try:
                with transaction.atomic():
                    list(
                        Box.objects.values_list("pk", flat=True)[:1]
                    )  # ACCESS SHARE until commit.
                    holding.set()
                    if not release.wait(timeout=10):
                        raise AssertionError("Competing reader was not released")
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(hold_reader)
            try:
                self.assertTrue(holding.wait(timeout=10))
                with self.assertRaises(DatabaseError) as caught, transaction.atomic():
                    self.guard()
                self.assertEqual(self.sqlstate(caught.exception), "55P03")
                # Through migrate, the preflight turns it into a clear refusal.
                with self.assertRaisesRegex(
                    IrreversibleError, "Protected tables are in use"
                ):
                    call_command("migrate", *BEFORE, verbosity=0, interactive=False)
            finally:
                release.set()
                future.result(timeout=15)
        self.assertIn(AFTER, MigrationExecutor(connection).loader.applied_migrations)
        with connection.cursor() as cursor:
            columns = {
                column.name
                for column in connection.introspection.get_table_description(
                    cursor, "cultures_box"
                )
            }
        self.assertIn("planned_weekday", columns)
