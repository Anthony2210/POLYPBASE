"""The DNA upgrade adds an empty table without changing existing evidence."""

from django.db import connection
from django.test import TransactionTestCase

from config.migration_test_utils import MigrationExecutor, historical_targets


class DnaValidationMigrationTests(TransactionTestCase):
    def test_additive_upgrade_preserves_existing_boxes_and_zero_measurements(self):
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        self.addCleanup(lambda: MigrationExecutor(connection).migrate(latest))
        before = historical_targets(executor.loader, {"cultures"}, [
            ("cultures", "0018_historicalboxmovement"),
        ])
        executor.migrate(before)
        apps = executor.loader.project_state(before).apps
        for model_name in ("BoxDnaValidation", "BoxHistoryStream", "BoxHistoryRevision", "BoxHistoryEventIdentity"):
            with self.subTest(model=model_name), self.assertRaises(LookupError):
                apps.get_model("cultures", model_name)
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
        # Every retained field is unchanged, including the zero counts. Columns
        # added later are checked separately; no other column may appear.
        additive = {("cultures", "Box"): {"global_identity_id": None}, ("measurements", "BiologicalMeasurement"): {}}
        for label, rows in snapshots.items():
            with self.subTest(model=label):
                current = list(apps.get_model(*label).objects.values())
                self.assertEqual([{key: row[key] for key in before_row} for row, before_row in zip(current, rows)],
                                 rows)
                self.assertEqual(len(current), len(rows))
                for row, before_row in zip(current, rows):
                    # 0022 leaves Global ID NULL ("not initialized") for a Box
                    # without a creation receipt or subculture allocation.
                    self.assertEqual({key: value for key, value in row.items() if key not in before_row},
                                     additive[label])
        measurement = apps.get_model("measurements", "BiologicalMeasurement").objects.get()
        self.assertEqual((measurement.polyp_count, measurement.ephyrae_count, measurement.strobila_count), (0, 0, 0))
        self.assertFalse(apps.get_model("cultures", "BoxLocalIdAlias").objects.exists())
        self.assertFalse(apps.get_model("cultures", "BoxDnaValidation").objects.exists())
        for model_name in ("BoxHistoryStream", "BoxHistoryRevision", "BoxHistoryEventIdentity"):
            with self.subTest(model=model_name):
                self.assertFalse(apps.get_model("cultures", model_name).objects.exists())
        self.assertEqual(
            executor.loader.graph.leaf_nodes("cultures"),
            [("cultures", "0024_local_id_guard_search_path")],
        )
