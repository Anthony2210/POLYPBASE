import json

from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.models import ProtectedError
from django.test import TestCase, TransactionTestCase
from django.urls import reverse

from apps.accounts.models import OrganizationMembership
from apps.cultures.models import Box
from apps.organizations.models import Organization

from .models import GlobalStrainIdentity, LocalStrainIdentity, OrganizationSpeciesCode, Species, Strain
from .serializers import StrainReferenceSerializer, StrainReferenceWriteSerializer


class StrainOrganizationTests(TestCase):
    def setUp(self):
        self.species = Species.objects.create(scientific_name="Test species")
        self.first_organization = Organization.objects.create(name="First institution")
        self.second_organization = Organization.objects.create(name="Second institution")

    def test_legacy_strain_and_box_remain_unassigned(self):
        strain = Strain.objects.create(species=self.species, code="LEG-1")
        first_box = Box.objects.create(
            organization=self.first_organization,
            strain=strain,
            global_code="LEG-1.001",
            box_number="001",
        )
        second_box = Box.objects.create(
            organization=self.second_organization,
            strain=strain,
            global_code="LEG-1.002",
            box_number="002",
        )

        strain.refresh_from_db()
        self.assertIsNone(strain.organization_id)
        self.assertIsNone(strain.global_identity_id)
        self.assertEqual({box.pk for box in strain.boxes.all()}, {first_box.pk, second_box.pk})

    def test_institution_can_own_multiple_strains_sharing_an_identity_across_institutions(self):
        identity = GlobalStrainIdentity.objects.create()
        first = Strain.objects.create(
            species=self.species, code="LOC-A", organization=self.first_organization,
            global_identity=identity,
        )
        another = Strain.objects.create(
            species=self.species, code="LOC-A2", organization=self.first_organization,
        )
        second = Strain.objects.create(
            species=self.species, code="LOC-B", organization=self.second_organization,
            global_identity=identity,
        )

        self.assertEqual(set(self.first_organization.strains.all()), {first, another})
        self.assertEqual(set(identity.strains.all()), {first, second})
        first.code = "LOC-A-UPDATED"
        first.save(update_fields=["code"])
        first.delete()
        self.assertTrue(Organization.objects.filter(pk=self.first_organization.pk).exists())
        self.assertTrue(GlobalStrainIdentity.objects.filter(pk=identity.pk).exists())
        self.assertEqual(second.global_identity_id, identity.pk)
        with self.assertRaises(ProtectedError):
            self.first_organization.delete()
        with self.assertRaises(ProtectedError):
            self.second_organization.delete()
        self.assertTrue(Organization.objects.filter(pk=self.first_organization.pk).exists())

    def test_existing_species_code_uniqueness_is_unchanged(self):
        Strain.objects.create(
            species=self.species, code="SAME", organization=self.first_organization,
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            Strain.objects.create(
                species=self.species, code="SAME", organization=self.second_organization,
            )
        self.assertEqual(Strain.objects.filter(species=self.species, code="SAME").count(), 1)

    def test_existing_serializers_do_not_expose_or_accept_organization(self):
        strain = Strain.objects.create(
            species=self.species, code="LOC-1", organization=self.first_organization,
        )
        self.assertNotIn("organization", StrainReferenceSerializer(strain).data)
        serializer = StrainReferenceWriteSerializer(data={
            "species": self.species.pk,
            "code": "LEG-2",
            "translations": {"fr": {"name": "Souche 2"}},
            "organization": self.first_organization.pk,
        })
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertNotIn("organization", serializer.validated_data)
        self.assertIsNone(serializer.save().organization_id)

    def test_api_creation_assigns_active_organization_without_exposing_ownership(self):
        assignment = OrganizationSpeciesCode.objects.create(
            organization=self.first_organization, species=self.species, code="AAA"
        )
        user = get_user_model().objects.create_user(username="strain_admin", email="strain_admin@example.org", password="secret")
        OrganizationMembership.objects.create(
            user=user, organization=self.first_organization,
            role=OrganizationMembership.Role.ADMIN,
        )
        self.client.force_login(user)
        response = self.client.post(
            reverse("api_taxonomy_strains"),
            data=json.dumps({
                "species": self.species.pk,
                "code": "API-1",
                "translations": {"fr": {"name": "Souche API"}},
                "organization": self.first_organization.pk,
            }),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertNotIn("organization", response.json())
        strain = Strain.objects.get(pk=response.json()["id"])
        self.assertEqual(strain.organization_id, self.first_organization.pk)
        self.assertEqual(LocalStrainIdentity.objects.get(strain=strain).species_code_assignment, assignment)
        listing = self.client.get(reverse("api_taxonomy_references"))
        self.assertEqual(listing.status_code, 200)
        self.assertNotIn("organization", listing.json()["strains"][0])


class StrainOrganizationMigrationTests(TransactionTestCase):
    def test_populated_strain_is_not_assigned_an_organization(self):
        before = ("taxonomy", "0003_globalstrainidentity_strain_global_identity")
        after = ("taxonomy", "0004_strain_organization")
        try:
            executor = MigrationExecutor(connection)
            executor.migrate([before])
            old_apps = executor.loader.project_state([before]).apps
            OldOrganization = old_apps.get_model("organizations", "Organization")
            OldSpecies = old_apps.get_model("taxonomy", "Species")
            OldStrain = old_apps.get_model("taxonomy", "Strain")
            OldIdentity = old_apps.get_model("taxonomy", "GlobalStrainIdentity")
            organization = OldOrganization.objects.create(name="Historical institution")
            species = OldSpecies.objects.create(scientific_name="Historical species")
            identity = OldIdentity.objects.create()
            unlinked = OldStrain.objects.create(species=species, code="HIS-1")
            linked = OldStrain.objects.create(
                species=species, code="HIS-2", global_identity=identity,
            )

            executor = MigrationExecutor(connection)
            executor.migrate([after])
            new_apps = executor.loader.project_state([after]).apps
            NewStrain = new_apps.get_model("taxonomy", "Strain")
            NewIdentity = new_apps.get_model("taxonomy", "GlobalStrainIdentity")
            NewOrganization = new_apps.get_model("organizations", "Organization")
            self.assertEqual(set(NewStrain.objects.values_list("pk", flat=True)), {unlinked.pk, linked.pk})
            self.assertIsNone(NewStrain.objects.get(pk=unlinked.pk).organization_id)
            self.assertIsNone(NewStrain.objects.get(pk=linked.pk).organization_id)
            self.assertIsNone(NewStrain.objects.get(pk=unlinked.pk).global_identity_id)
            self.assertEqual(NewStrain.objects.get(pk=linked.pk).global_identity_id, identity.pk)
            self.assertEqual(NewIdentity.objects.count(), 1)
            self.assertEqual(list(NewOrganization.objects.values_list("pk", flat=True)), [organization.pk])
        finally:
            MigrationExecutor(connection).migrate([after])
