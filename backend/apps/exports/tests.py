import csv
from datetime import date
from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.cultures.models import Box, ThermalZone
from apps.measurements.models import BiologicalMeasurement, DailyTemperature
from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain

from .models import DataExport


class MeasurementExportApiTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username="exporter", password="secret")
        self.organization = Organization.objects.create(
            name="Aquarium de Paris",
            slug="aquarium-de-paris",
        )
        self.other_organization = Organization.objects.create(
            name="Aquarium partenaire",
            slug="aquarium-partenaire",
        )
        OrganizationMembership.objects.create(
            user=self.user,
            organization=self.organization,
            role=OrganizationMembership.Role.VIEWER,
        )

        self.species = Species.objects.create(
            scientific_name="Aurelia labiata",
            genus_species_code="ALA",
        )
        self.strain = Strain.objects.create(
            species=self.species,
            code="1-ATL",
        )
        self.zone = ThermalZone.objects.create(
            organization=self.organization,
            name="Armoire 10 C",
            target_temperature_c=Decimal("10.0"),
        )
        self.box = Box.objects.create(
            organization=self.organization,
            global_code="ALA-1.003-ATL",
            local_code="1.03",
            box_number="003",
            strain=self.strain,
            thermal_zone=self.zone,
        )

        BiologicalMeasurement.objects.create(
            box=self.box,
            measured_on=date(2026, 5, 4),
            polyp_count=90,
            ephyrae_count=2,
            user=self.user,
        )
        BiologicalMeasurement.objects.create(
            box=self.box,
            measured_on=date(2026, 5, 6),
            polyp_count=100,
            ephyrae_count=5,
            user=self.user,
        )
        BiologicalMeasurement.objects.create(
            box=self.box,
            measured_on=date(2026, 5, 11),
            polyp_count=0,
            ephyrae_count=0,
            user=self.user,
        )
        DailyTemperature.objects.create(
            thermal_zone=self.zone,
            date=date(2026, 5, 6),
            average_temperature_c=Decimal("10.25"),
            measurement_count=1,
        )

    def test_export_options_are_scoped_to_accessible_organizations(self):
        foreign_zone = ThermalZone.objects.create(
            organization=self.other_organization,
            name="Armoire partenaire",
        )
        Box.objects.create(
            organization=self.other_organization,
            global_code="ALA-9.001-PAR",
            box_number="001",
            strain=self.strain,
            thermal_zone=foreign_zone,
        )
        self.client.login(username="exporter", password="secret")

        response = self.client.get(reverse("api_export_options"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(
            [organization["id"] for organization in payload["organizations"]],
            [self.organization.id],
        )
        self.assertEqual(
            [box["global_code"] for box in payload["boxes"]],
            [self.box.global_code],
        )

    def test_weekly_csv_matches_the_historical_wide_structure(self):
        self.client.login(username="exporter", password="secret")

        response = self.client.get(
            reverse("api_export_measurements_csv"),
            {
                "boxes": str(self.box.id),
                "date_from": "2026-05-01",
                "date_to": "2026-05-17",
            },
        )

        self.assertEqual(response.status_code, 200)
        rows = list(csv.reader(StringIO(response.content.decode("utf-8-sig"))))
        self.assertEqual(
            rows[0],
            [
                "Date",
                "année",
                "semaines cumulées",
                "semaines",
                "numéro de boite",
                "1.03_polypes",
                "1.03_ephyrules",
                "1.03_temperature",
            ],
        )
        self.assertEqual(rows[1], ["2026_S18", "2026", "1", "18", "1.03", "", "", ""])
        self.assertEqual(rows[2], ["2026_S19", "2026", "2", "19", "1.03", "100", "5", "10.25"])
        self.assertEqual(rows[3], ["2026_S20", "2026", "3", "20", "1.03", "0", "0", "10"])
        self.assertEqual(DataExport.objects.count(), 1)
        self.assertEqual(
            AuditLog.objects.filter(action=AuditLog.Action.EXPORT).count(),
            1,
        )

    def test_preview_returns_aggregated_values_without_recording_an_export(self):
        self.client.login(username="exporter", password="secret")

        response = self.client.get(
            reverse("api_export_measurements_preview"),
            {
                "boxes": str(self.box.id),
                "date_from": "2026-05-01",
                "date_to": "2026-05-17",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["metadata"]["box_count"], 1)
        self.assertEqual(payload["metadata"]["measurement_count"], 3)
        self.assertEqual(
            payload["points"],
            [
                {
                    "label": "2026_S18",
                    "polyp_count": 0,
                    "ephyrae_count": 0,
                    "average_temperature_c": None,
                    "measurement_count": 0,
                },
                {
                    "label": "2026_S19",
                    "polyp_count": 100,
                    "ephyrae_count": 5,
                    "average_temperature_c": 10.25,
                    "measurement_count": 1,
                },
                {
                    "label": "2026_S20",
                    "polyp_count": 0,
                    "ephyrae_count": 0,
                    "average_temperature_c": 10.0,
                    "measurement_count": 1,
                },
            ],
        )
        self.assertEqual(DataExport.objects.count(), 0)
        self.assertEqual(AuditLog.objects.filter(action=AuditLog.Action.EXPORT).count(), 0)

    def test_csv_filters_are_cumulative(self):
        other_species = Species.objects.create(scientific_name="Cassiopea andromeda")
        other_strain = Strain.objects.create(species=other_species, code="2-MED")
        other_box = Box.objects.create(
            organization=self.organization,
            global_code="CAN-2.001-MED",
            local_code="2.01",
            box_number="001",
            strain=other_strain,
            thermal_zone=self.zone,
        )
        BiologicalMeasurement.objects.create(
            box=other_box,
            measured_on=date(2026, 5, 6),
            polyp_count=40,
            ephyrae_count=1,
            user=self.user,
        )
        self.client.login(username="exporter", password="secret")

        response = self.client.get(
            reverse("api_export_measurements_csv"),
            {
                "species": f"{self.species.id},{other_species.id}",
                "strains": str(self.strain.id),
                "date_from": "2026-05-01",
                "date_to": "2026-05-10",
            },
        )

        self.assertEqual(response.status_code, 200)
        header = next(csv.reader(StringIO(response.content.decode("utf-8-sig"))))
        self.assertIn("1.03_polypes", header)
        self.assertNotIn("2.01_polypes", header)

    def test_csv_derives_short_box_number_when_local_code_is_missing(self):
        box = Box.objects.create(
            organization=self.organization,
            global_code="AFL-TAI-1.001",
            local_code="AFL-TAI-1.01",
            box_number="001",
            strain=self.strain,
            thermal_zone=self.zone,
        )
        BiologicalMeasurement.objects.create(
            box=box,
            measured_on=date(2026, 5, 6),
            polyp_count=12,
            ephyrae_count=3,
            user=self.user,
        )
        self.client.login(username="exporter", password="secret")

        response = self.client.get(
            reverse("api_export_measurements_csv"),
            {
                "boxes": str(box.id),
                "date_from": "2026-05-01",
                "date_to": "2026-05-10",
            },
        )

        self.assertEqual(response.status_code, 200)
        rows = list(csv.reader(StringIO(response.content.decode("utf-8-sig"))))
        self.assertIn("1.01_polypes", rows[0])
        self.assertNotIn("AFL-TAI-1.001_polypes", rows[0])
        self.assertNotIn("AFL-TAI-1.01_polypes", rows[0])
        self.assertEqual(rows[1][4], "1.01")

    def test_csv_rejects_an_unauthorized_organization(self):
        self.client.login(username="exporter", password="secret")

        response = self.client.get(
            reverse("api_export_measurements_csv"),
            {"organizations": str(self.other_organization.id)},
        )

        self.assertEqual(response.status_code, 403)
