"""The DNA upgrade adds an empty table without changing existing evidence."""

from django.db import connection
from django.test import TransactionTestCase

from config.migration_test_utils import MigrationExecutor


class DnaValidationMigrationTests(TransactionTestCase):
    def test_additive_upgrade_preserves_existing_boxes_and_zero_measurements(self):
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        self.addCleanup(lambda: MigrationExecutor(connection).migrate(latest))
        before = [node for node in latest if node[0] != "cultures"] + [
            ("cultures", "0018_historicalboxmovement"),
        ]
        executor.migrate(before)
        apps = executor.loader.project_state(before).apps
        organization = apps.get_model("organizations", "Organization").objects.create(name="DNA migration QA")
        species = apps.get_model("taxonomy", "Species").objects.create(scientific_name="Aurelia migration DNA")
        strain = apps.get_model("taxonomy", "Strain").objects.create(species=species, code="DNA-MIGRATION")
        box = apps.get_model("cultures", "Box").objects.create(
            organization=organization, strain=strain, global_code="DNA-MIGRATION.001", box_number="001",
        )
        apps.get_model("measurements", "BiologicalMeasurement").objects.create(
            box=box, measured_on="2026-01-05", week_start="2026-01-05", polyp_count=0,
            ephyrae_count=0, strobila_count=0,
        )
        labels = (("cultures", "Box"), ("measurements", "BiologicalMeasurement"))
        snapshots = {label: list(apps.get_model(*label).objects.values()) for label in labels}
        executor = MigrationExecutor(connection)
        executor.migrate(latest)
        apps = executor.loader.project_state(latest).apps
        for label, rows in snapshots.items():
            self.assertEqual(list(apps.get_model(*label).objects.values()), rows)
        self.assertFalse(apps.get_model("cultures", "BoxDnaValidation").objects.exists())
