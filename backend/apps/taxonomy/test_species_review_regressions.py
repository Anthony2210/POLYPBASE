"""Regression coverage for the Species/WoRMS independent review."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from time import monotonic, sleep
from unittest import skipUnless
from unittest.mock import patch

from django.db import IntegrityError, close_old_connections, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import SimpleTestCase, TestCase, TransactionTestCase

from apps.organizations.models import Organization
from config.disposable_postgres import DisposablePostgresFlushMixin

from . import species_codes, worms
from .models import OrganizationSpeciesCode, Species
from .serializers import SpeciesReferenceWriteSerializer
from .species_codes import allocate_species_code


class WormsRankRecognitionTests(SimpleTestCase):
    def test_only_official_supported_pairs_are_selectable(self):
        for rank_id, rank in worms.SPECIES_LEVEL_RANKS.items():
            with self.subTest(rank_id=rank_id):
                self.assertTrue(worms.parse_record({
                    "AphiaID": 123, "scientificname": "Aurelia aurita",
                    "taxonRankID": rank_id, "rank": rank.title(),
                })["is_species_level"])

    def test_unknown_missing_malformed_and_inconsistent_ranks_fail_closed(self):
        for rank_id, rank in (
            (999, "Species"), (221, "Species"), (999, "Unknown"),
            (180, "Genus"), (140, "Family"), (220, "Genus"),
            (230, "Species"), (220, ""), (None, "Species"),
            (True, "Species"), ("220", "Species"), (0, "Species"),
        ):
            with self.subTest(rank_id=rank_id, rank=rank):
                self.assertFalse(worms.parse_record({
                    "AphiaID": 123, "scientificname": "Aurelia aurita",
                    "taxonRankID": rank_id, "rank": rank,
                })["is_species_level"])


class SpeciesNormalizedNameTests(TestCase):
    def test_database_rejects_equivalent_names_without_rewriting_history(self):
        original = Species.objects.create(scientific_name="  Aurelia  aurita ")
        for name in (
            "  Aurelia  aurita ", "Aurelia aurita", "AURELIA AURITA",
            "Aurelia   aurita", "Aurelia\taurita", "Aurelia\n aurita",
            "\xa0Aurelia\u2003aurita\u3000",
        ):
            with self.subTest(name=name), self.assertRaises(IntegrityError), transaction.atomic():
                Species.objects.create(scientific_name=name)
        original.refresh_from_db()
        self.assertEqual(original.scientific_name, "  Aurelia  aurita ")
        self.assertEqual(Species.objects.count(), 1)

    def test_legacy_serializer_checks_the_same_key(self):
        Species.objects.create(scientific_name=" Aurelia  aurita ")
        serializer = SpeciesReferenceWriteSerializer(data={
            "scientific_name": "AURELIA\t aurita",
            "translations": {"fr": {"name": "Aurélie"}},
        })
        self.assertFalse(serializer.is_valid())
        self.assertEqual(set(serializer.errors), {"scientific_name"})


class SpeciesReviewTransactionTestCase(DisposablePostgresFlushMixin, TransactionTestCase):
    """Append-only SQL guards prohibit TRUNCATE; the mixin bypasses them only
    for fixture cleanup, on an independently verified disposable cluster."""


class SpeciesNameMigrationTests(SpeciesReviewTransactionTestCase):
    old = ("taxonomy", "0010_species_scientific_name_case_insensitive")
    new = ("taxonomy", "0011_species_normalized_name")

    def setUp(self):
        executor = MigrationExecutor(connection)
        executor.migrate([self.old])
        self.old_apps = executor.loader.project_state([self.old]).apps
        self.Species = self.old_apps.get_model("taxonomy", "Species")

    def tearDown(self):
        self.Species.objects.all().delete()
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_forward_migration_reports_all_colliding_ids_and_original_names(self):
        first = self.Species.objects.create(scientific_name="Aurelia aurita")
        second = self.Species.objects.create(scientific_name=" Aurelia  aurita ")
        third = self.Species.objects.create(scientific_name="AURELIA\taurita")
        with self.assertRaises(RuntimeError) as raised:
            MigrationExecutor(connection).migrate([self.new])
        for species in (first, second, third):
            self.assertIn(f"Species {species.pk} ({species.scientific_name!r})", str(raised.exception))
            self.assertEqual(self.Species.objects.get(pk=species.pk).scientific_name, species.scientific_name)
        self.assertEqual(self.Species.objects.count(), 3)

    def test_forward_migration_preserves_a_noncolliding_historical_name(self):
        original = self.Species.objects.create(scientific_name=" Aurelia  aurita ")
        MigrationExecutor(connection).migrate([self.new])
        self.assertEqual(Species.objects.get(pk=original.pk).scientific_name, " Aurelia  aurita ")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Species.objects.create(scientific_name="Aurelia aurita")


@skipUnless(connection.vendor == "postgresql", "Requires a new disposable PostgreSQL instance.")
class SpeciesReviewConcurrencyTests(SpeciesReviewTransactionTestCase):
    def race(self, action, *args):
        barrier = Barrier(len(args))

        def worker(arg):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return action(arg)
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=len(args)) as pool:
            futures = [pool.submit(worker, arg) for arg in args]
            return [future.result(timeout=30) for future in futures]

    def test_existing_species_compete_across_institutions(self):
        a = Organization.objects.create(name="Review A")
        b = Organization.objects.create(name="Review B")
        first = Species.objects.create(scientific_name="Aurelia aurita")
        second = Species.objects.create(scientific_name="Aurelia aurantia")

        def allocate(arg):
            organization, species = arg
            with transaction.atomic():
                return allocate_species_code(organization=organization, species=species)[0].code

        self.assertEqual(sorted(self.race(allocate, (a, first), (b, second))), ["AAR", "AAU"])
        self.assertEqual(OrganizationSpeciesCode.objects.count(), 2)

    def test_second_allocator_waits_before_candidate_checks(self):
        a = Organization.objects.create(name="Gate A")
        b = Organization.objects.create(name="Gate B")
        first = Species.objects.create(scientific_name="Aurelia aurita")
        second = Species.objects.create(scientific_name="Aurelia aurantia")
        first_check = Event()
        release_first = Event()
        second_started = Event()
        second_pid = []
        original = species_codes._is_safe

        def check(code, organization, species):
            if organization.pk == a.pk:
                first_check.set()
                if not release_first.wait(timeout=10):
                    raise AssertionError("First allocator was not released")
            return original(code, organization, species)

        def allocate(organization, species):
            close_old_connections()
            try:
                with transaction.atomic():
                    if organization.pk == b.pk:
                        with connection.cursor() as cursor:
                            cursor.execute("SELECT pg_backend_pid()")
                            second_pid.append(cursor.fetchone()[0])
                        second_started.set()
                    return allocate_species_code(organization=organization, species=species)[0].code
            finally:
                close_old_connections()

        with patch.object(species_codes, "_is_safe", side_effect=check), ThreadPoolExecutor(max_workers=2) as pool:
            one = pool.submit(allocate, a, first)
            try:
                self.assertTrue(first_check.wait(timeout=10))
                two = pool.submit(allocate, b, second)
                self.assertTrue(second_started.wait(timeout=10))
                deadline = monotonic() + 5
                blocked = False
                while monotonic() < deadline:
                    with connection.cursor() as cursor:
                        cursor.execute(
                            "SELECT wait_event FROM pg_stat_activity WHERE pid = %s", second_pid,
                        )
                        row = cursor.fetchone()
                    if row and row[0] == "advisory":
                        blocked = True
                        break
                    sleep(0.01)
                self.assertTrue(blocked, "Second allocator must wait on the shared gate")
            finally:
                release_first.set()
            self.assertEqual((one.result(timeout=15), two.result(timeout=15)), ("AAU", "AAR"))

    def test_same_species_can_share_generated_code_across_institutions(self):
        a = Organization.objects.create(name="Review A")
        b = Organization.objects.create(name="Review B")
        species = Species.objects.create(scientific_name="Aurelia aurita")

        def allocate(organization):
            with transaction.atomic():
                return allocate_species_code(organization=organization, species=species)[0].code

        self.assertEqual(self.race(allocate, a, b), ["AAU", "AAU"])

    def test_database_arbitrates_uncoordinated_normalized_name_inserts(self):
        def insert(name):
            try:
                with transaction.atomic():
                    Species.objects.create(scientific_name=name)
                return "created"
            except IntegrityError:
                return "duplicate"

        self.assertEqual(sorted(self.race(insert, " Aurelia  aurita ", "AURELIA\taurita")), ["created", "duplicate"])
        self.assertEqual(Species.objects.count(), 1)
