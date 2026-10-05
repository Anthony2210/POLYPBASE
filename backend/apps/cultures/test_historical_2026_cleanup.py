"""Guarded cleanup of the reviewed test data and its hand-off to the import."""

from datetime import date, datetime, timezone as dt_timezone
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.measurements.models import BiologicalMeasurement, DailyTemperature
from apps.taxonomy.models import Species, Strain

from .historical_2026 import importer, reviewed_cleanup as cleanup
from .models import Box, BoxLineage, BoxLocation, BoxMovement, SubcultureEvent, ThermalZone
from .test_historical_2026_import import ImporterBase, box_spec, item, make_manifest


class CleanupBase(ImporterBase):
    def setUp(self):
        super().setUp()
        self.zone = ThermalZone.objects.create(organization=self.org, name="Cleanup zone")
        self.boxes = {}
        for code in sorted({spec["box"] for spec in cleanup.APPROVED_TEST_MEASUREMENTS}):
            strain, _ = Strain.objects.get_or_create(
                species=self.species, code=code.rsplit(".", 1)[0], defaults={"organization": self.org}
            )
            self.boxes[code] = self.make_box(code, strain=strain)
        self.parent = self.make_box(
            cleanup.TEST_BOX["parent_code"],
            strain=Strain.objects.create(
                id=cleanup.TEST_BOX["strain_id"], species=self.species, organization=self.org, code="AAU-NBE-1"
            ),
        )
        user_model = get_user_model()
        for spec in cleanup.APPROVED_TEST_MEASUREMENTS:
            author = user_model.objects.filter(pk=spec["user_id"]).first() or user_model.objects.create_user(
                id=spec["user_id"], username=f"snapshot-{spec['user_id']}", email=f"snapshot{spec['user_id']}@example.org"
            )
            BiologicalMeasurement.objects.create(
                id=spec["pk"],
                box=self.boxes[spec["box"]],
                measured_on=date.fromisoformat(spec["measured_on"]),
                polyp_count=spec["polyps"],
                ephyrae_count=spec["ephyrae"],
                strobila_count=spec["strobila_count"],
                salinity_psu=spec["salinity_psu"],
                culture_status=spec["culture_status"],
                needs_attention=spec["needs_attention"],
                notes=spec["notes"],
                user=author,
            )
            # created_at is auto-generated; restore the reviewed value.
            BiologicalMeasurement.objects.filter(pk=spec["pk"]).update(
                created_at=datetime.fromisoformat(spec["created_at"])
            )
        self.test_box = Box.objects.create(
            id=cleanup.TEST_BOX["pk"], organization=self.org, strain=self.parent.strain,
            global_code=cleanup.TEST_BOX["global_code"], box_number="003", status=Box.Status.ACTIVE,
            thermal_zone=self.zone,
        )
        self.location = BoxLocation.objects.create(
            id=5992, box=self.test_box, thermal_zone=self.zone,
            starts_at=timezone.make_aware(datetime(2026, 9, 19)), notes="Initial location after subculture.",
        )
        self.event = SubcultureEvent.objects.create(id=4, parent_box=self.parent, event_date=date(2026, 9, 19))
        self.lineage = BoxLineage.objects.create(
            id=4, parent_box=self.parent, child_box=self.test_box, subculture_event=self.event
        )
        self.audits = []
        for pk in cleanup.REVIEWED_AUDIT_IDS[:-2]:
            self.audits.append(AuditLog.objects.create(
                id=pk, organization=self.org, action="entry", object_type="box",
                object_id=cleanup.TEST_BOX["global_code"], description="reviewed test audit",
                metadata={"box_id": cleanup.TEST_BOX["pk"]},
            ))
        # Legitimate neighbours that must survive.
        self.keep_measurement = BiologicalMeasurement.objects.create(
            box=self.boxes["AAL-FGU-1.001"], measured_on=date(2026, 1, 5), polyp_count=1, ephyrae_count=1
        )
        self.keep_temperature = DailyTemperature.objects.create(
            thermal_zone=self.zone, date=date(2026, 9, 1), average_temperature_c="20.0"
        )

    def cleanup_plan(self):
        return cleanup.build_cleanup_plan(self.org)

    def run_cleanup(self, **kwargs):
        plan_hash = kwargs.pop("plan_hash", None) or self.cleanup_plan().plan_hash
        return cleanup.apply_cleanup(self.org, actor=kwargs.pop("actor", self.admin), expected_plan_hash=plan_hash)

    def snapshot(self):
        return (Box.objects.count(), BiologicalMeasurement.objects.count(), AuditLog.objects.count(),
                BoxLineage.objects.count(), SubcultureEvent.objects.count(), BoxLocation.objects.count())


