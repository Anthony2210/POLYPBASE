"""Zone summary query count and data isolation with synthetic fixtures."""

from datetime import datetime, timedelta, timezone as datetime_timezone
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.models import OrganizationMembership
from apps.measurements.models import (
    BiologicalMeasurement,
    DailyTemperature,
    Probe,
    ProbeTemperatureBucket,
    SalinityMeasurement,
    TemperatureMeasurement,
)
from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain

from .models import Box, ThermalZone


NOW = datetime(2026, 11, 25, 12, tzinfo=datetime_timezone.utc)


class ThermalZoneSummaryApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.organization = Organization.objects.create(name="Summary laboratory")
        cls.other_organization = Organization.objects.create(name="Other laboratory")
        cls.user = get_user_model().objects.create_user(
            username="zone-summary", email="zone-summary@example.org",
        )
        for org in (cls.organization, cls.other_organization):
            OrganizationMembership.objects.create(
                user=cls.user, organization=org, role=OrganizationMembership.Role.LAB_TECHNICIAN,
            )
        cls.strain = Strain.objects.create(
            species=Species.objects.create(scientific_name="Summary species", genus_species_code="SSP"),
            code="SUMMARY",
        )

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def zones(self):
        return self.client.get(
            reverse("api_thermal_zone_list"), HTTP_X_ORGANIZATION_ID=str(self.organization.pk),
        )

    def create_zone(self, index, populated):
        zone = ThermalZone.objects.create(organization=self.organization, name=f"Cabinet {index:02}")
        for number in range(3):
            code = f"{index:02}-{number}"
            box = Box.objects.create(
                organization=self.organization, thermal_zone=zone, strain=self.strain,
                global_code=f"SSP-SUMMARY.{code}", box_number=code,
            )
            if number < 2:
                BiologicalMeasurement.objects.create(box=box, measured_on=NOW.date(), polyp_count=0)
            probe = Probe.objects.create(
                organization=self.organization, thermal_zone=zone, code=code,
                probe_type=Probe.ProbeType.IMINILIDE, external_serial=f"logger-{index}", external_channel=number,
            )
            if populated:
                TemperatureMeasurement.objects.create(probe=probe, measured_at=NOW, temperature_c=0)
        if populated:
            for day, value in ((NOW.date() - timedelta(days=1), 12), (NOW.date(), 0)):
                DailyTemperature.objects.create(
                    thermal_zone=zone, date=day, min_temperature_c=value,
                    average_temperature_c=value, max_temperature_c=value, measurement_count=1,
                )
                SalinityMeasurement.objects.create(thermal_zone=zone, measured_on=day, salinity_psu=value)
        return zone

    @patch("django.utils.timezone.now", return_value=NOW)
    def test_query_count_across_zone_volumes(self, _now):
        # The old queryset and serializer issued 176 queries at the 29-zone checkpoint.
        for start, end in ((0, 2), (2, 29), (29, 50)):
            for index in range(start, end):
                self.create_zone(index, populated=index == 0 or 2 <= index <= 13)
            # The only per-zone query left is the shared thermal-control service.
            with self.subTest(zone_count=end), self.assertNumQueries(12 + end):
                response = self.zones()
                self.assertEqual(response.status_code, 200)
                self.assertEqual(len(response.json()["results"]), end)

    @patch("django.utils.timezone.now", return_value=NOW)
    def test_summary_preserves_zero_missing_readings_and_organization_scope(self, _now):
        populated = self.create_zone(0, populated=True)
        empty = self.create_zone(1, populated=False)
        bucket_only = self.create_zone(2, populated=False)
        for zone, last_at in ((populated, NOW + timedelta(seconds=1)), (bucket_only, NOW - timedelta(seconds=1))):
            ProbeTemperatureBucket.objects.create(
                probe=zone.probes.order_by("pk").first(),
                period_start=NOW - timedelta(minutes=15), period_minutes=15,
                last_measured_at=last_at, last_temperature_c=0,
                min_temperature_c=0, max_temperature_c=0, average_temperature_c=0,
                temperature_sum_c=0, measurement_count=1,
            )
        ThermalZone.objects.create(organization=self.other_organization, name="Foreign cabinet")
        # Even an inconsistent legacy relation must not affect the active organization's summary.
        foreign_probe = Probe.objects.create(
            organization=self.other_organization, thermal_zone=empty, code="Foreign probe",
            probe_type=Probe.ProbeType.IMINILIDE, external_serial="foreign", external_channel=0,
        )
        TemperatureMeasurement.objects.create(probe=foreign_probe, measured_at=NOW, temperature_c=33)
        foreign_box = Box.objects.create(
            organization=self.other_organization, thermal_zone=populated, strain=self.strain,
            global_code="SSP-SUMMARY.foreign", box_number="foreign",
        )
        BiologicalMeasurement.objects.create(box=foreign_box, measured_on=NOW.date(), polyp_count=33)
        Box.objects.create(
            organization=self.organization, thermal_zone=populated, strain=self.strain,
            global_code="SSP-SUMMARY.inactive", box_number="inactive", status=Box.Status.INACTIVE,
        )

        response = self.zones()
        self.assertEqual(response.status_code, 200)
        rows = {row["id"]: row for row in response.json()["results"]}
        self.assertEqual(set(rows), {populated.pk, empty.pk, bucket_only.pk})
        zero = rows[populated.pk]
        self.assertEqual(zero["box_count"], 3)
        self.assertEqual(zero["current_polyp_totals"], {"polyp_count": 0, "unknown_box_count": 1})
        self.assertEqual(zero["latest_temperature"]["average_temperature_c"], 0)
        self.assertEqual(zero["latest_salinity"]["salinity_psu"], 0)
        self.assertTrue(zero["latest_salinity"]["can_edit"])
        self.assertEqual(zero["thermal_control"]["temperature_c"], 0)
        self.assertEqual(zero["thermal_control"]["future_probe_count"], 1)
        self.assertEqual(zero["probe_monitoring"]["last_reading_at"], "2026-11-25T12:00:00Z")
        self.assertFalse(zero["probe_monitoring"]["manual_entry_available"])

        missing = rows[empty.pk]
        self.assertEqual(len(missing["probes"]), 3)
        self.assertIsNone(missing["latest_temperature"])
        self.assertIsNone(missing["latest_salinity"])
        self.assertIsNone(missing["thermal_control"]["temperature_c"])
        self.assertEqual(missing["thermal_control"]["no_reading_probe_count"], 3)
        self.assertTrue(missing["probe_monitoring"]["has_collected_probes"])
        self.assertIsNone(missing["probe_monitoring"]["last_reading_at"])
        self.assertTrue(missing["probe_monitoring"]["manual_entry_available"])

        condensed = rows[bucket_only.pk]
        self.assertEqual(condensed["thermal_control"]["temperature_c"], 0)
        self.assertEqual(condensed["probe_monitoring"]["last_reading_at"], "2026-11-25T11:59:59Z")
        self.assertFalse(condensed["probe_monitoring"]["manual_entry_available"])
