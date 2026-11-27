"""Registry and migration tests only; no resolver or correction policy."""

from concurrent.futures import ThreadPoolExecutor
from importlib import import_module
from queue import Queue
from threading import Barrier, Event
from time import monotonic, sleep
from unittest import skipUnless
from unittest.mock import patch
import warnings

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import DatabaseError, close_old_connections, connection, transaction
from django.db.migrations.exceptions import IrreversibleError
from config.disposable_postgres import DisposablePostgresFlushMixin
from config.migration_test_utils import MigrationExecutor, historical_targets
from django.db.models.deletion import ProtectedError
from django.db.models.query import QuerySet
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from apps.measurements.models import BiologicalMeasurement

from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain

from .box_aliases import canonical_box_id, register_box_local_id_alias
from .box_codes import allocate_box_codes, lock_local_id_registry, preview_box_codes
from .models import Box, BoxLocalIdAlias

migration = import_module("apps.cultures.migrations.0023_box_local_id_alias")


class BoxLocalIdAliasTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Alias laboratory")
        self.other_org = Organization.objects.create(name="Other alias laboratory")
        species = Species.objects.create(
            scientific_name="Alias species", genus_species_code="ALS"
        )
        self.strain = Strain.objects.create(
            organization=self.org, species=species, code="ALS-LAB-1"
        )
        self.box = self.make_box("ALS-LAB-1.001")

    def make_box(self, code, **extra):
        return Box.objects.create(
            organization=extra.pop("organization", self.org),
            strain=self.strain,
            global_code=code,
            box_number=code.rsplit(".", 1)[-1],
            **extra,
        )

    def register(self, code, box=None):
        return register_box_local_id_alias(
            box=box or self.box, code=code, organization_id=self.org.pk
        )

    def test_multiple_exact_confirmed_aliases_are_additive_and_idempotent(self):
        user = get_user_model().objects.create_user(
            username="alias-science-author", email="alias-science@example.org"
        )
        measurement = BiologicalMeasurement.objects.create(
            box=self.box,
            measured_on=timezone.localdate(),
            polyp_count=0,
            user=user,
        )
        scientific_before = (
            BiologicalMeasurement.objects.filter(pk=measurement.pk).values().get()
        )
        before = Box.objects.filter(pk=self.box.pk).values().get()
        first = self.register("Retired-name.021")
        second = self.register("Another-old-name.002")
        self.assertEqual(self.register(first.code).pk, first.pk)
        self.assertEqual({first.box_id, second.box_id}, {self.box.pk})
        self.assertEqual(first.organization_id, self.org.pk)
        self.assertEqual(Box.objects.filter(pk=self.box.pk).values().get(), before)
        self.assertEqual(
            BiologicalMeasurement.objects.filter(pk=measurement.pk).values().get(),
            scientific_before,
        )
        self.assertIsNone(Box.objects.get(pk=self.box.pk).global_identity_id)
        self.assertEqual(BoxLocalIdAlias.objects.count(), 2)
        self.assertEqual(
            BoxLocalIdAlias.objects.filter(code__startswith="CLA-").count(), 0
        )
        self.assertEqual(
            BoxLocalIdAlias.objects.filter(code__startswith="SME-").count(), 0
        )

    def test_source_and_organization_must_be_explicit(self):
        with self.assertRaises(ValidationError):
            register_box_local_id_alias(
                box=self.box, code="old", organization_id=self.other_org.pk
            )
        for code in ("", " ", " old", "a" * 101):
            with self.subTest(code=code), self.assertRaises(ValidationError):
                self.register(code)
        foreign = self.make_box("foreign", organization=self.other_org)
        with self.assertRaises(ValidationError) as error:
            self.register(foreign.global_code)
        self.assertNotIn("foreign", str(error.exception))

    def test_collisions_with_current_previous_and_other_alias_are_global(self):
        other = self.make_box("ALS-LAB-1.002", previous_global_code="old-previous")
        self.register("registered")
        for code in (self.box.global_code, "old-previous", "registered"):
            source = self.box if code == "old-previous" else other
            with self.subTest(code=code), self.assertRaises(ValidationError):
                self.register(code, source)
        with self.assertRaises(DatabaseError), transaction.atomic():
            BoxLocalIdAlias.objects.bulk_create(
                [BoxLocalIdAlias(code="old-previous", box=self.box)]
            )
        with self.assertRaises(DatabaseError), transaction.atomic():
            Box.objects.filter(pk=other.pk).update(global_code="registered")
        with self.assertRaises(DatabaseError), transaction.atomic():
            Box.objects.filter(pk=other.pk).update(previous_global_code="registered")
        with self.assertRaises(DatabaseError), transaction.atomic():
            Box.objects.bulk_create(
                [
                    Box(
                        organization=self.other_org,
                        strain=self.strain,
                        global_code="registered",
                        box_number="0",
                    )
                ]
            )
        # Previous-vs-current conflicts are guarded even without a registry row.
        with self.assertRaises(DatabaseError), transaction.atomic():
            Box.objects.filter(pk=other.pk).update(
                previous_global_code=self.box.global_code
            )

    def test_pca_compatible_current_equals_merged_donor_previous(self):
        donor = self.make_box("retired-donor", status="inactive")
        Box.objects.filter(pk=donor.pk).update(
            merged_into=self.box, previous_global_code=self.box.global_code
        )
        donor.refresh_from_db()
        alias = self.register(self.box.global_code, donor)
        self.assertEqual(alias.box_id, donor.pk)
        self.assertEqual(canonical_box_id(donor), self.box.pk)
        # Never silently move ownership to the canonical record.
        with self.assertRaises(ValidationError):
            self.register(alias.code, self.box)
        with self.assertRaises(DatabaseError), transaction.atomic():
            Box.objects.filter(pk=donor.pk).update(merged_into=None)
        # Unchanged equivalent identity remains saveable.
        self.box.notes = "ordinary scientific metadata"
        self.box.save(update_fields=["notes"])

    def test_registered_aliases_cannot_be_invalidated_by_cycles_or_foreign_merges(self):
        other = self.make_box("other", status="inactive")
        Box.objects.filter(pk=other.pk).update(merged_into=self.box)
        self.register("other-old", other)
        with self.assertRaises(DatabaseError), transaction.atomic():
            Box.objects.filter(pk=self.box.pk).update(
                merged_into=other, status="inactive"
            )
        foreign = self.make_box("foreign", organization=self.other_org)
        with self.assertRaises(DatabaseError), transaction.atomic():
            Box.objects.filter(pk=other.pk).update(merged_into=foreign)
        # Unregistered legacy graph anomalies remain reviewable, but can never
        # acquire an alias (this migration is not a general merge-policy change).
        unregistered = self.make_box("unregistered", status="inactive")
        Box.objects.filter(pk=unregistered.pk).update(merged_into=foreign)
        unregistered.refresh_from_db()
        with self.assertRaises(ValidationError):
            self.register("unresolved-old", unregistered)
        with self.assertRaises(DatabaseError), transaction.atomic():
            BoxLocalIdAlias.objects.create(code="raw-unresolved", box=unregistered)
        self.register("immutable-org")
        with self.assertRaises(DatabaseError), transaction.atomic():
            Box.objects.filter(pk=self.box.pk).update(organization=self.other_org)

    def test_organization_of_terminal_and_indirect_alias_ancestors_is_permanent(self):
        donor = self.make_box("donor", status="inactive", merged_into=self.box)
        self.register("donor-old", donor)
        self.assertFalse(self.box.local_id_aliases.exists())
        # The terminal has no direct aliases: the incoming donor must protect it.
        with self.assertRaises(DatabaseError), transaction.atomic():
            Box.objects.filter(pk=self.box.pk).update(organization=self.other_org)
        middle = self.make_box("middle", status="inactive", merged_into=self.box)
        Box.objects.filter(pk=donor.pk).update(merged_into=middle)
        donor.refresh_from_db()
        for target in (middle, self.box):
            with (
                self.subTest(target=target.pk),
                self.assertRaises(DatabaseError),
                transaction.atomic(),
            ):
                with connection.cursor() as cursor:
                    cursor.execute(
                        "UPDATE cultures_box SET organization_id = %s WHERE id = %s",
                        [self.other_org.pk, target.pk],
                    )
            self.assertEqual(canonical_box_id(donor), self.box.pk)
            self.assertEqual(Box.objects.get(pk=target.pk).organization_id, self.org.pk)
        middle.organization = self.other_org
        with self.assertRaises(DatabaseError), transaction.atomic():
            Box.objects.bulk_update([middle], ["organization"])
        # A save of the unchanged organization is not a reassignment.
        Box.objects.filter(pk=self.box.pk).update(organization=self.org)
        unrelated = self.make_box("unregistered-org-change")
        Box.objects.filter(pk=unrelated.pk).update(organization=self.other_org)

    def test_coordination_precedes_all_box_and_namespace_row_locks(self):
        original_row_lock = QuerySet.select_for_update
        events = []

        def registry_lock(*args, **kwargs):
            events.append("registry")
            return lock_local_id_registry(*args, **kwargs)

        def row_lock(queryset, *args, **kwargs):
            events.append("row")
            return original_row_lock(queryset, *args, **kwargs)

        operations = (
            lambda: self.box.save(update_fields=["notes"]),
            lambda: self.register("numeric-old.200"),
            lambda: self.register("nonnumeric-old"),
            lambda: allocate_box_codes("numeric-old", 1),
        )
        for operation in operations:
            events.clear()
            with (
                patch(
                    "apps.cultures.box_codes.lock_local_id_registry",
                    side_effect=registry_lock,
                ),
                patch.object(
                    QuerySet, "select_for_update", autospec=True, side_effect=row_lock
                ),
            ):
                operation()
            self.assertIn("row", events)
            self.assertEqual(events[0], "registry")

    def test_registry_is_immutable_including_raw_delete_and_protects_source(self):
        alias = self.register("old")
        with self.assertRaises(ValidationError):
            alias.save()
        with self.assertRaises(ValidationError):
            alias.delete()
        with self.assertRaises(ValidationError):
            BoxLocalIdAlias.objects.all().delete()
        with self.assertRaises(ValidationError):
            BoxLocalIdAlias.objects.filter(pk=alias.pk).update(code="new")
        with self.assertRaises(ProtectedError):
            self.box.delete()
        for sql in (
            "UPDATE cultures_boxlocalidalias SET code = 'new' WHERE id = %s",
            "UPDATE cultures_boxlocalidalias SET created_at = CURRENT_TIMESTAMP WHERE id = %s",
            "DELETE FROM cultures_boxlocalidalias WHERE id = %s",
        ):
            with (
                self.subTest(sql=sql),
                self.assertRaises(DatabaseError),
                transaction.atomic(),
            ):
                with connection.cursor() as cursor:
                    cursor.execute(sql, [alias.pk])

    def test_allocation_reserves_current_previous_and_raw_registered_suffixes(self):
        self.register("retired-name.098-old")
        # Direct insertion deliberately does not advance a Python-side counter.
        BoxLocalIdAlias.objects.bulk_create(
            [BoxLocalIdAlias(code="retired-name.101", box=self.box)]
        )
        Box.objects.filter(pk=self.box.pk).update(
            previous_global_code="retired-name.105-old"
        )
        self.assertEqual(
            preview_box_codes("retired-name", 1), [("retired-name.106", "106")]
        )
        with transaction.atomic():
            self.assertEqual(
                allocate_box_codes("retired-name", 2),
                [("retired-name.106", "106"), ("retired-name.107", "107")],
            )
        self.assertEqual(
            preview_box_codes("retired-name", 1), [("retired-name.108", "108")]
        )


