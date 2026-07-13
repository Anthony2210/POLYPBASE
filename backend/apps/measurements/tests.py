"""Tests for measurements: editing an existing measurement (the "Modifier"
action) and the per-box salinity.

Salinity is entered per measurement but rarely changes, so a box must keep
showing the last salinity actually recorded, even when later measurements leave
it blank. That is the behaviour users reported as "the salinity disappears".
"""

import json
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import OrganizationMembership
from apps.cultures.models import Box, ThermalZone
from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain

from .models import BiologicalMeasurement


class MeasurementEditingApiTests(TestCase):
    def setUp(self):
        user_model = get_user_model()

        self.organization = Organization.objects.create(name="Aquarium de Paris", slug="paris")
        self.other_organization = Organization.objects.create(name="Aquarium de Tokyo", slug="tokyo")

        self.technician = user_model.objects.create_user(username="tech", password="secret")
        OrganizationMembership.objects.create(
            user=self.technician,
            organization=self.organization,
            role=OrganizationMembership.Role.LAB_TECHNICIAN,
        )

        self.viewer = user_model.objects.create_user(username="viewer", password="secret")
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

        self.today = date.today()

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

    # -- salinity persistence ----------------------------------------------

    def test_latest_salinity_survives_a_newer_measurement_without_salinity(self):
        """The regression users reported: saving a new measurement without a
        salinity used to make the displayed salinity disappear."""
        BiologicalMeasurement.objects.create(
            box=self.box,
            measured_on=self.today - timedelta(days=2),
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
            measured_on=self.today - timedelta(days=2),
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
