"""Overview history uses calendar cutoffs and authorized full-history metadata."""

from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.accounts.models import OrganizationMembership
from apps.measurements.models import BiologicalMeasurement, DailyTemperature
from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain

from .models import Box, BoxLocation, ThermalZone
from .serializers import BoxLocationSerializer


@override_settings(SECURE_SSL_REDIRECT=False)
class OverviewHistoryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.organization = Organization.objects.create(name="Overview laboratory")
        cls.foreign_organization = Organization.objects.create(name="Other overview laboratory")
        cls.user = get_user_model().objects.create_user(
            username="overview_history_viewer", email="overview_history_viewer@example.org"
        )
        for organization in (cls.organization, cls.foreign_organization):
            OrganizationMembership.objects.create(
                user=cls.user,
                organization=organization,
                role=OrganizationMembership.Role.VIEWER,
                is_active=True,
            )
        species = Species.objects.create(
            scientific_name="Aurelia overview", genus_species_code="AOV"
        )
        cls.strain = Strain.objects.create(
            species=species, organization=cls.organization, code="AOV-1"
        )
        foreign_strain = Strain.objects.create(
            species=species, organization=cls.foreign_organization, code="AOV-2"
        )
        cls.zone = ThermalZone.objects.create(
            organization=cls.organization,
            name="Overview zone",
            zone_type=ThermalZone.ZoneType.CABINET,
        )
        cls.box = Box.objects.create(
            organization=cls.organization,
            strain=cls.strain,
            global_code="AOV-1.001",
            box_number="001",
            thermal_zone=cls.zone,
        )
        cls.empty_box = Box.objects.create(
            organization=cls.organization,
            strain=cls.strain,
            global_code="AOV-1.002",
            box_number="002",
        )
        cls.foreign_box = Box.objects.create(
            organization=cls.foreign_organization,
            strain=foreign_strain,
            global_code="AOV-2.001",
            box_number="001",
        )

    def setUp(self):
        self.client.force_login(self.user)

    def get_overview(self, today=date(2026, 10, 3), params=None):
        with patch("apps.cultures.api_views.timezone.localdate", return_value=today) as localdate:
            response = self.client.get(
                reverse("api_overview_active_boxes"),
                data=params or {},
                HTTP_X_ORGANIZATION_ID=str(self.organization.pk),
            )
        self.assertEqual(response.status_code, 200)
        localdate.assert_called_once_with()
        return response.json()

    def test_default_three_calendar_months_includes_exact_boundary_and_both_counts(self):
        BiologicalMeasurement.objects.create(
            box=self.box, measured_on=date(2026, 7, 3), polyp_count=0, ephyrae_count=0
        )
        BiologicalMeasurement.objects.create(
            box=self.box, measured_on=date(2026, 7, 10), polyp_count=12, ephyrae_count=7
        )
        BiologicalMeasurement.objects.create(
            box=self.empty_box, measured_on=date(2026, 7, 2), polyp_count=99, ephyrae_count=88
        )
        for measured_on in (date(2026, 7, 2), date(2026, 7, 3)):
            DailyTemperature.objects.create(
                thermal_zone=self.zone, date=measured_on, average_temperature_c=15
            )

        payload = self.get_overview()
        self.assertEqual(payload["months"], 3)
        self.assertEqual(payload["history_start_date"], "2026-07-03")
        self.assertEqual(payload["history_end_date"], "2026-10-03")
        boxes = {box["id"]: box for box in payload["results"]}
        self.assertEqual(
            boxes[self.box.pk]["measurements"],
            [
                {"date": "2026-07-03", "polyp_count": 0, "ephyrae_count": 0, "salinity_psu": None},
                {"date": "2026-07-10", "polyp_count": 12, "ephyrae_count": 7, "salinity_psu": None},
            ],
        )
        self.assertEqual(boxes[self.empty_box.pk]["measurements"], [])
        self.assertEqual(
            boxes[self.box.pk]["temperatures"],
            [{"date": "2026-07-03", "average_temperature_c": 15.0}],
        )
        explicit_payload = self.get_overview(params={"months": 3})
        self.assertEqual(explicit_payload, payload)

    def test_complete_scoped_location_succession_matches_detail_in_initial_window(self):
        previous_zone = ThermalZone.objects.create(
            organization=self.organization,
            name="Previous overview zone",
            zone_type=ThermalZone.ZoneType.CABINET,
        )
        foreign_zone = ThermalZone.objects.create(
            organization=self.foreign_organization,
            name="Foreign overview zone",
            zone_type=ThermalZone.ZoneType.CABINET,
        )
        for measured_on, polyps, ephyrae in (
            (date(2026, 1, 1), 99, 88),
            (date(2026, 7, 3), 0, 0),
            (date(2026, 8, 10), 12, 7),
        ):
            BiologicalMeasurement.objects.create(
                box=self.box, measured_on=measured_on, polyp_count=polyps, ephyrae_count=ephyrae
            )
        # Initialize the active-organization session before comparing query counts.
        self.get_overview()
        with CaptureQueriesContext(connection) as baseline_queries:
            self.get_overview()

        current_start = datetime(2026, 8, 3, 14, 25, tzinfo=timezone.utc)
        cases = (
            ((datetime(2026, 6, 1, 9, 15, tzinfo=timezone.utc), None, True),),
            (
                (datetime(2026, 1, 1, 9, 15, tzinfo=timezone.utc), None, True),
                (
                    datetime(2026, 5, 1, 11, 20, tzinfo=timezone.utc),
                    datetime(2026, 6, 1, 16, 30, tzinfo=timezone.utc),
                    False,
                ),
            ),
        )
        for predecessors in cases:
            with self.subTest(chained=len(predecessors) > 1):
                periods = [
                    (previous_zone, starts_at, ends_at, unknown)
                    for starts_at, ends_at, unknown in predecessors
                ] + [(self.zone, current_start, None, False)]
                locations = [
                    BoxLocation.objects.create(
                        box=self.box,
                        thermal_zone=zone,
                        starts_at=starts_at,
                        ends_at=ends_at,
                        end_date_unknown=unknown,
                        notes="Historical location context",
                    )
                    for zone, starts_at, ends_at, unknown in reversed(periods)
                ][::-1]
                foreign_location = BoxLocation.objects.create(
                    box=self.box,
                    thermal_zone=foreign_zone,
                    starts_at=datetime(2026, 7, 15, 10, 5, tzinfo=timezone.utc),
                )
                with CaptureQueriesContext(connection) as overview_queries:
                    payload = self.get_overview()
                self.assertEqual(len(overview_queries), len(baseline_queries))
                self.assertEqual(payload["history_start_date"], "2026-07-03")
                self.assertEqual(payload["history_end_date"], "2026-10-03")
                box = next(box for box in payload["results"] if box["id"] == self.box.pk)
                expected_locations = [
                    {
                        **period,
                        "thermal_zone": {key: period["thermal_zone"][key] for key in ("id", "name")},
                    }
                    for period in BoxLocationSerializer(locations, many=True).data
                ]
                self.assertEqual(box["locations"], expected_locations)
                self.assertNotIn(foreign_location.pk, [period["id"] for period in box["locations"]])
                self.assertEqual(
                    box["measurements"],
                    [
                        {"date": "2026-07-03", "polyp_count": 0, "ephyrae_count": 0, "salinity_psu": None},
                        {"date": "2026-08-10", "polyp_count": 12, "ephyrae_count": 7, "salinity_psu": None},
                    ],
                )
                response = self.client.get(
                    reverse("api_box_detail", args=[self.box.pk]),
                    HTTP_X_ORGANIZATION_ID=str(self.organization.pk),
                )
                self.assertEqual(response.status_code, 200)
                detail_locations = [
                    {
                        **period,
                        "thermal_zone": {key: period["thermal_zone"][key] for key in ("id", "name")},

                    }
                    for period in reversed(response.json()["locations"])
                ]
                self.assertEqual(box["locations"], detail_locations)
                BoxLocation.objects.filter(box=self.box).delete()

    @override_settings(TIME_ZONE="Europe/Paris")
    def test_overnight_location_dates_match_detail_in_paris(self):
        self.assert_overnight_location_dates_match_detail(
            "2026-07-03T01:30:00+02:00", "2026-08-03T01:45:00+02:00"
        )

    @override_settings(TIME_ZONE="Asia/Tokyo")
    def test_overnight_location_dates_match_detail_in_tokyo(self):
        self.assert_overnight_location_dates_match_detail(
            "2026-07-03T08:30:00+09:00", "2026-08-03T08:45:00+09:00"
        )

    def assert_overnight_location_dates_match_detail(self, expected_start, expected_end):
        previous_zone = ThermalZone.objects.create(
            organization=self.organization,
            name="Overnight previous zone",
            zone_type=ThermalZone.ZoneType.CABINET,
        )
        transition = datetime(2026, 8, 2, 23, 45, tzinfo=timezone.utc)
        previous = BoxLocation.objects.create(
            box=self.box,
            thermal_zone=previous_zone,
            starts_at=datetime(2026, 7, 2, 23, 30, tzinfo=timezone.utc),
            ends_at=transition,
        )
        current = BoxLocation.objects.create(
            box=self.box, thermal_zone=self.zone, starts_at=transition
        )
        payload = self.get_overview()
        self.assertEqual(payload["months"], 3)
        self.assertEqual(payload["history_start_date"], "2026-07-03")
        self.assertEqual(payload["history_end_date"], "2026-10-03")
        overview = next(box for box in payload["results"] if box["id"] == self.box.pk)
        response = self.client.get(
            reverse("api_box_detail", args=[self.box.pk]),
            HTTP_X_ORGANIZATION_ID=str(self.organization.pk),
        )
        self.assertEqual(response.status_code, 200)
        detail = {period["id"]: period for period in response.json()["locations"]}
        self.assertEqual(detail[previous.pk]["starts_at"], expected_start)
        self.assertEqual(detail[previous.pk]["ends_at"], expected_end)
        self.assertEqual(detail[current.pk]["starts_at"], expected_end)
        self.assertEqual(
            overview["locations"],
            [
                {
                    **detail[location.pk],
                    "thermal_zone": {
                        key: detail[location.pk]["thermal_zone"][key] for key in ("id", "name")
                    },
                }
                for location in (previous, current)
            ],
        )
        self.assertEqual(overview["locations"][0]["starts_at"][:10], "2026-07-03")
        self.assertEqual(overview["locations"][0]["ends_at"][:10], "2026-08-03")
        self.assertEqual(overview["locations"][1]["starts_at"][:10], "2026-08-03")
        self.assertIsNone(overview["locations"][1]["ends_at"])

    def test_calendar_cutoff_clamps_month_end_and_crosses_year_boundary(self):
        cases = (
            (date(2026, 5, 31), date(2026, 2, 28)),
            (date(2024, 5, 31), date(2024, 2, 29)),
            (date(2026, 1, 31), date(2025, 10, 31)),
        )
        for today, cutoff in cases:
            with self.subTest(today=today):
                at_boundary = BiologicalMeasurement.objects.create(
                    box=self.box, measured_on=cutoff, polyp_count=0, ephyrae_count=5
                )
                before_boundary = BiologicalMeasurement.objects.create(
                    box=self.empty_box, measured_on=cutoff - timedelta(days=1), polyp_count=6
                )
                payload = self.get_overview(today=today)
                self.assertEqual(payload["history_start_date"], cutoff.isoformat())
                self.assertEqual(payload["history_end_date"], today.isoformat())
                boxes = {box["id"]: box for box in payload["results"]}
                self.assertEqual(boxes[self.box.pk]["measurements"][0]["date"], cutoff.isoformat())
                self.assertEqual(boxes[self.box.pk]["measurements"][0]["polyp_count"], 0)
                self.assertEqual(boxes[self.box.pk]["measurements"][0]["ephyrae_count"], 5)
                self.assertEqual(boxes[self.empty_box.pk]["measurements"], [])
                at_boundary.delete()
                before_boundary.delete()

    def test_earliest_metadata_includes_old_imported_zero_measurement(self):
        BiologicalMeasurement.objects.create(
            box=self.box, measured_on=date(2025, 1, 1), polyp_count=0, ephyrae_count=0
        )
        BiologicalMeasurement.objects.create(
            box=self.box, measured_on=date(2026, 7, 3), polyp_count=2, ephyrae_count=3, user=self.user
        )
        payload = self.get_overview()
        boxes = {box["id"]: box for box in payload["results"]}
        self.assertEqual(boxes[self.box.pk]["earliest_biological_measurement_on"], "2025-01-01")
        self.assertEqual([point["date"] for point in boxes[self.box.pk]["measurements"]], ["2026-07-03"])
        self.assertTrue(boxes[self.box.pk]["tracked_in_app"])
        self.assertIsNone(boxes[self.empty_box.pk]["earliest_biological_measurement_on"])
        self.assertEqual(boxes[self.empty_box.pk]["measurements"], [])

    def test_old_only_box_keeps_earliest_metadata_without_loading_old_history(self):
        BiologicalMeasurement.objects.create(
            box=self.box, measured_on=date(2025, 1, 1), polyp_count=0, ephyrae_count=0
        )
        boxes = {box["id"]: box for box in self.get_overview()["results"]}
        self.assertEqual(boxes[self.box.pk]["earliest_biological_measurement_on"], "2025-01-01")
        self.assertEqual(boxes[self.box.pk]["measurements"], [])
        self.assertFalse(boxes[self.box.pk]["tracked_in_app"])

    def test_foreign_organization_history_is_absent_even_with_membership(self):
        BiologicalMeasurement.objects.create(
            box=self.foreign_box, measured_on=date(2000, 1, 1), polyp_count=88
        )
        BiologicalMeasurement.objects.create(
            box=self.foreign_box, measured_on=date(2026, 7, 3), ephyrae_count=99
        )
        payload = self.get_overview()
        self.assertEqual({box["id"] for box in payload["results"]}, {self.box.pk, self.empty_box.pk})
        self.assertTrue(all(box["earliest_biological_measurement_on"] is None for box in payload["results"]))
        self.assertTrue(all(box["measurements"] == [] for box in payload["results"]))
        self.assertNotIn(self.foreign_box.global_code, str(payload))
        response = self.client.get(
            reverse("api_box_detail", args=[self.foreign_box.pk]),
            HTTP_X_ORGANIZATION_ID=str(self.organization.pk),
        )
        self.assertEqual(response.status_code, 404)

    def test_months_clamp_and_invalid_fallback_are_preserved(self):
        cases = (
            ("0", 1, "2026-09-03"),
            ("-5", 1, "2026-09-03"),
            ("13", 12, "2025-10-03"),
            ("invalid", 3, "2026-07-03"),
            ("", 3, "2026-07-03"),
        )
        for raw_months, months, cutoff in cases:
            with self.subTest(months=raw_months):
                payload = self.get_overview(params={"months": raw_months})
                self.assertEqual(payload["months"], months)
                self.assertEqual(payload["history_start_date"], cutoff)
                self.assertEqual(payload["history_end_date"], "2026-10-03")

    def test_existing_authorized_detail_returns_full_history_on_demand(self):
        BiologicalMeasurement.objects.create(
            box=self.box, measured_on=date(2025, 1, 1), polyp_count=0, ephyrae_count=0
        )
        BiologicalMeasurement.objects.create(
            box=self.box, measured_on=date(2026, 7, 3), polyp_count=2, ephyrae_count=3
        )
        response = self.client.get(
            reverse("api_box_detail", args=[self.box.pk]),
            HTTP_X_ORGANIZATION_ID=str(self.organization.pk),
        )
        self.assertEqual(response.status_code, 200)
        measurements = {point["measured_on"]: point for point in response.json()["biological_measurements"]}
        self.assertEqual(set(measurements), {"2025-01-01", "2026-07-03"})
        self.assertEqual(measurements["2025-01-01"]["polyp_count"], 0)
        self.assertEqual(measurements["2025-01-01"]["ephyrae_count"], 0)