class CleanupScopeTests(CleanupBase):
    def test_allowlist_is_exactly_the_reviewed_scope(self):
        self.assertEqual([m["pk"] for m in cleanup.APPROVED_TEST_MEASUREMENTS], list(range(100163, 100172)))
        self.assertEqual((cleanup.TEST_BOX["pk"], cleanup.TEST_LOCATION["pk"], cleanup.TEST_LINEAGE_PK, cleanup.TEST_EVENT["pk"]),
                         (2312, 5992, 4, 4))
        self.assertEqual(cleanup.REVIEWED_AUDIT_IDS, (
            706, 719, 733, 735, 737, 745, 1349, 1369, 1373, 1381, 1382, 1386, 1387, 1388, 1416, 1417,
        ))

    def test_dry_run_changes_nothing_and_reports_the_closure(self):
        before = self.snapshot()
        out = StringIO()
        call_command("cleanup_reviewed_test_data", organization_id=self.org.pk, stdout=out)
        self.assertEqual(self.snapshot(), before)
        self.assertIn("Dry-run only", out.getvalue())
        report = self.cleanup_plan().report()
        self.assertEqual(report["blockers"], [])
        self.assertEqual(report["dependency_closure"], {"locations": [5992], "parent_lineages": [4]})
        self.assertIn("audit:1416", report["absent"])
        with self.assertRaisesMessage(CommandError, "--expected-plan-hash"):
            call_command("cleanup_reviewed_test_data", organization_id=self.org.pk, apply=True)

    def test_apply_removes_only_the_reviewed_objects(self):
        _, receipt = self.run_cleanup()
        self.assertFalse(BiologicalMeasurement.objects.filter(pk__in=range(100163, 100172)).exists())
        self.assertFalse(Box.objects.filter(pk=2312).exists())
        self.assertFalse(BoxLocation.objects.filter(pk=5992).exists())
        self.assertFalse(BoxLineage.objects.filter(pk=4).exists())
        self.assertFalse(SubcultureEvent.objects.filter(pk=4).exists())
        self.assertFalse(AuditLog.objects.filter(pk__in=cleanup.REVIEWED_AUDIT_IDS).exists())
        # Legitimate parent, neighbours, zone, strain and temperatures survive.
        self.parent.refresh_from_db()
        self.assertEqual(self.parent.status, Box.Status.ACTIVE)
        self.assertTrue(BiologicalMeasurement.objects.filter(pk=self.keep_measurement.pk).exists())
        self.assertTrue(DailyTemperature.objects.filter(pk=self.keep_temperature.pk).exists())
        self.assertTrue(ThermalZone.objects.filter(pk=self.zone.pk).exists())
        self.assertTrue(Strain.objects.filter(code="AAU-NBE-1").exists())
        self.assertEqual(Box.objects.filter(global_code__in=self.boxes).count(), len(self.boxes))
        self.assertEqual(receipt.object_type, cleanup.RECEIPT_OBJECT_TYPE)
        self.assertEqual(len(receipt.metadata["removed"]["measurements"]), 9)
        self.assertEqual(len(receipt.metadata["removed"]["audits"]), 14)
        self.assertEqual(receipt.metadata["removed"]["box"]["global_code"], "AAU-NBE-1.003")

    def test_removed_readings_invalidate_stale_intents_on_their_boxes(self):
        box = self.boxes["ALA-JKA-1.009"]
        box.refresh_from_db()
        before = box.polyp_state_revision
        self.run_cleanup()
        box.refresh_from_db()
        self.assertEqual(box.polyp_state_revision, before + 1)

    def test_rerun_is_idempotent_and_leaves_one_receipt(self):
        self.run_cleanup()
        before = self.snapshot()
        plan, receipt = self.run_cleanup()
        self.assertIsNone(receipt)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(AuditLog.objects.filter(object_type=cleanup.RECEIPT_OBJECT_TYPE).count(), 1)


