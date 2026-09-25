import uuid

from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.models import ProtectedError
from django.test import TestCase, TransactionTestCase

from .models import GlobalStrainIdentity, Species, Strain
from .serializers import StrainReferenceSerializer, StrainReferenceWriteSerializer


class GlobalStrainIdentityTests(TestCase):
    def test_identity_is_independent_and_has_a_unique_opaque_id(self):
        first = GlobalStrainIdentity.objects.create()
        second = GlobalStrainIdentity.objects.create()

        self.assertIsInstance(first.global_id, uuid.UUID)
        self.assertEqual(first.global_id.version, 4)
        self.assertNotEqual(first.global_id, second.global_id)
        self.assertEqual(GlobalStrainIdentity.objects.get(global_id=first.global_id), first)
        with self.assertRaises(IntegrityError), transaction.atomic():
            GlobalStrainIdentity.objects.create(global_id=first.global_id)
        self.assertEqual(
            {field.name for field in GlobalStrainIdentity._meta.fields},
            {"id", "global_id"},
        )

    def test_legacy_strain_remains_unlinked_and_identity_can_be_shared(self):
        species = Species.objects.create(scientific_name="Aurelia aurita")
        legacy = Strain.objects.create(species=species, code="AAU-OLD-1")
        identity = GlobalStrainIdentity.objects.create()
        first = Strain.objects.create(
            species=species, code="AAU-LAB-2", global_identity=identity
        )
        second = Strain.objects.create(
            species=species, code="AAU-NEW-3", global_identity=identity
        )

        legacy.refresh_from_db()
        self.assertIsNone(legacy.global_identity_id)
        self.assertEqual(set(identity.strains.all()), {first, second})
        self.assertNotIn(first.code, str(identity.global_id))
        self.assertNotIn(second.code, str(identity.global_id))
        self.assertNotIn(species.scientific_name, str(identity.global_id))

        first.code = "AAU-CHANGED-2"
        first.save(update_fields=["code"])
        first.delete()
        self.assertTrue(GlobalStrainIdentity.objects.filter(pk=identity.pk).exists())
        self.assertEqual(second.global_identity_id, identity.pk)
        with self.assertRaises(ProtectedError):
            identity.delete()
        self.assertTrue(GlobalStrainIdentity.objects.filter(pk=identity.pk).exists())

    def test_existing_strain_serializers_do_not_expose_or_accept_identity(self):
        species = Species.objects.create(scientific_name="Aurelia coerulea")
        strain = Strain.objects.create(species=species, code="ACO-LAB-1")
        identity = GlobalStrainIdentity.objects.create()

        self.assertNotIn("global_identity", StrainReferenceSerializer(strain).data)
        serializer = StrainReferenceWriteSerializer(
            data={
                "species": species.pk,
                "code": "ACO-LAB-2",
                "translations": {"fr": {"name": "Souche 2"}},
                "global_identity": identity.pk,
            }
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertNotIn("global_identity", serializer.validated_data)
        self.assertIsNone(serializer.save().global_identity_id)


class GlobalStrainIdentityMigrationTests(TransactionTestCase):
    def test_populated_legacy_strain_is_not_backfilled(self):
        before = ("taxonomy", "0002_speciestranslation_straintranslation")
        after = ("taxonomy", "0003_globalstrainidentity_strain_global_identity")
        executor = MigrationExecutor(connection)
        try:
            executor.migrate([before])
            old_apps = executor.loader.project_state([before]).apps
            OldSpecies = old_apps.get_model("taxonomy", "Species")
            OldStrain = old_apps.get_model("taxonomy", "Strain")
            species = OldSpecies.objects.create(scientific_name="Legacy species")
            strain = OldStrain.objects.create(species=species, code="LEG-LAB-1")

            executor = MigrationExecutor(connection)
            executor.migrate([after])
            new_apps = executor.loader.project_state([after]).apps
            NewStrain = new_apps.get_model("taxonomy", "Strain")
            NewIdentity = new_apps.get_model("taxonomy", "GlobalStrainIdentity")
            self.assertIsNone(NewStrain.objects.get(pk=strain.pk).global_identity_id)
            self.assertEqual(NewIdentity.objects.count(), 0)
        finally:
            MigrationExecutor(connection).migrate([after])
