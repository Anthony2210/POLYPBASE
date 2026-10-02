from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.recorder import MigrationRecorder
from django.test import TestCase, TransactionTestCase

from apps.organizations.models import Organization

from .models import GlobalStrainIdentity, Species, Strain


CONSTRAINT = "unique_owned_strain_per_global_identity"
BEFORE = ("taxonomy", "0007_localstrainidentity_provenance_code_assignment")
AFTER = ("taxonomy", "0008_strain_canonical_global_identity")


class CanonicalGlobalIdentityTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name="Canonical institution")
        self.other_organization = Organization.objects.create(name="Other institution")
        self.species = Species.objects.create(scientific_name="Canonical species")
        self.other_species = Species.objects.create(scientific_name="Other species")
        self.identity = GlobalStrainIdentity.objects.create()

    def strain(self, code, **kwargs):
        return Strain.objects.create(**{
            "organization": self.organization,
            "species": self.species,
            "global_identity": self.identity,
            "code": code,
            **kwargs,
        })

    def test_same_owned_global_identity_is_rejected(self):
        first = self.strain("FIRST")
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.strain("SECOND")
        self.assertEqual(list(Strain.objects.values_list("pk", flat=True)), [first.pk])
        # The failed insert's savepoint does not poison the surrounding transaction.
        self.strain("OTHER", global_identity=GlobalStrainIdentity.objects.create())
        self.assertEqual(Strain.objects.count(), 2)

    def test_same_global_identity_in_different_organizations_is_allowed(self):
        self.strain("FIRST")
        self.strain("SECOND", organization=self.other_organization)
        self.assertEqual(Strain.objects.count(), 2)

    def test_multiple_null_global_identities_in_same_organization_are_allowed(self):
        self.strain("FIRST", global_identity=None)
        self.strain("SECOND", global_identity=None)
        self.assertEqual(Strain.objects.count(), 2)

    def test_multiple_unowned_representations_of_same_identity_are_allowed(self):
        self.strain("FIRST", organization=None)
        self.strain("SECOND", organization=None)
        self.assertEqual(Strain.objects.count(), 2)

    def test_owned_and_unowned_representations_of_same_identity_are_allowed(self):
        self.strain("FIRST")
        self.strain("SECOND", organization=None)
        self.assertEqual(Strain.objects.count(), 2)

    def test_different_global_identities_in_same_organization_are_allowed(self):
        self.strain("FIRST")
        self.strain("SECOND", global_identity=GlobalStrainIdentity.objects.create())
        self.assertEqual(Strain.objects.count(), 2)

    def test_species_is_not_part_of_canonical_key(self):
        first = self.strain("FIRST")
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.strain("SECOND", species=self.other_species)
        first.refresh_from_db()
        self.assertEqual(first.species_id, self.species.pk)
        self.assertEqual(Strain.objects.count(), 1)

    def test_different_organizations_may_have_different_species_for_same_identity(self):
        self.strain("FIRST")
        self.strain("SECOND", organization=self.other_organization, species=self.other_species)
        self.assertEqual(Strain.objects.count(), 2)

    def test_direct_update_cannot_assign_duplicate_identity(self):
        first = self.strain("FIRST")
        second = self.strain("SECOND", global_identity=None)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Strain.objects.filter(pk=second.pk).update(global_identity=self.identity)
        second.refresh_from_db()
        self.assertIsNone(second.global_identity_id)
        first.refresh_from_db()
        self.assertEqual(first.global_identity_id, self.identity.pk)

    def test_database_has_conditional_unique_index(self):
        with connection.cursor() as cursor:
            constraints = connection.introspection.get_constraints(cursor, Strain._meta.db_table)
            self.assertTrue(constraints[CONSTRAINT]["unique"])
            self.assertEqual(constraints[CONSTRAINT]["columns"], ["organization_id", "global_identity_id"])
            if connection.vendor == "postgresql":
                cursor.execute(
                    "SELECT pg_get_indexdef(indexrelid), pg_get_expr(indpred, indrelid) "
                    "FROM pg_index WHERE indexrelid = to_regclass(%s)", [CONSTRAINT],
                )
                definition, predicate = cursor.fetchone()
            else:
                cursor.execute("SELECT sql FROM sqlite_master WHERE name = %s", [CONSTRAINT])
                definition = cursor.fetchone()[0]
                predicate = definition.split(" WHERE ", 1)[1]
        self.assertIn("UNIQUE", definition.upper())
        self.assertIn("organization_id", predicate)
        self.assertIn("global_identity_id", predicate)
        self.assertEqual(predicate.upper().count("IS NOT NULL"), 2)