class CleanupDeactivatedStateTests(CleanupBase):
    """State B: the exact audited 2026-10-01 deactivation of the test Box."""

    def setUp(self):
        super().setUp()
        spec = cleanup.DEACTIVATED_STATE
        zone = ThermalZone.objects.filter(pk=3).first() or ThermalZone.objects.create(
            id=3, organization=self.org, name="Reviewed zone 3"
        )
        self.zone3 = zone
        ends_at = datetime.fromisoformat(spec["location"]["ends_at"])
        Box.objects.filter(pk=2312).update(
            status="inactive", stop_reason="N'existe pas", deactivated_on=date(2026, 10, 1),
            thermal_zone=None, entered_on=date(2026, 9, 19), created_on=date(2026, 9, 19),
        )
        BoxLocation.objects.filter(pk=5992).update(
            thermal_zone=zone, ends_at=ends_at,
            starts_at=datetime.fromisoformat(spec["location"]["starts_at"]),
        )
        SubcultureEvent.objects.filter(pk=4).update(user_id=6)
        self.audit_1417 = AuditLog.objects.create(
            id=1417, organization=self.org, user_id=6, action="update", object_type="box",
            object_id="AAU-NBE-1.003", description="Box deactivated: AAU-NBE-1.003",
            metadata=json_copy(spec["audit"]["metadata"]),
        )
        AuditLog.objects.filter(pk=1417).update(
            created_at=datetime.fromisoformat(spec["audit"]["created_at"])
        )

    def assert_blocks(self, target="location:5992"):
        plan = self.cleanup_plan()
        self.assertTrue(any(b["target"] == target for b in plan.blockers), plan.blockers)
        before = self.snapshot()
        with self.assertRaises(cleanup.CleanupBlocked):
            self.run_cleanup(plan_hash="x")
        self.assertEqual(self.snapshot(), before)

    def set_audit_metadata(self, **changes):
        metadata = json_copy(cleanup.DEACTIVATED_STATE["audit"]["metadata"])
        metadata.update(changes)
        AuditLog.objects.filter(pk=1417).update(metadata=metadata)

    def test_exact_deactivated_state_is_accepted_and_removed_atomically(self):
        self.assertEqual(self.cleanup_plan().blockers, [])
        _, receipt = self.run_cleanup()
        self.assertFalse(Box.objects.filter(pk=2312).exists())
        self.assertFalse(BoxLocation.objects.filter(pk=5992).exists())
        self.assertFalse(AuditLog.objects.filter(pk=1417).exists())
        self.assertFalse(BiologicalMeasurement.objects.filter(pk__in=range(100163, 100172)).exists())
        self.assertTrue(ThermalZone.objects.filter(pk=3).exists())
        self.assertEqual(receipt.object_type, cleanup.RECEIPT_OBJECT_TYPE)

    def test_rerun_after_deactivated_cleanup_is_idempotent(self):
        self.run_cleanup()
        before = self.snapshot()
        _, receipt = self.run_cleanup()
        self.assertIsNone(receipt)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(AuditLog.objects.filter(object_type=cleanup.RECEIPT_OBJECT_TYPE).count(), 1)

    def test_wrong_ends_at_blocks(self):
        BoxLocation.objects.filter(pk=5992).update(ends_at=datetime(2026, 10, 1, 7, 5, 54, 242613, tzinfo=dt_timezone.utc))
        self.assert_blocks()

    def test_wrong_stop_reason_blocks(self):
        Box.objects.filter(pk=2312).update(stop_reason="Autre")
        self.assert_blocks()

    def test_wrong_deactivated_on_blocks(self):
        Box.objects.filter(pk=2312).update(deactivated_on=date(2026, 10, 2))
        self.assert_blocks()

    def test_arbitrary_inactive_box_with_closed_location_blocks(self):
        BoxLocation.objects.filter(pk=5992).update(ends_at=datetime(2026, 10, 3, tzinfo=dt_timezone.utc))
        AuditLog.objects.filter(pk=1417).delete()
        self.assert_blocks()

    def test_missing_audit_1417_blocks(self):
        AuditLog.objects.filter(pk=1417).delete()
        self.assert_blocks()

    def test_altered_audit_transition_blocks(self):
        self.set_audit_metadata(transition="inactive->active")
        self.assert_blocks()

    def test_altered_audit_before_state_blocks(self):
        metadata = json_copy(cleanup.DEACTIVATED_STATE["audit"]["metadata"])
        metadata["before"]["status"] = "inactive"
        AuditLog.objects.filter(pk=1417).update(metadata=metadata)
        self.assert_blocks()

    def test_changed_closed_location_ids_block(self):
        for ids in ([5992, 5993], [], [5993]):
            with self.subTest(ids=ids):
                self.set_audit_metadata(closed_location_ids=ids)
                self.assert_blocks()

    def test_changed_at_different_from_location_end_blocks(self):
        self.set_audit_metadata(changed_at="2026-10-01T07:05:54.242613+00:00")
        self.assert_blocks()

    def test_wrong_thermal_zone_state_blocks(self):
        Box.objects.filter(pk=2312).update(thermal_zone=self.zone)
        self.assert_blocks()

    def test_wrong_audit_organization_blocks(self):
        AuditLog.objects.filter(pk=1417).update(organization=self.other)
        self.assert_blocks()

    def test_end_date_unknown_location_blocks(self):
        BoxLocation.objects.filter(pk=5992).update(ends_at=None, end_date_unknown=True)
        self.assert_blocks()

    def test_extra_location_still_blocks(self):
        BoxLocation.objects.create(box=self.test_box, thermal_zone=self.zone3)
        self.assert_blocks("box:2312/locations")

    def test_extra_movement_still_blocks(self):
        BoxMovement.objects.create(box=self.test_box, to_thermal_zone=self.zone3)
        self.assert_blocks("box:2312/movements")

    def test_state_a_remains_accepted_and_closed_state_needs_state_b(self):
        BoxLocation.objects.filter(pk=5992).update(ends_at=None)
        Box.objects.filter(pk=2312).update(status="active", stop_reason="", deactivated_on=None, thermal_zone=self.zone3)
        AuditLog.objects.filter(pk=1417).delete()
        self.assertEqual(self.cleanup_plan().blockers, [])


