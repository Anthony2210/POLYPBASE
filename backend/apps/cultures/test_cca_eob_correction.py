"""Synthetic regression tests for the reviewed CCA-EOB reassignment."""

import json
import os
from copy import deepcopy
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import connection, transaction
from django.test import SimpleTestCase, TestCase

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.audit.services import serialize_business_details
from apps.measurements import services as measurement_services
from apps.measurements.models import BiologicalMeasurement
from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain

from . import services as culture_services
from .historical_2026 import cca_eob_correction as correction
from .historical_2026.cca_eob_correction import fingerprint
from .models import (
    Box, BoxLineage, BoxLocation, BoxMovement, SubcultureAllocation,
    SubcultureEvent, ThermalZone, TransferEnvelope, TransferItem,
)
from .polyp_state import resolve_current_polyp_state


def json_snapshot(obj):
    return json.loads(json.dumps({
        field.attname: getattr(obj, field.attname)
        for field in obj._meta.concrete_fields
    }, default=str))


class CcaEobFixtureMixin:
    """Small, entirely synthetic source shared with the row-lock tests."""

    workbook_path = "synthetic-cca-eob.xlsx"

    def setUp(self):
        super().setUp()
        self.org = Organization.objects.create(name="CCA correction QA", slug="cca-qa")
        self.other_org = Organization.objects.create(name="Other QA", slug="other-cca-qa")
        self.admin = get_user_model().objects.create_user(
            username="cca-admin", email="cca-admin@example.org",
        )
        self.writer = get_user_model().objects.create_user(
            username="cca-writer", email="cca-writer@example.org",
        )
        for user, role in ((self.admin, "admin"), (self.writer, "lab_technician")):
            OrganizationMembership.objects.create(organization=self.org, user=user, role=role)
        species = Species.objects.create(scientific_name="Cyanea capillata QA")
        self.strain = Strain.objects.create(organization=self.org, species=species, code="CCA-EOB-2")
        self.zone = ThermalZone.objects.create(organization=self.org, name="CCA QA zone")
        self.boxes = {
            number: Box.objects.create(
                organization=self.org, strain=self.strain, thermal_zone=self.zone,
                global_code=f"CCA-EOB-2.{number:03}", box_number=f"{number:03}",
                notes=f"Preserve box {number}",
            ) for number in (4, 5, 6)
        }
        # W32 requires freeing .005 before moving .006. W33 is not that chain.
        specs = (
            (32, 5, 4, 101, 0, 3),
            (32, 6, 5, 202, 2, 5),
            (33, 6, 4, 0, 0, 2),
            (33, 5, 5, 303, 3, 7),
        )
        self.source = {
            "sha256": correction.V2_SHA256, "entries": [],
            "uncovered": [{"target_code": "CCA-EOB-2.004", "iso_year": 2026, "iso_week": 42}],
            "excluded_2025": [{"iso_year": 2025, "polyps": 9, "ephyrae": 0,
                               "reason": "RECAP_2025_COLUMN_E"}],
        }
        self.manifest = {"fingerprint": "synthetic-legacy-fingerprint", "measurements": []}
        self.measurements = []
        for index, (week, old, target, polyps, ephyrae, weekday) in enumerate(specs):
            row = 121 if target == 4 else 125
            column = "AK" if week == 32 else "AL"
            self.source["entries"].append({
                "sheet": "Semaestomeae", "source_identifier": f"CCA-EOB-2.{target:02}",
                "target_code": f"CCA-EOB-2.{target:03}", "iso_year": 2026, "iso_week": week,
                "polyp_cell": f"{column}{row}", "ephyrae_cell": f"{column}{row + 1}",
                "polyps": polyps, "ephyrae": ephyrae,
            })
            self.manifest["measurements"].append({
                "box": f"CCA-EOB-2.{old:03}", "year": 2026, "iso_week": week,
                "polyps": polyps, "ephyrae": ephyrae,
            })
            metadata = {
                "user": self.writer, "notes": "Historical note", "strobila_count": 0,
                "salinity_psu": "0.00", "culture_status": "medium", "needs_attention": True,
                "observer_status": "known", "original_observer": self.writer,
                "origin": "file_import", "import_format": "csv", "imported_by": self.writer,
                "temporal_precision": "exact_date",
            } if index % 2 == 0 else {
                "user": None, "notes": None, "strobila_count": None,
                "salinity_psu": None, "culture_status": None, "needs_attention": None,
            }
            measurement = BiologicalMeasurement.objects.create(
                box=self.boxes[old], measured_on=date.fromisocalendar(2026, week, weekday),
                polyp_count=polyps, ephyrae_count=ephyrae, **metadata,
            )
            BiologicalMeasurement.objects.filter(pk=measurement.pk).update(
                created_at=datetime(2026, 9, 1, 10, index, tzinfo=timezone.utc),
                polyp_state_sequence=(41, 42, None, 97)[index],
            )
            measurement.refresh_from_db()
            self.measurements.append(measurement)
        for number, box in self.boxes.items():
            Box.objects.filter(pk=box.pk).update(polyp_state_revision=300 + number)
        self.source_loader = self.enterContext(patch.object(correction, "load_source", return_value=self.source))
        self.manifest_loader = self.enterContext(patch.object(correction, "load_manifest", return_value=self.manifest))

    def plan(self, organization_id=None):
        return correction.build_plan(
            self.org.pk if organization_id is None else organization_id,
            workbook_path=self.workbook_path,
        )

    def apply(self, *, plan=None, actor=None, **overrides):
        options = {
            "workbook_path": self.workbook_path,
            "actor": self.admin if actor is None else actor,
            "expected_plan_hash": fingerprint(self.plan() if plan is None else plan),
        }
        options.update(overrides)
        return correction.apply_plan(self.org.pk, **options)

    def database_snapshot(self):
        with connection.cursor() as cursor:
            result = {}
            for table in connection.introspection.table_names(cursor):
                cursor.execute(f"SELECT * FROM {connection.ops.quote_name(table)}")
                result[table] = sorted(cursor.fetchall(), key=repr)
            return result

    def assert_blocked(self, plan=None):
        plan = self.plan() if plan is None else plan
        self.assertFalse(plan["ready"])
        self.assertTrue(plan["blockers"])
        before = self.database_snapshot()
        with self.assertRaisesMessage(ValueError, "Unresolved conflicts"):
            self.apply(plan=plan)
        self.assertEqual(self.database_snapshot(), before)


