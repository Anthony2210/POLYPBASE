import json
from io import StringIO
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import DatabaseError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.accounts.models import OrganizationMembership, UserPreference
from apps.audit.models import Alert, AuditLog
from apps.organizations.models import Organization
from apps.taxonomy.models import Origin, Species, Strain
from apps.measurements.models import BiologicalMeasurement, DailyTemperature, Probe, SalinityMeasurement

from .models import Box, BoxLineage, BoxLocation, BoxTransfer, BoxTransferImport, IdentificationTag, SubcultureEvent, ThermalZone


class DormantAlertSeedTests(TestCase):
    def test_demo_seed_preserves_dormant_alerts(self):
        organization = Organization.objects.create(name="Historical institution")
        Alert.objects.create(
            organization=organization,
            alert_type=Alert.AlertType.OTHER,
            message="Historical evidence",
        )
        before = list(Alert.objects.order_by("pk").values())
        call_command("seed_demo_data", stdout=StringIO())
        call_command("seed_demo_data", stdout=StringIO())
        self.assertEqual(list(Alert.objects.order_by("pk").values()), before)

    def test_demo_seed_does_not_create_alerts(self):
        call_command("seed_demo_data", stdout=StringIO())
        self.assertFalse(Alert.objects.exists())


class PolypbaseApiTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username="tech", email="tech@example.org",password="secret")

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

    @patch(
        "apps.cultures.api_views.connection.cursor",
        side_effect=DatabaseError("database unavailable"),
    )
    def test_health_endpoint_reports_database_unavailability(self, _cursor):
        response = self.client.get(reverse("api_health"))

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["status"], "unavailable")
        self.assertEqual(response.json()["service"], "polypbase")

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
        self.assertIsNone(payload["results"][0]["current_location_started_at"])

    def test_drf_box_list_exposes_the_canonical_current_location_start(self):
        previous_start = timezone.now() - timedelta(days=10)
        current_start = timezone.now() - timedelta(days=3)
        BoxLocation.objects.create(
            box=self.box,
            thermal_zone=self.second_zone,
            starts_at=previous_start,
            ends_at=current_start,
        )
        BoxLocation.objects.create(
            box=self.box,
            thermal_zone=self.zone,
            starts_at=current_start,
        )
        self.client.login(username="tech", password="secret")

        response = self.client.get(reverse("api_box_list"))

        self.assertEqual(response.status_code, 200)
        value = response.json()["results"][0]["current_location_started_at"]
        self.assertEqual(parse_datetime(value), current_start)

    def test_drf_box_list_allows_read_only_users_to_consult_their_organization(self):
        user_model = get_user_model()
        viewer = user_model.objects.create_user(username="box_viewer", email="box_viewer@example.org",password="secret")
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

    @patch("apps.cultures.api_views.timezone.localdate", return_value=date(2026, 10, 1))
    def test_overview_includes_every_active_box_in_the_selected_organization(self, _localdate):
        self.client.login(username="tech", password="secret")
        app_tracked_box = Box.objects.create(
            organization=self.organization,
            global_code="AAU-1.002-ATL",
            box_number="002",
            strain=self.strain,
            origin=self.origin,
            thermal_zone=self.zone,
        )
        inactive_box = Box.objects.create(
            organization=self.organization,
            global_code="AAU-1.003-ATL",
            box_number="003",
            strain=self.strain,
            origin=self.origin,
            thermal_zone=self.zone,
            status=Box.Status.INACTIVE,
        )
        old_box = Box.objects.create(
            organization=self.organization,
            global_code="AAU-1.004-ATL",
            box_number="004",
            strain=self.strain,
            origin=self.origin,
            thermal_zone=self.zone,
        )
        unmeasured_box = Box.objects.create(
            organization=self.organization,
            global_code="AAU-1.005-ATL",
            box_number="005",
            strain=self.strain,
            origin=self.origin,
            thermal_zone=self.zone,
        )
        BiologicalMeasurement.objects.create(
            box=self.box,
            measured_on=date(2026, 1, 10),
            polyp_count=20,
        )
        BiologicalMeasurement.objects.create(
            box=app_tracked_box,
            measured_on=date(2026, 7, 1),
            polyp_count=30,
            salinity_psu=Decimal("31.50"),
            user=self.user,
        )
        BiologicalMeasurement.objects.create(
            box=inactive_box,
            measured_on=date(2026, 3, 1),
            polyp_count=10,
        )
        BiologicalMeasurement.objects.create(
            box=old_box,
            measured_on=date(2025, 12, 31),
            polyp_count=40,
        )
        BiologicalMeasurement.objects.create(
            box=Box.objects.get(global_code="AAU-1.001-TKY"),
            measured_on=date(2026, 6, 1),
            polyp_count=50,
        )
        BoxLocation.objects.create(
            box=app_tracked_box,
            thermal_zone=self.zone,
            starts_at=timezone.now() - timedelta(days=10),
        )

        response = self.client.get(reverse("api_overview_active_boxes"))

        self.assertEqual(response.status_code, 200)
        boxes_by_code = {box["global_code"]: box for box in response.json()["results"]}
        self.assertEqual(
            set(boxes_by_code),
            {
                self.box.global_code,
                app_tracked_box.global_code,
                old_box.global_code,
                unmeasured_box.global_code,
            },
        )
        self.assertFalse(boxes_by_code[self.box.global_code]["tracked_in_app"])
        self.assertTrue(boxes_by_code[app_tracked_box.global_code]["tracked_in_app"])
        self.assertEqual(boxes_by_code[self.box.global_code]["measurements"], [])
        self.assertEqual(
            boxes_by_code[app_tracked_box.global_code]["measurements"][0]["salinity_psu"],
            "31.50",
        )
        self.assertEqual(
            boxes_by_code[app_tracked_box.global_code]["locations"][0]["thermal_zone"]["name"],
            self.zone.name,
        )
        self.assertEqual(boxes_by_code[old_box.global_code]["measurements"], [])
        self.assertEqual(boxes_by_code[unmeasured_box.global_code]["measurements"], [])

    def test_overview_never_exposes_location_from_another_organization(self):
        now = timezone.now()
        local_location = BoxLocation.objects.create(
            box=self.box,
            thermal_zone=self.zone,
            starts_at=now - timedelta(days=20),
            ends_at=now - timedelta(days=10),
            notes="Local location notes",
        )
        BoxLocation.objects.create(
            box=self.box,
            thermal_zone=self.other_zone,
            starts_at=now - timedelta(days=5),
            ends_at=now - timedelta(days=1),
            notes="Foreign location notes",
        )
        self.client.login(username="tech", password="secret")

        response = self.client.get(
            reverse("api_overview_active_boxes"),
            HTTP_X_ORGANIZATION_ID=str(self.organization.id),
        )

        self.assertEqual(response.status_code, 200)
        overview_box = next(
            item for item in response.json()["results"] if item["id"] == self.box.id
        )
        locations = overview_box["locations"]
        self.assertEqual([location["id"] for location in locations], [local_location.id])
        self.assertNotIn(
            self.other_zone.id,
            [location["thermal_zone"]["id"] for location in locations],
        )
        self.assertNotIn(
            self.other_zone.name,
            [location["thermal_zone"]["name"] for location in locations],
        )
        self.assertNotIn(
            "Foreign location notes",
            [location["notes"] for location in locations],
        )

    def test_lab_technician_can_create_box_directly(self):
        self.client.login(username="tech", password="secret")

        response = self.client.post(
            reverse("api_box_list"),
            data=json.dumps(
                {
                    "strain": self.strain.id,
                    "thermal_zone": self.zone.id,
                    "global_code": "1-ATL.002",
                    "local_code": "",
                    "box_number": "002",
                    "entered_on": "2026-07-16",
                    "volume_liters": "0.30",
                    "notes": "Création manuelle.",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        created_box = Box.objects.get(global_code="1-ATL.002")
        self.assertEqual(created_box.organization, self.organization)
        self.assertEqual(created_box.status, Box.Status.ACTIVE)
        self.assertEqual(created_box.thermal_zone, self.zone)
        self.assertTrue(BoxLocation.objects.filter(box=created_box, thermal_zone=self.zone).exists())
        self.assertTrue(
            AuditLog.objects.filter(
                organization=self.organization,
                user=self.user,
                action=AuditLog.Action.CREATION,
                object_id=created_box.global_code,
            ).exists()
        )

    def test_manual_box_creation_rolls_back_box_and_location_when_audit_fails(self):
        self.client.force_login(self.user)
        box_location_count = BoxLocation.objects.count()
        global_code = "1-ATL.002"

        with patch(
            "apps.cultures.api_views.AuditLog.objects.create",
            side_effect=RuntimeError("forced audit failure"),
        ), self.assertRaises(RuntimeError):
            self.client.post(
                reverse("api_box_list"),
                data=json.dumps(
                    {
                        "strain": self.strain.id,
                        "thermal_zone": self.zone.id,
                        "global_code": global_code,
                        "local_code": "",
                        "box_number": "002",
                        "entered_on": "2026-07-16",
                        "volume_liters": "0.30",
                        "notes": "Création manuelle.",
                    }
                ),
                content_type="application/json",
            )

        self.assertFalse(Box.objects.filter(global_code=global_code).exists())
        self.assertEqual(BoxLocation.objects.count(), box_location_count)

    def test_viewer_cannot_create_box_directly(self):
        user_model = get_user_model()
        viewer = user_model.objects.create_user(username="box_viewer", email="box_viewer@example.org",password="secret")
        OrganizationMembership.objects.create(
            user=viewer,
            organization=self.organization,
            role=OrganizationMembership.Role.VIEWER,
        )
        self.client.login(username="box_viewer", password="secret")

        response = self.client.post(
            reverse("api_box_list"),
            data=json.dumps(
                {
                    "strain": self.strain.id,
                    "thermal_zone": self.zone.id,
                    "global_code": "1-ATL.002",
                    "box_number": "002",
                    "entered_on": "2026-07-16",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Box.objects.filter(global_code="1-ATL.002").exists())

    def test_create_box_ignores_submitted_organization_and_uses_active_context(self):
        self.client.login(username="tech", password="secret")

        response = self.client.post(
            reverse("api_box_list"),
            data=json.dumps(
                {
                    "organization": self.other_organization.id,
                    "strain": self.strain.id,
                    "thermal_zone": self.zone.id,
                    "global_code": "1-ATL.002",
                    "box_number": "002",
                    "entered_on": "2026-07-16",
                }
            ),
            content_type="application/json",
            HTTP_X_ORGANIZATION_ID=str(self.organization.id),
        )

        self.assertEqual(response.status_code, 201)
        created_box = Box.objects.get(global_code="1-ATL.002")
        self.assertEqual(created_box.organization, self.organization)

    def test_create_box_rejects_duplicate_global_code_with_french_error(self):
        self.client.login(username="tech", password="secret")

        response = self.client.post(
            reverse("api_box_list"),
            data=json.dumps(
                {
                    "strain": self.strain.id,
                    "thermal_zone": self.zone.id,
                    "global_code": self.box.global_code,
                    "box_number": "001",
                    "entered_on": "2026-07-16",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Une boîte utilise déjà ce code.", response.json()["global_code"])

    def test_create_box_rejects_number_that_does_not_match_global_code(self):
        self.client.login(username="tech", password="secret")

        response = self.client.post(
            reverse("api_box_list"),
            data=json.dumps(
                {
                    "strain": self.strain.id,
                    "thermal_zone": self.zone.id,
                    "global_code": "1-ATL.004",
                    "box_number": "005",
                    "entered_on": "2026-07-16",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn(
            "Le numéro doit correspondre au numéro présent dans le code boîte.",
            response.json()["box_number"],
        )
        self.assertFalse(Box.objects.filter(global_code="1-ATL.004").exists())

    def test_normalize_box_codes_updates_legacy_prefix_safely(self):
        legacy_box = Box.objects.create(
            organization=self.organization,
            global_code="ATL-AAU-1.009",
            box_number="009",
            strain=self.strain,
            origin=self.origin,
            thermal_zone=self.zone,
        )
        IdentificationTag.objects.create(
            box=legacy_box,
            tag_type=IdentificationTag.TagType.QR,
            code="QR-ATL-AAU-1.009",
        )
        AuditLog.objects.create(
            organization=self.organization,
            action=AuditLog.Action.UPDATE,
            object_type="box",
            object_id="ATL-AAU-1.009",
            description="Legacy code update",
        )

        call_command("normalize_box_codes", apply=True, stdout=StringIO())

        legacy_box.refresh_from_db()
        self.assertEqual(legacy_box.global_code, "1-ATL.009")
        self.assertTrue(IdentificationTag.objects.filter(code="QR-1-ATL.009").exists())
        self.assertTrue(AuditLog.objects.filter(object_type="box", object_id="1-ATL.009").exists())

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

    def test_admin_can_mark_box_inactive_without_deleting_history(self):
        user_model = get_user_model()
        admin = user_model.objects.create_user(username="box_admin", email="box_admin@example.org",password="secret")
        OrganizationMembership.objects.create(
            user=admin,
            organization=self.organization,
            role=OrganizationMembership.Role.ADMIN,
        )
        BiologicalMeasurement.objects.create(
            box=self.box,
            measured_on=date(2026, 5, 4),
            polyp_count=42,
            user=self.user,
        )
        self.client.login(username="box_admin", password="secret")

        initial_location = BoxLocation.objects.create(
            box=self.box,
            thermal_zone=self.zone,
            starts_at=timezone.now() - timedelta(days=3),
        )
        response = self.client.post(
            reverse("api_box_archive", args=[self.box.id]),
            data={"reason": "Culture terminée."},
        )

        self.assertEqual(response.status_code, 200)
        self.box.refresh_from_db()
        initial_location.refresh_from_db()
        self.assertEqual(self.box.status, Box.Status.INACTIVE)
        self.assertIsNone(self.box.thermal_zone)
        self.assertIsNotNone(initial_location.ends_at)
        self.assertEqual(self.box.stop_reason, "Culture terminée.")
        self.assertTrue(BiologicalMeasurement.objects.filter(box=self.box).exists())
        self.assertTrue(
            AuditLog.objects.filter(
                organization=self.organization,
                user=admin,
                action=AuditLog.Action.UPDATE,
                object_id=self.box.global_code,
            ).exists()
        )

    def test_technician_cannot_mark_box_inactive(self):
        self.client.login(username="tech", password="secret")

        response = self.client.post(
            reverse("api_box_archive", args=[self.box.id]),
            data={"reason": "Culture terminée."},
        )

        self.assertEqual(response.status_code, 403)
        self.box.refresh_from_db()
        self.assertEqual(self.box.status, Box.Status.ACTIVE)

    def test_admin_can_reactivate_inactive_box(self):
        user_model = get_user_model()
        admin = user_model.objects.create_user(username="box_admin", email="box_admin@example.org",password="secret")
        OrganizationMembership.objects.create(
            user=admin,
            organization=self.organization,
            role=OrganizationMembership.Role.ADMIN,
        )
        self.box.status = Box.Status.INACTIVE
        self.box.thermal_zone = None
        self.box.stop_reason = "Erreur de manipulation."
        self.box.save(update_fields=["status", "thermal_zone", "stop_reason"])
        self.client.login(username="box_admin", password="secret")

        response = self.client.post(
            reverse("api_box_activate", args=[self.box.id]),
            data={"thermal_zone_id": self.second_zone.id},
        )

        self.assertEqual(response.status_code, 200)
        self.box.refresh_from_db()
        self.assertEqual(self.box.status, Box.Status.ACTIVE)
        self.assertEqual(self.box.thermal_zone, self.second_zone)
        self.assertEqual(self.box.stop_reason, "")
        self.assertTrue(
            AuditLog.objects.filter(
                organization=self.organization,
                user=admin,
                action=AuditLog.Action.UPDATE,
                object_id=self.box.global_code,
            ).exists()
        )

    def test_box_accesses_are_saved_for_the_current_account_only(self):
        user_model = get_user_model()
        other_user = user_model.objects.create_user(username="other_tech", email="other_tech@example.org",password="secret")
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
            status=Box.Status.INACTIVE,
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
            Box.Status.INACTIVE,
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

    def _historical_alerts(self):
        for alert_type, relations in (
            (Alert.AlertType.BIOLOGICAL, {"box": self.box}),
            (Alert.AlertType.TEMPERATURE, {"thermal_zone": self.zone}),
        ):
            for resolved_at in (None, timezone.now()):
                Alert.objects.create(
                    organization=self.organization,
                    alert_type=alert_type,
                    message="Historical evidence",
                    created_by=self.user,
                    resolved_at=resolved_at,
                    resolved_by=self.user if resolved_at else None,
                    **relations,
                )
        return list(Alert.objects.order_by("pk").values())

    def test_polyp_drop_zero_and_recovery_leave_historical_alerts_unchanged(self):
        BiologicalMeasurement.objects.create(
            box=self.box, measured_on=date(2026, 5, 1), polyp_count=80,
        )
        self.client.login(username="tech", password="secret")
        for history in (False, True):
            before = self._historical_alerts() if history else []
            first_date = date(2026, 6, 8) if history else date(2026, 5, 8)
            for week, count in enumerate((65, 0, 82)):
                measured_on = (first_date + timedelta(weeks=week)).isoformat()
                response = self.client.post(
                    reverse("api_box_measurements", args=[self.box.id]),
                    data=json.dumps({
                        "measured_on": measured_on,
                        "polyp_count": count,
                        "ephyrae_count": 0,
                    }),
                    content_type="application/json",
                )
                self.assertEqual(response.status_code, 201)
                measurement = BiologicalMeasurement.objects.get(pk=response.json()["id"])
                self.assertEqual(measurement.polyp_count, count)
                self.assertEqual(measurement.ephyrae_count, 0)
                audit = AuditLog.objects.get(metadata__measurement_id=measurement.pk)
                self.assertEqual(audit.action, AuditLog.Action.ENTRY)
                self.assertEqual(audit.metadata["valeurs"]["polypes"], count)
                self.assertEqual(list(Alert.objects.order_by("pk").values()), before)

    def test_alerts_are_not_exposed_in_dashboard_or_box_payloads(self):
        before = self._historical_alerts()
        self.client.login(username="tech", password="secret")
        dashboard = self.client.get(reverse("api_dashboard"))
        self.assertEqual(dashboard.status_code, 200)
        self.assertNotIn("alerts", dashboard.json())
        self.assertNotIn("active_alerts", dashboard.json()["stats"])
        listing = self.client.get(reverse("api_box_list"))
        self.assertEqual(listing.status_code, 200)
        for box in listing.json()["results"]:
            self.assertNotIn("active_alert_count", box)
            self.assertNotIn("active_alerts", box)
        detail = self.client.get(reverse("api_box_detail", args=[self.box.id]))
        self.assertEqual(detail.status_code, 200)
        self.assertNotIn("active_alert_count", detail.json())
        self.assertNotIn("active_alerts", detail.json())
        self.assertEqual(list(Alert.objects.order_by("pk").values()), before)

    def test_alert_resolution_route_and_admin_are_removed(self):
        from django.contrib import admin
        from django.urls import NoReverseMatch, Resolver404, resolve

        before = self._historical_alerts()
        with self.assertRaises(NoReverseMatch):
            reverse("api_alert_resolve", args=[before[0]["id"]])
        with self.assertRaises(Resolver404):
            resolve(f"/alerts/{before[0]['id']}/resolve/", urlconf="config.api_urls")
        self.assertNotIn(Alert, admin.site._registry)
        self.assertIn(AuditLog, admin.site._registry)
        self.assertEqual(list(Alert.objects.order_by("pk").values()), before)

    def test_manual_temperature_deviation_zero_and_recovery_do_not_sync_alerts(self):
        self.client.login(username="tech", password="secret")
        url = reverse("api_thermal_zone_manual_temperature", args=[self.zone.id])
        for history in (False, True):
            before = self._historical_alerts() if history else []
            measured_on = "2026-05-06" if history else "2026-05-05"
            for temperature in ("14.00", "0.00", "15.00"):
                response = self.client.post(
                    url,
                    data=json.dumps({
                        "measured_on": measured_on,
                        "temperature_c": temperature,
                    }),
                    content_type="application/json",
                )
                self.assertEqual(response.status_code, 201)
                self.assertEqual(list(Alert.objects.order_by("pk").values()), before)
            aggregate = DailyTemperature.objects.get(thermal_zone=self.zone, date=measured_on)
            self.assertEqual(aggregate.measurement_count, 3)
            self.assertEqual(aggregate.min_temperature_c, Decimal("0.00"))
            self.assertEqual(aggregate.max_temperature_c, Decimal("15.00"))
            self.assertEqual(aggregate.average_temperature_c, Decimal("9.67"))
        self.assertEqual(
            AuditLog.objects.filter(description__startswith="Manual temperature recorded:").count(),
            6,
        )

    def test_manual_temperatures_update_the_daily_aggregate_exactly(self):
        measured_on = date(2026, 5, 7)
        DailyTemperature.objects.create(
            thermal_zone=self.zone,
            date=measured_on,
            min_temperature_c=Decimal("10.00"),
            average_temperature_c=Decimal("10.00"),
            max_temperature_c=Decimal("10.00"),
            measurement_count=1,
        )
        self.client.login(username="tech", password="secret")
        url = reverse("api_thermal_zone_manual_temperature", args=[self.zone.id])

        for temperature_c in ("20.00", "30.00"):
            response = self.client.post(
                url,
                data=json.dumps(
                    {
                        "measured_on": measured_on.isoformat(),
                        "temperature_c": temperature_c,
                    }
                ),
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 201)

        aggregate = DailyTemperature.objects.get(
            thermal_zone=self.zone,
            date=measured_on,
        )
        self.assertEqual(aggregate.measurement_count, 3)
        self.assertEqual(aggregate.average_temperature_c, Decimal("20.00"))
        self.assertEqual(aggregate.min_temperature_c, Decimal("10.00"))
        self.assertEqual(aggregate.max_temperature_c, Decimal("30.00"))

    def test_manual_temperature_rolls_back_when_audit_creation_fails(self):
        measured_on = date(2026, 5, 7)
        aggregate = DailyTemperature.objects.create(
            thermal_zone=self.zone,
            date=measured_on,
            min_temperature_c=Decimal("10.00"),
            average_temperature_c=Decimal("10.00"),
            max_temperature_c=Decimal("10.00"),
            measurement_count=1,
        )
        before = self._historical_alerts()
        self.client.login(username="tech", password="secret")

        with patch(
            "apps.cultures.api_views.AuditLog.objects.create",
            side_effect=RuntimeError("forced audit failure"),
        ), self.assertRaises(RuntimeError):
            self.client.post(
                reverse("api_thermal_zone_manual_temperature", args=[self.zone.id]),
                data=json.dumps(
                    {
                        "measured_on": measured_on.isoformat(),
                        "temperature_c": "20.00",
                    }
                ),
                content_type="application/json",
            )

        aggregate.refresh_from_db()
        self.assertEqual(aggregate.measurement_count, 1)
        self.assertEqual(aggregate.average_temperature_c, Decimal("10.00"))
        self.assertEqual(aggregate.min_temperature_c, Decimal("10.00"))
        self.assertEqual(aggregate.max_temperature_c, Decimal("10.00"))
        self.assertEqual(list(Alert.objects.order_by("pk").values()), before)
        self.assertFalse(
            AuditLog.objects.filter(
                object_type="thermal_zone",
                object_id=self.zone.name,
            ).exists()
        )

    def test_drf_measurement_endpoint_blocks_read_only_users(self):
        user_model = get_user_model()
        viewer = user_model.objects.create_user(username="viewer", email="viewer@example.org",password="secret")
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

    def test_transfer_records_the_transmitted_polyp_count_and_preparer(self):
        membership = OrganizationMembership.objects.get(
            user=self.user,
            organization=self.organization,
        )
        membership.role = OrganizationMembership.Role.ADMIN
        membership.save(update_fields=["role"])
        self.user.first_name = "Camille"
        self.user.last_name = "Martin"
        self.user.save(update_fields=["first_name", "last_name"])
        self.client.login(username="tech", password="secret")

        response = self.client.post(
            reverse("api_box_transfer_create"),
            data=json.dumps({
                "box": self.box.id,
                "to_organization": self.other_organization.id,
                "polyp_count": 75,
                "notes": "Transport à 15 °C",
            }),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        transfer = BoxTransfer.objects.get(box=self.box)
        self.assertEqual(transfer.polyp_count, 75)
        self.assertEqual(transfer.user, self.user)
        self.assertEqual(response.json()["prepared_by"], "Camille MARTIN")

    def test_transfer_rejects_a_zero_polyp_count(self):
        membership = OrganizationMembership.objects.get(
            user=self.user,
            organization=self.organization,
        )
        membership.role = OrganizationMembership.Role.ADMIN
        membership.save(update_fields=["role"])
        self.client.login(username="tech", password="secret")

        response = self.client.post(
            reverse("api_box_transfer_create"),
            data=json.dumps({
                "box": self.box.id,
                "to_organization": self.other_organization.id,
                "polyp_count": 0,
            }),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(BoxTransfer.objects.exists())

    def test_transfer_csv_payload_creates_destination_box_and_prevents_duplicate_import(self):
        membership = OrganizationMembership.objects.get(user=self.user, organization=self.organization)
        membership.role = OrganizationMembership.Role.ADMIN
        membership.save(update_fields=["role"])
        self.client.login(username="tech", password="secret")
        source_data = {
            "format": "polypbase.box_transfer.v1",
            "transfer_id": "TR-42",
            "source_organization_name": "Aquarium de Tokyo",
            "source_global_code": "TKY-AAU-9.001",
            "species_scientific_name": "Chrysaora pacifica",
            "species_common_name": "Japanese sea nettle",
            "species_code": "CPA",
            "strain_code": "7-TKY",
            "strain_origin_code": "TKY",
            "transferred_polyp_count": "75",
            "latest_culture_status": "good",
        }
        payload = {
            "source_data": source_data,
            "organization": self.organization.id,
            "thermal_zone": self.zone.id,
            "global_code": "7-TKY.001",
        }

        response = self.client.post(
            reverse("api_box_transfer_import"),
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        imported_box = Box.objects.get(global_code="7-TKY.001")
        self.assertNotEqual(imported_box.id, self.box.id)
        self.assertEqual(imported_box.biological_measurements.get().polyp_count, 75)
        transfer_import = BoxTransferImport.objects.get(created_box=imported_box)
        self.assertEqual(transfer_import.source_global_code, "TKY-AAU-9.001")
        self.assertTrue(AuditLog.objects.filter(action=AuditLog.Action.IMPORT, object_id=imported_box.global_code).exists())

        second_payload = {
            **payload,
            "global_code": "7-TKY.002",
            "source_data": {
                **source_data,
                "transfer_id": "TR-43",
                "source_global_code": "TKY-AAU-9.002",
            },
        }
        second = self.client.post(
            reverse("api_box_transfer_import"),
            data=json.dumps(second_payload),
            content_type="application/json",
        )
        self.assertEqual(second.status_code, 201)
        self.assertTrue(Box.objects.filter(global_code="7-TKY.002", box_number="002").exists())

        conflicting_payload = {
            **payload,
            "source_data": {**source_data, "transfer_id": "TR-44"},
        }
        conflict = self.client.post(
            reverse("api_box_transfer_import"),
            data=json.dumps(conflicting_payload),
            content_type="application/json",
        )
        self.assertEqual(conflict.status_code, 400)
        self.assertIn("Suggestion : 7-TKY.003", str(conflict.json()))

        duplicate = self.client.post(
            reverse("api_box_transfer_import"),
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(duplicate.status_code, 400)
        self.assertFalse(Box.objects.filter(global_code="7-TKY.003").exists())

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

    def _subculture_revision(self):
        from .polyp_state import resolve_current_polyp_state
        BiologicalMeasurement.objects.get_or_create(
            box=self.box, measured_on=timezone.localdate(), defaults={"polyp_count": 100, "user": self.user},
        )
        self.box.refresh_from_db()
        return resolve_current_polyp_state(self.box)["revision"]

    def test_drf_subculture_endpoint_creates_multiple_child_boxes(self):
        self.client.login(username="tech", password="secret")
        Box.objects.create(organization=self.organization, strain=self.strain, global_code="1-ATL.003", box_number="003")

        response = self.client.post(
            reverse("api_box_subcultures", args=[self.box.id]),
            data=json.dumps(
                {
                    "expected_current_state_revision": self._subculture_revision(),
                    "reason": "High polyp density",
                    "notes": "Two child boxes created during the same operation.",
                    "children": [
                        {
                            "local_code": "004",
                            "thermal_zone_id": self.zone.id,
                            "copy_origin": True,
                            "allocated_polyps": 50,
                        },
                        {
                            "local_code": "005",
                            "thermal_zone_id": self.zone.id,
                            "copy_origin": False,
                            "allocated_polyps": 25,
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
        children = Box.objects.filter(global_code__in=["1-ATL.004", "1-ATL.005"])
        self.assertEqual(children.count(), 2)
        self.assertEqual(BoxLineage.objects.filter(subculture_event=event).count(), 2)
        self.assertEqual(BoxLocation.objects.filter(box__in=children, ends_at__isnull=True).count(), 2)

        inherited_child = children.get(global_code="1-ATL.004")
        empty_child = children.get(global_code="1-ATL.005")
        self.assertEqual(inherited_child.organization, self.box.organization)
        self.assertEqual(inherited_child.strain, self.box.strain)
        self.assertEqual(inherited_child.origin, self.box.origin)
        self.assertIsNone(empty_child.origin)
        self.assertIsNone(empty_child.volume_liters)
        self.assertFalse(BiologicalMeasurement.objects.filter(box__in=children).exists())
        self.assertEqual(inherited_child.subculture_initialization.allocated_polyps, 50)
        self.assertEqual(empty_child.subculture_initialization.allocated_polyps, 25)

        audit_log = AuditLog.objects.get(
            action=AuditLog.Action.SUBCULTURE,
            object_id=self.box.global_code,
        )
        self.assertEqual(len(audit_log.metadata["child_box_ids"]), 2)
        self.assertEqual(audit_log.metadata["allocations"][0]["allocated_polyps"], 50)
        self.assertEqual(audit_log.metadata["parent_polyp_count_after"], 25)

    def test_drf_subculture_endpoint_blocks_read_only_users(self):
        user_model = get_user_model()
        viewer = user_model.objects.create_user(username="subculture_viewer", email="subculture_viewer@example.org",password="secret")
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
                            "global_code": "1-ATL.004",
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
                    "expected_current_state_revision": self._subculture_revision(),
                    "children": [
                        {
                            "allocated_polyps": 0,
                            "thermal_zone_id": self.zone.id,
                        }
                    ]
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertTrue(Box.objects.filter(global_code="1-ATL.001").exists())

    def test_drf_subculture_endpoint_rejects_a_zone_from_another_organization(self):
        self.client.login(username="tech", password="secret")
        revision = self._subculture_revision()

        response = self.client.post(
            reverse("api_box_subcultures", args=[self.box.id]),
            data=json.dumps(
                {
                    "expected_current_state_revision": revision,
                    "children": [
                        {
                            "allocated_polyps": 0,
                            "thermal_zone_id": self.other_zone.id,
                        }
                    ]
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Box.objects.filter(global_code="1-ATL.004").exists())
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
                    "expected_thermal_zone_id": self.zone.id,
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
                ends_at__isnull=True,
                end_date_unknown=False,
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

    def test_drf_move_endpoint_requires_expected_location(self):
        self.client.login(username="tech", password="secret")

        response = self.client.post(
            reverse("api_box_move", args=[self.box.id]),
            data=json.dumps({"thermal_zone_id": self.second_zone.id}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("expected_thermal_zone_id", response.json())
        self.box.refresh_from_db()
        self.assertEqual(self.box.thermal_zone, self.zone)
        self.assertEqual(self.box.locations.count(), 0)
        self.assertEqual(self.box.movements.count(), 0)
        self.assertEqual(AuditLog.objects.filter(object_id=self.box.global_code).count(), 0)

    def test_drf_move_endpoint_accepts_explicit_null_for_unlocated_box(self):
        self.box.thermal_zone = None
        self.box.save(update_fields=["thermal_zone"])
        self.client.login(username="tech", password="secret")

        response = self.client.post(
            reverse("api_box_move", args=[self.box.id]),
            data=json.dumps(
                {
                    "expected_thermal_zone_id": None,
                    "thermal_zone_id": self.second_zone.id,
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.box.refresh_from_db()
        self.assertEqual(self.box.thermal_zone, self.second_zone)
        self.assertEqual(self.box.locations.filter(ends_at__isnull=True).count(), 1)
        movement = self.box.movements.get()
        self.assertIsNone(movement.from_thermal_zone)
        self.assertEqual(movement.to_thermal_zone, self.second_zone)
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
        viewer = user_model.objects.create_user(username="move_viewer", email="move_viewer@example.org",password="secret")
        OrganizationMembership.objects.create(
            user=viewer,
            organization=self.organization,
            role=OrganizationMembership.Role.VIEWER,
        )
        self.client.login(username="move_viewer", password="secret")

        response = self.client.post(
            reverse("api_box_move", args=[self.box.id]),
            data=json.dumps(
                {
                    "expected_thermal_zone_id": self.zone.id,
                    "thermal_zone_id": self.second_zone.id,
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)
        self.box.refresh_from_db()
        self.assertEqual(self.box.thermal_zone, self.zone)

    def test_drf_move_endpoint_rejects_zone_from_another_organization(self):
        self.client.login(username="tech", password="secret")

        response = self.client.post(
            reverse("api_box_move", args=[self.box.id]),
            data=json.dumps(
                {
                    "expected_thermal_zone_id": self.zone.id,
                    "thermal_zone_id": self.other_zone.id,
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.box.refresh_from_db()
        self.assertEqual(self.box.thermal_zone, self.zone)

    def test_move_endpoint_rejects_a_stale_source_location(self):
        initial_location = BoxLocation.objects.create(
            box=self.box,
            thermal_zone=self.zone,
            starts_at=timezone.now() - timedelta(days=10),
            ends_at=timezone.now() - timedelta(days=1),
        )
        BoxLocation.objects.create(
            box=self.box,
            thermal_zone=self.second_zone,
            starts_at=timezone.now() - timedelta(days=1),
        )
        self.box.thermal_zone = self.second_zone
        self.box.save(update_fields=["thermal_zone"])
        target_zone = ThermalZone.objects.create(
            organization=self.organization,
            name="Cabinet-25",
            target_temperature_c=25,
        )
        self.client.login(username="tech", password="secret")

        response = self.client.post(
            reverse("api_box_move", args=[self.box.id]),
            data=json.dumps(
                {
                    "expected_thermal_zone_id": self.zone.id,
                    "thermal_zone_id": target_zone.id,
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "box_location_changed")
        self.assertEqual(
            response.json()["current_thermal_zone_id"],
            self.second_zone.id,
        )
        self.box.refresh_from_db()
        initial_location.refresh_from_db()
        self.assertEqual(self.box.thermal_zone, self.second_zone)
        self.assertEqual(self.box.movements.count(), 0)
        self.assertEqual(self.box.locations.count(), 2)
        self.assertEqual(AuditLog.objects.filter(object_id=self.box.global_code).count(), 0)

    def test_move_rolls_back_every_write_when_audit_creation_fails(self):
        initial_location = BoxLocation.objects.create(
            box=self.box,
            thermal_zone=self.zone,
            starts_at=timezone.now() - timedelta(days=10),
        )
        self.client.login(username="tech", password="secret")

        with patch(
            "apps.cultures.services.AuditLog.objects.create",
            side_effect=RuntimeError("forced audit failure"),
        ), self.assertRaises(RuntimeError):
            self.client.post(
                reverse("api_box_move", args=[self.box.id]),
                data=json.dumps(
                    {
                        "expected_thermal_zone_id": self.zone.id,
                        "thermal_zone_id": self.second_zone.id,
                    }
                ),
                content_type="application/json",
            )

        self.box.refresh_from_db()
        initial_location.refresh_from_db()
        self.assertEqual(self.box.thermal_zone, self.zone)
        self.assertIsNone(initial_location.ends_at)
        self.assertEqual(self.box.locations.count(), 1)
        self.assertEqual(self.box.movements.count(), 0)
        self.assertEqual(AuditLog.objects.filter(object_id=self.box.global_code).count(), 0)

    def test_subculture_transaction_rolls_back_if_lineage_creation_fails(self):
        self.client.login(username="tech", password="secret")
        revision = self._subculture_revision()

        with patch("apps.cultures.services.BoxLineage.objects.create", side_effect=RuntimeError("failure")):
            with self.assertRaises(RuntimeError):
                self.client.post(
                    reverse("api_box_subcultures", args=[self.box.id]),
                    data=json.dumps(
                        {
                            "expected_current_state_revision": revision,
                            "children": [
                                {
                                    "allocated_polyps": 0,
                                    "thermal_zone_id": self.zone.id,
                                }
                            ]
                        }
                    ),
                    content_type="application/json",
                )

        self.assertFalse(Box.objects.filter(global_code="1-ATL.004").exists())
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

    def test_scan_hands_off_to_react_before_resolution(self):
        """A scanned QR code must open the React app, never a server-rendered page."""
        self.client.login(username="tech", password="secret")

        response = self.client.get(reverse("scan_boite", args=[self.box.id]))

        self.assertEqual(response.status_code, 302)
        # The SPA supplies the organization header before resolving the ID.
        self.assertRedirects(
            response,
            f"/?scan_box={self.box.id}",
            fetch_redirect_response=False,
        )
        self.assertEqual(
            AuditLog.objects.filter(
                action=AuditLog.Action.SCAN, object_id=self.box.global_code
            ).count(),
            0,
        )

    def test_scan_requires_login_and_sends_to_the_react_login(self):
        response = self.client.get(reverse("scan_boite", args=[self.box.id]))

        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response["Location"])
        # The scan target is preserved so the user lands on the box after login.
        self.assertIn(f"/bac/{self.box.id}/", response["Location"])

    def test_scan_is_scoped_to_authorized_boxes(self):
        other_box = Box.objects.get(global_code="AAU-1.001-TKY")
        self.client.login(username="tech", password="secret")

        response = self.client.post(
            reverse("api_box_scan", args=[other_box.id]),
            HTTP_X_ORGANIZATION_ID=str(self.organization.id),
        )

        self.assertEqual(response.status_code, 404)
        self.assertFalse(AuditLog.objects.filter(action=AuditLog.Action.SCAN).exists())

    @patch("apps.cultures.views.qr.render_qr_svg", return_value=b"<svg></svg>")
    def test_qr_endpoint_returns_svg_for_the_current_public_app_address(self, render_qr_svg):
        self.client.login(username="tech", password="secret")

        response = self.client.get(
            reverse("qr_boite", args=[self.box.id]),
            {"public_base_url": "https://polypbase-demo.trycloudflare.com"},
            HTTP_X_ORGANIZATION_ID=str(self.organization.id),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/svg+xml")
        self.assertIn(b"svg", response.content)
        render_qr_svg.assert_called_once_with(
            f"https://polypbase-demo.trycloudflare.com/bac/{self.box.id}/"
        )

    def test_qr_endpoint_is_scoped_to_authorized_boxes(self):
        other_box = Box.objects.get(global_code="AAU-1.001-TKY")
        self.client.login(username="tech", password="secret")

        response = self.client.get(
            reverse("qr_boite", args=[other_box.id]),
            HTTP_X_ORGANIZATION_ID=str(self.organization.id),
        )

        self.assertEqual(response.status_code, 404)

    def test_qr_resolution_uses_active_context_not_all_memberships(self):
        OrganizationMembership.objects.create(
            user=self.user,
            organization=self.other_organization,
            role=OrganizationMembership.Role.VIEWER,
        )
        other_box = Box.objects.get(global_code="AAU-1.001-TKY")
        self.client.force_login(self.user)
        for organization, allowed, refused in (
            (self.organization, self.box, other_box),
            (self.other_organization, other_box, self.box),
        ):
            with self.subTest(organization=organization.id):
                headers = {"HTTP_X_ORGANIZATION_ID": str(organization.id)}
                scan_count = AuditLog.objects.filter(action=AuditLog.Action.SCAN).count()
                response = self.client.post(reverse("api_box_scan", args=[refused.id]), **headers)
                self.assertEqual(response.status_code, 404)
                self.assertNotIn(refused.global_code, response.content.decode())
                self.assertEqual(
                    AuditLog.objects.filter(action=AuditLog.Action.SCAN).count(), scan_count,
                )
                svg = self.client.get(reverse("qr_boite", args=[refused.id]), **headers)
                self.assertEqual(svg.status_code, 404)
                self.assertNotIn(refused.global_code, svg.content.decode())

                response = self.client.post(reverse("api_box_scan", args=[allowed.id]), **headers)
                self.assertEqual(response.status_code, 201)
                self.assertEqual(response.json(), {"global_code": allowed.global_code})
                audit = AuditLog.objects.get(action=AuditLog.Action.SCAN, object_id=allowed.global_code)
                self.assertEqual(audit.organization_id, organization.id)
                self.assertEqual(audit.user_id, self.user.id)
                self.assertEqual(audit.metadata, {"box_id": allowed.id, "source": "qr_link"})
                self.assertEqual(audit.description, f"QR scan of {allowed.global_code}")
                svg = self.client.get(reverse("qr_boite", args=[allowed.id]), **headers)
                self.assertEqual(svg.status_code, 200)
                self.assertEqual(svg["Content-Type"], "image/svg+xml")
                self.assertIn(b"svg", svg.content)
                self.assertIn("no-store", svg["Cache-Control"])
                self.assertIn("private", svg["Cache-Control"])
                self.assertIn("X-Organization-Id", svg["Vary"])

    def test_qr_requires_explicit_valid_context_without_fallback(self):
        membership = OrganizationMembership.objects.create(
            user=self.user, organization=self.other_organization,
            role=OrganizationMembership.Role.VIEWER, is_active=False,
        )
        self.client.force_login(self.user)
        for context in (None, "invalid", str(membership.organization_id), "999999"):
            with self.subTest(context=context):
                headers = {} if context is None else {"HTTP_X_ORGANIZATION_ID": context}
                scan = self.client.post(reverse("api_box_scan", args=[self.box.id]), **headers)
                self.assertEqual(scan.status_code, 403)
                svg = self.client.get(reverse("qr_boite", args=[self.box.id]), **headers)
                self.assertEqual(svg.status_code, 404)
                self.assertNotIn(self.box.global_code, scan.content.decode())
                self.assertNotIn(self.box.global_code, svg.content.decode())
        self.assertFalse(AuditLog.objects.filter(action=AuditLog.Action.SCAN).exists())

    def test_qr_handoff_discloses_no_box_and_never_audits(self):
        self.client.force_login(self.user)
        other_box = Box.objects.get(global_code="AAU-1.001-TKY")
        for box_id in (other_box.id, 999999):
            with self.subTest(box_id=box_id), patch("apps.cultures.views.Box.objects") as boxes:
                response = self.client.get(reverse("scan_boite", args=[box_id]))
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response["Location"], f"/?scan_box={box_id}")
                boxes.filter.assert_not_called()
                boxes.get.assert_not_called()
                self.assertNotIn(other_box.global_code, response.content.decode())
        self.assertFalse(AuditLog.objects.filter(action=AuditLog.Action.SCAN).exists())

    def test_qr_superuser_still_requires_active_context(self):
        self.user.is_superuser = True
        self.user.save(update_fields=["is_superuser"])
        self.client.force_login(self.user)
        other_box = Box.objects.get(global_code="AAU-1.001-TKY")
        for name, method in (("api_box_scan", self.client.post), ("qr_boite", self.client.get)):
            self.assertEqual(method(reverse(name, args=[other_box.id]),
                HTTP_X_ORGANIZATION_ID=str(self.organization.id)).status_code, 404)
            response = method(reverse(name, args=[other_box.id]),
                HTTP_X_ORGANIZATION_ID=str(self.other_organization.id))
            self.assertIn(response.status_code, (200, 201))

    def test_scan_api_requires_authentication(self):
        response = self.client.post(reverse("api_box_scan", args=[self.box.id]),
            HTTP_X_ORGANIZATION_ID=str(self.organization.id))
        self.assertEqual(response.status_code, 403)
        self.assertFalse(AuditLog.objects.filter(action=AuditLog.Action.SCAN).exists())
