"""A refused rollback leaves no dependent migration unapplied (full migrate plans)."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Event
from unittest import skipUnless
from unittest.mock import patch
import uuid

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import DatabaseError, close_old_connections, connection, transaction
from django.db.migrations.exceptions import IrreversibleError
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from apps.accounts.models import OrganizationMembership
from apps.measurements.models import BiologicalMeasurement
from apps.organizations.models import Organization
from apps.taxonomy.models import (
    BiologicalProvenance,
    InstitutionSpecies,
    InstitutionStrainCreationRequest,
    OrganizationProvenanceCode,
    Origin,
    Species,
    SpeciesCodeChangeRequest,
    Strain,
)
from config import rollback_preflight
from config.disposable_postgres import DisposablePostgresFlushMixin

from .box_codes import lock_local_id_registry
from .models import Box, BoxGlobalIdentity, BoxLocalIdAlias

CULTURES_0021 = ("cultures", "0021_merge_dna_and_history")
CULTURES_0022 = ("cultures", "0022_box_global_identity")
CULTURES_0023 = ("cultures", "0023_box_local_id_alias")
CULTURES_0024_HARDENING = ("cultures", "0024_local_id_guard_search_path")
TAXONOMY_0012 = ("taxonomy", "0012_institution_species_foundation")
CULTURES_0026 = ("cultures", "0026_box_planned_weekday")
Reason = BoxGlobalIdentity.AllocationReason


def without_planned_weekday(snapshot):
    """Expected state after also undoing 0026, which only drops its own column."""
    columns = snapshot["columns"]["cultures_box"]
    return {
        **snapshot,
        "columns": {
            **snapshot["columns"],
            "cultures_box": [name for name in columns if name != "planned_weekday"],
        },
    }


def sqlstate(error):
    cause = error.__cause__
    return getattr(cause, "sqlstate", None) or getattr(cause, "pgcode", None)


class RollbackPlanPreflightTests(DisposablePostgresFlushMixin, TransactionTestCase):
    def setUp(self):
        self.addCleanup(lambda: call_command("migrate", verbosity=0, interactive=False))
        self.addCleanup(rollback_preflight.release_registry_lock, connection.alias)
        self.organization = Organization.objects.create(name="Rollback plan laboratory")
        self.user = get_user_model().objects.create_user(
            username="rollback-plan", email="rollback-plan@example.org"
        )
        OrganizationMembership.objects.create(
            organization=self.organization, user=self.user, role="lab_technician"
        )
        species = Species.objects.create(scientific_name="Rollback plan species")
        self.strain = Strain.objects.create(
            organization=self.organization, species=species, code="RBP-LAB-1"
        )

    def migrate(self, target):
        call_command("migrate", *target, verbosity=0, interactive=False)

    def box(self, number=1):
        return Box.objects.create(
            organization=self.organization,
            strain=self.strain,
            global_code=f"RBP-LAB-1.{number:03}",
            box_number=f"{number:03}",
        )

    def assigned_global_id(self):
        box = self.box()
        identity = BoxGlobalIdentity.objects.create(
            allocation_reason=Reason.AUTOMATIC_CREATION
        )
        Box.objects.filter(pk=box.pk).update(global_identity=identity.global_id)
        return box, identity

    def receipt(self):
        return InstitutionStrainCreationRequest.objects.create(
            organization=self.organization,
            actor=self.user,
            request_id=uuid.uuid4(),
            intent={"profile": 1},
            strain=self.strain,
        )

    def snapshot(self):
        """Every observable effect of a partial downgrade, and the evidence."""
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT app, name, applied FROM django_migrations ORDER BY id"
            )
            migrations = cursor.fetchall()
            tables = sorted(connection.introspection.table_names(cursor))
            columns = {
                table: sorted(
                    column.name
                    for column in connection.introspection.get_table_description(
                        cursor, table
                    )
                )
                for table in ("cultures_box", "taxonomy_organizationprovenancecode")
            }
            if connection.vendor == "postgresql":
                cursor.execute(
                    "SELECT c.relname, t.tgname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
                    "WHERE NOT t.tgisinternal ORDER BY 1, 2"
                )
            else:
                cursor.execute(
                    "SELECT tbl_name, name FROM sqlite_master WHERE type = 'trigger' ORDER BY 1, 2"
                )
            triggers = cursor.fetchall()
            cursor.execute(
                "SELECT global_id, allocation_reason, created_at FROM cultures_boxglobalidentity "
                "ORDER BY global_id"
            )
            registry = cursor.fetchall()
            cursor.execute("SELECT id, global_id FROM cultures_box ORDER BY id")
            boxes = cursor.fetchall()
            cursor.execute(
                "SELECT id, request_id, strain_id FROM taxonomy_institutionstraincreationrequest ORDER BY id"
            )
            receipts = cursor.fetchall()
            evidence = {}
            for table in (
                "cultures_boxlocalidalias",
                "measurements_biologicalmeasurement",
                "taxonomy_speciescodechangerequest",
                "taxonomy_organizationprovenancecode",
                "taxonomy_origin",
            ):
                cursor.execute(f"SELECT * FROM {table} ORDER BY id")
                evidence[table] = cursor.fetchall()
        return {
            "migrations": migrations,
            "tables": tables,
            "columns": columns,
            "triggers": triggers,
            "registry": registry,
            "boxes": boxes,
            "receipts": receipts,
            "evidence": evidence,
        }

    def assert_refused_without_change(self, target, message):
        before = self.snapshot()
        plan = MigrationExecutor(connection).migration_plan([target])
        if sum(bool(backwards) for _, backwards in plan) > 1:
            message = "Multi-migration rollback across protected evidence is refused"
        with self.assertRaisesRegex(IrreversibleError, message):
            self.migrate(target)
        self.assertEqual(self.snapshot(), before)
        if connection.vendor == "postgresql":
            self.assertFalse(self.holds_registry_key())

    def holds_registry_key(self):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND pid = pg_backend_pid() "
                "AND classid = %s AND objid = %s AND objsubid = 2",
                rollback_preflight.REGISTRY_LOCK,
            )
            return cursor.fetchone()[0] > 0

    def test_plan_includes_dependents_before_protected_migrations(self):
        executor = MigrationExecutor(connection)

        def names(target):
            return [
                f"{m.app_label}.{m.name}"
                for m, backwards in executor.migration_plan([target])
            ]

        self.assertEqual(
            names(CULTURES_0021),
            [
                "cultures.0026_box_planned_weekday",
                "cultures.0025_merge_local_id_hardening_and_donations",
                "cultures.0024_merge_donation_and_local_identity",
                "cultures.0024_local_id_guard_search_path",
                "cultures.0023_box_local_id_alias",
                "cultures.0022_box_global_identity",
            ],
        )
        self.assertTrue(
            all(backwards for _, backwards in executor.migration_plan([CULTURES_0021]))
        )
        self.assertEqual(
            names(TAXONOMY_0012),
            [
                "taxonomy.0015_provenance_country_acquisition_types",
                "taxonomy.0014_speciescodechangerequest",
                "taxonomy.0013_institutionstraincreationrequest",
            ],
        )

    def test_assigned_global_id_refuses_before_0023_is_unapplied(self):
        self.assigned_global_id()
        self.assert_refused_without_change(CULTURES_0021, "Global IDs are permanent")

    @skipUnless(
        connection.vendor == "postgresql", "Requires PostgreSQL guarded fixture cleanup"
    )
    def test_refusal_retains_aliases_zero_measurements_and_active_sql_guards(self):
        box, identity = self.assigned_global_id()
        alias = BoxLocalIdAlias.objects.create(box=box, code="Permanent-rollback-alias")
        BiologicalMeasurement.objects.create(
            box=box,
            user=self.user,
            measured_on=date(2026, 11, 16),
            polyp_count=0,
            ephyrae_count=0,
        )
        self.assert_refused_without_change(CULTURES_0021, "Local ID aliases")
        with self.assertRaises(DatabaseError), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM cultures_boxlocalidalias WHERE id=%s", [alias.pk]
                )
        with self.assertRaises(DatabaseError), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE cultures_box SET global_id=NULL WHERE id=%s", [box.pk]
                )
        self.assertEqual(
            Box.objects.get(pk=box.pk).global_identity_id, identity.global_id
        )
        self.assertEqual(BiologicalMeasurement.objects.get(box=box).polyp_count, 0)

    def test_species_code_receipt_refuses_before_any_taxonomy_downgrade(self):
        profile = InstitutionSpecies.objects.create(
            organization=self.organization,
            species=self.strain.species,
            scientific_name="Rollback plan species",
        )
        SpeciesCodeChangeRequest.objects.create(
            organization=self.organization,
            actor=self.user,
            profile=profile,
            request_id=uuid.uuid4(),
            intent={},
            result={},
        )
        self.assert_refused_without_change(
            TAXONOMY_0012, "Species code change receipts"
        )

    def test_recorded_country_refuses_before_any_taxonomy_downgrade(self):
        provenance = BiologicalProvenance.objects.create(name="Rollback collection")
        OrganizationProvenanceCode.objects.create(
            organization=self.organization,
            biological_provenance=provenance,
            code="FRA",
            country_code="FR",
        )
        self.assert_refused_without_change(TAXONOMY_0012, "Recorded countries")

    def test_recorded_acquisition_refuses_before_any_taxonomy_downgrade(self):
        Origin.objects.create(source_type="artificial_fertilization_wild")
        self.assert_refused_without_change(TAXONOMY_0012, "Recorded countries")

    def test_box_creation_origin_refuses_complete_measurement_plan(self):
        BiologicalMeasurement.objects.create(
            box=self.box(),
            user=self.user,
            measured_on=date(2026, 11, 16),
            polyp_count=0,
            origin="box_creation",
            observer_status="known",
            original_observer=self.user,
        )
        self.assert_refused_without_change(
            ("measurements", "0018_box_1902_preservation"),
            "Box-creation initial measurements",
        )

    def test_unassigned_registry_identity_refuses_before_0023_is_unapplied(self):
        BoxGlobalIdentity.objects.create(allocation_reason=Reason.SUBCULTURE)
        self.assert_refused_without_change(CULTURES_0021, "Global IDs are permanent")

    def test_strain_receipt_refuses_before_0015_or_0014_is_unapplied(self):
        self.receipt()
        self.assert_refused_without_change(
            TAXONOMY_0012, "Strain creation receipts must be retained"
        )

    def test_alias_rollback_with_merge_is_refused_without_change_with_global_ids(self):
        self.assigned_global_id()
        plan = MigrationExecutor(connection).migration_plan([CULTURES_0022])
        self.assertEqual(
            [
                (migration.app_label, migration.name, backwards)
                for migration, backwards in plan
            ],
            [
                (*CULTURES_0026, True),
                ("cultures", "0025_merge_local_id_hardening_and_donations", True),
                ("cultures", "0024_merge_donation_and_local_identity", True),
                ("cultures", "0024_local_id_guard_search_path", True),
                ("cultures", "0023_box_local_id_alias", True),
            ],
        )
        self.assert_refused_without_change(CULTURES_0022, "Multi-migration rollback")

    def test_merge_only_rollback_is_still_allowed_with_global_ids(self):
        box, identity = self.assigned_global_id()
        target = CULTURES_0024_HARDENING
        plan = MigrationExecutor(connection).migration_plan([target])
        self.assertEqual(
            [
                (migration.app_label, migration.name, backwards)
                for migration, backwards in plan
            ],
            [
                (*CULTURES_0026, True),
                ("cultures", "0025_merge_local_id_hardening_and_donations", True),
            ],
        )
        before = self.snapshot()
        self.migrate(target)
        after = self.snapshot()
        removed = {(migration.app_label, migration.name) for migration, _ in plan}
        self.assertEqual(
            after.pop("migrations"),
            [row for row in before.pop("migrations") if row[:2] not in removed],
        )
        self.assertEqual(after, without_planned_weekday(before))
        # The current model would select the planned weekday column 0026 just removed.
        self.assertEqual(
            Box.objects.filter(pk=box.pk)
            .values_list("global_identity_id", flat=True)
            .get(),
            identity.global_id,
        )
        if connection.vendor == "postgresql":
            self.assertFalse(self.holds_registry_key())

    def test_lossless_partial_rollback_is_still_allowed_with_global_ids(self):
        box, identity = self.assigned_global_id()
        executor = MigrationExecutor(connection)
        plan = executor.migration_plan([CULTURES_0023])
        self.assertEqual(
            [
                (migration.app_label, migration.name, backwards)
                for migration, backwards in plan
            ],
            [
                (*CULTURES_0026, True),
                ("cultures", "0025_merge_local_id_hardening_and_donations", True),
                ("cultures", "0024_local_id_guard_search_path", True),
                ("cultures", "0024_merge_donation_and_local_identity", True),
            ],
        )
        self.assertEqual(rollback_preflight.guarded_migrations(plan), [])
        before = self.snapshot()
        self.migrate(CULTURES_0023)
        after = self.snapshot()
        removed = {(migration.app_label, migration.name) for migration, _ in plan}
        self.assertEqual(
            after.pop("migrations"),
            [row for row in before.pop("migrations") if row[:2] not in removed],
        )
        self.assertEqual(after, without_planned_weekday(before))
        plan = MigrationExecutor(connection).migration_plan([CULTURES_0022])
        self.assertEqual(
            [
                (migration.app_label, migration.name, backwards)
                for migration, backwards in plan
            ],
            [
                ("cultures", "0023_box_local_id_alias", True),
            ],
        )
        self.migrate(CULTURES_0022)
        self.assertNotIn(
            "cultures_boxlocalidalias", connection.introspection.table_names()
        )
        self.assertNotIn(
            CULTURES_0023, MigrationExecutor(connection).loader.applied_migrations
        )
        call_command("migrate", verbosity=0, interactive=False)
        self.assertEqual(
            Box.objects.get(pk=box.pk).global_identity_id, identity.global_id
        )
        if connection.vendor == "postgresql":
            self.assertFalse(self.holds_registry_key())

    def test_single_guarded_rollback_is_still_allowed_without_its_evidence(self):
        box, identity = self.assigned_global_id()
        target = ("taxonomy", "0014_speciescodechangerequest")
        plan = MigrationExecutor(connection).migration_plan([target])
        self.assertEqual(
            [
                (migration.app_label, migration.name, backwards)
                for migration, backwards in plan
            ],
            [
                ("taxonomy", "0015_provenance_country_acquisition_types", True),
            ],
        )
        before = self.snapshot()
        self.migrate(target)
        self.assertNotIn(
            ("taxonomy", "0015_provenance_country_acquisition_types"),
            MigrationExecutor(connection).loader.applied_migrations,
        )
        call_command("migrate", verbosity=0, interactive=False)
        after = self.snapshot()
        before.pop("migrations")
        after.pop("migrations")
        self.assertEqual(after, before)
        self.assertEqual(
            Box.objects.get(pk=box.pk).global_identity_id, identity.global_id
        )
        if connection.vendor == "postgresql":
            self.assertFalse(self.holds_registry_key())

    def test_empty_full_rollbacks_are_refused_without_any_downgrade(self):
        self.assert_refused_without_change(CULTURES_0021, "Multi-migration rollback")
        self.assert_refused_without_change(TAXONOMY_0012, "Multi-migration rollback")
        before = self.snapshot()
        call_command("migrate", verbosity=0, interactive=False)
        self.assertEqual(self.snapshot(), before)

    @skipUnless(
        connection.vendor == "postgresql",
        "Requires independent PostgreSQL receipt writers",
    )
    def test_receipt_committed_after_preflight_cannot_cause_partial_downgrade(self):
        profile = InstitutionSpecies.objects.create(
            organization=self.organization,
            species=self.strain.species,
            scientific_name="Rollback plan species",
        )
        for receipt_model, create in (
            (InstitutionStrainCreationRequest, self.receipt),
            (
                SpeciesCodeChangeRequest,
                lambda: SpeciesCodeChangeRequest.objects.create(
                    organization=self.organization,
                    actor=self.user,
                    profile=profile,
                    request_id=uuid.uuid4(),
                    intent={},
                    result={},
                ),
            ),
        ):
            with self.subTest(receipt=receipt_model.__name__):
                before = self.snapshot()
                allow_write = Event()

                def writer():
                    close_old_connections()
                    try:
                        if not allow_write.wait(timeout=10):
                            raise AssertionError("Preflight did not release the writer")
                        # Deliberately no registry advisory key: the old fence
                        # did not cover this legitimate database write.
                        with transaction.atomic():
                            receipt = create()
                            with connection.cursor() as cursor:
                                cursor.execute("SELECT pg_backend_pid()")
                                return receipt.pk, cursor.fetchone()[0]
                    finally:
                        connection.close()

                original_preflight = rollback_preflight.preflight_rollback_plan
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    migration_pid = cursor.fetchone()[0]
                with ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(writer)

                    def write_after_preflight(**kwargs):
                        try:
                            original_preflight(**kwargs)
                        finally:
                            # The old implementation has just committed and
                            # released table locks here. Also write on refusal
                            # to prove the restriction needs no writer fence.
                            self.assertFalse(connection.in_atomic_block)
                            allow_write.set()
                            future.result(timeout=15)

                    try:
                        with (
                            patch.object(
                                rollback_preflight,
                                "preflight_rollback_plan",
                                write_after_preflight,
                            ),
                            patch.object(
                                MigrationExecutor,
                                "unapply_migration",
                                autospec=True,
                                side_effect=MigrationExecutor.unapply_migration,
                            ) as unapply,
                        ):
                            with self.assertRaisesRegex(
                                IrreversibleError, "Multi-migration rollback"
                            ):
                                self.migrate(TAXONOMY_0012)
                            unapply.assert_not_called()
                    finally:
                        allow_write.set()
                    receipt_pk, writer_pid = future.result(timeout=15)
                self.assertNotEqual(writer_pid, migration_pid)
                self.assertTrue(receipt_model.objects.filter(pk=receipt_pk).exists())
                after = self.snapshot()
                # Only the intentionally committed concurrent receipt differs.
                before.pop("receipts")
                after.pop("receipts")
                before["evidence"].pop("taxonomy_speciescodechangerequest")
                after["evidence"].pop("taxonomy_speciescodechangerequest")
                self.assertEqual(after, before)
                self.assertFalse(self.holds_registry_key())

    @skipUnless(
        connection.vendor == "postgresql",
        "Requires PostgreSQL advisory and table locks",
    )
    def test_active_identity_writer_refuses_rollback_without_change(self):
        holding, release = Event(), Event()

        def writer():
            close_old_connections()
            try:
                with transaction.atomic():
                    lock_local_id_registry()
                    holding.set()
                    if not release.wait(timeout=15):
                        raise AssertionError("Writer was not released")
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(writer)
            try:
                self.assertTrue(holding.wait(timeout=10))
                self.assert_refused_without_change(
                    CULTURES_0021, "Identity writers are active"
                )
                self.assert_refused_without_change(
                    TAXONOMY_0012, "Identity writers are active"
                )
            finally:
                release.set()
            future.result(timeout=15)

    @skipUnless(
        connection.vendor == "postgresql",
        "Requires PostgreSQL advisory and table locks",
    )
    def test_uncommitted_raw_identity_or_receipt_refuses_rollback_without_change(self):
        for target, create in (
            (
                CULTURES_0021,
                lambda: BoxGlobalIdentity.objects.create(
                    allocation_reason=Reason.AUTOMATIC_CREATION
                ),
            ),
            (TAXONOMY_0012, self.receipt),
        ):
            holding, release = Event(), Event()

            def writer():
                close_old_connections()
                try:
                    with transaction.atomic():
                        create()  # Does not take the registry key: not a cooperating writer.
                        holding.set()
                        if not release.wait(timeout=15):
                            raise AssertionError("Writer was not released")
                        raise RuntimeError("Roll back the uncommitted write")
                except RuntimeError:
                    pass
                finally:
                    connection.close()

            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(writer)
                try:
                    self.assertTrue(holding.wait(timeout=10))
                    self.assert_refused_without_change(
                        target, "Protected tables are in use"
                    )
                finally:
                    release.set()
                future.result(timeout=15)
        self.assertFalse(BoxGlobalIdentity.objects.exists())
        self.assertFalse(InstitutionStrainCreationRequest.objects.exists())

    @skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL advisory locks")
    def test_passed_preflight_blocks_new_evidence_until_the_rollback_ends(self):
        # Both 0024 branches and 0025 reverse without a protected step.
        self.migrate(CULTURES_0023)
        executor = MigrationExecutor(connection)
        plan = executor.migration_plan([CULTURES_0022])
        self.assertEqual(
            [
                (migration.app_label, migration.name, backwards)
                for migration, backwards in plan
            ],
            [
                ("cultures", "0023_box_local_id_alias", True),
            ],
        )
        apps = executor._create_project_state(with_applied_migrations=True).apps
        rollback_preflight.preflight_rollback_plan(
            plan=plan, apps=apps, using=connection.alias
        )
        self.assertTrue(self.holds_registry_key())

        def allocate():
            close_old_connections()
            try:
                with transaction.atomic():
                    with connection.cursor() as cursor:
                        cursor.execute("SET LOCAL lock_timeout = '200ms'")
                    lock_local_id_registry()
                    BoxGlobalIdentity.objects.create(
                        allocation_reason=Reason.AUTOMATIC_CREATION
                    )
            except DatabaseError as error:
                return sqlstate(error)
            finally:
                connection.close()
            return "committed"

        with ThreadPoolExecutor(max_workers=1) as pool:
            self.assertEqual(pool.submit(allocate).result(timeout=15), "55P03")
            rollback_preflight.release_registry_lock(connection.alias)
            self.assertFalse(self.holds_registry_key())
            self.assertEqual(pool.submit(allocate).result(timeout=15), "committed")
        self.assertEqual(BoxGlobalIdentity.objects.count(), 1)
