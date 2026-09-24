from datetime import date, datetime, timedelta, timezone as datetime_timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import OrganizationMembership
from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain

from .models import Box, BoxLocation, BoxMovement, ThermalZone


class ThermalZoneMovementHistoryApiTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="zone_history_user",
            email="zone-history@example.org",
            password="secret",
        )
        self.organization = Organization.objects.create(name="History laboratory")
        self.other_organization = Organization.objects.create(name="Other history laboratory")
        for organization in (self.organization, self.other_organization):
            OrganizationMembership.objects.create(
                user=self.user,
                organization=organization,
                role=OrganizationMembership.Role.VIEWER,
            )

        species = Species.objects.create(
            scientific_name="History species",
            genus_species_code="HSP",
        )
        self.strain = Strain.objects.create(species=species, code="H-1")
        self.zone = ThermalZone.objects.create(
            organization=self.organization,
            name="Cabinet history A",
        )
        self.second_zone = ThermalZone.objects.create(
            organization=self.organization,
            name="Cabinet history B",
        )
        self.empty_zone = ThermalZone.objects.create(
            organization=self.organization,
            name="Cabinet empty",
        )
        self.other_zone = ThermalZone.objects.create(
            organization=self.other_organization,
            name="Other cabinet",
        )
        self.box = Box.objects.create(
            organization=self.organization,
            global_code="HSP-H-1.001",
            box_number="001",
            strain=self.strain,
            status=Box.Status.INACTIVE,
        )
        self.actor = get_user_model().objects.create_user(
            username="movement_actor",
            email="movement-actor@example.org",
            password="secret",
        )
        self.client.login(username="zone_history_user", password="secret")

    def aware(self, year, month, day, hour):
        return timezone.make_aware(datetime(year, month, day, hour, 0))

    def history(self, zone, params=None, organization=None):
        return self.client.get(
            reverse("api_thermal_zone_history", args=[zone.id]),
            params or {},
            HTTP_X_ORGANIZATION_ID=str((organization or self.organization).id),
        )

    def summary(self, zone, organization=None):
        return self.client.get(
            reverse("api_thermal_zone_history_summary", args=[zone.id]),
            HTTP_X_ORGANIZATION_ID=str((organization or self.organization).id),
        )

    def create_repeated_visit_history(self):
        first_arrival = self.aware(2026, 9, 1, 9)
        first_departure = self.aware(2026, 9, 2, 14)
        second_arrival = self.aware(2026, 9, 3, 11)
        BoxLocation.objects.create(
            box=self.box,
            thermal_zone=self.zone,
            starts_at=first_arrival,
            ends_at=first_departure,
        )
        BoxLocation.objects.create(
            box=self.box,
            thermal_zone=self.second_zone,
            starts_at=first_departure,
            ends_at=second_arrival,
        )
        BoxLocation.objects.create(
            box=self.box,
            thermal_zone=self.zone,
            starts_at=second_arrival,
        )
        BoxMovement.objects.create(
            box=self.box,
            from_thermal_zone=self.zone,
            to_thermal_zone=self.second_zone,
            moved_at=first_departure,
            user=self.actor,
            notes="Internal audit note",
        )
        BoxMovement.objects.create(
            box=self.box,
            from_thermal_zone=self.second_zone,
            to_thermal_zone=self.zone,
            moved_at=second_arrival,
            user=self.actor,
            notes="Another internal audit note",
        )

    def test_history_requires_authentication(self):
        self.client.logout()

        response = self.history(self.zone)

        self.assertIn(response.status_code, {401, 403})

    def test_history_lists_arrivals_and_departures_for_repeated_visits_newest_first(self):
        self.create_repeated_visit_history()

        response = self.history(self.zone)

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["count"], 3)
        self.assertEqual(
            [entry["event_type"] for entry in payload["results"]],
            ["arrival", "departure", "arrival"],
        )
        self.assertEqual(
            [entry["box_id"] for entry in payload["results"]],
            [self.box.id, self.box.id, self.box.id],
        )
        self.assertEqual(payload["results"][0]["related_zone_name"], self.second_zone.name)
        self.assertEqual(payload["results"][1]["related_zone_name"], self.second_zone.name)
        self.assertIsNone(payload["results"][2]["related_zone_name"])

    def test_history_filters_each_direction(self):
        self.create_repeated_visit_history()

        arrivals = self.history(self.zone, {"direction": "arrival"})
        departures = self.history(self.zone, {"direction": "departure"})

        self.assertEqual(arrivals.status_code, 200)
        self.assertEqual(arrivals.json()["count"], 2)
        self.assertEqual(
            [entry["event_type"] for entry in arrivals.json()["results"]],
            ["arrival", "arrival"],
        )
        self.assertEqual(departures.status_code, 200)
        self.assertEqual(departures.json()["count"], 1)
        self.assertEqual(
            [entry["event_type"] for entry in departures.json()["results"]],
            ["departure"],
        )

    def test_history_rejects_invalid_direction(self):
        response = self.history(self.zone, {"direction": "sideways"})

        self.assertEqual(response.status_code, 400)
        self.assertIn("direction", response.json())

    def test_history_keeps_inactive_historical_box_and_excludes_actor_data(self):
        self.create_repeated_visit_history()

        response = self.history(self.zone, {"limit": 1})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["count"], 3)
        self.assertIsNotNone(payload["next"])
        entry = payload["results"][0]
        self.assertEqual(entry["box_status"], Box.Status.INACTIVE)
        self.assertEqual(entry["box_code"], self.box.global_code)
        self.assertNotIn("user", entry)
        self.assertNotIn("actor", entry)
        self.assertNotIn("notes", entry)

    def test_closed_location_without_movement_still_has_a_departure(self):
        BoxLocation.objects.create(
            box=self.box,
            thermal_zone=self.zone,
            starts_at=self.aware(2026, 9, 1, 9),
            ends_at=self.aware(2026, 9, 4, 16),
        )

        response = self.history(self.zone)

        self.assertEqual(response.status_code, 200)
        results = response.json()["results"]
        self.assertEqual([entry["event_type"] for entry in results], ["departure", "arrival"])
        self.assertIsNone(results[0]["related_zone_id"])
        self.assertIsNone(results[0]["related_zone_name"])

    def test_related_zone_enrichment_never_crosses_organizations(self):
        arrived_at = self.aware(2026, 9, 1, 9)
        BoxLocation.objects.create(
            box=self.box,
            thermal_zone=self.zone,
            starts_at=arrived_at,
        )
        BoxMovement.objects.create(
            box=self.box,
            from_thermal_zone=self.other_zone,
            to_thermal_zone=self.zone,
            moved_at=arrived_at,
            user=self.actor,
            notes="Must not leak the foreign zone",
        )

        response = self.history(self.zone)

        self.assertEqual(response.status_code, 200)
        entry = response.json()["results"][0]
        self.assertIsNone(entry["related_zone_id"])
        self.assertIsNone(entry["related_zone_name"])

    def test_history_is_scoped_to_active_organization(self):
        BoxLocation.objects.create(
            box=self.box,
            thermal_zone=self.zone,
            starts_at=self.aware(2026, 9, 1, 9),
        )

        response = self.history(self.zone, organization=self.other_organization)

        self.assertEqual(response.status_code, 404)

    def test_history_empty_state_is_paginated(self):
        response = self.history(self.empty_zone)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            "count": 0,
            "next": None,
            "previous": None,
            "results": [],
        })

    def test_summary_limits_and_orders_arrivals_and_departures_independently(self):
        base = self.aware(2026, 9, 1, 9)
        for index in range(4):
            arrived_at = base + timedelta(days=index * 2)
            BoxLocation.objects.create(
                box=self.box,
                thermal_zone=self.zone,
                starts_at=arrived_at,
                ends_at=arrived_at + timedelta(hours=index + 1),
            )

        with patch("apps.cultures.api_views.timezone.localdate", return_value=date(2026, 9, 8)):
            response = self.summary(self.zone)

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload["recent_arrivals"]), 3)
        self.assertEqual(len(payload["recent_departures"]), 3)
        self.assertEqual(
            [entry["occurred_at"] for entry in payload["recent_arrivals"]],
            sorted(
                [entry["occurred_at"] for entry in payload["recent_arrivals"]],
                reverse=True,
            ),
        )
        self.assertEqual(
            [entry["occurred_at"] for entry in payload["recent_departures"]],
            sorted(
                [entry["occurred_at"] for entry in payload["recent_departures"]],
                reverse=True,
            ),
        )

    def test_summary_handles_one_empty_side_and_current_location_has_no_exit(self):
        BoxLocation.objects.create(
            box=self.box,
            thermal_zone=self.zone,
            starts_at=self.aware(2026, 9, 8, 9),
        )

        with patch("apps.cultures.api_views.timezone.localdate", return_value=date(2026, 9, 8)):
            response = self.summary(self.zone)

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload["recent_arrivals"]), 1)
        self.assertEqual(payload["recent_departures"], [])
        self.assertEqual(payload["weeks"][-1]["entry_count"], 1)
        self.assertEqual(payload["weeks"][-1]["exit_count"], 0)
        self.assertNotIn("actor", payload["recent_arrivals"][0])
        self.assertNotIn("user", payload["recent_arrivals"][0])
        self.assertNotIn("notes", payload["recent_arrivals"][0])

    def test_summary_returns_zero_filled_iso_weeks_across_year_boundary(self):
        BoxLocation.objects.create(
            box=self.box,
            thermal_zone=self.zone,
            starts_at=self.aware(2025, 12, 8, 9),
            ends_at=self.aware(2026, 1, 1, 10),
        )

        with patch("apps.cultures.api_views.timezone.localdate", return_value=date(2026, 1, 1)):
            response = self.summary(self.zone)

        self.assertEqual(response.status_code, 200)
        weeks = response.json()["weeks"]
        self.assertEqual(len(weeks), 8)
        self.assertEqual(weeks[0]["week_start"], "2025-11-10")
        self.assertEqual(weeks[-1]["week_start"], "2025-12-29")
        self.assertEqual(
            [(week["iso_year"], week["iso_week"]) for week in weeks[-2:]],
            [(2025, 52), (2026, 1)],
        )
        self.assertEqual(
            [(week["entry_count"], week["exit_count"]) for week in weeks],
            [(0, 0), (0, 0), (0, 0), (0, 0), (1, 0), (0, 0), (0, 0), (0, 1)],
        )

    def test_summary_assigns_monday_and_sunday_boundaries_without_next_week(self):
        previous_sunday = self.aware(2026, 9, 6, 23) + timedelta(minutes=59)
        monday = self.aware(2026, 9, 7, 0)
        sunday = self.aware(2026, 9, 13, 23) + timedelta(minutes=59)
        next_monday = self.aware(2026, 9, 14, 0)
        for start, end in (
            (previous_sunday, monday),
            (monday, sunday),
            (sunday, next_monday),
            (next_monday, None),
        ):
            BoxLocation.objects.create(
                box=self.box, thermal_zone=self.zone, starts_at=start, ends_at=end,
            )

        with patch("apps.cultures.api_views.timezone.localdate", return_value=date(2026, 9, 13)):
            response = self.summary(self.zone)

        self.assertEqual(response.status_code, 200)
        weeks = {week["week_start"]: week for week in response.json()["weeks"]}
        self.assertEqual(
            (weeks["2026-08-31"]["entry_count"], weeks["2026-08-31"]["exit_count"]),
            (1, 0),
        )
        self.assertEqual(
            (weeks["2026-09-07"]["entry_count"], weeks["2026-09-07"]["exit_count"]),
            (2, 2),
        )
        self.assertNotIn("2026-09-14", weeks)

    def test_summary_counts_each_visit_within_the_same_week(self):
        for day in (7, 9, 13):
            BoxLocation.objects.create(
                box=self.box,
                thermal_zone=self.zone,
                starts_at=self.aware(2026, 9, day, 9),
                ends_at=self.aware(2026, 9, day, 10),
            )

        with patch("apps.cultures.api_views.timezone.localdate", return_value=date(2026, 9, 13)):
            response = self.summary(self.zone)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["weeks"][-1], {
            "week_start": "2026-09-07",
            "iso_year": 2026,
            "iso_week": 37,
            "entry_count": 3,
            "exit_count": 3,
        })

    def test_summary_uses_iso_year_for_early_january_sunday(self):
        BoxLocation.objects.create(
            box=self.box,
            thermal_zone=self.zone,
            starts_at=self.aware(2021, 1, 3, 9),
            ends_at=self.aware(2021, 1, 4, 10),
        )

        with patch("apps.cultures.api_views.timezone.localdate", return_value=date(2021, 1, 4)):
            response = self.summary(self.zone)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["weeks"][-2:],
            [
                {"week_start": "2020-12-28", "iso_year": 2020, "iso_week": 53,
                 "entry_count": 1, "exit_count": 0},
                {"week_start": "2021-01-04", "iso_year": 2021, "iso_week": 1,
                 "entry_count": 0, "exit_count": 1},
            ],
        )

    def test_summary_zero_fills_all_weeks_for_an_empty_zone(self):
        with patch("apps.cultures.api_views.timezone.localdate", return_value=date(2026, 9, 13)):
            response = self.summary(self.empty_zone)

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["recent_arrivals"], [])
        self.assertEqual(payload["recent_departures"], [])
        self.assertEqual(
            [week["week_start"] for week in payload["weeks"]],
            [(date(2026, 7, 20) + timedelta(weeks=index)).isoformat() for index in range(8)],
        )
        self.assertTrue(all(
            week["entry_count"] == week["exit_count"] == 0 for week in payload["weeks"]
        ))

    def test_summary_groups_utc_instants_by_local_week(self):
        for day, hour in ((7, 6), (7, 7), (14, 6), (14, 7)):
            BoxLocation.objects.create(
                box=self.box,
                thermal_zone=self.zone,
                starts_at=datetime(2026, 9, day, hour, tzinfo=datetime_timezone.utc),
            )

        with timezone.override(ZoneInfo("America/Los_Angeles")):
            with patch("apps.cultures.api_views.timezone.localdate", return_value=date(2026, 9, 13)):
                response = self.summary(self.zone)

        self.assertEqual(response.status_code, 200)
        weeks = response.json()["weeks"]
        self.assertEqual(weeks[-2]["week_start"], "2026-08-31")
        self.assertEqual(weeks[-2]["entry_count"], 1)
        self.assertEqual(weeks[-1]["week_start"], "2026-09-07")
        self.assertEqual(weeks[-1]["entry_count"], 2)
        self.assertEqual(sum(week["entry_count"] for week in weeks), 3)
        self.assertTrue(all(week["exit_count"] == 0 for week in weeks))

    def test_summary_excludes_foreign_boxes_and_requires_the_active_organization(self):
        foreign_box = Box.objects.create(
            organization=self.other_organization,
            global_code="HSP-H-1.999",
            box_number="999",
            strain=self.strain,
            status=Box.Status.INACTIVE,
        )
        BoxLocation.objects.create(
            box=foreign_box,
            thermal_zone=self.zone,
            starts_at=self.aware(2026, 9, 8, 9),
        )

        with patch("apps.cultures.api_views.timezone.localdate", return_value=date(2026, 9, 8)):
            allowed_response = self.summary(self.zone)
        denied_response = self.summary(self.zone, organization=self.other_organization)

        self.assertEqual(allowed_response.status_code, 200)
        self.assertEqual(allowed_response.json()["recent_arrivals"], [])
        self.assertTrue(
            all(week["entry_count"] == 0 for week in allowed_response.json()["weeks"])
        )
        self.assertEqual(denied_response.status_code, 404)
