from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection
from django.test import TestCase

from apps.audit.models import AuditLog
from apps.measurements.models import BiologicalMeasurement
from apps.organizations.models import Organization
from apps.taxonomy.models import GlobalStrainIdentity, LocalStrainIdentity, Species, Strain

from .models import Box, BoxLocation, BoxTransfer, BoxTransferImport, ThermalZone
from .serializers import BoxTransferCreateSerializer
from .transfer_v1 import (
    StrainOwnershipError, TransferV1ValidationError, import_transfer_v1, prepare_transfer_v1,
)


class TransferStrainOwnershipTests(TestCase):
    def setUp(self):
        self.destination = Organization.objects.create(name="Destination", slug="destination")
        self.foreign = Organization.objects.create(name="Foreign", slug="foreign")
        self.user = get_user_model().objects.create_user(username="importer", email="importer@example.org", password="secret")
        self.zone = ThermalZone.objects.create(
            organization=self.destination, name="Tank", zone_type=ThermalZone.ZoneType.CABINET,
            target_temperature_c=15,
        )
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
        return import_transfer_v1(
            source={**self.source, **source_changes}, organization=self.destination,
            zone=self.zone, user=self.user, global_code=global_code,
        )

    def test_new_strain_belongs_to_destination_and_reuse_preserves_metadata(self):
        self.import_transfer()
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

        self.import_transfer(
            global_code="7-SRC.002", transfer_id="transfer-2", strain_origin_code="CHANGED"
        )
        self.assertEqual(Strain.objects.count(), 1)
        self.assertEqual(Strain.objects.get(pk=strain.pk).origin_code, "SRC")

    def test_reuses_destination_owned_strain(self):
        species = Species.objects.create(scientific_name="Test species")
        strain = Strain.objects.create(species=species, code="7-SRC", organization=self.destination, origin_code="LOCAL")
        self.import_transfer()
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
                with self.assertRaises(StrainOwnershipError):
                    self.import_transfer()
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

    def test_sequential_replay_keeps_legacy_error_and_identity(self):
        self.import_transfer()
        with self.assertRaisesMessage(TransferV1ValidationError, "Ce transfert a déjà été importé."):
            self.import_transfer(
                global_code="OTHER.001", species_scientific_name="Another species",
                strain_code="OTHER", source_global_code="OTHER-SOURCE.001",
            )
        self.assertEqual(Species.objects.count(), 1)
        self.assertEqual(Box.objects.count(), 1)
        self.assertEqual(BoxTransferImport.objects.count(), 1)
        self.assertEqual(AuditLog.objects.count(), 1)

    def test_database_replay_constraint_rolls_back_before_translation(self):
        self.import_transfer()
        # Reproduce the late uniqueness failure with a real INSERT rather than
        # mocking IntegrityError. A changed payload still has the same v1 key.
        with patch("apps.cultures.transfer_v1._reject_replay"):
            with self.assertRaisesMessage(TransferV1ValidationError, "Ce transfert a déjà été importé."):
                self.import_transfer(
                    global_code="OTHER.001", species_scientific_name="Another species",
                    strain_code="OTHER",
                )
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
        self.import_transfer(**extra)
        self.assertEqual(BoxTransferImport.objects.get().source_data, {**self.source, **extra})
        self.assertEqual(BiologicalMeasurement.objects.get().culture_status, "good")

    def test_import_count_validation_details_remain_unchanged(self):
        cases = (
            ("invalid", "Le nombre de polypes est invalide."),
            ("0", "Le nombre de polypes doit être positif."),
            ("-1", "Le nombre de polypes doit être positif."),
        )
        for count, detail in cases:
            with self.subTest(count=count), self.assertRaises(TransferV1ValidationError) as error:
                self.import_transfer(transferred_polyp_count=count)
            self.assertEqual(error.exception.detail, {"source_data": detail})
        self.assertFalse(Species.objects.exists())
        self.assertFalse(Box.objects.exists())


class TransferPreparationBoundaryTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name="Source", slug="source")
        self.destination = Organization.objects.create(name="Destination", slug="destination")
        self.user = get_user_model().objects.create_user(username="preparer", email="preparer@example.org")
        species = Species.objects.create(scientific_name="Preparation species")
        strain = Strain.objects.create(species=species, code="SOURCE", organization=self.organization)
        self.box = Box.objects.create(organization=self.organization, strain=strain, global_code="SOURCE.001")

    def prepare(self):
        return prepare_transfer_v1(
            box=self.box, to_organization=self.destination, user=self.user,
            polyp_count=12, notes="Transport notes",
        )

    def test_success_keeps_history_serializable_and_source_state(self):
        transfer = self.prepare()
        self.assertEqual(set(BoxTransferCreateSerializer(transfer).data), {
            "id", "box", "to_organization", "transfer_date", "polyp_count", "notes",
            "prepared_by", "parent_box_codes", "origin",
        })
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
