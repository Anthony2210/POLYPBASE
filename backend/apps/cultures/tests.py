import json
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import OrganizationMembership, UserPreference
from apps.audit.models import AuditLog
from apps.organizations.models import Organization
from apps.taxonomy.models import Origin, Species, Strain
from apps.measurements.models import BiologicalMeasurement, DailyTemperature, Probe, SalinityMeasurement

from .models import Box, BoxLineage, BoxLocation, SubcultureEvent, ThermalZone


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
        self.origin = Origin.objects.create(
            source_type=Origin.SourceType.DONATION,
            origin_institution_name="Aquarium partenaire",
        )
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
            origin=self.origin,
            thermal_zone=self.zone,
            volume_liters=Decimal("0.30"),
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

    def test_drf_subculture_endpoint_creates_multiple_child_boxes(self):
        self.client.login(username="tech", password="secret")

        response = self.client.post(
            reverse("api_box_subcultures", args=[self.box.id]),
            data=json.dumps(
                {
                    "event_date": "2026-06-15",
                    "reason": "High polyp density",
                    "notes": "Two child boxes created during the same operation.",
                    "children": [
                        {
                            "global_code": "AAU-1.004-ATL",
                            "local_code": "004",
                            "box_number": "004",
                            "thermal_zone_id": self.zone.id,
                            "copy_origin": True,
                            "copy_volume_liters": True,
                        },
                        {
                            "global_code": "AAU-1.005-ATL",
                            "local_code": "005",
                            "box_number": "005",
                            "thermal_zone_id": self.zone.id,
                            "copy_origin": False,
                            "copy_volume_liters": False,
                            "notes": "Smaller experimental box.",
                        },
                    ],
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertEqual(payload["parent_box"], self.box.global_code)
        self.assertEqual(len(payload["children"]), 2)

        event = SubcultureEvent.objects.get(parent_box=self.box)
        children = Box.objects.filter(global_code__in=["AAU-1.004-ATL", "AAU-1.005-ATL"])
        self.assertEqual(children.count(), 2)
        self.assertEqual(BoxLineage.objects.filter(subculture_event=event).count(), 2)
        self.assertEqual(BoxLocation.objects.filter(box__in=children, ends_at__isnull=True).count(), 2)

        inherited_child = children.get(global_code="AAU-1.004-ATL")
        empty_child = children.get(global_code="AAU-1.005-ATL")
        self.assertEqual(inherited_child.organization, self.box.organization)
        self.assertEqual(inherited_child.strain, self.box.strain)
        self.assertEqual(inherited_child.origin, self.box.origin)
        self.assertEqual(inherited_child.volume_liters, self.box.volume_liters)
        self.assertIsNone(empty_child.origin)
        self.assertIsNone(empty_child.volume_liters)

        audit_log = AuditLog.objects.get(
            action=AuditLog.Action.SUBCULTURE,
            object_id=self.box.global_code,
        )
        self.assertEqual(len(audit_log.metadata["child_box_ids"]), 2)

    def test_drf_subculture_endpoint_blocks_read_only_users(self):
        user_model = get_user_model()
        viewer = user_model.objects.create_user(username="subculture_viewer", password="secret")
        OrganizationMembership.objects.create(
            user=viewer,
            organization=self.organization,
            role=OrganizationMembership.Role.VIEWER,
        )
        self.client.login(username="subculture_viewer", password="secret")

        response = self.client.post(
            reverse("api_box_subcultures", args=[self.box.id]),
            data=json.dumps(
                {
                    "children": [
                        {
                            "global_code": "AAU-1.004-ATL",
                            "box_number": "004",
                            "thermal_zone_id": self.zone.id,
                        }
                    ]
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(SubcultureEvent.objects.filter(parent_box=self.box).exists())

    def test_drf_subculture_endpoint_allows_organization_admins(self):
        membership = OrganizationMembership.objects.get(
            user=self.user,
            organization=self.organization,
        )
        membership.role = OrganizationMembership.Role.ADMIN
        membership.save(update_fields=["role"])
        self.client.login(username="tech", password="secret")

        response = self.client.post(
            reverse("api_box_subcultures", args=[self.box.id]),
            data=json.dumps(
                {
                    "children": [
                        {
                            "global_code": "AAU-1.004-ATL",
                            "box_number": "004",
                            "thermal_zone_id": self.zone.id,
                        }
                    ]
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertTrue(Box.objects.filter(global_code="AAU-1.004-ATL").exists())

    def test_drf_subculture_endpoint_rejects_a_zone_from_another_organization(self):
        self.client.login(username="tech", password="secret")

        response = self.client.post(
            reverse("api_box_subcultures", args=[self.box.id]),
            data=json.dumps(
                {
                    "children": [
                        {
                            "global_code": "AAU-1.004-ATL",
                            "box_number": "004",
                            "thermal_zone_id": self.other_zone.id,
                        }
                    ]
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Box.objects.filter(global_code="AAU-1.004-ATL").exists())
        self.assertFalse(SubcultureEvent.objects.filter(parent_box=self.box).exists())

    def test_subculture_transaction_rolls_back_if_lineage_creation_fails(self):
        self.client.login(username="tech", password="secret")

        with patch("apps.cultures.services.BoxLineage.objects.create", side_effect=RuntimeError("failure")):
            with self.assertRaises(RuntimeError):
                self.client.post(
                    reverse("api_box_subcultures", args=[self.box.id]),
                    data=json.dumps(
                        {
                            "children": [
                                {
                                    "global_code": "AAU-1.004-ATL",
                                    "box_number": "004",
                                    "thermal_zone_id": self.zone.id,
                                }
                            ]
                        }
                    ),
                    content_type="application/json",
                )

        self.assertFalse(Box.objects.filter(global_code="AAU-1.004-ATL").exists())
        self.assertFalse(SubcultureEvent.objects.filter(parent_box=self.box).exists())
        self.assertFalse(
            AuditLog.objects.filter(
                action=AuditLog.Action.SUBCULTURE,
                object_id=self.box.global_code,
            ).exists()
        )