class CcaEobCorrectionTests(CcaEobFixtureMixin, TestCase):
    def test_plan_is_json_deterministic_read_only_and_orders_the_chain(self):
        before = self.database_snapshot()
        plan = self.plan()
        self.assertEqual(json.loads(json.dumps(plan)), plan)
        self.assertEqual(self.plan(), plan)
        self.assertEqual(self.database_snapshot(), before)
        self.source_loader.assert_called_with(self.workbook_path)
        self.manifest_loader.assert_called_with()
        self.assertTrue(plan["ready"], plan["blockers"])
        self.assertEqual(plan["counts"], {correction.MOVE: 3, correction.MATCH: 1})
        entries = plan["entries"]
        self.assertEqual([entry["measurement_pk"] for entry in entries], [m.pk for m in self.measurements])
        self.assertEqual(
            [(entry["iso_week"], entry["current_box"], entry["target_box"]) for entry in entries],
            [(32, "CCA-EOB-2.005", "CCA-EOB-2.004"),
             (32, "CCA-EOB-2.006", "CCA-EOB-2.005"),
             (33, "CCA-EOB-2.006", "CCA-EOB-2.004"),
             (33, "CCA-EOB-2.005", "CCA-EOB-2.005")],
        )
        self.assertLess(plan["move_order"].index(self.measurements[0].pk),
                        plan["move_order"].index(self.measurements[1].pk))
        self.assertEqual(set(plan["move_order"]), {m.pk for m in self.measurements[:3]})
        self.assertEqual(plan["uncovered"], self.source["uncovered"])
        self.assertEqual(plan["excluded_2025"], self.source["excluded_2025"])
        self.assertEqual(plan["legacy_manifest_fingerprint"], self.manifest["fingerprint"])
        self.assertEqual(plan["revision_changes"], [
            {"box_id": box.pk, "before": 300 + number, "after": 302 + number}
            for number, box in self.boxes.items()
        ])

    def test_revision_floor_preserves_incoming_sequences_and_future_consumption(self):
        Box.objects.filter(pk__in=[box.pk for box in self.boxes.values()]).update(polyp_state_revision=0)
        incoming = self.measurements[2]
        BiologicalMeasurement.objects.filter(pk=incoming.pk).update(polyp_state_sequence=701, polyp_count=8)
        self.source["entries"][2]["polyps"] = 8
        self.manifest["measurements"][2]["polyps"] = 8
        before = {m.pk: json_snapshot(m) for m in BiologicalMeasurement.objects.all()}
        plan = self.plan()
        self.assertTrue(plan["ready"], plan["blockers"])
        self.assertEqual(plan, self.plan())
        self.assertEqual(plan["revision_changes"], [
            {"box_id": self.boxes[4].pk, "before": 0, "after": 701},
            {"box_id": self.boxes[5].pk, "before": 0, "after": 97},
            {"box_id": self.boxes[6].pk, "before": 0, "after": 2},
        ])
        self.apply(plan=plan)
        for measurement in BiologicalMeasurement.objects.all():
            expected = before[measurement.pk]
            entry = next(entry for entry in plan["entries"] if entry["measurement_pk"] == measurement.pk)
            expected["box_id"] = entry["target_box_pk"]
            self.assertEqual(json_snapshot(measurement), expected)
        for revision in plan["revision_changes"]:
            self.assertEqual(Box.objects.get(pk=revision["box_id"]).polyp_state_revision, revision["after"])
        summary = AuditLog.objects.get(object_type="historical_correction")
        self.assertEqual(summary.metadata["revisions"], plan["revision_changes"])
        parent = Box.objects.get(pk=self.boxes[4].pk)
        state = resolve_current_polyp_state(parent)
        self.assertEqual(state["polyp_count"], 8)
        self.assertEqual(state["source"]["id"], incoming.pk)
        # A future-dated source must be consumed too, not just an older date.
        occurred_at = datetime(2026, 8, 10, 10, tzinfo=timezone.utc)
        self.assertGreater(incoming.measured_on, occurred_at.date())
        with patch.object(culture_services.timezone, "now", return_value=occurred_at):
            event, children = culture_services.create_subculture(
                parent_box=parent, user=self.admin, organization=self.org,
                expected_current_state_revision=state["revision"],
                children=[{"thermal_zone": self.zone, "allocated_polyps": 3}],
            )
        self.assertEqual(event.parent_state_sequence, 702)
        self.assertEqual((event.parent_polyp_count_before, event.allocated_polyp_count,
                          event.parent_polyp_count_after), (8, 3, 5))
        self.assertEqual(len(children), 1)
        parent.refresh_from_db()
        consumed = resolve_current_polyp_state(parent)
        self.assertEqual(consumed["polyp_count"], 5)
        self.assertEqual(consumed["source"]["kind"], "subculture")
        self.assertEqual(consumed["source"]["id"], event.pk)
        incoming.refresh_from_db()
        self.assertEqual(incoming.polyp_state_sequence, 701)

    def test_apply_preserves_every_field_and_every_unrelated_row(self):
        for year, week, box in ((2025, 32, self.boxes[5]), (2027, 32, self.boxes[6]),
                                (2026, 42, self.boxes[4])):
            BiologicalMeasurement.objects.create(
                box=box, measured_on=date.fromisocalendar(year, week, 4),
                polyp_count=0, ephyrae_count=0, strobila_count=None, notes=None,
            )
        unrelated = Box.objects.create(
            organization=self.org, strain=self.strain, thermal_zone=self.zone,
            global_code="CCA-EOB-2.007", box_number="007",
        )
        location = BoxLocation.objects.create(
            box=unrelated, thermal_zone=self.zone,
            starts_at=datetime(2026, 1, 1, tzinfo=timezone.utc), notes="Historical location",
        )
        BoxMovement.objects.create(
            box=unrelated, to_thermal_zone=self.zone, destination_location=location,
            moved_at=location.starts_at, user=self.writer, notes="Historical movement",
        )
        AuditLog.objects.create(
            organization=self.org, user=self.writer, action=AuditLog.Action.ENTRY,
            object_type="box", object_id=self.boxes[5].global_code, metadata={"legacy": True},
        )
        plan = self.plan()
        self.assertTrue(plan["ready"], plan["blockers"])
        self.assertEqual(plan["counts"][correction.OUTSIDE], 1)
        before = self.database_snapshot()
        measurements = {m.pk: json_snapshot(m) for m in BiologicalMeasurement.objects.all()}
        boxes = {b.pk: json_snapshot(b) for b in Box.objects.all()}
        self.assertEqual(self.apply(plan=plan), plan)
        targets = {self.measurements[0].pk: self.boxes[4].pk,
                   self.measurements[1].pk: self.boxes[5].pk,
                   self.measurements[2].pk: self.boxes[4].pk}
        self.assertEqual(BiologicalMeasurement.objects.count(), len(measurements))
        for measurement in BiologicalMeasurement.objects.all():
            expected = measurements[measurement.pk]
            expected["box_id"] = targets.get(measurement.pk, expected["box_id"])
            self.assertEqual(json_snapshot(measurement), expected)
        for number, increment in ((4, 2), (5, 2), (6, 2)):
            box = Box.objects.get(pk=self.boxes[number].pk)
            expected = boxes[box.pk]
            expected["polyp_state_revision"] += increment
            self.assertEqual(json_snapshot(box), expected)
        after = self.database_snapshot()
        mutable_tables = {Box._meta.db_table, BiologicalMeasurement._meta.db_table, AuditLog._meta.db_table}
        for table in before.keys() - mutable_tables:
            self.assertEqual(after[table], before[table], table)
        for row in before[AuditLog._meta.db_table]:
            self.assertIn(row, after[AuditLog._meta.db_table])

    def test_zero_pair_is_a_real_observation_but_missing_is_not(self):
        plan = self.plan()
        zero = plan["entries"][2]
        self.assertEqual(zero["source_value"], [0, 0])
        self.assertEqual(zero["current_value"], [0, 0])
        self.assertEqual(zero["classification"], correction.MOVE)
        self.measurements[2].delete()
        plan = self.plan()
        self.assertEqual(plan["entries"][2]["classification"], correction.UNKNOWN)
        self.assertIsNone(plan["entries"][2]["measurement_pk"])
        self.assert_blocked(plan)

    def test_occupied_target_same_counts_is_not_merged(self):
        original = self.measurements[0]
        duplicate = BiologicalMeasurement.objects.create(
            box=self.boxes[4], measured_on=original.measured_on,
            polyp_count=original.polyp_count, ephyrae_count=original.ephyrae_count,
        )
        plan = self.plan()
        entry = plan["entries"][0]
        self.assertEqual(entry["classification"], correction.CONFLICT)
        self.assertEqual(set(entry["candidate_pks"]), {original.pk, duplicate.pk})
        self.assert_blocked(plan)

    def test_occupied_target_different_counts_is_not_overwritten(self):
        BiologicalMeasurement.objects.create(
            box=self.boxes[4], measured_on=self.measurements[0].measured_on,
            polyp_count=999, ephyrae_count=999,
        )
        plan = self.plan()
        self.assertTrue(any(e["classification"] == correction.CONFLICT for e in plan["entries"]))
        self.assert_blocked(plan)

    def test_destination_cannot_be_occupied_even_if_its_row_is_already_matched(self):
        # An already-matched row occupies .004; it cannot be deleted to free it.
        BiologicalMeasurement.objects.filter(pk=self.measurements[0].pk).update(box=self.boxes[4])
        self.source["entries"][1]["target_code"] = self.boxes[4].global_code
        plan = self.plan()
        self.assertIn("Occupied destination", " ".join(plan["blockers"]))
        self.assert_blocked(plan)

    def test_duplicate_source_cannot_reuse_one_persisted_measurement(self):
        self.source["entries"].append(deepcopy(self.source["entries"][0]))
        plan = self.plan()
        self.assertEqual(plan["entries"][0]["classification"], correction.CONFLICT)
        self.assertEqual(plan["entries"][-1]["classification"], correction.CONFLICT)
        self.assert_blocked(plan)

    def test_absent_mismatched_and_duplicate_legacy_witnesses_block(self):
        original = deepcopy(self.manifest["measurements"])
        variants = []
        variants.append(original[1:])
        mismatch = deepcopy(original)
        mismatch[0]["polyps"] += 1
        variants.append(mismatch)
        wrong_week = deepcopy(original)
        wrong_week[0]["iso_week"] = 31
        variants.append(wrong_week)
        wrong_year = deepcopy(original)
        wrong_year[0]["year"] = 2025
        variants.append(wrong_year)
        variants.append(original + [deepcopy(original[0])])
        for rows in variants:
            with self.subTest(rows=rows):
                self.manifest["measurements"] = rows
                plan = self.plan()
                self.assertEqual(plan["entries"][0]["classification"], correction.UNKNOWN)
                self.assert_blocked(plan)

    def test_counts_do_not_authorize_searching_an_unwitnessed_old_box(self):
        self.manifest["measurements"][0]["box"] = self.boxes[6].global_code
        plan = self.plan()
        self.assertEqual(plan["entries"][0]["candidate_pks"], [])
        self.assert_blocked(plan)

    def test_mismatched_current_counts_block_without_guessing(self):
        BiologicalMeasurement.objects.filter(pk=self.measurements[0].pk).update(polyp_count=0)
        self.assert_blocked()

    def test_observation_date_must_agree_with_stored_iso_key(self):
        BiologicalMeasurement.objects.filter(pk=self.measurements[0].pk).update(
            measured_on=date.fromisocalendar(2026, 31, 3),
        )
        plan = self.plan()
        self.assertEqual(plan["entries"][0]["classification"], correction.CONFLICT)
        self.assertIn("ISO key disagree", " ".join(plan["entries"][0]["issues"]))
        self.assert_blocked(plan)

    def test_exact_organization_and_codes_exclude_decoys(self):
        decoys = []
        for number in (4, 5, 6):
            box = Box.objects.create(
                organization=self.other_org, strain=self.strain,
                global_code=f"CCA-EOB-2.{number + 100:03}", box_number=f"{number + 100:03}",
            )
            decoys.append(box)
        decoys.append(Box.objects.create(
            organization=self.org, strain=self.strain, global_code="CCA-EOB-2.004-extra", box_number="999",
        ))
        for box in decoys:
            BiologicalMeasurement.objects.create(
                box=box, measured_on=self.measurements[0].measured_on, polyp_count=101, ephyrae_count=0,
            )
        baseline = {box.pk: json_snapshot(box) for box in Box.objects.filter(pk__in=[b.pk for b in decoys])}
        rows = list(BiologicalMeasurement.objects.filter(box__in=decoys).order_by("pk").values())
        plan = self.plan()
        self.assertEqual({b["id"] for b in plan["baseline"]["boxes"]}, {b.pk for b in self.boxes.values()})
        self.assertEqual({m["id"] for m in plan["baseline"]["measurements"]}, {m.pk for m in self.measurements})
        self.apply(plan=plan)
        self.assertEqual({box.pk: json_snapshot(box) for box in Box.objects.filter(pk__in=baseline)}, baseline)
        self.assertEqual(list(BiologicalMeasurement.objects.filter(box__in=decoys).order_by("pk").values()), rows)

    def test_box_in_another_organization_cannot_complete_the_exact_scope(self):
        Box.objects.filter(pk=self.boxes[4].pk).update(organization=self.other_org)
        self.assert_blocked()
        self.assertFalse(self.plan(organization_id=max(self.org.pk, self.other_org.pk) + 100)["ready"])

    def test_apply_requires_explicit_actor_and_hash(self):
        for options in ({"actor": None}, {"actor": get_user_model()()}, {"expected_plan_hash": ""}):
            with self.subTest(options=options):
                before = self.database_snapshot()
                kwargs = {"actor": self.admin, "expected_plan_hash": fingerprint(self.plan())}
                kwargs.update(options)
                with self.assertRaisesMessage(ValueError, "explicit active administrator"):
                    correction.apply_plan(self.org.pk, workbook_path=self.workbook_path, **kwargs)
                self.assertEqual(self.database_snapshot(), before)

    def test_only_active_admin_of_explicit_organization_can_apply(self):
        for role, member_active, user_active, foreign, superuser in (
            ("viewer", True, True, False, False),
            ("lab_technician", True, True, False, False),
            ("admin", False, True, False, False),
            ("admin", True, False, False, False),
            ("admin", True, True, True, False),
            (None, True, True, False, True),
        ):
            with self.subTest(role=role, member_active=member_active, user_active=user_active,
                              foreign=foreign, superuser=superuser), transaction.atomic():
                actor = get_user_model().objects.create_user(
                    username="unauthorized-cca", email="unauthorized-cca@example.org",
                    is_active=user_active, is_superuser=superuser, is_staff=superuser,
                )
                if role:
                    OrganizationMembership.objects.create(
                        organization=self.other_org if foreign else self.org,
                        user=actor, role=role, is_active=member_active,
                    )
                before = self.database_snapshot()
                with self.assertRaises(PermissionDenied):
                    self.apply(actor=actor)
                self.assertEqual(self.database_snapshot(), before)
                transaction.set_rollback(True)

    def test_authorization_reloads_persisted_user_and_membership(self):
        plan = self.plan()
        get_user_model().objects.filter(pk=self.admin.pk).update(is_active=False)
        self.assertTrue(self.admin.is_active)
        before = self.database_snapshot()
        with self.assertRaises(PermissionDenied):
            self.apply(plan=plan)
        self.assertEqual(self.database_snapshot(), before)

    def test_inactive_boxes_keep_status_and_can_receive_historical_reassignment(self):
        Box.objects.filter(pk__in=[b.pk for b in self.boxes.values()]).update(
            status=Box.Status.INACTIVE, thermal_zone=None, deactivated_on=date(2026, 9, 1),
            stop_reason="Historical stop",
        )
        plan = self.plan()
        self.assertTrue(plan["ready"], plan["blockers"])
        self.apply(plan=plan)
        self.assertEqual(Box.objects.filter(status=Box.Status.INACTIVE).count(), 3)
        self.assertEqual(BiologicalMeasurement.objects.get(pk=self.measurements[2].pk).box_id, self.boxes[4].pk)

    def test_foreign_historical_attribution_accounts_block(self):
        outsider = get_user_model().objects.create_user(username="outside-cca", email="outside-cca@example.org")
        OrganizationMembership.objects.create(organization=self.other_org, user=outsider, role="admin")
        for field in ("original_observer_id", "imported_by_id"):
            with self.subTest(field=field), transaction.atomic():
                BiologicalMeasurement.objects.filter(pk=self.measurements[0].pk).update(**{field: outsider.pk})
                plan = self.plan()
                self.assertEqual(plan["entries"][0]["classification"], correction.CONFLICT)
                self.assert_blocked(plan)
                transaction.set_rollback(True)

    def test_persisted_metadata_status_and_revision_changes_stale_the_hash(self):
        for model, pk, changes in (
            (BiologicalMeasurement, self.measurements[0].pk, {"notes": "Changed note only"}),
            (BiologicalMeasurement, self.measurements[1].pk, {"strobila_count": 0}),
            (BiologicalMeasurement, self.measurements[0].pk, {"measured_on": date.fromisocalendar(2026, 32, 4)}),
            (BiologicalMeasurement, self.measurements[0].pk, {"polyp_state_sequence": 999}),
            (Box, self.boxes[4].pk, {"notes": "Changed box metadata"}),
            (Box, self.boxes[4].pk, {"status": Box.Status.INACTIVE}),
            (Box, self.boxes[4].pk, {"polyp_state_revision": 999}),
        ):
            with self.subTest(model=model.__name__, changes=changes), transaction.atomic():
                plan = self.plan()
                model.objects.filter(pk=pk).update(**changes)
                refreshed = self.plan()
                self.assertTrue(refreshed["ready"], refreshed["blockers"])
                self.assertNotEqual(fingerprint(plan), fingerprint(refreshed))
                before = self.database_snapshot()
                with self.assertRaisesMessage(ValueError, "Stale plan"):
                    self.apply(plan=plan)
                self.assertEqual(self.database_snapshot(), before)
                transaction.set_rollback(True)

    def test_source_and_manifest_changes_stale_the_reviewed_hash(self):
        plan = self.plan()
        changed_source = deepcopy(self.source)
        changed_source["entries"][0]["polyp_cell"] = "AK123"
        changed_hash = deepcopy(self.source)
        changed_hash["sha256"] = "a" * 64
        changed_counts = deepcopy(self.source)
        changed_counts["entries"][0]["polyps"] += 1
        changed_manifest = deepcopy(self.manifest)
        changed_manifest["fingerprint"] = "new-synthetic-legacy-fingerprint"
        for loader, value in (("load_source", changed_source), ("load_source", changed_hash),
                              ("load_source", changed_counts), ("load_manifest", changed_manifest)):
            with self.subTest(loader=loader, value=value), patch.object(correction, loader, return_value=value):
                self.assertNotEqual(fingerprint(plan), fingerprint(self.plan()))
                before = self.database_snapshot()
                with self.assertRaisesMessage(ValueError, "Stale plan"):
                    self.apply(plan=plan)
                self.assertEqual(self.database_snapshot(), before)

    def test_idempotence_requires_a_refreshed_plan_and_creates_no_more_audit(self):
        original = self.plan()
        self.apply(plan=original)
        before = self.database_snapshot()
        with self.assertRaisesMessage(ValueError, "Stale plan"):
            self.apply(plan=original)
        self.assertEqual(self.database_snapshot(), before)
        refreshed = self.plan()
        self.assertNotEqual(fingerprint(original), fingerprint(refreshed))
        self.assertTrue(refreshed["ready"], refreshed["blockers"])
        self.assertEqual(refreshed["counts"], {correction.MATCH: 4})
        self.assertEqual(refreshed["move_order"], [])
        self.assertEqual(refreshed["revision_changes"], [])
        self.assertEqual(self.apply(plan=refreshed), refreshed)
        self.assertEqual(self.database_snapshot(), before)

    def test_audits_record_real_actor_exact_before_after_and_reviewed_evidence(self):
        plan = self.plan()
        self.apply(plan=plan)
        audits = list(AuditLog.objects.filter(metadata__operation=correction.OPERATION).order_by("pk"))
        self.assertEqual(len(audits), 4)
        by_pk = {entry["measurement_pk"]: entry for entry in plan["entries"]}
        for audit in audits[:3]:
            entry = by_pk[audit.metadata["measurement_id"]]
            measurement = BiologicalMeasurement.objects.get(pk=entry["measurement_pk"])
            self.assertEqual((audit.organization_id, audit.user_id, audit.action, audit.object_type),
                             (self.org.pk, self.admin.pk, AuditLog.Action.UPDATE, "box"))
            self.assertEqual(audit.object_id, entry["target_box"])
            self.assertEqual(audit.description,
                             f"{correction.REASON} {entry['current_box']} -> {entry['target_box']}; "
                             f"2026-W{entry['iso_week']:02}.")
            self.assertEqual(audit.metadata["modifications"], {
                "boite": {"avant": entry["current_box"], "apres": entry["target_box"]},
            })
            self.assertEqual(audit.metadata["valeurs"], {
                "date": measurement.measured_on.isoformat(), "polypes": measurement.polyp_count,
                "ephyrules": measurement.ephyrae_count, "strobiles": measurement.strobila_count,
                "salinite_psu": str(measurement.salinity_psu) if measurement.salinity_psu is not None else None,
                "statut_culture": measurement.culture_status, "a_verifier": measurement.needs_attention,
                "note": measurement.notes,
            })
            self.assertEqual(audit.metadata["before"], entry["before"])
            self.assertEqual(audit.metadata["after"], json_snapshot(measurement))
            self.assertEqual(audit.metadata["after"], {**entry["before"], "box_id": entry["target_box_pk"]})
            self.assertEqual(audit.metadata["previous_box"], entry["current_box"])
            self.assertEqual(audit.metadata["new_box"], entry["target_box"])
            self.assertEqual(audit.metadata["plan_hash"], fingerprint(plan))
            self.assertEqual(audit.metadata["v2_sha256"], self.source["sha256"])
            self.assertEqual(audit.metadata["legacy_manifest_fingerprint"], self.manifest["fingerprint"])
            self.assertEqual(audit.metadata["source"], {
                key: entry[key] for key in ("sheet", "polyp_cell", "ephyrae_cell", "source_identifier",
                                           "target_code", "iso_year", "iso_week", "polyps", "ephyrae")
            })
            self.assertNotEqual(measurement.user_id, self.admin.pk)
        summary = audits[-1]
        self.assertEqual((summary.organization_id, summary.user_id, summary.action, summary.object_type),
                         (self.org.pk, self.admin.pk, AuditLog.Action.IMPORT, "historical_correction"))
        self.assertEqual(summary.object_id, correction.OPERATION)
        self.assertEqual(summary.metadata["moved"], 3)
        self.assertEqual(summary.metadata["reviewed_plan"], plan)
        self.assertEqual(fingerprint(summary.metadata["reviewed_plan"]), fingerprint(plan))
        self.assertEqual({r["box_id"]: (r["before"], r["after"]) for r in summary.metadata["revisions"]},
                         {box.pk: (300 + number, 302 + number) for number, box in self.boxes.items()})

    def test_serialized_audit_values_stay_frozen_after_later_ordinary_corrections(self):
        self.apply()
        audits = list(AuditLog.objects.filter(
            metadata__operation=correction.OPERATION, action=AuditLog.Action.UPDATE,
        ).order_by("pk"))
        self.assertEqual(len(audits), 3)
        for audit in audits:
            with self.subTest(measurement_id=audit.metadata["measurement_id"]):
                measurement = BiologicalMeasurement.objects.get(pk=audit.metadata["measurement_id"])
                original_metadata = deepcopy(audit.metadata)
                original_details = serialize_business_details(audit, measurement=measurement)
                self.assertEqual(original_details["type"], "measurement")
                self.assertEqual(original_details["values"], audit.metadata["valeurs"])
                self.assertEqual(serialize_business_details(audit), original_details)
                measurement_services.correct_biological_measurement(
                    box_id=measurement.box_id, measurement_id=measurement.pk,
                    organization_ids=[self.org.pk], user=self.admin,
                    data={"polyp_count": 909, "ephyrae_count": 808, "strobila_count": 7,
                          "salinity_psu": "31.25", "culture_status": "bad",
                          "needs_attention": False, "notes": "Later ordinary correction"},
                )
                measurement.refresh_from_db()
                audit.refresh_from_db()
                self.assertEqual(measurement.polyp_count, 909)
                self.assertEqual(audit.metadata, original_metadata)
                self.assertEqual(serialize_business_details(audit, measurement=measurement), original_details)
                self.assertEqual(serialize_business_details(audit), original_details)

    def test_later_measurement_or_summary_audit_failure_rolls_back_everything(self):
        for fail_at in (2, 4):
            with self.subTest(fail_at=fail_at):
                plan = self.plan()
                before = self.database_snapshot()
                real_create = AuditLog.objects.create
                calls = []

                def failing_create(**kwargs):
                    calls.append(kwargs)
                    if len(calls) == fail_at:
                        raise RuntimeError("Synthetic later audit failure")
                    return real_create(**kwargs)

                with patch.object(AuditLog.objects, "create", side_effect=failing_create):
                    with self.assertRaisesMessage(RuntimeError, "Synthetic later audit failure"):
                        self.apply(plan=plan)
                self.assertEqual(len(calls), fail_at)
                self.assertEqual(self.database_snapshot(), before)
                self.assertEqual(self.plan(), plan)

    def test_subculture_lineage_and_transfer_histories_block_the_entire_correction(self):
        outside = Box.objects.create(
            organization=self.org, strain=self.strain, global_code="CCA-EOB-2.099", box_number="099",
        )
        for kind in ("subculture_events", "subculture_allocations", "lineages_parent",
                     "lineages_child", "transfer_items"):
            with self.subTest(kind=kind), transaction.atomic():
                if kind == "subculture_events":
                    SubcultureEvent.objects.create(
                        parent_box=self.boxes[5], event_date=date(2026, 9, 1), user=self.writer,
                        occurred_at=datetime(2026, 9, 1, tzinfo=timezone.utc), parent_state_sequence=41,
                        parent_polyp_count_before=101, allocated_polyp_count=1, parent_polyp_count_after=100,
                        parent_state_snapshot={"measurement_id": self.measurements[0].pk, "polyps": 101},
                    )
                elif kind == "subculture_allocations":
                    event = SubcultureEvent.objects.create(parent_box=outside, user=self.writer)
                    SubcultureAllocation.objects.create(
                        event=event, child_box=self.boxes[4], position=1, allocated_polyps=None,
                        child_global_code=self.boxes[4].global_code,
                    )
                elif kind.startswith("lineages"):
                    parent, child = (self.boxes[6], outside) if kind.endswith("parent") else (outside, self.boxes[4])
                    BoxLineage.objects.create(parent_box=parent, child_box=child, notes="Preserve lineage")
                else:
                    envelope = TransferEnvelope.objects.create(
                        source_organization=self.org, source_institution_id=self.org.portable_id,
                        source_institution_name=self.org.name, created_by=self.writer,
                    )
                    TransferItem.objects.create(
                        envelope=envelope, source_box=self.boxes[5], source_box_code=self.boxes[5].global_code,
                        source_strain_code=self.strain.code, species_scientific_name=self.strain.species.scientific_name,
                        global_strain_id=uuid4(), declared_polyp_quantity=0,
                    )
                plan = self.plan()
                history_key = "lineages" if kind.startswith("lineages") else kind
                self.assertTrue(plan["baseline"]["histories"][history_key])
                self.assertIn("independent resolution", " ".join(plan["blockers"]))
                self.assert_blocked(plan)
                transaction.set_rollback(True)


