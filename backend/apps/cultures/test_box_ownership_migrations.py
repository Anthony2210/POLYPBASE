from datetime import date
from uuid import uuid4

from django.db import connection, migrations
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.recorder import MigrationRecorder
from django.test import SimpleTestCase, TransactionTestCase

from config.migration_test_utils import MigrationExecutor, historical_targets


BEFORE = ("cultures", "0018_historicalboxmovement")
AFTER = ("cultures", "0019_global_box_identity")
NEW_MODELS = {"GlobalBoxIdentity", "BoxOwnershipEpisode"}


class BoxIdentityMigrationGraphTests(SimpleTestCase):
    def test_migration_is_additive_without_data_operations(self):
        migration = MigrationLoader(None).disk_migrations[AFTER]
        self.assertEqual(
            set(migration.dependencies),
            {BEFORE, ("organizations", "0002_organization_portable_id")},
        )
        created = {op.name for op in migration.operations if isinstance(op, migrations.CreateModel)}
        self.assertEqual(created, NEW_MODELS)
        others = [op for op in migration.operations if not isinstance(op, migrations.CreateModel)]
        # Only a reverse guard: no AddField/AlterField on Box, no SQL, no backfill.
        self.assertEqual(len(others), 1)
        self.assertIsInstance(others[0], migrations.RunPython)
        self.assertIs(others[0].code, migrations.RunPython.noop)
        self.assertIs(migration.operations[-1], others[0])


class BoxIdentityMigrationTests(TransactionTestCase):
    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        self.latest = executor.loader.graph.leaf_nodes()
        executor.migrate(self.latest)
        self.addCleanup(lambda: MigrationExecutor(connection).migrate(self.latest))

    def migrate_to(self, cultures):
        targets = historical_targets(MigrationLoader(None), {"cultures"}, [cultures])
        executor = MigrationExecutor(connection)
        executor.migrate(targets)
        return MigrationExecutor(connection).loader.project_state(targets).apps

    def legacy_rows(self, apps):
        Organization = apps.get_model("organizations", "Organization")
        source = Organization.objects.create(name="Legacy A", slug="legacy-own-a")
        destination = Organization.objects.create(name="Legacy B", slug="legacy-own-b")
        species = apps.get_model("taxonomy", "Species").objects.create(scientific_name="Legacy own species")
        strain = apps.get_model("taxonomy", "Strain").objects.create(
            species=species, organization=source, code="LEG-OWN",
        )
        Box = apps.get_model("cultures", "Box")
        boxes = [
            Box.objects.create(
                organization=source if index < 2 else destination, strain=strain,
                global_code=f"LEG-OWN.{index}", box_number=str(index),
                status=("active", "inactive", "pending_review")[index], entered_on=date(2020, 1, 1),
            )
            for index in range(3)
        ]
        apps.get_model("measurements", "BiologicalMeasurement").objects.create(
            box=boxes[0], measured_on=date(2020, 1, 6), week_start=date(2020, 1, 6), polyp_count=0,
        )
        apps.get_model("cultures", "BoxTransferImport").objects.create(
            format_version="polypbase.box_transfer.v1", source_transfer_id="legacy",
            source_organization_name="Legacy A", source_global_code="LEG-OWN.0",
            destination_organization=destination, created_box=boxes[2],
        )
        return source, boxes

    def snapshot(self, apps):
        labels = [("cultures", "Box"), ("measurements", "BiologicalMeasurement"),
                  ("cultures", "BoxTransferImport"), ("organizations", "Organization")]
        return {label: list(apps.get_model(*label).objects.order_by("pk").values()) for label in labels}

    def test_upgrade_preserves_legacy_rows_and_assigns_no_identity(self):
        old_apps = self.migrate_to(BEFORE)
        self.legacy_rows(old_apps)
        before = self.snapshot(old_apps)
        new_apps = self.migrate_to(AFTER)
        self.assertEqual(self.snapshot(new_apps), before)
        for name in NEW_MODELS:
            self.assertFalse(new_apps.get_model("cultures", name).objects.exists())
        restored = self.migrate_to(BEFORE)
        self.assertEqual(self.snapshot(restored), before)
        self.assertNotIn(AFTER, MigrationRecorder(connection).applied_migrations())

    def test_reverse_refuses_to_drop_recorded_identities(self):
        old_apps = self.migrate_to(BEFORE)
        source, boxes = self.legacy_rows(old_apps)
        new_apps = self.migrate_to(AFTER)
        identity = new_apps.get_model("cultures", "GlobalBoxIdentity").objects.create(global_box_id=uuid4())
        Episode = new_apps.get_model("cultures", "BoxOwnershipEpisode")
        Episode.objects.create(identity=identity, sequence=1, organization_id=source.pk, local_box_id=boxes[0].pk)
        with self.assertRaisesMessage(RuntimeError, "Refusing to drop"):
            self.migrate_to(BEFORE)
        self.assertIn(AFTER, MigrationRecorder(connection).applied_migrations())
        self.assertEqual(Episode.objects.count(), 1)
