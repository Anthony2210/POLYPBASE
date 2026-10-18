"""Approved target-box context changes preserve all environmental records."""

from copy import deepcopy
from datetime import date, datetime, timezone
from unittest.mock import patch

from django.db import transaction
from django.test import TestCase

from apps.audit.models import AuditLog
from apps.exports.services import _measurement_temperature, _thermal_zone_for_date
from apps.measurements.models import BiologicalMeasurement

from .historical_2026 import cca_eob_correction as correction
from .models import Box, BoxLocation, BoxMovement, ThermalZone
from .test_cca_eob_correction import CcaEobFixtureMixin, json_snapshot


class CcaEobLocationTests(CcaEobFixtureMixin, TestCase):
    def location(self, **overrides):
        values = dict(box=self.boxes[5], thermal_zone=self.zone,
                      starts_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
        values.update(overrides)
        return BoxLocation.objects.create(**values)

    def movement(self, **overrides):
        values = dict(box=self.boxes[5], to_thermal_zone=self.zone, user=self.writer,
                      moved_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
        values.update(overrides)
        return BoxMovement.objects.create(**values)

    def assert_stale(self, old_plan):
        before = self.database_snapshot()
        with self.assertRaisesMessage(ValueError, "Stale plan"):
            self.apply(plan=old_plan)
        self.assertEqual(self.database_snapshot(), before)

    def test_same_fallback_preserves_export_zone_and_zero_temperature(self):
        plan = self.plan()
        before = {}
        for entry in plan["entries"]:
            if entry["classification"] != correction.MOVE:
                continue
            evidence = entry["location_context"]
            self.assertTrue(evidence["safe"])
            measurement = BiologicalMeasurement.objects.get(pk=entry["measurement_pk"])
            before[measurement.pk] = (
                _thermal_zone_for_date(measurement.box, measurement.measured_on).pk,
                _measurement_temperature(box=measurement.box, measured_on=measurement.measured_on,
                                         daily_temperatures={(self.zone.pk, measurement.measured_on): 0}),
            )
        self.apply(plan=plan)
        for pk, expected in before.items():
            measurement = BiologicalMeasurement.objects.get(pk=pk)
            self.assertEqual((
                _thermal_zone_for_date(measurement.box, measurement.measured_on).pk,
                _measurement_temperature(box=measurement.box, measured_on=measurement.measured_on,
                                         daily_temperatures={(self.zone.pk, measurement.measured_on): 0}),
            ), expected)
            self.assertEqual(expected, (self.zone.pk, 0))

    def test_absent_context_is_safe_without_inventing_location(self):
        Box.objects.filter(pk__in=[box.pk for box in self.boxes.values()]).update(thermal_zone=None)
        plan = self.plan()
        self.assertTrue(plan["ready"], plan["blockers"])
        self.apply(plan=plan)
        for measurement in BiologicalMeasurement.objects.select_related("box"):
            self.assertIsNone(_thermal_zone_for_date(measurement.box, measurement.measured_on))
        self.assertFalse(BoxLocation.objects.exists())
        self.assertFalse(BoxMovement.objects.exists())

    def test_different_fallback_is_authorized_even_with_equal_setpoints(self):
        other = ThermalZone.objects.create(organization=self.org, name="Other",
                                          target_temperature_c=self.zone.target_temperature_c)
        Box.objects.filter(pk=self.boxes[4].pk).update(thermal_zone=other)
        plan = self.plan()
        entry = plan["entries"][0]
        self.assertTrue(entry["location_context"]["safe"])
        self.assertTrue(entry["location_context"]["context_change_accepted"])
        self.assertTrue(plan["ready"], plan["blockers"])
        self.apply(plan=plan)
        measurement = BiologicalMeasurement.objects.get(pk=self.measurements[0].pk)
        self.assertEqual(_thermal_zone_for_date(measurement.box, measurement.measured_on), other)

    def test_known_to_unknown_fallback_is_authorized_without_inference(self):
        Box.objects.filter(pk=self.boxes[4].pk).update(thermal_zone=None)
        self.apply()
        measurement = BiologicalMeasurement.objects.get(pk=self.measurements[0].pk)
        self.assertIsNone(_thermal_zone_for_date(measurement.box, measurement.measured_on))
        self.assertFalse(BoxLocation.objects.exists())
        self.assertFalse(BoxMovement.objects.exists())

    def test_foreign_fallback_is_blocked_even_when_identical(self):
        foreign = ThermalZone.objects.create(organization=self.other_org, name="Foreign")
        Box.objects.filter(pk__in=[box.pk for box in self.boxes.values()]).update(thermal_zone=foreign)
        self.assert_blocked()

    def test_valid_period_shapes_are_preserved_without_assuming_an_observation_day(self):
        for values in (
            {},
            {"ends_at": datetime(2026, 2, 1, tzinfo=timezone.utc)},
            {"end_date_unknown": True},
            {"starts_at": datetime(2027, 1, 1, tzinfo=timezone.utc)},
            {"starts_at": datetime(2026, 8, 3, 23, 30, tzinfo=timezone.utc)},
        ):
            with self.subTest(values=values), transaction.atomic():
                location = self.location(**values)
                plan = self.plan()
                self.assertEqual(plan["baseline"]["location_context"]["locations"], [json_snapshot(location)])
                self.assertIn(location.pk, plan["entries"][0]["location_context"]["location_ids"])
                self.assertTrue(plan["ready"], plan["blockers"])
                self.apply(plan=plan)
                self.assertEqual(json_snapshot(BoxLocation.objects.get(pk=location.pk)), json_snapshot(location))
                self.assertFalse(BoxMovement.objects.exists())
                transaction.set_rollback(True)

    def test_all_movement_kinds_are_preserved_even_without_legacy_period_links(self):
        for kind in BoxMovement.Kind.values:
            with self.subTest(kind=kind), transaction.atomic():
                movement = self.movement(kind=kind)
                plan = self.plan()
                self.assertEqual(plan["baseline"]["location_context"]["movements"], [json_snapshot(movement)])
                self.assertIn(movement.pk, plan["entries"][0]["location_context"]["movement_ids"])
                self.assertTrue(plan["ready"], plan["blockers"])
                self.apply(plan=plan)
                self.assertEqual(json_snapshot(BoxMovement.objects.get(pk=movement.pk)), json_snapshot(movement))
                self.assertFalse(BoxLocation.objects.exists())
                transaction.set_rollback(True)

    def test_overlapping_periods_are_blocked_not_rebuilt(self):
        self.location()
        self.location()
        plan = self.plan()
        self.assertIn("Overlapping historical", " ".join(plan["entries"][0]["issues"]))
        self.assert_blocked(plan)

    def test_cross_box_links_are_blocked_not_rebuilt(self):
        first = self.location()
        self.movement(destination_location=first, box=self.boxes[4])
        plan = self.plan()
        self.assertIn("another box", " ".join(plan["entries"][0]["issues"]))
        self.assert_blocked(plan)

    def test_external_movement_link_to_selected_period_blocks_and_stales_plan(self):
        first = self.location()
        plan = self.plan()
        unrelated = Box.objects.create(organization=self.org, strain=self.strain,
                                       global_code="CCA-EOB-2.007", box_number="007")
        self.movement(box=unrelated, destination_location=first)
        self.assert_stale(plan)
        self.assert_blocked()

    def test_linked_period_disagreements_are_blocked(self):
        location = self.location()
        self.movement(destination_location=location, moved_at=datetime(2026, 1, 2, tzinfo=timezone.utc))
        plan = self.plan()
        self.assertIn("contradicts", " ".join(plan["entries"][0]["issues"]))
        self.assert_blocked(plan)

    def test_invalid_interval_is_blocked(self):
        self.location(ends_at=datetime(2025, 12, 31, tzinfo=timezone.utc))
        self.assert_blocked()

    def test_foreign_zone_in_each_historical_relation_blocks(self):
        foreign = ThermalZone.objects.create(organization=self.other_org, name="Foreign history")
        for relation in ("source_pointer", "target_pointer", "period", "movement_origin", "movement_target"):
            with self.subTest(relation=relation), transaction.atomic():
                if relation.endswith("pointer"):
                    box = self.boxes[5] if relation == "source_pointer" else self.boxes[4]
                    Box.objects.filter(pk=box.pk).update(thermal_zone=foreign)
                elif relation == "period":
                    self.location(thermal_zone=foreign)
                elif relation == "movement_origin":
                    self.movement(from_thermal_zone=foreign)
                else:
                    self.movement(to_thermal_zone=foreign)
                self.assert_blocked()
                transaction.set_rollback(True)

    def test_zone_tenancy_and_current_pointer_changes_make_plan_stale(self):
        plan = self.plan()
        ThermalZone.objects.filter(pk=self.zone.pk).update(organization=self.other_org)
        self.assert_stale(plan)
        ThermalZone.objects.filter(pk=self.zone.pk).update(organization=self.org)
        plan = self.plan()
        Box.objects.filter(pk=self.boxes[4].pk).update(thermal_zone=None)
        self.assert_stale(plan)

    def test_full_rollback_with_authorized_history_after_final_receipt_error(self):
        self.location()
        self.movement()
        plan = self.plan()
        self.assertTrue(plan["ready"], plan["blockers"])
        before = self.database_snapshot()
        real_create = AuditLog.objects.create

        def fail_receipt(**kwargs):
            result = real_create(**kwargs)
            if kwargs.get("object_type") == "historical_correction":
                raise RuntimeError("Synthetic receipt failure")
            return result

        with patch.object(AuditLog.objects, "create", side_effect=fail_receipt):
            with self.assertRaisesMessage(RuntimeError, "Synthetic receipt failure"):
                self.apply(plan=plan)
        self.assertEqual(self.database_snapshot(), before)

    def test_previous_operation_and_environment_policy_hashes_are_stale(self):
        plan = self.plan()
        self.assertEqual(plan["operation"], "cca_eob_v2_20261016_v3")
        self.assertEqual(plan["product_decision"], correction.PRODUCT_DECISION)
        for changes in ("operation", "policy", "decision"):
            with self.subTest(changes=changes):
                old_plan = deepcopy(plan)
                if changes == "operation":
                    old_plan["operation"] = "cca_eob_v2_20261016_v2"
                elif changes == "policy":
                    old_plan["baseline"]["location_context"]["policy"] = (
                        "no_periods_or_movements_and_identical_current_zone_v1"
                    )
                else:
                    old_plan["product_decision"]["environmental_context_change_accepted"] = False
                self.assert_stale(old_plan)

    def test_touching_boundaries_and_unknown_ends_do_not_prove_overlap(self):
        first = self.location(ends_at=datetime(2026, 2, 1, tzinfo=timezone.utc))
        unknown = self.location(starts_at=first.ends_at, end_date_unknown=True)
        self.location(starts_at=datetime(2026, 3, 1, tzinfo=timezone.utc))
        self.movement(origin_location=first, destination_location=unknown, moved_at=first.ends_at,
                      from_thermal_zone=self.zone)
        before_locations = list(BoxLocation.objects.order_by("pk").values())
        before_movements = list(BoxMovement.objects.order_by("pk").values())
        plan = self.plan()
        self.assertTrue(plan["ready"], plan["blockers"])
        self.apply(plan=plan)
        self.assertEqual(list(BoxLocation.objects.order_by("pk").values()), before_locations)
        self.assertEqual(list(BoxMovement.objects.order_by("pk").values()), before_movements)

    def test_location_insertion_edit_and_deletion_make_plan_stale(self):
        initial = self.plan()
        location = self.location()
        self.assert_stale(initial)
        with_period = self.plan()
        BoxLocation.objects.filter(pk=location.pk).update(notes="Changed without touching Box")
        self.assert_stale(with_period)
        edited = self.plan()
        location.delete()
        self.assert_stale(edited)

    def test_movement_insertion_revision_links_author_and_deletion_make_plan_stale(self):
        initial = self.plan()
        movement = self.movement()
        self.assert_stale(initial)
        for changes in ({"notes": "Changed"}, {"revision": 1}, {"user": None},
                        {"destination_location": self.location()}):
            with self.subTest(changes=changes):
                plan = self.plan()
                BoxMovement.objects.filter(pk=movement.pk).update(**changes)
                self.assert_stale(plan)
        plan = self.plan()
        movement.delete()
        self.assert_stale(plan)

    def test_history_on_a_box_not_in_any_reassignment_does_not_block_safe_pairs(self):
        for index in (1, 2):
            self.source["entries"][index]["target_code"] = self.measurements[index].box.global_code
        self.location(box=self.boxes[6])
        self.movement(box=self.boxes[6])
        plan = self.plan()
        self.assertTrue(plan["ready"], plan["blockers"])
        self.assertEqual(plan["counts"], {correction.MOVE: 1, correction.MATCH: 3})
        before_locations = list(BoxLocation.objects.values())
        before_movements = list(BoxMovement.objects.values())
        self.apply(plan=plan)
        self.assertEqual(list(BoxLocation.objects.values()), before_locations)
        self.assertEqual(list(BoxMovement.objects.values()), before_movements)

    def test_four_uncovered_w41_w42_measurements_are_preserved_exactly(self):
        retained = []
        for box in (self.boxes[4], self.boxes[5]):
            for week in (41, 42):
                measurement = BiologicalMeasurement.objects.create(
                    box=box, measured_on=date.fromisocalendar(2026, week, 1),
                    polyp_count=0, ephyrae_count=0,
                )
                retained.append(json_snapshot(measurement))
        plan = self.plan()
        self.assertEqual(plan["counts"][correction.OUTSIDE], 4)
        self.apply(plan=plan)
        for before in retained:
            self.assertEqual(json_snapshot(BiologicalMeasurement.objects.get(pk=before["id"])), before)

    def test_history_does_not_prevent_idempotent_already_matching_plan(self):
        self.apply()
        self.location()
        self.movement()
        plan = self.plan()
        self.assertTrue(plan["ready"], plan["blockers"])
        before = self.database_snapshot()
        self.apply(plan=plan)
        self.assertEqual(self.database_snapshot(), before)
        self.assertEqual(AuditLog.objects.count(), 4)
