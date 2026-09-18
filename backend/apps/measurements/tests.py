"""Tests for measurements: editing an existing measurement (the "Modifier"
action) and the per-box salinity.

Salinity is entered per measurement but rarely changes, so a box must keep
showing the last salinity actually recorded, even when later measurements leave
it blank. That is the behaviour users reported as "the salinity disappears".
"""

import json
from datetime import date, datetime, timedelta, timezone as datetime_timezone
from io import StringIO
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import OrganizationMembership
from apps.audit.models import Alert, AuditLog
from apps.cultures.models import Box, ThermalZone
from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain

from .models import BiologicalMeasurement


class MeasurementEditingApiTests(TestCase):
    def setUp(self):
        user_model = get_user_model()

        self.organization = Organization.objects.create(name="Aquarium de Paris", slug="paris")
        self.other_organization = Organization.objects.create(name="Aquarium de Tokyo", slug="tokyo")

        self.technician = user_model.objects.create_user(username="tech", email="tech@example.org",password="secret")
        OrganizationMembership.objects.create(
            user=self.technician,
            organization=self.organization,
            role=OrganizationMembership.Role.LAB_TECHNICIAN,
        )

        self.admin = user_model.objects.create_user(
            username="admin",
            email="admin@example.org",
            password="secret",
        )
        OrganizationMembership.objects.create(
            user=self.admin,
            organization=self.organization,
            role=OrganizationMembership.Role.ADMIN,
        )

        self.viewer = user_model.objects.create_user(username="viewer", email="viewer@example.org",password="secret")
        OrganizationMembership.objects.create(
            user=self.viewer,
            organization=self.organization,
            role=OrganizationMembership.Role.VIEWER,
        )

        self.species = Species.objects.create(
            scientific_name="Aurelia aurita",
            genus_species_code="AAU",
        )
        self.strain = Strain.objects.create(
            species=self.species,
            code="1-ATL",
            number=1,
            origin_code="ATL",
        )
        self.zone = ThermalZone.objects.create(
            organization=self.organization,
            name="Cabinet-15",
            zone_type=ThermalZone.ZoneType.CABINET,
        )
        self.box = Box.objects.create(
            organization=self.organization,
            global_code="ATL-AAU-1.001",
            box_number="001",
            strain=self.strain,
            thermal_zone=self.zone,
        )
        self.other_box = Box.objects.create(
            organization=self.organization,
            global_code="ATL-AAU-1.002",
            box_number="002",
            strain=self.strain,
            thermal_zone=self.zone,
        )

        self.today = date(2026, 9, 16)

    def patch_measurement(self, box, measurement, payload):
        return self.client.patch(
            reverse("api_box_measurement_detail", args=[box.id, measurement.id]),
            data=json.dumps(payload),
            content_type="application/json",
        )

    # -- creating with a salinity -----------------------------------------

    def test_measurement_can_be_created_with_a_salinity(self):
        self.client.login(username="tech", password="secret")

        response = self.client.post(
            reverse("api_box_measurements", args=[self.box.id]),
            data=json.dumps(
                {
                    "measured_on": self.today.isoformat(),
                    "polyp_count": 12,
                    "ephyrae_count": 3,
                    "salinity_psu": "35.0",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        measurement = BiologicalMeasurement.objects.get(box=self.box)
        self.assertEqual(str(measurement.salinity_psu), "35.00")
        self.assertEqual(response.json()["salinity_psu"], "35.00")

    def test_zero_counts_are_created_as_real_measurements(self):
        self.client.login(username="tech", password="secret")

        response = self.client.post(
            reverse("api_box_measurements", args=[self.box.id]),
            data={
                "measured_on": self.today.isoformat(),
                "polyp_count": 0,
                "ephyrae_count": 0,
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        measurement = BiologicalMeasurement.objects.get(
            box=self.box,
            measured_on=self.today,
        )
        self.assertEqual(measurement.polyp_count, 0)
        self.assertEqual(measurement.ephyrae_count, 0)
        self.assertEqual(response.json()["polyp_count"], 0)
        self.assertEqual(response.json()["ephyrae_count"], 0)

    def test_box_detail_exposes_server_measurement_creation_capability(self):
        detail_url = reverse("api_box_detail", args=[self.box.id])

        self.client.login(username="tech", password="secret")
        self.assertTrue(self.client.get(detail_url).json()["can_create_measurement"])

        self.client.login(username="viewer", password="secret")
        self.assertFalse(self.client.get(detail_url).json()["can_create_measurement"])

        self.box.status = Box.Status.INACTIVE
        self.box.save(update_fields=["status"])
        self.client.login(username="admin", password="secret")
        self.assertFalse(self.client.get(detail_url).json()["can_create_measurement"])

    def test_post_for_existing_date_returns_conflict_without_mutation_or_audit(self):
        self.client.login(username="tech", password="secret")
        url = reverse("api_box_measurements", args=[self.box.id])

        created_response = self.client.post(
            url,
            data={
                "measured_on": self.today.isoformat(),
                "polyp_count": 12,
                "ephyrae_count": 3,
            },
            content_type="application/json",
        )
        conflict_response = self.client.post(
            url,
            data={
                "measured_on": self.today.isoformat(),
                "polyp_count": 0,
                "ephyrae_count": 0,
            },
            content_type="application/json",
        )

        self.assertEqual(created_response.status_code, 201)
        self.assertEqual(conflict_response.status_code, 409)
        self.assertEqual(conflict_response.json()["code"], "measurement_week_conflict")
        self.assertEqual(
            created_response.json()["id"],
            conflict_response.json()["measurement_id"],
        )
        self.assertEqual(
            BiologicalMeasurement.objects.filter(
                box=self.box,
                measured_on=self.today,
            ).count(),
            1,
        )
        measurement = BiologicalMeasurement.objects.get(box=self.box, measured_on=self.today)
        self.assertEqual(measurement.polyp_count, 12)
        self.assertEqual(measurement.ephyrae_count, 3)
        audits = list(
            AuditLog.objects.filter(
                object_type="box",
                metadata__measurement_id=measurement.id,
            ).order_by("created_at", "id")
        )
        self.assertEqual(len(audits), 1)
        self.assertEqual(audits[0].action, AuditLog.Action.ENTRY)
        self.assertEqual(audits[0].metadata["valeurs"]["polypes"], 12)

    def test_measurement_and_alert_roll_back_when_audit_fails(self):
        BiologicalMeasurement.objects.create(
            box=self.box,
            measured_on=self.today - timedelta(days=7),
            polyp_count=80,
            ephyrae_count=2,
        )
        self.client.login(username="tech", password="secret")

        with patch(
            "apps.cultures.api_views._record_measurement_audit",
            side_effect=RuntimeError("forced audit failure"),
        ), self.assertRaises(RuntimeError):
            self.client.post(
                reverse("api_box_measurements", args=[self.box.id]),
                data={
                    "measured_on": self.today.isoformat(),
                    "polyp_count": 60,
                    "ephyrae_count": 0,
                },
                content_type="application/json",
            )

        self.assertFalse(
            BiologicalMeasurement.objects.filter(
                box=self.box,
                measured_on=self.today,
            ).exists()
        )
        self.assertFalse(
            Alert.objects.filter(
                box=self.box,
                alert_type=Alert.AlertType.BIOLOGICAL,
            ).exists()
        )
        self.assertFalse(AuditLog.objects.filter(object_id=self.box.global_code).exists())

    def test_database_rejects_a_second_measurement_for_the_same_box_and_date(self):
        BiologicalMeasurement.objects.create(
            box=self.box,
            measured_on=self.today,
            polyp_count=10,
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            BiologicalMeasurement.objects.create(
                box=self.box,
                measured_on=self.today,
                polyp_count=20,
            )

        self.assertEqual(
            BiologicalMeasurement.objects.filter(
                box=self.box,
                measured_on=self.today,
            ).count(),
            1,
        )

    def test_measurement_cannot_be_created_for_another_organization(self):
        foreign_box = Box.objects.create(
            organization=self.other_organization,
            global_code="TKY-AAU-1.003",
            box_number="003",
            strain=self.strain,
        )
        self.client.login(username="tech", password="secret")

        response = self.client.post(
            reverse("api_box_measurements", args=[foreign_box.id]),
            data={
                "measured_on": self.today.isoformat(),
                "polyp_count": 10,
                "ephyrae_count": 2,
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 404)
        self.assertFalse(BiologicalMeasurement.objects.filter(box=foreign_box).exists())

    # -- editing an existing measurement ----------------------------------

    def test_technician_updates_a_measurement_and_untouched_fields_are_kept(self):
        measurement = BiologicalMeasurement.objects.create(
            box=self.box,
            measured_on=self.today,
            polyp_count=10,
            ephyrae_count=2,
            salinity_psu="35.0",
        )
        self.client.login(username="tech", password="secret")

        response = self.patch_measurement(
            self.box,
            measurement,
            {"polyp_count": 42, "salinity_psu": "33.5"},
        )

        self.assertEqual(response.status_code, 200)
        measurement.refresh_from_db()
        self.assertEqual(measurement.polyp_count, 42)
        self.assertEqual(str(measurement.salinity_psu), "33.50")
        # Partial update: a field that was not sent must not be wiped.
        self.assertEqual(measurement.ephyrae_count, 2)
        self.assertEqual(measurement.user, self.technician)

    def test_measurement_update_and_alert_roll_back_when_audit_fails(self):
        BiologicalMeasurement.objects.create(
            box=self.box,
            measured_on=self.today - timedelta(days=7),
            polyp_count=20,
        )
        measurement = BiologicalMeasurement.objects.create(
            box=self.box,
            measured_on=self.today,
            polyp_count=15,
        )
        self.client.login(username="tech", password="secret")

        with patch(
            "apps.cultures.api_views._record_measurement_audit",
            side_effect=RuntimeError("forced audit failure"),
        ), self.assertRaises(RuntimeError):
            self.patch_measurement(self.box, measurement, {"polyp_count": 5})

        measurement.refresh_from_db()
        self.assertEqual(measurement.polyp_count, 15)
        self.assertFalse(
            Alert.objects.filter(
                box=self.box,
                alert_type=Alert.AlertType.BIOLOGICAL,
            ).exists()
        )
        self.assertFalse(AuditLog.objects.filter(object_id=self.box.global_code).exists())

    def test_read_only_user_cannot_update_a_measurement(self):
        measurement = BiologicalMeasurement.objects.create(
            box=self.box,
            measured_on=self.today,
            polyp_count=10,
        )
        self.client.login(username="viewer", password="secret")

        response = self.patch_measurement(self.box, measurement, {"polyp_count": 99})

        self.assertEqual(response.status_code, 403)
        measurement.refresh_from_db()
        self.assertEqual(measurement.polyp_count, 10)
        self.assertFalse(
            AuditLog.objects.filter(metadata__measurement_id=measurement.id).exists()
        )

    def test_a_measurement_cannot_be_updated_through_another_box(self):
        measurement = BiologicalMeasurement.objects.create(
            box=self.box,
            measured_on=self.today,
            polyp_count=10,
        )
        self.client.login(username="tech", password="secret")

        # The measurement belongs to self.box, not self.other_box.
        response = self.patch_measurement(self.other_box, measurement, {"polyp_count": 99})

        self.assertEqual(response.status_code, 404)
        measurement.refresh_from_db()
        self.assertEqual(measurement.polyp_count, 10)

    def test_updating_a_measurement_of_another_organization_is_refused(self):
        foreign_box = Box.objects.create(
            organization=self.other_organization,
            global_code="TKY-AAU-1.001",
            box_number="001",
            strain=self.strain,
        )
        measurement = BiologicalMeasurement.objects.create(
            box=foreign_box,
            measured_on=self.today,
            polyp_count=10,
        )
        self.client.login(username="tech", password="secret")

        response = self.patch_measurement(foreign_box, measurement, {"polyp_count": 99})

        self.assertEqual(response.status_code, 404)
        measurement.refresh_from_db()
        self.assertEqual(measurement.polyp_count, 10)
        self.assertFalse(
            AuditLog.objects.filter(metadata__measurement_id=measurement.id).exists()
        )

    def test_weekly_duplicate_diagnostic_reports_ids_and_dates_without_mutation(self):
        rows = MagicMock()
        rows.order_by.return_value.iterator.return_value = iter(
            [
                {
                    "id": 11,
                    "box_id": self.box.id,
                    "box__global_code": self.box.global_code,
                    "box__organization_id": self.organization.id,
                    "box__organization__name": self.organization.name,
                    "measured_on": date(2026, 9, 14),
                },
                {
                    "id": 12,
                    "box_id": self.box.id,
                    "box__global_code": self.box.global_code,
                    "box__organization_id": self.organization.id,
                    "box__organization__name": self.organization.name,
                    "measured_on": date(2026, 9, 20),
                },
            ]
        )
        output = StringIO()

        with patch.object(
            BiologicalMeasurement.objects,
            "values",
            return_value=rows,
        ), self.assertRaises(CommandError):
            call_command(
                "check_biological_measurement_duplicates",
                stdout=output,
            )

        report = output.getvalue()
        self.assertIn("iso_week=2026-W38", report)
        self.assertIn("week_start=2026-09-14", report)
        self.assertIn("measurements=11:2026-09-14,12:2026-09-20", report)

    def test_week_start_uses_monday_across_sunday_and_iso_year_boundaries(self):
        cases = {
            date(2026, 9, 14): date(2026, 9, 14),
            date(2026, 9, 16): date(2026, 9, 14),
            date(2026, 9, 20): date(2026, 9, 14),
            date(2026, 9, 21): date(2026, 9, 21),
            date(2020, 12, 31): date(2020, 12, 28),
            date(2021, 1, 3): date(2020, 12, 28),
            date(2021, 1, 4): date(2021, 1, 4),
        }

        for measured_on, expected in cases.items():
            with self.subTest(measured_on=measured_on):
                self.assertEqual(
                    BiologicalMeasurement.week_start_for(measured_on),
                    expected,
                )

    def test_database_rejects_different_dates_in_the_same_week(self):
        first = BiologicalMeasurement.objects.create(
            box=self.box,
            measured_on=date(2026, 9, 14),
            polyp_count=10,
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            BiologicalMeasurement.objects.create(
                box=self.box,
                measured_on=date(2026, 9, 20),
                polyp_count=20,
            )

        self.assertEqual(first.week_start, date(2026, 9, 14))
        self.assertEqual(BiologicalMeasurement.objects.filter(box=self.box).count(), 1)

    def test_same_week_conflict_is_stable_and_zero_occupies_the_slot(self):
        BiologicalMeasurement.objects.create(
            box=self.box,
            measured_on=date(2026, 9, 14),
            polyp_count=0,
            ephyrae_count=0,
        )
        self.client.login(username="admin", password="secret")

        response = self.client.post(
            reverse("api_box_measurements", args=[self.box.id]),
            data={
                "measured_on": "2026-09-20",
                "polyp_count": 5,
                "ephyrae_count": 1,
            },
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "measurement_week_conflict")
        self.assertEqual(response.json()["week_start"], "2026-09-14")
        self.assertEqual(BiologicalMeasurement.objects.filter(box=self.box).count(), 1)
        self.assertFalse(AuditLog.objects.filter(object_id=self.box.global_code).exists())

    def test_next_week_and_separate_boxes_are_independent(self):
        self.client.login(username="tech", password="secret")
        for box, measured_on in (
            (self.box, "2026-09-14"),
            (self.other_box, "2026-09-16"),
            (self.box, "2026-09-21"),
        ):
            response = self.client.post(
                reverse("api_box_measurements", args=[box.id]),
                data={
                    "measured_on": measured_on,
                    "polyp_count": 0,
                    "ephyrae_count": 0,
                },
            )
            self.assertEqual(response.status_code, 201)

        self.assertEqual(BiologicalMeasurement.objects.filter(box=self.box).count(), 2)
        self.assertEqual(BiologicalMeasurement.objects.filter(box=self.other_box).count(), 1)

    def test_viewer_cannot_create_a_measurement(self):
        self.client.login(username="viewer", password="secret")

        response = self.client.post(
            reverse("api_box_measurements", args=[self.box.id]),
            data={
                "measured_on": self.today.isoformat(),
                "polyp_count": 1,
                "ephyrae_count": 0,
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "measurement_write_forbidden")
        self.assertFalse(BiologicalMeasurement.objects.filter(box=self.box).exists())

    def test_technician_edit_window_is_open_only_before_the_exact_deadline(self):
        measurement = BiologicalMeasurement.objects.create(
            box=self.box,
            measured_on=self.today,
            polyp_count=10,
        )
        created_at = datetime(2026, 9, 16, 8, 0, tzinfo=datetime_timezone.utc)
        BiologicalMeasurement.objects.filter(pk=measurement.pk).update(created_at=created_at)
        measurement.refresh_from_db()
        self.client.login(username="tech", password="secret")

        with patch(
            "apps.measurements.services.timezone.now",
            return_value=created_at + timedelta(hours=24) - timedelta(microseconds=1),
        ):
            before_deadline = self.patch_measurement(
                self.box,
                measurement,
                {"polyp_count": 11},
            )
        self.assertEqual(before_deadline.status_code, 200)

        with patch(
            "apps.measurements.services.timezone.now",
            return_value=created_at + timedelta(hours=24),
        ):
            at_deadline = self.patch_measurement(
                self.box,
                measurement,
                {"polyp_count": 12},
            )
        self.assertEqual(at_deadline.status_code, 403)
        self.assertEqual(at_deadline.json()["code"], "edit_window_expired")

        with patch(
            "apps.measurements.services.timezone.now",
            return_value=created_at + timedelta(hours=24, microseconds=1),
        ):
            after_deadline = self.patch_measurement(
                self.box,
                measurement,
                {"polyp_count": 13},
            )
        self.assertEqual(after_deadline.status_code, 403)
        measurement.refresh_from_db()
        self.assertEqual(measurement.polyp_count, 11)
        self.assertEqual(measurement.created_at, created_at)
        audits = AuditLog.objects.filter(metadata__measurement_id=measurement.id)
        self.assertEqual(audits.count(), 1)
        self.assertEqual(audits.get().action, AuditLog.Action.UPDATE)

    def test_repeated_technician_patches_preserve_identity_deadline_and_append_updates(self):
        measurement = BiologicalMeasurement.objects.create(
            box=self.box,
            measured_on=self.today,
            polyp_count=10,
            ephyrae_count=2,
        )
        created_at = datetime(2026, 9, 16, 8, 0, tzinfo=datetime_timezone.utc)
        BiologicalMeasurement.objects.filter(pk=measurement.pk).update(created_at=created_at)
        measurement.refresh_from_db()
        self.client.login(username="tech", password="secret")

        with patch(
            "apps.measurements.services.timezone.now",
            return_value=created_at + timedelta(hours=23),
        ):
            first = self.patch_measurement(
                self.box,
                measurement,
                {"polyp_count": 0, "ephyrae_count": 0},
            )
            second = self.patch_measurement(
                self.box,
                measurement,
                {"polyp_count": 7},
            )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json()["id"], measurement.id)
        self.assertEqual(second.json()["id"], measurement.id)
        measurement.refresh_from_db()
        self.assertEqual(measurement.created_at, created_at)
        self.assertEqual(measurement.polyp_count, 7)
        self.assertEqual(measurement.ephyrae_count, 0)
        events = list(
            AuditLog.objects.filter(metadata__measurement_id=measurement.id).order_by("id")
        )
        self.assertEqual(
            [event.action for event in events],
            [AuditLog.Action.UPDATE, AuditLog.Action.UPDATE],
        )
        self.assertEqual(events[0].metadata["after"]["polypes"], 0)
        self.assertEqual(events[0].metadata["after"]["ephyrules"], 0)
        self.assertEqual(events[1].metadata["before"]["polypes"], 0)
        self.assertEqual(events[1].metadata["after"]["polypes"], 7)

    def test_old_historical_measurement_is_locked_for_technician(self):
        measurement = BiologicalMeasurement.objects.create(
            box=self.box,
            measured_on=date(2020, 1, 1),
            polyp_count=10,
        )
        BiologicalMeasurement.objects.filter(pk=measurement.pk).update(
            created_at=datetime(2020, 1, 1, tzinfo=datetime_timezone.utc)
        )
        self.client.login(username="tech", password="secret")

        response = self.patch_measurement(self.box, measurement, {"polyp_count": 99})

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "edit_window_expired")

    def test_admin_can_correct_old_measurement_on_inactive_box(self):
        self.box.status = Box.Status.INACTIVE
        self.box.save(update_fields=["status"])
        measurement = BiologicalMeasurement.objects.create(
            box=self.box,
            measured_on=date(2020, 1, 1),
            polyp_count=10,
        )
        BiologicalMeasurement.objects.filter(pk=measurement.pk).update(
            created_at=datetime(2020, 1, 1, tzinfo=datetime_timezone.utc)
        )
        self.client.login(username="admin", password="secret")

        response = self.patch_measurement(
            self.box,
            measurement,
            {"polyp_count": 0, "ephyrae_count": 0},
        )

        self.assertEqual(response.status_code, 200)
        measurement.refresh_from_db()
        self.assertEqual(measurement.polyp_count, 0)
        self.assertEqual(measurement.ephyrae_count, 0)
        self.assertEqual(
            AuditLog.objects.filter(metadata__measurement_id=measurement.id).count(),
            1,
        )

    def test_patch_cannot_move_measurement_into_an_occupied_week(self):
        first = BiologicalMeasurement.objects.create(
            box=self.box,
            measured_on=date(2026, 9, 14),
            polyp_count=10,
        )
        second = BiologicalMeasurement.objects.create(
            box=self.box,
            measured_on=date(2026, 9, 21),
            polyp_count=20,
        )
        self.client.login(username="admin", password="secret")

        response = self.patch_measurement(
            self.box,
            second,
            {"measured_on": "2026-09-20"},
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "measurement_week_conflict")
        second.refresh_from_db()
        self.assertEqual(second.measured_on, date(2026, 9, 21))
        self.assertFalse(AuditLog.objects.filter(metadata__measurement_id=second.id).exists())
        self.assertEqual(BiologicalMeasurement.objects.filter(pk=first.pk).count(), 1)

    # -- salinity persistence ----------------------------------------------

    def test_latest_salinity_survives_a_newer_measurement_without_salinity(self):
        """The regression users reported: saving a new measurement without a
        salinity used to make the displayed salinity disappear."""
        BiologicalMeasurement.objects.create(
            box=self.box,
            measured_on=self.today - timedelta(days=7),
            polyp_count=10,
            salinity_psu="35.0",
        )
        BiologicalMeasurement.objects.create(
            box=self.box,
            measured_on=self.today,
            polyp_count=12,
            salinity_psu=None,
        )
        self.client.login(username="tech", password="secret")

        detail = self.client.get(reverse("api_box_detail", args=[self.box.id])).json()

        # The newest measurement carries no salinity...
        self.assertIsNone(detail["latest_measurement"]["salinity_psu"])
        # ...but the box still reports the last salinity actually recorded.
        self.assertEqual(detail["latest_salinity_psu"], "35.00")

    def test_box_list_also_exposes_the_last_recorded_salinity(self):
        BiologicalMeasurement.objects.create(
            box=self.box,
            measured_on=self.today - timedelta(days=7),
            polyp_count=10,
            salinity_psu="35.0",
        )
        BiologicalMeasurement.objects.create(
            box=self.box,
            measured_on=self.today,
            polyp_count=12,
            salinity_psu=None,
        )
        self.client.login(username="tech", password="secret")

        payload = self.client.get(reverse("api_box_list")).json()
        box_payload = next(item for item in payload["results"] if item["id"] == self.box.id)

        self.assertEqual(box_payload["latest_salinity_psu"], "35.00")

    def test_box_without_any_salinity_reports_none(self):
        BiologicalMeasurement.objects.create(
            box=self.box,
            measured_on=self.today,
            polyp_count=12,
        )
        self.client.login(username="tech", password="secret")

        detail = self.client.get(reverse("api_box_detail", args=[self.box.id])).json()

        self.assertIsNone(detail["latest_salinity_psu"])
