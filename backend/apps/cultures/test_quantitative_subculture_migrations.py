"""Additive upgrades preserve legacy measurements, events and relationships."""

from datetime import date

from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone


class QuantitativeSubcultureMigrationTests(TransactionTestCase):
    def test_optional_upgrade_preserves_complete_evidence_and_guards(self):
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        self.addCleanup(lambda: MigrationExecutor(connection).migrate(latest))
        before = [node for node in latest if node[0] != "cultures"] + [("cultures", "0011_protect_subculture_history")]
        executor.migrate(before)
        apps = executor.loader.project_state(before).apps
        org = apps.get_model("organizations", "Organization").objects.create(name="Optional upgrade QA")
        species = apps.get_model("taxonomy", "Species").objects.create(scientific_name="Optional Aurelia")
        strain = apps.get_model("taxonomy", "Strain").objects.create(species=species, code="OPTIONAL")
        Box = apps.get_model("cultures", "Box")
        parent = Box.objects.create(organization=org, strain=strain, global_code="OPTIONAL.001", box_number="001")
        child = Box.objects.create(organization=org, strain=strain, global_code="OPTIONAL.002", box_number="002")
        Event = apps.get_model("cultures", "SubcultureEvent")
        Allocation = apps.get_model("cultures", "SubcultureAllocation")
        event = Event.objects.create(parent_box=parent, event_date=date(2026, 1, 1), occurred_at=timezone.now(),
            parent_state_sequence=1, parent_polyp_count_before=100, allocated_polyp_count=30,
            parent_polyp_count_after=70, parent_state_snapshot={"polyp_count": 100}, author_name="Original author")
        Allocation.objects.create(event=event, child_box=child, position=0, allocated_polyps=30,
            child_global_code=child.global_code)
        event_rows = list(Event.objects.values())
        allocation_rows = list(Allocation.objects.values())
        executor = MigrationExecutor(connection)
        executor.migrate(latest)
        apps = executor.loader.project_state(latest).apps
        Event = apps.get_model("cultures", "SubcultureEvent")
        Allocation = apps.get_model("cultures", "SubcultureAllocation")
        self.assertEqual(list(Event.objects.values()), event_rows)
        self.assertEqual(list(Allocation.objects.values()), allocation_rows)
        partial = Event.objects.create(parent_box_id=parent.pk, event_date=date(2026, 1, 2), occurred_at=timezone.now(),
            parent_state_sequence=2, parent_polyp_count_before=70, parent_state_snapshot={"polyp_count": 70})
        unknown_child = apps.get_model("cultures", "Box").objects.create(organization_id=org.pk,
            strain_id=strain.pk, global_code="OPTIONAL.003", box_number="003")
        allocation = Allocation.objects.create(event=partial, child_box=unknown_child, position=0,
            allocated_polyps=None, child_global_code=unknown_child.global_code)
        self.assertIsNone(allocation.allocated_polyps)
        for values in ({"allocated_polyp_count": 0}, {"parent_polyp_count_after": 70},
                       {"parent_state_sequence": None}, {"parent_state_snapshot": None},
                       {"parent_polyp_count_before": None}):
            with self.subTest(values=values), self.assertRaises(IntegrityError), transaction.atomic():
                fields = dict(parent_box_id=parent.pk, event_date=date(2026, 1, 3), occurred_at=timezone.now(),
                    parent_state_sequence=3, parent_polyp_count_before=70, parent_state_snapshot={"polyp_count": 70})
                fields.update(values)
                Event.objects.create(**fields)
        for table, field, pk in (("cultures_subcultureevent", "parent_polyp_count_before", partial.pk),
                                 ("cultures_subcultureallocation", "allocated_polyps", allocation.pk)):
            with self.subTest(table=table), self.assertRaises(IntegrityError), transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(f"UPDATE {table} SET {field} = 0 WHERE id = %s", [pk])
    def test_populated_upgrade_preserves_legacy_values_without_inventing_occurrence(self):
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        self.addCleanup(lambda: MigrationExecutor(connection).migrate(latest))
        other_targets = [node for node in latest if node[0] not in {"cultures", "measurements"}]
        before = other_targets + [("cultures", "0009_portable_lineage"), ("measurements", "0005_biologicalmeasurement_week_start")]
        executor.migrate(before)
        old_apps = executor.loader.project_state(before).apps
        org = old_apps.get_model("organizations", "Organization").objects.create(name="Legacy quantitative QA")
        species = old_apps.get_model("taxonomy", "Species").objects.create(scientific_name="Legacy Aurelia")
        strain = old_apps.get_model("taxonomy", "Strain").objects.create(species=species, code="LEGACY")
        zone = old_apps.get_model("cultures", "ThermalZone").objects.create(organization=org, name="Legacy zone")
        Box = old_apps.get_model("cultures", "Box")
        parent = Box.objects.create(organization=org, strain=strain, thermal_zone=zone, global_code="LEGACY.001", box_number="001")
        child = Box.objects.create(organization=org, strain=strain, thermal_zone=zone, global_code="LEGACY.002", box_number="002")
        event = old_apps.get_model("cultures", "SubcultureEvent").objects.create(parent_box=parent,
            event_date=date(2001, 1, 1), reason="Legacy reason", notes="No known count")
        old_apps.get_model("cultures", "BoxLineage").objects.create(parent_box=parent, child_box=child, subculture_event=event)
        old_apps.get_model("measurements", "BiologicalMeasurement").objects.create(box=child,
            measured_on=date(2001, 1, 1), week_start=date(2001, 1, 1), polyp_count=0, ephyrae_count=0,
            strobila_count=0, notes="Legacy synthetic entry remains untouched")
        labels = [("cultures", "Box"), ("cultures", "SubcultureEvent"), ("cultures", "BoxLineage"), ("measurements", "BiologicalMeasurement")]
        snapshots = {label: list(old_apps.get_model(*label).objects.order_by("pk").values()) for label in labels}
        executor = MigrationExecutor(connection)
        executor.migrate(latest)
        new_apps = executor.loader.project_state(latest).apps
        for label, rows in snapshots.items():
            new_rows = list(new_apps.get_model(*label).objects.order_by("pk").values())
            self.assertEqual([{key: row[key] for key in rows[0]} for row in new_rows], rows)
        new_event = new_apps.get_model("cultures", "SubcultureEvent").objects.get(pk=event.pk)
        for field in ("occurred_at", "parent_state_snapshot", "parent_state_sequence",
                      "parent_polyp_count_before", "parent_polyp_count_after", "allocated_polyp_count"):
            self.assertIsNone(getattr(new_event, field))
        self.assertFalse(new_apps.get_model("cultures", "SubcultureAllocation").objects.exists())
        self.assertFalse(new_apps.get_model("cultures", "BoxCodeNamespace").objects.exists())
        self.assertIsNone(new_apps.get_model("measurements", "BiologicalMeasurement").objects.get().polyp_state_sequence)
        executor = MigrationExecutor(connection)
        executor.migrate(before)
        reversed_apps = executor.loader.project_state(before).apps
        for label, rows in snapshots.items():
            self.assertEqual(list(reversed_apps.get_model(*label).objects.order_by("pk").values()), rows)
