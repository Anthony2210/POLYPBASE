"""Salinity creation, correction window, and zone history API tests."""

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.measurements.models import SalinityMeasurement
from apps.organizations.models import Organization

from .models import ThermalZone


class ZoneSalinityLifecycleTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name="Local", slug="local")
        self.other_organization = Organization.objects.create(name="Other", slug="other")
        self.zone = ThermalZone.objects.create(organization=self.organization, name="Local zone")
        self.local_zone = ThermalZone.objects.create(organization=self.organization, name="Second zone")
        self.other_zone = ThermalZone.objects.create(organization=self.other_organization, name="Other zone")
        user_model = get_user_model()
        self.technician = user_model.objects.create_user(username="salinity_tech", email="salinity_tech@example.org", password="secret")
        self.viewer = user_model.objects.create_user(username="salinity_viewer", email="salinity_viewer@example.org", password="secret")
        OrganizationMembership.objects.create(
            user=self.technician, organization=self.organization,
            role=OrganizationMembership.Role.LAB_TECHNICIAN,
        )
        OrganizationMembership.objects.create(
            user=self.viewer, organization=self.organization,
            role=OrganizationMembership.Role.VIEWER,
        )
        self.client.force_login(self.technician)
        self.created_at = datetime(2026, 9, 20, 12, tzinfo=timezone.utc)

    def url(self, name, zone=None, measurement=None):
        args = [zone.pk if zone is not None else self.zone.pk]
        if measurement is not None:
            args.append(measurement.pk)
        return reverse(name, args=args)

    def post(self, data, zone=None, organization=None):
        return self.client.post(
            self.url("api_thermal_zone_manual_salinity", zone),
            data=json.dumps(data), content_type="application/json",
            HTTP_X_ORGANIZATION_ID=str((organization or self.organization).pk),
        )

    def patch_measurement(self, measurement, data, zone=None, organization=None):
        return self.client.patch(
            self.url("api_thermal_zone_salinity_detail", zone, measurement),
            data=json.dumps(data), content_type="application/json",
            HTTP_X_ORGANIZATION_ID=str((organization or self.organization).pk),
        )

    def history(self, zone=None, organization=None):
        return self.client.get(
            self.url("api_thermal_zone_salinity_history", zone),
            HTTP_X_ORGANIZATION_ID=str((organization or self.organization).pk),
        )

    def zones(self):
        return self.client.get(
            reverse("api_thermal_zone_list"),
            HTTP_X_ORGANIZATION_ID=str(self.organization.pk),
        )

    def measurement(self, zone=None, measured_on="2026-09-20", salinity="35.00"):
        row = SalinityMeasurement.objects.create(
            thermal_zone=zone or self.zone, measured_on=measured_on,
            salinity_psu=salinity, notes="Original", user=self.technician,
        )
        SalinityMeasurement.objects.filter(pk=row.pk).update(created_at=self.created_at)
        row.refresh_from_db()
        return row

    def test_post_creates_dated_zero_row_and_rejects_duplicate_date(self):
        with patch("django.utils.timezone.now", return_value=self.created_at):
            response = self.post({"measured_on": "2026-09-20", "salinity_psu": "0.00"})
        self.assertEqual(response.status_code, 201)
        row = SalinityMeasurement.objects.get(thermal_zone=self.zone)
        self.assertEqual(row.salinity_psu, Decimal("0.00"))
        self.assertEqual(row.user, self.technician)
        latest = response.json()["latest_salinity"]
        self.assertEqual(latest["id"], row.pk)
        self.assertEqual(Decimal(str(latest["salinity_psu"])), Decimal("0.00"))
        self.assertTrue(latest["can_edit"])
        self.assertEqual(latest["editable_until"], (row.created_at + timedelta(hours=24)).isoformat().replace("+00:00", "Z"))
        log = AuditLog.objects.get(object_type="salinity_measurement", object_id=str(row.pk))
        self.assertEqual(log.organization, self.organization)
        self.assertEqual(log.metadata["valeurs"]["salinite_psu"], "0.00")
        duplicate = self.post({"measured_on": "2026-09-20", "salinity_psu": "34.00"})
        self.assertEqual(duplicate.status_code, 400)
        self.assertIn("measured_on", duplicate.json())
        self.assertEqual(SalinityMeasurement.objects.filter(thermal_zone=self.zone).count(), 1)
        self.assertEqual(AuditLog.objects.filter(object_type="salinity_measurement").count(), 1)

    def test_later_dated_post_preserves_first_row_and_audits_both_creations(self):
        with patch("django.utils.timezone.now", return_value=self.created_at):
            first_response = self.post({
                "measured_on": "2026-09-19", "salinity_psu": "0.00", "notes": "First reading",
            })
        self.assertEqual(first_response.status_code, 201)
        first = SalinityMeasurement.objects.get(thermal_zone=self.zone, measured_on="2026-09-19")
        self.assertEqual(first_response.json()["latest_salinity"]["id"], first.pk)

        later_at = self.created_at + timedelta(days=3)
        with patch("django.utils.timezone.now", return_value=later_at):
            second_response = self.post({
                "measured_on": "2026-09-22", "salinity_psu": "35.50", "notes": "Later reading",
            })
            history_response = self.history()
            latest = next(
                item for item in self.zones().json()["results"] if item["id"] == self.zone.pk
            )["latest_salinity"]

        self.assertEqual(second_response.status_code, 201)
        self.assertEqual(history_response.status_code, 200)
        first.refresh_from_db()
        second = SalinityMeasurement.objects.get(thermal_zone=self.zone, measured_on="2026-09-22")
        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(SalinityMeasurement.objects.filter(thermal_zone=self.zone).count(), 2)
        self.assertEqual(first.salinity_psu, Decimal("0.00"))
        self.assertEqual(first.notes, "First reading")
        self.assertEqual(first.created_at, self.created_at)
        self.assertEqual(first.user, self.technician)
        self.assertEqual(second.created_at, later_at)
        self.assertEqual(second.user, self.technician)
        self.assertEqual(second_response.json()["latest_salinity"]["id"], second.pk)
        self.assertEqual(latest["id"], second.pk)
        rows = history_response.json()
        self.assertEqual([row["id"] for row in rows], [second.pk, first.pk])
        self.assertEqual([row["notes"] for row in rows], ["Later reading", "First reading"])
        self.assertEqual([row["salinity_psu"] for row in rows], ["35.50", "0.00"])
        self.assertTrue(rows[0]["can_edit"])
        self.assertFalse(rows[1]["can_edit"])

        logs = AuditLog.objects.filter(
            organization=self.organization,
            action=AuditLog.Action.CREATION,
            object_type="salinity_measurement",
        )
        self.assertEqual(logs.count(), 2)
        values_by_row = {log.object_id: log.metadata["valeurs"] for log in logs}
        self.assertEqual(values_by_row, {
            str(first.pk): {"date": "2026-09-19", "salinite_psu": "0.00", "note": "First reading"},
            str(second.pk): {"date": "2026-09-22", "salinite_psu": "35.50", "note": "Later reading"},
        })

    def test_temperature_post_keeps_latest_salinity_editable_until_creation_deadline(self):
        older = self.measurement(measured_on="2026-05-01")
        newest = self.measurement(measured_on="2026-06-01", salinity="0.00")
        deadline = newest.created_at + timedelta(hours=24)
        with patch("django.utils.timezone.now", return_value=deadline - timedelta(microseconds=1)):
            response = self.client.post(
                self.url("api_thermal_zone_manual_temperature"),
                data=json.dumps({"measured_on": "2026-09-21", "temperature_c": "15.00"}),
                content_type="application/json",
                HTTP_X_ORGANIZATION_ID=str(self.organization.pk),
            )
        self.assertEqual(response.status_code, 201)
        latest = response.json()["latest_salinity"]
        self.assertEqual(latest["id"], newest.pk)
        self.assertNotEqual(latest["id"], older.pk)
        self.assertTrue(latest["can_edit"])
        self.assertEqual(latest["editable_until"], deadline.isoformat().replace("+00:00", "Z"))
        self.assertEqual(
            datetime.fromisoformat(latest["created_at"].replace("Z", "+00:00")),
            newest.created_at,
        )
        self.assertEqual(Decimal(str(latest["salinity_psu"])), Decimal("0.00"))
        self.assertFalse(AuditLog.objects.filter(object_type="salinity_measurement").exists())

    def test_patch_corrects_only_target_row_without_changing_author_or_date(self):
        row = self.measurement()
        newer = self.measurement(measured_on="2026-09-21", salinity="36.00")
        with patch("django.utils.timezone.now", return_value=self.created_at + timedelta(hours=23)):
            response = self.patch_measurement(row, {"salinity_psu": "0.00", "notes": "Corrected"})
        self.assertEqual(response.status_code, 200)
        row.refresh_from_db()
        newer.refresh_from_db()
        self.assertEqual(row.salinity_psu, Decimal("0.00"))
        self.assertEqual(row.notes, "Corrected")
        self.assertEqual(row.measured_on.isoformat(), "2026-09-20")
        self.assertEqual(row.user, self.technician)
        self.assertEqual(newer.salinity_psu, Decimal("36.00"))
        self.assertEqual(response.json()["latest_salinity"]["id"], newer.pk)
        log = AuditLog.objects.get(action=AuditLog.Action.UPDATE, object_type="salinity_measurement")
        self.assertEqual(log.organization, self.organization)
        self.assertEqual(log.metadata["before"], {"date": "2026-09-20", "salinite_psu": "35.00", "note": "Original"})
        self.assertEqual(log.metadata["after"], {"date": "2026-09-20", "salinite_psu": "0.00", "note": "Corrected"})
        self.assertEqual(set(log.metadata["modifications"]), {"salinite_psu", "note"})

    def test_no_op_does_not_save_or_audit_and_date_is_immutable(self):
        row = self.measurement()
        with patch("django.utils.timezone.now", return_value=self.created_at + timedelta(hours=23)):
            with patch("apps.cultures.api_views.SalinityMeasurement.save") as save:
                response = self.patch_measurement(row, {"salinity_psu": "35.00", "notes": "Original"})
            invalid = self.patch_measurement(row, {"salinity_psu": "35.01", "measured_on": "2026-09-21"})
        self.assertEqual(response.status_code, 200)
        save.assert_not_called()
        self.assertEqual(invalid.status_code, 400)
        self.assertIn("measured_on", invalid.json())
        self.assertFalse(AuditLog.objects.filter(object_type="salinity_measurement").exists())

    def test_exact_deadline_and_later_are_locked_even_for_no_op(self):
        row = self.measurement(measured_on="2026-05-01")
        with patch("django.utils.timezone.now", return_value=self.created_at + timedelta(hours=24) - timedelta(microseconds=1)):
            self.assertTrue(self.history().json()[0]["can_edit"])
            self.assertEqual(self.patch_measurement(row, {"salinity_psu": "35.00"}).status_code, 200)
        for instant in (self.created_at + timedelta(hours=24), self.created_at + timedelta(hours=25)):
            with self.subTest(instant=instant):
                with patch("django.utils.timezone.now", return_value=instant):
                    response = self.patch_measurement(row, {"salinity_psu": "35.00"})
                    latest = next(item for item in self.zones().json()["results"] if item["id"] == self.zone.pk)["latest_salinity"]
                    history = self.history().json()
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.json()["code"], "salinity_edit_window_expired")
                self.assertFalse(latest["can_edit"])
                self.assertFalse(history[0]["can_edit"])
                self.assertEqual(history[0]["editable_until"], latest["editable_until"])
                self.assertEqual(
                    history[0]["editable_until"],
                    (row.created_at + timedelta(hours=24)).isoformat().replace("+00:00", "Z"),
                )
        row.refresh_from_db()
        self.assertEqual(row.salinity_psu, Decimal("35.00"))
        self.assertEqual(SalinityMeasurement.objects.filter(thermal_zone=self.zone).count(), 1)
        self.assertFalse(AuditLog.objects.filter(object_type="salinity_measurement").exists())

    def test_history_returns_all_rows_by_measurement_date_without_actor(self):
        older = self.measurement(measured_on="2026-09-18", salinity="0.00")
        newer = self.measurement(measured_on="2026-09-21")
        with patch("django.utils.timezone.now", return_value=self.created_at + timedelta(hours=23)):
            response = self.history()
        self.assertEqual(response.status_code, 200)
        rows = response.json()
        self.assertEqual([item["id"] for item in rows], [newer.pk, older.pk])
        self.assertEqual(rows[1]["salinity_psu"], "0.00")
        self.assertEqual(set(rows[0]), {"id", "measured_on", "salinity_psu", "notes", "created_at", "editable_until", "can_edit"})
        self.assertTrue(all(item["can_edit"] for item in rows))
        self.assertEqual(self.history(self.local_zone).json(), [])

    def test_active_organization_and_role_guard_history_post_patch_and_latest(self):
        local = self.measurement()
        foreign = self.measurement(zone=self.other_zone)
        second_zone = self.measurement(zone=self.local_zone, measured_on="2026-09-21")
        self.assertEqual(self.history(self.other_zone).status_code, 404)
        self.assertEqual(self.post({"measured_on": "2026-09-22", "salinity_psu": "34.00"}, self.other_zone).status_code, 404)
        self.assertEqual(self.patch_measurement(foreign, {"salinity_psu": "0.00"}, self.other_zone).status_code, 404)
        self.assertEqual(self.patch_measurement(second_zone, {"salinity_psu": "0.00"}).status_code, 404)
        self.assertEqual(self.patch_measurement(local, {"salinity_psu": "0.00"}, self.other_zone).status_code, 404)
        self.client.force_login(self.viewer)
        with patch("django.utils.timezone.now", return_value=self.created_at + timedelta(hours=1)):
            self.assertFalse(self.history().json()[0]["can_edit"])
            latest = next(item for item in self.zones().json()["results"] if item["id"] == self.zone.pk)["latest_salinity"]
            self.assertFalse(latest["can_edit"])
            self.assertEqual(self.patch_measurement(local, {"salinity_psu": "0.00"}).status_code, 403)
            self.assertEqual(self.post({"measured_on": "2026-09-22", "salinity_psu": "0.00"}).status_code, 403)
        self.assertEqual(self.history(self.other_zone).status_code, 404)
        self.assertEqual(AuditLog.objects.filter(object_type="salinity_measurement").count(), 0)

    def test_membership_in_two_organizations_does_not_bypass_active_context(self):
        OrganizationMembership.objects.create(
            user=self.technician, organization=self.other_organization,
            role=OrganizationMembership.Role.LAB_TECHNICIAN,
        )
        local = self.measurement()
        foreign = self.measurement(zone=self.other_zone)
        with patch("django.utils.timezone.now", return_value=self.created_at + timedelta(hours=1)):
            self.assertEqual(self.history(self.other_zone).status_code, 404)
            self.assertEqual(self.patch_measurement(foreign, {"salinity_psu": "0.00"}, self.other_zone).status_code, 404)
            self.assertEqual(self.post({"measured_on": "2026-09-22", "salinity_psu": "0.00"}, self.other_zone).status_code, 404)
            self.assertEqual(self.history(self.zone, self.other_organization).status_code, 404)
            self.assertEqual(self.patch_measurement(local, {"salinity_psu": "0.00"}, self.zone, self.other_organization).status_code, 404)
            other_history = self.history(self.other_zone, self.other_organization)
        self.assertEqual(other_history.status_code, 200)
        self.assertEqual([item["id"] for item in other_history.json()], [foreign.pk])
        self.assertTrue(other_history.json()[0]["can_edit"])
        self.assertEqual(AuditLog.objects.filter(object_type="salinity_measurement").count(), 0)

    @patch("apps.cultures.api_views.AuditLog.objects.create", side_effect=RuntimeError("Audit unavailable"))
    def test_patch_rolls_back_when_audit_fails(self, _create_audit):
        row = self.measurement()
        with patch("django.utils.timezone.now", return_value=self.created_at + timedelta(hours=1)):
            with self.assertRaises(RuntimeError):
                self.patch_measurement(row, {"salinity_psu": "0.00"})
        row.refresh_from_db()
        self.assertEqual(row.salinity_psu, Decimal("35.00"))
