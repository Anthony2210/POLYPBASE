from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import OrganizationMembership
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
