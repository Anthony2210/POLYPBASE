"""Organization strain eligibility at box creation and subculture boundaries."""

from datetime import date

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.measurements.models import BiologicalMeasurement
from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain

from .models import Box, BoxLineage, BoxLocation, SubcultureEvent, ThermalZone
from .services import create_subculture


@override_settings(SECURE_SSL_REDIRECT=False)
class BoxStrainScopingTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name="Box strain laboratory")
        self.other_organization = Organization.objects.create(name="Other box laboratory")
        self.user = get_user_model().objects.create_user(
            username="box_strain_technician",
                        email="box_strain_technician@example.org",
                        password="secret"
        )
        OrganizationMembership.objects.create(
            user=self.user,
            organization=self.organization,
            role=OrganizationMembership.Role.LAB_TECHNICIAN,
        )
        self.species = Species.objects.create(
            scientific_name="Aurelia scoped", genus_species_code="ASC"
        )
        self.owned = Strain.objects.create(
            species=self.species, code="ASC-OWN-1", organization=self.organization
        )
        self.legacy = Strain.objects.create(species=self.species, code="ASC-LEG-1")
        self.foreign = Strain.objects.create(
            species=self.species, code="ASC-OTHER-1", organization=self.other_organization
        )
        self.zone = ThermalZone.objects.create(
            organization=self.organization, name="Box strain zone", target_temperature_c=15
        )
        self.client.force_login(self.user)

    def create_box_request(self, strain, number="001"):
        return self.client.post(
            reverse("api_box_list"),
            data={
                "strain": strain.pk,
                "thermal_zone": self.zone.pk,
                "global_code": f"{strain.code}.{number}",
                "box_number": number,
            },
            content_type="application/json",
            HTTP_X_ORGANIZATION_ID=str(self.organization.pk),
        )

    def test_lab_technician_can_create_box_with_owned_strain(self):
        response = self.create_box_request(self.owned)
        self.assertEqual(response.status_code, 201, response.content)
        box = Box.objects.get(global_code="ASC-OWN-1.001")
        self.assertEqual(box.organization_id, self.organization.pk)
        self.assertEqual(box.strain_id, self.owned.pk)
        self.assertTrue(BoxLocation.objects.filter(box=box, thermal_zone=self.zone).exists())

    def test_legacy_unowned_strain_remains_eligible_for_box_creation(self):
        Box.objects.create(organization=self.organization, strain=self.legacy, global_code="ASC-LEG-1.000", box_number="000", status=Box.Status.INACTIVE)
        response = self.create_box_request(self.legacy)
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(Box.objects.get(global_code="ASC-LEG-1.001").strain_id, self.legacy.pk)

    def test_foreign_owned_strain_is_rejected_without_partial_writes(self):
        response = self.create_box_request(self.foreign)
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("strain", response.json())
        self.assertFalse(Box.objects.exists())
        self.assertFalse(BoxLocation.objects.exists())

    def test_legacy_strain_only_used_elsewhere_or_orphan_is_rejected(self):
        Box.objects.create(organization=self.other_organization, strain=self.legacy, global_code="ASC-LEG-1.000", box_number="000")
        for strain in (self.legacy, Strain.objects.create(species=self.species, code="ASC-ORPHAN-1")):
            response = self.create_box_request(strain)
            self.assertEqual(response.status_code, 400, response.content)
            self.assertIn("strain", response.json())
        self.assertEqual(Box.objects.count(), 1)
        self.assertFalse(BoxLocation.objects.exists())
        self.assertFalse(AuditLog.objects.exists())

    def test_anomalous_parent_subculture_is_rejected_without_writes(self):
        parent = Box.objects.create(
            organization=self.organization,
            strain=self.foreign,
            global_code="ASC-OTHER-1.001",
            box_number="001",
            thermal_zone=self.zone,
        )
        child = {
            "global_code": "ASC-OTHER-1.002",
            "box_number": "002",
            "thermal_zone_id": self.zone.pk,
            "initial_polyp_count": 0,
        }
        response = self.client.post(
            reverse("api_box_subcultures", args=[parent.pk]),
            data={"event_date": "2026-09-01", "children": [child]},
            content_type="application/json",
            HTTP_X_ORGANIZATION_ID=str(self.organization.pk),
        )
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(Box.objects.count(), 1)
        self.assertFalse(SubcultureEvent.objects.exists())
        self.assertFalse(BoxLineage.objects.exists())
        self.assertFalse(BoxLocation.objects.exists())
        self.assertFalse(BiologicalMeasurement.objects.exists())
        self.assertFalse(AuditLog.objects.exists())

        with self.assertRaises(ValidationError):
            create_subculture(
                parent_box=parent,
                user=self.user,
                event_date=date(2026, 9, 1),
                reason="",
                notes="",
                children=[{
                    "global_code": child["global_code"],
                    "box_number": child["box_number"],
                    "thermal_zone": self.zone,
                    "initial_polyp_count": 0,
                }],
            )
        self.assertEqual(Box.objects.count(), 1)
        self.assertFalse(SubcultureEvent.objects.exists())
        self.assertFalse(AuditLog.objects.exists())

    def test_legacy_parent_can_still_be_subcultured(self):
        parent = Box.objects.create(
            organization=self.organization,
            strain=self.legacy,
            global_code="ASC-LEG-1.001",
            box_number="001",
            thermal_zone=self.zone,
        )
        response = self.client.post(
            reverse("api_box_subcultures", args=[parent.pk]),
            data={
                "event_date": "2026-09-01",
                "children": [{
                    "global_code": "ASC-LEG-1.002",
                    "box_number": "002",
                    "thermal_zone_id": self.zone.pk,
                    "initial_polyp_count": 0,
                }],
            },
            content_type="application/json",
            HTTP_X_ORGANIZATION_ID=str(self.organization.pk),
        )
        self.assertEqual(response.status_code, 201, response.content)
        child = Box.objects.get(global_code="ASC-LEG-1.002")
        self.assertEqual(child.organization_id, self.organization.pk)
        self.assertEqual(child.strain_id, self.legacy.pk)
        self.assertEqual(child.biological_measurements.get().polyp_count, 0)
