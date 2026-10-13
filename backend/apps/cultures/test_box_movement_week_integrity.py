"""Weekly keys must remain correct even when writes bypass model.save()."""

from datetime import date, datetime
from unittest.mock import patch

from django.core import serializers
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature

from .models import BoxMovement
from .movement_corrections import correct_box_movement
from .services import move_box_to_thermal_zone
from .test_box_movement_weekly import MovementFixtureMixin, paris


class BoxMovementWeekIntegrityTests(MovementFixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.box = self.create_box()

    def ordinary(self, *, moved_at=None, week=date(2026, 3, 2), **kwargs):
        return BoxMovement(
            box=self.box, to_thermal_zone=self.zone_b,
            moved_at=moved_at or paris(2026, 3, 3),
            kind=BoxMovement.Kind.ORDINARY, iso_week_start=week, **kwargs,
        )

    def assert_rejected_without_changes(self, write, error=IntegrityError):
        before = self.snapshot(self.box)
        with self.assertRaises(error), transaction.atomic():
            write()
        self.assertEqual(self.snapshot(self.box), before)

    def test_bulk_create_rejects_deliberately_wrong_non_null_week(self):
        self.assert_rejected_without_changes(lambda: BoxMovement.objects.bulk_create([
            self.ordinary(week=date(2026, 3, 9)),
        ]))

    def test_bulk_create_cannot_use_inconsistent_keys_for_same_actual_week(self):
        # batch_size=1 proves an earlier successful batch is rolled back too.
        self.assert_rejected_without_changes(lambda: BoxMovement.objects.bulk_create([
            self.ordinary(),
            self.ordinary(moved_at=paris(2026, 3, 8, 23), week=date(2026, 3, 9)),
        ], batch_size=1))

    def test_bulk_create_correct_duplicate_keys_still_fail_uniqueness(self):
        self.assert_rejected_without_changes(lambda: BoxMovement.objects.bulk_create([
            self.ordinary(), self.ordinary(moved_at=paris(2026, 3, 8, 23)),
        ], batch_size=1))

    def test_normal_creation_derives_the_week_and_valid_bulk_creation_succeeds(self):
        movement = self.ordinary(week=date(2026, 3, 9))
        movement.save()
        movement.refresh_from_db()
        self.assertEqual(movement.iso_week_start, date(2026, 3, 2))
        BoxMovement.objects.bulk_create([
            self.ordinary(moved_at=paris(2026, 3, 9), week=date(2026, 3, 9)),
        ])
        self.assertEqual(self.box.movements.count(), 2)

    @override_settings(TIME_ZONE="Pacific/Honolulu")
    def test_database_expression_matches_paris_boundaries_not_default_timezone(self):
        instants = [
            ("2026-03-01T22:59:59+00:00", date(2026, 2, 23)),
            ("2026-03-01T23:00:00+00:00", date(2026, 3, 2)),
            ("2026-03-02T08:30:00+09:00", date(2026, 3, 2)),
            ("2026-03-29T21:59:59+00:00", date(2026, 3, 23)),
            ("2026-03-29T22:00:00+00:00", date(2026, 3, 30)),
            ("2026-10-25T22:59:59+00:00", date(2026, 10, 19)),
            ("2026-10-25T23:00:00+00:00", date(2026, 10, 26)),
            ("2025-12-31T12:00:00+01:00", date(2025, 12, 29)),
            ("2027-01-03T23:00:00+00:00", date(2027, 1, 4)),
        ]
        for instant, expected in instants:
            with self.subTest(instant=instant):
                moved_at = datetime.fromisoformat(instant)
                self.assertEqual(BoxMovement.week_start_for(moved_at), expected)
                movement = self.ordinary(moved_at=moved_at, week=expected)
                BoxMovement.objects.bulk_create([movement])
                movement.refresh_from_db()
                self.assertEqual(movement.iso_week_start, expected)
                movement.delete()
                self.assert_rejected_without_changes(lambda: BoxMovement.objects.bulk_create([
                    self.ordinary(moved_at=moved_at, week=date(2020, 1, 6)),
                ]))

    def test_bulk_lifecycle_and_unclassified_rows_do_not_consume_allowance(self):
        rows = [BoxMovement(
            box=self.box, to_thermal_zone=self.zone_b, moved_at=paris(2026, 3, 3), kind=kind,
        ) for kind in BoxMovement.Kind if kind != BoxMovement.Kind.ORDINARY]
        BoxMovement.objects.bulk_create(rows + [self.ordinary()])
        self.assertEqual(self.box.movements.count(), 5)
        for row in rows:
            row.refresh_from_db()
            self.assertIsNone(row.iso_week_start)
            self.assertIsNone(row.origin_location_id)
            self.assertIsNone(row.destination_location_id)
        self.assert_rejected_without_changes(lambda: BoxMovement.objects.bulk_create([
            BoxMovement(box=self.box, to_thermal_zone=self.zone_b,
                        kind=BoxMovement.Kind.UNCLASSIFIED, iso_week_start=date(2026, 3, 2)),
        ]))

    def test_queryset_update_and_bulk_update_reject_protected_fields(self):
        movement = self.ordinary()
        movement.save()
        for field, value in (
            ("moved_at", paris(2026, 3, 10)),
            ("iso_week_start", date(2026, 3, 9)),
            ("kind", BoxMovement.Kind.UNCLASSIFIED),
        ):
            with self.subTest(field=field):
                setattr(movement, field, value)
                self.assert_rejected_without_changes(
                    lambda: BoxMovement.objects.filter(pk=movement.pk).update(**{field: value}),
                    ValidationError,
                )
                self.assert_rejected_without_changes(
                    lambda: BoxMovement.objects.bulk_update([movement], [field]), ValidationError,
                )
                movement.refresh_from_db()
        movement.notes = "Bulk note"
        BoxMovement.objects.bulk_update([movement], ["notes"])
        movement.refresh_from_db()
        self.assertEqual(movement.notes, "Bulk note")

    def test_base_manager_cannot_bypass_week_coherence(self):
        self.assert_rejected_without_changes(lambda: BoxMovement._base_manager.bulk_create([
            self.ordinary(week=date(2026, 3, 9)),
        ]))
        movement = self.ordinary()
        movement.save()
        self.assert_rejected_without_changes(lambda: BoxMovement._base_manager.filter(
            pk=movement.pk,
        ).update(moved_at=paris(2026, 3, 10)))

    def test_fixture_deserialization_rejects_wrong_week_and_preserves_legacy(self):
        fixture = serializers.serialize("json", [self.ordinary(week=date(2026, 3, 9))])
        self.assert_rejected_without_changes(lambda: next(serializers.deserialize("json", fixture)).save())
        legacy = BoxMovement(box=self.box, to_thermal_zone=self.zone_b, moved_at=paris(2026, 3, 3))
        next(serializers.deserialize("json", serializers.serialize("json", [legacy]))).save()
        saved = self.box.movements.get()
        self.assertEqual(saved.kind, BoxMovement.Kind.UNCLASSIFIED)
        self.assertIsNone(saved.iso_week_start)

    def test_raw_save_and_direct_sql_cannot_bypass_week_coherence(self):
        self.assert_rejected_without_changes(lambda: self.ordinary(
            week=date(2026, 3, 9),
        ).save_base(raw=True))
        movement = self.ordinary()
        movement.save()

        def raw_update():
            table = connection.ops.quote_name(BoxMovement._meta.db_table)
            with connection.cursor() as cursor:
                cursor.execute(f"UPDATE {table} SET iso_week_start = %s WHERE id = %s",
                               [date(2026, 3, 9), movement.pk])

        self.assert_rejected_without_changes(raw_update)

    @skipUnlessDBFeature("supports_update_conflicts")
    def test_bulk_upsert_cannot_change_date_without_matching_week(self):
        movement = self.ordinary()
        movement.save()
        self.assert_rejected_without_changes(lambda: BoxMovement.objects.bulk_create([
            self.ordinary(pk=movement.pk, moved_at=paris(2026, 3, 10), week=date(2026, 3, 9)),
        ], update_conflicts=True, update_fields=["moved_at"], unique_fields=["pk"]))

    def test_ignore_conflicts_cannot_persist_wrong_week(self):
        write = lambda: BoxMovement.objects.bulk_create([
            self.ordinary(week=date(2026, 3, 9)),
        ], ignore_conflicts=True)
        if connection.vendor == "postgresql":
            self.assert_rejected_without_changes(write)
        else:
            # SQLite INSERT OR IGNORE may silently skip CHECK violations.
            before = self.snapshot(self.box)
            write()
            self.assertEqual(self.snapshot(self.box), before)

    def test_constraint_failure_rolls_back_service_location_and_correction_writes(self):
        def invalid_save(movement, *args, **kwargs):
            movement.iso_week_start = date(2020, 1, 6)
            if kwargs.get("update_fields") is not None:
                kwargs["update_fields"] = set(kwargs["update_fields"]) | {"iso_week_start"}
            return movement.save_base(*args, **kwargs)

        before = self.snapshot(self.box)
        with patch.object(BoxMovement, "save", invalid_save), self.assertRaises(IntegrityError):
            move_box_to_thermal_zone(
                box=self.box, thermal_zone=self.zone_b, moved_at=paris(2026, 3, 3),
                user=self.admin, notes="Rejected creation",
            )
        self.assertEqual(self.snapshot(self.box), before)
        movement = move_box_to_thermal_zone(
            box=self.box, thermal_zone=self.zone_b, moved_at=paris(2026, 3, 3),
            user=self.admin, notes="Original",
        )
        before = self.snapshot(self.box)
        with patch.object(BoxMovement, "save", invalid_save), self.assertRaises(IntegrityError):
            correct_box_movement(
                box_id=self.box.pk, movement_id=movement.pk,
                organization_ids=[self.organization.pk], user=self.admin,
                data={"expected_revision": 0, "moved_at": paris(2026, 3, 4), "notes": "Rejected correction"},
            )
        self.assertEqual(self.snapshot(self.box), before)

    def test_partial_save_cannot_persist_key_for_an_unsaved_date(self):
        movement = self.ordinary()
        movement.save()
        movement.moved_at = paris(2026, 3, 10)
        movement.notes = "Only notes requested"
        self.assert_rejected_without_changes(lambda: movement.save(update_fields=["notes"]))


class BoxMovementWeekConstraintMigrationTests(TransactionTestCase):
    def test_upgrade_and_reverse_preserve_existing_movement_values(self):
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        self.addCleanup(lambda: MigrationExecutor(connection).migrate(latest))
        before = [node for node in latest if node[0] != "cultures"] + [
            ("cultures", "0013_box_movement_kind_week_links"),
        ]
        executor.migrate(before)
        apps = executor.loader.project_state(before).apps
        organization = apps.get_model("organizations", "Organization").objects.create(name="Week schema QA")
        species = apps.get_model("taxonomy", "Species").objects.create(scientific_name="Week schema Aurelia")
        strain = apps.get_model("taxonomy", "Strain").objects.create(species=species, code="WEEK-SCHEMA")
        box = apps.get_model("cultures", "Box").objects.create(
            organization=organization, strain=strain, global_code="WEEK-SCHEMA.001", box_number="001",
        )
        zone = apps.get_model("cultures", "ThermalZone").objects.create(organization=organization, name="QA")
        Movement = apps.get_model("cultures", "BoxMovement")
        for kind in ("ordinary", "unclassified", "qualification", "initial_location", "reactivation"):
            Movement.objects.create(
                box=box, to_thermal_zone=zone, kind=kind, moved_at=paris(2026, 3, 3),
                iso_week_start=date(2026, 3, 2) if kind == "ordinary" else None,
            )
        rows = list(Movement.objects.order_by("pk").values())
        # The old constraint accepts the confirmed bypass; the upgrade refuses it.
        invalid = Movement.objects.create(
            box=box, to_thermal_zone=zone, kind="ordinary", moved_at=paris(2026, 3, 4),
            iso_week_start=date(2026, 3, 9),
        )
        with self.assertRaises(IntegrityError):
            MigrationExecutor(connection).migrate(latest)
        self.assertEqual(Movement.objects.get(pk=invalid.pk).iso_week_start, date(2026, 3, 9))
        self.assertEqual(list(Movement.objects.exclude(pk=invalid.pk).order_by("pk").values()), rows)
        # Remove only this synthetic invalid row so the successful path can be tested.
        invalid.delete()
        executor = MigrationExecutor(connection)
        executor.migrate(latest)
        NewMovement = executor.loader.project_state(latest).apps.get_model("cultures", "BoxMovement")
        self.assertEqual(list(NewMovement.objects.order_by("pk").values()), rows)
        with self.assertRaises(IntegrityError), transaction.atomic():
            NewMovement.objects.create(box_id=box.pk, to_thermal_zone_id=zone.pk, kind="ordinary",
                                       moved_at=paris(2026, 3, 4), iso_week_start=date(2026, 3, 9))
        MigrationExecutor(connection).migrate(before)
        self.assertEqual(list(Movement.objects.order_by("pk").values()), rows)
