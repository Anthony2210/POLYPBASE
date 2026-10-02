import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.measurements.models import BiologicalMeasurement
from apps.organizations.models import Organization
from apps.taxonomy.models import GlobalStrainIdentity, LocalStrainIdentity, Species, Strain

from .models import Box, BoxLocation, BoxTransfer, BoxTransferImport, ThermalZone


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

    def import_transfer(
        self, global_code="7-SRC.001", *, organization=None, zone=None,
        active_organization=None, **source_changes,
    ):
        return self.client.post(
            reverse("api_box_transfer_import"),
            data=json.dumps({
                "source_data": {**self.source, **source_changes},
                "organization": (organization or self.destination).pk,
                "thermal_zone": (zone or self.zone).pk,
                "global_code": global_code,
            }),
            content_type="application/json",
            HTTP_X_ORGANIZATION_ID=str((active_organization or self.destination).pk),
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
        measurement = BiologicalMeasurement.objects.get(box=box)
        self.assertEqual(measurement.polyp_count, 12)
        self.assertEqual(measurement.ephyrae_count, 0)
        self.assertIsNone(strain.global_identity_id)
        self.assertFalse(LocalStrainIdentity.objects.exists())
        self.assertFalse(GlobalStrainIdentity.objects.exists())
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
        with patch("apps.cultures.transfer_v1.AuditLog.objects.create", side_effect=RuntimeError("audit failed")):
            with self.assertRaisesMessage(RuntimeError, "audit failed"):
                self.import_transfer()
        self.assertFalse(Species.objects.exists())
        self.assertFalse(Strain.objects.exists())
        self.assertFalse(Box.objects.exists())
        self.assertFalse(BoxLocation.objects.exists())
        self.assertFalse(BiologicalMeasurement.objects.exists())
        self.assertFalse(BoxTransferImport.objects.exists())
        self.assertFalse(AuditLog.objects.exists())

    def test_sequential_replay_keeps_legacy_response_and_identity(self):
        self.assertEqual(self.import_transfer().status_code, 201)
        response = self.import_transfer(
            global_code="OTHER.001", species_scientific_name="Another species",
            strain_code="OTHER", source_global_code="OTHER-SOURCE.001",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), ["Ce transfert a déjà été importé."])
        self.assertEqual(Species.objects.count(), 1)
        self.assertEqual(Box.objects.count(), 1)
        self.assertEqual(BoxTransferImport.objects.count(), 1)
        self.assertEqual(AuditLog.objects.count(), 1)

    def test_database_replay_constraint_rolls_back_before_translation(self):
        self.assertEqual(self.import_transfer().status_code, 201)
        # Reproduce the late uniqueness failure with a real INSERT rather than
        # mocking IntegrityError. A changed payload still has the same v1 key.
        with patch("apps.cultures.transfer_v1._reject_replay"):
            response = self.import_transfer(
                global_code="OTHER.001", species_scientific_name="Another species",
                strain_code="OTHER",
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), ["Ce transfert a déjà été importé."])
        self.assertFalse(connection.needs_rollback)
        for model in (Species, Strain, Box, BoxLocation, BiologicalMeasurement, BoxTransferImport, AuditLog):
            self.assertEqual(model.objects.count(), 1, model.__name__)
        self.assertFalse(Species.objects.filter(scientific_name="Another species").exists())

    def test_other_integrity_errors_are_not_reported_as_replays(self):
        with patch(
            "apps.cultures.transfer_v1.BoxTransferImport.objects.create",
            side_effect=IntegrityError("unrelated constraint"),
        ):
            with self.assertRaisesMessage(IntegrityError, "unrelated constraint"):
                self.import_transfer()
        self.assertFalse(connection.needs_rollback)
        for model in (Species, Strain, Box, BoxLocation, BiologicalMeasurement, BoxTransferImport, AuditLog):
            self.assertFalse(model.objects.exists(), model.__name__)

    def test_import_preserves_extra_source_data(self):
        extra = {"legacy_optional_field": {"value": 0}, "latest_culture_status": "good"}
        response = self.import_transfer(**extra)
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(BoxTransferImport.objects.get().source_data, {**self.source, **extra})
        self.assertEqual(BiologicalMeasurement.objects.get().culture_status, "good")

    def test_import_rejects_foreign_or_inactive_zone_without_writes(self):
        foreign_zone = ThermalZone.objects.create(organization=self.foreign, name="Foreign tank")
        inactive_zone = ThermalZone.objects.create(
            organization=self.destination, name="Closed tank", is_active=False,
        )
        for zone in (foreign_zone, inactive_zone):
            with self.subTest(zone=zone):
                response = self.import_transfer(zone=zone)
                self.assertEqual(response.status_code, 404)
                self.assertFalse(Species.objects.exists())
                self.assertFalse(Box.objects.exists())
                self.assertFalse(AuditLog.objects.exists())

    def test_import_cannot_target_non_active_organization_even_for_its_admin(self):
        OrganizationMembership.objects.create(
            user=self.user, organization=self.foreign, role=OrganizationMembership.Role.ADMIN,
        )
        foreign_zone = ThermalZone.objects.create(organization=self.foreign, name="Foreign tank")
        response = self.import_transfer(organization=self.foreign, zone=foreign_zone)
        self.assertEqual(response.status_code, 404)
        self.assertFalse(Box.objects.exists())
        self.assertFalse(Species.objects.exists())
        self.assertFalse(AuditLog.objects.exists())

    def test_import_requires_active_membership_and_admin_in_selected_organization(self):
        membership = OrganizationMembership.objects.get(user=self.user, organization=self.destination)
        for role in (OrganizationMembership.Role.LAB_TECHNICIAN, OrganizationMembership.Role.VIEWER):
            with self.subTest(role=role):
                membership.role = role
                membership.save(update_fields=["role"])
                response = self.import_transfer()
                self.assertEqual(response.status_code, 403)
        membership.is_active = False
        membership.save(update_fields=["is_active"])
        self.assertEqual(self.import_transfer().status_code, 403)
        self.assertFalse(Box.objects.exists())
        self.assertFalse(Species.objects.exists())
        self.assertFalse(AuditLog.objects.exists())

    def test_import_rejects_unauthorized_active_organization(self):
        response = self.import_transfer(active_organization=self.foreign)
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Box.objects.exists())
        self.assertFalse(Species.objects.exists())
        self.assertFalse(AuditLog.objects.exists())

    def test_import_validation_details_remain_unchanged(self):
        cases = (
            ({"format": "v2"}, {"source_data": "Version de transfert Polypbase non reconnue."}),
            ({"transferred_polyp_count": "invalid"}, {"source_data": "Le nombre de polypes est invalide."}),
            ({"transferred_polyp_count": "0"}, {"source_data": "Le nombre de polypes doit être positif."}),
            ({"transferred_polyp_count": "-1"}, {"source_data": "Le nombre de polypes doit être positif."}),
            ({"strain_code": ""}, {"source_data": "Colonnes obligatoires manquantes : strain_code"}),
        )
        for changes, detail in cases:
            with self.subTest(changes=changes):
                response = self.import_transfer(**changes)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json(), detail)
        self.assertFalse(Species.objects.exists())
        self.assertFalse(Box.objects.exists())


class TransferPreparationBoundaryTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name="Source", slug="source")
        self.destination = Organization.objects.create(name="Destination", slug="destination")
        self.user = get_user_model().objects.create_user(username="preparer", email="preparer@example.org")
        self.membership = OrganizationMembership.objects.create(
            user=self.user, organization=self.organization, role=OrganizationMembership.Role.ADMIN,
        )
        species = Species.objects.create(scientific_name="Preparation species")
        strain = Strain.objects.create(species=species, code="SOURCE", organization=self.organization)
        self.box = Box.objects.create(organization=self.organization, strain=strain, global_code="SOURCE.001")
        self.client.force_login(self.user)

    def prepare(self, *, box=None, destination=None, active_organization=None):
        return self.client.post(
            reverse("api_box_transfer_create"),
            data=json.dumps({
                "box": (box or self.box).pk,
                "to_organization": (destination or self.destination).pk,
                "polyp_count": 12,
                "notes": "Transport notes",
            }),
            content_type="application/json",
            HTTP_X_ORGANIZATION_ID=str((active_organization or self.organization).pk),
        )

    def test_success_keeps_contract_and_source_state(self):
        response = self.prepare()
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(set(response.json()), {
            "id", "box", "to_organization", "transfer_date", "polyp_count", "notes",
            "prepared_by", "parent_box_codes", "origin",
        })
        transfer = BoxTransfer.objects.get()
        self.assertEqual(transfer.from_organization, self.organization)
        self.assertEqual(transfer.status, BoxTransfer.Status.PLANNED)
        self.assertEqual(transfer.user, self.user)
        audit = AuditLog.objects.get()
        self.assertEqual(audit.organization, self.organization)
        self.assertEqual(audit.metadata, {
            "transfer_id": transfer.pk, "box_id": self.box.pk, "code_global": self.box.global_code,
            "to_organization": self.destination.name, "date": transfer.transfer_date.isoformat(),
            "polypes": 12, "note": "Transport notes",
        })
        self.box.refresh_from_db()
        self.assertEqual(self.box.organization, self.organization)
        self.assertEqual(self.box.global_code, "SOURCE.001")
        self.assertEqual(self.box.status, Box.Status.ACTIVE)
        self.assertFalse(BoxLocation.objects.exists())
        self.assertFalse(BiologicalMeasurement.objects.exists())

    def test_audit_failure_rolls_back_preparation(self):
        with patch("apps.cultures.transfer_v1.AuditLog.objects.create", side_effect=RuntimeError("audit failed")):
            with self.assertRaisesMessage(RuntimeError, "audit failed"):
                self.prepare()
        self.assertFalse(BoxTransfer.objects.exists())
        self.assertFalse(AuditLog.objects.exists())
        self.assertTrue(Box.objects.filter(pk=self.box.pk).exists())

    def test_same_or_inactive_destination_is_rejected(self):
        self.assertEqual(self.prepare(destination=self.organization).status_code, 400)
        self.destination.is_active = False
        self.destination.save(update_fields=["is_active"])
        self.assertEqual(self.prepare().status_code, 400)
        self.assertFalse(BoxTransfer.objects.exists())
        self.assertFalse(AuditLog.objects.exists())

    def test_source_from_another_organization_is_rejected_even_for_its_admin(self):
        OrganizationMembership.objects.create(
            user=self.user, organization=self.destination, role=OrganizationMembership.Role.ADMIN,
        )
        foreign_strain = Strain.objects.create(
            organization=self.destination, species=self.box.strain.species, code="FOREIGN",
        )
        foreign_box = Box.objects.create(
            organization=self.destination, strain=foreign_strain, global_code="FOREIGN.001",
        )
        response = self.prepare(box=foreign_box, destination=self.organization)
        self.assertEqual(response.status_code, 403)
        self.assertFalse(BoxTransfer.objects.exists())
        self.assertFalse(AuditLog.objects.exists())

    def test_preparation_requires_admin_and_authorized_active_context(self):
        self.membership.role = OrganizationMembership.Role.LAB_TECHNICIAN
        self.membership.save(update_fields=["role"])
        self.assertEqual(self.prepare().status_code, 403)
        self.assertEqual(self.prepare(active_organization=self.destination).status_code, 403)
        self.assertFalse(BoxTransfer.objects.exists())
        self.assertFalse(AuditLog.objects.exists())
