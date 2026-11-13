"""PostgreSQL proofs for the exceptional five-measurement CCA-EOB reassignment."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from io import StringIO
from time import monotonic
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, close_old_connections, connection, connections, transaction
from django.test import TestCase, TransactionTestCase

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.measurements.models import (
    BiologicalMeasurement, DailyTemperature, HistoricalTemperatureEvidence, HistoricalTemperatureReading,
)
from apps.measurements.test_historical_temperature_plan import evidence
from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain

from .historical_2026 import cca_eob_five_reassignment as op
from .models import (
    Box, BoxLineage, BoxLocation, BoxMovement, SubcultureAllocation, SubcultureEvent,
    ThermalZone, TransferEnvelope, TransferItem,
)

REASON = "Researcher confirmed these five measurements belong to CCA-EOB-2.004 (test reference)."


def at(month, day):
    return datetime(2026, month, day, 12, tzinfo=timezone.utc)


class FiveFixture:
    """Synthetic data with the production identifiers, never production values."""

    def setUp(self):
        super().setUp()
        self.org, _ = Organization.objects.get_or_create(pk=1, defaults={"name": "QA org", "slug": "qa-org"})
        self.other_org = Organization.objects.create(pk=2001, name="Other", slug="other-qa")
        user_model = get_user_model()
        self.admin = user_model.objects.create_user(username="five-admin", email="a@example.org")
        self.writer = user_model.objects.create_user(username="five-writer", email="w@example.org")
        self.technician = user_model.objects.create_user(username="five-tech", email="t@example.org")
        OrganizationMembership.objects.create(organization=self.org, user=self.admin, role="admin")
        OrganizationMembership.objects.create(organization=self.org, user=self.writer, role="lab_technician")
        OrganizationMembership.objects.create(organization=self.org, user=self.technician, role="lab_technician")
        species = Species.objects.create(scientific_name="Cyanea capillata five QA")
        self.strain = Strain.objects.create(pk=op.STRAIN_ID, organization=self.org, species=species, code="CCA-EOB-2")
        self.zone = ThermalZone.objects.create(pk=op.ZONE_ID, organization=self.org, name="Zone 3",
                                               target_temperature_c=15)
        self.source = self.make_box(1837, "CCA-EOB-2.006", "006")
        self.target = self.make_box(1835, "CCA-EOB-2.004", "004")
        self.sibling = self.make_box(1836, "CCA-EOB-2.005", "005")
        Box.objects.filter(pk=1837).update(status=Box.Status.INACTIVE)
        for box in (self.source, self.target):
            BoxLocation.objects.create(box=box, thermal_zone=self.zone, starts_at=at(1, 1))
        self.make_measurements()

    def make_box(self, pk, code, number):
        return Box.objects.create(pk=pk, organization=self.org, strain=self.strain, thermal_zone=self.zone,
                                  global_code=code, box_number=number, notes=f"Preserve {code}")

    def make_measurements(self):
        self.measurements = {}
        metadata = {
            104358: {"user": self.writer, "notes": "Keep note", "strobila_count": 0, "salinity_psu": "0.00",
                     "culture_status": "medium", "needs_attention": True, "observer_status": "known",
                     "original_observer": self.writer, "origin": "file_import", "import_format": "csv",
                     "imported_by": self.writer},
            104529: {"user": None, "notes": None, "strobila_count": None, "salinity_psu": None,
                     "culture_status": None, "needs_attention": None},
        }
        for index, (pk, (day, _, polyps, ephyrae, precision, sequence)) in enumerate(sorted(op.APPROVED.items())):
            measurement = BiologicalMeasurement.objects.create(
                pk=pk, box=self.source, measured_on=day, polyp_count=polyps, ephyrae_count=ephyrae,
                **metadata.get(pk, {"user": self.writer, "notes": ""}),
            )
            BiologicalMeasurement.objects.filter(pk=pk).update(
                temporal_precision=precision, polyp_state_sequence=sequence,
                created_at=datetime(2026, 11, 1, 10, index, tzinfo=timezone.utc))
            self.measurements[pk] = pk
        # Not approved: unchanged neighbours on both boxes, including a 2025 row.
        self.neighbours = [
            BiologicalMeasurement.objects.create(box=self.source, measured_on=date(2026, 9, 30), polyp_count=7,
                                                 ephyrae_count=0, user=self.writer),
            BiologicalMeasurement.objects.create(box=self.source, measured_on=date(2026, 11, 11), polyp_count=0,
                                                 ephyrae_count=0, user=self.writer),
            BiologicalMeasurement.objects.create(box=self.source, measured_on=date(2025, 10, 8), polyp_count=3,
                                                 ephyrae_count=1, user=self.writer),
            BiologicalMeasurement.objects.create(box=self.target, measured_on=date(2026, 9, 30), polyp_count=11,
                                                 ephyrae_count=0, user=self.writer),
            BiologicalMeasurement.objects.create(box=self.target, measured_on=date(2026, 11, 11), polyp_count=0,
                                                 ephyrae_count=2, user=self.writer),
        ]
        Box.objects.filter(pk=1837).update(polyp_state_revision=80)
        Box.objects.filter(pk=1835).update(polyp_state_revision=30)
        for neighbour in self.neighbours:
            BiologicalMeasurement.objects.filter(pk=neighbour.pk).update(polyp_state_sequence=None)

    # -- helpers
    def plan(self):
        return op.build_plan()

    def apply(self, plan=None, **overrides):
        plan = self.plan() if plan is None else plan
        options = {"actor": self.admin, "reason": REASON, "expected_count": 5,
                   "expected_plan_hash": op.fingerprint(plan)}
        options.update(overrides)
        return op.apply_plan(**options)

    def snapshot_db(self):
        with connection.cursor() as cursor:
            result = {}
            for table in connection.introspection.table_names(cursor):
                cursor.execute(f"SELECT * FROM {connection.ops.quote_name(table)}")
                result[table] = sorted(cursor.fetchall(), key=repr)
            return result

    def assert_blocked(self, text=None):
        plan = self.plan()
        self.assertFalse(plan["ready"])
        if text:
            self.assertIn(text, " | ".join(plan["blockers"]))
        before = self.snapshot_db()
        with self.assertRaises(ValueError):
            self.apply(plan=plan)
        self.assertEqual(self.snapshot_db(), before)

    def add_reading(self, pk=104849, status="confirmed"):
        measurement = BiologicalMeasurement.objects.get(pk=pk)
        _, week, polyps, ephyrae, _, _ = op.APPROVED[pk]
        with transaction.atomic():
            reading = HistoricalTemperatureReading.objects.create(
                biological_measurement=measurement, iso_year=2026, iso_week=week, temperature_c="15.000")
            HistoricalTemperatureEvidence.objects.create(reading=reading, **evidence(
                source_box="CCA-EOB-2.006", polyp_value=str(polyps), ephyrae_value=str(ephyrae),
                week_value=str(week), source_iso_week=week, source_value="15.000"))
        if status == "pending_review":
            # Only the SQL guard's irreversible invalidation is allowed, never the ORM.
            with connection.cursor() as cursor:
                cursor.execute("UPDATE measurements_historicaltemperaturereading SET association_status=%s WHERE id=%s",
                               [status, reading.pk])
        return reading


@skipUnless(connection.vendor == "postgresql", "The reassignment relies on PostgreSQL guards and locks.")
class FiveMeasurementReassignmentTests(FiveFixture, TestCase):
    def test_dry_run_is_deterministic_json_and_writes_nothing(self):
        before = self.snapshot_db()
        plan = self.plan()
        self.assertEqual(self.plan(), plan)
        self.assertEqual(json.loads(json.dumps(plan)), plan)
        self.assertEqual(self.snapshot_db(), before)
        self.assertTrue(plan["ready"], plan["blockers"])
        self.assertEqual(plan["move_order"], sorted(op.APPROVED))
        self.assertEqual(plan["revision_changes"], [{"box_id": 1835, "before": 30, "after": 67},
                                                    {"box_id": 1837, "before": 80, "after": 85}])
        self.assertEqual([row["zone_id"] for row in [e["target"] for e in plan["environment"]]], [3] * 5)
        self.assertEqual(plan["prior_contract"]["measurement_id"], 104689)

    def test_exact_five_rows_move_and_everything_else_is_preserved(self):
        before = {m.pk: {f.attname: getattr(m, f.attname) for f in m._meta.concrete_fields}
                  for m in BiologicalMeasurement.objects.all()}
        boxes_before = {b.pk: {f.attname: getattr(b, f.attname) for f in b._meta.concrete_fields}
                        for b in Box.objects.all()}
        locations = list(BoxLocation.objects.values().order_by("pk"))
        zones = list(ThermalZone.objects.values().order_by("pk"))
        plan = self.apply()
        self.assertIn("receipt_id", plan)
        for pk, row in before.items():
            now = {f.attname: getattr(m, f.attname) for m in [BiologicalMeasurement.objects.get(pk=pk)]
                   for f in m._meta.concrete_fields}
            if pk in op.APPROVED:
                self.assertEqual(now, {**row, "box_id": 1835})
            else:
                self.assertEqual(now, row)
        self.assertEqual(BiologicalMeasurement.objects.filter(box_id=1835, pk__in=op.APPROVED).count(), 5)
        # 0 stays 0 and NULL stays NULL.
        self.assertEqual(BiologicalMeasurement.objects.get(pk=104358).ephyrae_count, 0)
        self.assertIsNone(BiologicalMeasurement.objects.get(pk=104529).strobila_count)
        self.assertEqual(BiologicalMeasurement.objects.get(pk=104358).strobila_count, 0)
        for box in Box.objects.all():
            now = {f.attname: getattr(box, f.attname) for f in box._meta.concrete_fields}
            expected = dict(boxes_before[box.pk])
            if box.pk in (1835, 1837):
                expected["polyp_state_revision"] = {1835: 67, 1837: 85}[box.pk]
            self.assertEqual(now, expected)
        self.assertEqual(Box.objects.get(pk=1837).status, Box.Status.INACTIVE)
        self.assertEqual(Box.objects.get(pk=1835).status, Box.Status.ACTIVE)
        self.assertEqual(list(BoxLocation.objects.values().order_by("pk")), locations)
        self.assertEqual(list(ThermalZone.objects.values().order_by("pk")), zones)
        self.assertEqual(BoxMovement.objects.count(), 0)
        # The revision stays at least every carried sequence, so a new write cannot reuse one.
        created = BiologicalMeasurement.objects.create(box=self.target, measured_on=date(2026, 12, 2),
                                                       polyp_count=1, ephyrae_count=0)
        self.assertEqual(created.polyp_state_sequence, 68)

    def test_audits_receipt_and_prior_contract_are_recorded_without_touching_old_audits(self):
        old = AuditLog.objects.create(
            organization=self.org, user=self.admin, action=AuditLog.Action.ENTRY, object_type="box",
            object_id="CCA-EOB-2.006", description="Older contract", metadata={"measurement_id": 104689, "x": 1})
        old_row = AuditLog.objects.filter(pk=old.pk).values().get()
        plan = self.apply()
        self.assertEqual(AuditLog.objects.filter(pk=old.pk).values().get(), old_row)
        audits = AuditLog.objects.filter(metadata__operation=op.OPERATION, object_type="box")
        self.assertEqual(sorted(a.metadata["measurement_id"] for a in audits), sorted(op.APPROVED))
        one = audits.get(metadata__measurement_id=104689)
        self.assertEqual((one.metadata["previous_box"], one.metadata["new_box"]), ("CCA-EOB-2.006", "CCA-EOB-2.004"))
        self.assertEqual(one.metadata["prior_contract"]["retained_box_id"], 1837)
        self.assertEqual(one.metadata["prior_contract"]["status"], "preserved_unmodified")
        self.assertEqual(one.metadata["before"]["box_id"], 1837)
        self.assertEqual(one.metadata["after"]["box_id"], 1835)
        self.assertEqual(one.user_id, self.admin.pk)
        receipt = AuditLog.objects.get(object_type=op.RECEIPT_OBJECT_TYPE, object_id=op.OPERATION)
        self.assertEqual(receipt.pk, plan["receipt_id"])
        self.assertEqual(sorted(receipt.metadata["audit_ids"]), sorted(audits.values_list("pk", flat=True)))
        self.assertEqual(receipt.metadata["reason"], REASON)
        self.assertEqual(receipt.metadata["plan_hash"], op.fingerprint({k: v for k, v in plan.items()
                                                                          if k != "receipt_id"}))
        self.assertIn("researcher", receipt.metadata["product_decision"]["basis"])

    def test_replay_is_refused_and_never_silent(self):
        plan = self.plan()
        self.apply(plan=plan)
        self.assertIn("already has a receipt", " | ".join(self.plan()["blockers"]))
        with self.assertRaises(ValueError):
            self.apply(plan=plan)
        self.assertEqual(AuditLog.objects.filter(object_type=op.RECEIPT_OBJECT_TYPE).count(), 1)
        self.assertEqual(AuditLog.objects.filter(metadata__operation=op.OPERATION, object_type="box").count(), 5)

    def test_stale_fingerprint_count_reason_actor_and_authority_are_refused(self):
        plan = self.plan()
        before = self.snapshot_db()
        cases = [
            {"expected_plan_hash": "0" * 64}, {"expected_count": 4}, {"expected_count": 6},
            {"reason": ""}, {"reason": "short"}, {"actor": None}, {"expected_plan_hash": ""},
        ]
        for override in cases:
            with self.subTest(override=override), self.assertRaises(ValueError):
                self.apply(plan=plan, **override)
        for actor in (self.technician,):
            with self.assertRaises(PermissionDenied):
                self.apply(plan=plan, actor=actor)
        OrganizationMembership.objects.filter(user=self.admin).update(is_active=False)
        with self.assertRaises(PermissionDenied):
            self.apply(plan=plan)
        OrganizationMembership.objects.filter(user=self.admin).update(is_active=True)
        get_user_model().objects.filter(pk=self.admin.pk).update(is_active=False)
        with self.assertRaises(PermissionDenied):
            self.apply(plan=plan)
        get_user_model().objects.filter(pk=self.admin.pk).update(is_active=True)
        self.assertEqual(self.snapshot_db(), before)

    def test_foreign_organization_admin_is_refused(self):
        foreign = get_user_model().objects.create_user(username="foreign-admin", email="f@example.org")
        OrganizationMembership.objects.create(organization=self.other_org, user=foreign, role="admin")
        with self.assertRaises(PermissionDenied):
            self.apply(actor=foreign)

    def test_plan_changes_after_any_state_change(self):
        plan = self.plan()
        BiologicalMeasurement.objects.filter(pk=self.neighbours[0].pk).update(notes="changed")
        with self.assertRaisesMessage(ValueError, "Stale plan"):
            self.apply(plan=plan)

    def test_destination_week_collisions_block_including_duplicate_evidence(self):
        for week_day in (date(2026, 10, 5), date(2026, 11, 4)):
            with self.subTest(day=week_day), transaction.atomic():
                BiologicalMeasurement.objects.create(box=self.target, measured_on=week_day, polyp_count=1)
                self.assert_blocked("already has a row for this ISO week")
                transaction.set_rollback(True)

    def test_destination_sequence_collision_blocks(self):
        BiologicalMeasurement.objects.filter(pk=self.neighbours[3].pk).update(polyp_state_sequence=24)
        self.assert_blocked("already uses this polyp state sequence")

    def test_historical_temperature_links_block_for_any_status_and_sql_guard_is_active(self):
        for status in ("confirmed", "pending_review"):
            with self.subTest(status=status), transaction.atomic():
                self.add_reading(status=status)
                self.assert_blocked("historical_temperature_readings")
                transaction.set_rollback(True)
        reading = self.add_reading(104358)
        with self.assertRaises(IntegrityError), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("UPDATE measurements_biologicalmeasurement SET box_id=1835 WHERE id=104358")
        self.assertTrue(HistoricalTemperatureReading.objects.filter(pk=reading.pk).exists())
        self.assertEqual(BiologicalMeasurement.objects.get(pk=104358).box_id, 1837)

    def test_incoming_duplicate_environment_override_and_other_references_block(self):
        cases = {
            "incoming_duplicates": lambda: BiologicalMeasurement.objects.bulk_create([BiologicalMeasurement(
                box=self.sibling, measured_on=date(2026, 10, 22), week_start=date(2026, 10, 19),
                polyp_count=69, ephyrae_count=0, duplicate_of_id=104689)]),
            "environment_overrides_to_boxes": lambda: BiologicalMeasurement.objects.filter(
                pk=self.neighbours[3].pk).update(environment_source_box=self.source),
        }
        for label, build in cases.items():
            with self.subTest(label=label), transaction.atomic():
                build()
                self.assert_blocked(label)
                transaction.set_rollback(True)

    def test_subculture_lineage_and_transfer_dependencies_block(self):
        outside = self.sibling
        for kind in ("event", "allocation", "lineage_parent", "lineage_child", "transfer"):
            with self.subTest(kind=kind), transaction.atomic():
                if kind == "event":
                    SubcultureEvent.objects.create(
                        parent_box=self.source, event_date=date(2026, 9, 1), user=self.writer,
                        occurred_at=at(9, 1), parent_state_sequence=22, parent_polyp_count_before=8,
                        allocated_polyp_count=1, parent_polyp_count_after=7,
                        parent_state_snapshot={"measurement_id": 104358})
                    label = "subculture_events"
                elif kind == "allocation":
                    event = SubcultureEvent.objects.create(parent_box=outside, user=self.writer)
                    SubcultureAllocation.objects.create(event=event, child_box=self.target, position=1,
                                                        allocated_polyps=None, child_global_code="CCA-EOB-2.004")
                    label = "subculture_allocations"
                elif kind.startswith("lineage"):
                    pair = (self.source, outside) if kind.endswith("parent") else (outside, self.target)
                    BoxLineage.objects.create(parent_box=pair[0], child_box=pair[1])
                    label = "lineages"
                else:
                    envelope = TransferEnvelope.objects.create(
                        source_organization=self.org, source_institution_id=self.org.portable_id,
                        source_institution_name=self.org.name, created_by=self.writer)
                    TransferItem.objects.create(
                        envelope=envelope, source_box=self.source, source_box_code="CCA-EOB-2.006",
                        source_strain_code=self.strain.code,
                        species_scientific_name=self.strain.species.scientific_name,
                        global_strain_id=uuid4(), declared_polyp_quantity=0)
                    label = "transfer_items"
                self.assert_blocked(label)
                transaction.set_rollback(True)

    def test_scope_preconditions_block(self):
        cases = {
            "values": lambda: BiologicalMeasurement.objects.filter(pk=104849).update(polyp_count=74),
            "box": lambda: BiologicalMeasurement.objects.filter(pk=104849).update(box_id=1836),
            "precision": lambda: BiologicalMeasurement.objects.filter(pk=104358).update(
                temporal_precision="exact_date"),
            "sequence": lambda: BiologicalMeasurement.objects.filter(pk=104358).update(polyp_state_sequence=5),
            "lifecycle": lambda: Box.objects.filter(pk=1837).update(status=Box.Status.ACTIVE),
            "strain": lambda: Box.objects.filter(pk=1835).update(global_code="CCA-EOB-2.007"),
        }
        for label, mutate in cases.items():
            with self.subTest(label=label), transaction.atomic():
                mutate()
                self.assertFalse(self.plan()["ready"])
                with self.assertRaises(ValueError):
                    self.apply(plan=self.plan())
                transaction.set_rollback(True)
        with transaction.atomic():
            BiologicalMeasurement.objects.filter(pk=104849).delete()
            self.assertFalse(self.plan()["ready"])
            transaction.set_rollback(True)

    def test_environment_differences_fail_closed_with_the_specific_difference(self):
        other = ThermalZone.objects.create(organization=self.org, name="Zone 9", target_temperature_c=18)
        with transaction.atomic():
            BoxLocation.objects.filter(box=self.target).update(thermal_zone=other)
            plan = self.plan()
            self.assertFalse(plan["ready"])
            self.assertIn("environmental zone would change from 3 to %s" % other.pk, " | ".join(plan["blockers"]))
            with self.assertRaises(ValueError):
                self.apply(plan=plan)
            transaction.set_rollback(True)
        with transaction.atomic():
            # Same zone but different recorded temperature behaviour is still compared by value.
            BoxLocation.objects.filter(box=self.target).delete()
            Box.objects.filter(pk=1835).update(thermal_zone=None)
            plan = self.plan()
            self.assertFalse(plan["ready"])
            self.assertIn("resolves no thermal zone", " | ".join(plan["blockers"]))
            transaction.set_rollback(True)
        with transaction.atomic():
            BoxLocation.objects.create(box=self.target, thermal_zone=other, starts_at=at(10, 1))
            self.assertIn("several zones", " | ".join(self.plan()["blockers"]))
            transaction.set_rollback(True)
        with transaction.atomic():
            Box.objects.filter(pk=1835).update(organization=self.other_org)
            self.assertFalse(self.plan()["ready"])
            transaction.set_rollback(True)

    def test_environment_override_is_never_invented(self):
        self.apply()
        self.assertEqual(
            BiologicalMeasurement.objects.filter(pk__in=op.APPROVED, environment_source_box__isnull=False).count(), 0)

    def test_exports_resolve_through_the_target_box_after_apply(self):
        from apps.exports.services import build_weekly_measurement_csv, select_measurement_export_data

        for day in (date(2026, 10, 7), date(2026, 10, 14), date(2026, 10, 21), date(2026, 10, 28), date(2026, 11, 4)):
            DailyTemperature.objects.create(thermal_zone=self.zone, date=day, average_temperature_c="15.00")
        before = self.plan()["environment"]
        self.apply()
        _, rows = select_measurement_export_data(
            boxes=Box.objects.filter(pk=1835), date_from=date(2026, 10, 5), date_to=date(2026, 11, 8),
            zone_ids=[3])
        self.assertTrue(set(op.APPROVED) <= {m.pk for m in rows})
        self.assertFalse(BiologicalMeasurement.objects.filter(box_id=1837, pk__in=op.APPROVED).exists())
        self.assertEqual([e["source"]["temperature_c"] for e in before], ["15.00"] * 5)
        self.assertEqual([e["target"]["temperature_c"] for e in before], ["15.00"] * 5)
        self.assertTrue(callable(build_weekly_measurement_csv))

    def test_guards_are_required_present_and_unchanged_after_apply(self):
        before = op._trigger_state()
        self.apply()
        self.assertEqual(op._trigger_state(), before)
        for table, name in op.REQUIRED_TRIGGERS:
            self.assertEqual(before[f"{table}.{name}"], "O")
        with transaction.atomic():
            real = op._trigger_state()
            real.pop("measurements_biologicalmeasurement.historical_biology_guard")
            with patch.object(op, "_trigger_state", return_value=real):
                plan = self.plan()
            self.assertIn("historical_biology_guard", " | ".join(plan["blockers"]))
            transaction.set_rollback(True)
        self.assertEqual(op._trigger_state(), before)

    def test_failure_after_partial_work_rolls_back_every_row_revision_and_audit(self):
        plan = self.plan()
        before = self.snapshot_db()
        real = AuditLog.objects.create
        calls = []

        def flaky(**kwargs):
            calls.append(kwargs)
            if len(calls) == 4:
                raise RuntimeError("simulated audit failure")
            return real(**kwargs)

        with patch.object(AuditLog.objects, "create", side_effect=flaky), self.assertRaises(RuntimeError):
            self.apply(plan=plan)
        self.assertEqual(self.snapshot_db(), before)

    def test_receipt_failure_rolls_back_the_moves(self):
        plan = self.plan()
        before = self.snapshot_db()
        real = AuditLog.objects.create

        def flaky(**kwargs):
            if kwargs.get("object_type") == op.RECEIPT_OBJECT_TYPE:
                raise RuntimeError("receipt failed")
            return real(**kwargs)

        with patch.object(AuditLog.objects, "create", side_effect=flaky), self.assertRaises(RuntimeError):
            self.apply(plan=plan)
        self.assertEqual(self.snapshot_db(), before)

    def test_postcondition_failure_rolls_back(self):
        plan = self.plan()
        before = self.snapshot_db()
        with patch.object(op, "_export_zone_filter_ids", side_effect=[sorted(op.APPROVED), []]), \
                self.assertRaisesMessage(ValueError, "export zone filter"):
            self.apply(plan=plan)
        self.assertEqual(self.snapshot_db(), before)

    def test_command_defaults_to_dry_run_and_requires_all_apply_inputs(self):
        before = self.snapshot_db()
        out = StringIO()
        call_command("reassign_cca_eob_2026_measurements", stdout=out, stderr=StringIO())
        payload = json.loads(out.getvalue())
        self.assertTrue(payload["plan"]["ready"])
        self.assertEqual(self.snapshot_db(), before)
        for kwargs in ({}, {"actor": "five-admin"}, {"actor": "five-admin", "reason": REASON},
                       {"actor": "five-admin", "reason": REASON, "expected_count": 5}):
            with self.assertRaises(CommandError):
                call_command("reassign_cca_eob_2026_measurements", apply=True, stdout=StringIO(),
                             stderr=StringIO(), **kwargs)
        self.assertEqual(self.snapshot_db(), before)
        call_command("reassign_cca_eob_2026_measurements", apply=True, actor="five-admin", reason=REASON,
                     expected_count=5, expected_plan_hash=payload["plan_hash"], stdout=StringIO(), stderr=StringIO())
        self.assertEqual(BiologicalMeasurement.objects.filter(box_id=1835, pk__in=op.APPROVED).count(), 5)
        with self.assertRaises(CommandError):
            call_command("reassign_cca_eob_2026_measurements", stdout=StringIO(), stderr=StringIO())


@skipUnless(connection.vendor == "postgresql", "Concurrency proofs require PostgreSQL.")
class FiveMeasurementReassignmentConcurrencyTests(FiveFixture, TransactionTestCase):
    reset_sequences = False

    def _fixture_teardown(self):
        # TRUNCATE is refused by the historical guards, so remove only synthetic rows.
        BiologicalMeasurement.objects.all().delete()
        AuditLog.objects.all().delete()
        BoxLocation.objects.all().delete()
        Box.objects.all().delete()
        Strain.objects.all().delete()
        ThermalZone.objects.all().delete()
        Organization.objects.all().delete()
        get_user_model().objects.all().delete()
        Species.objects.all().delete()

    def test_repeatable_read_is_refused(self):
        plan = self.plan()
        with self.assertRaises(ValueError), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            self.apply(plan=plan)
        self.assertFalse(BiologicalMeasurement.objects.filter(box_id=1835, pk__in=op.APPROVED).exists())

    def test_two_simultaneous_applications_commit_once(self):
        plan = self.plan()
        expected = op.fingerprint(plan)
        barrier = threading.Barrier(2)

        def run():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                op.apply_plan(actor=self.admin, reason=REASON, expected_count=5, expected_plan_hash=expected)
                return "applied"
            except (ValueError, IntegrityError) as error:
                return f"refused: {error}"
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: run(), range(2)))
        self.assertEqual(sorted(r.split(":")[0] for r in results), ["applied", "refused"])
        self.assertEqual(BiologicalMeasurement.objects.filter(box_id=1835, pk__in=op.APPROVED).count(), 5)
        self.assertEqual(AuditLog.objects.filter(object_type=op.RECEIPT_OBJECT_TYPE).count(), 1)
        self.assertEqual(AuditLog.objects.filter(metadata__operation=op.OPERATION, object_type="box").count(), 5)

    def test_ordinary_write_to_target_waits_for_the_box_lock_and_both_commit(self):
        plan = self.plan()
        expected = op.fingerprint(plan)
        entered, attempted, release = threading.Event(), threading.Event(), threading.Event()
        pids, lock_queries = {}, []
        real = AuditLog.objects.create

        def configure_worker(name):
            with connection.cursor() as cursor:
                cursor.execute("SET lock_timeout = '30s'")
                cursor.execute("SET statement_timeout = '30s'")
                cursor.execute("SET idle_in_transaction_session_timeout = '60s'")
                cursor.execute("SELECT pg_backend_pid()")
                pids[name] = cursor.fetchone()[0]

        def blocking(**kwargs):
            row = real(**kwargs)
            if (kwargs.get("object_type") == "box" and kwargs["metadata"].get("operation") == op.OPERATION
                    and not entered.is_set()):
                entered.set()
                if not release.wait(timeout=60):
                    raise TimeoutError("Reassignment was not released from its locked audit.")
            return row

        def apply_worker():
            close_old_connections()
            try:
                configure_worker("apply")
                with patch.object(AuditLog.objects, "create", side_effect=blocking):
                    return op.apply_plan(actor=self.admin, reason=REASON, expected_count=5, expected_plan_hash=expected)
            finally:
                connections.close_all()

        def observe_box_lock(execute, sql, params, many, context):
            if "FOR UPDATE" in sql and '"cultures_box"' in sql:
                lock_queries.append((sql, tuple(params)))
                attempted.set()
            return execute(sql, params, many, context)

        def writer():
            close_old_connections()
            try:
                configure_worker("write")
                with connection.execute_wrapper(observe_box_lock):
                    return BiologicalMeasurement.objects.create(
                        box_id=1835, measured_on=date(2026, 12, 2), polyp_count=2).pk
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(apply_worker)
            try:
                self.assertTrue(entered.wait(timeout=10), "Reassignment did not reach its locked audit.")
                second = pool.submit(writer)
                self.assertTrue(attempted.wait(timeout=10), "Writer did not attempt the Box lock.")
                self.assertEqual(lock_queries[0][1], (1835,))
                self.assertNotEqual(pids["apply"], pids["write"])
                deadline = monotonic() + 10
                while monotonic() < deadline:
                    with connection.cursor() as cursor:
                        cursor.execute(
                            "SELECT pg_blocking_pids(pid), wait_event_type, query "
                            "FROM pg_stat_activity WHERE pid = %s", [pids["write"]])
                        state = cursor.fetchone()
                    if state and pids["apply"] in state[0] and state[1] == "Lock":
                        self.assertIn('"cultures_box"', state[2])
                        self.assertIn("FOR UPDATE", state[2])
                        break
                    if second.done():
                        second.result()  # Surface a worker error instead of reporting a lock timeout.
                        self.fail("Writer completed before the reassignment released its Box lock.")
                    release.wait(timeout=0.01)
                else:
                    self.fail("PostgreSQL did not report the writer blocked by the reassignment.")
                self.assertFalse(release.is_set())
                self.assertFalse(first.done())
                self.assertFalse(second.done(), "Writer completed while the Box lock was held.")
                self.assertFalse(BiologicalMeasurement.objects.filter(
                    box_id=1835, measured_on=date(2026, 12, 2)).exists())
            finally:
                # Release before executor shutdown, including when any assertion fails.
                release.set()
            applied = first.result(timeout=30)
            newest_id = second.result(timeout=30)
        self.assertEqual(applied["move_order"], plan["move_order"])
        self.assertEqual(BiologicalMeasurement.objects.filter(box_id=1835, pk__in=op.APPROVED).count(), 5)
        self.assertEqual(AuditLog.objects.filter(object_type=op.RECEIPT_OBJECT_TYPE).count(), 1)
        self.assertEqual(AuditLog.objects.filter(metadata__operation=op.OPERATION, object_type="box").count(), 5)
        for pk, approved in op.APPROVED.items():
            self.assertEqual(BiologicalMeasurement.objects.get(pk=pk).polyp_state_sequence, approved[-1])
        revisions = {row["box_id"]: row["after"] for row in plan["revision_changes"]}
        self.assertEqual(Box.objects.get(pk=1837).polyp_state_revision, revisions[1837])
        target = Box.objects.get(pk=1835)
        newest = BiologicalMeasurement.objects.get(pk=newest_id, box_id=1835, measured_on=date(2026, 12, 2))
        self.assertEqual(newest.polyp_state_sequence, revisions[1835] + 1)
        self.assertEqual(newest.polyp_state_sequence, 68)
        self.assertEqual(target.polyp_state_revision, newest.polyp_state_sequence)
