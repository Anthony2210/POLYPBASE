import json
from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import OrganizationMembership, UserPreference
from apps.audit.models import AuditLog
from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain
from apps.measurements.models import BiologicalMeasurement, DailyTemperature, Probe, SalinityMeasurement

from .models import Box, ThermalZone


class PolypbaseApiTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username="tech", password="secret")

        self.organization = Organization.objects.create(name="Aquarium de Paris", slug="aquariumdeparis")
        self.other_organization = Organization.objects.create(name="Aquarium de Tokyo", slug="aquariumdetokyo")
        OrganizationMembership.objects.create(
            user=self.user,
            organization=self.organization,
            role=OrganizationMembership.Role.LAB_TECHNICIAN,
        )

        self.species = Species.objects.create(
            scientific_name="Aurelia aurita",
            genus_species_code="AAU",
        )
        self.strain = Strain.objects.create(species=self.species, code="1-ATL", number=1, origin_code="ATL")
        self.zone = ThermalZone.objects.create(
            organization=self.organization,
            name="Cabinet-15",
            zone_type=ThermalZone.ZoneType.CABINET,
            target_temperature_c=15,
        )
        self.other_zone = ThermalZone.objects.create(
            organization=self.other_organization,
            name="Cabinet-10",
            zone_type=ThermalZone.ZoneType.CABINET,
            target_temperature_c=10,
        )
        self.box = Box.objects.create(
            organization=self.organization,
            global_code="AAU-1.001-ATL",
            box_number="001",
            strain=self.strain,
            thermal_zone=self.zone,
        )
        Box.objects.create(
            organization=self.other_organization,
            global_code="AAU-1.001-TKY",
            box_number="001",
            strain=self.strain,
            thermal_zone=self.other_zone,
        )

    def test_health_endpoint_is_public(self):
        response = self.client.get(reverse("api_health"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    def test_legacy_french_api_routes_are_removed(self):
        self.client.login(username="tech", password="secret")

        self.assertEqual(self.client.get("/api/boites/").status_code, 404)
        self.assertEqual(self.client.get("/api/zones/").status_code, 404)

    def test_drf_box_list_is_paginated_and_scoped(self):
        self.client.login(username="tech", password="secret")

        response = self.client.get(reverse("api_box_list"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["results"][0]["global_code"], "AAU-1.001-ATL")

    def test_drf_box_detail_returns_measurement_history(self):
        BiologicalMeasurement.objects.create(
            box=self.box,
            measured_on=date(2026, 5, 4),
            polyp_count=42,
            user=self.user,
        )
        self.client.login(username="tech", password="secret")

        response = self.client.get(reverse("api_box_detail", args=[self.box.id]))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["global_code"], "AAU-1.001-ATL")
        self.assertEqual(payload["biological_measurements"][0]["polyp_count"], 42)

    def test_drf_measurement_endpoint_creates_a_measurement(self):
        self.client.login(username="tech", password="secret")

        response = self.client.post(
            reverse("api_box_measurements", args=[self.box.id]),
            data=json.dumps(
                {
                    "measured_on": "2026-05-05",
                    "polyp_count": 55,
                    "ephyrae_count": 6,
                    "strobila_count": 3,
                    "culture_status": BiologicalMeasurement.CultureStatus.GOOD,
                    "needs_attention": False,
                    "notes": "Clean API entry",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        measurement = BiologicalMeasurement.objects.get(box=self.box, measured_on=date(2026, 5, 5))
        self.assertEqual(measurement.polyp_count, 55)
        self.assertEqual(
            AuditLog.objects.filter(action=AuditLog.Action.ENTRY, object_id=self.box.global_code).count(),
            1,
        )

    def test_drf_measurement_endpoint_blocks_read_only_users(self):
        user_model = get_user_model()
        viewer = user_model.objects.create_user(username="viewer", password="secret")
        OrganizationMembership.objects.create(
            user=viewer,
            organization=self.organization,
            role=OrganizationMembership.Role.VIEWER,
        )
        self.client.login(username="viewer", password="secret")

        response = self.client.post(
            reverse("api_box_measurements", args=[self.box.id]),
            data=json.dumps(
                {
                    "measured_on": "2026-05-05",
                    "polyp_count": 55,
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)

    def test_drf_thermal_zones_include_probes_and_latest_readings(self):
        Probe.objects.create(
            organization=self.organization,
            thermal_zone=self.zone,
            code="PROBE-15-A",
            probe_type=Probe.ProbeType.IMINILIDE,
        )
        DailyTemperature.objects.create(
            thermal_zone=self.zone,
            date=date(2026, 5, 4),
            average_temperature_c=15.2,
            measurement_count=24,
        )
        SalinityMeasurement.objects.create(
            thermal_zone=self.zone,
            measured_on=date(2026, 5, 4),
            salinity_psu=33.5,
            user=self.user,
        )
        self.client.login(username="tech", password="secret")

        response = self.client.get(reverse("api_thermal_zone_list"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["count"], 1)
        zone = payload["results"][0]
        self.assertEqual(zone["probes"][0]["code"], "PROBE-15-A")
        self.assertEqual(zone["latest_temperature"]["measurement_count"], 24)
        self.assertEqual(zone["latest_salinity"]["salinity_psu"], 33.5)

    def test_drf_profile_endpoint_updates_interface_language(self):
        self.client.login(username="tech", password="secret")

        response = self.client.patch(
            reverse("api_profile"),
            data=json.dumps({"interface_language": UserPreference.InterfaceLanguage.ENGLISH}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["interface_language"], UserPreference.InterfaceLanguage.ENGLISH)
        self.assertEqual(payload["organizations"][0]["name"], "Aquarium de Paris")

    def test_box_detail_api_exposes_qr_urls(self):
        self.client.login(username="tech", password="secret")

        response = self.client.get(reverse("api_box_detail", args=[self.box.id]))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["scan_url"].endswith(f"/bac/{self.box.id}/"))
        self.assertTrue(payload["qr_image_url"].endswith(f"/boites/{self.box.id}/qr.svg"))

    def test_scan_redirects_to_detail_and_logs_scan(self):
        self.client.login(username="tech", password="secret")

        response = self.client.get(reverse("scan_boite", args=[self.box.id]))

        self.assertRedirects(
            response,
            reverse("detail_boite", args=[self.box.id]),
            target_status_code=200,
        )
        self.assertEqual(
            AuditLog.objects.filter(
                action=AuditLog.Action.SCAN, object_id=self.box.global_code
            ).count(),
            1,
        )

    def test_scan_requires_login(self):
        response = self.client.get(reverse("scan_boite", args=[self.box.id]))

        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])

    def test_scan_is_scoped_to_authorized_boxes(self):
        other_box = Box.objects.get(global_code="AAU-1.001-TKY")
        self.client.login(username="tech", password="secret")

        response = self.client.get(reverse("scan_boite", args=[other_box.id]))

        self.assertEqual(response.status_code, 404)
        self.assertFalse(AuditLog.objects.filter(action=AuditLog.Action.SCAN).exists())

    def test_qr_endpoint_returns_svg(self):
        self.client.login(username="tech", password="secret")

        response = self.client.get(reverse("qr_boite", args=[self.box.id]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/svg+xml")
        self.assertIn(b"svg", response.content)

    def test_qr_endpoint_is_scoped_to_authorized_boxes(self):
        other_box = Box.objects.get(global_code="AAU-1.001-TKY")
        self.client.login(username="tech", password="secret")

        response = self.client.get(reverse("qr_boite", args=[other_box.id]))

        self.assertEqual(response.status_code, 404)
