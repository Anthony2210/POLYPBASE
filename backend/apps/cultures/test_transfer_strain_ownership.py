import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.measurements.models import BiologicalMeasurement
from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain

from .models import Box, BoxLocation, BoxTransferImport, ThermalZone


class TransferStrainOwnershipTests(TestCase):
    def setUp(self):
        self.destination = Organization.objects.create(name="Destination", slug="destination")
        self.foreign = Organization.objects.create(name="Foreign", slug="foreign")
        self.user = get_user_model().objects.create_user(username="importer", email="importer@example.org", password="secret")
        OrganizationMembership.objects.create(
            user=self.user, organization=self.destination, role=OrganizationMembership.Role.ADMIN
        )
        self.zone = ThermalZone.objects.create(
            organization=self.destination, name="Tank", zone_type=ThermalZone.ZoneType.CABINET,
            target_temperature_c=15,
        )
        self.client.force_login(self.user)
        self.source = {
            "format": "polypbase.box_transfer.v1",
            "transfer_id": "transfer-1",
            "source_organization_name": "Source",
            "source_global_code": "SOURCE.001",
            "species_scientific_name": "Test species",
            "strain_code": "7-SRC",
            "strain_origin_code": "SRC",
            "transferred_polyp_count": "12",
        }

    def import_transfer(self, global_code="7-SRC.001", **source_changes):
        return self.client.post(
            reverse("api_box_transfer_import"),
            data=json.dumps({
                "source_data": {**self.source, **source_changes},
                "organization": self.destination.pk,
                "thermal_zone": self.zone.pk,
                "global_code": global_code,
            }),
            content_type="application/json",
            HTTP_X_ORGANIZATION_ID=str(self.destination.pk),
        )

    def test_new_strain_belongs_to_destination_and_reuse_preserves_metadata(self):
        response = self.import_transfer()
        self.assertEqual(response.status_code, 201, response.content)
        strain = Strain.objects.get(species__scientific_name="Test species", code="7-SRC")
        self.assertEqual(strain.organization_id, self.destination.pk)
        self.assertEqual(strain.origin_code, "SRC")
        box = Box.objects.get(strain=strain)
        self.assertEqual(box.organization_id, self.destination.pk)
        self.assertEqual(BoxLocation.objects.get(box=box).thermal_zone_id, self.zone.pk)
        self.assertEqual(BiologicalMeasurement.objects.get(box=box).polyp_count, 12)
        self.assertEqual(BoxTransferImport.objects.get(created_box=box).destination_organization_id, self.destination.pk)
        self.assertTrue(AuditLog.objects.filter(organization=self.destination, object_id=box.global_code).exists())

        response = self.import_transfer(
            global_code="7-SRC.002", transfer_id="transfer-2", strain_origin_code="CHANGED"
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(Strain.objects.count(), 1)
        self.assertEqual(Strain.objects.get(pk=strain.pk).origin_code, "SRC")

    def test_reuses_destination_owned_strain(self):
        species = Species.objects.create(scientific_name="Test species")
        strain = Strain.objects.create(species=species, code="7-SRC", organization=self.destination, origin_code="LOCAL")
        response = self.import_transfer()
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(Strain.objects.count(), 1)
        self.assertEqual(Box.objects.get().strain_id, strain.pk)
        strain.refresh_from_db()
        self.assertEqual(strain.origin_code, "LOCAL")

    def test_foreign_and_unowned_collisions_are_explicit_and_atomic(self):
        for owner in (self.foreign, None):
            with self.subTest(owner=owner):
                Strain.objects.all().delete()
                species = Species.objects.create(scientific_name="Test species") if not Species.objects.exists() else Species.objects.get()
                strain = Strain.objects.create(species=species, code="7-SRC", organization=owner)
                response = self.import_transfer()
                self.assertEqual(response.status_code, 409, response.content)
                self.assertIn("appartient", str(response.json()))
                self.assertEqual(Strain.objects.get(pk=strain.pk).organization_id, owner.pk if owner else None)
                self.assertFalse(Box.objects.exists())
                self.assertFalse(BoxLocation.objects.exists())
                self.assertFalse(BiologicalMeasurement.objects.exists())
                self.assertFalse(BoxTransferImport.objects.exists())
                self.assertFalse(AuditLog.objects.exists())

    def test_late_failure_rolls_back_new_strain_box_and_measurement(self):
        with patch("apps.cultures.api_views.AuditLog.objects.create", side_effect=RuntimeError("audit failed")):
            with self.assertRaisesMessage(RuntimeError, "audit failed"):
                self.import_transfer()
        self.assertFalse(Species.objects.exists())
        self.assertFalse(Strain.objects.exists())
        self.assertFalse(Box.objects.exists())
        self.assertFalse(BoxLocation.objects.exists())
        self.assertFalse(BiologicalMeasurement.objects.exists())
        self.assertFalse(BoxTransferImport.objects.exists())