class BoxLocalIdAliasMigrationTests(DisposablePostgresFlushMixin, TransactionTestCase):
    def _fixture_teardown(self):
        # Production guards forbid DELETE/TRUNCATE; test flush is not a business
        # operation. PostgreSQL: the mixin verifies the disposable cluster and
        # suppresses triggers for this cleanup transaction only.
        if connection.vendor == "postgresql":
            return super()._fixture_teardown()
        # SQLite: restore every trigger immediately after isolated cleanup.
        with connection.schema_editor() as editor:
            migration.remove_guards(None, editor)
        try:
            super()._fixture_teardown()
        finally:
            with connection.schema_editor() as editor:
                migration.install_guards(None, editor)

    def postgres_box(self):
        org = Organization.objects.create(name="PostgreSQL alias laboratory")
        species = Species.objects.create(
            scientific_name="PostgreSQL alias species", genus_species_code="PAL"
        )
        strain = Strain.objects.create(
            organization=org, species=species, code="PAL-LAB-1"
        )
        return Box.objects.create(
            organization=org,
            strain=strain,
            global_code="PAL-LAB-1.001",
            box_number="001",
        )

    @skipUnless(
        connection.vendor == "postgresql", "Requires PostgreSQL table writer exclusion"
    )
    def test_rollback_fences_both_tables_until_transaction_commit(self):
        box = self.postgres_box()
        register_box_local_id_alias(
            box=box, code=box.global_code, organization_id=box.organization_id
        )

        def competing_write(table):
            close_old_connections()
            try:
                with transaction.atomic(), connection.cursor() as cursor:
                    cursor.execute("SET LOCAL lock_timeout = '250ms'")
                    if table == "alias":
                        cursor.execute(
                            "INSERT INTO cultures_boxlocalidalias (code, box_id, created_at) "
                            "VALUES (%s, %s, CURRENT_TIMESTAMP)",
                            ["late-extra", box.pk],
                        )
                    else:
                        cursor.execute(
                            "UPDATE cultures_box SET notes = 'late change' WHERE id = %s",
                            [box.pk],
                        )
                return "committed"
            except DatabaseError as exc:
                return getattr(exc.__cause__, "sqlstate", None)
            finally:
                connection.close()

        with transaction.atomic():
            with connection.schema_editor(atomic=False) as editor:
                migration.refuse_lossy_rollback(apps, editor)
            # Preflight has returned, but the migration transaction has not:
            # neither a new alias nor a changed reconstruction basis can slip in.
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [
                    pool.submit(competing_write, table) for table in ("alias", "box")
                ]
                self.assertEqual(
                    [future.result(timeout=5) for future in futures], ["55P03", "55P03"]
                )
        self.assertEqual(competing_write("alias"), "committed")
        self.assertEqual(competing_write("box"), "committed")

    @skipUnless(
        connection.vendor == "postgresql", "Requires PostgreSQL NOWAIT table locks"
    )
    def test_rollback_refuses_busy_tables_before_reading_snapshot(self):
        held, release = Event(), Event()

        def reader():
            close_old_connections()
            try:
                with transaction.atomic(), connection.cursor() as cursor:
                    # The second table is busy: NOWAIT must release the first
                    # table too when this rollback attempt aborts.
                    cursor.execute("SELECT code FROM cultures_boxlocalidalias")
                    held.set()
                    if not release.wait(timeout=10):
                        raise AssertionError(
                            "Rollback did not finish its NOWAIT preflight."
                        )
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(reader)
            try:
                self.assertTrue(held.wait(timeout=5))
                with patch.object(migration, "_state") as snapshot:
                    with (
                        self.assertRaises(DatabaseError) as error,
                        transaction.atomic(),
                    ):
                        with connection.schema_editor(atomic=False) as editor:
                            migration.refuse_lossy_rollback(apps, editor)
                    self.assertEqual(
                        getattr(error.exception.__cause__, "sqlstate", None), "55P03"
                    )
                    snapshot.assert_not_called()
            finally:
                release.set()
            future.result(timeout=5)
        # The aborted migration did not remove either table or its guards.
        self.assertIn(
            "cultures_boxlocalidalias", connection.introspection.table_names()
        )

    @skipUnless(
        connection.vendor == "postgresql",
        "Requires PostgreSQL statement triggers and row locks",
    )
    def test_numeric_registration_does_not_invert_save_raw_or_allocation_locks(self):
        box = self.postgres_box()
        for index, kind in enumerate(("save", "raw", "allocate"), start=1):
            held, finish_alias = Event(), Event()
            writer_pid = Queue()

            def register():
                close_old_connections()
                try:
                    with transaction.atomic():
                        lock_local_id_registry()
                        held.set()
                        if not finish_alias.wait(timeout=10):
                            raise AssertionError(
                                "The competing writer did not reach coordination."
                            )
                        return register_box_local_id_alias(
                            box=box,
                            code=f"retired-lock-order.{index:03}",
                            organization_id=box.organization_id,
                        ).pk
                finally:
                    connection.close()

            def write():
                close_old_connections()
                try:
                    with transaction.atomic():
                        with connection.cursor() as cursor:
                            cursor.execute("SELECT pg_backend_pid()")
                            writer_pid.put(cursor.fetchone()[0])
                        if kind == "save":
                            current = Box.objects.get(pk=box.pk)
                            current.notes = "ordinary save"
                            current.save(update_fields=["notes"])
                        elif kind == "raw":
                            with connection.cursor() as cursor:
                                cursor.execute(
                                    "UPDATE cultures_box SET global_code = global_code WHERE id = %s",
                                    [box.pk],
                                )
                        else:
                            allocate_box_codes("retired-lock-order", 1)
                finally:
                    connection.close()

            with self.subTest(kind=kind), ThreadPoolExecutor(max_workers=2) as pool:
                alias_future = pool.submit(register)
                try:
                    self.assertTrue(held.wait(timeout=5))
                    writer_future = pool.submit(write)
                    pid = writer_pid.get(timeout=5)
                    deadline = monotonic() + 5
                    waiting = False
                    while monotonic() < deadline:
                        with connection.cursor() as cursor:
                            cursor.execute(
                                "SELECT wait_event FROM pg_stat_activity WHERE pid = %s",
                                [pid],
                            )
                            row = cursor.fetchone()
                        if row and row[0] == "advisory":
                            waiting = True
                            break
                        sleep(0.02)
                    self.assertTrue(
                        waiting,
                        "Writer must wait for coordination before taking a Box row lock.",
                    )
                finally:
                    finish_alias.set()
                # On the old order, the alias holder now waited for the writer's
                # Box row while that writer waited for its advisory lock.
                self.assertIsNotNone(alias_future.result(timeout=15))
                writer_future.result(timeout=15)

    @skipUnless(
        connection.vendor == "postgresql",
        "Requires isolated PostgreSQL row locks and snapshots",
    )
    def test_concurrent_raw_claims_cannot_both_commit(self):
        org = Organization.objects.create(name="Concurrent alias laboratory")
        species = Species.objects.create(
            scientific_name="Concurrent alias species", genus_species_code="CAR"
        )
        strain = Strain.objects.create(
            organization=org, species=species, code="CAR-LAB-1"
        )
        boxes = [
            Box.objects.create(
                organization=org,
                strain=strain,
                global_code=f"CAR-LAB-1.{n:03}",
                box_number=str(n),
            )
            for n in (1, 2)
        ]
        for left, right in (
            ("alias", "current"),
            ("alias", "previous"),
            ("current", "previous"),
        ):
            barrier = Barrier(2)
            code = f"race-{left}-{right}"

            def claim(kind, box):
                close_old_connections()
                try:
                    with transaction.atomic():
                        barrier.wait(timeout=10)
                        with connection.cursor() as cursor:
                            if kind == "alias":
                                cursor.execute(
                                    "INSERT INTO cultures_boxlocalidalias (code, box_id, created_at) "
                                    "VALUES (%s, %s, CURRENT_TIMESTAMP)",
                                    [code, box.pk],
                                )
                            else:
                                field = (
                                    "global_code"
                                    if kind == "current"
                                    else "previous_global_code"
                                )
                                cursor.execute(
                                    f"UPDATE cultures_box SET {field} = %s WHERE id = %s",
                                    [code, box.pk],
                                )
                    return "committed"
                except DatabaseError:
                    return "rejected"
                finally:
                    connection.close()

            with (
                self.subTest(left=left, right=right),
                ThreadPoolExecutor(max_workers=2) as pool,
            ):
                futures = [
                    pool.submit(claim, left, boxes[0]),
                    pool.submit(claim, right, boxes[1]),
                ]
                self.assertCountEqual(
                    [future.result(timeout=20) for future in futures],
                    ["committed", "rejected"],
                )

    def test_verified_backfill_preserves_conflicts_and_lossless_rollback(self):
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        before = historical_targets(
            executor.loader,
            {"cultures"},
            [
                ("cultures", "0022_box_global_identity"),
            ],
        )
        executor.migrate(before)
        self.addCleanup(lambda: MigrationExecutor(connection).migrate(latest))
        apps = executor.loader.project_state(before).apps
        Org = apps.get_model("organizations", "Organization")
        org = Org.objects.create(name="Migration alias laboratory")
        foreign = Org.objects.create(name="Foreign migration laboratory")
        species = apps.get_model("taxonomy", "Species").objects.create(
            scientific_name="Migration alias species"
        )
        strain = apps.get_model("taxonomy", "Strain").objects.create(
            species=species, code="MAL-LAB-1"
        )
        HistoricalBox = apps.get_model("cultures", "Box")

        def box(code, previous="", **extra):
            return HistoricalBox.objects.create(
                organization=extra.pop("organization", org),
                strain=strain,
                global_code=code,
                previous_global_code=previous,
                box_number="0",
                **extra,
            )

        clean = box("clean-current", "clean-previous", volume_liters=0)
        canonical = box("official", "older-official")
        donor = box("retired", "official", merged_into=canonical, status="inactive")
        conflict = box("conflict", "ambiguous")
        box("ambiguous", organization=foreign)
        cycle_a = box("cycle-a", "cycle-previous", status="inactive")
        cycle_b = box("cycle-b", status="inactive", merged_into=cycle_a)
        HistoricalBox.objects.filter(pk=cycle_a.pk).update(merged_into=cycle_b)
        box(
            "foreign-chain",
            "foreign-previous",
            status="inactive",
            merged_into=conflict,
            organization=foreign,
        )
        rows = list(HistoricalBox.objects.order_by("pk").values())
        executor = MigrationExecutor(connection)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            executor.migrate(latest)
        self.assertTrue(any("skipped 3" in str(w.message) for w in caught))
        self.assertTrue(all("ambiguous" not in str(w.message) for w in caught))
        apps = executor.loader.project_state(latest).apps
        Alias = apps.get_model("cultures", "BoxLocalIdAlias")
        self.assertEqual(
            dict(Alias.objects.values_list("code", "box_id")),
            {
                "clean-previous": clean.pk,
                "older-official": canonical.pk,
                "official": donor.pk,
            },
        )
        self.assertEqual(
            list(apps.get_model("cultures", "Box").objects.order_by("pk").values()),
            rows,
        )
        # Unchanged legacy conflicts remain inspectable and scientifically editable.
        HistoricalBox.objects.filter(pk=conflict.pk).update(notes="review pending")
        with self.assertRaises(DatabaseError), transaction.atomic():
            HistoricalBox.objects.filter(pk=clean.pk).update(
                previous_global_code="ambiguous"
            )
        # A harmless write of the same legacy value must not erase/reject it.
        HistoricalBox.objects.filter(pk=conflict.pk).update(
            previous_global_code="ambiguous"
        )
        MigrationExecutor(connection).migrate(before)
        self.assertNotIn(
            "cultures_boxlocalidalias", connection.introspection.table_names()
        )
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            MigrationExecutor(connection).migrate(latest)
        Alias.objects.create(code="explicit-extra", box_id=clean.pk)
        with self.assertRaises(IrreversibleError):
            MigrationExecutor(connection).migrate(before)
        # Refusal occurs before removing guards.
        with self.assertRaises(DatabaseError), transaction.atomic():
            Alias.objects.filter(code="explicit-extra").delete()
        # Make the alias reconstructible using existing fields; no registry
        # deletion bypass is needed to complete this downgrade/re-upgrade test.
        HistoricalBox.objects.filter(pk=clean.pk).update(global_code="explicit-extra")
        MigrationExecutor(connection).migrate(before)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            MigrationExecutor(connection).migrate(latest)
