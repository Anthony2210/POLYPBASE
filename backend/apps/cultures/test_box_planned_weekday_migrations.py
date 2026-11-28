"""Migration 0026 adds an empty planned weekday and reverses only once every value is null."""

from django.db import IntegrityError, connection, transaction
from django.db.migrations.exceptions import IrreversibleError
from django.test import TransactionTestCase

from config.migration_test_utils import MigrationExecutor, historical_targets

BEFORE = ("cultures", "0025_merge_local_id_hardening_and_donations")
AFTER = ("cultures", "0026_box_planned_weekday")


class BoxPlannedWeekdayMigrationTests(TransactionTestCase):
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
