from datetime import timedelta

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.accounts.models import OrganizationMembership
from apps.measurements.models import BiologicalMeasurement
from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain

from .models import Box, BoxLocation, ThermalZone


class DashboardApiTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.now = timezone.now()
        cls.today = timezone.localdate(cls.now)
        cls.user = get_user_model().objects.create_user(username="dashboard", email="dashboard@example.org")
        cls.org = Organization.objects.create(name="A Dashboard laboratory")
        cls.other_org = Organization.objects.create(name="B Dashboard laboratory")
        for org in (cls.org, cls.other_org):
            OrganizationMembership.objects.create(
                user=cls.user, organization=org, role=OrganizationMembership.Role.LAB_TECHNICIAN,
            )
        species = Species.objects.create(scientific_name="Aurelia dashboard", genus_species_code="ADB")
        cls.strain = Strain.objects.create(organization=cls.org, species=species, code="ADB-DASH-1")
        cls.other_strain = Strain.objects.create(organization=cls.other_org, species=species, code="ADB-OTH-1")
        cls.zone = ThermalZone.objects.create(organization=cls.org, name="Dashboard zone", is_active=False)
        cls.other_zone = ThermalZone.objects.create(organization=cls.other_org, name="Other zone")

    def setUp(self):
        self.client.force_authenticate(self.user)
        self.url = reverse("api_dashboard")

    def box(self, number, *, strain=None, status=Box.Status.ACTIVE):
        strain = strain or self.strain
        return Box.objects.create(
            organization=strain.organization, strain=strain, status=status,
            global_code=f"{strain.code}.{number:03d}", box_number=str(number),
        )

    def measurement(self, box, polyp_count, *, days=0, strobila_count=None, **kwargs):
        return BiologicalMeasurement.objects.create(
            box=box, measured_on=self.today + timedelta(days=days), user=self.user,
            polyp_count=polyp_count, ephyrae_count=1, strobila_count=strobila_count, **kwargs,
        )

    def test_dashboard_query_budget_with_eight_distinct_environment_sources(self):
        entries = []
        for number in range(8):
            box = self.box(number + 1)
            entries.append(self.measurement(box, number, days=-number, environment_source_box=box))
            BoxLocation.objects.create(box=box, thermal_zone=self.zone)
        self.box(9)  # Active, but no known current count.
        inactive = self.box(10, status=Box.Status.INACTIVE)
        old = self.measurement(inactive, 20, days=-30, strobila_count=0)
        self.measurement(inactive, 999, days=-30, duplicate_of=old, strobila_count=999)
        self.measurement(self.box(1, strain=self.other_strain), 999, days=1, strobila_count=999)

        with self.assertNumQueries(12):
            response = self.client.get(self.url, HTTP_X_ORGANIZATION_ID=str(self.org.pk))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual([row["id"] for row in data["organizations"]], [self.org.pk])
        self.assertEqual(data["stats"], {
            "boxes_total": 10, "active_boxes": 9, "species_count": 1, "thermal_zones": 1,
            "current_polyps": 28, "current_polyps_unknown_box_count": 1,
            "measured_polyps": 48, "measured_ephyrae": 9, "measured_strobilae": 0,
        })
        self.assertEqual([row["id"] for row in data["latest_entries"]], [entry.pk for entry in entries])
        for row in data["latest_entries"]:
            self.assertTrue(row["can_edit"])
            self.assertEqual(row["user"], self.user.username)
            self.assertIsNone(row["strobila_count"])
            self.assertEqual([loc["thermal_zone"]["id"] for loc in row["environment_locations"]], [self.zone.pk])
        self.assertEqual(data["latest_entries"][0]["polyp_count"], 0)

    def test_environment_periods_preserve_order_and_distinguish_absent_source(self):
        box = self.box(1)
        source = self.box(2, status=Box.Status.INACTIVE)
        empty_source = self.box(3, status=Box.Status.INACTIVE)
        start = self.now - timedelta(days=40)
        later = BoxLocation.objects.create(box=source, thermal_zone=self.zone, starts_at=self.now)
        earlier = BoxLocation.objects.create(box=source, thermal_zone=self.zone, starts_at=start, ends_at=self.now)
        BoxLocation.objects.create(box=source, thermal_zone=self.other_zone, starts_at=start)
        # Simulate a stale cross-organization source link to ensure it cannot expose periods.
        foreign_source = self.box(1, strain=self.other_strain)
        BoxLocation.objects.create(box=foreign_source, thermal_zone=self.other_zone)
        out_of_scope = self.measurement(box, 1, days=-28)
        BiologicalMeasurement.objects.filter(pk=out_of_scope.pk).update(environment_source_box=foreign_source)
        shared = [self.measurement(box, 1, days=-day, environment_source_box=source) for day in (0, 7)]
        empty = self.measurement(box, 1, days=-14, environment_source_box=empty_source)
        absent = self.measurement(box, 1, days=-21)

        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        locations = {row["id"]: row["environment_locations"] for row in response.json()["latest_entries"]}
        for entry in shared:
            self.assertEqual([row["id"] for row in locations[entry.pk]], [earlier.pk, later.pk])
        self.assertEqual(locations[empty.pk], [])
        self.assertEqual(locations[out_of_scope.pk], [])
        self.assertIsNone(locations[absent.pk])
        self.assertIsNone(response.json()["stats"]["measured_strobilae"])

    def test_dashboard_with_no_authorized_organization_is_empty(self):
        self.measurement(self.box(1), 5)
        self.client.force_authenticate(get_user_model().objects.create_user(username="no-memberships", email="no-memberships@example.org"))
        denied = self.client.get(self.url, HTTP_X_ORGANIZATION_ID=str(self.org.pk))
        self.assertEqual(denied.status_code, 403)
        with self.assertNumQueries(1):
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            "organizations": [], "latest_entries": [], "recent_accesses": [],
            "stats": {
                "boxes_total": 0, "active_boxes": 0, "species_count": 0, "thermal_zones": 0,
                "current_polyps": 0, "current_polyps_unknown_box_count": 0,
                "measured_polyps": 0, "measured_ephyrae": 0, "measured_strobilae": 0,
            },
        })
