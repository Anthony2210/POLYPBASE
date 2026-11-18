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
        for label, rows in snapshots.items():
            model = apps.get_model(*label)
            historical_fields = tuple(rows[0])
            added_fields = {"global_identity_id"} if label == ("cultures", "Box") else set()
            self.assertEqual({field.attname for field in model._meta.concrete_fields},
                             set(historical_fields) | added_fields)
            self.assertEqual(list(model.objects.values(*historical_fields)), rows)
        # This historical Box has no verified creation receipt: 0022 must not
        # infer physical continuity or allocate an identity during the upgrade.
        self.assertIsNone(apps.get_model("cultures", "Box").objects.get(pk=box.pk).global_identity_id)
        self.assertFalse(apps.get_model("cultures", "BoxGlobalIdentity").objects.exists())
        self.assertFalse(apps.get_model("cultures", "GlobalBoxIdentity").objects.exists())
        self.assertFalse(apps.get_model("cultures", "BoxOwnershipEpisode").objects.exists())
        measurement = apps.get_model("measurements", "BiologicalMeasurement").objects.get(box_id=box.pk)
        self.assertEqual((measurement.polyp_count, measurement.ephyrae_count, measurement.strobila_count),
                         (0, 0, 0))
        self.assertFalse(apps.get_model("cultures", "BoxLocalIdAlias").objects.exists())
        self.assertFalse(apps.get_model("cultures", "BoxDnaValidation").objects.exists())
        for model_name in ("BoxHistoryStream", "BoxHistoryRevision", "BoxHistoryEventIdentity"):
            with self.subTest(model=model_name):
                self.assertFalse(apps.get_model("cultures", model_name).objects.exists())
        self.assertEqual(
            executor.loader.graph.leaf_nodes("cultures"),
            [("cultures", "0025_merge_local_id_hardening_and_donations")],
        )