def json_copy(value):
    import json

    return json.loads(json.dumps(value))


class CleanupMeasurementSnapshotGuardTests(CleanupBase):
    """A row with the right PK and counts but any other changed field is never deleted."""

    def change(self, pk, **values):
        BiologicalMeasurement.objects.filter(pk=pk).update(**values)

    def assert_field_blocks(self, pk, field, **values):
        self.change(pk, **values)
        plan = self.cleanup_plan()
        blocker = next(b for b in plan.blockers if b["target"] == f"measurement:{pk}")
        self.assertIn(f"{field} expected", blocker["detail"])
        before = self.snapshot()
        with self.assertRaises(cleanup.CleanupBlocked):
            self.run_cleanup(plan_hash="x")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(BiologicalMeasurement.objects.filter(pk__in=range(100163, 100172)).count(), 9)
        self.assertTrue(Box.objects.filter(pk=2312).exists())
        return blocker

    def test_exact_reviewed_snapshot_is_eligible(self):
        report = self.cleanup_plan().report()
        self.assertEqual(report["blockers"], [])
        self.assertEqual(len([p for p in report["present"] if p.startswith("measurement:")]), 9)

    def test_each_guarded_field_is_checked_independently_for_every_measurement(self):
        mutations = {
            "strobila_count": 1, "notes": "changed", "salinity_psu": "12.00", "culture_status": "dead",
            "needs_attention": True, "polyp_count": 1, "ephyrae_count": 1,
        }
        for spec in cleanup.APPROVED_TEST_MEASUREMENTS:
            for field, value in mutations.items():
                with self.subTest(pk=spec["pk"], field=field):
                    original = getattr(BiologicalMeasurement.objects.get(pk=spec["pk"]), field)
                    if str(original) == str(value):
                        continue
                    self.change(spec["pk"], **{field: value})
                    self.assertTrue(
                        any(b["target"] == f"measurement:{spec['pk']}" for b in self.cleanup_plan().blockers)
                    )
                    self.change(spec["pk"], **{field: original})
                    self.assertEqual(self.cleanup_plan().blockers, [])

    def test_changed_strobila_blocks_and_reports_expected_and_actual(self):
        blocker = self.assert_field_blocks(100165, "strobila_count", strobila_count=2)
        self.assertIn("strobila_count expected 0, found 2", blocker["detail"])

    def test_null_strobila_does_not_match_the_reviewed_zero(self):
        blocker = self.assert_field_blocks(100165, "strobila_count", strobila_count=None)
        self.assertIn("strobila_count expected 0, found None", blocker["detail"])

    def test_zero_salinity_does_not_match_a_reviewed_null_salinity(self):
        blocker = self.assert_field_blocks(100166, "salinity_psu", salinity_psu="0.00")
        self.assertIn("salinity_psu expected None, found '0.00'", blocker["detail"])

    def test_null_salinity_does_not_match_a_reviewed_value(self):
        self.assert_field_blocks(100163, "salinity_psu", salinity_psu=None)

    def test_changed_notes_block(self):
        blocker = self.assert_field_blocks(100165, "notes", notes="real observation")
        self.assertIn("notes expected 'test', found 'real observation'", blocker["detail"])

    def test_changed_author_blocks_and_reports_only_the_user_pk(self):
        other = get_user_model().objects.create_user(username="someone-else", email="else@example.org")
        blocker = self.assert_field_blocks(100164, "user_id", user=other)
        self.assertIn(f"user_id expected 6, found {other.pk}", blocker["detail"])
        self.assertNotIn("someone-else", blocker["detail"])
        self.assertNotIn("else@example.org", blocker["detail"])

    def test_removed_author_blocks(self):
        self.assert_field_blocks(100164, "user_id", user=None)

    def test_changed_culture_status_and_attention_block(self):
        self.assert_field_blocks(100167, "culture_status", culture_status="dead")
        self.change(100167, culture_status="not_specified")
        self.assert_field_blocks(100167, "needs_attention", needs_attention=True)

    def test_changed_creation_time_blocks(self):
        self.assert_field_blocks(100168, "created_at", created_at=datetime(2026, 9, 1, 12, 0, tzinfo=dt_timezone.utc))

    def test_changed_measured_date_blocks(self):
        self.assert_field_blocks(100169, "measured_on", measured_on=date(2026, 9, 16))

    def test_blocked_apply_through_the_command_deletes_nothing(self):
        self.change(100165, notes="real observation")
        out = StringIO()
        call_command("cleanup_reviewed_test_data", organization_id=self.org.pk, stdout=out)
        self.assertIn("blockers: 1", out.getvalue())
        self.assertIn("notes expected 'test'", out.getvalue())
        self.assertNotIn("Apply with", out.getvalue())
        with self.assertRaises(CommandError):
            call_command(
                "cleanup_reviewed_test_data", organization_id=self.org.pk, apply=True,
                expected_plan_hash="x", actor=self.admin.username, stdout=StringIO(),
            )
        self.assertEqual(BiologicalMeasurement.objects.filter(pk__in=range(100163, 100172)).count(), 9)

    def test_approved_cleanup_still_succeeds_and_rerun_is_idempotent(self):
        _, receipt = self.run_cleanup()
        self.assertEqual(receipt.metadata["removed"]["measurements"][0]["strobila"], 0)
        self.assertFalse(BiologicalMeasurement.objects.filter(pk__in=range(100163, 100172)).exists())
        before = self.snapshot()
        _, again = self.run_cleanup()
        self.assertIsNone(again)
        self.assertEqual(self.snapshot(), before)


