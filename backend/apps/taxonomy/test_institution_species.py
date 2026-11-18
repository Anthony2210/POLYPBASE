"""Additive institutional Species data, preparation and compatibility gates."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.db import DataError, IntegrityError, close_old_connections, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.exceptions import IrreversibleError
from django.db.models.deletion import ProtectedError
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework.exceptions import PermissionDenied

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.cultures.models import Box, BoxCodeNamespace
from apps.organizations.models import Organization

from .institution_species import institution_species_for_admin, prepare_institution_species_manifest
from .models import GlobalStrainIdentity, InstitutionSpecies, LocalStrainIdentity, OrganizationSpeciesCode, Species, Strain
from .test_species_review_regressions import SpeciesReviewTransactionTestCase


class InstitutionSpeciesTests(TestCase):
    def setUp(self):
        self.a = Organization.objects.create(name="Local A")
        self.b = Organization.objects.create(name="Local B")
        self.species = Species.objects.create(scientific_name="Aurelia aurita", worms_aphia_id=135306)
        self.other = Species.objects.create(scientific_name="Cassiopea andromeda")

    def profile(self, organization=None, species=None, name="Aurelia local", aphia_id=None):
        return InstitutionSpecies.objects.create(
            organization=organization or self.a, species=species or self.species,
            scientific_name=name, worms_aphia_id=aphia_id,
        )

    def test_two_institutions_can_interpret_one_reference_differently(self):
        a = self.profile(name="Aurelia local A", aphia_id=135306)
        b = self.profile(self.b, name="Aurelia local B", aphia_id=135306)
        self.assertNotEqual(a.pk, b.pk)
        self.assertEqual(a.species_id, b.species_id)
        self.assertEqual(a.worms_aphia_id, b.worms_aphia_id)
        self.species.refresh_from_db()
        self.assertEqual(self.species.scientific_name, "Aurelia aurita")
        self.assertEqual(OrganizationSpeciesCode.objects.count(), 0)

    def test_identical_local_names_are_allowed_in_different_institutions(self):
        self.profile(name="Aurelia local")
        self.profile(self.b, self.other, name=" AURELIA  local ")
        self.assertEqual(InstitutionSpecies.objects.count(), 2)

    def test_one_record_per_institution_and_reference(self):
        self.profile()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.profile(name="Another local name")

    def test_local_name_uniqueness_uses_existing_database_normalization(self):
        self.profile(name=" Aurelia  local ")
        for name in ("Aurelia local", "AURELIA LOCAL", "Aurelia\tlocal", "\xa0Aurelia\u2003local "):
            with self.subTest(name=name), self.assertRaises(IntegrityError), transaction.atomic():
                self.profile(species=self.other, name=name)

    def test_blank_normalized_names_are_rejected_by_database(self):
        for name in ("", " \t\n ", "\xa0\u2003"):
            with self.subTest(name=name), self.assertRaises(IntegrityError), transaction.atomic():
                self.profile(name=name)

    def test_nullable_aphia_is_not_zero_or_a_globally_unique_identity(self):
        first = self.profile()
        second = self.profile(species=self.other, name="Cassiopea local")
        self.assertIsNone(first.worms_aphia_id)
        self.assertIsNone(second.worms_aphia_id)
        for value in (0, -1, 2147483648):
            with self.subTest(value=value), self.assertRaises((DataError, IntegrityError)), transaction.atomic():
                InstitutionSpecies.objects.filter(pk=first.pk).update(worms_aphia_id=value)

    def test_reference_integrity_and_protection(self):
        profile = self.profile()
        with self.assertRaises(ProtectedError):
            self.a.delete()
        with self.assertRaises(ProtectedError):
            self.species.delete()
        for field in ("organization_id", "species_id"):
            with self.subTest(field=field), self.assertRaises(IntegrityError), transaction.atomic():
                InstitutionSpecies.objects.filter(pk=profile.pk).update(**{field: 999999999})
                connection.check_constraints()

    def test_revision_is_positive_and_compare_and_swap_rejects_stale_or_foreign_writes(self):
        profile = self.profile()
        self.assertEqual(profile.revision, 1)
        kwargs = {"pk": profile.pk, "expected_revision": 1, "scientific_name": "Aurelia corrected", "worms_aphia_id": None}
        self.assertEqual(InstitutionSpecies.objects.compare_and_swap(organization=self.b, **kwargs), 0)
        self.assertEqual(InstitutionSpecies.objects.compare_and_swap(organization=self.a, **kwargs), 1)
        self.assertEqual(InstitutionSpecies.objects.compare_and_swap(organization=self.a, **kwargs), 0)
        profile.refresh_from_db()
        self.assertEqual((profile.revision, profile.scientific_name), (2, "Aurelia corrected"))
        with self.assertRaises(IntegrityError), transaction.atomic():
            InstitutionSpecies.objects.filter(pk=profile.pk).update(revision=0)
        for revision in (0, -1, True, None):
            with self.subTest(revision=revision), self.assertRaises(ValueError):
                InstitutionSpecies.objects.compare_and_swap(organization=self.a, **{**kwargs, "expected_revision": revision})
        self.assertFalse(InstitutionSpecies.objects.for_organization(None).exists())

    def test_local_information_never_rewrites_historical_identity_or_assigns_codes(self):
        global_identity = GlobalStrainIdentity.objects.create()
        assignment = OrganizationSpeciesCode.objects.create(organization=self.a, species=self.species, code="AAU")
        strain = Strain.objects.create(
            organization=self.a, species=self.species, code="AAU-FRA-1", global_identity=global_identity,
        )
        identity = LocalStrainIdentity.objects.create(strain=strain, species_code_assignment=assignment)
        box = Box.objects.create(organization=self.a, strain=strain, global_code="AAU-FRA-1.001", box_number="001")
        before_namespaces = list(BoxCodeNamespace.objects.order_by("pk").values())
        b = self.profile(self.b, name="B interpretation", aphia_id=135306)
        profile = self.profile(name="A interpretation", aphia_id=135306)
        InstitutionSpecies.objects.compare_and_swap(
            organization=self.a, pk=profile.pk, expected_revision=1,
            scientific_name="A correction", worms_aphia_id=None,
        )
        for instance in (self.species, strain, identity, assignment, box, b):
            instance.refresh_from_db()
        self.assertEqual(self.species.scientific_name, "Aurelia aurita")
        self.assertEqual(self.species.worms_aphia_id, 135306)
        self.assertEqual((strain.species_id, strain.code, strain.global_identity_id), (self.species.pk, "AAU-FRA-1", global_identity.pk))
        self.assertEqual(identity.species_code_assignment_id, assignment.pk)
        self.assertEqual(assignment.code, "AAU")
        self.assertEqual((box.strain_id, box.global_code), (strain.pk, "AAU-FRA-1.001"))
        self.assertEqual((b.scientific_name, b.worms_aphia_id, b.revision), ("B interpretation", 135306, 1))
        self.assertEqual(list(BoxCodeNamespace.objects.order_by("pk").values()), before_namespaces)
        self.assertFalse(OrganizationSpeciesCode.objects.filter(organization=self.b).exists())
        self.assertFalse(AuditLog.objects.exists(), "No business workflow is activated by the model")

    def test_compare_and_swap_participates_in_callers_rollback(self):
        profile = self.profile()
        with self.assertRaises(RuntimeError), transaction.atomic():
            InstitutionSpecies.objects.compare_and_swap(
                organization=self.a, pk=profile.pk, expected_revision=1,
                scientific_name="A rolled back correction", worms_aphia_id=None,
            )
            raise RuntimeError("Simulated future audit failure")
        profile.refresh_from_db()
        self.assertEqual((profile.scientific_name, profile.revision), ("Aurelia local", 1))


class InstitutionSpeciesAccessTests(TestCase):
    def setUp(self):
        self.a = Organization.objects.create(name="Access A")
        self.b = Organization.objects.create(name="Access B")
        self.species = Species.objects.create(scientific_name="Aurelia aurita")
        self.local = InstitutionSpecies.objects.create(organization=self.a, species=self.species, scientific_name="A local name")
        self.foreign = InstitutionSpecies.objects.create(organization=self.b, species=self.species, scientific_name="Private B name", worms_aphia_id=999)
        self.users = {}
        for key, organization, role in (
            ("a_admin", self.a, OrganizationMembership.Role.ADMIN),
            ("b_admin", self.b, OrganizationMembership.Role.ADMIN),
            ("technician", self.a, OrganizationMembership.Role.LAB_TECHNICIAN),
            ("viewer", self.a, OrganizationMembership.Role.VIEWER),
        ):
            user = get_user_model().objects.create_user(username=key, email=f"{key}@example.org")
            OrganizationMembership.objects.create(user=user, organization=organization, role=role)
            self.users[key] = user

    def request(self, user, organization):
        headers = {} if organization is None else {"HTTP_X_ORGANIZATION_ID": str(organization.pk)}
        request = RequestFactory().get("/", **headers)
        request.user = user
        return request

    def test_shared_reference_does_not_grant_access_to_foreign_information(self):
        queryset = institution_species_for_admin(self.request(self.users["a_admin"], self.a))
        self.assertEqual(list(queryset.values_list("pk", flat=True)), [self.local.pk])
        self.assertFalse(queryset.filter(pk=self.foreign.pk).exists())
        self.assertEqual(queryset.compare_and_swap(
            organization=self.b, pk=self.foreign.pk, expected_revision=1,
            scientific_name="Forbidden", worms_aphia_id=None,
        ), 0)

    def test_explicit_active_admin_context_is_required(self):
        for user, organization in (
            (AnonymousUser(), self.a), (self.users["a_admin"], None),
            (self.users["a_admin"], self.b), (self.users["b_admin"], self.a),
            (self.users["technician"], self.a), (self.users["viewer"], self.a),
        ):
            with self.subTest(user=str(user)), self.assertRaises(PermissionDenied):
                institution_species_for_admin(self.request(user, organization))
        self.users["a_admin"].is_active = False
        with self.assertRaises(PermissionDenied):
            institution_species_for_admin(self.request(self.users["a_admin"], self.a))

    def test_existing_api_does_not_start_exposing_local_information(self):
        self.client.force_login(self.users["a_admin"])
        for route in ("api_admin_species_catalog", "api_taxonomy_references"):
            response = self.client.get(reverse(route), HTTP_X_ORGANIZATION_ID=str(self.a.pk))
            self.assertEqual(response.status_code, 200)
            self.assertNotIn("Private B name", response.content.decode())
            self.assertNotIn("A local name", response.content.decode())


class InstitutionSpeciesManifestTests(TestCase):
    def setUp(self):
        self.a = Organization.objects.create(name="Manifest A")
        self.b = Organization.objects.create(name="Manifest B")
        self.shared = Species.objects.create(scientific_name="Aurelia aurita")
        self.no_code = Species.objects.create(scientific_name="Cassiopea andromeda")
        self.unrelated = Species.objects.create(scientific_name="Chrysaora hysoscella")

    def test_manifest_uses_explicit_evidence_and_never_backfills(self):
        assignment = OrganizationSpeciesCode.objects.create(organization=self.a, species=self.shared, code="AAU")
        owned = Strain.objects.create(organization=self.a, species=self.no_code, code="CAN-FRA-1")
        legacy = Strain.objects.create(species=self.shared, code="AAU-LEG-1")
        local_box = Box.objects.create(organization=self.a, strain=legacy, global_code="AAU-LEG-1.001", box_number="001")
        Box.objects.create(organization=self.b, strain=legacy, global_code="AAU-LEG-1.002", box_number="002")
        # A matching name/AphiaID/code/global identity elsewhere is not local ownership.
        foreign_identity = GlobalStrainIdentity.objects.create()
        owned.global_identity = foreign_identity
        owned.save(update_fields=["global_identity"])
        foreign = Strain.objects.create(organization=self.b, species=self.unrelated, code=owned.code, global_identity=foreign_identity)
        foreign_usage = Box.objects.create(organization=self.a, strain=foreign, global_code="CAN-FRA-1.001", box_number="001")
        InstitutionSpecies.objects.create(organization=self.b, species=self.unrelated, scientific_name="Foreign private interpretation")
        with CaptureQueriesContext(connection) as queries:
            manifest = prepare_institution_species_manifest(organization=self.a)
        self.assertTrue(all(query["sql"].lstrip().upper().startswith("SELECT") for query in queries))
        self.assertTrue(manifest["advisory_only"])
        self.assertEqual([row["species_id"] for row in manifest["candidates"]], [self.shared.pk, self.no_code.pk])
        self.assertEqual(manifest["candidates"][0]["code_assignment_id"], assignment.pk)
        self.assertEqual(manifest["candidates"][1]["owned_strain_ids"], [owned.pk])
        self.assertEqual(manifest["species_without_institution_code"], [self.no_code.pk])
        self.assertEqual(manifest["unowned_strains"], [{
            "strain_id": legacy.pk, "species_id": self.shared.pk,
            "institution_box_ids": [local_box.pk], "also_used_by_another_institution": True,
            "ownership_unresolved": True,
        }])
        self.assertIn({
            "kind": "foreign_owned_strain_usage", "box_id": foreign_usage.pk,
            "strain_id": foreign.pk, "species_id": self.unrelated.pk,
        }, manifest["conflicts"])
        self.assertNotIn("Foreign private interpretation", str(manifest))
        self.assertEqual(InstitutionSpecies.objects.count(), 1)
        self.assertFalse(InstitutionSpecies.objects.for_organization(self.a).exists())
        self.assertEqual(OrganizationSpeciesCode.objects.count(), 1)
        legacy.refresh_from_db()
        self.assertIsNone(legacy.organization_id)

    def test_manifest_reports_prospective_name_collisions_and_identity_conflicts(self):
        assignment = OrganizationSpeciesCode.objects.create(organization=self.a, species=self.shared, code="AAU")
        OrganizationSpeciesCode.objects.create(organization=self.a, species=self.no_code, code="CAN")
        InstitutionSpecies.objects.create(organization=self.a, species=self.shared, scientific_name=" CASSIOPEA  andromeda ")
        strain = Strain.objects.create(organization=self.a, species=self.shared, code="WRG-FRA-1")
        other = Strain.objects.create(organization=self.a, species=self.shared, code="BAD-FRA-1")
        foreign_assignment = OrganizationSpeciesCode.objects.create(organization=self.b, species=self.shared, code="AAU")
        LocalStrainIdentity.objects.create(strain=strain, species_code_assignment=foreign_assignment)
        foreign = Strain.objects.create(organization=self.b, species=self.unrelated, code="foreign")
        LocalStrainIdentity.objects.create(strain=foreign, species_code_assignment=assignment)
        manifest = prepare_institution_species_manifest(organization=self.a)
        self.assertEqual(manifest["potential_name_collisions"][0]["species_ids"], [self.shared.pk, self.no_code.pk])
        kinds = {item["kind"] for item in manifest["conflicts"]}
        self.assertTrue({"multiple_owned_prefixes", "assignment_prefix_mismatch", "local_identity_mismatch", "assignment_used_by_nonowned_strain"}.issubset(kinds))
        self.assertEqual(manifest["candidates"][0]["owned_strain_ids"], [strain.pk, other.pk])

    def test_legacy_usage_alone_does_not_become_an_assignment(self):
        legacy = Strain.objects.create(species=self.shared, code="legacy")
        Box.objects.create(organization=self.a, strain=legacy, global_code="legacy.001", box_number="001")
        manifest = prepare_institution_species_manifest(organization=self.a)
        self.assertTrue(manifest["candidates"][0]["requires_review"])
        self.assertEqual(manifest["candidates"][0]["owned_strain_ids"], [])
        self.assertEqual(manifest["species_without_institution_code"], [self.shared.pk])
        self.assertFalse(InstitutionSpecies.objects.exists())
        self.assertFalse(OrganizationSpeciesCode.objects.exists())

    def test_manifest_requires_an_existing_explicit_institution(self):
        for organization in (None, Organization(name="Unsaved")):
            with self.assertRaises(ValueError):
                prepare_institution_species_manifest(organization=organization)


class InstitutionSpeciesMigrationTests(SpeciesReviewTransactionTestCase):
    old = ("taxonomy", "0011_species_normalized_name")
    new = ("taxonomy", "0012_institution_species_foundation")

    def setUp(self):
        # Registered first so the latest schema is restored even if setUp or a test fails.
        self.addCleanup(self.restore_latest_schema)
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        # A preceding migration test may leave other apps at historical states
        # (e.g. cultures before 0015); normalize before targeting history.
        executor.migrate(latest)
        others = [node for node in latest if node[0] != "taxonomy"]
        self.before_targets = others + [self.old]
        self.after_targets = others + [self.new]
        executor = MigrationExecutor(connection)
        executor.migrate(self.before_targets)
        apps = executor.loader.project_state(self.before_targets).apps
        # Fixtures use historical models matching the physical schema.
        self.organization = apps.get_model("organizations", "Organization").objects.create(name="Migration institution")
        self.species = apps.get_model("taxonomy", "Species").objects.create(scientific_name="Historical species")
        self.strain = apps.get_model("taxonomy", "Strain").objects.create(
            organization=self.organization, species=self.species, code="HIS-OLD-1",
        )
        self.no_code = apps.get_model("taxonomy", "Species").objects.create(scientific_name="Unassigned historical species")
        self.assignment = apps.get_model("taxonomy", "OrganizationSpeciesCode").objects.create(
            organization=self.organization, species=self.species, code="HIS",
        )
        self.identity = apps.get_model("taxonomy", "LocalStrainIdentity").objects.create(
            strain=self.strain, species_code_assignment=self.assignment,
        )
        self.box = apps.get_model("cultures", "Box").objects.create(
            organization=self.organization, strain=self.strain,
            global_code="HIS-OLD-1.001", box_number="001",
        )

    def restore_latest_schema(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        InstitutionSpecies.objects.all().delete()

    def migrate(self, targets):
        MigrationExecutor(connection).migrate(targets)

    def test_forward_and_empty_reverse_preserve_history_without_backfill(self):
        self.migrate(self.after_targets)
        self.assertFalse(InstitutionSpecies.objects.exists())
        self.assertEqual(OrganizationSpeciesCode.objects.count(), 1)
        self.assertFalse(OrganizationSpeciesCode.objects.filter(species_id=self.no_code.pk).exists())
        self.migrate(self.before_targets)
        self.assertNotIn("taxonomy_institutionspecies", connection.introspection.table_names())
        self.assertEqual(Species.objects.get(pk=self.species.pk).scientific_name, "Historical species")
        strain = Strain.objects.get(pk=self.strain.pk)
        self.assertEqual((strain.species_id, strain.code), (self.species.pk, "HIS-OLD-1"))
        self.assignment.refresh_from_db()
        self.identity.refresh_from_db()
        self.box.refresh_from_db()
        self.assertEqual(self.assignment.code, "HIS")
        self.assertEqual(self.identity.species_code_assignment_id, self.assignment.pk)
        self.assertEqual((self.box.strain_id, self.box.global_code), (strain.pk, "HIS-OLD-1.001"))

    def test_populated_reverse_refuses_to_discard_local_information(self):
        self.migrate(self.after_targets)
        profile = InstitutionSpecies.objects.create(
            organization_id=self.organization.pk, species_id=self.species.pk,
            scientific_name="Local historical interpretation",
        )
        with self.assertRaises(IrreversibleError):
            self.migrate(self.before_targets)
        self.assertEqual(InstitutionSpecies.objects.get(pk=profile.pk).scientific_name, "Local historical interpretation")
        self.assertEqual(Strain.objects.get(pk=self.strain.pk).species_id, self.species.pk)


@skipUnless(connection.vendor == "postgresql", "Requires a new verified disposable PostgreSQL instance.")
class InstitutionSpeciesConcurrencyTests(SpeciesReviewTransactionTestCase):
    def setUp(self):
        self.a = Organization.objects.create(name="Concurrent local A")
        self.b = Organization.objects.create(name="Concurrent local B")
        self.first = Species.objects.create(scientific_name="First shared reference")
        self.second = Species.objects.create(scientific_name="Second shared reference")

    def race(self, operation, *args):
        barrier = Barrier(len(args))

        def worker(arg):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return operation(arg)
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=len(args)) as pool:
            futures = [pool.submit(worker, arg) for arg in args]
            return [future.result(timeout=30) for future in futures]

    def insert(self, args):
        organization, species, name = args
        try:
            with transaction.atomic():
                InstitutionSpecies.objects.create(organization=organization, species=species, scientific_name=name)
            return "created"
        except IntegrityError:
            return "conflict"

    def test_concurrent_normalized_local_names_have_one_winner(self):
        results = self.race(self.insert, (self.a, self.first, " Local  name "), (self.a, self.second, "LOCAL\tname"))
        self.assertEqual(sorted(results), ["conflict", "created"])
        self.assertEqual(InstitutionSpecies.objects.count(), 1)

    def test_concurrent_reference_registration_has_one_winner(self):
        results = self.race(self.insert, (self.a, self.first, "One name"), (self.a, self.first, "Another name"))
        self.assertEqual(sorted(results), ["conflict", "created"])

    def test_different_institutions_can_register_same_name_concurrently(self):
        results = self.race(self.insert, (self.a, self.first, "Local name"), (self.b, self.first, "LOCAL name"))
        self.assertEqual(results, ["created", "created"])

    def test_concurrent_revision_updates_have_one_winner(self):
        profile = InstitutionSpecies.objects.create(organization=self.a, species=self.first, scientific_name="Before correction")

        def update(name):
            return InstitutionSpecies.objects.compare_and_swap(
                organization=self.a, pk=profile.pk, expected_revision=1,
                scientific_name=name, worms_aphia_id=None,
            )

        self.assertEqual(sorted(self.race(update, "First correction", "Second correction")), [0, 1])
        profile.refresh_from_db()
        self.assertEqual(profile.revision, 2)
