import io
import json

from copy import deepcopy
from datetime import date, datetime, timedelta
from unittest.mock import patch
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, connection, transaction
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils.dateparse import parse_datetime

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.measurements.models import BiologicalMeasurement
from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain


from .historical_movements import accept_historical_movements, plan_historical_movements
from .models import MOVEMENT_WEEK_TIME_ZONE, Box, BoxLocation, BoxMovement, HistoricalBoxMovement, ThermalZone
from .movement_corrections import correct_box_movement
from .services import deactivate_box, move_box_to_thermal_zone, reactivate_box, qualify_pending_box


def paris(year, month, day, hour=0):
    return datetime(year, month, day, hour, tzinfo=MOVEMENT_WEEK_TIME_ZONE)



class HistoricalMovementsTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Historical lab")
        self.other = Organization.objects.create(name="Other lab")
        self.admin = get_user_model().objects.create_user(username="historical_admin", email="historical-admin@example.org", first_name="Review", last_name="Admin")
        self.viewer = get_user_model().objects.create_user(username="historical_viewer", email="historical-viewer@example.org")
        self.tech = get_user_model().objects.create_user(username="historical_tech", email="historical-tech@example.org")
        for user, role in ((self.admin, "admin"), (self.viewer, "viewer"), (self.tech, "lab_technician")):
            OrganizationMembership.objects.create(user=user, organization=self.org, role=role)
        species = Species.objects.create(scientific_name="Historical synthetic", genus_species_code="HIS")
        strain = Strain.objects.create(species=species, code="HIS-LAB-1", organization=self.org, number=1, origin_code="LAB")
        self.a = ThermalZone.objects.create(organization=self.org, name="A")
        self.b = ThermalZone.objects.create(organization=self.org, name="B")
        self.foreign = ThermalZone.objects.create(organization=self.other, name="Never expose")
        self.box = Box.objects.create(organization=self.org, global_code="HIS-LAB-1.001", box_number="001", strain=strain, thermal_zone=self.b)
        self.origin = BoxLocation.objects.create(box=self.box, thermal_zone=self.a, starts_at=paris(2025, 12, 15), ends_at=paris(2025, 12, 29))
        self.destination = BoxLocation.objects.create(box=self.box, thermal_zone=self.b, starts_at=paris(2025, 12, 29))
        self.before = BiologicalMeasurement.objects.create(box=self.box, measured_on=date(2025, 12, 22), polyp_count=0, ephyrae_count=0, strobila_count=None)
        self.after = BiologicalMeasurement.objects.create(box=self.box, measured_on=date(2025, 12, 29), polyp_count=0, ephyrae_count=0, strobila_count=None)

    def plan(self, **kwargs):
        return plan_historical_movements(organization_id=self.org.pk, box_ids=[self.box.pk], **kwargs)

    def accept(self, plan=None, **kwargs):
        plan = plan or self.plan()
        args = dict(organization_id=self.org.pk, box_ids=[self.box.pk], candidate_ids=[row["id"] for row in plan["candidates"][:1]],
                    expected_fingerprint=plan["fingerprint"], actor=self.admin, notes="Synthetic explicit review")
        args.update(kwargs)
        return accept_historical_movements(**args)

    def stored_reconstruction(self, *, legacy=False):
        """Represent a pre-review record without passing the acceptance gate."""
        plan = self.plan()
        candidate = plan["candidates"][0]
        return HistoricalBoxMovement.objects.create(
            box=self.box, iso_year=candidate["iso_year"], iso_week=candidate["iso_week"],
            from_thermal_zone=self.a, to_thermal_zone=self.b,
            origin_location=self.origin, destination_location=self.destination,
            candidate_key=candidate["id"], plan_fingerprint=plan["fingerprint"],
            evidence={"basis": "legacy_unestablished"} if legacy else candidate["evidence"],
            accepted_by=self.admin, notes="Synthetic pre-review record",
        )

    def snapshot(self):
        return [list(model.objects.order_by("pk").values()) for model in (Box, BoxLocation, BoxMovement, BiologicalMeasurement, AuditLog)]

    def history(self, user=None, **headers):
        self.client.force_login(user or self.viewer)
        return self.client.get(reverse("api_box_history", args=[self.box.pk]), HTTP_X_ORGANIZATION_ID=str(self.org.pk), **headers)

    def test_planner_is_read_only_deterministic_and_preserves_zero(self):
        before = self.snapshot()
        plan = self.plan()
        self.assertEqual(plan, self.plan())
        self.assertEqual(before, self.snapshot())
        candidate = plan["candidates"][0]
        self.assertEqual(candidate["blockers"], [])
        self.assertEqual(candidate["reconciliation_status"], "review_required")
        self.assertEqual(candidate["classification"], "eligible")
        self.assertEqual((candidate["iso_year"], candidate["iso_week"]), (2025, 52))
        self.assertFalse(candidate["evidence"]["physical_occurrence_proven"])
        self.assertEqual(candidate["evidence"]["measurement_ids"], [self.before.pk, self.after.pk])

    def test_accept_is_additive_audited_and_idempotent(self):
        plan = self.plan()
        before = self.snapshot()[:4]
        ids = self.accept(plan)
        self.assertEqual(ids, self.accept(plan))
        self.assertEqual(before, self.snapshot()[:4])
        self.assertEqual(HistoricalBoxMovement.objects.count(), 1)
        audit = AuditLog.objects.get(metadata__event="historical_movements_v1")
        self.assertEqual(audit.organization_id, self.org.pk)
        self.assertEqual(audit.user_id, self.admin.pk)
        self.assertEqual(audit.metadata["plan_fingerprint"], plan["fingerprint"])

    def test_audit_failure_rolls_back(self):
        with patch("apps.cultures.historical_movements.AuditLog.objects.create", side_effect=RuntimeError("audit unavailable")):
            with self.assertRaises(RuntimeError):
                self.accept()
        self.assertFalse(HistoricalBoxMovement.objects.exists())

    def test_batch_acceptance_rolls_back_all_events_when_second_audit_fails(self):
        box = Box.objects.create(organization=self.org, strain=self.box.strain, box_number="002", global_code="HIS-LAB-1.002")
        BoxLocation.objects.create(box=box, thermal_zone=self.a, starts_at=self.origin.starts_at, ends_at=self.origin.ends_at)
        BoxLocation.objects.create(box=box, thermal_zone=self.b, starts_at=self.destination.starts_at)
        for source in (self.before, self.after):
            BiologicalMeasurement.objects.create(box=box, measured_on=source.measured_on, polyp_count=0, ephyrae_count=0)
        selection = [self.box.pk, box.pk]
        plan = plan_historical_movements(organization_id=self.org.pk, box_ids=selection)
        create = AuditLog.objects.create
        count = 0

        def fail_second(**kwargs):
            nonlocal count
            count += 1
            if count == 2:
                raise RuntimeError("Second audit unavailable")
            return create(**kwargs)

        with patch("apps.cultures.historical_movements.AuditLog.objects.create", side_effect=fail_second):
            with self.assertRaises(RuntimeError):
                accept_historical_movements(organization_id=self.org.pk, box_ids=selection,
                                            candidate_ids=[c["id"] for c in plan["candidates"]],
                                            expected_fingerprint=plan["fingerprint"], actor=self.admin, notes="Synthetic batch review")
        self.assertFalse(HistoricalBoxMovement.objects.exists())
        self.assertFalse(AuditLog.objects.filter(metadata__event="historical_movements_v1").exists())

    def test_stale_measurement_fingerprint(self):
        plan = self.plan()
        self.after.polyp_count = 1
        self.after.save()
        with self.assertRaises(ValidationError):
            self.accept(plan)
        self.assertFalse(HistoricalBoxMovement.objects.exists())

    def test_stale_period_fingerprint_without_box_change(self):
        plan = self.plan()
        BoxLocation.objects.filter(pk=self.origin.pk).update(notes="Independent evidence change")
        with self.assertRaises(ValidationError):
            self.accept(plan)

    def test_stale_movement_fingerprint(self):
        plan = self.plan()
        BoxMovement.objects.create(box=self.box, to_thermal_zone=self.a, moved_at=paris(2025, 11, 1))
        with self.assertRaises(ValidationError):
            self.accept(plan)

    def test_permissions_reloaded_under_lock(self):
        for actor in (self.viewer, self.tech):
            with self.assertRaises(PermissionDenied):
                self.accept(actor=actor)
        OrganizationMembership.objects.filter(user=self.admin).update(is_active=False)
        with self.assertRaises(PermissionDenied):
            self.accept()

    def test_inactive_account_and_organization_refused(self):
        get_user_model().objects.filter(pk=self.admin.pk).update(is_active=False)
        with self.assertRaises(PermissionDenied):
            self.accept()
        get_user_model().objects.filter(pk=self.admin.pk).update(is_active=True)
        Organization.objects.filter(pk=self.org.pk).update(is_active=False)
        with self.assertRaises(PermissionDenied):
            self.accept()

    def test_scope_requires_all_explicit_boxes(self):
        for selection in ([], [self.box.pk, 999999]):
            with self.assertRaises(ValidationError):
                plan_historical_movements(organization_id=self.org.pk, box_ids=selection)
        with self.assertRaises(ValidationError):
            plan_historical_movements(organization_id=self.other.pk, box_ids=[self.box.pk])

    def test_missing_measurement_blocks_accept(self):
        self.after.delete()
        plan = self.plan()
        self.assertEqual(plan["candidates"], [])
        assessment = next(row for row in plan["assessments"] if row["classification"] == "insufficient_evidence")
        self.assertIn("adjacent_measurement_evidence_missing", assessment["blockers"])
        with self.assertRaises(ValidationError):
            self.accept(plan=plan, candidate_ids=[assessment["id"]])

    def test_foreign_environment_measurement_is_not_evidence(self):
        other_box = Box.objects.create(organization=self.org, strain=self.box.strain, box_number="002", global_code="HIS-LAB-1.002")
        BiologicalMeasurement.objects.filter(pk=self.after.pk).update(environment_source_box=other_box)
        self.assertIn("measurement_environment_attribution_unresolved", self.plan()["candidates"][0]["blockers"])

    def test_unknown_end_and_gap_block(self):
        self.origin.ends_at = None
        self.origin.end_date_unknown = True
        self.origin.save()
        self.assertIn("period_continuity_unresolved", self.plan()["candidates"][0]["blockers"])
        self.origin.ends_at = paris(2025, 12, 28)
        self.origin.end_date_unknown = False
        self.origin.save()
        self.assertIn("period_continuity_unresolved", self.plan()["candidates"][0]["blockers"])

    def test_non_week_boundary_blocks(self):
        at = paris(2025, 12, 29, 10)
        BoxLocation.objects.filter(pk=self.origin.pk).update(ends_at=at)
        BoxLocation.objects.filter(pk=self.destination.pk).update(starts_at=at)
        self.assertIn("whole_week_period_evidence_required", self.plan()["candidates"][0]["blockers"])

    def test_recorded_unclassified_is_not_reclassified_or_duplicated(self):
        movement = BoxMovement.objects.create(box=self.box, from_thermal_zone=self.a, to_thermal_zone=self.b, moved_at=paris(2025, 12, 29))
        candidate = self.plan()["candidates"][0]
        self.assertEqual(candidate["reconciliation_status"], "already_represented")
        self.assertEqual(candidate["blockers"], [])
        self.assertEqual(candidate["evidence"]["represented_movement_ids"], [movement.pk])
        with self.assertRaises(ValidationError):
            self.accept()
        movement.refresh_from_db()
        self.assertEqual(movement.kind, "unclassified")

    def test_foreign_period_blocks_and_does_not_expose_zone_name(self):
        BoxLocation.objects.filter(pk=self.origin.pk).update(thermal_zone=self.foreign)
        candidate = self.plan()["candidates"][0]
        self.assertIn("invalid_or_foreign_period", candidate["blockers"])
        self.assertIsNone(candidate["from_thermal_zone_id"])
        self.assertNotIn("Never expose", self.history().content.decode())

    def test_exact_selection_and_note_required(self):
        for kwargs in ({"candidate_ids": []}, {"candidate_ids": ["unknown"]}, {"notes": " "}, {"expected_fingerprint": None}):
            with self.assertRaises(ValidationError):
                self.accept(**kwargs)

    @override_settings(SETTINGS_MODULE="config.settings")
    def test_real_environment_refused_before_query(self):
        with self.assertNumQueries(0):
            with self.assertRaises(PermissionDenied):
                self.accept(plan={"fingerprint": "fake", "candidates": [{"id": "fake"}]})
            with self.assertRaises(CommandError):
                call_command("reconcile_historical_movements", organization_id=self.org.pk, box_id=[self.box.pk], actor_id=self.admin.pk)

    def test_non_memory_and_postgresql_acceptance_refused_before_query(self):
        for vendor in ("sqlite", "postgresql"):
            guarded_connection = SimpleNamespace(vendor=vendor, is_in_memory_db=lambda: False)
            with self.subTest(vendor=vendor), self.assertNumQueries(0):
                with patch("apps.cultures.historical_movements.connection", guarded_connection):
                    with self.assertRaises(PermissionDenied):
                        self.accept(plan={"fingerprint": "fake", "candidates": [{"id": "fake"}]})
                with patch("apps.cultures.management.commands.reconcile_historical_movements.connection", guarded_connection):
                    with self.assertRaises(CommandError):
                        call_command("reconcile_historical_movements", organization_id=self.org.pk,
                                     box_id=[self.box.pk], actor_id=self.admin.pk)

    def test_command_real_preview_accept_and_replay(self):
        output = io.StringIO()
        call_command("reconcile_historical_movements", organization_id=self.org.pk, box_id=[self.box.pk], actor_id=self.admin.pk, stdout=output)
        plan = json.loads(output.getvalue())
        self.assertFalse(HistoricalBoxMovement.objects.exists())
        self.assertEqual(plan["candidates"][0]["reconciliation_status"], "review_required")
        before = self.snapshot()[:4]
        for _ in range(2):
            call_command("reconcile_historical_movements", organization_id=self.org.pk, box_id=[self.box.pk], actor_id=self.admin.pk,
                         accept=True, candidate_id=[plan["candidates"][0]["id"]], expected_fingerprint=plan["fingerprint"], notes="Reviewed synthetic", stdout=io.StringIO())
        self.assertEqual(HistoricalBoxMovement.objects.count(), 1)
        self.assertEqual(AuditLog.objects.filter(metadata__event="historical_movements_v1").count(), 1)
        self.assertEqual(before, self.snapshot()[:4])

    def test_get_is_read_only_with_full_contract(self):
        before = self.snapshot()
        response = self.history()
        self.assertEqual(response.status_code, 200, response.content)
        expected = {"id", "kind", "temporal_precision", "occurred_at", "iso_year", "iso_week", "from_thermal_zone", "to_thermal_zone", "actor", "provenance", "evidence", "reconciliation_status", "recorded_movement_id", "can_correct", "notes"}
        for event in response.json()["events"]:
            self.assertEqual(set(event), expected)
            self.assertFalse(event["can_correct"])
            self.assertIsNone(event["occurred_at"])
            self.assertEqual(event["temporal_precision"], "unresolved")
        self.assertEqual(before, self.snapshot())

    def test_get_authentication_explicit_context_and_cross_scope(self):
        url = reverse("api_box_history", args=[self.box.pk])
        self.assertIn(self.client.get(url).status_code, (401, 403))
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.get(url, HTTP_X_ORGANIZATION_ID=str(self.other.pk)).status_code, 403)
        OrganizationMembership.objects.create(user=self.viewer, organization=self.other, role="viewer")
        self.assertEqual(self.client.get(url, HTTP_X_ORGANIZATION_ID=str(self.other.pk)).status_code, 404)
        self.assertEqual(self.client.post(url, HTTP_X_ORGANIZATION_ID=str(self.org.pk)).status_code, 405)

    def test_unverified_stored_projection_does_not_certify_a_week(self):
        self.stored_reconstruction(legacy=True)
        event = next(e for e in self.history().json()["events"] if e["kind"] == "historical_reconstruction")
        self.assertEqual(event["temporal_precision"], "unresolved")
        self.assertEqual(event["reconciliation_status"], "unresolved")
        self.assertIsNone(event["occurred_at"])
        self.assertIsNone(event["actor"])
        self.assertEqual((event["iso_year"], event["iso_week"]), (None, None))
        self.assertEqual((event["evidence"]["stored_iso_year"], event["evidence"]["stored_iso_week"]), (2025, 52))
        self.assertEqual(self.plan()["candidates"][0]["reconciliation_status"], "blocked")
        self.assertFalse(event["can_correct"])
        self.assertFalse(event["evidence"]["period_pairing_reliable"])
        self.assertEqual(event["evidence"]["accepted_by"]["first_name"], "Review")

    def test_real_acceptance_projects_approved_week_only_evidence(self):
        plan = self.plan()
        self.accept(plan)
        persisted = HistoricalBoxMovement.objects.get()
        self.assertEqual((persisted.iso_year, persisted.iso_week), (2025, 52))
        self.assertEqual(self.plan()["candidates"][0]["reconciliation_status"], "accepted")
        event = next(row for row in self.history().json()["events"] if row["kind"] == "historical_reconstruction")
        self.assertEqual(event["temporal_precision"], "iso_week")
        self.assertEqual(event["reconciliation_status"], "accepted")
        self.assertEqual((event["iso_year"], event["iso_week"]), (2025, 52))
        self.assertEqual(event["provenance"], "persisted_location_periods_and_canonical_measurement_weeks")
        self.assertEqual(event["evidence"]["source_location_provenance"], "established_by_period_identity")
        self.assertEqual(event["evidence"]["attributed_week_start"], "2025-12-22")
        self.assertEqual(event["evidence"]["destination_week_start"], "2025-12-29")
        self.assertIsNone(event["occurred_at"])
        self.assertIsNone(event["actor"])
        self.assertFalse(event["evidence"]["exact_time_proven"])
        self.assertFalse(event["evidence"]["period_pairing_reliable"])
        self.assertFalse(event["can_correct"])

    def test_initial_assignment_and_missing_history_are_assessments_not_movements(self):
        plan = self.plan()
        initial = next(row for row in plan["assessments"] if row["classification"] == "initial_assignment")
        self.assertEqual(initial["evidence"]["location_id"], self.origin.pk)
        self.assertNotIn(initial["id"], [row["id"] for row in plan["candidates"]])
        BoxLocation.objects.filter(box=self.box).delete()
        plan = self.plan()
        self.assertEqual(plan["candidates"], [])
        self.assertEqual(plan["assessments"][0]["classification"], "insufficient_evidence")
        self.assertEqual(plan["assessments"][0]["blockers"], ["location_history_missing"])

    def test_full_destination_week_may_end_at_following_monday(self):
        BoxLocation.objects.filter(pk=self.destination.pk).update(ends_at=paris(2026, 1, 5))
        self.assertEqual(self.plan()["candidates"][0]["classification"], "eligible")
        self.accept()
        self.assertEqual(HistoricalBoxMovement.objects.count(), 1)

    def test_partial_origin_or_destination_week_is_blocked(self):
        BoxLocation.objects.filter(pk=self.origin.pk).update(starts_at=paris(2025, 12, 23))
        self.assertIn("whole_week_period_evidence_required", self.plan()["candidates"][0]["blockers"])
        with self.assertRaises(ValidationError):
            self.accept()
        BoxLocation.objects.filter(pk=self.origin.pk).update(starts_at=paris(2025, 12, 15))
        BoxLocation.objects.filter(pk=self.destination.pk).update(ends_at=paris(2026, 1, 4))
        self.assertIn("destination_period_does_not_cover_week", self.plan()["candidates"][0]["blockers"])
        with self.assertRaises(ValidationError):
            self.accept()

    def test_missing_origin_measurement_does_not_interpolate_from_an_earlier_week(self):
        self.before.delete()
        BiologicalMeasurement.objects.create(box=self.box, measured_on=date(2025, 12, 15), polyp_count=0)
        plan = self.plan()
        self.assertEqual(plan["candidates"], [])
        self.assertTrue(any(row["classification"] == "insufficient_evidence" for row in plan["assessments"]))

    def test_duplicate_evidence_is_not_a_second_canonical_observation(self):
        BiologicalMeasurement.objects.create(box=self.box, measured_on=self.before.measured_on,
                                             duplicate_of=self.before, polyp_count=0)
        self.assertEqual(self.plan()["candidates"][0]["evidence"]["measurement_ids"], [self.before.pk, self.after.pk])
        self.accept()
        self.assertEqual(HistoricalBoxMovement.objects.count(), 1)

    def test_linked_boundary_movement_is_already_represented_without_changing_exact_week(self):
        movement = BoxMovement.objects.create(
            box=self.box, kind="ordinary", from_thermal_zone=self.a, to_thermal_zone=self.b,
            moved_at=self.destination.starts_at, origin_location=self.origin, destination_location=self.destination,
        )
        before = self.snapshot()
        candidate = self.plan()["candidates"][0]
        self.assertEqual(candidate["classification"], "already_represented")
        self.assertEqual(candidate["reconciliation_status"], "already_represented")
        self.assertEqual(candidate["blockers"], [])
        self.assertEqual((candidate["iso_year"], candidate["iso_week"]), (2025, 52))
        movement.refresh_from_db()
        self.assertEqual(movement.iso_week_start.isocalendar()[:2], (2026, 1))
        with self.assertRaisesMessage(ValidationError, "already represented"):
            self.accept()
        self.assertEqual(before, self.snapshot())
        self.assertFalse(HistoricalBoxMovement.objects.exists())

    def test_unlinked_boundary_match_does_not_forge_links(self):
        movement = BoxMovement.objects.create(box=self.box, from_thermal_zone=self.a,
                                             to_thermal_zone=self.b, moved_at=self.destination.starts_at)
        before = self.snapshot()
        self.assertEqual(self.plan()["candidates"][0]["classification"], "already_represented")
        self.history()
        movement.refresh_from_db()
        self.assertIsNone(movement.origin_location_id)
        self.assertIsNone(movement.destination_location_id)
        self.assertEqual(before, self.snapshot())

    def test_conflicting_link_is_not_treated_as_a_corresponding_boundary(self):
        BoxMovement.objects.create(box=self.box, from_thermal_zone=self.a, to_thermal_zone=self.b,
                                   moved_at=paris(2025, 12, 28, 12),
                                   origin_location=self.origin, destination_location=self.destination)
        candidate = self.plan()["candidates"][0]
        self.assertEqual(candidate["classification"], "ambiguous")
        self.assertIn("recorded_period_link_mismatch", candidate["blockers"])
        with self.assertRaises(ValidationError):
            self.accept()

    def test_unrelated_movement_in_origin_week_blocks_reconstruction(self):
        BoxMovement.objects.create(box=self.box, from_thermal_zone=self.b, to_thermal_zone=self.a,
                                   moved_at=paris(2025, 12, 24, 12))
        self.assertIn("recorded_event_in_week", self.plan()["candidates"][0]["blockers"])
        with self.assertRaises(ValidationError):
            self.accept()

    def test_unrelated_movement_in_destination_week_blocks_reconstruction(self):
        BoxMovement.objects.create(box=self.box, from_thermal_zone=self.b, to_thermal_zone=self.a,
                                   moved_at=paris(2025, 12, 31, 12))
        self.assertIn("recorded_event_in_week", self.plan()["candidates"][0]["blockers"])
        with self.assertRaises(ValidationError):
            self.accept()

    def test_multiple_boundary_representations_are_ambiguous(self):
        for _ in range(2):
            BoxMovement.objects.create(box=self.box, from_thermal_zone=self.a,
                                       to_thermal_zone=self.b, moved_at=self.destination.starts_at)
        candidate = self.plan()["candidates"][0]
        self.assertEqual(candidate["classification"], "ambiguous")
        self.assertIn("multiple_recorded_representations", candidate["blockers"])
        with self.assertRaises(ValidationError):
            self.accept()

    def test_prior_boundary_week_receipt_blocks_duplicate_period_reconstruction(self):
        receipt = self.stored_reconstruction(legacy=True)
        HistoricalBoxMovement._base_manager.filter(pk=receipt.pk).update(
            iso_year=2026, iso_week=1, candidate_key="old-boundary-week-key",
        )
        self.assertIn("historical_event_in_week", self.plan()["candidates"][0]["blockers"])
        with self.assertRaises(ValidationError):
            self.accept()
        self.assertEqual(HistoricalBoxMovement.objects.count(), 1)

    def test_missing_acceptance_audit_blocks_replay_and_uncertifies_projection(self):
        self.accept()
        AuditLog.objects.filter(metadata__event="historical_movements_v1").delete()
        candidate = self.plan()["candidates"][0]
        self.assertIn("historical_receipt_audit_unresolved", candidate["blockers"])
        with self.assertRaises(ValidationError):
            self.accept()
        event = next(row for row in self.history().json()["events"] if row["kind"] == "historical_reconstruction")
        self.assertEqual(event["reconciliation_status"], "unresolved")
        self.assertEqual(HistoricalBoxMovement.objects.count(), 1)

    def test_changed_period_evidence_invalidates_replay_and_projection(self):
        plan = self.plan()
        self.accept(plan)
        BoxLocation.objects.filter(pk=self.origin.pk).update(starts_at=paris(2025, 12, 23))
        with self.assertRaisesMessage(ValidationError, "preview has changed"):
            self.accept(plan)
        event = next(row for row in self.history().json()["events"] if row["kind"] == "historical_reconstruction")
        self.assertEqual(event["temporal_precision"], "unresolved")
        self.assertEqual(HistoricalBoxMovement.objects.count(), 1)

    def test_ordinary_projection_preserves_correction_endpoint(self):
        movement = move_box_to_thermal_zone(box=self.box, thermal_zone=self.a, moved_at=paris(2026, 1, 7, 12), user=self.admin, notes="")
        event = next(e for e in self.history(user=self.tech).json()["events"] if e["recorded_movement_id"] == movement.pk)
        self.assertEqual(event["kind"], "ordinary")
        self.assertTrue(event["can_correct"])
        self.assertTrue(event["evidence"]["period_pairing_reliable"])
        self.assertTrue(event["evidence"]["recorded_via_polypbase"])
        self.assertEqual(event["temporal_precision"], "exact")

    def test_recorded_via_polypbase_requires_original_service_audit(self):
        for kind in BoxMovement.Kind.values:
            with self.subTest(kind=kind):
                movement = BoxMovement.objects.create(
                    box=self.box, from_thermal_zone=self.b, to_thermal_zone=self.a,
                    moved_at=paris(2026, 1, 7, 12), user=self.admin, kind=kind,
                )
                event = next(e for e in self.history().json()["events"] if e["recorded_movement_id"] == movement.pk)
                self.assertFalse(event["evidence"]["recorded_via_polypbase"])
                movement.delete()

    def test_legacy_service_audit_proves_unclassified_without_period_pairing(self):
        movement = BoxMovement.objects.create(
            box=self.box, from_thermal_zone=self.a, to_thermal_zone=self.b,
            moved_at=self.destination.starts_at, user=self.admin,
        )
        audit = AuditLog.objects.create(
            organization=self.org, user=self.admin, action=AuditLog.Action.UPDATE,
            object_type="box", object_id=self.box.global_code,
            metadata={"movement_id": movement.pk, "from_thermal_zone_id": self.a.pk,
                      "to_thermal_zone_id": self.b.pk, "moved_at": movement.moved_at.isoformat()},
        )
        for numeric_identity in (False, True):
            with self.subTest(numeric_identity=numeric_identity):
                if numeric_identity:
                    audit.object_id = "old-unavailable-code"
                    audit.metadata["box_id"] = self.box.pk
                    audit.save()
                event = next(e for e in self.history().json()["events"] if e["recorded_movement_id"] == movement.pk)
                self.assertEqual(event["kind"], "unclassified")
                self.assertTrue(event["evidence"]["recorded_via_polypbase"])
                self.assertFalse(event["evidence"]["period_pairing_reliable"])
                self.assertFalse(event["evidence"]["has_origin_link"])
                self.assertFalse(event["evidence"]["has_destination_link"])

    def test_inconsistent_or_malformed_service_audit_is_not_proof(self):
        movement = move_box_to_thermal_zone(box=self.box, thermal_zone=self.a, moved_at=paris(2026, 1, 7, 12), user=self.admin, notes="")
        audit = AuditLog.objects.get(metadata__movement_id=movement.pk)
        original_metadata = audit.metadata.copy()
        cases = [
            {"movement_id": None}, {"movement_id": str(movement.pk)}, {"movement_id": True},
            {"movement_id": [movement.pk]}, {"movement_id": movement.pk + 1000},
            {"moved_at": None}, {"moved_at": "invalid"}, {"moved_at": "2026-99-99T12:00:00Z"},
            {"moved_at": "2026-01-07T12:00:00"}, {"moved_at": paris(2026, 1, 8, 12).isoformat()},
            {"from_thermal_zone_id": self.a.pk}, {"from_thermal_zone_id": None},
            {"from_thermal_zone_id": str(self.b.pk)}, {"to_thermal_zone_id": self.b.pk},
            {"to_thermal_zone_id": [self.a.pk]}, {"box_id": self.box.pk + 1000},
            {"box_id": str(self.box.pk)}, {"box_id": True},
            {"event": "box_movement_correction"}, {"transition": "inactive->active"},
            {"movement_kind": "reactivation"}, {"iso_week_start": "2026-01-12"},
            {"origin_location_id": self.origin.pk}, {"destination_location_id": self.destination.pk},
            {"destination_location_id": True},
        ]
        for changes in cases:
            with self.subTest(changes=changes):
                AuditLog.objects.filter(pk=audit.pk).update(metadata={**original_metadata, **changes})
                event = next(e for e in self.history().json()["events"] if e["recorded_movement_id"] == movement.pk)
                self.assertFalse(event["evidence"]["recorded_via_polypbase"])
        for missing in ("movement_id", "moved_at", "from_thermal_zone_id", "to_thermal_zone_id"):
            with self.subTest(missing=missing):
                AuditLog.objects.filter(pk=audit.pk).update(metadata={k: v for k, v in original_metadata.items() if k != missing})
                event = next(e for e in self.history().json()["events"] if e["recorded_movement_id"] == movement.pk)
                self.assertFalse(event["evidence"]["recorded_via_polypbase"])
        for changes in ({"organization": self.other}, {"object_type": "other"},
                        {"object_id": "another-box"}, {"user": self.tech}, {"user": None},
                        {"action": AuditLog.Action.CREATION}):
            with self.subTest(audit_fields=changes):
                AuditLog.objects.filter(pk=audit.pk).update(
                    organization=self.org, object_type="box", object_id=self.box.global_code,
                    user=self.admin, action=AuditLog.Action.UPDATE, metadata=original_metadata,
                )
                AuditLog.objects.filter(pk=audit.pk).update(**changes)
                event = next(e for e in self.history().json()["events"] if e["recorded_movement_id"] == movement.pk)
                self.assertFalse(event["evidence"]["recorded_via_polypbase"])

    def test_correction_audit_alone_does_not_prove_service_recording(self):
        movement = BoxMovement.objects.create(
            box=self.box, from_thermal_zone=self.a, to_thermal_zone=self.b,
            moved_at=self.destination.starts_at, user=self.admin,
        )
        correct_box_movement(box_id=self.box.pk, movement_id=movement.pk,
                             organization_ids=[self.org.pk], user=self.admin,
                             data={"expected_revision": movement.revision, "notes": "Reviewed note"})
        event = next(e for e in self.history().json()["events"] if e["recorded_movement_id"] == movement.pk)
        self.assertFalse(event["evidence"]["recorded_via_polypbase"])

    def corrected_service_movement(self):
        movement = move_box_to_thermal_zone(box=self.box, thermal_zone=self.a, moved_at=paris(2026, 1, 7, 12), user=self.admin, notes="Original note")
        third = ThermalZone.objects.create(organization=self.org, name="C")
        for changes in ({"moved_at": paris(2026, 1, 8, 12).isoformat()},
                        {"to_thermal_zone_id": third.pk}, {"notes": "Corrected note"}):
            movement = correct_box_movement(
                box_id=self.box.pk, movement_id=movement.pk, organization_ids=[self.org.pk], user=self.tech,
                data={"expected_revision": movement.revision, **changes},
            )
        return movement

    def assert_service_recording(self, movement, expected):
        event = next(e for e in self.history().json()["events"] if e["recorded_movement_id"] == movement.pk)
        self.assertEqual(event["evidence"]["recorded_via_polypbase"], expected)

    def test_original_service_provenance_survives_date_destination_and_notes_corrections(self):
        movement = self.corrected_service_movement()
        self.assertEqual(movement.revision, 3)
        self.assertNotEqual(movement.to_thermal_zone_id, self.a.pk)
        self.assertEqual(movement.moved_at, paris(2026, 1, 8, 12))
        self.assert_service_recording(movement, True)
        ThermalZone.objects.filter(pk=movement.to_thermal_zone_id).update(name="Renamed C")
        self.assert_service_recording(movement, True)

    def test_correction_chain_without_original_receipt_never_proves_provenance(self):
        movement = self.corrected_service_movement()
        AuditLog.objects.filter(metadata__movement_id=movement.pk, metadata__event__isnull=True).delete()
        self.assert_service_recording(movement, False)

    def test_every_correction_revision_is_required_even_for_notes_only(self):
        movement = self.corrected_service_movement()
        for audit in AuditLog.objects.filter(metadata__event="box_movement_correction").order_by("pk"):
            with self.subTest(revision=audit.metadata["revision"]):
                AuditLog.objects.filter(pk=audit.pk).update(metadata={})
                self.assert_service_recording(movement, False)
                AuditLog.objects.filter(pk=audit.pk).update(metadata=audit.metadata)
        self.assert_service_recording(movement, True)

    def test_malformed_or_inconsistent_correction_chain_is_not_proof(self):
        movement = self.corrected_service_movement()
        audit = AuditLog.objects.filter(metadata__event="box_movement_correction").order_by("pk").first()
        original = audit.metadata
        mutations = [
            (("box_id",), self.box.pk + 1000), (("box_id",), str(self.box.pk)),
            (("movement_id",), str(movement.pk)), (("original_author_id",), self.tech.pk),
            (("movement_kind",), "unclassified"), (("corrected_at",), "2026-01-09T12:00:00"),
            (("revision",), None), (("revision", "before"), True), (("revision", "after"), 3),
            (("changed_fields",), ["notes"]), (("changed_fields",), ["moved_at", "moved_at"]),
            (("changed_fields",), [["moved_at"]]), (("movement_before",), None),
            (("movement_before", "moved_at"), paris(2026, 1, 6, 12).isoformat()),
            (("movement_before", "to_thermal_zone", "id"), self.foreign.pk),
            (("movement_after", "moved_at"), paris(2026, 1, 9, 12).isoformat()),
            (("movement_after", "notes"), "Unrecorded change"),
            (("movement_after", "iso_week_start"), "2026-01-12"),
        ]
        for path, value in mutations:
            with self.subTest(path=path, value=value):
                metadata = deepcopy(original)
                target = metadata
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = value
                AuditLog.objects.filter(pk=audit.pk).update(metadata=metadata)
                self.assert_service_recording(movement, False)
        AuditLog.objects.filter(pk=audit.pk).update(metadata=original)
        for fields in ({"organization": self.other}, {"user": None}, {"object_type": "other"},
                       {"action": AuditLog.Action.CREATION}):
            with self.subTest(audit_fields=fields):
                AuditLog.objects.filter(pk=audit.pk).update(**fields)
                self.assert_service_recording(movement, False)
                AuditLog.objects.filter(pk=audit.pk).update(organization=self.org, user=self.tech, object_type="box", action=AuditLog.Action.UPDATE)
        self.assert_service_recording(movement, True)

    def test_ambiguous_correction_revision_and_current_state_mismatch_are_not_proof(self):
        movement = self.corrected_service_movement()
        audit = AuditLog.objects.filter(metadata__event="box_movement_correction").order_by("pk").first()
        duplicate = AuditLog.objects.create(organization=self.org, user=self.tech, action=AuditLog.Action.UPDATE,
                                           object_type="box", object_id=self.box.global_code, metadata=audit.metadata)
        self.assert_service_recording(movement, False)
        duplicate.delete()
        BoxMovement.objects.filter(pk=movement.pk).update(notes="Unaudited edit")
        self.assert_service_recording(movement, False)

    def test_initial_location_service_audit_proves_null_origin_and_same_utc_instant(self):
        self.box.thermal_zone = None
        self.box.save(update_fields=["thermal_zone"])
        self.destination.ends_at = paris(2026, 1, 6)
        self.destination.save(update_fields=["ends_at"])
        movement = move_box_to_thermal_zone(box=self.box, thermal_zone=self.a, moved_at=paris(2026, 1, 7, 12), user=self.admin, notes="")
        audit = AuditLog.objects.get(metadata__movement_id=movement.pk)
        audit.metadata["moved_at"] = "2026-01-07T11:00:00Z"
        audit.save()
        event = next(e for e in self.history().json()["events"] if e["recorded_movement_id"] == movement.pk)
        self.assertEqual(event["kind"], "initial_location")
        self.assertTrue(event["evidence"]["recorded_via_polypbase"])
        del audit.metadata["from_thermal_zone_id"]
        audit.save()
        event = next(e for e in self.history().json()["events"] if e["recorded_movement_id"] == movement.pk)
        self.assertFalse(event["evidence"]["recorded_via_polypbase"])

    def test_service_recording_reuses_loaded_audits(self):
        self.client.force_login(self.viewer)
        with CaptureQueriesContext(connection) as baseline:
            self.history()
        for day, zone in ((7, self.a), (14, self.b), (21, self.a)):
            movement = move_box_to_thermal_zone(box=self.box, thermal_zone=zone, moved_at=paris(2026, 1, day, 12), user=self.admin, notes="")
            correct_box_movement(box_id=self.box.pk, movement_id=movement.pk, organization_ids=[self.org.pk], user=self.tech,
                                 data={"expected_revision": movement.revision, "notes": "Audited correction"})
        with CaptureQueriesContext(connection) as populated:
            events = self.history().json()["events"]
        audit_table = AuditLog._meta.db_table
        self.assertEqual(sum(audit_table in q["sql"] for q in baseline),
                         sum(audit_table in q["sql"] for q in populated))
        self.assertEqual(sum(e["evidence"]["recorded_via_polypbase"] for e in events), 3)
        self.assertTrue(all(not e["evidence"]["recorded_via_polypbase"] for e in events if e["recorded_movement_id"] is None))

    def test_lifecycle_projection_uses_explicit_pairing_not_audit_timestamp(self):
        deactivate_box(box=self.box, user=self.admin, reason="Synthetic stop")
        reactivate_box(box=self.box, user=self.admin, thermal_zone=self.a)
        events = self.history().json()["events"]
        deactivation = next(e for e in events if e["kind"] == "deactivation")
        self.assertEqual(deactivation["temporal_precision"], "exact")
        self.assertEqual(deactivation["evidence"]["closed_location_ids"], [self.destination.pk])
        reactivation = next(e for e in events if e["provenance"] == "lifecycle_audit" and e["kind"] == "reactivation")
        self.assertTrue(reactivation["evidence"]["lifecycle_pairing_reliable"])
        self.assertIsNotNone(reactivation["evidence"]["paired_movement_id"])

    def test_new_qualification_records_explicit_movement_pair(self):
        box = Box.objects.create(organization=self.org, strain=self.box.strain, box_number="002",
                                 global_code="HIS-LAB-1.002", status=Box.Status.PENDING_REVIEW)
        qualify_pending_box(box=box, user=self.admin, target_status=Box.Status.ACTIVE, thermal_zone=self.a)
        self.box = box
        events = self.history().json()["events"]
        audit_event = next(e for e in events if e["provenance"] == "lifecycle_audit")
        self.assertEqual(audit_event["kind"], "qualification")
        self.assertTrue(audit_event["evidence"]["lifecycle_pairing_reliable"])
        self.assertEqual(audit_event["evidence"]["paired_movement_id"], box.movements.get().pk)
        self.assertEqual(audit_event["temporal_precision"], "exact")

    def test_duplicate_lifecycle_claim_never_certifies_movement_pair(self):
        self.inactive_cycle()
        audit = AuditLog.objects.get(metadata__transition="inactive->active")
        AuditLog.objects.create(
            organization=self.org, object_type="box", user=self.admin,
            action=AuditLog.Action.UPDATE, metadata=audit.metadata,
        )
        before = self.snapshot()
        events = self.history().json()["events"]
        claims = [e for e in events if e["provenance"] == "lifecycle_audit" and e["kind"] == "reactivation"]
        self.assertEqual(len(claims), 2)
        for event in claims:
            self.assertFalse(event["evidence"]["lifecycle_pairing_reliable"])
            self.assertIsNone(event["evidence"]["paired_movement_id"])
            self.assertIsNone(event["to_thermal_zone"])
            self.assertEqual(event["temporal_precision"], "unresolved")
        self.assertEqual(before, self.snapshot())

    def test_lifecycle_movement_pair_requires_scoped_zones_and_update_action(self):
        self.inactive_cycle()
        audit = AuditLog.objects.get(metadata__transition="inactive->active")
        movement = self.box.movements.get(kind="reactivation")
        for changes in ({"action": AuditLog.Action.CREATION}, {"user": self.tech}):
            with self.subTest(changes=changes):
                AuditLog.objects.filter(pk=audit.pk).update(**changes)
                event = next(e for e in self.inactive_events() if e["kind"] == "reactivation")
                self.assertFalse(event["evidence"]["lifecycle_pairing_reliable"])
                self.assertIsNone(event["to_thermal_zone"])
                AuditLog.objects.filter(pk=audit.pk).update(action=AuditLog.Action.UPDATE, user=self.admin)
        BoxMovement.objects.filter(pk=movement.pk).update(to_thermal_zone=self.foreign)
        event = next(e for e in self.inactive_events() if e["kind"] == "reactivation")
        self.assertFalse(event["evidence"]["lifecycle_pairing_reliable"])
        self.assertEqual(event["temporal_precision"], "unresolved")
        self.assertIsNone(event["to_thermal_zone"])

    def test_lifecycle_pair_requires_unique_original_movement_receipt(self):
        self.inactive_cycle()
        movement = self.box.movements.get(kind="reactivation")
        receipt = AuditLog.objects.get(metadata__movement_id=movement.pk, metadata__transition__isnull=True)
        duplicate = AuditLog.objects.create(
            organization=self.org, object_type="box", object_id=self.box.global_code,
            user=self.admin, action=AuditLog.Action.UPDATE, metadata=receipt.metadata,
        )
        for missing in (False, True):
            with self.subTest(missing=missing):
                if missing:
                    duplicate.delete()
                    receipt.delete()
                event = next(e for e in self.inactive_events() if e["kind"] == "reactivation")
                self.assertFalse(event["evidence"]["lifecycle_pairing_reliable"])
                self.assertEqual(event["temporal_precision"], "unresolved")
                self.assertIsNone(event["to_thermal_zone"])

    def test_lifecycle_pair_survives_audited_destination_correction_and_rename(self):
        self.inactive_cycle()
        movement = self.box.movements.get(kind="reactivation")
        correct_box_movement(box_id=self.box.pk, movement_id=movement.pk,
                             organization_ids=[self.org.pk], user=self.admin,
                             data={"expected_revision": movement.revision, "to_thermal_zone_id": self.b.pk})
        ThermalZone.objects.filter(pk=self.b.pk).update(name="Renamed selected zone")
        event = next(e for e in self.inactive_events() if e["kind"] == "reactivation")
        self.assertTrue(event["evidence"]["lifecycle_pairing_reliable"])
        self.assertEqual(event["evidence"]["paired_movement_id"], movement.pk)
        self.assertEqual(event["to_thermal_zone"], {"id": self.b.pk, "name": "Renamed selected zone"})

    def test_duplicate_deactivation_claim_does_not_certify_period_end(self):
        self.inactive_cycle()
        audit = AuditLog.objects.get(metadata__transition="active->inactive")
        AuditLog.objects.create(
            organization=self.org, object_type="box", user=self.admin,
            action=AuditLog.Action.UPDATE, metadata=audit.metadata,
        )
        events = self.history().json()["events"]
        closures = [e for e in events if e["provenance"] == "lifecycle_audit" and e["kind"] == "deactivation"]
        self.assertEqual(len(closures), 2)
        self.assertTrue(all(not e["evidence"]["period_pairing_reliable"] for e in closures))
        boundary = next(e for e in events if e["id"] == f"location:{self.destination.pk}:end")
        self.assertFalse(boundary["evidence"]["lifecycle_pairing_reliable"])

    def test_non_update_deactivation_audit_does_not_certify_period_end(self):
        self.inactive_cycle()
        audit = AuditLog.objects.get(metadata__transition="active->inactive")
        AuditLog.objects.filter(pk=audit.pk).update(action=AuditLog.Action.CREATION)
        event = next(e for e in self.inactive_events() if e["kind"] == "deactivation")
        self.assertEqual(event["temporal_precision"], "unresolved")
        self.assertFalse(event["evidence"]["period_pairing_reliable"])
        self.assertIsNone(event["from_thermal_zone"])

    def test_unlocated_qualification_is_not_a_reactivation_or_zone_entry(self):
        self.box = Box.objects.create(
            organization=self.org, strain=self.box.strain, box_number="002",
            global_code="HIS-LAB-1.002", status=Box.Status.PENDING_REVIEW,
        )
        qualify_pending_box(box=self.box, user=self.admin, target_status=Box.Status.ACTIVE)
        before = self.snapshot()
        events = self.history().json()["events"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["kind"], "qualification")
        self.assertEqual(events[0]["temporal_precision"], "unresolved")
        self.assertIsNone(events[0]["to_thermal_zone"])
        self.assertFalse(events[0]["evidence"]["lifecycle_pairing_reliable"])
        self.assertFalse(self.box.movements.exists())
        self.assertFalse(self.box.locations.exists())
        for zone in (self.a, self.b):
            response = self.client.get(reverse("api_thermal_zone_history", args=[zone.pk]),
                                       HTTP_X_ORGANIZATION_ID=str(self.org.pk))
            self.assertEqual(response.status_code, 200)
            self.assertNotIn(self.box.pk, [row["box_id"] for row in response.json()["results"]])
        self.box.refresh_from_db()
        self.assertEqual(self.box.status, Box.Status.ACTIVE)
        self.assertIsNone(self.box.thermal_zone_id)
        self.assertEqual(before, self.snapshot())

    def check_lifecycle_zone_counts(self, selected_zone):
        start, end = paris(2026, 1, 7, 12), paris(2026, 1, 8, 12)
        with patch("apps.cultures.services.timezone.now", return_value=start):
            deactivate_box(box=self.box, user=self.admin, reason="Synthetic stop")
        self.box.refresh_from_db()
        self.assertEqual(self.box.status, Box.Status.INACTIVE)
        self.assertIsNone(self.box.thermal_zone_id)
        self.assertFalse(self.box.locations.filter(ends_at__isnull=True, end_date_unknown=False).exists())
        self.assertEqual(self.b.boxes.filter(status=Box.Status.ACTIVE).count(), 0)
        stopped = self.snapshot()
        with self.assertRaises(ValidationError):
            reactivate_box(box=self.box, user=self.admin, thermal_zone=self.foreign)
        self.assertEqual(stopped, self.snapshot())
        events = self.history().json()["events"]
        exit_event = next(e for e in events if e["kind"] == "deactivation")
        self.assertEqual(exit_event["from_thermal_zone"]["id"], self.b.pk)
        self.assertIsNone(exit_event["to_thermal_zone"])
        self.assertEqual(parse_datetime(exit_event["occurred_at"]), start)
        self.assertEqual(stopped, self.snapshot())
        with patch("apps.cultures.services.timezone.now", return_value=end):
            reactivate_box(box=self.box, user=self.admin, thermal_zone=selected_zone)
        self.box.refresh_from_db()
        movement = self.box.movements.get(kind="reactivation")
        self.assertIsNone(movement.from_thermal_zone_id)
        self.assertEqual(movement.to_thermal_zone_id, selected_zone.pk)
        self.assertEqual(self.box.thermal_zone_id, selected_zone.pk)
        self.assertEqual(self.box.status, Box.Status.ACTIVE)
        self.assertEqual(self.box.locations.count(), 3)
        self.assertEqual(self.box.movements.count(), 1)
        self.assertEqual(self.box.locations.filter(ends_at__isnull=True, end_date_unknown=False).count(), 1)
        self.assertEqual(selected_zone.boxes.filter(status=Box.Status.ACTIVE).count(), 1)
        before = self.snapshot()
        events = self.history().json()["events"]
        audit_entry = next(e for e in events if e["provenance"] == "lifecycle_audit" and e["kind"] == "reactivation")
        self.assertTrue(audit_entry["evidence"]["lifecycle_pairing_reliable"])
        self.assertEqual(audit_entry["evidence"]["paired_movement_id"], movement.pk)
        self.assertIsNone(audit_entry["from_thermal_zone"])
        self.assertEqual(audit_entry["to_thermal_zone"]["id"], selected_zone.pk)
        self.assertEqual(parse_datetime(audit_entry["occurred_at"]), end)
        for zone, expected_entries, expected_exits in (
            (self.b, 1 + int(selected_zone == self.b), 1),
            (self.a, 1 + int(selected_zone == self.a), 1),
        ):
            with self.subTest(zone=zone.pk):
                response = self.client.get(reverse("api_thermal_zone_history", args=[zone.pk]),
                                           HTTP_X_ORGANIZATION_ID=str(self.org.pk))
                self.assertEqual(response.status_code, 200)
                rows = response.json()["results"]
                self.assertEqual(response.json()["count"], expected_entries + expected_exits)
                self.assertEqual(sum(row["event_type"] == "arrival" for row in rows), expected_entries)
                self.assertEqual(sum(row["event_type"] == "departure" for row in rows), expected_exits)
                # The deactivation has no destination, even if reactivation selects another zone.
                stopped_rows = [row for row in rows if row["event_type"] == "departure"
                                and parse_datetime(row["occurred_at"]) == start]
                if zone == self.b:
                    self.assertEqual(len(stopped_rows), 1)
                    self.assertIsNone(stopped_rows[0]["related_zone_id"])
                with patch("apps.cultures.api_views.timezone.localdate", return_value=date(2026, 1, 9)):
                    summary = self.client.get(reverse("api_thermal_zone_history_summary", args=[zone.pk]),
                                              HTTP_X_ORGANIZATION_ID=str(self.org.pk))
                self.assertEqual(summary.status_code, 200)
                self.assertEqual(sum(week["entry_count"] for week in summary.json()["weeks"]), expected_entries)
                self.assertEqual(sum(week["exit_count"] for week in summary.json()["weeks"]), expected_exits)
                self.assertEqual(summary.json()["historical_counts"], {"entry_count": 0, "exit_count": 0})
        self.assertEqual(before, self.snapshot())

    def test_same_zone_reactivation_counts_real_periods_once(self):
        self.check_lifecycle_zone_counts(self.b)

    def test_different_zone_reactivation_counts_real_periods_once(self):
        self.check_lifecycle_zone_counts(self.a)
        self.assertEqual(self.b.boxes.filter(status=Box.Status.ACTIVE).count(), 0)

    def test_qualification_without_movement_remains_unresolved(self):
        self.box.status = Box.Status.PENDING_REVIEW
        self.box.save(update_fields=["status"])
        qualify_pending_box(box=self.box, user=self.admin, target_status=Box.Status.INACTIVE, reason="", reason_missing_from_history=True)
        event = next(e for e in self.history().json()["events"] if e["kind"] == "qualification")
        self.assertEqual(event["temporal_precision"], "unresolved")
        self.assertIsNone(event["occurred_at"])

    def test_lifecycle_association_survives_rename_and_rejects_other_organization(self):
        AuditLog.objects.create(organization=self.other, object_type="box", metadata={"box_id": self.box.pk, "transition": "active->inactive"})
        AuditLog.objects.create(organization=self.org, object_type="box", object_id="old-code", metadata={"box_id": self.box.pk, "transition": "active->inactive"})
        events = [e for e in self.history().json()["events"] if e["kind"] == "deactivation"]
        self.assertEqual(len(events), 1)
        self.assertIsNone(events[0]["occurred_at"])

    def test_malformed_lifecycle_fields_are_not_pairing_evidence(self):
        for metadata in ({"transition": ["active->inactive"]},
                         {"transition": "inactive->active", "movement_id": [1]},
                         {"transition": "active->inactive", "changed_at": "not-a-date", "closed_location_ids": "invalid"}):
            AuditLog.objects.create(organization=self.org, object_type="box", metadata={"box_id": self.box.pk, **metadata})
        response = self.history()
        self.assertEqual(response.status_code, 200, response.content)
        for event in response.json()["events"]:
            if event["provenance"] == "lifecycle_audit":
                self.assertEqual(event["temporal_precision"], "unresolved")
                self.assertFalse(event["evidence"]["lifecycle_pairing_reliable"])

    def test_lifecycle_event_blocks_week_reconstruction(self):
        audit = AuditLog.objects.create(organization=self.org, object_type="box", metadata={"box_id": self.box.pk, "transition": "active->inactive"})
        AuditLog.objects.filter(pk=audit.pk).update(created_at=paris(2025, 12, 30))
        self.assertIn("lifecycle_event_in_week", self.plan()["candidates"][0]["blockers"])

    def test_ambiguous_multiple_transitions_are_blocked(self):
        BoxLocation.objects.filter(pk=self.destination.pk).update(ends_at=paris(2026, 1, 1))
        BoxLocation.objects.create(box=self.box, thermal_zone=self.a, starts_at=paris(2026, 1, 1))
        for candidate in self.plan()["candidates"]:
            self.assertIn("multiple_period_transitions_in_week", candidate["blockers"])
        with self.assertRaises(ValidationError):
            self.accept()

    def test_overlap_blocks_entire_box(self):
        BoxLocation.objects.create(box=self.box, thermal_zone=self.a, starts_at=paris(2025, 12, 20), ends_at=paris(2025, 12, 23))
        self.assertTrue(all("period_continuity_unresolved" in c["blockers"] for c in self.plan()["candidates"]))

    def test_actor_of_recorded_event_is_not_leaked_from_foreign_membership(self):
        foreign_actor = get_user_model().objects.create_user(username="foreign-author", email="foreign-author@example.org")
        OrganizationMembership.objects.create(user=foreign_actor, organization=self.other, role="admin")
        movement = BoxMovement.objects.create(box=self.box, to_thermal_zone=self.b, user=foreign_actor)
        event = next(e for e in self.history().json()["events"] if e["recorded_movement_id"] == movement.pk)
        self.assertIsNone(event["actor"])

    def test_legacy_unclassified_without_links_never_pairs_by_timestamp(self):
        movement = BoxMovement.objects.create(box=self.box, from_thermal_zone=self.a, to_thermal_zone=self.b, moved_at=self.destination.starts_at)
        events = self.history().json()["events"]
        event = next(e for e in events if e["recorded_movement_id"] == movement.pk)
        self.assertFalse(event["evidence"]["period_pairing_reliable"])
        for period in (e for e in events if e["kind"] == "location_evidence"):
            self.assertFalse(period["evidence"]["period_pairing_reliable"])

    def test_reconstructions_are_read_only(self):
        self.stored_reconstruction()
        event = HistoricalBoxMovement.objects.get()
        for action in (event.save, event.delete, HistoricalBoxMovement.objects.all().delete,
                       lambda: HistoricalBoxMovement.objects.all().update(notes="Overwrite")):
            with self.assertRaises(ValidationError):
                action()

    def test_incomplete_period_evidence_is_not_overridden_by_review_note(self):
        BoxLocation.objects.filter(pk=self.origin.pk).update(starts_at=paris(2025, 12, 23))
        before = self.snapshot()
        for note in ("Reviewed all adjacent weeks", "Source zones verified manually"):
            with self.assertRaisesMessage(ValidationError, "Blocked or already represented"):
                self.accept(notes=note)
        self.assertFalse(HistoricalBoxMovement.objects.exists())
        self.assertEqual(before, self.snapshot())

    def inactive_cycle(self, hours=2, start=None):
        start = start or paris(2026, 1, 7, 12)
        end = start + timedelta(hours=hours)
        with patch("apps.cultures.services.timezone.now", return_value=start):
            deactivate_box(box=self.box, user=self.admin, reason="Synthetic inactive interval")
        with patch("apps.cultures.services.timezone.now", return_value=end):
            reactivate_box(box=self.box, user=self.admin, thermal_zone=self.a)
        return start, end

    def inactive_events(self):
        return [event for event in self.history().json()["events"]
                if event["provenance"] == "lifecycle_audit" and event["kind"] in ("deactivation", "reactivation")]

    def assert_no_inactive_pair(self):
        events = self.inactive_events()
        self.assertTrue(events)
        for event in events:
            self.assertFalse(event["evidence"]["inactive_pairing_reliable"])
            self.assertIsNone(event["evidence"]["inactive_interval"])

    def test_known_inactive_pair_describes_gap_not_active_period(self):
        start, end = self.inactive_cycle()
        before = self.snapshot()
        events = self.inactive_events()
        self.assertEqual(len(events), 2)
        intervals = [event["evidence"]["inactive_interval"] for event in events]
        self.assertEqual(intervals[0], intervals[1])
        interval = intervals[0]
        self.assertEqual(interval["duration_seconds"], 7200)
        self.assertEqual(parse_datetime(interval["starts_at"]), start)
        self.assertEqual(parse_datetime(interval["ends_at"]), end)
        movement = self.box.movements.get(kind="reactivation")
        self.assertEqual(interval["previous_location_id"], self.destination.pk)
        self.assertEqual(interval["next_location_id"], movement.destination_location_id)
        self.assertNotEqual(interval["previous_location_id"], interval["next_location_id"])
        self.assertTrue(all(event["evidence"]["inactive_pairing_reliable"] for event in events))
        self.assertTrue(interval["masking_eligible"])
        self.assertFalse(interval["measurement_overlap_possible"])
        self.assertEqual(before, self.snapshot())

    def test_short_active_period_is_not_the_inactive_gap(self):
        movement = move_box_to_thermal_zone(box=self.box, thermal_zone=self.a,
                                           moved_at=paris(2026, 1, 7, 11), user=self.admin, notes="Synthetic move")
        previous = movement.destination_location
        self.inactive_cycle()
        events = self.inactive_events()
        for event in events:
            interval = event["evidence"]["inactive_interval"]
            self.assertEqual(interval["previous_location_id"], previous.pk)
            self.assertEqual(interval["duration_seconds"], 7200)
        previous.refresh_from_db()
        self.assertEqual((previous.ends_at - previous.starts_at).total_seconds(), 3600)
        self.assertTrue(BoxLocation.objects.filter(pk=previous.pk).exists())

    def test_environment_source_observation_also_prevents_masking(self):
        self.inactive_cycle()
        other_box = Box.objects.create(organization=self.org, strain=self.box.strain, box_number="002", global_code="HIS-LAB-1.002")
        BiologicalMeasurement.objects.create(box=other_box, environment_source_box=self.box,
                                             measured_on=date(2026, 1, 5), polyp_count=0)
        for event in self.inactive_events():
            interval = event["evidence"]["inactive_interval"]
            self.assertTrue(interval["measurement_overlap_possible"])
            self.assertFalse(interval["masking_eligible"])

    def test_gap_duration_uses_elapsed_time_across_paris_dst(self):
        self.inactive_cycle(hours=24, start=paris(2026, 3, 28, 12))
        for event in self.inactive_events():
            self.assertTrue(event["evidence"]["inactive_pairing_reliable"])
            interval = event["evidence"]["inactive_interval"]
            self.assertEqual(interval["duration_seconds"], 23 * 3600)
            self.assertTrue(interval["masking_eligible"])

    def test_long_inactive_gap_is_paired_but_not_maskable(self):
        self.inactive_cycle(hours=24)
        for event in self.inactive_events():
            self.assertTrue(event["evidence"]["inactive_pairing_reliable"])
            self.assertFalse(event["evidence"]["inactive_interval"]["masking_eligible"])

    def test_measurement_day_or_legacy_week_overlap_prevents_masking(self):
        self.inactive_cycle()
        measurement = BiologicalMeasurement.objects.create(box=self.box, measured_on=date(2026, 1, 5), polyp_count=0)
        for precision, day in (("legacy_unverified", date(2026, 1, 5)), ("exact_date", date(2026, 1, 7))):
            measurement.temporal_precision = precision
            measurement.measured_on = day
            measurement.save()
            for event in self.inactive_events():
                self.assertTrue(event["evidence"]["inactive_pairing_reliable"])
                interval = event["evidence"]["inactive_interval"]
                self.assertTrue(interval["measurement_overlap_possible"])
                self.assertFalse(interval["masking_eligible"])

    def test_mismatched_deactivation_timestamp_prevents_pairing(self):
        start, end = self.inactive_cycle()
        audit = AuditLog.objects.get(metadata__transition="active->inactive")
        audit.metadata["changed_at"] = (start - timedelta(minutes=1)).isoformat()
        audit.save(update_fields=["metadata"])
        self.assert_no_inactive_pair()

    def test_foreign_audit_cannot_create_or_block_pairing(self):
        start, end = self.inactive_cycle()
        audit = AuditLog.objects.create(organization=self.other, object_type="box", object_id=self.box.global_code,
                                        metadata={"box_id": self.box.pk, "transition": "unknown"})
        AuditLog.objects.filter(pk=audit.pk).update(created_at=start + timedelta(hours=1))
        self.assertTrue(all(event["evidence"]["inactive_pairing_reliable"] for event in self.inactive_events()))

    def test_unverified_receipt_and_exact_measurement_dates_do_not_override_blocker(self):
        self.stored_reconstruction(legacy=True)
        for measurement in (self.before, self.after):
            measurement.temporal_precision = "exact_date"
            measurement.save()
        before = self.snapshot()
        self.assertEqual(self.plan()["candidates"][0]["reconciliation_status"], "blocked")
        with self.assertRaisesMessage(ValidationError, "Blocked or already represented"):
            self.accept()
        self.assertEqual(HistoricalBoxMovement.objects.count(), 1)
        self.assertEqual(before, self.snapshot())

    def test_interposed_lifecycle_even_unresolved_prevents_pairing(self):
        start, end = self.inactive_cycle()
        audit = AuditLog.objects.create(organization=self.org, object_type="box", metadata={"box_id": self.box.pk, "transition": "unknown"})
        AuditLog.objects.filter(pk=audit.pk).update(created_at=start + timedelta(hours=1))
        self.assert_no_inactive_pair()

    def test_interposed_audit_by_code_without_numeric_box_id_prevents_pairing(self):
        start, end = self.inactive_cycle()
        audit = AuditLog.objects.create(organization=self.org, object_type="box", object_id=self.box.global_code, metadata={"event": "unknown"})
        AuditLog.objects.filter(pk=audit.pk).update(created_at=start + timedelta(hours=1))
        self.assert_no_inactive_pair()

    def test_interposed_movement_prevents_pairing(self):
        start, end = self.inactive_cycle()
        BoxMovement.objects.create(box=self.box, to_thermal_zone=self.b, moved_at=start + timedelta(hours=1))
        self.assert_no_inactive_pair()

    def test_interposed_location_prevents_pairing(self):
        start, end = self.inactive_cycle()
        BoxLocation.objects.create(box=self.box, thermal_zone=self.b,
                                   starts_at=start + timedelta(minutes=15), ends_at=end - timedelta(minutes=15))
        self.assert_no_inactive_pair()

    def test_duplicate_deactivation_audit_prevents_pairing(self):
        self.inactive_cycle()
        audit = AuditLog.objects.get(metadata__transition="active->inactive")
        duplicate = AuditLog.objects.create(organization=self.org, object_type="box", user=self.admin, metadata=audit.metadata)
        AuditLog.objects.filter(pk=duplicate.pk).update(created_at=audit.created_at)
        self.assert_no_inactive_pair()

    def test_missing_reactivation_link_and_unknown_period_end_prevent_pairing(self):
        self.inactive_cycle()
        audit = AuditLog.objects.get(metadata__transition="inactive->active")
        original = audit.metadata.copy()
        audit.metadata.pop("movement_id")
        audit.save(update_fields=["metadata"])
        self.assert_no_inactive_pair()
        audit.metadata = original
        audit.save(update_fields=["metadata"])
        BoxLocation.objects.filter(pk=self.destination.pk).update(ends_at=None, end_date_unknown=True)
        self.assert_no_inactive_pair()

    def test_legacy_closure_and_inconsistent_status_snapshots_prevent_pairing(self):
        self.inactive_cycle()
        audit = AuditLog.objects.get(metadata__transition="inactive->active")
        original = audit.metadata.copy()
        audit.metadata["legacy_closed_location_ids"] = [self.origin.pk]
        audit.save(update_fields=["metadata"])
        self.assert_no_inactive_pair()
        audit.metadata = original
        audit.metadata["before"] = {"status": "active", "thermal_zone_id": None}
        audit.save(update_fields=["metadata"])
        self.assert_no_inactive_pair()

    def test_foreign_destination_period_prevents_pairing_without_leaking_zone(self):
        self.inactive_cycle()
        movement = self.box.movements.get(kind="reactivation")
        BoxLocation.objects.filter(pk=movement.destination_location_id).update(thermal_zone=self.foreign)
        self.assert_no_inactive_pair()
        self.assertNotIn("Never expose", self.history().content.decode())

    def test_model_constraints_and_period_scope(self):
        self.stored_reconstruction()
        event = HistoricalBoxMovement.objects.get()
        event.iso_year, event.iso_week = 2025, 53
        with self.assertRaises(ValidationError):
            event.full_clean()
        event.iso_year, event.iso_week = 2026, 1
        event.to_thermal_zone = self.foreign
        with self.assertRaises(ValidationError):
            event.full_clean()
        with self.assertRaises(IntegrityError), transaction.atomic():
            HistoricalBoxMovement._base_manager.filter(pk=event.pk).update(iso_week=54)
