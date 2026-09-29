from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.models import ProtectedError
from django.test import TestCase, TransactionTestCase

from apps.cultures.models import Box
from apps.organizations.models import Organization

from .models import (
    BiologicalProvenance,
    GlobalStrainIdentity,
    LocalStrainIdentity,
    OrganizationProvenanceCode,
    OrganizationSpeciesCode,
    Origin,
    Species,
    Strain,
)


class LocalStrainIdentityTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name="First institution")
        self.other_organization = Organization.objects.create(name="Second institution")
        self.species = Species.objects.create(scientific_name="Test species", genus_species_code="OLD")
        self.assignment = OrganizationSpeciesCode.objects.create(
            organization=self.organization, species=self.species, code="AAA"
        )
        self.strain = Strain.objects.create(
            organization=self.organization, species=self.species, code="OLD-1"
        )

    def test_strains_do_not_require_or_automatically_receive_local_identity(self):
        legacy = Strain.objects.create(species=self.species, code="LEG-1")
        self.assertIsNone(legacy.organization_id)
        self.assertFalse(LocalStrainIdentity.objects.exists())
        self.assertFalse(LocalStrainIdentity.objects.filter(strain__in=[self.strain, legacy]).exists())

    def test_one_to_one_and_required_assignment(self):
        local = LocalStrainIdentity.objects.create(
            strain=self.strain, species_code_assignment=self.assignment
        )
        self.assertEqual(self.strain.local_identity.pk, local.pk)
        with self.assertRaises(IntegrityError), transaction.atomic():
            LocalStrainIdentity.objects.create(
                strain=self.strain, species_code_assignment=self.assignment
            )
        other = Strain.objects.create(species=self.species, code="OLD-2")
        with self.assertRaises(IntegrityError), transaction.atomic():
            LocalStrainIdentity.objects.create(strain=other, species_code_assignment=None)
        with self.assertRaises(IntegrityError), transaction.atomic():
            LocalStrainIdentity.objects.create(strain=other, species_code_assignment_id=999999)
            # SQLite defers FK checks until commit; TestCase wraps the test in a transaction.
            connection.check_constraints()
        self.assertEqual(LocalStrainIdentity.objects.count(), 1)

    def test_assignment_is_normalized_source_for_organization_and_species(self):
        local = LocalStrainIdentity.objects.create(
            strain=self.strain, species_code_assignment=self.assignment
        )
        self.assertEqual(
            {field.name for field in local._meta.fields},
            {"id", "strain", "species_code_assignment", "provenance_code_assignment"},
        )
        self.assertEqual(local.species_code_assignment.organization_id, self.organization.pk)
        self.assertEqual(local.species_code_assignment.species_id, self.species.pk)
        self.assertEqual(local.species_code_assignment.code, "AAA")
        self.assertIsNone(local.provenance_code_assignment_id)
        self.assertEqual(Strain.objects.get(pk=self.strain.pk).code, "OLD-1")

    def test_protected_relationships_preserve_identity(self):
        local = LocalStrainIdentity.objects.create(
            strain=self.strain, species_code_assignment=self.assignment
        )
        with self.assertRaises(ProtectedError):
            self.strain.delete()
        with self.assertRaises(ProtectedError):
            self.assignment.delete()
        self.assertTrue(LocalStrainIdentity.objects.filter(pk=local.pk).exists())
        self.assertTrue(Strain.objects.filter(pk=self.strain.pk).exists())
        self.assertTrue(OrganizationSpeciesCode.objects.filter(pk=self.assignment.pk).exists())

    def test_known_provenance_assignment_is_protected(self):
        provenance = BiologicalProvenance.objects.create(name="Source")
        bbb = OrganizationProvenanceCode.objects.create(
            organization=self.organization, biological_provenance=provenance, code="BBB"
        )
        local = LocalStrainIdentity.objects.create(
            strain=self.strain, species_code_assignment=self.assignment,
            provenance_code_assignment=bbb,
        )
        self.assertEqual(local.provenance_code_assignment.biological_provenance_id, provenance.pk)
        with self.assertRaises(ProtectedError):
            bbb.delete()
        self.assertTrue(LocalStrainIdentity.objects.filter(pk=local.pk).exists())
        self.assertTrue(OrganizationProvenanceCode.objects.filter(pk=bbb.pk).exists())

    def test_cross_organization_assignment_is_currently_representable(self):
        # FKs cannot compare the assignment to Strain's compatibility fields.
        other_assignment = OrganizationSpeciesCode.objects.create(
            organization=self.other_organization, species=self.species, code="AAA"
        )
        local = LocalStrainIdentity.objects.create(
            strain=self.strain, species_code_assignment=other_assignment
        )
        self.assertNotEqual(local.species_code_assignment.organization_id, self.strain.organization_id)
        self.assertEqual(local.species_code_assignment.species_id, self.strain.species_id)
        # The future scoped writer must reject this; no runtime writer exists yet.

    def test_legacy_fields_boxes_and_global_uniqueness_remain_unchanged(self):
        origin = Origin.objects.create(description="Historical acquisition")
        identity = GlobalStrainIdentity.objects.create()
        self.strain.global_identity = identity
        self.strain.origin = origin
        self.strain.origin_code = "OLD"
        self.strain.number = 1
        self.strain.save()
        box = Box.objects.create(
            organization=self.organization, strain=self.strain, origin=origin,
            global_code="OLD-1.001", box_number="001",
        )
        LocalStrainIdentity.objects.create(
            strain=self.strain, species_code_assignment=self.assignment
        )
        self.strain.refresh_from_db()
        box.refresh_from_db()
        self.assertEqual(
            (self.strain.organization_id, self.strain.species_id, self.strain.code,
             self.strain.number, self.strain.origin_code, self.strain.origin_id,
             self.strain.global_identity_id),
            (self.organization.pk, self.species.pk, "OLD-1", 1, "OLD", origin.pk, identity.pk),
        )
        self.assertEqual((box.origin_id, box.global_code, box.box_number),
                         (origin.pk, "OLD-1.001", "001"))
        with self.assertRaises(IntegrityError), transaction.atomic():
            Strain.objects.create(
                organization=self.other_organization, species=self.species, code="OLD-1"
            )


