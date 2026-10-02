"""Local X allocation regressions; concurrency requires isolated PostgreSQL."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from unittest import skipUnless
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import (
    IntegrityError,
    close_old_connections,
    connection,
    connections,
    models,
    transaction,
)
from django.db.models.query import QuerySet
from django.test import TestCase, TransactionTestCase

from apps.cultures.models import Box
from apps.organizations.models import Organization

from .models import (
    BiologicalProvenance,
    GlobalStrainIdentity,
    LocalStrainIdentity,
    LocalStrainNumberCounter,
    OrganizationProvenanceCode,
    OrganizationSpeciesCode,
    Origin,
    Species,
    Strain,
)
from .services import allocate_next_local_strain_number


class LocalStrainNumberFixtures:
    def setUp(self):
        super().setUp()
        self.organization = Organization.objects.create(name="Allocator institution")
        self.other_organization = Organization.objects.create(name="Other institution")
        self.species = Species.objects.create(scientific_name="Allocator species")
        self.other_species = Species.objects.create(scientific_name="Other species")
        self.provenance = BiologicalProvenance.objects.create(name="Allocator source")
        self.other_provenance = BiologicalProvenance.objects.create(name="Other source")
        self.strain_serial = 0

    def scope(self, **overrides):
        scope = {
            "organization": self.organization,
            "species": self.species,
            "biological_provenance": None,
        }
        scope.update(overrides)
        return scope

    def allocate(self, **overrides):
        return allocate_next_local_strain_number(**self.scope(**overrides))

    def explicit_strain(self, number, *, identity=True, **overrides):
        scope = self.scope(**overrides)
        organization = scope["organization"]
        species = scope["species"]
        provenance = scope["biological_provenance"]
        self.strain_serial += 1
        strain = Strain.objects.create(
            organization=organization,
            species=species,
            code=f"ISSUED-{self.strain_serial}",
            number=number,
        )
        if identity:
            aaa, _ = OrganizationSpeciesCode.objects.get_or_create(
                organization=organization,
                species=species,
                defaults={"code": "AAA" if species == self.species else "CCC"},
            )
            bbb = None
            if provenance is not None:
                bbb, _ = OrganizationProvenanceCode.objects.get_or_create(
                    organization=organization,
                    biological_provenance=provenance,
                    defaults={"code": "BBB" if provenance == self.provenance else "DDD"},
                )
            LocalStrainIdentity.objects.create(
                strain=strain,
                species_code_assignment=aaa,
                provenance_code_assignment=bbb,
            )
        return strain


class LocalStrainNumberAllocationTests(LocalStrainNumberFixtures, TestCase):
    def test_empty_scope_starts_at_one_without_creating_domain_objects(self):
        domain_models = (
            Strain, GlobalStrainIdentity, LocalStrainIdentity,
            OrganizationSpeciesCode, OrganizationProvenanceCode, Box, Origin,
            Organization, Species, BiologicalProvenance,
        )
        before = {model: list(model.objects.values()) for model in domain_models}
        self.assertEqual(self.allocate(), 1)
        counter = LocalStrainNumberCounter.objects.get(**self.scope())
        self.assertEqual(counter.last_number, 1)
        for model in domain_models:
            with self.subTest(model=model.__name__):
                self.assertEqual(list(model.objects.values()), before[model])

    def test_zero_is_an_explicit_number_and_is_not_modified(self):
        strain = self.explicit_strain(0)
        self.assertEqual(self.allocate(), 1)
        strain.refresh_from_db()
        self.assertEqual(strain.number, 0)
        self.assertTrue(LocalStrainIdentity.objects.filter(strain=strain).exists())

    def test_sparse_explicit_numbers_use_maximum_not_count(self):
        strains = [self.explicit_strain(number) for number in (0, 1, 4, None)]
        self.assertEqual(self.allocate(), 5)
        self.assertEqual(
            list(Strain.objects.filter(pk__in=[strain.pk for strain in strains])
                 .order_by("pk").values_list("number", flat=True)),
            [0, 1, 4, None],
        )

    def test_null_number_does_not_supply_a_baseline(self):
        self.explicit_strain(None)
        self.assertEqual(self.allocate(), 1)

    def test_repeated_allocations_advance_without_creating_strains(self):
        self.assertEqual([self.allocate() for _ in range(4)], [1, 2, 3, 4])
        self.assertEqual(LocalStrainNumberCounter.objects.count(), 1)
        self.assertEqual(LocalStrainNumberCounter.objects.get().last_number, 4)
        self.assertFalse(Strain.objects.exists())

    def test_every_allocation_respects_later_explicit_inserts(self):
        # Sequential imports are respected; this does not claim to lock manual writers.
        self.assertEqual(self.allocate(), 1)
        self.explicit_strain(9)
        self.assertEqual(self.allocate(), 10)
        self.explicit_strain(4)
        self.assertEqual(self.allocate(), 11)
        self.explicit_strain(20)
        self.assertEqual(self.allocate(), 21)

    def test_counter_ahead_of_baseline_never_goes_backwards(self):
        LocalStrainNumberCounter.objects.create(**self.scope(), last_number=40)
        self.explicit_strain(4)
        self.assertEqual(self.allocate(), 41)

    def test_organization_species_and_provenance_are_independent_namespaces(self):
        scopes = [
            self.scope(),
            self.scope(organization=self.other_organization),
            self.scope(species=self.other_species),
            self.scope(biological_provenance=self.provenance),
            self.scope(biological_provenance=self.other_provenance),
        ]
        for scope, baseline in zip(scopes, (4, 10, 20, 30, 40)):
            self.explicit_strain(baseline, **scope)
        for scope, expected in zip(scopes, (5, 11, 21, 31, 41)):
            with self.subTest(scope=scope):
                self.assertEqual(allocate_next_local_strain_number(**scope), expected)
                self.assertEqual(allocate_next_local_strain_number(**scope), expected + 1)
        self.assertEqual(LocalStrainNumberCounter.objects.count(), len(scopes))

    def test_same_number_can_be_allocated_in_every_distinct_empty_scope(self):
        scopes = [
            self.scope(),
            self.scope(organization=self.other_organization),
            self.scope(species=self.other_species),
            self.scope(biological_provenance=self.provenance),
            self.scope(biological_provenance=self.other_provenance),
        ]
        self.assertEqual(
            [allocate_next_local_strain_number(**scope) for scope in scopes],
            [1, 1, 1, 1, 1],
        )
        self.assertEqual(LocalStrainNumberCounter.objects.count(), 5)

    def test_legacy_strains_without_identity_are_excluded(self):
        self.explicit_strain(900, identity=False)
        self.explicit_strain(4)
        self.assertEqual(self.allocate(), 5)

    def test_wrong_aaa_organization_or_species_is_excluded(self):
        assignments = [
            OrganizationSpeciesCode.objects.create(
                organization=self.other_organization, species=self.species, code="AAA"
            ),
            OrganizationSpeciesCode.objects.create(
                organization=self.organization, species=self.other_species, code="CCC"
            ),
        ]
        for aaa in assignments:
            strain = self.explicit_strain(900, identity=False)
            LocalStrainIdentity.objects.create(strain=strain, species_code_assignment=aaa)
        self.assertEqual(self.allocate(), 1)

    def test_wrong_strain_organization_or_species_is_excluded_even_with_local_aaa(self):
        aaa = OrganizationSpeciesCode.objects.create(
            organization=self.organization, species=self.species, code="AAA"
        )
        for scope in (
            self.scope(organization=self.other_organization),
            self.scope(species=self.other_species),
        ):
            strain = self.explicit_strain(900, identity=False, **scope)
            LocalStrainIdentity.objects.create(strain=strain, species_code_assignment=aaa)
        self.assertEqual(self.allocate(), 1)

    def test_foreign_bbb_is_excluded_even_for_the_same_provenance(self):
        strain = self.explicit_strain(900)
        foreign_bbb = OrganizationProvenanceCode.objects.create(
            organization=self.other_organization,
            biological_provenance=self.provenance,
            code="BBB",
        )
        LocalStrainIdentity.objects.filter(strain=strain).update(
            provenance_code_assignment=foreign_bbb
        )
        self.assertEqual(self.allocate(biological_provenance=self.provenance), 1)
        self.assertEqual(self.allocate(), 1)

    def test_codes_origin_and_box_do_not_infer_a_namespace_or_number(self):
        strain = self.explicit_strain(4)
        origin = Origin.objects.create(description="BBB source, number 900")
        Strain.objects.filter(pk=strain.pk).update(
            code="AAA-BBB-900", origin_code="BBB", origin=origin
        )
        box = Box.objects.create(
            organization=self.organization, strain=strain, origin=origin,
            global_code="AAA-BBB-900.999", box_number="999",
        )
        OrganizationProvenanceCode.objects.create(
            organization=self.organization, biological_provenance=self.provenance,
            code="BBB",
        )
        before_strain = Strain.objects.values().get(pk=strain.pk)
        before_box = Box.objects.values().get(pk=box.pk)
        self.assertEqual(self.allocate(biological_provenance=self.provenance), 1)
        self.assertEqual(self.allocate(), 5)
        self.assertEqual(Strain.objects.values().get(pk=strain.pk), before_strain)
        self.assertEqual(Box.objects.values().get(pk=box.pk), before_box)
        self.assertIsNone(LocalStrainIdentity.objects.get(strain=strain).provenance_code_assignment_id)

    def test_invalid_scope_arguments_are_rejected_without_counter_creation(self):
        invalid = {
            "organization": (None, Organization(name="Unsaved"),
                             Organization(pk=2147483647), self.species, 1),
            "species": (None, Species(scientific_name="Unsaved"),
                        Species(pk=2147483647), self.organization, 1),
            "biological_provenance": (BiologicalProvenance(name="Unsaved"),
                                      BiologicalProvenance(pk=2147483647),
                                      self.species, 1),
        }
        for field, values in invalid.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    with self.assertRaises(ValidationError):
                        self.allocate(**{field: value})
                    self.assertFalse(LocalStrainNumberCounter.objects.exists())

    def test_scope_arguments_are_required_and_keyword_only(self):
        for missing in self.scope():
            scope = self.scope()
            del scope[missing]
            with self.subTest(missing=missing), self.assertRaises(TypeError):
                allocate_next_local_strain_number(**scope)
        with self.assertRaises(TypeError):
            allocate_next_local_strain_number(self.organization, self.species, None)
        self.assertFalse(LocalStrainNumberCounter.objects.exists())

    def test_integer_limit_can_be_allocated_once_then_exhausts(self):
        counter = LocalStrainNumberCounter.objects.create(
            **self.scope(), last_number=2147483646
        )
        self.assertEqual(self.allocate(), 2147483647)
        with self.assertRaises(ValidationError):
            self.allocate()
        counter.refresh_from_db()
        self.assertEqual(counter.last_number, 2147483647)

    def test_explicit_integer_limit_exhausts_without_leaving_a_new_counter(self):
        strain = self.explicit_strain(2147483647)
        with self.assertRaises(ValidationError):
            self.allocate()
        self.assertFalse(LocalStrainNumberCounter.objects.exists())
        strain.refresh_from_db()
        self.assertEqual(strain.number, 2147483647)


class LocalStrainNumberSchemaTests(LocalStrainNumberFixtures, TestCase):
    def test_clean_schema_has_an_empty_counter_table_and_expected_fields(self):
        meta = LocalStrainNumberCounter._meta
        self.assertIn(meta.db_table, connection.introspection.table_names())
        self.assertFalse(LocalStrainNumberCounter.objects.exists())
        for name, related_model, nullable in (
            ("organization", Organization, False),
            ("species", Species, False),
            ("biological_provenance", BiologicalProvenance, True),
        ):
            with self.subTest(field=name):
                field = meta.get_field(name)
                self.assertIsInstance(field, models.ForeignKey)
                self.assertIs(field.remote_field.model, related_model)
                self.assertEqual(field.null, nullable)
        number = meta.get_field("last_number")
        self.assertIsInstance(number, models.PositiveIntegerField)
        self.assertEqual(number.default, 0)
        self.assertFalse(number.null)
        counter = LocalStrainNumberCounter.objects.create(**self.scope())
        counter.refresh_from_db()
        self.assertEqual(counter.last_number, 0)

    def test_partial_unique_constraints_exist_in_model_and_database(self):
        expected = {
            "unique_local_x_scope": (
                ["organization", "species", "biological_provenance"],
                models.Q(biological_provenance__isnull=False),
            ),
            "unique_local_x_null_scope": (
                ["organization", "species"],
                models.Q(biological_provenance__isnull=True),
            ),
        }
        constraints = {item.name: item for item in LocalStrainNumberCounter._meta.constraints}
        with connection.cursor() as cursor:
            database = connection.introspection.get_constraints(
                cursor, LocalStrainNumberCounter._meta.db_table
            )
        for name, (fields, condition) in expected.items():
            with self.subTest(constraint=name):
                self.assertIsInstance(constraints[name], models.UniqueConstraint)
                self.assertEqual(list(constraints[name].fields), fields)
                self.assertEqual(constraints[name].condition, condition)
                self.assertTrue(database[name]["unique"])
                self.assertEqual(
                    database[name]["columns"],
                    [LocalStrainNumberCounter._meta.get_field(field).column for field in fields],
                )

    def test_database_rejects_duplicate_null_and_nonnull_scopes(self):
        for provenance in (None, self.provenance):
            scope = self.scope(biological_provenance=provenance)
            counter = LocalStrainNumberCounter.objects.create(**scope)
            with self.subTest(provenance=provenance):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    LocalStrainNumberCounter.objects.create(**scope)
                self.assertEqual(LocalStrainNumberCounter.objects.get(**scope).pk, counter.pk)
        LocalStrainNumberCounter.objects.create(
            **self.scope(biological_provenance=self.other_provenance)
        )
        LocalStrainNumberCounter.objects.create(
            **self.scope(organization=self.other_organization)
        )
        LocalStrainNumberCounter.objects.create(**self.scope(species=self.other_species))
        self.assertEqual(LocalStrainNumberCounter.objects.count(), 5)


class LocalStrainNumberTransactionTests(LocalStrainNumberFixtures, TestCase):
    def test_outer_rollback_removes_first_allocation(self):
        with self.assertRaisesMessage(RuntimeError, "Abort caller transaction"):
            with transaction.atomic():
                self.assertEqual(self.allocate(), 1)
                raise RuntimeError("Abort caller transaction")
        self.assertFalse(LocalStrainNumberCounter.objects.exists())
        self.assertEqual(self.allocate(), 1)

    def test_outer_rollback_restores_existing_counter(self):
        self.assertEqual(self.allocate(), 1)
        with self.assertRaisesMessage(RuntimeError, "Abort caller transaction"):
            with transaction.atomic():
                self.assertEqual(self.allocate(), 2)
                self.assertEqual(self.allocate(), 3)
                raise RuntimeError("Abort caller transaction")
        self.assertEqual(LocalStrainNumberCounter.objects.get().last_number, 1)
        self.assertEqual(self.allocate(), 2)

    def test_handled_duplicate_savepoint_leaves_outer_transaction_usable(self):
        for provenance in (None, self.provenance):
            scope = self.scope(biological_provenance=provenance)
            with self.subTest(provenance=provenance), transaction.atomic():
                self.assertEqual(allocate_next_local_strain_number(**scope), 1)
                with self.assertRaises(IntegrityError), transaction.atomic():
                    LocalStrainNumberCounter.objects.create(**scope)
                self.assertEqual(allocate_next_local_strain_number(**scope), 2)
                self.assertEqual(LocalStrainNumberCounter.objects.get(**scope).last_number, 2)


@skipUnless(connection.vendor == "postgresql", "PostgreSQL row locks are required")
class LocalStrainNumberConcurrencyTests(LocalStrainNumberFixtures, TransactionTestCase):
    workers = 8
    wait_timeout = 10

    def database_worker(self, operation):
        close_old_connections()
        try:
            # Bound database waits as well as futures, including executor shutdown.
            with connection.cursor() as cursor:
                cursor.execute("SET lock_timeout = '10s'")
                cursor.execute("SET statement_timeout = '15s'")
                cursor.execute("SELECT pg_backend_pid()")
                backend_pid = cursor.fetchone()[0]
            return backend_pid, operation()
        finally:
            connections.close_all()

    def concurrent_allocations(self, scopes):
        start = Barrier(self.workers)

        def allocate(scope):
            start.wait(timeout=self.wait_timeout)
            return allocate_next_local_strain_number(**scope)

        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures = [
                pool.submit(self.database_worker, lambda scope=scope: allocate(scope))
                for scope in scopes
            ]
            results = [future.result(timeout=25) for future in futures]
        self.assertEqual(len({pid for pid, _ in results}), self.workers)
        return [number for _, number in results]

    def assert_scope_coherent(self, scope, results, first):
        self.assertEqual(sorted(results), list(range(first, first + len(results))))
        counters = LocalStrainNumberCounter.objects.filter(**scope)
        self.assertEqual(counters.count(), 1)
        self.assertEqual(counters.get().last_number, max(results))

    def test_repeated_allocations_serialize_on_initialized_namespace(self):
        for provenance in (None, self.provenance):
            scope = self.scope(biological_provenance=provenance)
            self.assertEqual(allocate_next_local_strain_number(**scope), 1)
            for first in (2, 10, 18):
                with self.subTest(provenance=provenance, first=first):
                    results = self.concurrent_allocations([scope] * self.workers)
                    self.assert_scope_coherent(scope, results, first)
        self.assertEqual(LocalStrainNumberCounter.objects.count(), 2)

    def test_competing_initial_inserts_recover_without_breaking_transactions(self):
        # Force every initial lookup to miss before any counter insert runs.
        # This exercises the real unique-index conflict and get_or_create retry,
        # rather than injecting an artificial database exception.
        for provenance in (None, self.provenance):
            scope = self.scope(biological_provenance=provenance)
            insert = Barrier(self.workers)
            original_create = QuerySet.create

            def synchronized_create(queryset, **kwargs):
                if queryset.model is LocalStrainNumberCounter:
                    insert.wait(timeout=self.wait_timeout)
                return original_create(queryset, **kwargs)

            with self.subTest(provenance=provenance):
                with patch.object(QuerySet, "create", synchronized_create):
                    results = self.concurrent_allocations([scope] * self.workers)
                self.assert_scope_coherent(scope, results, 1)
                self.assertEqual(allocate_next_local_strain_number(**scope), 9)

    def test_concurrent_first_use_of_null_scope(self):
        scope = self.scope()
        results = self.concurrent_allocations([scope] * self.workers)
        self.assert_scope_coherent(scope, results, 1)
        self.assertEqual(LocalStrainNumberCounter.objects.count(), 1)

    def test_concurrent_first_use_of_nonnull_scope_with_explicit_baseline(self):
        scope = self.scope(biological_provenance=self.provenance)
        for number in (0, 1, 9):
            self.explicit_strain(number, **scope)
        results = self.concurrent_allocations([scope] * self.workers)
        self.assert_scope_coherent(scope, results, 10)
        self.assertEqual(LocalStrainNumberCounter.objects.count(), 1)
        self.assertEqual(list(Strain.objects.order_by("number")
                              .values_list("number", flat=True)), [0, 1, 9])

    def test_concurrent_first_use_of_null_scope_with_explicit_baseline(self):
        scope = self.scope()
        for number in (0, 1, 9):
            self.explicit_strain(number)
        results = self.concurrent_allocations([scope] * self.workers)
        self.assert_scope_coherent(scope, results, 10)

    def test_concurrent_first_use_of_nonnull_scope_without_baseline(self):
        scope = self.scope(biological_provenance=self.provenance)
        results = self.concurrent_allocations([scope] * self.workers)
        self.assert_scope_coherent(scope, results, 1)

    def test_concurrent_different_scopes_keep_independent_counters(self):
        scopes = [
            self.scope(),
            self.scope(organization=self.other_organization),
            self.scope(species=self.other_species),
            self.scope(biological_provenance=self.provenance),
        ]
        for scope in scopes:
            for number in (0, 1, 9):
                self.explicit_strain(number, **scope)
        results = self.concurrent_allocations(scopes * 2)
        for index, scope in enumerate(scopes):
            self.assert_scope_coherent(scope, [results[index], results[index + 4]], 10)
        self.assertEqual(LocalStrainNumberCounter.objects.count(), 4)

    def test_other_scopes_progress_while_a_counter_lock_is_held(self):
        locked_scope = self.scope()
        counter = LocalStrainNumberCounter.objects.create(**locked_scope)
        locked = Event()
        release = Event()

        def hold_counter_lock():
            with transaction.atomic():
                LocalStrainNumberCounter.objects.select_for_update().get(pk=counter.pk)
                locked.set()
                if not release.wait(timeout=20):
                    raise AssertionError("Timed out waiting to release counter lock")

        other_scopes = [
            self.scope(organization=self.other_organization),
            self.scope(species=self.other_species),
            self.scope(biological_provenance=self.provenance),
        ]
        with ThreadPoolExecutor(max_workers=4) as pool:
            holder = pool.submit(self.database_worker, hold_counter_lock)
            try:
                if not locked.wait(timeout=self.wait_timeout):
                    if holder.done():
                        holder.result()
                    self.fail("The holder never acquired the counter lock")
                futures = [
                    pool.submit(
                        self.database_worker,
                        lambda scope=scope: allocate_next_local_strain_number(**scope),
                    )
                    for scope in other_scopes
                ]
                for future in futures:
                    self.assertEqual(future.result(timeout=5)[1], 1)
                self.assertFalse(holder.done())
            finally:
                release.set()
            holder.result(timeout=25)
        counter.refresh_from_db()
        self.assertEqual(counter.last_number, 0)
        self.assertEqual(self.allocate(), 1)
        self.assertEqual(LocalStrainNumberCounter.objects.count(), 4)
