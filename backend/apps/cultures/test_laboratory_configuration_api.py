"""Laboratory configuration: listing, creating and editing locations.

Covers the contract used by Administration > Laboratory configuration:
organization scoping, administrator-only writes, validation, the meaning of
the occupancy count, and capacity staying informational.
"""

import json
from datetime import datetime, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain

from .models import MOVEMENT_WEEK_TIME_ZONE, Box, BoxLocation, ThermalZone
from .serializers import ThermalZoneCreateSerializer


class LaboratoryConfigurationApiTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.organization = Organization.objects.create(name="Lab configuration home", slug="lab-home")
        self.other_organization = Organization.objects.create(name="Lab configuration other", slug="lab-other")

        self.admin = user_model.objects.create_user(
            username="config_admin", email="config_admin@example.org", password="secret",
        )
        OrganizationMembership.objects.create(
            user=self.admin, organization=self.organization, role=OrganizationMembership.Role.ADMIN,
        )
        # The same administrator only reads the other institution.
        OrganizationMembership.objects.create(
            user=self.admin, organization=self.other_organization, role=OrganizationMembership.Role.VIEWER,
        )
        self.technician = user_model.objects.create_user(
            username="config_tech", email="config_tech@example.org", password="secret",
        )
        OrganizationMembership.objects.create(
            user=self.technician, organization=self.organization,
            role=OrganizationMembership.Role.LAB_TECHNICIAN,
        )
        self.viewer = user_model.objects.create_user(
            username="config_viewer", email="config_viewer@example.org", password="secret",
        )
        OrganizationMembership.objects.create(
            user=self.viewer, organization=self.organization, role=OrganizationMembership.Role.VIEWER,
        )

        species = Species.objects.create(scientific_name="Aurelia configuration", genus_species_code="ACF")
        self.strain = Strain.objects.create(
            species=species, code="ACF-LAB-1", organization=self.organization, number=1, origin_code="LAB",
        )
        self.zone = ThermalZone.objects.create(
            organization=self.organization, name="Armoire 15", target_temperature_c="15.0",
            capacity=2, salinity_psu="32.00",
        )
        self.empty_zone = ThermalZone.objects.create(organization=self.organization, name="Armoire 20")
        self.foreign_zone = ThermalZone.objects.create(
            organization=self.other_organization, name="Foreign 15", capacity=10,
        )

    def headers(self, organization=None):
        return {"HTTP_X_ORGANIZATION_ID": str((organization or self.organization).pk)}

    def create_box(self, number, *, zone, status=Box.Status.ACTIVE):
        box = Box.objects.create(
            organization=self.organization, global_code=f"ACF-LAB-1.{number}", box_number=number,
            strain=self.strain, status=status, thermal_zone=zone,
        )
        if zone is not None:
            BoxLocation.objects.create(
                box=box, thermal_zone=zone,
                starts_at=datetime(2025, 1, 6, 9, 0, tzinfo=MOVEMENT_WEEK_TIME_ZONE),
            )
        return box

    def list_zones(self, user=None, organization=None):
        self.client.force_login(user or self.admin)
        response = self.client.get(reverse("api_thermal_zone_list"), **self.headers(organization))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        return payload["results"] if isinstance(payload, dict) else payload

    def create_zone(self, payload, *, user=None, organization=None):
        self.client.force_login(user or self.admin)
        return self.client.post(
            reverse("api_thermal_zone_list"), data=json.dumps(payload),
            content_type="application/json", **self.headers(organization),
        )

    def update_zone(self, zone, payload, *, user=None, organization=None):
        self.client.force_login(user or self.admin)
        return self.client.patch(
            reverse("api_thermal_zone_detail", args=[zone.id]), data=json.dumps(payload),
            content_type="application/json", **self.headers(organization),
        )

    # -- listing --------------------------------------------------------------

    def test_list_contains_only_active_institution_locations(self):
        names = {zone["name"] for zone in self.list_zones()}
        self.assertEqual(names, {"Armoire 15", "Armoire 20"})

        foreign = self.list_zones(organization=self.other_organization)
        self.assertEqual([zone["name"] for zone in foreign], ["Foreign 15"])

    def test_box_count_counts_only_active_boxes_currently_in_the_location(self):
        self.create_box("001", zone=self.zone)
        self.create_box("002", zone=self.zone)
        self.create_box("003", zone=self.zone, status=Box.Status.PENDING_REVIEW)
        self.create_box("004", zone=None, status=Box.Status.INACTIVE)

        zones = {zone["name"]: zone for zone in self.list_zones()}

        self.assertEqual(zones["Armoire 15"]["box_count"], 2)
        self.assertEqual(zones["Armoire 15"]["capacity"], 2)
        # An empty location reports a real zero, and no capacity stays null.
        self.assertEqual(zones["Armoire 20"]["box_count"], 0)
        self.assertIsNone(zones["Armoire 20"]["capacity"])

    # -- creation -------------------------------------------------------------

    def test_admin_creates_location_with_configuration_and_audit(self):
        response = self.create_zone({
            "organization": self.organization.id, "name": "  Étuve 25  ",
            "target_temperature_c": "25.5", "capacity": 0, "salinity_psu": "0",
        })

        self.assertEqual(response.status_code, 201, response.content)
        zone = ThermalZone.objects.get(organization=self.organization, name="Étuve 25")
        # Zero is a configured value, never confused with "not set".
        self.assertEqual(zone.capacity, 0)
        self.assertEqual(str(zone.salinity_psu), "0.00")
        self.assertEqual(str(zone.target_temperature_c), "25.5")
        log = AuditLog.objects.get(action=AuditLog.Action.CREATION, object_type="thermal_zone")
        self.assertEqual(log.organization, self.organization)
        self.assertEqual(log.user, self.admin)
        self.assertEqual(log.metadata["valeurs"]["capacite"], 0)

    def test_optional_configuration_can_be_omitted(self):
        response = self.create_zone({"organization": self.organization.id, "name": "Armoire 10"})

        self.assertEqual(response.status_code, 201, response.content)
        zone = ThermalZone.objects.get(name="Armoire 10")
        self.assertIsNone(zone.capacity)
        self.assertIsNone(zone.target_temperature_c)
        self.assertIsNone(zone.salinity_psu)

    def test_technician_and_viewer_cannot_create_locations(self):
        for user in (self.technician, self.viewer):
            response = self.create_zone(
                {"organization": self.organization.id, "name": f"Refused {user.username}"}, user=user,
            )
            self.assertEqual(response.status_code, 403)
        self.assertFalse(ThermalZone.objects.filter(name__startswith="Refused").exists())
        self.assertFalse(AuditLog.objects.filter(object_type="thermal_zone").exists())

    def test_admin_cannot_create_in_an_institution_where_they_are_not_admin(self):
        response = self.create_zone(
            {"organization": self.other_organization.id, "name": "Foreign new"},
            organization=self.other_organization,
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(ThermalZone.objects.filter(name="Foreign new").exists())

    def test_create_rejects_invalid_configuration(self):
        invalid_payloads = [
            {"name": "   "},
            {"name": "Armoire 15"},
            {"name": "Negative", "capacity": -1},
            {"name": "Fraction", "capacity": 1.5},
            {"name": "Too precise", "target_temperature_c": "15.25"},
            {"name": "Too large", "target_temperature_c": "1000"},
        ]
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                response = self.create_zone({"organization": self.organization.id, **payload})
                self.assertEqual(response.status_code, 400)
        self.assertEqual(ThermalZone.objects.filter(organization=self.organization).count(), 2)

    def test_duplicate_name_is_reported_on_the_name_field(self):
        create = self.create_zone({"organization": self.organization.id, "name": "Armoire 15"})
        update = self.update_zone(self.empty_zone, {"name": "Armoire 15"})

        for response in (create, update):
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json(), {"name": ["Une zone porte déjà ce nom dans cette structure."]})

    def test_concurrent_duplicate_name_is_reported_as_validation_error(self):
        # Simulates a second request committing the same name after validation.
        with (
            patch.object(ThermalZoneCreateSerializer, "validate", lambda serializer, attrs: attrs),
            patch.object(ThermalZoneCreateSerializer, "get_validators", lambda serializer: []),
        ):
            response = self.create_zone({"organization": self.organization.id, "name": "Armoire 15"})

        self.assertEqual(response.status_code, 400)
        self.assertIn("name", response.json())
        self.assertEqual(ThermalZone.objects.filter(name="Armoire 15").count(), 1)
        self.assertFalse(AuditLog.objects.filter(object_type="thermal_zone").exists())

    # -- editing --------------------------------------------------------------

    def test_admin_edits_name_capacity_and_target_with_before_after_audit(self):
        response = self.update_zone(self.zone, {
            "name": "Armoire 16", "capacity": 40, "target_temperature_c": "16.0",
        })

        self.assertEqual(response.status_code, 200, response.content)
        self.zone.refresh_from_db()
        self.assertEqual(self.zone.name, "Armoire 16")
        self.assertEqual(self.zone.capacity, 40)
        self.assertEqual(str(self.zone.target_temperature_c), "16.0")
        # Fields absent from a partial update keep their value.
        self.assertEqual(str(self.zone.salinity_psu), "32.00")
        log = AuditLog.objects.get(action=AuditLog.Action.UPDATE, object_type="thermal_zone")
        self.assertEqual(log.organization, self.organization)
        self.assertEqual(log.user, self.admin)
        self.assertEqual(log.metadata["modifications"]["capacite"], {"avant": 2, "apres": 40})
        self.assertEqual(log.metadata["modifications"]["nom"], {"avant": "Armoire 15", "apres": "Armoire 16"})

    def test_admin_clears_capacity_and_sets_zero_distinctly(self):
        response = self.update_zone(self.zone, {"capacity": None})
        self.assertEqual(response.status_code, 200)
        self.zone.refresh_from_db()
        self.assertIsNone(self.zone.capacity)

        response = self.update_zone(self.zone, {"capacity": 0})
        self.assertEqual(response.status_code, 200)
        self.zone.refresh_from_db()
        self.assertEqual(self.zone.capacity, 0)

    def test_technician_and_viewer_cannot_edit_locations(self):
        for user in (self.technician, self.viewer):
            response = self.update_zone(self.zone, {"capacity": 99}, user=user)
            self.assertEqual(response.status_code, 403)
        self.zone.refresh_from_db()
        self.assertEqual(self.zone.capacity, 2)
        self.assertFalse(AuditLog.objects.filter(object_type="thermal_zone").exists())

    def test_foreign_location_id_is_not_found_in_the_active_institution(self):
        response = self.update_zone(self.foreign_zone, {"capacity": 99})

        self.assertEqual(response.status_code, 404)
        self.foreign_zone.refresh_from_db()
        self.assertEqual(self.foreign_zone.capacity, 10)

    def test_reader_membership_in_other_institution_cannot_edit_its_locations(self):
        response = self.update_zone(self.foreign_zone, {"capacity": 99}, organization=self.other_organization)

        self.assertEqual(response.status_code, 403)
        self.foreign_zone.refresh_from_db()
        self.assertEqual(self.foreign_zone.capacity, 10)

    def test_edit_rejects_duplicate_name_and_invalid_values(self):
        for payload in ({"name": "Armoire 20"}, {"capacity": -5}, {"target_temperature_c": "abc"}):
            with self.subTest(payload=payload):
                self.assertEqual(self.update_zone(self.zone, payload).status_code, 400)
        self.zone.refresh_from_db()
        self.assertEqual((self.zone.name, self.zone.capacity), ("Armoire 15", 2))

    # -- capacity semantics -----------------------------------------------------

    def test_capacity_is_informational_and_never_blocks_a_move(self):
        self.create_box("001", zone=self.zone)
        self.create_box("002", zone=self.zone)
        extra = self.create_box("003", zone=self.empty_zone)

        self.client.force_login(self.technician)
        response = self.client.post(
            reverse("api_box_move", args=[extra.id]),
            data=json.dumps({
                "expected_thermal_zone_id": self.empty_zone.id,
                "thermal_zone_id": self.zone.id,
                "moved_at": (timezone.now() - timedelta(minutes=1)).isoformat(),
                "notes": "",
            }),
            content_type="application/json",
            **self.headers(),
        )

        self.assertIn(response.status_code, (200, 201), response.content)
        zones = {zone["name"]: zone for zone in self.list_zones()}
        # Over capacity is reported as the factual numbers.
        self.assertEqual((zones["Armoire 15"]["box_count"], zones["Armoire 15"]["capacity"]), (3, 2))

    def test_lowering_capacity_below_occupancy_is_allowed_and_moves_nothing(self):
        self.create_box("001", zone=self.zone)
        self.create_box("002", zone=self.zone)

        response = self.update_zone(self.zone, {"capacity": 1})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Box.objects.filter(thermal_zone=self.zone, status=Box.Status.ACTIVE).count(), 2)
        self.assertEqual(BoxLocation.objects.filter(thermal_zone=self.zone, ends_at__isnull=True).count(), 2)
