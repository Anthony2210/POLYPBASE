"""Confirmed week-precision temperatures are read through the active organization."""

from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.db import models
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.urls import reverse

from apps.accounts.models import OrganizationMembership
from apps.measurements.models import (
    BiologicalMeasurement, HistoricalTemperatureEvidence, HistoricalTemperatureReading,
)
from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain

from .models import Box


@override_settings(SECURE_SSL_REDIRECT=False)
class BoxHistoricalTemperatureAPITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.organization = Organization.objects.create(name="Historical A")
        cls.other = Organization.objects.create(name="Historical B")
        cls.user = get_user_model().objects.create_user(
            username="historical-viewer", email="historical-viewer@example.org",
        )
        OrganizationMembership.objects.create(
            user=cls.user, organization=cls.organization,
            role=OrganizationMembership.Role.VIEWER,
        )
        species = Species.objects.create(scientific_name="Historical species", genus_species_code="HST")
        strain = Strain.objects.create(species=species, organization=cls.organization, code="HST-A")
        other_strain = Strain.objects.create(species=species, organization=cls.other, code="HST-B")
        cls.box = Box.objects.create(
            organization=cls.organization, strain=strain, global_code="HST-ABC-1.001", box_number="001",
        )
        cls.second_box = Box.objects.create(
            organization=cls.organization, strain=strain, global_code="HST-ABC-1.002", box_number="002",
        )
        cls.other_box = Box.objects.create(
            organization=cls.other, strain=other_strain, global_code="HST-ABC-2.001", box_number="001",
        )

    def setUp(self):
        self.client.force_login(self.user)

    def reading(self, box, day, temperature, status="confirmed", counts=(1, 0)):
        measurement = BiologicalMeasurement.objects.create(
            box=box, measured_on=day, polyp_count=counts[0], ephyrae_count=counts[1],
            user=self.user,
        )
        iso = day.isocalendar()
        saved = HistoricalTemperatureReading(
            biological_measurement=measurement, iso_year=iso.year, iso_week=iso.week,
            temperature_c=temperature,
        )
        saved.save()
        HistoricalTemperatureEvidence(
            reading=saved, source_file="synthetic.xlsx", source_sha256="a" * 64, sheet="S",
            temperature_cell="C3", temperature_merged_range="C3:C4", source_value=temperature,
            source_box=box.global_code, polyp_cell="F3",
            polyp_value=str(counts[0]), ephyrae_cell="F4", ephyrae_value=str(counts[1]),
            week_cell="F1", week_value=str(iso.week), source_iso_year=iso.year, source_iso_week=iso.week,
        ).save()
        if status != "confirmed":
            models.QuerySet(model=HistoricalTemperatureReading).filter(pk=saved.pk).update(
                association_status=status,
            )
        return saved

    def get(self, box=None, start="2026-01-01", end="2026-03-31", org=None, **params):
        return self.client.get(
            reverse("api_box_historical_temperatures", args=[(box or self.box).pk]),
            {"start": start, "end": end, **params},
            HTTP_X_ORGANIZATION_ID=str((org or self.organization).pk),
        )

    def test_returns_week_points_without_inventing_a_day_and_keeps_zero_exact(self):
        self.reading(self.box, date(2026, 1, 7), "0")
        self.reading(self.box, date(2026, 1, 14), "15.250")
        response = self.get()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 2)
        zero, warm = response.json()["results"]
        self.assertEqual(zero["temperature_c"], "0")
        self.assertEqual(warm["temperature_c"], "15.250")
        self.assertIsNone(zero["measured_on"])
        self.assertIsNone(zero["measured_at"])
        self.assertIsNone(zero["probe_id"])
        self.assertIsNone(zero["thermal_zone_id"])
        self.assertEqual(zero["temporal_precision"], "iso_week")
        self.assertEqual((zero["interval_start"], zero["interval_end"]), ("2026-01-05", "2026-01-11"))
        self.assertEqual(zero["source_kind"], "historical_weekly")
        self.assertEqual(zero["evidence_count"], 1)
        self.assertEqual(zero["box_id"], self.box.pk)

    def test_range_uses_week_overlap_and_other_boxes_are_excluded(self):
        self.reading(self.box, date(2026, 1, 7), "14")
        self.reading(self.box, date(2026, 2, 4), "16")
        self.reading(self.second_box, date(2026, 1, 7), "99")
        # Wednesday-start range still includes that Monday-based week.
        response = self.get(start="2026-01-07", end="2026-01-07")
        self.assertEqual([r["temperature_c"] for r in response.json()["results"]], ["14"])
        response = self.get(start="2026-01-12", end="2026-01-18")
        self.assertEqual(response.json()["results"], [])

    def test_pending_review_associations_are_not_a_confirmed_series_point(self):
        self.reading(self.box, date(2026, 1, 7), "14")
        self.reading(self.box, date(2026, 1, 14), "17", status="pending_review")
        self.assertEqual([r["temperature_c"] for r in self.get().json()["results"]], ["14"])

    def test_requires_authentication_and_active_organization_scope(self):
        self.reading(self.other_box, date(2026, 1, 7), "21")
        self.client.logout()
        self.assertIn(self.get().status_code, (401, 403))
        self.client.force_login(self.user)
        # A Box from another institution is not found, not merely empty.
        self.assertEqual(self.get(box=self.other_box).status_code, 404)
        # A member cannot read through an organization they do not belong to.
        self.assertIn(self.get(box=self.other_box, org=self.other).status_code, (400, 403, 404))
        stranger = get_user_model().objects.create_user(
            username="no-member", email="no-member@example.org",
        )
        self.client.force_login(stranger)
        self.assertIn(self.get().status_code, (400, 403, 404))

    def test_validates_range_and_bounds_queries(self):
        for params in ({"start": "2026-02-01", "end": "2026-01-01"}, {"start": "bad", "end": "2026-01-01"},
                       {"start": "2026-01-01", "end": "2027-01-02"}):
            self.assertEqual(self.get(**params).status_code, 400)
        for index in range(5):
            self.reading(self.box, date(2026, 1, 5) + timedelta(weeks=index), str(index))
        with CaptureQueriesContext(connection) as queries:
            self.assertEqual(self.get().json()["count"], 5)
        small = len(queries)
        for index in range(5, 9):
            self.reading(self.box, date(2026, 1, 5) + timedelta(weeks=index), str(index))
        with CaptureQueriesContext(connection) as queries:
            self.assertEqual(self.get().json()["count"], 9)
        self.assertEqual(len(queries), small)