class LocalStrainIdentityMigrationTests(TransactionTestCase):
    def test_populated_upgrade_does_not_infer_local_identity(self):
        before = ("taxonomy", "0005_biologicalprovenance_organizationprovenancecode_and_more")
        after = ("taxonomy", "0006_localstrainidentity")
        cultures = ("cultures", "0007_box_inventory_lifecycle")
        try:
            executor = MigrationExecutor(connection)
            executor.migrate([before, cultures])
            old_apps = executor.loader.project_state([before, cultures]).apps
            OldOrganization = old_apps.get_model("organizations", "Organization")
            OldSpecies = old_apps.get_model("taxonomy", "Species")
            OldAssignment = old_apps.get_model("taxonomy", "OrganizationSpeciesCode")
            OldOrigin = old_apps.get_model("taxonomy", "Origin")
            OldIdentity = old_apps.get_model("taxonomy", "GlobalStrainIdentity")
            OldStrain = old_apps.get_model("taxonomy", "Strain")
            OldBox = old_apps.get_model("cultures", "Box")

            organization = OldOrganization.objects.create(name="Historical institution")
            species = OldSpecies.objects.create(scientific_name="Historical species", genus_species_code="HIS")
            assignment = OldAssignment.objects.create(
                organization=organization, species=species, code="AAA"
            )
            origin = OldOrigin.objects.create(description="Historical event")
            identity = OldIdentity.objects.create()
            strain = OldStrain.objects.create(
                organization=organization, species=species, global_identity=identity,
                origin=origin, code="HIS-OLD-1", number=1, origin_code="OLD",
            )
            unknown = OldStrain.objects.create(species=species, code="HIS-OLD-2")
            box = OldBox.objects.create(
                organization=organization, strain=strain, origin=origin,
                global_code="HIS-OLD-1.001", box_number="001",
            )

            executor = MigrationExecutor(connection)
            executor.migrate([after, cultures])
            new_apps = executor.loader.project_state([after, cultures]).apps
            self.assertEqual(new_apps.get_model("taxonomy", "LocalStrainIdentity").objects.count(), 0)
            self.assertEqual(
                new_apps.get_model("taxonomy", "OrganizationSpeciesCode").objects.get(pk=assignment.pk).code,
                "AAA",
            )
            self.assertEqual(new_apps.get_model("taxonomy", "Species").objects.get(pk=species.pk).genus_species_code, "HIS")
            self.assertEqual(new_apps.get_model("taxonomy", "GlobalStrainIdentity").objects.get(pk=identity.pk).global_id, identity.global_id)
            NewStrain = new_apps.get_model("taxonomy", "Strain")
            upgraded = NewStrain.objects.get(pk=strain.pk)
            self.assertEqual(
                (upgraded.organization_id, upgraded.species_id, upgraded.global_identity_id,
                 upgraded.origin_id, upgraded.code, upgraded.number, upgraded.origin_code),
                (organization.pk, species.pk, identity.pk, origin.pk, "HIS-OLD-1", 1, "OLD"),
            )
            self.assertIsNone(NewStrain.objects.get(pk=unknown.pk).organization_id)
            self.assertEqual(NewStrain.objects.get(pk=unknown.pk).code, "HIS-OLD-2")
            upgraded_box = new_apps.get_model("cultures", "Box").objects.get(pk=box.pk)
            self.assertEqual(
                (upgraded_box.origin_id, upgraded_box.global_code, upgraded_box.box_number),
                (origin.pk, "HIS-OLD-1.001", "001"),
            )
        finally:
            MigrationExecutor(connection).migrate([
                ("taxonomy", "0007_localstrainidentity_provenance_code_assignment"), cultures
            ])


