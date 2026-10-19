import json
from datetime import date, datetime, timedelta
from datetime import timezone as dt_timezone
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.measurements.models import BiologicalMeasurement, DailyTemperature
from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain

from .models import MOVEMENT_WEEK_TIME_ZONE, Box, BoxLocation, BoxMovement, ThermalZone
from .services import deactivate_box, move_box_to_thermal_zone, reactivate_box

PARIS = MOVEMENT_WEEK_TIME_ZONE


def paris(*args):
    return datetime(*args, tzinfo=PARIS)


class BoxMovementCorrectionTests(TestCase):
    def setUp(self):
        user_model = get_user_model()

        def user(name):
            return user_model.objects.create_user(username=name, email=f"{name}@example.org", password="secret")

        self.admin = user("corr_admin")
        self.technician = user("corr_tech")
        self.viewer = user("corr_viewer")
        self.inactive_member = user("corr_inactive")
        self.foreign_admin = user("corr_foreign")
        self.outsider = user("corr_outsider")
        self.organization = Organization.objects.create(name="Correction laboratory")
        self.other_organization = Organization.objects.create(name="Correction foreign laboratory")
        roles = OrganizationMembership.Role
        for member, organization, role, active in (
            (self.admin, self.organization, roles.ADMIN, True),
            (self.technician, self.organization, roles.LAB_TECHNICIAN, True),
            (self.viewer, self.organization, roles.VIEWER, True),
            (self.inactive_member, self.organization, roles.LAB_TECHNICIAN, False),
            (self.foreign_admin, self.other_organization, roles.ADMIN, True),
        ):
            OrganizationMembership.objects.create(user=member, organization=organization, role=role, is_active=active)
        species = Species.objects.create(scientific_name="Aurelia correction", genus_species_code="ACR")
        self.strain = Strain.objects.create(
            species=species, code="ACR-LAB-1", organization=self.organization, number=1, origin_code="LAB",
        )
        self.zone_a = ThermalZone.objects.create(organization=self.organization, name="Zone A")
        self.zone_b = ThermalZone.objects.create(organization=self.organization, name="Zone B")
        self.zone_c = ThermalZone.objects.create(organization=self.organization, name="Zone C")
        self.inactive_zone = ThermalZone.objects.create(organization=self.organization, name="Closed", is_active=False)
        self.foreign_zone = ThermalZone.objects.create(organization=self.other_organization, name="Foreign")

        self.box = Box.objects.create(
            organization=self.organization, global_code="ACR-LAB-1.001", box_number="001",
            strain=self.strain, thermal_zone=self.zone_a,
        )
        self.t0 = paris(2026, 8, 3, 9, 0)
        self.initial = BoxLocation.objects.create(box=self.box, thermal_zone=self.zone_a, starts_at=self.t0)
        self.t1 = paris(2026, 8, 11, 10, 0)  # week of 2026-08-10
        self.movement = move_box_to_thermal_zone(
            box=self.box, thermal_zone=self.zone_b, moved_at=self.t1, user=self.admin, notes="Initial note",
        )

    # Helpers -----------------------------------------------------------------

    def patch_movement(self, payload, *, user=None, movement=None, box=None, organization=None):
        self.client.force_login(user or self.technician)
        headers = {}
        if organization is not None:
            headers["HTTP_X_ORGANIZATION_ID"] = str(organization.pk)
        return self.client.patch(
            reverse("api_box_movement_detail", args=[(box or self.box).pk, (movement or self.movement).pk]),
            data=json.dumps(payload),
            content_type="application/json",
            **headers,
        )

    def snapshot(self):
        return {
            "box": list(Box.objects.order_by("id").values()),
            "locations": list(BoxLocation.objects.order_by("id").values()),
            "movements": list(BoxMovement.objects.order_by("id").values()),
            "audits": list(AuditLog.objects.order_by("id").values()),
        }

    def assert_unchanged(self, before):
        self.assertEqual(self.snapshot(), before)

    def second_movement(self, moved_at=None):
        return move_box_to_thermal_zone(
            box=self.box, thermal_zone=self.zone_c, moved_at=moved_at or paris(2026, 8, 19, 10), user=self.admin,
            notes="",
        )

    def correction_audits(self):
        return AuditLog.objects.filter(metadata__event="box_movement_correction").order_by("id")

    # Notes -------------------------------------------------------------------

    def test_notes_only_correction_preserves_identity_author_and_periods(self):
        movement_audit = AuditLog.objects.get(metadata__movement_id=self.movement.pk)
        destination = self.movement.destination_location
        periods_before = list(BoxLocation.objects.order_by("id").values("id", "starts_at", "ends_at", "thermal_zone_id"))

        response = self.patch_movement({"expected_revision": 0, "notes": "Corrected note"})

        self.assertEqual(response.status_code, 200, response.content)
        self.movement.refresh_from_db()
        self.assertEqual(self.movement.notes, "Corrected note")
        self.assertEqual(self.movement.user, self.admin)
        self.assertEqual(self.movement.moved_at, self.t1)
        self.assertEqual(self.movement.revision, 1)
        self.assertEqual(
            list(BoxLocation.objects.order_by("id").values("id", "starts_at", "ends_at", "thermal_zone_id")),
            periods_before,
        )
        destination.refresh_from_db()
        self.assertEqual(destination.notes, "Corrected note")
        audit = self.correction_audits().get()
        self.assertEqual(audit.user, self.technician)
        self.assertEqual(audit.organization, self.organization)
        self.assertEqual(audit.metadata["movement_id"], self.movement.pk)
        self.assertEqual(audit.metadata["original_author_id"], self.admin.pk)
        self.assertEqual(audit.metadata["revision"], {"before": 0, "after": 1})
        self.assertEqual(audit.metadata["changed_fields"], ["notes"])
        self.assertEqual(audit.metadata["movement_before"]["notes"], "Initial note")
        self.assertEqual(audit.metadata["movement_after"]["notes"], "Corrected note")
        self.assertIsNone(audit.metadata["current_thermal_zone_change"])
        self.assertEqual(
            [(change["location_id"], change["role"]) for change in audit.metadata["period_changes"]],
            [(destination.pk, "destination")],
        )
        self.assertEqual(audit.metadata["modifications"], {"note": {"avant": "Initial note", "apres": "Corrected note"}})
        movement_audit_after = AuditLog.objects.get(pk=movement_audit.pk)
        self.assertEqual(movement_audit_after.metadata, movement_audit.metadata)
        payload = next(item for item in response.json()["movements"] if item["id"] == self.movement.pk)
        self.assertEqual((payload["notes"], payload["revision"]), ("Corrected note", 1))

    def test_notes_correction_does_not_overwrite_an_independent_period_note(self):
        destination = self.movement.destination_location
        BoxLocation.objects.filter(pk=destination.pk).update(notes="Independent period note")
        self.assertEqual(self.patch_movement({"expected_revision": 0, "notes": "x"}).status_code, 200)
        destination.refresh_from_db()
        self.assertEqual(destination.notes, "Independent period note")
        self.assertEqual(self.correction_audits().get().metadata["period_changes"], [])

    def test_unclassified_movement_allows_notes_but_refuses_structural_edits(self):
        legacy = BoxMovement.objects.create(
            box=self.box, from_thermal_zone=self.zone_a, to_thermal_zone=self.zone_b,
            moved_at=self.t1, user=None, notes="Legacy",
        )
        before = self.snapshot()
        cases = (
            ({"moved_at": paris(2026, 8, 12).isoformat()}, "movement_provenance_unresolved"),
            # No recorded period link: the timeline is never guessed.
            ({"to_thermal_zone_id": self.zone_c.pk}, "movement_period_link_missing"),
        )
        for payload, code in cases:
            response = self.patch_movement({"expected_revision": 0, **payload}, movement=legacy)
            self.assertEqual((response.status_code, response.json()["code"]), (409, code))
        self.assert_unchanged(before)
        self.assertEqual(self.patch_movement({"expected_revision": 0, "notes": "Legacy fixed"}, movement=legacy).status_code, 200)
        legacy.refresh_from_db()
        self.assertEqual((legacy.notes, legacy.kind, legacy.iso_week_start), ("Legacy fixed", "unclassified", None))

    def test_unclassified_movement_with_proven_links_allows_a_terminal_destination_only(self):
        self.movement.kind = BoxMovement.Kind.UNCLASSIFIED
        self.movement.save()
        before = self.snapshot()
        response = self.patch_movement({"expected_revision": 0, "moved_at": paris(2026, 8, 12).isoformat()})
        self.assertEqual((response.status_code, response.json()["code"]), (409, "movement_provenance_unresolved"))
        self.assert_unchanged(before)
        response = self.patch_movement({"expected_revision": 0, "to_thermal_zone_id": self.zone_c.pk})
        self.assertEqual(response.status_code, 200, response.content)
        self.movement.refresh_from_db()
        self.assertEqual((self.movement.kind, self.movement.iso_week_start), ("unclassified", None))
        self.assertEqual(self.movement.destination_location.thermal_zone, self.zone_c)

    def test_serializer_exposes_period_link_flags_without_ids(self):
        legacy = BoxMovement.objects.create(
            box=self.box, from_thermal_zone=self.zone_a, to_thermal_zone=self.zone_b, moved_at=self.t0,
        )
        self.client.force_login(self.technician)
        response = self.client.get(reverse("api_box_detail", args=[self.box.pk]))
        items = {item["id"]: item for item in response.json()["movements"]}
        linked = items[self.movement.pk]
        self.assertEqual((linked["has_origin_link"], linked["has_destination_link"]), (True, True))
        self.assertEqual((items[legacy.pk]["has_origin_link"], items[legacy.pk]["has_destination_link"]), (False, False))
        self.assertNotIn("origin_location", items[legacy.pk])

    # Date --------------------------------------------------------------------

    def test_date_correction_within_the_same_week_moves_both_boundaries(self):
        new_time = paris(2026, 8, 13, 15, 30)
        response = self.patch_movement({"expected_revision": 0, "moved_at": new_time.isoformat()})

        self.assertEqual(response.status_code, 200, response.content)
        self.movement.refresh_from_db()
        self.initial.refresh_from_db()
        destination = self.movement.destination_location
        self.assertEqual(self.movement.moved_at, new_time)
        self.assertEqual(self.movement.iso_week_start, date(2026, 8, 10))
        self.assertEqual(self.initial.ends_at, new_time)
        self.assertEqual(destination.starts_at, new_time)
        self.box.refresh_from_db()
        self.assertEqual(self.box.thermal_zone, self.zone_b)
        audit = self.correction_audits().get()
        self.assertEqual(audit.metadata["changed_fields"], ["moved_at"])
        changes = {change["role"]: change for change in audit.metadata["period_changes"]}
        self.assertEqual(datetime.fromisoformat(changes["origin"]["before"]["ends_at"]), self.t1)
        self.assertEqual(datetime.fromisoformat(changes["origin"]["after"]["ends_at"]), new_time)
        self.assertEqual(datetime.fromisoformat(changes["destination"]["before"]["starts_at"]), self.t1)
        self.assertEqual(datetime.fromisoformat(changes["destination"]["after"]["starts_at"]), new_time)

    def boxed_movement(self, moved_at, *, start, code):
        """An ordinary movement of its own box, after a first period opened at ``start``."""
        box = Box.objects.create(
            organization=self.organization, global_code=code, box_number=code[-3:],
            strain=self.strain, thermal_zone=self.zone_a,
        )
        BoxLocation.objects.create(box=box, thermal_zone=self.zone_a, starts_at=start)
        movement = move_box_to_thermal_zone(
            box=box, thermal_zone=self.zone_b, moved_at=moved_at, user=self.admin, notes="",
        )
        return {"movement": movement, "box": box}

    def test_date_correction_covers_monday_to_sunday_of_the_original_week(self):
        steps = (paris(2026, 8, 10, 0, 0, 0), paris(2026, 8, 16, 23, 59, 59), self.t1)
        for revision, moved_at in enumerate(steps):
            response = self.patch_movement({"expected_revision": revision, "moved_at": moved_at.isoformat()})
            self.assertEqual(response.status_code, 200, (moved_at, response.content))
            self.movement.refresh_from_db()
            self.assertEqual((self.movement.moved_at, self.movement.iso_week_start), (moved_at, date(2026, 8, 10)))

    def test_date_correction_cannot_leave_the_original_week(self):
        before = self.snapshot()
        cases = (
            paris(2026, 8, 17, 0, 0, 0),  # Sunday into Monday
            paris(2026, 8, 9, 23, 59, 59),  # Monday back into the previous Sunday
            paris(2026, 8, 6, 8),  # a free earlier week stays refused
            paris(2026, 8, 19, 10),  # a free later week stays refused
        )
        for moved_at in cases:
            response = self.patch_movement({"expected_revision": 0, "moved_at": moved_at.isoformat(), "notes": "also"})
            self.assertEqual((response.status_code, response.json()["code"]), (400, "movement_date_outside_week"), moved_at)
            self.assertIn("moved_at", response.json())
        self.assert_unchanged(before)

    def test_week_boundary_uses_paris_days_not_utc_days(self):
        # Sunday 23:30 Paris is 21:30 UTC the same day; Monday 00:30 Paris is still Sunday in UTC.
        sunday_late = paris(2026, 8, 16, 23, 30)
        self.assertEqual(self.patch_movement({"expected_revision": 0, "moved_at": sunday_late.isoformat()}).status_code, 200)
        monday_early = paris(2026, 8, 17, 0, 30)
        self.assertEqual(monday_early.astimezone(dt_timezone.utc).date(), date(2026, 8, 16))
        before = self.snapshot()
        response = self.patch_movement({"expected_revision": 1, "moved_at": monday_early.isoformat()})
        self.assertEqual(response.json()["code"], "movement_date_outside_week")
        self.assert_unchanged(before)
        # In its own Monday week, the same kind of instant is accepted.
        target = self.boxed_movement(paris(2026, 8, 25, 10), start=paris(2026, 8, 20), code="ACR-LAB-1.090")
        early = paris(2026, 8, 24, 0, 30)
        self.assertEqual(early.astimezone(dt_timezone.utc).date(), date(2026, 8, 23))
        response = self.patch_movement({"expected_revision": 0, "moved_at": early.isoformat()}, **target)
        self.assertEqual(response.status_code, 200, response.content)
        target["movement"].refresh_from_db()
        self.assertEqual(target["movement"].iso_week_start, date(2026, 8, 24))

    def test_iso_year_boundary_weeks_stay_within_their_own_week(self):
        # 2026-12-31 is in ISO week 53 of 2026 (Mon 2026-12-28 to Sun 2027-01-03); week 1 of 2027 starts 2027-01-04.
        cases = (
            (paris(2026, 12, 31, 10), paris(2026, 12, 28, 0), paris(2027, 1, 3, 23, 59, 59), paris(2027, 1, 4, 0)),
            (paris(2027, 1, 5, 10), paris(2027, 1, 4, 0), paris(2027, 1, 10, 23, 59, 59), paris(2027, 1, 3, 23, 59, 59)),
        )
        with patch("apps.cultures.movement_corrections.timezone.now", return_value=paris(2027, 3, 1)):
            for index, (original, first, last, outside) in enumerate(cases):
                target = self.boxed_movement(original, start=paris(2026, 12, 1), code=f"ACR-LAB-1.09{index + 1}")
                for revision, moved_at in enumerate((first, last)):
                    response = self.patch_movement({"expected_revision": revision, "moved_at": moved_at.isoformat()}, **target)
                    self.assertEqual(response.status_code, 200, (moved_at, response.content))
                    target["movement"].refresh_from_db()
                    self.assertEqual(target["movement"].iso_week_start, BoxMovement.week_start_for(original))
                response = self.patch_movement({"expected_revision": 2, "moved_at": outside.isoformat()}, **target)
                self.assertEqual(response.json()["code"], "movement_date_outside_week", outside)

    def test_dst_change_weeks_are_correctable_within_the_week_only(self):
        # Spring change: Sunday 2026-03-29, 02:00 -> 03:00. Autumn change: Sunday 2026-10-25, 03:00 -> 02:00.
        cases = (
            (paris(2026, 3, 26, 10), paris(2026, 3, 29, 3, 30), paris(2026, 3, 30, 0, 0)),
            (paris(2026, 10, 22, 10), paris(2026, 10, 25, 23, 59, 59), paris(2026, 10, 26, 0, 0)),
        )
        with patch("apps.cultures.movement_corrections.timezone.now", return_value=paris(2026, 12, 1)):
            for index, (original, inside, outside) in enumerate(cases):
                target = self.boxed_movement(original, start=paris(2026, 3, 1), code=f"ACR-LAB-1.08{index + 1}")
                response = self.patch_movement({"expected_revision": 0, "moved_at": inside.isoformat()}, **target)
                self.assertEqual(response.status_code, 200, response.content)
                target["movement"].refresh_from_db()
                self.assertEqual(target["movement"].moved_at, inside)
                self.assertEqual(target["movement"].iso_week_start, BoxMovement.week_start_for(original))
                response = self.patch_movement({"expected_revision": 1, "moved_at": outside.isoformat()}, **target)
                self.assertEqual(response.json()["code"], "movement_date_outside_week")

    def test_week_violation_rolls_back_a_combined_date_and_destination_correction(self):
        before = self.snapshot()
        response = self.patch_movement(
            {"expected_revision": 0, "moved_at": paris(2026, 8, 19, 10).isoformat(), "to_thermal_zone_id": self.zone_c.pk},
        )
        self.assertEqual(response.status_code, 400)
        self.assert_unchanged(before)

    def test_zone_history_still_matches_movements_after_a_date_correction(self):
        new_time = paris(2026, 8, 12, 7, 0)
        self.assertEqual(self.patch_movement({"expected_revision": 0, "moved_at": new_time.isoformat()}).status_code, 200)
        self.client.force_login(self.technician)
        events = self.client.get(reverse("api_thermal_zone_history", args=[self.zone_b.pk])).json()["results"]
        arrival = next(event for event in events if event["event_type"] == "arrival")
        self.assertEqual(datetime.fromisoformat(arrival["occurred_at"]), new_time)
        self.assertEqual(arrival["related_zone_id"], self.zone_a.pk)

    def test_date_correction_into_an_occupied_week_is_rejected_without_writes(self):
        later = self.second_movement(paris(2026, 8, 19, 10))
        before = self.snapshot()
        response = self.patch_movement(
            {"expected_revision": 0, "moved_at": paris(2026, 8, 14, 10).isoformat(), "notes": "also"},
            movement=later,
        )
        self.assertEqual((response.status_code, response.json()["code"]), (400, "movement_date_outside_week"))
        self.assert_unchanged(before)

    def test_date_corrections_keep_period_chronology(self):
        # The origin period opens on the Monday of the movement's own week and the destination closes on Thursday.
        target = self.boxed_movement(paris(2026, 9, 9, 10), start=paris(2026, 9, 7, 9), code="ACR-LAB-1.070")
        with patch("apps.cultures.services.timezone.now", return_value=paris(2026, 9, 10, 12)):
            deactivate_box(box=target["box"], user=self.admin, reason="Stopped")
        before = self.snapshot()
        cases = (
            paris(2026, 9, 7, 9),  # zero-duration origin
            paris(2026, 9, 7, 8),  # inverted origin
            paris(2026, 9, 10, 12),  # zero-duration destination
            paris(2026, 9, 11, 10),  # after the destination ended
        )
        for moved_at in cases:
            response = self.patch_movement({"expected_revision": 0, "moved_at": moved_at.isoformat()}, **target)
            self.assertEqual((response.status_code, response.json()["code"]), (409, "movement_chronology_conflict"), moved_at)
        self.assert_unchanged(before)

    def test_future_date_is_rejected(self):
        before = self.snapshot()
        with patch("apps.cultures.movement_corrections.timezone.now", return_value=paris(2026, 8, 12, 12)):
            response = self.patch_movement({"expected_revision": 0, "moved_at": paris(2026, 8, 13, 10).isoformat()})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "movement_date_in_future")
        self.assert_unchanged(before)

    def test_date_of_lifecycle_movements_is_not_correctable(self):
        deactivate_box(box=self.box, user=self.admin, reason="Stopped")
        reactivate_box(box=self.box, thermal_zone=self.zone_c, user=self.admin)
        reactivation = self.box.movements.get(kind=BoxMovement.Kind.REACTIVATION)
        before = self.snapshot()
        response = self.patch_movement(
            {"expected_revision": 0, "moved_at": (reactivation.moved_at - timedelta(minutes=1)).isoformat()},
            movement=reactivation,
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "movement_date_not_correctable")
        self.assert_unchanged(before)

    def test_date_correction_cannot_cross_a_deactivation(self):
        with patch("apps.cultures.services.timezone.now", return_value=paris(2026, 8, 13, 12)):
            deactivate_box(box=self.box, user=self.admin, reason="Stopped")
        destination = self.movement.destination_location
        destination.refresh_from_db()
        response = self.patch_movement(
            {"expected_revision": 0, "moved_at": (destination.ends_at + timedelta(seconds=1)).isoformat()},
        )
        self.assertEqual(response.json()["code"], "movement_chronology_conflict")
        # A date still inside the closed destination period remains correctable.
        inside = paris(2026, 8, 12, 9)
        self.assertEqual(self.patch_movement({"expected_revision": 0, "moved_at": inside.isoformat()}).status_code, 200)
        destination.refresh_from_db()
        self.assertEqual(destination.starts_at, inside)

    def test_missing_origin_link_refuses_a_date_correction(self):
        unlocated = Box.objects.create(
            organization=self.organization, global_code="ACR-LAB-1.002", box_number="002",
            strain=self.strain, thermal_zone=self.zone_a,
        )
        movement = move_box_to_thermal_zone(
            box=unlocated, thermal_zone=self.zone_b, moved_at=self.t1, user=self.admin, notes="",
        )
        self.assertIsNone(movement.origin_location)
        response = self.patch_movement(
            {"expected_revision": 0, "moved_at": paris(2026, 8, 12).isoformat()}, movement=movement, box=unlocated,
        )
        self.assertEqual(response.json()["code"], "movement_period_link_missing")

    def test_period_mismatch_refuses_structural_corrections(self):
        BoxLocation.objects.filter(pk=self.movement.destination_location_id).update(starts_at=self.t1 + timedelta(minutes=5))
        before = self.snapshot()
        for payload in ({"moved_at": paris(2026, 8, 12).isoformat()}, {"to_thermal_zone_id": self.zone_c.pk}):
            response = self.patch_movement({"expected_revision": 0, **payload})
            self.assertEqual((response.status_code, response.json()["code"]), (409, "movement_period_mismatch"))
        self.assert_unchanged(before)

    def test_overlapping_foreign_period_refuses_a_date_correction(self):
        BoxLocation.objects.create(
            box=self.box, thermal_zone=self.zone_c, starts_at=paris(2026, 8, 5), ends_at=paris(2026, 8, 6),
        )
        response = self.patch_movement({"expected_revision": 0, "moved_at": paris(2026, 8, 12).isoformat()})
        self.assertEqual(response.json()["code"], "movement_period_overlap")

    # Destination -------------------------------------------------------------

    def test_terminal_destination_correction_updates_period_pointer_and_audit(self):
        measurement = BiologicalMeasurement.objects.create(
            box=self.box, measured_on=date(2026, 8, 12), polyp_count=0, ephyrae_count=0, strobila_count=0,
        )
        temperature = DailyTemperature.objects.create(
            thermal_zone=self.zone_b, date=date(2026, 8, 12), average_temperature_c=Decimal("15.0"),
            min_temperature_c=Decimal("14.5"), max_temperature_c=Decimal("15.5"), measurement_count=2,
        )
        measurement_values = BiologicalMeasurement.objects.filter(pk=measurement.pk).values().get()
        temperature_values = DailyTemperature.objects.filter(pk=temperature.pk).values().get()

        response = self.patch_movement({"expected_revision": 0, "to_thermal_zone_id": self.zone_c.pk}, user=self.admin)

        self.assertEqual(response.status_code, 200, response.content)
        self.movement.refresh_from_db()
        self.box.refresh_from_db()
        destination = self.movement.destination_location
        self.assertEqual(self.movement.to_thermal_zone, self.zone_c)
        self.assertEqual(destination.thermal_zone, self.zone_c)
        self.assertEqual(destination.starts_at, self.t1)
        self.assertEqual(self.box.thermal_zone, self.zone_c)
        self.assertEqual(response.json()["thermal_zone"]["id"], self.zone_c.pk)
        audit = self.correction_audits().get()
        self.assertEqual(
            audit.metadata["current_thermal_zone_change"],
            {"before": {"id": self.zone_b.pk, "name": "Zone B"}, "after": {"id": self.zone_c.pk, "name": "Zone C"}},
        )
        self.assertEqual(audit.metadata["period_changes"][0]["before"]["thermal_zone_id"], self.zone_b.pk)
        self.assertEqual(audit.metadata["period_changes"][0]["after"]["thermal_zone_id"], self.zone_c.pk)
        self.assertEqual(BiologicalMeasurement.objects.filter(pk=measurement.pk).values().get(), measurement_values)
        self.assertEqual(DailyTemperature.objects.filter(pk=temperature.pk).values().get(), temperature_values)

    def test_destination_correction_of_a_non_terminal_movement_is_rejected(self):
        self.second_movement()
        before = self.snapshot()
        response = self.patch_movement({"expected_revision": 0, "to_thermal_zone_id": self.zone_c.pk})
        self.assertEqual((response.status_code, response.json()["code"]), (409, "movement_not_terminal"))
        self.assert_unchanged(before)

    def test_destination_correction_after_deactivation_is_rejected(self):
        deactivate_box(box=self.box, user=self.admin, reason="Stopped")
        response = self.patch_movement({"expected_revision": 0, "to_thermal_zone_id": self.zone_c.pk})
        self.assertEqual(response.json()["code"], "movement_not_terminal")

    def test_destination_validation(self):
        before = self.snapshot()
        same_as_origin = self.patch_movement({"expected_revision": 0, "to_thermal_zone_id": self.zone_a.pk})
        self.assertEqual((same_as_origin.status_code, same_as_origin.json()["code"]), (400, "movement_same_zone"))
        foreign = self.patch_movement({"expected_revision": 0, "to_thermal_zone_id": self.foreign_zone.pk})
        missing = self.patch_movement({"expected_revision": 0, "to_thermal_zone_id": 999999})
        inactive = self.patch_movement({"expected_revision": 0, "to_thermal_zone_id": self.inactive_zone.pk})
        for response in (foreign, missing, inactive):
            self.assertEqual(response.status_code, 400)
        # A foreign zone is indistinguishable from a missing one.
        self.assertEqual(foreign.json()["to_thermal_zone_id"][0].replace(str(self.foreign_zone.pk), "N"),
                         missing.json()["to_thermal_zone_id"][0].replace("999999", "N"))
        self.assertNotIn("Foreign", foreign.content.decode())
        self.assert_unchanged(before)

    def test_terminal_initial_location_destination_is_correctable(self):
        unlocated = Box.objects.create(
            organization=self.organization, global_code="ACR-LAB-1.003", box_number="003", strain=self.strain,
        )
        self.client.force_login(self.admin)
        self.client.post(
            reverse("api_admin_box_initial_location", args=[unlocated.pk]), data={"thermal_zone_id": self.zone_a.pk},
        )
        initial = unlocated.movements.get()
        response = self.patch_movement(
            {"expected_revision": 0, "to_thermal_zone_id": self.zone_b.pk}, movement=initial, box=unlocated,
        )
        self.assertEqual(response.status_code, 200, response.content)
        unlocated.refresh_from_db()
        self.assertEqual(unlocated.thermal_zone, self.zone_b)
        initial.refresh_from_db()
        self.assertEqual((initial.kind, initial.to_thermal_zone), (BoxMovement.Kind.INITIAL_LOCATION, self.zone_b))

    def test_combined_date_and_destination_correction(self):
        new_time = paris(2026, 8, 12, 8)
        response = self.patch_movement(
            {"expected_revision": 0, "to_thermal_zone_id": self.zone_c.pk, "moved_at": new_time.isoformat()},
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.movement.refresh_from_db()
        destination = self.movement.destination_location
        self.assertEqual((destination.thermal_zone, destination.starts_at), (self.zone_c, new_time))
        self.assertEqual(self.correction_audits().get().metadata["changed_fields"], ["moved_at", "to_thermal_zone"])

    # Stale state -------------------------------------------------------------

    def test_stale_revision_and_a_b_a_changes_are_detected(self):
        self.assertEqual(self.patch_movement({"expected_revision": 0, "notes": "B"}).status_code, 200)
        self.assertEqual(self.patch_movement({"expected_revision": 1, "notes": "Initial note"}).status_code, 200)
        before = self.snapshot()
        response = self.patch_movement({"expected_revision": 0, "notes": "stale form"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json(), {
            "code": "box_movement_changed",
            "detail": response.json()["detail"],
            "movement_id": self.movement.pk,
            "expected_revision": 0,
            "current_revision": 2,
        })
        self.assert_unchanged(before)

    def test_request_validation(self):
        before = self.snapshot()
        missing_revision = self.patch_movement({"notes": "x"})
        self.assertIn("expected_revision", missing_revision.json())
        nothing = self.patch_movement({"expected_revision": 0})
        unknown = self.patch_movement({"expected_revision": 0, "notes": "x", "kind": "reactivation", "user": 1})
        self.assertEqual(set(unknown.json()), {"kind", "user"})
        no_change = self.patch_movement({"expected_revision": 0, "notes": "Initial note"})
        self.assertEqual(no_change.json()["code"], "movement_correction_no_change")
        for response in (missing_revision, nothing, unknown, no_change):
            self.assertEqual(response.status_code, 400)
        self.assert_unchanged(before)

    def test_post_is_not_a_correction_path(self):
        self.client.force_login(self.technician)
        response = self.client.post(
            reverse("api_box_movement_detail", args=[self.box.pk, self.movement.pk]),
            data=json.dumps({"expected_revision": 0, "notes": "x"}), content_type="application/json",
        )
        self.assertEqual(response.status_code, 405)

    # Permissions and scope ---------------------------------------------------

    def test_admin_and_technician_can_correct_without_time_window_or_author_restriction(self):
        old_box = Box.objects.create(
            organization=self.organization, global_code="ACR-LAB-1.004", box_number="004",
            strain=self.strain, thermal_zone=self.zone_a,
        )
        BoxLocation.objects.create(box=old_box, thermal_zone=self.zone_a, starts_at=paris(2023, 1, 2))
        old = move_box_to_thermal_zone(
            box=old_box, thermal_zone=self.zone_b, moved_at=paris(2023, 6, 6), user=self.foreign_admin, notes="",
        )
        self.assertEqual(
            self.patch_movement({"expected_revision": 0, "notes": "tech"}, movement=old, box=old_box).status_code, 200,
        )
        self.assertEqual(
            self.patch_movement({"expected_revision": 1, "moved_at": paris(2023, 6, 7).isoformat()},
                                movement=old, box=old_box, user=self.admin).status_code,
            200,
        )
        old.refresh_from_db()
        self.assertEqual((old.user, old.revision), (self.foreign_admin, 2))
        self.assertEqual(
            list(self.correction_audits().values_list("user__username", flat=True)), ["corr_tech", "corr_admin"],
        )

    def test_read_only_inactive_and_foreign_accounts_are_rejected_without_writes(self):
        before = self.snapshot()
        payload = {"expected_revision": 0, "notes": "intrusion"}
        viewer = self.patch_movement(payload, user=self.viewer)
        self.assertEqual((viewer.status_code, viewer.json()["code"]), (403, "movement_correction_forbidden"))
        self.assertEqual(self.patch_movement(payload, user=self.inactive_member).status_code, 404)
        self.assertEqual(self.patch_movement(payload, user=self.outsider).status_code, 404)
        self.assertEqual(self.patch_movement(payload, user=self.foreign_admin).status_code, 404)
        self.assertEqual(
            self.patch_movement(payload, user=self.foreign_admin, organization=self.organization).status_code, 403,
        )
        self.assertEqual(self.patch_movement(payload, user=self.inactive_member, organization=self.organization).status_code, 403)
        self.client.logout()
        anonymous = self.client.patch(
            reverse("api_box_movement_detail", args=[self.box.pk, self.movement.pk]),
            data=json.dumps(payload), content_type="application/json",
        )
        self.assertIn(anonymous.status_code, {401, 403})
        self.assert_unchanged(before)

    def test_movement_must_belong_to_the_addressed_box(self):
        other_box = Box.objects.create(
            organization=self.organization, global_code="ACR-LAB-1.005", box_number="005",
            strain=self.strain, thermal_zone=self.zone_a,
        )
        response = self.patch_movement({"expected_revision": 0, "notes": "x"}, box=other_box)
        self.assertEqual(response.status_code, 404)

    def test_foreign_box_is_not_found_for_a_local_account(self):
        foreign_strain = Strain.objects.create(
            species=self.strain.species, code="ACR-FOR-1", organization=self.other_organization, number=2,
            origin_code="FOR",
        )
        foreign_box = Box.objects.create(
            organization=self.other_organization, global_code="ACR-FOR-1.001", box_number="001",
            strain=foreign_strain, thermal_zone=self.foreign_zone,
        )
        foreign_movement = BoxMovement.objects.create(box=foreign_box, to_thermal_zone=self.foreign_zone, moved_at=self.t1)
        before = self.snapshot()
        response = self.patch_movement(
            {"expected_revision": 0, "notes": "x"}, box=foreign_box, movement=foreign_movement, user=self.admin,
        )
        self.assertEqual(response.status_code, 404)
        self.assertNotIn("Foreign", response.content.decode())
        self.assert_unchanged(before)

    # Rollback ----------------------------------------------------------------

    def test_audit_failure_rolls_back_every_write(self):
        before = self.snapshot()
        with patch(
            "apps.cultures.movement_corrections.AuditLog.objects.create",
            side_effect=RuntimeError("forced audit failure"),
        ), self.assertRaises(RuntimeError):
            self.patch_movement({
                "expected_revision": 0,
                "moved_at": paris(2026, 8, 12).isoformat(),
                "to_thermal_zone_id": self.zone_c.pk,
                "notes": "rolled back",
            })
        self.assert_unchanged(before)