class CleanupFailClosedTests(CleanupBase):
    def assert_blocked(self, label_prefix):
        plan = self.cleanup_plan()
        self.assertTrue(any(b["target"].startswith(label_prefix) for b in plan.blockers), plan.blockers)
        before = self.snapshot()
        with self.assertRaises(cleanup.CleanupBlocked):
            self.run_cleanup(plan_hash="x")
        self.assertEqual(self.snapshot(), before)

    def test_changed_measurement_values_block(self):
        BiologicalMeasurement.objects.filter(pk=100165).update(polyp_count=88)
        self.assert_blocked("measurement:100165")

    def test_measurement_in_another_organization_blocks(self):
        foreign = self.make_box("FOR-EIG-1.001", org=self.other, strain=Strain.objects.create(
            species=self.species, organization=self.other, code="FOR-EIG-1"))
        BiologicalMeasurement.objects.filter(pk=100164).update(box=foreign)
        self.assert_blocked("measurement:100164")

    def test_test_box_in_another_organization_blocks(self):
        Box.objects.filter(pk=2312).update(organization=self.other)
        self.assert_blocked("box:2312")

    def test_test_box_with_another_strain_sharing_the_code_blocks(self):
        other_species = Species.objects.create(scientific_name="Other species", genus_species_code="OTH")
        lookalike = Strain.objects.create(species=other_species, organization=self.org, code="AAU-NBE-1")
        self.assertNotEqual(lookalike.pk, cleanup.TEST_BOX["strain_id"])
        Box.objects.filter(pk=2312).update(strain=lookalike)
        self.assert_blocked("box:2312")

    def test_test_box_with_changed_identity_blocks(self):
        Box.objects.filter(pk=2312).update(box_number="004")
        self.assert_blocked("box:2312")

    def test_new_dependent_measurement_on_the_test_box_blocks(self):
        BiologicalMeasurement.objects.create(box=self.test_box, measured_on=date(2026, 9, 21), polyp_count=3, ephyrae_count=0)
        self.assert_blocked("box:2312/biological_measurements")

    def test_new_dependent_movement_on_the_test_box_blocks(self):
        BoxMovement.objects.create(box=self.test_box, to_thermal_zone=self.zone)
        self.assert_blocked("box:2312/movements")

    def test_second_child_on_the_subculture_event_blocks(self):
        other_child = self.make_box("AAU-NBE-1.004", strain=self.parent.strain, number="004")
        BoxLineage.objects.create(parent_box=self.parent, child_box=other_child, subculture_event=self.event)
        self.assert_blocked("event:4")

    def test_a_child_of_the_test_box_blocks(self):
        child = self.make_box("AAU-NBE-1.005", strain=self.parent.strain, number="005")
        BoxLineage.objects.create(parent_box=self.test_box, child_box=child)
        self.assert_blocked("box:2312/child_lineages")

    def test_changed_location_blocks(self):
        BoxLocation.objects.filter(pk=5992).update(notes="Moved by a person")
        self.assert_blocked("location:5992")

    def test_unlisted_audit_referencing_the_test_objects_blocks(self):
        AuditLog.objects.create(organization=self.org, action="view", object_type="box",
                                object_id="AAU-NBE-1.003", description="opened later", metadata={"box_id": 2312})
        self.assert_blocked("audit:")

    def test_listed_audit_not_tied_to_the_test_objects_blocks(self):
        AuditLog.objects.filter(pk=706).update(object_id="AAL-FGU-1.002", metadata={"note": "legitimate"})
        self.assert_blocked("audit:706")

    def test_apply_needs_an_active_administrator_of_the_organization(self):
        plan_hash = self.cleanup_plan().plan_hash
        for actor in (self.tech, self.foreign_admin):
            with self.assertRaises(PermissionDenied):
                self.run_cleanup(actor=actor, plan_hash=plan_hash)
        self.assertTrue(Box.objects.filter(pk=2312).exists())

    def test_stale_plan_hash_is_rejected(self):
        plan_hash = self.cleanup_plan().plan_hash
        AuditLog.objects.filter(pk=706).delete()
        with self.assertRaisesMessage(ValueError, "database changed"):
            self.run_cleanup(plan_hash=plan_hash)
        self.assertTrue(Box.objects.filter(pk=2312).exists())

    def test_failure_during_deletion_rolls_everything_back(self):
        plan_hash = self.cleanup_plan().plan_hash
        before = self.snapshot()
        from unittest import mock

        with mock.patch.object(AuditLog.objects, "create", side_effect=RuntimeError("audit down")):
            with self.assertRaises(RuntimeError):
                self.run_cleanup(plan_hash=plan_hash)
        self.assertEqual(self.snapshot(), before)