class LocalStrainProvenanceMigrationTests(TransactionTestCase):
    def test_populated_upgrade_leaves_existing_identities_unknown(self):
        before = ("taxonomy", "0006_localstrainidentity")
        after = ("taxonomy", "0007_localstrainidentity_provenance_code_assignment")
        cultures = ("cultures", "0007_box_inventory_lifecycle")
        try:
            executor = MigrationExecutor(connection)
            executor.migrate([before, cultures])
            old_apps = executor.loader.project_state([before, cultures]).apps
            OldOrganization = old_apps.get_model("organizations", "Organization")
            OldSpecies = old_apps.get_model("taxonomy", "Species")
            OldStrain = old_apps.get_model("taxonomy", "Strain")
            OldOrigin = old_apps.get_model("taxonomy", "Origin")
            OldBox = old_apps.get_model("cultures", "Box")
            OldAAA = old_apps.get_model("taxonomy", "OrganizationSpeciesCode")
            OldBBB = old_apps.get_model("taxonomy", "OrganizationProvenanceCode")
            OldProvenance = old_apps.get_model("taxonomy", "BiologicalProvenance")
            OldLocal = old_apps.get_model("taxonomy", "LocalStrainIdentity")

            organization = OldOrganization.objects.create(name="Historical institution")
            species = OldSpecies.objects.create(scientific_name="Historical species")
            aaa = OldAAA.objects.create(organization=organization, species=species, code="AAA")
            provenance = OldProvenance.objects.create(name="Historical source")
            bbb = OldBBB.objects.create(
                organization=organization, biological_provenance=provenance, code="OLD"
            )
            origin = OldOrigin.objects.create(description="Historical acquisition")
            strain = OldStrain.objects.create(
                organization=organization, species=species, origin=origin,
                origin_code="OLD", code="OLD-1",
            )
            local = OldLocal.objects.create(strain=strain, species_code_assignment=aaa)
            without_identity = OldStrain.objects.create(
                organization=organization, species=species, code="OLD-2"
            )
            box = OldBox.objects.create(
                organization=organization, strain=strain, origin=origin,
                global_code="OLD-1.001", box_number="001",
            )

            executor = MigrationExecutor(connection)
            executor.migrate([after, cultures])
            new_apps = executor.loader.project_state([after, cultures]).apps
            NewLocal = new_apps.get_model("taxonomy", "LocalStrainIdentity")
            NewStrain = new_apps.get_model("taxonomy", "Strain")
            upgraded = NewLocal.objects.get(pk=local.pk)
            self.assertEqual(upgraded.species_code_assignment_id, aaa.pk)
            self.assertIsNone(upgraded.provenance_code_assignment_id)
            self.assertEqual(NewLocal.objects.count(), 1)
            self.assertFalse(NewLocal.objects.filter(strain_id=without_identity.pk).exists())
            self.assertEqual(new_apps.get_model("taxonomy", "OrganizationProvenanceCode").objects.get(pk=bbb.pk).code, "OLD")
            self.assertEqual(
                (NewStrain.objects.get(pk=strain.pk).code,
                 NewStrain.objects.get(pk=strain.pk).origin_code,
                 NewStrain.objects.get(pk=strain.pk).origin_id),
                ("OLD-1", "OLD", origin.pk),
            )
            upgraded_box = new_apps.get_model("cultures", "Box").objects.get(pk=box.pk)
            self.assertEqual((upgraded_box.global_code, upgraded_box.box_number, upgraded_box.origin_id),
                             ("OLD-1.001", "001", origin.pk))
        finally:
            MigrationExecutor(connection).migrate([after, cultures])
