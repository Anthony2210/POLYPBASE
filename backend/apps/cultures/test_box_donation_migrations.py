from datetime import date
from uuid import uuid4

from django.db import connection, migrations
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.recorder import MigrationRecorder
from django.test import SimpleTestCase, TransactionTestCase
from django.utils import timezone

from config.migration_test_utils import MigrationExecutor, historical_targets


BEFORE = ("cultures", "0019_global_box_identity")
AFTER = ("cultures", "0020_box_donation_proposal")


class BoxDonationMigrationGraphTests(SimpleTestCase):
    def test_migration_is_additive_without_data_operations(self):
        migration = MigrationLoader(None).disk_migrations[AFTER]
        self.assertIn(BEFORE, migration.dependencies)
        created = {op.name for op in migration.operations if isinstance(op, migrations.CreateModel)}
        self.assertEqual(created, {"BoxDonationProposal"})
        others = [op for op in migration.operations if not isinstance(op, migrations.CreateModel)]
        # Only a reverse guard: no Box/identity field change, no SQL, no backfill.
        self.assertEqual(len(others), 1)
        self.assertIsInstance(others[0], migrations.RunPython)
        self.assertIs(others[0].code, migrations.RunPython.noop)


class BoxDonationMigrationTests(TransactionTestCase):
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

    def rows(self, apps):
        Organization = apps.get_model("organizations", "Organization")
        source = Organization.objects.create(name="Legacy donor", slug="legacy-don-a")
        destination = Organization.objects.create(name="Legacy receiver", slug="legacy-don-b")
        species = apps.get_model("taxonomy", "Species").objects.create(scientific_name="Legacy don species")
        strain = apps.get_model("taxonomy", "Strain").objects.create(
            species=species, organization=source, code="LEG-DON",
        )
        box = apps.get_model("cultures", "Box").objects.create(
            organization=source, strain=strain, global_code="LEG-DON.1", box_number="1",
            status="active", entered_on=date(2020, 1, 1),
        )
        apps.get_model("measurements", "BiologicalMeasurement").objects.create(
            box=box, measured_on=date(2020, 1, 6), week_start=date(2020, 1, 6), polyp_count=0,
        )
        identity = apps.get_model("cultures", "GlobalBoxIdentity").objects.create(global_box_id=uuid4())
        episode = apps.get_model("cultures", "BoxOwnershipEpisode").objects.create(
            identity=identity, sequence=1, organization_id=source.pk, local_box_id=box.pk,
        )
        return source, destination, box, identity, episode

    def snapshot(self, apps):
        labels = [("cultures", "Box"), ("measurements", "BiologicalMeasurement"),
                  ("cultures", "GlobalBoxIdentity"), ("cultures", "BoxOwnershipEpisode"),
                  ("organizations", "Organization")]
        return {label: list(apps.get_model(*label).objects.order_by("pk").values()) for label in labels}

    def test_upgrade_preserves_rows_and_creates_no_proposal(self):
        old_apps = self.migrate_to(BEFORE)
        self.rows(old_apps)
        before = self.snapshot(old_apps)
        new_apps = self.migrate_to(AFTER)
        self.assertEqual(self.snapshot(new_apps), before)
        self.assertFalse(new_apps.get_model("cultures", "BoxDonationProposal").objects.exists())
        restored = self.migrate_to(BEFORE)
        self.assertEqual(self.snapshot(restored), before)
        self.assertNotIn(AFTER, MigrationRecorder(connection).applied_migrations())

    def test_reverse_refuses_to_drop_recorded_proposals(self):
        old_apps = self.migrate_to(BEFORE)
        source, destination, box, identity, episode = self.rows(old_apps)
        new_apps = self.migrate_to(AFTER)
        user = new_apps.get_model("auth", "User").objects.create(username="legacy-don-admin", email="legacy-don-admin@example.test")
        Proposal = new_apps.get_model("cultures", "BoxDonationProposal")
        now = timezone.now()
        Proposal.objects.create(
            source_organization_id=source.pk, destination_organization_id=destination.pk,
            source_box_id=box.pk, identity_id=identity.pk, source_episode_id=episode.pk,
            source_ownership_sequence=1, source_institution_name="Legacy donor",
            destination_institution_name="Legacy receiver", source_box_code="LEG-DON.1",
            source_strain_code="LEG-DON", species_scientific_name="Legacy don species",
            global_strain_id=uuid4(), history_verified_by_id=user.pk, history_verified_at=now,
            history_snapshot_version="1.0", history_sha256="0" * 64, source_polyp_state_revision=1,
            created_by_id=user.pk, created_at=now, request_id=uuid4(), request_fingerprint="0" * 64,
        )
        with self.assertRaisesMessage(RuntimeError, "Refusing to drop"):
            self.migrate_to(BEFORE)
        self.assertIn(AFTER, MigrationRecorder(connection).applied_migrations())
        self.assertEqual(Proposal.objects.count(), 1)
