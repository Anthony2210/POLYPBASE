"""Tests for the administrator-only creation endpoints.

Covers thermal zones, probes, organizations and box transfers: who is allowed
to create what, and that a user can never reach another organization's data.
"""

import json
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.measurements.models import BiologicalMeasurement, DailyTemperature, Probe
from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain

from .models import Box, BoxTransfer, ThermalZone


class AdminResourceCreationApiTests(TestCase):
    def setUp(self):
        user_model = get_user_model()

        self.organization = Organization.objects.create(name="Aquarium de Paris", slug="paris")
        self.other_organization = Organization.objects.create(name="Aquarium de Tokyo", slug="tokyo")

        self.admin = user_model.objects.create_user(username="org_admin", password="secret")
        OrganizationMembership.objects.create(
            user=self.admin,
            organization=self.organization,
            role=OrganizationMembership.Role.ADMIN,
        )

        self.technician = user_model.objects.create_user(username="tech", password="secret")
        OrganizationMembership.objects.create(
            user=self.technician,
            organization=self.organization,
            role=OrganizationMembership.Role.LAB_TECHNICIAN,
        )

        self.superuser = user_model.objects.create_superuser(username="root", password="secret")

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
        self.other_zone = ThermalZone.objects.create(
            organization=self.other_organization,
            name="Cabinet-10",
            zone_type=ThermalZone.ZoneType.CABINET,
        )
        self.box = Box.objects.create(
            organization=self.organization,
            global_code="ATL-AAU-1.001",
            box_number="001",
            strain=self.strain,
            thermal_zone=self.zone,
        )

    def post(self, url_name, payload, args=None):
        return self.client.post(
            reverse(url_name, args=args),
            data=json.dumps(payload),
            content_type="application/json",
        )

    # -- thermal zones ----------------------------------------------------

    def test_admin_creates_a_thermal_zone(self):
        self.client.login(username="org_admin", password="secret")

        response = self.post(
            "api_thermal_zone_list",
            {
                "organization": self.organization.id,
                "name": "Etuve-25",
                "zone_type": ThermalZone.ZoneType.INCUBATOR,
                "target_temperature_c": "25.0",
                "capacity": 42,
            },
        )

        self.assertEqual(response.status_code, 201)
        zone = ThermalZone.objects.get(name="Etuve-25")
        self.assertEqual(zone.organization, self.organization)
        self.assertEqual(zone.zone_type, ThermalZone.ZoneType.INCUBATOR)
        self.assertEqual(zone.capacity, 42)

    def test_admin_updates_thermal_zone_capacity(self):
        self.client.login(username="org_admin", password="secret")

        response = self.client.patch(
            reverse("api_thermal_zone_detail", args=[self.zone.id]),
            {"capacity": 30},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.zone.refresh_from_db()
        self.assertEqual(self.zone.capacity, 30)

    def test_admin_updates_thermal_zone_salinity(self):
        self.client.login(username="org_admin", password="secret")

        response = self.client.patch(
            reverse("api_thermal_zone_detail", args=[self.zone.id]),
            {"salinity_psu": "35.00"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.zone.refresh_from_db()
        self.assertEqual(self.zone.salinity_psu, Decimal("35.00"))
        # The box sheets render this straight from the API, so the shape of the
        # value must not drift between databases.
        self.assertEqual(response.json()["salinity_psu"], "35.00")

    def test_admin_clears_thermal_zone_salinity(self):
        self.zone.salinity_psu = Decimal("35.00")
        self.zone.save(update_fields=["salinity_psu"])
        self.client.login(username="org_admin", password="secret")

        response = self.client.patch(
            reverse("api_thermal_zone_detail", args=[self.zone.id]),
            {"salinity_psu": None},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.zone.refresh_from_db()
        self.assertIsNone(self.zone.salinity_psu)

    def test_lab_technician_cannot_update_thermal_zone_salinity(self):
        self.client.login(username="tech", password="secret")

        response = self.client.patch(
            reverse("api_thermal_zone_detail", args=[self.zone.id]),
            {"salinity_psu": "35.00"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)

    def test_lab_technician_cannot_update_thermal_zone_capacity(self):
        self.client.login(username="tech", password="secret")

        response = self.client.patch(
            reverse("api_thermal_zone_detail", args=[self.zone.id]),
            {"capacity": 30},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)

    def test_lab_technician_cannot_create_a_thermal_zone(self):
        self.client.login(username="tech", password="secret")

        response = self.post(
            "api_thermal_zone_list",
            {"organization": self.organization.id, "name": "Etuve-25"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(ThermalZone.objects.filter(name="Etuve-25").exists())

    def test_lab_technician_can_record_manual_zone_temperature(self):
        self.client.login(username="tech", password="secret")

        response = self.post(
            "api_thermal_zone_manual_temperature",
            {"measured_on": "2026-07-17", "temperature_c": "14.80"},
            args=[self.zone.id],
        )

        self.assertEqual(response.status_code, 201)
        temperature = DailyTemperature.objects.get(thermal_zone=self.zone, date="2026-07-17")
        self.assertEqual(temperature.average_temperature_c, Decimal("14.80"))
        self.assertEqual(temperature.measurement_count, 1)
        self.assertEqual(
            Decimal(str(response.json()["latest_temperature"]["average_temperature_c"])),
            Decimal("14.80"),
        )

    def test_viewer_cannot_record_manual_zone_temperature(self):
        viewer = get_user_model().objects.create_user(username="viewer", password="secret")
        OrganizationMembership.objects.create(
            user=viewer,
            organization=self.organization,
            role=OrganizationMembership.Role.VIEWER,
        )
        self.client.login(username="viewer", password="secret")

        response = self.post(
            "api_thermal_zone_manual_temperature",
            {"measured_on": "2026-07-17", "temperature_c": "14.80"},
            args=[self.zone.id],
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(DailyTemperature.objects.filter(thermal_zone=self.zone).exists())

    def test_admin_cannot_create_a_zone_in_another_organization(self):
        self.client.login(username="org_admin", password="secret")

        response = self.post(
            "api_thermal_zone_list",
            {"organization": self.other_organization.id, "name": "Etuve-25"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(ThermalZone.objects.filter(name="Etuve-25").exists())

    def test_duplicate_zone_name_in_the_same_organization_is_rejected(self):
        self.client.login(username="org_admin", password="secret")

        response = self.post(
            "api_thermal_zone_list",
            {"organization": self.organization.id, "name": "Cabinet-15"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(ThermalZone.objects.filter(name="Cabinet-15").count(), 1)

    # -- audit log --------------------------------------------------------

    def test_admin_can_view_organization_audit_log(self):
        measurement = BiologicalMeasurement.objects.create(
            box=self.box,
            measured_on="2026-07-15",
            polyp_count=12,
            ephyrae_count=4,
            salinity_psu="35.00",
            notes="Test admin audit",
            user=self.technician,
        )
        AuditLog.objects.create(
            organization=self.organization,
            user=self.technician,
            action=AuditLog.Action.ENTRY,
            object_type="box",
            object_id=self.box.global_code,
            description="Biological measurement for today",
            metadata={"measurement_id": measurement.id},
        )
        AuditLog.objects.create(
            organization=self.organization,
            user=self.technician,
            action=AuditLog.Action.VIEW,
            object_type="box",
            object_id=self.box.global_code,
            description="Box opened",
        )
        self.client.login(username="org_admin", password="secret")

        response = self.client.get(reverse("api_account_audit_log"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload["results"]), 1)
        self.assertEqual(payload["results"][0]["object_id"], self.box.global_code)
        self.assertEqual(payload["results"][0]["metadata"]["valeurs"]["polypes"], 12)
        self.assertEqual(payload["results"][0]["metadata"]["valeurs"]["ephyrules"], 4)

    def test_admin_audit_log_recovers_measurement_values_from_box_and_date(self):
        BiologicalMeasurement.objects.create(
            box=self.box,
            measured_on="2026-07-08",
            polyp_count=18,
            ephyrae_count=7,
            salinity_psu="35.00",
            notes="Recovered from box and date",
            user=self.technician,
        )
        AuditLog.objects.create(
            organization=self.organization,
            user=self.technician,
            action=AuditLog.Action.UPDATE,
            object_type="box",
            object_id=self.box.global_code,
            description="Biological measurement for 2026-07-08",
            metadata={"measurement_id": 999999},
        )
        self.client.login(username="org_admin", password="secret")

        response = self.client.get(reverse("api_account_audit_log"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["results"][0]["metadata"]["valeurs"]["polypes"], 18)
        self.assertEqual(payload["results"][0]["metadata"]["valeurs"]["ephyrules"], 7)

    def test_lab_technician_cannot_view_organization_audit_log(self):
        self.client.login(username="tech", password="secret")

        response = self.client.get(reverse("api_account_audit_log"))

        self.assertEqual(response.status_code, 403)

    # -- probes ------------------------------------------------------------

    def test_admin_creates_a_probe_inheriting_the_zone_organization(self):
        self.client.login(username="org_admin", password="secret")

        response = self.post(
            "api_probe_create",
            {
                "thermal_zone": self.zone.id,
                "code": "SONDE-15-01",
                "probe_type": Probe.ProbeType.LORAWAN,
                "location": "Etagere haute",
            },
        )

        self.assertEqual(response.status_code, 201)
        probe = Probe.objects.get(code="SONDE-15-01")
        self.assertEqual(probe.thermal_zone, self.zone)
        # The organization is never sent by the client: it comes from the zone.
        self.assertEqual(probe.organization, self.organization)

    def test_admin_cannot_add_a_probe_to_another_organization_zone(self):
        self.client.login(username="org_admin", password="secret")

        response = self.post(
            "api_probe_create",
            {"thermal_zone": self.other_zone.id, "code": "SONDE-10-01"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(Probe.objects.filter(code="SONDE-10-01").exists())

    def test_duplicate_probe_code_in_the_same_organization_is_rejected(self):
        Probe.objects.create(
            organization=self.organization,
            thermal_zone=self.zone,
            code="SONDE-15-01",
        )
        self.client.login(username="org_admin", password="secret")

        response = self.post(
            "api_probe_create",
            {"thermal_zone": self.zone.id, "code": "SONDE-15-01"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(Probe.objects.filter(code="SONDE-15-01").count(), 1)

    # -- organizations -----------------------------------------------------

    def test_superuser_creates_an_organization(self):
        self.client.login(username="root", password="secret")

        response = self.post(
            "api_organization_create",
            {"name": "Aquarium de Nausicaa", "city": "Boulogne", "country": "France"},
        )

        self.assertEqual(response.status_code, 201)
        self.assertTrue(Organization.objects.filter(name="Aquarium de Nausicaa").exists())

    def test_organization_admin_creates_a_partner_organization(self):
        self.client.login(username="org_admin", password="secret")

        response = self.post("api_organization_create", {"name": "Aquarium de Nausicaa"})

        self.assertEqual(response.status_code, 201)
        organization = Organization.objects.get(name="Aquarium de Nausicaa")
        self.assertTrue(
            OrganizationMembership.objects.filter(
                user=self.admin,
                organization=organization,
                role=OrganizationMembership.Role.ADMIN,
                is_active=True,
            ).exists()
        )

    def test_duplicate_organization_name_is_rejected_case_insensitively(self):
        self.client.login(username="root", password="secret")

        response = self.post("api_organization_create", {"name": "aquarium de paris"})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(Organization.objects.filter(name__iexact="aquarium de paris").count(), 1)

    def test_superuser_updates_an_organization(self):
        self.client.login(username="root", password="secret")

        response = self.client.patch(
            reverse("api_organization_detail", args=[self.other_organization.id]),
            {"name": "Aquarium de La Rochelle", "city": "La Rochelle"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.other_organization.refresh_from_db()
        self.assertEqual(self.other_organization.name, "Aquarium de La Rochelle")
        self.assertEqual(self.other_organization.city, "La Rochelle")

    def test_organization_admin_cannot_update_an_organization(self):
        self.client.login(username="org_admin", password="secret")

        response = self.client.patch(
            reverse("api_organization_detail", args=[self.organization.id]),
            {"name": "Renamed"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)

    def test_superuser_deletes_an_unused_organization(self):
        organization = Organization.objects.create(name="Aquarium vide")
        self.client.login(username="root", password="secret")

        response = self.client.delete(reverse("api_organization_detail", args=[organization.id]))

        self.assertEqual(response.status_code, 204)
        self.assertFalse(Organization.objects.filter(id=organization.id).exists())

    def test_superuser_cannot_delete_organization_with_lab_data(self):
        self.client.login(username="root", password="secret")

        response = self.client.delete(reverse("api_organization_detail", args=[self.organization.id]))

        self.assertEqual(response.status_code, 400)
        self.assertTrue(Organization.objects.filter(id=self.organization.id).exists())

    # -- box transfers -----------------------------------------------------

    def test_admin_records_a_planned_transfer_without_reassigning_the_box(self):
        self.client.login(username="org_admin", password="secret")

        response = self.post(
            "api_box_transfer_create",
            {
                "box": self.box.id,
                "to_organization": self.other_organization.id,
                "polyp_count": 50,
                "notes": "Envoi convenu par mail",
            },
        )

        self.assertEqual(response.status_code, 201)
        transfer = BoxTransfer.objects.get(box=self.box)
        self.assertEqual(transfer.from_organization, self.organization)
        self.assertEqual(transfer.to_organization, self.other_organization)
        self.assertEqual(transfer.status, BoxTransfer.Status.PLANNED)
        self.assertEqual(transfer.user, self.admin)

        # A transfer only records the intent: the box must not change owner.
        self.box.refresh_from_db()
        self.assertEqual(self.box.organization, self.organization)

    def test_transfer_to_the_same_organization_is_rejected(self):
        self.client.login(username="org_admin", password="secret")

        response = self.post(
            "api_box_transfer_create",
            {"box": self.box.id, "to_organization": self.organization.id, "polyp_count": 50},
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(BoxTransfer.objects.filter(box=self.box).exists())

    def test_lab_technician_cannot_transfer_a_box(self):
        self.client.login(username="tech", password="secret")

        response = self.post(
            "api_box_transfer_create",
            {"box": self.box.id, "to_organization": self.other_organization.id, "polyp_count": 50},
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(BoxTransfer.objects.filter(box=self.box).exists())