class CleanupToImportContractTests(CleanupBase):
    SLOTS = (
        ("AAL-FGU-1.001", 34), ("AAL-FGU-1.001", 35), ("AAL-FGU-1.001", 38), ("AAL-FGU-1.001", 39),
        ("ALA-JKA-1.009", 35), ("ALA-JKA-1.009", 38), ("NBR-JKA-1.006", 35),
    )

    def source_manifest(self):
        boxes = [box_spec(code, local=self.boxes[code].local_code) for code in
                 ("AAL-FGU-1.001", "ALA-JKA-1.009", "NBR-JKA-1.006")]
        return make_manifest(boxes, [item(code, week, 300, 20) for code, week in self.SLOTS])

    def test_seven_slots_move_from_test_collision_to_create_after_cleanup(self):
        manifest = self.source_manifest()
        plan = importer.build_plan(manifest, self.org)
        collisions = {key for key, cls in self.classes(plan).items() if cls == importer.EXPECTED_TEST_DATA_COLLISION}
        self.assertEqual(collisions, set(self.SLOTS))
        self.assertEqual(len(collisions), 7)
        self.assertEqual(plan.counts[importer.EXPECTED_TEST_DATA_COLLISION], 7)
        self.assertEqual({b["classification"] for b in plan.blockers}, {importer.EXPECTED_TEST_DATA_COLLISION})
        before = self.snapshot()
        with self.assertRaises(importer.HistoricalImportBlocked):
            self.apply(manifest, plan_hash="x")  # the importer never deletes test data
        self.assertEqual(self.snapshot(), before)
        self.run_cleanup()
        plan = importer.build_plan(manifest, self.org)
        self.assertEqual(set(self.classes(plan).values()), {importer.CREATE})
        self.assertEqual(plan.blockers, [])
        self.apply(manifest)
        self.assertEqual(
            BiologicalMeasurement.objects.filter(box__global_code="AAL-FGU-1.001", strobila_count__isnull=True).count(), 4
        )
        _, receipt = self.apply(manifest)
        self.assertIsNone(receipt)

    def test_a_same_slot_row_that_is_not_the_reviewed_test_row_stays_a_genuine_conflict(self):
        BiologicalMeasurement.objects.filter(pk=100165).update(polyp_count=88)  # no longer matches the review
        manifest = self.source_manifest()
        classes = self.classes(importer.build_plan(manifest, self.org))
        self.assertEqual(classes[("ALA-JKA-1.009", 35)], importer.CONFLICT)

    def test_a_real_measurement_in_a_colliding_slot_is_never_classified_as_test_data(self):
        BiologicalMeasurement.objects.create(
            box=self.boxes["ALA-JKA-1.009"], measured_on=date.fromisocalendar(2026, 36, 1), polyp_count=9, ephyrae_count=9
        )
        manifest = make_manifest([box_spec("ALA-JKA-1.009", local=self.boxes["ALA-JKA-1.009"].local_code)], [item("ALA-JKA-1.009", 36, 300, 20)])
        self.assertEqual(self.classes(importer.build_plan(manifest, self.org)), {("ALA-JKA-1.009", 36): importer.CONFLICT})
