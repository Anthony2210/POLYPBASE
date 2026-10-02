from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.models import ProtectedError
from django.test import TestCase, TransactionTestCase

from apps.cultures.models import Box
from apps.organizations.models import Organization

from .models import (
    BiologicalProvenance,
    GlobalStrainIdentity,
    OrganizationProvenanceCode,
    OrganizationSpeciesCode,
    Origin,
    Species,
    Strain,
)


class LocalCodeSchemaTests(TestCase):
    def setUp(self):
        self.first = Organization.objects.create(name="First institution")
        self.second = Organization.objects.create(name="Second institution")
        self.species = Species.objects.create(scientific_name="Test species", genus_species_code="OLD")
        self.other_species = Species.objects.create(scientific_name="Other species")
        self.provenance = BiologicalProvenance.objects.create(name="Source concept")
        self.other_provenance = BiologicalProvenance.objects.create(name="Another source")

    def test_species_codes_are_local_and_unambiguous(self):
        assignment = OrganizationSpeciesCode.objects.create(
            organization=self.first, species=self.species, code="AAA"
        )
        OrganizationSpeciesCode.objects.create(
            organization=self.second, species=self.species, code="BBB"
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            OrganizationSpeciesCode.objects.create(
                organization=self.first, species=self.other_species, code="AAA"
            )
        with self.assertRaises(IntegrityError), transaction.atomic():
            OrganizationSpeciesCode.objects.create(
                organization=self.first, species=self.species, code="CCC"
            )
        assignment.code = "CCC"
        assignment.save(update_fields=["code"])
        self.assertEqual(assignment.pk, OrganizationSpeciesCode.objects.get(
            organization=self.first, species=self.species
        ).pk)
        self.assertEqual(Species.objects.get(pk=self.species.pk).genus_species_code, "OLD")

    def test_provenance_codes_are_local_and_unambiguous(self):
        assignment = OrganizationProvenanceCode.objects.create(
            organization=self.first, biological_provenance=self.provenance, code="AAA"
        )
        OrganizationProvenanceCode.objects.create(
            organization=self.second, biological_provenance=self.provenance, code="BBB"
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            OrganizationProvenanceCode.objects.create(
                organization=self.first, biological_provenance=self.other_provenance, code="AAA"
            )
        with self.assertRaises(IntegrityError), transaction.atomic():
            OrganizationProvenanceCode.objects.create(
                organization=self.first, biological_provenance=self.provenance, code="CCC"
            )
        assignment.code = "CCC"
        assignment.save(update_fields=["code"])
        self.assertEqual(assignment.pk, OrganizationProvenanceCode.objects.get(
            organization=self.first, biological_provenance=self.provenance
        ).pk)

    def test_same_provenance_code_can_be_reused_by_another_organization(self):
        first_assignment = OrganizationProvenanceCode.objects.create(
            organization=self.first, biological_provenance=self.provenance, code="ABC"
        )
        second_assignment = OrganizationProvenanceCode.objects.create(
            organization=self.second, biological_provenance=self.other_provenance, code="ABC"
        )

        self.assertNotEqual(first_assignment.pk, second_assignment.pk)
        self.assertEqual(
            set(OrganizationProvenanceCode.objects.filter(code="ABC").values_list(
                "organization_id", "biological_provenance_id"
            )),
            {
                (self.first.pk, self.provenance.pk),
                (self.second.pk, self.other_provenance.pk),
            },
        )

    def test_assignments_protect_referenced_concepts_and_organizations(self):
        OrganizationSpeciesCode.objects.create(
            organization=self.first, species=self.species, code="AAA"
        )
        OrganizationProvenanceCode.objects.create(
            organization=self.first, biological_provenance=self.provenance, code="BBB"
        )
        for parent in (self.first, self.species, self.provenance):
            with self.assertRaises(ProtectedError):
                parent.delete()
        self.assertEqual(OrganizationSpeciesCode.objects.count(), 1)
        self.assertEqual(OrganizationProvenanceCode.objects.count(), 1)

    def test_shared_provenance_and_legacy_objects_are_independent(self):
        unassigned = BiologicalProvenance.objects.create()

        self.assertEqual({field.name for field in unassigned._meta.fields}, {"id", "name"})
        self.assertFalse(OrganizationProvenanceCode.objects.filter(biological_provenance=unassigned).exists())
        origin = Origin.objects.create(description="Historical acquisition")
        identity = GlobalStrainIdentity.objects.create()
        strain = Strain.objects.create(
            species=self.species, organization=self.first, global_identity=identity,
            origin=origin, origin_code="OLD", code="OLD-1",
        )
        box = Box.objects.create(
            organization=self.first, strain=strain, origin=origin,
            global_code="OLD-1.001", box_number="001",
        )
        legacy = Strain.objects.create(species=self.other_species, code="LEG-1")
        self.assertIsNone(legacy.organization_id)
        self.assertEqual(strain.origin_code, "OLD")
        self.assertEqual(box.global_code, "OLD-1.001")
        self.assertEqual(box.origin_id, origin.pk)
        self.assertEqual(strain.global_identity_id, identity.pk)
        self.assertEqual(Origin.objects.count(), 1)
        self.assertFalse(OrganizationSpeciesCode.objects.exists())
        self.assertFalse(OrganizationProvenanceCode.objects.exists())
        with self.assertRaises(IntegrityError), transaction.atomic():
            Strain.objects.create(species=self.species, organization=self.second, code="OLD-1")


class LocalCodeSchemaMigrationTests(TransactionTestCase):
    def test_populated_upgrade_does_not_infer_local_codes_or_provenance(self):
        before = ("taxonomy", "0004_strain_organization")
        after = ("taxonomy", "0005_biologicalprovenance_organizationprovenancecode_and_more")
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        # Keep the physical schema aligned with the historical cross-app models.
        other_targets = [node for node in latest if node[0] not in {"taxonomy", "organizations"}]
        organizations = ("organizations", "0001_initial")
        before_targets = other_targets + [before, organizations]
        after_targets = other_targets + [after, organizations]
        try:
            # A preceding migration test may have left another historical state.
            executor.migrate(latest)
            executor = MigrationExecutor(connection)
            executor.migrate(before_targets)
            old_apps = executor.loader.project_state(before_targets).apps
            OldOrganization = old_apps.get_model("organizations", "Organization")
            OldSpecies = old_apps.get_model("taxonomy", "Species")
            OldOrigin = old_apps.get_model("taxonomy", "Origin")
            OldStrain = old_apps.get_model("taxonomy", "Strain")
            OldIdentity = old_apps.get_model("taxonomy", "GlobalStrainIdentity")
            OldBox = old_apps.get_model("cultures", "Box")
            organization = OldOrganization.objects.create(name="Historical institution")
            species = OldSpecies.objects.create(scientific_name="Historical species", genus_species_code="HIS")
            origin = OldOrigin.objects.create(description="Historical event", origin_institution_name="Provider")
            identity = OldIdentity.objects.create()
            strain = OldStrain.objects.create(
                species=species, organization=organization, global_identity=identity,
                code="HIS-OLD-1", number=1, origin_code="OLD", origin=origin,
            )
            unknown = OldStrain.objects.create(species=species, code="HIS-OLD-2", origin_code="UNK")
            box = OldBox.objects.create(
                organization=organization, strain=strain, origin=origin,
                global_code="HIS-OLD-1.001", box_number="001",
            )

            executor = MigrationExecutor(connection)
            executor.migrate(after_targets)
            new_apps = executor.loader.project_state(after_targets).apps
            NewStrain = new_apps.get_model("taxonomy", "Strain")
            self.assertEqual(new_apps.get_model("taxonomy", "OrganizationSpeciesCode").objects.count(), 0)
            self.assertEqual(new_apps.get_model("taxonomy", "BiologicalProvenance").objects.count(), 0)
            self.assertEqual(new_apps.get_model("taxonomy", "OrganizationProvenanceCode").objects.count(), 0)
            self.assertEqual(new_apps.get_model("taxonomy", "Species").objects.get(pk=species.pk).genus_species_code, "HIS")
            self.assertEqual(new_apps.get_model("taxonomy", "Origin").objects.get(pk=origin.pk).description, "Historical event")
            self.assertEqual(new_apps.get_model("taxonomy", "GlobalStrainIdentity").objects.get(pk=identity.pk).global_id, identity.global_id)
            self.assertEqual(new_apps.get_model("organizations", "Organization").objects.get(pk=organization.pk).name, "Historical institution")
            self.assertEqual(set(NewStrain.objects.values_list("pk", flat=True)), {strain.pk, unknown.pk})
            upgraded = NewStrain.objects.get(pk=strain.pk)
            self.assertEqual(
                (upgraded.organization_id, upgraded.global_identity_id, upgraded.species_id,
                 upgraded.code, upgraded.number, upgraded.origin_code, upgraded.origin_id),
                (organization.pk, identity.pk, species.pk, "HIS-OLD-1", 1, "OLD", origin.pk),
            )
            self.assertIsNone(NewStrain.objects.get(pk=unknown.pk).organization_id)
            self.assertEqual(NewStrain.objects.get(pk=unknown.pk).origin_code, "UNK")
            upgraded_box = new_apps.get_model("cultures", "Box").objects.get(pk=box.pk)
            self.assertEqual(
                (upgraded_box.organization_id, upgraded_box.strain_id, upgraded_box.origin_id,
                 upgraded_box.global_code, upgraded_box.box_number),
                (organization.pk, strain.pk, origin.pk, "HIS-OLD-1.001", "001"),
            )
        finally:
            MigrationExecutor(connection).migrate(latest)
