from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.measurements.models import BiologicalMeasurement
from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain

from .models import Box, BoxLocation, BoxMovement, ThermalZone


class AdminBoxInventoryApiTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.admin = user_model.objects.create_user(
            username="inventory_admin",
            password="secret",
        )
        self.technician = user_model.objects.create_user(
            username="inventory_technician",
            password="secret",
        )
        self.organization = Organization.objects.create(name="Inventory laboratory")
        self.other_organization = Organization.objects.create(name="Other inventory laboratory")
        OrganizationMembership.objects.create(
            user=self.admin,
            organization=self.organization,
            role=OrganizationMembership.Role.ADMIN,
        )
        OrganizationMembership.objects.create(
            user=self.technician,
            organization=self.organization,
            role=OrganizationMembership.Role.LAB_TECHNICIAN,
        )
        self.species = Species.objects.create(
            scientific_name="Aurelia inventory",
            genus_species_code="AIN",
        )
        self.strain = Strain.objects.create(
            species=self.species,
            code="AIN-LAB-1",
            number=1,
            origin_code="LAB",
        )
        self.zone = ThermalZone.objects.create(
            organization=self.organization,
            name="Zone 15.0°C",
            target_temperature_c=15,
        )
        self.second_zone = ThermalZone.objects.create(
            organization=self.organization,
            name="Zone 20.0°C",
            target_temperature_c=20,
        )
        self.foreign_zone = ThermalZone.objects.create(
            organization=self.other_organization,
            name="Foreign zone",
            target_temperature_c=10,
        )

    def create_box(self, number, *, status=Box.Status.ACTIVE, zone=None, organization=None):
        organization = organization or self.organization
        return Box.objects.create(
            organization=organization,
            global_code=f"AIN-LAB-1.{number:03d}",
            box_number=f"{number:03d}",
            strain=self.strain,
            status=status,
            thermal_zone=zone,
        )

    def login_admin(self):
        self.client.login(username=self.admin.username, password="secret")

    def test_inventory_is_admin_only_scoped_and_paginated(self):
        for number in range(1, 27):
            self.create_box(number, status=Box.Status.PENDING_REVIEW)
        self.create_box(
            99,
            status=Box.Status.PENDING_REVIEW,
            organization=self.other_organization,
        )

        self.login_admin()
        response = self.client.get(
            reverse("api_admin_box_inventory"),
            {"limit": 10, "offset": 10},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 26)
        self.assertEqual(len(response.data["results"]), 10)
        self.assertNotIn(
            "AIN-LAB-1.099",
            {item["global_code"] for item in response.data["results"]},
        )

        self.client.logout()
        self.client.login(username=self.technician.username, password="secret")
        forbidden = self.client.get(reverse("api_admin_box_inventory"))
        self.assertEqual(forbidden.status_code, 403)

    def test_status_and_location_filters_are_independent_and_combinable(self):
        pending = self.create_box(1, status=Box.Status.PENDING_REVIEW)
        active_without_location = self.create_box(2, status=Box.Status.ACTIVE)
        active_in_zone = self.create_box(3, status=Box.Status.ACTIVE, zone=self.zone)
        inactive = self.create_box(4, status=Box.Status.INACTIVE)
        self.login_admin()

        pending_response = self.client.get(
            reverse("api_admin_box_inventory"),
            {"status": Box.Status.PENDING_REVIEW},
        )
        self.assertEqual(
            [item["id"] for item in pending_response.data["results"]],
            [pending.id],
        )

        active_without_location_response = self.client.get(
            reverse("api_admin_box_inventory"),
            {"status": Box.Status.ACTIVE, "location": "none"},
        )
        self.assertEqual(
            [item["id"] for item in active_without_location_response.data["results"]],
            [active_without_location.id],
        )

        zone_response = self.client.get(
            reverse("api_admin_box_inventory"),
            {"location": str(self.zone.id)},
        )
        self.assertEqual(
            [item["id"] for item in zone_response.data["results"]],
            [active_in_zone.id],
        )
        self.assertNotIn(inactive.id, [item["id"] for item in zone_response.data["results"]])

    def test_summary_counts_are_institution_wide_not_filtered_or_paginated(self):
        self.create_box(1, status=Box.Status.PENDING_REVIEW)
        self.create_box(2, status=Box.Status.PENDING_REVIEW, zone=self.zone)
        self.create_box(3, status=Box.Status.ACTIVE)
        self.create_box(4, status=Box.Status.ACTIVE, zone=self.zone)
        self.create_box(5, status=Box.Status.INACTIVE)
        self.create_box(99, status=Box.Status.PENDING_REVIEW, organization=self.other_organization)
        # Even an administrator of both institutions must see only the active one.
        OrganizationMembership.objects.create(
            user=self.admin, organization=self.other_organization,
            role=OrganizationMembership.Role.ADMIN,
        )
        self.login_admin()
        for filters in ({}, {"limit": 1, "offset": 1}, {"q": "absent"},
                        {"status": "active", "location": "none"}):
            with self.subTest(filters=filters):
                response = self.client.get(
                    reverse("api_admin_box_inventory"), filters,
                    HTTP_X_ORGANIZATION_ID=str(self.organization.id),
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.data["summary"], {
                    "pending_review_count": 2,
                    "active_without_location_count": 1,
                    "pending_without_location_count": 1,
                })

    def test_summary_keeps_zero_counts_for_empty_inventory(self):
        self.login_admin()
        response = self.client.get(reverse("api_admin_box_inventory"))
        self.assertEqual(response.data["summary"], {
            "pending_review_count": 0,
            "active_without_location_count": 0,
            "pending_without_location_count": 0,
        })

    def test_summary_refreshes_after_individual_and_batch_qualification(self):
        first = self.create_box(1, status=Box.Status.PENDING_REVIEW)
        second = self.create_box(2, status=Box.Status.PENDING_REVIEW)
        self.login_admin()
        individual = self.client.post(
            reverse("api_box_qualify", args=[first.id]),
            data={"target_status": "active", "thermal_zone_id": None},
            content_type="application/json",
        )
        self.assertEqual(individual.status_code, 200)
        batch = self.client.post(
            reverse("api_admin_box_inventory_batch_qualify"),
            data={"box_ids": [second.id], "target_status": "inactive", "reason_missing_from_history": True},
            content_type="application/json",
        )
        self.assertEqual(batch.data["success_count"], 1)
        summary = self.client.get(reverse("api_admin_box_inventory")).data["summary"]
        self.assertEqual(summary, {
            "pending_review_count": 0,
            "active_without_location_count": 1,
            "pending_without_location_count": 0,
        })

    def test_inventory_preserves_zero_and_distinguishes_missing_measurements(self):
        zero_box = self.create_box(1, zone=self.zone)
        empty_box = self.create_box(2, zone=self.zone)
        BiologicalMeasurement.objects.create(
            box=zero_box,
            measured_on=date(2026, 8, 27),
            polyp_count=0,
            ephyrae_count=0,
        )
        self.login_admin()

        response = self.client.get(reverse("api_admin_box_inventory"))
        results = {item["id"]: item for item in response.data["results"]}

        self.assertEqual(results[zero_box.id]["latest_measurement"]["polyp_count"], 0)
        self.assertEqual(results[zero_box.id]["latest_measurement"]["ephyrae_count"], 0)
        self.assertIsNone(results[empty_box.id]["latest_measurement"])

    def test_inventory_uses_first_measurement_as_displayed_creation_date_for_imported_boxes(self):
        imported = self.create_box(1)
        imported_without_measurement = self.create_box(2)
        regular = self.create_box(3)
        Box.objects.filter(id__in=[imported.id, imported_without_measurement.id]).update(
            created_on=date(2026, 7, 3)
        )
        Box.objects.filter(id=regular.id).update(created_on=date(2026, 7, 4))
        BiologicalMeasurement.objects.create(
            box=imported,
            measured_on=date(2021, 5, 12),
            polyp_count=0,
        )
        BiologicalMeasurement.objects.create(
            box=imported,
            measured_on=date(2023, 6, 8),
            polyp_count=4,
        )
        BiologicalMeasurement.objects.create(
            box=regular,
            measured_on=date(2020, 1, 2),
            polyp_count=2,
        )
        self.login_admin()

        response = self.client.get(reverse("api_admin_box_inventory"))
        results = {item["id"]: item for item in response.data["results"]}

        self.assertEqual(results[imported.id]["created_on"], "2026-07-03")
        self.assertEqual(results[imported.id]["inventory_created_on"], "2021-05-12")
        self.assertEqual(
            results[imported_without_measurement.id]["inventory_created_on"],
            "2026-07-03",
        )
        self.assertEqual(results[regular.id]["inventory_created_on"], "2026-07-04")

    def test_creation_year_options_and_combined_filter_are_scoped_to_active_institution(self):
        target = self.create_box(
            1,
            status=Box.Status.PENDING_REVIEW,
            zone=self.zone,
        )
        regular = self.create_box(2, status=Box.Status.ACTIVE, zone=self.zone)
        foreign = self.create_box(
            99,
            status=Box.Status.PENDING_REVIEW,
            organization=self.other_organization,
        )
        Box.objects.filter(id__in=[target.id, foreign.id]).update(
            created_on=date(2026, 7, 3),
        )
        Box.objects.filter(id=regular.id).update(created_on=date(2024, 4, 2))
        BiologicalMeasurement.objects.create(
            box=target,
            measured_on=date(2021, 5, 12),
            polyp_count=0,
            ephyrae_count=3,
        )
        BiologicalMeasurement.objects.create(
            box=foreign,
            measured_on=date(1998, 2, 1),
            polyp_count=4,
            ephyrae_count=0,
        )
        OrganizationMembership.objects.create(
            user=self.admin,
            organization=self.other_organization,
            role=OrganizationMembership.Role.ADMIN,
        )
        self.login_admin()

        response = self.client.get(
            reverse("api_admin_box_inventory"),
            {
                "creation_year": 2021,
                "status": Box.Status.PENDING_REVIEW,
                "location": self.zone.id,
                "q": target.global_code,
            },
            HTTP_X_ORGANIZATION_ID=str(self.organization.id),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["id"] for item in response.data["results"]], [target.id])
        self.assertEqual(response.data["filter_options"]["creation_years"], [2021, 2024])
        self.assertNotIn(1998, response.data["filter_options"]["creation_years"])

    def test_measurement_age_filter_keeps_zero_and_separates_missing_future_and_recent(self):
        old_zero = self.create_box(1, status=Box.Status.PENDING_REVIEW)
        boundary = self.create_box(2, status=Box.Status.PENDING_REVIEW)
        future = self.create_box(3, status=Box.Status.PENDING_REVIEW)
        without_measurement = self.create_box(4, status=Box.Status.PENDING_REVIEW)
        old_positive = self.create_box(5, status=Box.Status.PENDING_REVIEW)
        recent_zero = self.create_box(6, status=Box.Status.PENDING_REVIEW)
        measurements = (
            (old_zero, date(2026, 2, 27), 0, 0),
            (boundary, date(2026, 2, 28), 1, 0),
            (future, date(2026, 6, 15), 2, 1),
            (old_positive, date(2025, 12, 31), 12, 3),
            (recent_zero, date(2026, 5, 20), 0, 0),
        )
        for box, measured_on, polyps, ephyrae in measurements:
            BiologicalMeasurement.objects.create(
                box=box,
                measured_on=measured_on,
                polyp_count=polyps,
                ephyrae_count=ephyrae,
            )
        self.login_admin()

        older_response = self.client.get(
            reverse("api_admin_box_inventory"),
            {
                "measurement_filter": "older_than",
                "age_months": 3,
                "reference_date": "2026-05-31",
            },
        )
        older_results = {item["id"]: item for item in older_response.data["results"]}

        self.assertEqual(older_response.status_code, 200)
        self.assertEqual(set(older_results), {old_zero.id, old_positive.id})
        self.assertEqual(older_results[old_zero.id]["latest_measurement"]["polyp_count"], 0)
        self.assertEqual(older_results[old_zero.id]["latest_measurement"]["ephyrae_count"], 0)
        self.assertNotIn(boundary.id, older_results)
        self.assertNotIn(future.id, older_results)
        self.assertNotIn(recent_zero.id, older_results)
        self.assertNotIn(without_measurement.id, older_results)

        missing_response = self.client.get(
            reverse("api_admin_box_inventory"),
            {"measurement_filter": "none", "reference_date": "2026-05-31"},
        )
        self.assertEqual(
            [item["id"] for item in missing_response.data["results"]],
            [without_measurement.id],
        )

    def test_measurement_age_defaults_to_server_today(self):
        old = self.create_box(1, status=Box.Status.PENDING_REVIEW)
        BiologicalMeasurement.objects.create(
            box=old,
            measured_on=date(2026, 5, 31),
            polyp_count=0,
            ephyrae_count=2,
        )
        self.login_admin()

        with patch(
            "apps.cultures.api_views.timezone.localdate",
            return_value=date(2026, 9, 1),
        ):
            response = self.client.get(
                reverse("api_admin_box_inventory"),
                {"measurement_filter": "older_than", "age_months": 3},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["filter_options"]["reference_date"], "2026-09-01")
        self.assertEqual([item["id"] for item in response.data["results"]], [old.id])

    def test_inventory_rejects_invalid_review_filters(self):
        self.login_admin()

        for filters, field in (
            ({"creation_year": "unknown"}, "creation_year"),
            ({"creation_year": 0}, "creation_year"),
            ({"reference_date": "31-05-2026"}, "reference_date"),
            ({"measurement_filter": "stale"}, "measurement_filter"),
            ({"measurement_filter": "older_than", "age_months": ""}, "age_months"),
            ({"measurement_filter": "older_than", "age_months": 0}, "age_months"),
        ):
            with self.subTest(filters=filters):
                response = self.client.get(reverse("api_admin_box_inventory"), filters)
                self.assertEqual(response.status_code, 400)
                self.assertIn(field, response.data)

    def test_inactive_inventory_exposes_real_last_location_and_detail_keeps_history(self):
        located = self.create_box(1, status=Box.Status.INACTIVE)
        never_located = self.create_box(2, status=Box.Status.INACTIVE)
        now = timezone.now()
        first = BoxLocation.objects.create(
            box=located,
            thermal_zone=self.zone,
            starts_at=now - timedelta(days=40),
            ends_at=now - timedelta(days=20),
        )
        last = BoxLocation.objects.create(
            box=located,
            thermal_zone=self.second_zone,
            starts_at=now - timedelta(days=19),
            end_date_unknown=True,
        )
        self.login_admin()

        response = self.client.get(
            reverse("api_admin_box_inventory"),
            {"status": Box.Status.INACTIVE},
        )
        results = {item["id"]: item for item in response.data["results"]}

        self.assertEqual(results[located.id]["last_location"]["id"], last.id)
        self.assertEqual(
            results[located.id]["last_location"]["thermal_zone"]["id"],
            self.second_zone.id,
        )
        self.assertIsNone(results[never_located.id]["last_location"])
        detail = self.client.get(reverse("api_box_detail", args=[located.id]))
        self.assertEqual(detail.status_code, 200)
        self.assertIsNone(detail.data["thermal_zone"])
        self.assertEqual(
            {item["id"] for item in detail.data["locations"]},
            {first.id, last.id},
        )

    def test_location_history_never_exposes_a_foreign_institution_zone(self):
        box = self.create_box(1, status=Box.Status.INACTIVE)
        now = timezone.now()
        local_location = BoxLocation.objects.create(
            box=box,
            thermal_zone=self.zone,
            starts_at=now - timedelta(days=20),
            ends_at=now - timedelta(days=10),
        )
        BoxLocation.objects.create(
            box=box,
            thermal_zone=self.foreign_zone,
            starts_at=now - timedelta(days=5),
            end_date_unknown=True,
        )
        self.login_admin()

        inventory = self.client.get(reverse("api_admin_box_inventory"))
        inventory_box = next(
            item for item in inventory.data["results"] if item["id"] == box.id
        )
        detail = self.client.get(reverse("api_box_detail", args=[box.id]))

        self.assertEqual(inventory_box["last_location"]["id"], local_location.id)
        self.assertEqual(
            [item["id"] for item in detail.data["locations"]],
            [local_location.id],
        )
        self.assertNotContains(inventory, self.foreign_zone.name)
        self.assertNotContains(detail, self.foreign_zone.name)

    def test_filtered_selection_returns_only_eligible_boxes_from_active_institution(self):
        eligible = self.create_box(1, status=Box.Status.PENDING_REVIEW)
        active = self.create_box(2, status=Box.Status.ACTIVE)
        foreign = self.create_box(
            99,
            status=Box.Status.PENDING_REVIEW,
            organization=self.other_organization,
        )
        OrganizationMembership.objects.create(
            user=self.admin,
            organization=self.other_organization,
            role=OrganizationMembership.Role.ADMIN,
        )
        self.login_admin()

        response = self.client.get(
            reverse("api_admin_box_inventory_selection"),
            HTTP_X_ORGANIZATION_ID=str(self.organization.id),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["matched_count"], 2)
        self.assertEqual(response.data["eligible_count"], 1)
        self.assertEqual(response.data["ineligible_count"], 1)
        self.assertEqual([item["id"] for item in response.data["results"]], [eligible.id])
        self.assertNotIn(foreign.id, [item["id"] for item in response.data["results"]])
        self.assertNotIn(active.id, [item["id"] for item in response.data["results"]])

        self.client.logout()
        self.client.login(username=self.technician.username, password="secret")
        forbidden = self.client.get(reverse("api_admin_box_inventory_selection"))
        self.assertEqual(forbidden.status_code, 403)

    def test_filtered_selection_reuses_measurement_filters_and_preserves_zero(self):
        candidate = self.create_box(1, status=Box.Status.PENDING_REVIEW, zone=self.zone)
        recent = self.create_box(2, status=Box.Status.PENDING_REVIEW, zone=self.zone)
        BiologicalMeasurement.objects.create(
            box=candidate,
            measured_on=date(2025, 12, 1),
            polyp_count=0,
            ephyrae_count=0,
        )
        BiologicalMeasurement.objects.create(
            box=recent,
            measured_on=date(2026, 5, 1),
            polyp_count=0,
            ephyrae_count=0,
        )
        self.login_admin()

        response = self.client.get(
            reverse("api_admin_box_inventory_selection"),
            {
                "status": Box.Status.PENDING_REVIEW,
                "location": self.zone.id,
                "measurement_filter": "older_than",
                "age_months": 3,
                "reference_date": "2026-05-31",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["id"] for item in response.data["results"]], [candidate.id])

    def test_filtered_selection_and_batch_reject_more_than_supported_limit(self):
        self.create_box(1, status=Box.Status.PENDING_REVIEW)
        self.create_box(2, status=Box.Status.PENDING_REVIEW)
        self.login_admin()

        with patch("apps.cultures.api_views.BOX_INVENTORY_BATCH_MAX_ITEMS", 1):
            selection = self.client.get(reverse("api_admin_box_inventory_selection"))
        self.assertEqual(selection.status_code, 400)
        self.assertEqual(selection.data["code"], "selection_too_large")
        self.assertEqual(selection.data["eligible_count"], 2)

        batch = self.client.post(
            reverse("api_admin_box_inventory_batch_qualify"),
            data={
                "box_ids": list(range(1, 502)),
                "target_status": Box.Status.ACTIVE,
            },
            content_type="application/json",
        )
        self.assertEqual(batch.status_code, 400)
        self.assertIn("box_ids", batch.data)

    def test_inventory_query_count_does_not_grow_with_page_size(self):
        self.create_box(1, status=Box.Status.PENDING_REVIEW, zone=self.zone)
        self.login_admin()
        self.client.get(reverse("api_admin_box_inventory"))
        with CaptureQueriesContext(connection) as one_box_queries:
            self.client.get(reverse("api_admin_box_inventory"))

        for number in range(2, 22):
            box = self.create_box(number, status=Box.Status.PENDING_REVIEW, zone=self.zone)
            BoxLocation.objects.create(box=box, thermal_zone=self.zone)
            BiologicalMeasurement.objects.create(
                box=box,
                measured_on=date(2026, 1, 1),
                polyp_count=number,
                ephyrae_count=0,
            )
        with CaptureQueriesContext(connection) as full_page_queries:
            self.client.get(reverse("api_admin_box_inventory"))

        self.assertEqual(len(full_page_queries), len(one_box_queries))

    def test_admin_can_assign_first_location_only_to_active_unlocated_box(self):
        unlocated = self.create_box(1, status=Box.Status.ACTIVE)
        located = self.create_box(2, status=Box.Status.ACTIVE, zone=self.zone)
        inactive = self.create_box(3, status=Box.Status.INACTIVE)
        self.login_admin()

        response = self.client.post(
            reverse("api_admin_box_initial_location", args=[unlocated.id]),
            data={"thermal_zone_id": self.second_zone.id, "notes": "Location verified."},
        )

        self.assertEqual(response.status_code, 200)
        unlocated.refresh_from_db()
        self.assertEqual(unlocated.thermal_zone, self.second_zone)
        self.assertTrue(
            BoxLocation.objects.filter(
                box=unlocated,
                thermal_zone=self.second_zone,
                ends_at__isnull=True,
                end_date_unknown=False,
            ).exists()
        )
        self.assertTrue(
            BoxMovement.objects.filter(
                box=unlocated,
                from_thermal_zone__isnull=True,
                to_thermal_zone=self.second_zone,
            ).exists()
        )

        already_located = self.client.post(
            reverse("api_admin_box_initial_location", args=[located.id]),
            data={"thermal_zone_id": self.second_zone.id},
        )
        self.assertEqual(already_located.status_code, 400)

        inactive_response = self.client.post(
            reverse("api_admin_box_initial_location", args=[inactive.id]),
            data={"thermal_zone_id": self.second_zone.id},
        )
        self.assertEqual(inactive_response.status_code, 400)

        foreign_zone_response = self.client.post(
            reverse("api_admin_box_initial_location", args=[self.create_box(4).id]),
            data={"thermal_zone_id": self.foreign_zone.id},
        )
        self.assertEqual(foreign_zone_response.status_code, 400)

    def test_pending_box_can_be_qualified_active_with_a_location(self):
        box = self.create_box(1, status=Box.Status.PENDING_REVIEW)
        self.login_admin()

        response = self.client.post(
            reverse("api_box_qualify", args=[box.id]),
            data={
                "target_status": Box.Status.ACTIVE,
                "thermal_zone_id": self.zone.id,
            },
        )

        self.assertEqual(response.status_code, 200)
        box.refresh_from_db()
        self.assertEqual(box.status, Box.Status.ACTIVE)
        self.assertEqual(box.thermal_zone, self.zone)
        self.assertTrue(BoxLocation.objects.filter(box=box, thermal_zone=self.zone).exists())

    def test_technician_cannot_assign_an_initial_location(self):
        box = self.create_box(1, status=Box.Status.ACTIVE)
        self.client.login(username=self.technician.username, password="secret")

        response = self.client.post(
            reverse("api_admin_box_initial_location", args=[box.id]),
            data={"thermal_zone_id": self.zone.id},
        )

        self.assertEqual(response.status_code, 403)
        box.refresh_from_db()
        self.assertIsNone(box.thermal_zone)

    def test_batch_qualify_active_reports_locations_without_assigning_one(self):
        located = self.create_box(
            1,
            status=Box.Status.PENDING_REVIEW,
            zone=self.zone,
        )
        unlocated = self.create_box(2, status=Box.Status.PENDING_REVIEW)
        self.login_admin()

        response = self.client.post(
            reverse("api_admin_box_inventory_batch_qualify"),
            data={
                "box_ids": [located.id, unlocated.id],
                "target_status": Box.Status.ACTIVE,
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["requested_count"], 2)
        self.assertEqual(response.data["success_count"], 2)
        self.assertEqual(response.data["failure_count"], 0)
        self.assertEqual(response.data["active_with_location_count"], 1)
        self.assertEqual(response.data["active_without_location_count"], 1)
        located.refresh_from_db()
        unlocated.refresh_from_db()
        self.assertEqual(located.status, Box.Status.ACTIVE)
        self.assertEqual(located.thermal_zone, self.zone)
        self.assertEqual(unlocated.status, Box.Status.ACTIVE)
        self.assertIsNone(unlocated.thermal_zone)
        self.assertFalse(BoxLocation.objects.filter(box=unlocated).exists())

        anomaly_response = self.client.get(
            reverse("api_admin_box_inventory"),
            {"status": Box.Status.ACTIVE, "location": "none"},
        )
        self.assertEqual(
            [item["id"] for item in anomaly_response.data["results"]],
            [unlocated.id],
        )

    def test_batch_qualify_inactive_uses_missing_historical_reason(self):
        box = self.create_box(
            1,
            status=Box.Status.PENDING_REVIEW,
            zone=self.zone,
        )
        location = BoxLocation.objects.create(box=box, thermal_zone=self.zone)
        self.login_admin()

        response = self.client.post(
            reverse("api_admin_box_inventory_batch_qualify"),
            data={
                "box_ids": [box.id],
                "target_status": Box.Status.INACTIVE,
                "reason_missing_from_history": True,
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["success_count"], 1)
        box.refresh_from_db()
        location.refresh_from_db()
        self.assertEqual(box.status, Box.Status.INACTIVE)
        self.assertIsNone(box.thermal_zone)
        self.assertEqual(box.stop_reason, "")
        self.assertTrue(box.stop_reason_missing_from_history)
        self.assertIsNone(box.deactivated_on)
        self.assertTrue(location.end_date_unknown)
        self.assertIsNone(location.ends_at)

    def test_batch_qualify_allows_partial_success_and_hides_foreign_boxes(self):
        valid = self.create_box(1, status=Box.Status.PENDING_REVIEW)
        changed_since_selection = self.create_box(2, status=Box.Status.ACTIVE)
        foreign = self.create_box(
            3,
            status=Box.Status.PENDING_REVIEW,
            organization=self.other_organization,
        )
        missing_id = max(valid.id, changed_since_selection.id, foreign.id) + 1000
        self.login_admin()

        response = self.client.post(
            reverse("api_admin_box_inventory_batch_qualify"),
            data={
                "box_ids": [valid.id, changed_since_selection.id, foreign.id, missing_id],
                "target_status": Box.Status.ACTIVE,
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["requested_count"], 4)
        self.assertEqual(response.data["success_count"], 1)
        self.assertEqual(response.data["failure_count"], 3)
        failures = {item["box_id"]: item for item in response.data["failures"]}
        self.assertIn("pending review", failures[changed_since_selection.id]["error"])
        self.assertIsNone(failures[foreign.id]["global_code"])
        self.assertEqual(
            failures[foreign.id]["error"],
            "Box not found in the active institution.",
        )
        self.assertIsNone(failures[missing_id]["global_code"])
        valid.refresh_from_db()
        changed_since_selection.refresh_from_db()
        foreign.refresh_from_db()
        self.assertEqual(valid.status, Box.Status.ACTIVE)
        self.assertEqual(changed_since_selection.status, Box.Status.ACTIVE)
        self.assertEqual(foreign.status, Box.Status.PENDING_REVIEW)

    def test_batch_qualify_rolls_back_only_the_box_that_fails(self):
        succeeds = self.create_box(
            1,
            status=Box.Status.PENDING_REVIEW,
            zone=self.zone,
        )
        fails = self.create_box(
            2,
            status=Box.Status.PENDING_REVIEW,
            zone=self.zone,
        )
        succeeds_location = BoxLocation.objects.create(box=succeeds, thermal_zone=self.zone)
        fails_location = BoxLocation.objects.create(box=fails, thermal_zone=self.zone)
        original_create = AuditLog.objects.create

        def create_audit_or_fail(**kwargs):
            if kwargs.get("object_id") == fails.global_code:
                raise IntegrityError("Simulated audit failure")
            return original_create(**kwargs)

        self.login_admin()
        with patch(
            "apps.cultures.services.AuditLog.objects.create",
            side_effect=create_audit_or_fail,
        ):
            response = self.client.post(
                reverse("api_admin_box_inventory_batch_qualify"),
                data={
                    "box_ids": [succeeds.id, fails.id],
                    "target_status": Box.Status.INACTIVE,
                    "reason_missing_from_history": True,
                },
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["success_count"], 1)
        self.assertEqual(response.data["failure_count"], 1)
        succeeds.refresh_from_db()
        fails.refresh_from_db()
        succeeds_location.refresh_from_db()
        fails_location.refresh_from_db()
        self.assertEqual(succeeds.status, Box.Status.INACTIVE)
        self.assertTrue(succeeds_location.end_date_unknown)
        self.assertEqual(fails.status, Box.Status.PENDING_REVIEW)
        self.assertEqual(fails.thermal_zone, self.zone)
        self.assertFalse(fails.stop_reason_missing_from_history)
        self.assertFalse(fails_location.end_date_unknown)
        self.assertIsNone(fails_location.ends_at)

    def test_batch_qualify_is_admin_only(self):
        box = self.create_box(1, status=Box.Status.PENDING_REVIEW)
        self.client.login(username=self.technician.username, password="secret")

        response = self.client.post(
            reverse("api_admin_box_inventory_batch_qualify"),
            data={"box_ids": [box.id], "target_status": Box.Status.ACTIVE},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)
        box.refresh_from_db()
        self.assertEqual(box.status, Box.Status.PENDING_REVIEW)

    def test_batch_qualify_keeps_zero_measurements_visible(self):
        box = self.create_box(1, status=Box.Status.PENDING_REVIEW)
        BiologicalMeasurement.objects.create(
            box=box,
            measured_on=date(2026, 8, 28),
            polyp_count=0,
            ephyrae_count=0,
        )
        self.login_admin()

        batch_response = self.client.post(
            reverse("api_admin_box_inventory_batch_qualify"),
            data={"box_ids": [box.id], "target_status": Box.Status.ACTIVE},
            content_type="application/json",
        )
        self.assertEqual(batch_response.status_code, 200)

        inventory_response = self.client.get(reverse("api_admin_box_inventory"))
        item = next(
            result
            for result in inventory_response.data["results"]
            if result["id"] == box.id
        )
        self.assertEqual(item["latest_measurement"]["polyp_count"], 0)
        self.assertEqual(item["latest_measurement"]["ephyrae_count"], 0)

    def test_batch_qualify_rejects_duplicate_ids(self):
        box = self.create_box(1, status=Box.Status.PENDING_REVIEW)
        self.login_admin()

        response = self.client.post(
            reverse("api_admin_box_inventory_batch_qualify"),
            data={"box_ids": [box.id, box.id], "target_status": Box.Status.ACTIVE},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        box.refresh_from_db()
        self.assertEqual(box.status, Box.Status.PENDING_REVIEW)
