from datetime import date
from decimal import Decimal
from uuid import uuid4

from django.conf import settings
from django.db import connection, migrations
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.recorder import MigrationRecorder
from django.test import SimpleTestCase, TransactionTestCase
from django.utils import timezone


BEFORE = ("cultures", "0008_transfer_v2_source_package")
AFTER = ("cultures", "0009_portable_lineage")
ORGANIZATIONS_INITIAL = ("organizations", "0001_initial")
TAXONOMY_INITIAL = ("taxonomy", "0001_initial")


class PortableLineageMigrationGraphTests(SimpleTestCase):
    def test_dependencies_are_minimal(self):
        loader = MigrationLoader(None)
        migration = loader.disk_migrations[AFTER]
        self.assertEqual(set(migration.dependencies), {BEFORE, ORGANIZATIONS_INITIAL})
        ancestors = set(loader.graph.forwards_plan(AFTER))
        self.assertEqual({node for node in ancestors if node[0] == "organizations"}, {ORGANIZATIONS_INITIAL})
        self.assertEqual({node for node in ancestors if node[0] == "taxonomy"}, {TAXONOMY_INITIAL})

    def test_operations_are_schema_only_without_eager_identities(self):
        migration = MigrationLoader(None).disk_migrations[AFTER]
        self.assertEqual(len(migration.operations), 3)
        self.assertEqual(
            {operation.name for operation in migration.operations if isinstance(operation, migrations.CreateModel)},
            {"PortableLineageNode", "PortableLineageEdge"},
        )
        added = [operation for operation in migration.operations if isinstance(operation, migrations.AddField)]
        self.assertEqual(len(added), 1)
        self.assertEqual((added[0].model_name, added[0].name), ("transferitem", "lineage_snapshot"))
        self.assertTrue(added[0].field.null)
        self.assertFalse(added[0].field.editable)
        self.assertIsNone(added[0].field.default)
        self.assertFalse(any(isinstance(operation, (migrations.RunPython, migrations.RunSQL)) for operation in migration.operations))