class CanonicalGlobalIdentityMigrationTests(TransactionTestCase):
    def migrate_before(self):
        executor = MigrationExecutor(connection)
        self.latest = executor.loader.graph.leaf_nodes()
        # Normalize preceding historical tests, then retain current cross-app states,
        # including Organization.portable_id, in both schema and historical models.
        executor.migrate(self.latest)
        self.before_targets = [node for node in self.latest if node[0] != "taxonomy"] + [BEFORE]
        executor = MigrationExecutor(connection)
        executor.migrate(self.before_targets)
        return executor.loader.project_state(self.before_targets).apps

    def create_historical_rows(self, apps):
        organization_model = apps.get_model("organizations", "Organization")
        species_model = apps.get_model("taxonomy", "Species")
        identity_model = apps.get_model("taxonomy", "GlobalStrainIdentity")
        strain_model = apps.get_model("taxonomy", "Strain")
        organization = organization_model.objects.create(name="Historical institution")
        species = species_model.objects.create(scientific_name="Historical species")
        identity = identity_model.objects.create()
        strain = strain_model.objects.create(
            organization=organization, species=species, global_identity=identity,
            code="HISTORICAL-1", number=0, origin_code="OLD", notes="Preserved",
        )
        return organization, species, identity, strain, strain_model

    def test_clean_upgrade_preserves_rows_and_null_exclusions(self):
        try:
            apps = self.migrate_before()
            organization, species, identity, strain, strain_model = self.create_historical_rows(apps)
            other_organization = apps.get_model("organizations", "Organization").objects.create(
                name="Other historical institution",
            )
            strain_model.objects.create(
                organization=other_organization, species=species, global_identity=identity,
                code="FOREIGN",
            )
            for index in range(2):
                strain_model.objects.create(
                    organization=organization, species=species, code=f"NULL-GLOBAL-{index}",
                )
                strain_model.objects.create(
                    species=species, global_identity=identity, code=f"UNOWNED-{index}",
                )
            model_names = ("Strain", "Species", "GlobalStrainIdentity", "LocalStrainIdentity")
            snapshots = {
                name: list(apps.get_model("taxonomy", name).objects.order_by("pk").values())
                for name in model_names
            }
            organizations_before = list(apps.get_model("organizations", "Organization").objects.order_by("pk").values())
            executor = MigrationExecutor(connection)
            executor.migrate(self.latest)
            new_apps = executor.loader.project_state(self.latest).apps
            for name, rows in snapshots.items():
                self.assertEqual(list(new_apps.get_model("taxonomy", name).objects.order_by("pk").values()), rows)
            self.assertEqual(
                list(new_apps.get_model("organizations", "Organization").objects.order_by("pk").values()),
                organizations_before,
            )
            self.assertIn(AFTER, MigrationRecorder(connection).applied_migrations())
        finally:
            MigrationExecutor(connection).migrate(self.latest)

    def test_duplicate_upgrade_fails_before_constraint_without_mutation(self):
        strain_model = None
        try:
            apps = self.migrate_before()
            organization, species, identity, first, strain_model = self.create_historical_rows(apps)
            strain_model.objects.create(
                organization=organization, species=species, global_identity=identity,
                code="HISTORICAL-2", number=2, notes="Also preserved",
            )
            rows = list(strain_model.objects.order_by("pk").values())
            organizations_before = list(apps.get_model("organizations", "Organization").objects.values())
            identities_before = list(apps.get_model("taxonomy", "GlobalStrainIdentity").objects.values())
            expected = (
                f"Cannot add {CONSTRAINT}: organization_id={organization.pk}, "
                f"global_identity_id={identity.pk} has 2 Strains."
            )
            with self.assertRaisesMessage(RuntimeError, expected):
                MigrationExecutor(connection).migrate(self.latest)
            self.assertEqual(list(strain_model.objects.order_by("pk").values()), rows)
            self.assertEqual(list(apps.get_model("organizations", "Organization").objects.values()), organizations_before)
            self.assertEqual(list(apps.get_model("taxonomy", "GlobalStrainIdentity").objects.values()), identities_before)
            self.assertNotIn(AFTER, MigrationRecorder(connection).applied_migrations())
            with connection.cursor() as cursor:
                self.assertNotIn(CONSTRAINT, connection.introspection.get_constraints(cursor, strain_model._meta.db_table))
        finally:
            # Remove only disposable test fixtures AFTER preservation assertions,
            # so current leaves can be restored even following a blocked upgrade.
            if strain_model is not None:
                strain_model.objects.all().delete()
            MigrationExecutor(connection).migrate(self.latest)
