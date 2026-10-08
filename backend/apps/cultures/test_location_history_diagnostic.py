import json
from contextlib import nullcontext
from datetime import date, datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from io import StringIO
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import DatabaseError, connection
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.measurements.models import BiologicalMeasurement
from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain

from . import location_history_diagnostics as diagnostics
from .location_history_diagnostics import diagnose_box_location_history
from .models import Box, BoxLocation, BoxMovement, ThermalZone
from .services import move_box_to_thermal_zone


COMMAND = "diagnose_box_location_history"
T0 = datetime(2026, 1, 5, 8, 0, tzinfo=dt_timezone.utc)


def at(days):
    return T0 + timedelta(days=days)


class ReadOnlySnapshotTests(SimpleTestCase):
    def test_postgresql_outermost_run_requests_read_only_snapshot(self):
        conn = MagicMock(vendor="postgresql", in_atomic_block=False, alias="default")
        cursor = conn.cursor.return_value.__enter__.return_value
        with patch.object(diagnostics.transaction, "atomic", return_value=nullcontext()) as atomic:
            with diagnostics._read_only_snapshot(conn) as active:
                self.assertTrue(active)
        atomic.assert_called_once_with(using="default")
        cursor.execute.assert_called_once_with("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")

    def test_postgresql_read_only_setup_failure_propagates(self):
        conn = MagicMock(vendor="postgresql", in_atomic_block=False, alias="default")
        conn.cursor.return_value.__enter__.return_value.execute.side_effect = DatabaseError("setup failed")
        with patch.object(diagnostics.transaction, "atomic", return_value=nullcontext()):
            with self.assertRaisesMessage(DatabaseError, "setup failed"):
                with diagnostics._read_only_snapshot(conn):
                    self.fail("A failed read-only setup must not run the diagnostic")

    def test_nested_or_non_postgresql_run_issues_no_statement(self):
        for vendor, nested in (("sqlite", False), ("postgresql", True)):
            conn = MagicMock(vendor=vendor, in_atomic_block=nested, alias="default")
            with diagnostics._read_only_snapshot(conn) as active:
                self.assertFalse(active)
            conn.cursor.assert_not_called()


class LocationHistoryDiagnosticTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="diag_tech", email="tech@example.org", first_name="Ada", last_name="Lab", password="x",
        )
        self.organization = Organization.objects.create(name="Diagnostic laboratory")
        self.other_organization = Organization.objects.create(name="Foreign laboratory")
        self.membership = OrganizationMembership.objects.create(user=self.user, organization=self.organization)
        self.species = Species.objects.create(scientific_name="Aurelia diagnostic", genus_species_code="ADG")
        self.strain = Strain.objects.create(
            species=self.species, code="ADG-LAB-1", organization=self.organization, number=1, origin_code="LAB",
        )
        self.zone_a = ThermalZone.objects.create(organization=self.organization, name="Zone A", target_temperature_c=0)
        self.zone_b = ThermalZone.objects.create(organization=self.organization, name="Zone B", target_temperature_c=15)
        self.foreign_zone = ThermalZone.objects.create(
            organization=self.other_organization, name="Foreign secret zone", target_temperature_c=21,
        )

    def box(self, code, *, zone=None, status=Box.Status.ACTIVE, organization=None):
        return Box.objects.create(
            organization=organization or self.organization, global_code=code, box_number=code[-3:],
            strain=self.strain, thermal_zone=zone, status=status,
        )

    def period(self, box, zone, start, end=None, *, unknown=False, notes=""):
        return BoxLocation.objects.create(
            box=box, thermal_zone=zone, starts_at=start, ends_at=end, end_date_unknown=unknown, notes=notes,
        )

    def report(self, *codes):
        return diagnose_box_location_history(organization=self.organization, global_codes=list(codes))

    def single(self, code):
        report = self.report(code)
        self.assertEqual(report["unresolved_global_codes"], [])
        self.assertEqual(len(report["boxes"]), 1)
        return report["boxes"][0]

    def codes(self, findings):
        return [item["code"] for item in findings]

    def run_command(self, *codes, fmt="text", organization=None):
        out = StringIO()
        args = ["--organization-id", str((organization or self.organization).pk), "--format", fmt]
        for code in codes:
            args += ["--global-code", code]
        call_command(COMMAND, *args, stdout=out)
        return out.getvalue()

    def snapshot(self):
        return {
            model.__name__: list(model.objects.order_by("pk").values())
            for model in (Box, BoxLocation, BoxMovement, AuditLog, BiologicalMeasurement, ThermalZone)
        }

    def test_one_normal_current_location(self):
        box = self.box("ADG-LAB-1.001", zone=self.zone_a)
        location = self.period(box, self.zone_a, at(0))
        result = self.single(box.global_code)

        self.assertEqual(result["current_state"]["thermal_zone"]["id"], self.zone_a.pk)
        self.assertEqual(result["current_state"]["open_period_ids"], [location.pk])
        self.assertEqual([p["state"] for p in result["location_periods"]], ["OPEN"])
        self.assertEqual(result["inconsistencies"], [])
        self.assertEqual(result["observations"], [])
        self.assertEqual(result["persisted_movements"], [])

    def test_repeated_stays_are_preserved_separately_with_one_open_current_period(self):
        box = self.box("ADG-LAB-1.002", zone=self.zone_a)
        first = self.period(box, self.zone_a, at(0), at(7))
        middle = self.period(box, self.zone_b, at(7), at(14))
        current = self.period(box, self.zone_a, at(14))
        result = self.single(box.global_code)

        periods = result["location_periods"]
        self.assertEqual([p["id"] for p in periods], [first.pk, middle.pk, current.pk])
        self.assertEqual([p["thermal_zone"]["id"] for p in periods], [self.zone_a.pk, self.zone_b.pk, self.zone_a.pk])
        self.assertEqual([p["state"] for p in periods], ["CLOSED", "CLOSED", "OPEN"])
        self.assertEqual(periods[0]["duration_seconds"], 7 * 86400)
        self.assertEqual(result["current_state"]["open_period_ids"], [current.pk])
        self.assertEqual(result["inconsistencies"], [])
        self.assertEqual(result["observations"], [])

    def test_gap_is_visible_and_not_filled(self):
        box = self.box("ADG-LAB-1.003", zone=self.zone_b)
        first = self.period(box, self.zone_a, at(0), at(7))
        later = self.period(box, self.zone_b, at(21))
        result = self.single(box.global_code)

        self.assertEqual(len(result["location_periods"]), 2)
        self.assertEqual(result["inconsistencies"], [])
        gap = result["observations"][0]
        self.assertEqual(gap["code"], "GAP_IN_KNOWN_COVERAGE")
        self.assertEqual(gap["period_ids"], [first.pk, later.pk])
        self.assertEqual(gap["gap_seconds"], 14 * 86400)
        self.assertTrue(any("is not recorded" in fact for fact in result["undetermined"]))

    def test_overlapping_periods_are_detected(self):
        box = self.box("ADG-LAB-1.004", zone=self.zone_b)
        first = self.period(box, self.zone_a, at(0), at(10))
        second = self.period(box, self.zone_b, at(7))
        result = self.single(box.global_code)

        overlaps = [item for item in result["inconsistencies"] if item["code"] == "OVERLAPPING_PERIODS"]
        self.assertEqual([item["period_ids"] for item in overlaps], [[first.pk, second.pk]])

    def test_multiple_open_periods_are_detected(self):
        box = self.box("ADG-LAB-1.005", zone=self.zone_b)
        first = self.period(box, self.zone_a, at(0))
        second = self.period(box, self.zone_b, at(7))
        result = self.single(box.global_code)

        codes = self.codes(result["inconsistencies"])
        self.assertIn("MULTIPLE_OPEN_PERIODS", codes)
        self.assertIn("OVERLAPPING_PERIODS", codes)
        self.assertEqual(result["current_state"]["open_period_ids"], [first.pk, second.pk])

    def test_current_pointer_inconsistent_with_open_period_is_detected(self):
        box = self.box("ADG-LAB-1.006", zone=self.zone_b)
        self.period(box, self.zone_a, at(0))
        result = self.single(box.global_code)
        self.assertEqual(self.codes(result["inconsistencies"]), ["CURRENT_POINTER_MISMATCH"])

        pointer_only = self.box("ADG-LAB-1.007", zone=self.zone_a)
        self.period(pointer_only, self.zone_a, at(0), at(3))
        self.assertEqual(
            self.codes(self.single(pointer_only.global_code)["inconsistencies"]), ["CURRENT_POINTER_MISMATCH"],
        )

    def test_inactive_box_with_open_period_is_detected(self):
        box = self.box("ADG-LAB-1.008", status=Box.Status.INACTIVE)
        self.period(box, self.zone_a, at(0))
        codes = self.codes(self.single(box.global_code)["inconsistencies"])
        self.assertIn("INACTIVE_WITH_CURRENT_LOCATION", codes)
        self.assertIn("CURRENT_POINTER_MISMATCH", codes)

    def test_period_transition_without_movement_is_not_a_movement(self):
        box = self.box("ADG-LAB-1.009", zone=self.zone_b)
        first = self.period(box, self.zone_a, at(0), at(7))
        second = self.period(box, self.zone_b, at(7))
        result = self.single(box.global_code)

        self.assertEqual(result["persisted_movements"], [])
        self.assertEqual(result["inconsistencies"], [])
        self.assertEqual(
            [(b["kind"], b["period_id"], b["coinciding_movement_ids"]) for b in result["period_boundaries"]],
            [("START", first.pk, []), ("END", first.pk, []), ("START", second.pk, [])],
        )
        self.assertTrue(any("cannot be determined from these rows" in fact for fact in result["undetermined"]))
        output = self.run_command(box.global_code)
        self.assertIn("none persisted (this does not prove that no physical move happened)", output)
        self.assertIn("no coinciding persisted BoxMovement", output)

    def test_service_movement_and_period_boundary_are_reported_separately(self):
        box = self.box("ADG-LAB-1.010", zone=self.zone_a)
        initial = self.period(box, self.zone_a, at(0))
        movement = move_box_to_thermal_zone(
            box=box, thermal_zone=self.zone_b, moved_at=at(7), user=self.user, notes="Moved for review",
        )
        audit = AuditLog.objects.get(metadata__movement_id=movement.pk)
        destination = BoxLocation.objects.get(box=box, thermal_zone=self.zone_b)
        result = self.single(box.global_code)

        self.assertEqual(len(result["persisted_movements"]), 1)
        persisted = result["persisted_movements"][0]
        self.assertEqual(persisted["id"], movement.pk)
        self.assertEqual(persisted["from_thermal_zone"]["id"], self.zone_a.pk)
        self.assertEqual(persisted["to_thermal_zone"]["id"], self.zone_b.pk)
        self.assertEqual(persisted["recorded_actor"], {"id": self.user.pk, "label": "Ada LAB"})
        self.assertEqual(persisted["coinciding_period_start_ids"], [destination.pk])
        self.assertEqual(persisted["coinciding_period_end_ids"], [initial.pk])
        self.assertEqual(persisted["linked_audit_ids"], [audit.pk])
        self.assertEqual(
            [(b["kind"], b["period_id"], b["coinciding_movement_ids"]) for b in result["period_boundaries"]],
            [("START", initial.pk, []), ("END", initial.pk, [movement.pk]), ("START", destination.pk, [movement.pk])],
        )
        self.assertEqual(len(result["location_periods"]), 2)
        self.assertEqual(result["inconsistencies"], [])
        self.assertEqual(result["observations"], [])
        evidence = result["audit_evidence"][0]
        self.assertEqual(evidence["movement_id"], movement.pk)
        self.assertTrue(evidence["movement_is_persisted_for_box"])

        output = self.run_command(box.global_code)
        self.assertEqual(output.count(f"  BoxMovement #{movement.pk}\n"), 1)
        self.assertIn(f"coincides with persisted BoxMovement #{movement.pk}", output)

    def test_movement_without_period_and_unknown_audit_references_are_detected(self):
        box = self.box("ADG-LAB-1.011", zone=self.zone_b)
        movement = BoxMovement.objects.create(
            box=box, from_thermal_zone=self.zone_a, to_thermal_zone=self.zone_b, moved_at=at(3),
        )
        AuditLog.objects.create(
            organization=self.organization, action=AuditLog.Action.UPDATE, object_type="box",
            object_id=box.global_code, metadata={"movement_id": 999999, "closed_location_ids": [888888]},
        )
        result = self.single(box.global_code)

        codes = self.codes(result["inconsistencies"])
        for code in (
            "AUDITED_MOVEMENT_NOT_FOUND", "AUDITED_LOCATION_NOT_FOUND", "CURRENT_POINTER_MISMATCH",
        ):
            self.assertIn(code, codes)
        for code in (
            "MOVEMENT_WITHOUT_LINKED_AUDIT", "MOVEMENT_WITHOUT_MATCHING_PERIOD_START",
            "MOVEMENT_WITHOUT_MATCHING_PERIOD_END",
        ):
            self.assertIn(code, self.codes(result["observations"]))
        self.assertTrue(any(f"BoxMovement #{movement.pk} records no actor" in f for f in result["undetermined"]))

    def audit(self, box, *, metadata, description="Local diagnostic evidence", user=None, action=AuditLog.Action.UPDATE):
        return AuditLog.objects.create(
            organization=self.organization, action=action, object_type="box",
            object_id=box.global_code, metadata=metadata, description=description, user=user,
        )

    def test_actor_membership_evidence_includes_inactive_ended_relationship(self):
        box = self.box("ACTOR-LOCAL", zone=self.zone_a)
        self.period(box, self.zone_a, at(0))
        BoxMovement.objects.create(box=box, to_thermal_zone=self.zone_a, moved_at=at(0), user=self.user)
        self.audit(box, metadata={"to_thermal_zone_id": self.zone_a.pk}, user=self.user)
        for active in (True, False):
            with self.subTest(active=active):
                self.membership.is_active = active
                self.membership.ends_on = None if active else date(2025, 12, 31)
                self.membership.save(update_fields=["is_active", "ends_on"])
                self.user.is_active = active
                self.user.save(update_fields=["is_active"])
                result = self.single(box.global_code)
                expected = {"id": self.user.pk, "label": "Ada LAB"}
                self.assertEqual(result["persisted_movements"][0]["recorded_actor"], expected)
                self.assertEqual(result["audit_evidence"][0]["recorded_actor"], expected)
                for fmt in ("text", "json"):
                    self.assertIn("Ada LAB", self.run_command(box.global_code, fmt=fmt))

    def test_foreign_and_unresolvable_actor_references_have_identical_masking(self):
        box = self.box("ACTOR-FOREIGN", zone=self.zone_a)
        foreign = get_user_model().objects.create_user(
            username="foreign-technical-name", first_name="ForeignPrivateName", email="private@foreign.test",
        )
        OrganizationMembership.objects.create(user=foreign, organization=self.other_organization)
        movement = BoxMovement.objects.create(box=box, to_thermal_zone=self.zone_a, moved_at=at(0), user=foreign)
        audit = self.audit(box, metadata={"movement_id": movement.pk}, user=foreign)
        masked = {"reference_status": "UNRESOLVED_SCOPE"}
        result = self.single(box.global_code)
        self.assertEqual(result["persisted_movements"][0]["recorded_actor"], masked)
        self.assertEqual(result["audit_evidence"][0]["recorded_actor"], masked)
        self.assertEqual(diagnostics._actor(999999, {}), masked)
        for fmt in ("text", "json"):
            output = self.run_command(box.global_code, fmt=fmt)
            for secret in (foreign.first_name, foreign.email, foreign.username):
                self.assertNotIn(secret, output)
        foreign.delete()  # Both real FK contracts use SET_NULL on account deletion.
        result = self.single(box.global_code)
        self.assertIsNone(result["persisted_movements"][0]["recorded_actor"])
        self.assertIsNone(result["audit_evidence"][0]["recorded_actor"])
        movement.refresh_from_db()
        audit.refresh_from_db()
        self.assertIsNone(movement.user_id)
        self.assertIsNone(audit.user_id)
        for fmt in ("text", "json"):
            output = self.run_command(box.global_code, fmt=fmt)
            self.assertNotIn("ForeignPrivateName", output)
            self.assertNotIn("private@foreign.test", output)
        self.assertIn("not recorded", self.run_command(box.global_code))

    def test_audit_zone_references_fail_closed_without_coercion(self):
        box = self.box("AUDIT-ZONES")
        cases = (
            (self.zone_a.pk, "IN_ORGANIZATION", False),
            (self.foreign_zone.pk, "OUTSIDE_ORGANIZATION", True),
            (None, None, False),
            (str(self.foreign_zone.pk), "MALFORMED", True),
            (True, "MALFORMED", True),
            ([], "MALFORMED", True),
            ({"foreign": "PrivateMetadataValue"}, "MALFORMED", True),
            (999999, "MISSING", True),
            (9223372036854775808, "MALFORMED", True),
        )
        for placement in ("from_thermal_zone_id", "to_thermal_zone_id", "before", "after"):
            for value, status, withheld in cases:
                with self.subTest(placement=placement, value=value):
                    metadata = {placement: {"thermal_zone_id": value} if placement in ("before", "after") else value}
                    audit = self.audit(box, metadata=metadata, description="PrivateForeignLocationDescription")
                    result = self.single(box.global_code)
                    evidence = next(row for row in result["audit_evidence"] if row["id"] == audit.pk)
                    key = f"{placement}_thermal_zone" if placement in ("before", "after") else placement.removesuffix("_id")
                    ref = evidence[key]
                    self.assertEqual(None if ref is None else ref["reference_status"], status)
                    self.assertEqual(evidence["description_withheld"], withheld)
                    self.assertEqual(evidence["description"], None if withheld else "PrivateForeignLocationDescription")
                    for fmt in ("text", "json"):
                        output = self.run_command(box.global_code, fmt=fmt)
                        self.assertEqual("PrivateForeignLocationDescription" in output, not withheld)
                        self.assertNotIn("PrivateMetadataValue", output)
                        if status == "MALFORMED":
                            self.assertIn("MALFORMED", output)
                    audit.delete()
        audit = self.audit(box, metadata={"before": ["PrivateMalformedSide"]}, action=AuditLog.Action.CREATION)
        result = self.single(box.global_code)["audit_evidence"][0]
        self.assertTrue(result["description_withheld"])
        self.assertEqual(result["malformed_metadata_fields"], ["before"])
        for fmt in ("text", "json"):
            self.assertNotIn("PrivateMalformedSide", self.run_command(box.global_code, fmt=fmt))

    def test_malformed_metadata_identifiers_do_not_abort_or_invent_references(self):
        box = self.box("AUDIT-MALFORMED", zone=self.zone_a)
        location = self.period(box, self.zone_a, at(0))
        movement = BoxMovement.objects.create(box=box, to_thermal_zone=self.zone_a, moved_at=at(0))
        examples = (
            {"movement_id": []}, {"closed_location_ids": [{}]}, {"child_box_ids": [{}]},
            {"movement_id": True}, {"box_id": True, "movement_id": movement.pk},
            {"movement_id": {}}, {"movement_id": "1"},
            {"closed_location_ids": [location.pk, {}, [], True, "1", None]},
            {"legacy_closed_location_ids": [location.pk, {}]},
            {"closed_location_ids": {}}, {"box_id": [], "movement_id": movement.pk},
            {"movement_id": movement.pk, "transition": {"secret": "PrivateMalformedScalar"}},
            [], True, "PrivateMalformedMetadata",
        )
        for metadata in examples:
            with self.subTest(metadata=metadata):
                audit = self.audit(box, metadata=metadata, action=AuditLog.Action.CREATION)
                result = self.single(box.global_code)
                self.assertEqual(len(result["location_periods"]), 1)
                self.assertEqual(len(result["persisted_movements"]), 1)
                evidence = result["audit_evidence"][0]
                self.assertTrue(evidence["malformed_metadata_fields"])
                self.assertTrue(evidence["description_withheld"])
                self.assertIn("MALFORMED_AUDIT_METADATA", self.codes(result["observations"]))
                if not isinstance(metadata, dict) or not diagnostics._is_identifier(metadata.get("movement_id")):
                    self.assertNotIn("movement_id", evidence)
                    self.assertEqual(result["persisted_movements"][0]["linked_audit_ids"], [])
                if "closed_location_ids" in evidence:
                    self.assertTrue(all(type(value) is int for value in evidence["closed_location_ids"]))
                for fmt in ("text", "json"):
                    output = self.run_command(box.global_code, fmt=fmt)
                    self.assertIn("MALFORMED_AUDIT_METADATA", output)
                    self.assertNotIn("PrivateMalformed", output)
                audit.delete()

    def test_unrelated_malformed_audits_do_not_break_another_box_report(self):
        box = self.box("AUDIT-UNRELATED", zone=self.zone_a)
        self.period(box, self.zone_a, at(0))
        unrelated = self.box("UNRELATED-PARENT")
        for metadata in ({"movement_id": []}, {"closed_location_ids": [{}]}, {"child_box_ids": [{}]}):
            self.audit(unrelated, metadata=metadata, action=AuditLog.Action.SUBCULTURE)
        result = self.single(box.global_code)
        self.assertEqual(result["audit_evidence"], [])
        self.assertEqual(result["observations"], [])
        for fmt in ("text", "json"):
            self.run_command(box.global_code, fmt=fmt)

    def test_subculture_parent_scope_and_individual_child_ids_are_validated(self):
        child = self.box("SUBCULTURE-CHILD")
        local_parent = self.box("LOCAL-PARENT")
        foreign_parent = self.box("PRIVATE-FOREIGN-PARENT", organization=self.other_organization)
        for parent, validated in ((local_parent, True), (foreign_parent, False), (None, False)):
            for malformed in (False, True):
                with self.subTest(parent=parent, malformed=malformed):
                    description = f"PrivateParentDescription: {parent.global_code if parent else 'PRIVATE-MISSING-PARENT'}"
                    child_ids = [child.pk] + ([{}, [], True, "1"] if malformed else [])
                    audit = self.audit(
                        parent or local_parent,
                        metadata={"child_box_ids": child_ids, "occurred_at": at(0).isoformat()},
                        description=description, action=AuditLog.Action.SUBCULTURE,
                    )
                    if parent is None:
                        audit.object_id = "PRIVATE-MISSING-PARENT"
                        audit.save(update_fields=["object_id"])
                    evidence = self.single(child.global_code)["audit_evidence"][0]
                    self.assertEqual(evidence["parent_scope_validated"], validated)
                    self.assertEqual(evidence["description_withheld"], not validated or malformed)
                    self.assertEqual(evidence["description"], description if validated and not malformed else None)
                    if not validated:
                        self.assertNotIn("occurred_at", evidence)
                        self.assertIn("SUBCULTURE_PARENT_SCOPE_UNRESOLVED", self.codes(self.single(child.global_code)["observations"]))
                    for fmt in ("text", "json"):
                        output = self.run_command(child.global_code, fmt=fmt)
                        self.assertEqual("PrivateParentDescription" in output, validated and not malformed)
                        self.assertNotIn("PRIVATE-FOREIGN-PARENT", output)
                        self.assertNotIn("PRIVATE-MISSING-PARENT", output)
                    audit.delete()
        self.audit(local_parent, metadata={"child_box_ids": [999999, {}]}, action=AuditLog.Action.SUBCULTURE)
        self.assertEqual(self.single(child.global_code)["audit_evidence"], [])
        for fmt in ("text", "json"):
            self.assertNotIn("Local diagnostic evidence", self.run_command(child.global_code, fmt=fmt))

    def test_unvalidated_subculture_parent_remains_candidate_not_linked_movement_evidence(self):
        child = self.box("CANDIDATE-CHILD")
        foreign = self.box("PRIVATE-CANDIDATE-PARENT", organization=self.other_organization)
        movement = BoxMovement.objects.create(box=child, to_thermal_zone=self.zone_a, moved_at=at(0))
        self.audit(
            foreign, action=AuditLog.Action.SUBCULTURE,
            metadata={"child_box_ids": [child.pk], "movement_id": movement.pk},
            description="PrivateCandidateParentDescription",
        )
        result = self.single(child.global_code)
        self.assertEqual(result["persisted_movements"][0]["linked_audit_ids"], [])
        self.assertIn("MOVEMENT_WITHOUT_LINKED_AUDIT", self.codes(result["observations"]))
        evidence = result["audit_evidence"][0]
        self.assertFalse(evidence["parent_scope_validated"])
        self.assertTrue(evidence["description_withheld"])
        self.assertNotIn("movement_id", evidence)
        for fmt in ("text", "json"):
            output = self.run_command(child.global_code, fmt=fmt)
            self.assertNotIn("PrivateCandidateParentDescription", output)
            self.assertNotIn("PRIVATE-CANDIDATE-PARENT", output)
            self.assertIn("SUBCULTURE_PARENT_SCOPE_UNRESOLVED", output)

    def test_linked_rows_cannot_bypass_foreign_zone_audit_description_masking(self):
        box = self.box("LINKED-FOREIGN-ZONE")
        location = self.period(box, self.foreign_zone, at(0), at(1))
        movement = BoxMovement.objects.create(box=box, to_thermal_zone=self.foreign_zone, moved_at=at(0))
        for metadata in (
            {"movement_id": movement.pk}, {"closed_location_ids": [location.pk]},
            {"legacy_closed_location_ids": [location.pk]},
        ):
            with self.subTest(metadata=metadata):
                audit = self.audit(box, metadata=metadata, description="PrivateLinkedForeignZoneDescription")
                evidence = self.single(box.global_code)["audit_evidence"][0]
                self.assertTrue(evidence["description_withheld"])
                self.assertIsNone(evidence["description"])
                for fmt in ("text", "json"):
                    self.assertNotIn("PrivateLinkedForeignZoneDescription", self.run_command(box.global_code, fmt=fmt))
                audit.delete()

    def test_movement_with_no_period_evidence_is_only_an_observation(self):
        box = self.box("MOVEMENT-NO-PERIODS")
        BoxMovement.objects.create(
            box=box, from_thermal_zone=self.zone_a, to_thermal_zone=self.zone_b, moved_at=at(7),
        )
        result = self.single(box.global_code)
        self.assertEqual(result["inconsistencies"], [])
        self.assertEqual(result["location_periods"], [])
        self.assertIn("MOVEMENT_WITHOUT_MATCHING_PERIOD_START", self.codes(result["observations"]))
        self.assertIn("MOVEMENT_WITHOUT_MATCHING_PERIOD_END", self.codes(result["observations"]))
        text = self.run_command(box.global_code)
        errors, facts = text.split("DETECTED INCONSISTENCIES\n")[1].split("FACTUAL OBSERVATIONS (not errors)\n")
        self.assertNotIn("MOVEMENT_WITHOUT_MATCHING_PERIOD", errors)
        self.assertIn("MOVEMENT_WITHOUT_MATCHING_PERIOD", facts)
        payload = json.loads(self.run_command(box.global_code, fmt="json"))["boxes"][0]
        self.assertEqual(payload["inconsistencies"], [])
        self.assertEqual(payload["observations"], result["observations"])

    def test_exact_timestamp_correspondence_is_a_fact_and_mismatches_are_observations(self):
        for index, offset in enumerate((timedelta(0), timedelta(microseconds=1), timedelta(days=2))):
            with self.subTest(offset=offset):
                box = self.box(f"TIMESTAMP-{index}", zone=self.zone_b)
                origin = self.period(box, self.zone_a, at(0), at(7))
                destination = self.period(box, self.zone_b, at(7))
                movement = BoxMovement.objects.create(
                    box=box, from_thermal_zone=self.zone_a, to_thermal_zone=self.zone_b, moved_at=at(7) + offset,
                )
                result = self.single(box.global_code)
                self.assertEqual(result["inconsistencies"], [])
                persisted = result["persisted_movements"][0]
                self.assertEqual(persisted["coinciding_period_start_ids"], [] if offset else [destination.pk])
                self.assertEqual(persisted["coinciding_period_end_ids"], [] if offset else [origin.pk])
                mismatch_codes = {"MOVEMENT_WITHOUT_MATCHING_PERIOD_START", "MOVEMENT_WITHOUT_MATCHING_PERIOD_END"}
                self.assertEqual(mismatch_codes.intersection(self.codes(result["observations"])), mismatch_codes if offset else set())
                text = self.run_command(box.global_code)
                errors, facts = text.split("DETECTED INCONSISTENCIES\n")[1].split("FACTUAL OBSERVATIONS (not errors)\n")
                for code in mismatch_codes:
                    self.assertNotIn(code, errors)
                    self.assertEqual(code in facts, bool(offset))
                payload = json.loads(self.run_command(box.global_code, fmt="json"))["boxes"][0]
                self.assertEqual(payload["observations"], result["observations"])
                self.assertEqual(payload["inconsistencies"], [])
                if not offset:
                    self.assertIn(f"coincides with persisted BoxMovement #{movement.pk}", text)

    def test_unknown_end_overlap_is_undetermined_not_an_error(self):
        box = self.box("ADG-LAB-1.012", status=Box.Status.INACTIVE)
        unknown = self.period(box, self.zone_a, at(0), unknown=True)
        later = self.period(box, self.zone_b, at(7), at(14))
        result = self.single(box.global_code)

        self.assertEqual(result["inconsistencies"], [])
        self.assertEqual(result["location_periods"][0]["state"], "CLOSED_END_UNKNOWN")
        self.assertTrue(any(
            f"#{unknown.pk} has an unknown end" in fact and f"#{later.pk}" in fact for fact in result["undetermined"]
        ))

    def test_identifiers_are_exact_and_unknown_codes_are_reported(self):
        box = self.box("ADG-LAB-1.013", zone=self.zone_a)
        self.period(box, self.zone_a, at(0))
        report = self.report("ADG-LAB-1", "adg-lab-1.013", box.global_code)
        self.assertEqual([item["current_state"]["id"] for item in report["boxes"]], [box.pk])
        self.assertEqual(report["unresolved_global_codes"], ["ADG-LAB-1", "adg-lab-1.013"])

    def test_box_of_another_organization_cannot_be_inspected(self):
        foreign = self.box("ADG-LAB-1.014", zone=self.foreign_zone, organization=self.other_organization)
        self.period(foreign, self.foreign_zone, at(0), notes="Foreign operational note")

        report = self.report(foreign.global_code)
        self.assertEqual(report["boxes"], [])
        self.assertEqual(report["unresolved_global_codes"], [foreign.global_code])
        out = StringIO()
        with self.assertRaises(CommandError):
            call_command(
                COMMAND, "--organization-id", str(self.organization.pk),
                "--global-code", foreign.global_code, stdout=out,
            )
        output = out.getvalue()
        self.assertIn("no Box with this exact global_code in this organization", output)
        self.assertNotIn("Foreign secret zone", output)
        self.assertNotIn("Foreign operational note", output)
        self.assertNotIn(f"BOX {foreign.global_code}", output)

    def test_corrupt_cross_organization_relations_do_not_leak_foreign_data(self):
        box = self.box("ADG-LAB-1.015", zone=self.foreign_zone)
        location = BoxLocation.objects.create(
                    box=box, thermal_zone=self.foreign_zone, starts_at=at(0), notes="PrivateForeignPeriodNote",
                )
        movement = BoxMovement.objects.create(
            box=box, from_thermal_zone=self.zone_a, to_thermal_zone=self.foreign_zone, moved_at=at(0),
                        notes="PrivateForeignMovementNote",
        )
        AuditLog.objects.create(
            organization=self.organization, action=AuditLog.Action.UPDATE, object_type="box",
            object_id=box.global_code, description="Box moved to Foreign secret zone",
            metadata={"movement_id": movement.pk, "to_thermal_zone_id": self.foreign_zone.pk},
        )
        result = self.single(box.global_code)

        cross = [item for item in result["inconsistencies"] if item["code"] == "CROSS_ORGANIZATION_RELATION"]
        self.assertEqual(
            sorted((item["relation"], item["row_id"]) for item in cross),
            sorted([
                ("Box.thermal_zone", box.pk), ("BoxLocation", location.pk),
                ("BoxMovement.to_thermal_zone", movement.pk),
            ]),
        )
        self.assertEqual(result["location_periods"][0]["thermal_zone"], {"reference_status": "OUTSIDE_ORGANIZATION"})
        self.assertTrue(result["audit_evidence"][0]["description_withheld"])
        for fmt in ("text", "json"):
            output = self.run_command(box.global_code, fmt=fmt)
            self.assertNotIn("Foreign secret zone", output)
            self.assertNotIn("Foreign laboratory", output)
            self.assertNotIn("21.0", output)
            self.assertNotIn("PrivateForeignPeriodNote", output)
            self.assertNotIn("PrivateForeignMovementNote", output)
        payload = json.loads(self.run_command(box.global_code, fmt="json"))
        self.assertNotIn(self.foreign_zone.pk, _all_zone_ids(payload))

    def test_diagnostic_is_read_only_and_preserves_zero_measurements(self):
        box = self.box("ADG-LAB-1.016", zone=self.zone_a)
        self.period(box, self.zone_a, at(0), at(7))
        move_box_to_thermal_zone(box=box, thermal_zone=self.zone_b, moved_at=at(14), user=self.user, notes="")
        BoxLocation.objects.create(box=box, thermal_zone=self.zone_a, starts_at=at(10))
        BiologicalMeasurement.objects.create(
            box=box, measured_on=date(2026, 1, 12), polyp_count=0, ephyrae_count=0,
            strobila_count=0, salinity_psu=Decimal("0"),
        )
        before = self.snapshot()

        with CaptureQueriesContext(connection) as queries:
            text = self.run_command(box.global_code)
            payload = json.loads(self.run_command(box.global_code, fmt="json"))
            self.report(box.global_code)

        self.assertEqual(self.snapshot(), before)
        for query in queries.captured_queries:
            sql = query["sql"].upper()
            self.assertNotRegex(sql, r"\b(INSERT|UPDATE|DELETE|REPLACE|ALTER|DROP|CREATE|TRUNCATE)\b")
            self.assertNotIn("FOR UPDATE", sql)
        measurement = BiologicalMeasurement.objects.get(box=box)
        self.assertEqual(
            (measurement.polyp_count, measurement.ephyrae_count, measurement.strobila_count, measurement.salinity_psu),
            (0, 0, 0, Decimal("0")),
        )
        # A configured zero target is reported as zero, never as absent.
        zone = payload["boxes"][0]["location_periods"][0]["thermal_zone"]
        self.assertEqual(zone["current_target_temperature_c"], "0.0")
        self.assertIn("current target 0.0 C", text)
        self.assertTrue(text.isascii())
        for section in (
            "CURRENT STATE", "LOCATION PERIODS", "PERSISTED MOVEMENTS", "AUDIT EVIDENCE",
            "DETECTED INCONSISTENCIES", "FACTS THAT CANNOT BE DETERMINED",
        ):
            self.assertIn(section, text)

    def test_output_is_deterministic(self):
        first = self.box("ADG-LAB-1.017", zone=self.zone_a)
        second = self.box("ADG-LAB-1.018", zone=self.zone_b)
        self.period(first, self.zone_a, at(0))
        self.period(second, self.zone_b, at(0))
        forward = self.run_command(first.global_code, second.global_code)
        backward = self.run_command(second.global_code, first.global_code, first.global_code)
        self.assertEqual(forward, backward)
        self.assertLess(forward.index("BOX ADG-LAB-1.017"), forward.index("BOX ADG-LAB-1.018"))

    def test_unknown_organization_is_rejected(self):
        with self.assertRaises(CommandError):
            call_command(COMMAND, "--organization-id", "999999", "--global-code", "X", stdout=StringIO())


def _all_zone_ids(value):
    ids = set()
    if isinstance(value, dict):
        if "reference_status" in value and "id" in value:
            ids.add(value["id"])
        for item in value.values():
            ids |= _all_zone_ids(item)
    elif isinstance(value, list):
        for item in value:
            ids |= _all_zone_ids(item)
    return ids
