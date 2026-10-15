"""Bounded, organization-scoped temperatures follow recorded box occupancy."""

from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.accounts.models import OrganizationMembership
from apps.measurements.models import DailyTemperature
from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain

from .models import Box, BoxLocation, ThermalZone
from .serializers import BoxDetailSerializer
from .temperature_history import box_temperature_queryset


@override_settings(SECURE_SSL_REDIRECT=False)
class BoxTemperatureHistoryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.organization = Organization.objects.create(name="Temperature A")
        cls.other = Organization.objects.create(name="Temperature B")
        cls.user = get_user_model().objects.create_user(
                    username="temperature-viewer", email="temperature-viewer@example.org",
                )
        for organization in (cls.organization, cls.other):
            OrganizationMembership.objects.create(
                user=cls.user, organization=organization,
                role=OrganizationMembership.Role.VIEWER,
            )
        species = Species.objects.create(scientific_name="Temperature species", genus_species_code="TMP")
        strain = Strain.objects.create(species=species, organization=cls.organization, code="TMP-A")
        cls.box = Box.objects.create(
            organization=cls.organization, strain=strain, global_code="TMP-A.001", box_number="001",
        )
        cls.other_box = Box.objects.create(
            organization=cls.other, strain=strain, global_code="TMP-A.002", box_number="002",
        )
        cls.zone = ThermalZone.objects.create(organization=cls.organization, name="Previous zone")
        cls.next_zone = ThermalZone.objects.create(organization=cls.organization, name="Current zone")
        cls.foreign_zone = ThermalZone.objects.create(organization=cls.other, name="Private zone")
        cls.today = date(2026, 10, 15)

    def setUp(self):
        self.client.force_login(self.user)
        self.clock = patch("django.utils.timezone.localdate", return_value=self.today)
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def location(self, zone, start, end=None, unknown=False):
        return BoxLocation.objects.create(
            box=self.box, thermal_zone=zone,
            starts_at=datetime.fromisoformat(start).replace(tzinfo=timezone.utc),
            ends_at=datetime.fromisoformat(end).replace(tzinfo=timezone.utc) if end else None,
            end_date_unknown=unknown,
        )

    def reading(self, zone, day, average="0.00", minimum=None, maximum="0.00"):
        return DailyTemperature.objects.create(
            thermal_zone=zone, date=day, average_temperature_c=average,
            min_temperature_c=minimum, max_temperature_c=maximum,
        )

    def get(self, start="2024-01-01", end="2024-01-31", box=None, **params):
        return self.client.get(
            reverse("api_box_temperatures", args=[(box or self.box).pk]),
            {"start": start, "end": end, **params},
            HTTP_X_ORGANIZATION_ID=str(self.organization.pk),
        )

    def test_historical_inclusive_boundaries_zero_null_and_previous_zone(self):
        self.location(self.zone, "2024-01-02T23:30:00", "2024-01-04T00:10:00")
        self.box.thermal_zone = self.next_zone
        self.box.save(update_fields=["thermal_zone"])
        for day in range(1, 6):
            self.reading(self.zone, date(2024, 1, day))
            self.reading(self.next_zone, date(2024, 1, day))
        response = self.get("2024-01-02", "2024-01-04")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["count"], 3)
        self.assertEqual([p["date"] for p in data["results"]], ["2024-01-02", "2024-01-03", "2024-01-04"])
        self.assertEqual(data["results"][0], {
            "date": "2024-01-02", "average_temperature_c": 0.0,
            "min_temperature_c": None, "max_temperature_c": 0.0,
            "zone_id": self.zone.pk, "zone_name": self.zone.name,
        })
        self.assertEqual(self.get("2024-01-03", "2024-01-03").json()["count"], 1)

    def test_same_day_movement_both_zones_and_revisits_deduplicated(self):
        self.location(self.zone, "2024-01-01T12:00:00", "2024-01-03T12:00:00")
        self.location(self.next_zone, "2024-01-03T12:00:00", "2024-01-03T14:00:00")
        self.location(self.zone, "2024-01-03T14:00:00", "2024-01-04T12:00:00")
        for zone in (self.zone, self.next_zone):
            self.reading(zone, date(2024, 1, 3))
        data = self.get().json()
        self.assertEqual(data["count"], 2)
        self.assertEqual([p["zone_id"] for p in data["results"]], sorted([self.zone.pk, self.next_zone.pk]))

    def test_unknown_end_only_proves_start_day(self):
        self.location(self.zone, "2024-01-02T12:00:00", unknown=True)
        for day in (1, 2, 3, 20):
            self.reading(self.zone, date(2024, 1, day))
        self.assertEqual([p["date"] for p in self.get().json()["results"]], ["2024-01-02"])
        self.assertEqual(self.get("2024-01-03", "2024-01-31").json()["count"], 0)

    def test_empty_without_occupancy_even_with_current_zone(self):
        self.box.thermal_zone = self.zone
        self.box.save(update_fields=["thermal_zone"])
        self.reading(self.zone, date(2024, 1, 1))
        self.assertEqual(self.get().json(), {"count": 0, "next": None, "previous": None, "results": []})

    def test_detail_default_window_and_open_end_stop_today(self):
        self.location(self.zone, "2024-01-01T00:00:00")
        cutoff = self.today - timedelta(days=180)
        for day in (date(2024, 1, 1), cutoff - timedelta(days=1), cutoff, self.today, self.today + timedelta(days=1)):
            self.reading(self.zone, day)
        with self.assertNumQueries(1):
            points = BoxDetailSerializer().get_temperature_history(self.box)
        self.assertEqual([p["date"] for p in points], [cutoff, self.today])
        response = self.get(self.today.isoformat(), (self.today + timedelta(days=1)).isoformat())
        self.assertEqual(response.json()["count"], 1)

    def test_invalid_and_missing_bounds(self):
        for params in (
            {}, {"start": "2024-01-01"}, {"end": "2024-01-01"},
            {"start": "2024-02-30", "end": "2024-03-01"},
            {"start": "2024-1-01", "end": "2024-01-31"},
            {"start": "2024-01-01T00:00:00Z", "end": "2024-01-31"},
            {"start": "2024-02-01", "end": "2024-01-01"},
            {"start": "2024-01-01", "end": "2025-01-01"},
        ):
            with self.subTest(params=params):
                response = self.client.get(reverse("api_box_temperatures", args=[self.box.pk]), params,
                                           HTTP_X_ORGANIZATION_ID=str(self.organization.pk))
                self.assertEqual(response.status_code, 400)
        self.assertEqual(self.get("2024-01-01", "2024-12-31").status_code, 200)

    def test_authentication_context_and_membership_required(self):
        url = reverse("api_box_temperatures", args=[self.box.pk])
        self.assertEqual(self.client.get(url, {"start": "2024-01-01", "end": "2024-01-31"}).status_code, 403)
        self.client.logout()
        self.assertEqual(self.get().status_code, 403)
        self.client.force_login(self.user)
        OrganizationMembership.objects.filter(user=self.user, organization=self.organization).update(is_active=False)
        self.assertEqual(self.get().status_code, 403)

    def test_active_organization_and_foreign_location_isolation(self):
        self.assertEqual(self.get(box=self.other_box).status_code, 404)
        self.location(self.foreign_zone, "2024-01-01T00:00:00")
        self.reading(self.foreign_zone, date(2024, 1, 2))
        self.assertEqual(self.get().json()["count"], 0)
        self.assertEqual(BoxDetailSerializer().get_temperature_history(self.box), [])
        response = self.client.get(reverse("api_box_temperatures", args=[self.box.pk]),
                                   HTTP_X_ORGANIZATION_ID="invalid")
        self.assertEqual(response.status_code, 403)

    def test_pagination_bounds_rows_and_stable_order(self):
        self.location(self.zone, "2024-01-01T00:00:00")
        self.location(self.next_zone, "2024-01-01T00:00:00")
        for day in range(366):
            for zone in (self.zone, self.next_zone):
                self.reading(zone, date(2024, 1, 1) + timedelta(days=day))
        data = self.get("2024-01-01", "2024-12-31").json()
        self.assertEqual(data["count"], 732)
        self.assertEqual(len(data["results"]), 100)
        self.assertIsNotNone(data["next"])
        maximum = self.get("2024-01-01", "2024-12-31", limit=9999).json()
        self.assertEqual(len(maximum["results"]), 500)
        first = self.get(limit=1).json()
        second = self.get(limit=1, offset=1).json()
        self.assertEqual(first["results"][0]["date"], second["results"][0]["date"])
        self.assertNotEqual(first["results"][0]["zone_id"], second["results"][0]["zone_id"])
        self.assertIsNotNone(second["previous"])
        self.assertEqual(self.get(offset=9999).json()["results"], [])

    def test_query_counts_do_not_grow_with_location_count(self):
        self.location(self.zone, "2024-01-01T00:00:00")
        self.reading(self.zone, date(2024, 1, 2))
        self.get()  # Warm session initialization before comparing request queries.
        with CaptureQueriesContext(connection) as before:
            self.assertEqual(self.get().status_code, 200)
        for day in range(2, 25):
            self.location(self.zone, f"2024-01-{day:02d}T00:00:00")
        with CaptureQueriesContext(connection) as after:
            self.assertEqual(self.get().status_code, 200)
        self.assertEqual(len(before), len(after))
        with self.assertNumQueries(1):
            self.assertEqual(len(list(box_temperature_queryset(self.box, date(2024, 1, 1), date(2024, 1, 31)))), 1)
        with self.assertNumQueries(1):
            BoxDetailSerializer().get_temperature_history(self.box)