class PortableLineageMigrationTests(TransactionTestCase):
    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        self.latest = executor.loader.graph.leaf_nodes()
        # Historical tests may leave a partial state; always restore it before targeting history.
        executor.migrate(self.latest)
        self.addCleanup(self.restore_latest)
        self.other_targets = [
            node for node in self.latest if node[0] not in {"cultures", "organizations", "taxonomy"}
        ]

    def restore_latest(self):
        MigrationExecutor(connection).migrate(self.latest)

    def migrate_to(self, cultures, taxonomy=TAXONOMY_INITIAL):
        targets = self.other_targets + [cultures, ORGANIZATIONS_INITIAL, taxonomy]
        executor = MigrationExecutor(connection)
        executor.migrate(targets)
        executor = MigrationExecutor(connection)
        executor.loader.check_consistent_history(connection)
        return executor.loader.project_state(targets).apps

    def create_historical_rows(self, apps):
        organization = apps.get_model("organizations", "Organization").objects.create(
            name="Historical lineage laboratory", slug="historical-lineage", notes="Keep organization",
        )
        actor = apps.get_model(settings.AUTH_USER_MODEL).objects.create(
            username="historical-lineage-author", email="historical-lineage-author@example.test",
        )
        species = apps.get_model("taxonomy", "Species").objects.create(scientific_name="Historical lineage species")
        origin = apps.get_model("taxonomy", "Origin").objects.create(
            description="Original provenance", event_date=date(2001, 2, 3),
        )
        strain = apps.get_model("taxonomy", "Strain").objects.create(
            species=species, origin=origin, code="LEGACY-LINEAGE", number=0, notes="Original strain",
        )
        zone = apps.get_model("cultures", "ThermalZone").objects.create(
            organization=organization, name="Historical tank", target_temperature_c=Decimal("0.0"),
            capacity=0, salinity_psu=Decimal("0.00"), notes="Original zone",
        )
        Box = apps.get_model("cultures", "Box")
        boxes = [
            Box.objects.create(
                organization=organization, strain=strain, origin=origin,
                thermal_zone=zone if index == 0 else None,
                global_code=f"LEGACY-LINEAGE.{index}", local_code=f"LOCAL-{index}", box_number=str(index),
                status=("active", "pending_review", "inactive", "inactive", "active")[index],
                entered_on=date(2002, 3, 4), volume_liters=Decimal("0.00") if index == 0 else None,
                stop_reason="Historical stop" if index == 2 else "",
                stop_reason_missing_from_history=index == 3,
                deactivated_on=date(2003, 4, 5) if index == 2 else None,
                notes=f"Original box {index}",
            )
            for index in range(5)
        ]
        Box.objects.filter(pk=boxes[0].pk).update(created_on=date(2000, 1, 2))
        event = apps.get_model("cultures", "SubcultureEvent").objects.create(
            parent_box=boxes[0], event_date=date(2004, 5, 6), user=actor,
            reason="Original reason", notes="Original event",
        )
        for index, relationship_type in enumerate(
            ("subculture", "sexual_reproduction", "historical_import", "other"), start=1,
        ):
            apps.get_model("cultures", "BoxLineage").objects.create(
                parent_box=boxes[0], child_box=boxes[index], relationship_type=relationship_type,
                subculture_event=event if index == 1 else None, notes=f"Original edge {index}",
            )
        timestamp = timezone.now()
        apps.get_model("cultures", "BoxLocation").objects.create(
            box=boxes[0], thermal_zone=zone, starts_at=timestamp,
            end_date_unknown=True, notes="Original location",
        )
        apps.get_model("cultures", "BoxMovement").objects.create(
            box=boxes[0], to_thermal_zone=zone, user=actor, moved_at=timestamp, notes="Original movement",
        )
        item_id = uuid4()
        for index in range(2):
            envelope = apps.get_model("cultures", "TransferEnvelope").objects.create(
                transfer_id=uuid4(), source_organization=organization,
                source_institution_id=uuid4(), source_institution_name="Frozen source",
                destination_institution_id=uuid4() if index else None,
                destination_institution_name="Frozen destination" if index else "",
                protocol_major=2, protocol_minor=0, created_at=timestamp, created_by=actor,
            )
            apps.get_model("cultures", "TransferItem").objects.create(
                envelope=envelope, item_id=item_id, source_box=boxes[index],
                source_box_code=f"FROZEN-BOX-{index}", source_strain_code="FROZEN-STRAIN",
                species_scientific_name="Frozen species", global_strain_id=uuid4(),
                declared_polyp_quantity=index,
            )

    def snapshot(self, apps, labels):
        return {
            label: list(apps.get_model(*label).objects.order_by("pk").values())
            for label in labels
        }

    def test_populated_upgrade_and_reverse_preserve_all_existing_values(self):
        old_apps = self.migrate_to(BEFORE)
        self.create_historical_rows(old_apps)
        labels = [
            ("organizations", "Organization"), tuple(settings.AUTH_USER_MODEL.split(".")),
            ("taxonomy", "Species"), ("taxonomy", "Origin"), ("taxonomy", "Strain"),
            ("cultures", "ThermalZone"), ("cultures", "Box"), ("cultures", "BoxLineage"),
            ("cultures", "SubcultureEvent"), ("cultures", "BoxLocation"), ("cultures", "BoxMovement"),
            ("cultures", "TransferEnvelope"), ("cultures", "TransferItem"),
        ]
        before = self.snapshot(old_apps, labels)
        new_apps = self.migrate_to(AFTER)
        after = self.snapshot(new_apps, labels)
        for row in after[("cultures", "TransferItem")]:
            self.assertIsNone(row.pop("lineage_snapshot"))
        self.assertEqual(after, before)
        for name in ("PortableLineageNode", "PortableLineageEdge"):
            self.assertFalse(new_apps.get_model("cultures", name).objects.exists())
        applied = MigrationRecorder(connection).applied_migrations()
        self.assertIn(AFTER, applied)
        self.assertNotIn(("organizations", "0002_organization_portable_id"), applied)
        self.assertEqual({node for node in applied if node[0] == "taxonomy"}, {TAXONOMY_INITIAL})
        self.assertEqual(
            list(new_apps.get_model("cultures", "TransferItem").objects.order_by("pk").values_list("declared_polyp_quantity", flat=True)),
            [0, 1],
        )
        restored_apps = self.migrate_to(BEFORE)
        self.assertEqual(self.snapshot(restored_apps, labels), before)
        self.assertNotIn(AFTER, MigrationRecorder(connection).applied_migrations())

    def test_historical_taxonomy_targets_remain_reachable_with_portable_schema(self):
        loader = MigrationLoader(None)
        taxonomy_targets = sorted(
            node for node in loader.disk_migrations if node[0] == "taxonomy"
        )
        for target in taxonomy_targets:
            with self.subTest(target=target):
                apps = self.migrate_to(AFTER, taxonomy=target)
                applied = MigrationRecorder(connection).applied_migrations()
                expected_taxonomy = {
                    node for node in loader.graph.forwards_plan(target) if node[0] == "taxonomy"
                }
                self.assertEqual({node for node in applied if node[0] == "taxonomy"}, expected_taxonomy)
                self.assertIn(AFTER, applied)
                self.assertNotIn(("organizations", "0002_organization_portable_id"), applied)
                self.assertFalse(apps.get_model("cultures", "PortableLineageNode").objects.exists())
                self.assertFalse(apps.get_model("cultures", "PortableLineageEdge").objects.exists())