class CcaEobDatabaseGuardTests(SimpleTestCase):
    pg_config = {"ENGINE": "django.db.backends.postgresql", "HOST": "127.0.0.1",
                 "PORT": "55439", "NAME": "test_subculture_qa"}
    clean_environment = {"PGSERVICE": "", "PGSERVICEFILE": "", "PGHOSTADDR": "127.0.0.1",
                         "PGOPTIONS": ""}

    def check_config(self, config, *, environment=None):
        fake = Mock(settings_dict=config)
        with patch.object(correction, "connection", fake), patch.dict(
            os.environ, {**self.clean_environment, **(environment or {})},
        ):
            try:
                correction.require_local_database()
            finally:
                fake.ensure_connection.assert_not_called()
                fake.cursor.assert_not_called()

    def test_allows_only_memory_sqlite_variants(self):
        for name in (":memory:", "file:memorydb_default?mode=memory&cache=shared"):
            with self.subTest(name=name):
                self.check_config({"ENGINE": "django.db.backends.sqlite3", "NAME": name})
        for name in ("db.sqlite3", "file:memorydb_default", "file:other?mode=memory",
                     "file:memorydb_default?not_mode=memory", "file:memorydb_default?mode=memory2",
                     "file:memorydb_default?mode=memory&mode=rw",
                     "file:memorydb_default?mode=rw&not_mode=memory",
                     "file:memorydb_default#mode=memory"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.check_config({"ENGINE": "django.db.backends.sqlite3", "NAME": name})

    def test_sqlite_options_cannot_replace_the_memory_database(self):
        for options in ({"database": "other.sqlite3"}, {"init_command": "ATTACH DATABASE 'other.sqlite3' AS other"}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.check_config({"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:", "OPTIONS": options})

    def test_allows_explicit_postgresql_test_settings_target_not_near_matches(self):
        config = {"ENGINE": "django.db.backends.postgresql", "HOST": "127.0.0.1",
                  "PORT": "55439", "NAME": "test_subculture_qa"}
        self.check_config(config)
        self.check_config({**config, "PORT": 55439})
        for override in ({"HOST": "localhost"}, {"HOST": "db.example.invalid"},
                         {"PORT": "5432"}, {"NAME": "subculture_qa"},
                         {"NAME": "test_subculture_qa_extra"}, {"ENGINE": "django.db.backends.mysql"}):
            with self.subTest(override=override), self.assertRaises(ValueError):
                self.check_config({**config, **override})

    def test_postgresql_options_cannot_redirect_connection(self):
        for options in (
            {"dbname": "other_database"}, {"host": "db.example.invalid"},
            {"hostaddr": "192.0.2.1"}, {"port": "5432"},
            {"service": "external-service"}, {"servicefile": "synthetic-service.conf"},
            {"options": "-c statement_timeout=30000", "dbname": "other_database"},
        ):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.check_config({**self.pg_config, "OPTIONS": options})
        self.check_config({**self.pg_config, "OPTIONS": {
            "options": "-c lock_timeout=10000 -c statement_timeout=30000",
            "connect_timeout": 5, "sslmode": "disable",
        }})
        self.check_config(self.pg_config, environment={"PGOPTIONS": "-c statement_timeout=30000"})

    def test_libpq_environment_cannot_redirect_an_approved_config(self):
        for environment in ({"PGSERVICE": "external-service"},
                            {"PGSERVICEFILE": "synthetic-service.conf"},
                            {"PGHOSTADDR": "192.0.2.1"}, {"PGHOSTADDR": ""}):
            with self.subTest(environment=environment), self.assertRaises(ValueError):
                self.check_config(self.pg_config, environment=environment)

    def test_effective_psycopg_connection_is_verified_without_sql(self):
        approved = {"hostaddr": "127.0.0.1", "port": 55439, "dbname": "test_subculture_qa"}
        for override in ({}, {"hostaddr": "192.0.2.1"}, {"port": 5432}, {"dbname": "other_database"}):
            fake = Mock(vendor="postgresql")
            fake.connection.info = SimpleNamespace(**{**approved, **override})
            with self.subTest(override=override), patch.object(correction, "connection", fake):
                if override:
                    with self.assertRaisesMessage(ValueError, "effective PostgreSQL connection"):
                        correction._verify_connection()
                else:
                    correction._verify_connection()
                fake.ensure_connection.assert_called_once_with()
                fake.cursor.assert_not_called()

    def test_build_and_apply_reject_effective_redirection_before_sources_or_sql(self):
        for operation in ("build_plan", "apply_plan"):
            fake = Mock(vendor="postgresql", settings_dict=self.pg_config)
            fake.connection.info = SimpleNamespace(hostaddr="127.0.0.1", port=55439, dbname="other_database")
            with self.subTest(operation=operation), patch.object(correction, "connection", fake), patch.dict(
                os.environ, self.clean_environment,
            ), patch.object(correction, "load_source") as source, patch.object(correction, "load_manifest") as manifest:
                kwargs = {"workbook_path": "synthetic.xlsx"}
                if operation == "apply_plan":
                    kwargs.update(actor=SimpleNamespace(pk=1), expected_plan_hash="reviewed-hash")
                with self.assertRaisesMessage(ValueError, "effective PostgreSQL connection"):
                    getattr(correction, operation)(1, **kwargs)
                fake.ensure_connection.assert_called_once_with()
                fake.cursor.assert_not_called()
                source.assert_not_called()
                manifest.assert_not_called()
