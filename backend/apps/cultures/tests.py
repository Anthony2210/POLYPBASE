import json
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

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
        self.second_zone = ThermalZone.objects.create(
            organization=self.organization,
            name="Cabinet-20",
            zone_type=ThermalZone.ZoneType.CABINET,
            target_temperature_c=20,
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

    def test_drf_box_list_allows_read_only_users_to_consult_their_organization(self):
        user_model = get_user_model()
        viewer = user_model.objects.create_user(username="box_viewer", password="secret")
        OrganizationMembership.objects.create(
            user=viewer,
            organization=self.organization,
            role=OrganizationMembership.Role.VIEWER,
        )
        self.client.login(username="box_viewer", password="secret")

        response = self.client.get(reverse("api_box_list"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 1)
        self.assertEqual(response.json()["results"][0]["id"], self.box.id)

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

    def test_box_accesses_are_saved_for_the_current_account_only(self):
        user_model = get_user_model()
        other_user = user_model.objects.create_user(username="other_tech", password="secret")
        OrganizationMembership.objects.create(
            user=other_user,
            organization=self.organization,
            role=OrganizationMembership.Role.LAB_TECHNICIAN,
        )
        AuditLog.objects.create(
            organization=self.organization,
            user=other_user,
            action=AuditLog.Action.VIEW,
            object_type="box",
            object_id=self.box.global_code,
            description="Box opened by another account.",
        )

        self.client.login(username="tech", password="secret")
        response = self.client.post(reverse("api_box_access", args=[self.box.id]))

        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            AuditLog.objects.filter(
                user=self.user,
                action=AuditLog.Action.VIEW,
                object_id=self.box.global_code,
            ).count(),
            1,
        )

        dashboard = self.client.get(reverse("api_dashboard")).json()
        self.assertEqual(len(dashboard["recent_accesses"]), 1)
        self.assertEqual(dashboard["recent_accesses"][0]["object_id"], self.box.global_code)

    def test_drf_box_detail_returns_parent_and_child_lineage(self):
        child_box = Box.objects.create(
            organization=self.organization,
            global_code="AAU-1.004-ATL",
            box_number="004",
            strain=self.strain,
            thermal_zone=self.zone,
        )
        event = SubcultureEvent.objects.create(
            parent_box=self.box,
            event_date=date(2026, 6, 15),
            user=self.user,
            reason="High polyp density",
            notes="Child box created during the weekly check.",
        )
        BoxLineage.objects.create(
            parent_box=self.box,
            child_box=child_box,
            subculture_event=event,
        )
        self.client.login(username="tech", password="secret")

        parent_response = self.client.get(reverse("api_box_detail", args=[self.box.id]))
        child_response = self.client.get(reverse("api_box_detail", args=[child_box.id]))

        self.assertEqual(parent_response.status_code, 200)
        parent_lineage = parent_response.json()["lineage"]
        self.assertEqual(parent_lineage["parents"], [])
        self.assertEqual(parent_lineage["children"][0]["box"]["global_code"], child_box.global_code)
        self.assertEqual(parent_lineage["children"][0]["event"]["event_date"], "2026-06-15")
        self.assertEqual(parent_lineage["children"][0]["event"]["user"], self.user.username)

        self.assertEqual(child_response.status_code, 200)
        child_lineage = child_response.json()["lineage"]
        self.assertEqual(child_lineage["children"], [])
        self.assertEqual(child_lineage["parents"][0]["box"]["global_code"], self.box.global_code)
        self.assertEqual(
            child_lineage["parents"][0]["event"]["reason"],
            "High polyp density",
        )

    def test_drf_box_detail_hides_lineage_from_another_organization(self):
        foreign_box = Box.objects.get(global_code="AAU-1.001-TKY")
        event = SubcultureEvent.objects.create(
            parent_box=self.box,
            event_date=date(2026, 6, 15),
            user=self.user,
        )
        BoxLineage.objects.create(
            parent_box=self.box,
            child_box=foreign_box,
            subculture_event=event,
        )
        self.client.login(username="tech", password="secret")

        response = self.client.get(reverse("api_box_detail", args=[self.box.id]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["lineage"]["children"], [])

    def test_drf_lineage_graph_returns_all_accessible_generations(self):
        child_box = Box.objects.create(
            organization=self.organization,
            global_code="AAU-1.004-ATL",
            box_number="004",
            strain=self.strain,
            thermal_zone=self.zone,
        )
        grandchild_box = Box.objects.create(
            organization=self.organization,
            global_code="AAU-1.005-ATL",
            box_number="005",
            strain=self.strain,
            thermal_zone=self.zone,
            status=Box.Status.STOPPED,
        )
        first_event = SubcultureEvent.objects.create(
            parent_box=self.box,
            event_date=date(2026, 6, 10),
            user=self.user,
        )
        second_event = SubcultureEvent.objects.create(
            parent_box=child_box,
            event_date=date(2026, 6, 15),
            user=self.user,
        )
        BoxLineage.objects.create(
            parent_box=self.box,
            child_box=child_box,
            subculture_event=first_event,
        )
        BoxLineage.objects.create(
            parent_box=child_box,
            child_box=grandchild_box,
            subculture_event=second_event,
        )
        self.client.login(username="tech", password="secret")

        response = self.client.get(reverse("api_box_lineage", args=[child_box.id]))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["root_box_id"], child_box.id)
        self.assertEqual(
            {node["global_code"] for node in payload["nodes"]},
            {self.box.global_code, child_box.global_code, grandchild_box.global_code},
        )
        self.assertEqual(len(payload["edges"]), 2)
        self.assertFalse(payload["truncated"])
        self.assertTrue(
            next(node for node in payload["nodes"] if node["id"] == child_box.id)["is_root"]
        )
        self.assertEqual(
            next(node for node in payload["nodes"] if node["id"] == grandchild_box.id)["status"],
            Box.Status.STOPPED,
        )

    def test_drf_lineage_graph_excludes_other_organizations(self):
        foreign_box = Box.objects.get(global_code="AAU-1.001-TKY")
        event = SubcultureEvent.objects.create(
            parent_box=self.box,
            event_date=date(2026, 6, 15),
            user=self.user,
        )
        BoxLineage.objects.create(
            parent_box=self.box,
            child_box=foreign_box,
            subculture_event=event,
        )
        self.client.login(username="tech", password="secret")

        response = self.client.get(reverse("api_box_lineage", args=[self.box.id]))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual([node["global_code"] for node in payload["nodes"]], [self.box.global_code])
        self.assertEqual(payload["edges"], [])

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
        self.assertEqual(payload["count"], 2)
        zone = next(item for item in payload["results"] if item["id"] == self.zone.id)
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
        self.assertEqual(payload["memberships"][0]["role"], OrganizationMembership.Role.LAB_TECHNICIAN)

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

    def test_drf_move_endpoint_moves_box_and_keeps_location_history(self):
        initial_location = BoxLocation.objects.create(
            box=self.box,
            thermal_zone=self.zone,
            starts_at=timezone.now() - timedelta(days=10),
            notes="Initial test location.",
        )
        self.client.login(username="tech", password="secret")

        response = self.client.post(
            reverse("api_box_move", args=[self.box.id]),
            data=json.dumps(
                {
                    "thermal_zone_id": self.second_zone.id,
                    "notes": "Moved after temperature adjustment.",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.box.refresh_from_db()
        initial_location.refresh_from_db()
        self.assertEqual(self.box.thermal_zone, self.second_zone)
        self.assertIsNotNone(initial_location.ends_at)
        self.assertEqual(
            BoxLocation.objects.filter(
                box=self.box,
                thermal_zone=self.second_zone,
                ends_at__isnull=True,
            ).count(),
            1,
        )
        movement = self.box.movements.get()
        self.assertEqual(movement.from_thermal_zone, self.zone)
        self.assertEqual(movement.to_thermal_zone, self.second_zone)
        self.assertEqual(movement.user, self.user)

        payload = response.json()
        self.assertEqual(payload["thermal_zone"]["name"], self.second_zone.name)
        self.assertEqual(len(payload["locations"]), 2)
        self.assertEqual(payload["movements"][0]["to_thermal_zone"]["name"], self.second_zone.name)
        self.assertEqual(
            AuditLog.objects.filter(
                action=AuditLog.Action.UPDATE,
                object_id=self.box.global_code,
                metadata__to_thermal_zone_id=self.second_zone.id,
            ).count(),
            1,
        )

    def test_drf_move_endpoint_blocks_read_only_users(self):
        user_model = get_user_model()
        viewer = user_model.objects.create_user(username="move_viewer", password="secret")
        OrganizationMembership.objects.create(
            user=viewer,
            organization=self.organization,
            role=OrganizationMembership.Role.VIEWER,
        )
        self.client.login(username="move_viewer", password="secret")

        response = self.client.post(
            reverse("api_box_move", args=[self.box.id]),
            data=json.dumps({"thermal_zone_id": self.second_zone.id}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)
        self.box.refresh_from_db()
        self.assertEqual(self.box.thermal_zone, self.zone)

    def test_drf_move_endpoint_rejects_zone_from_another_organization(self):
        self.client.login(username="tech", password="secret")

        response = self.client.post(
            reverse("api_box_move", args=[self.box.id]),
            data=json.dumps({"thermal_zone_id": self.other_zone.id}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.box.refresh_from_db()
        self.assertEqual(self.box.thermal_zone, self.zone)

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
