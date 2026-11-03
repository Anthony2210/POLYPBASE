"""Synthetic PostgreSQL 18 proofs for the six-Box variant correction.

Run only through scripts/test_historical_temperature_production.py, which owns a
fresh native cluster; the disposable guard below refuses any other server.
"""

from contextlib import contextmanager
from datetime import date
from threading import Event, Thread
from time import monotonic
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management.color import no_style
from django.db import IntegrityError, close_old_connections, connection, connections, transaction
from django.test import TransactionTestCase

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.measurements.models import BiologicalMeasurement
from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain

from .historical_2026 import identity_execution as execution
from .historical_2026 import variant_species_correction as correction
from .models import Box

WAIT_SECONDS = 20
EXPECTED = {pk: name for pk, _code, _strain_code, name in correction.SPECS}


@skipUnless(connection.vendor == "postgresql", "Requires the disposable native PostgreSQL cluster.")
class VariantSpeciesPostgreSQLTests(TransactionTestCase):
    def _fixture_teardown(self):
        if execution.require_database() != "disposable":
            raise RuntimeError("Trigger-aware flush requires the owned fresh native cluster")
        # Only synthetic teardown may bypass these append-only TRUNCATE guards.
        tables = ("measurements_historicaltemperaturereading", "measurements_historicaltemperatureevidence",
                  "measurements_historicaltemperatureimportreceipt")
        disabled = []
        try:
            with connection.cursor() as cursor:
                for table in tables:
                    cursor.execute(f"ALTER TABLE {connection.ops.quote_name(table)} DISABLE TRIGGER USER")
                    disabled.append(table)
            super()._fixture_teardown()
        finally:
            with connection.cursor() as cursor:
                for table in disabled:
                    cursor.execute(f"ALTER TABLE {connection.ops.quote_name(table)} ENABLE TRIGGER USER")

    def setUp(self):
        super().setUp()
        self.assertEqual(execution.require_database(), "disposable")
        self.assertFalse(connection.in_atomic_block)
        with connection.cursor() as cursor:
            cursor.execute("SHOW server_version_num")
            self.assertEqual(int(cursor.fetchone()[0]) // 10000, 18)
        self.org = Organization.objects.create(pk=correction.ORGANIZATION_PK, name="Variant PG QA",
                                               slug="variant-pg-qa", portable_id=correction.ORGANIZATION_PORTABLE_ID)
        self.admin = get_user_model().objects.create_user(username="variant-pg-admin",
                                                          email="variant-pg-admin@example.org")
        OrganizationMembership.objects.create(organization=self.org, user=self.admin, role="admin")
        self.white = Species.objects.create(pk=127, scientific_name=correction.WHITE)
        for pk, name in correction.SOURCE_SPECIES.items():
            Species.objects.create(pk=pk, scientific_name=name)
        for pk, (code, species_pk) in correction.SOURCE_STRAINS.items():
            Strain.objects.create(pk=pk, species_id=species_pk, code=code)
        for pk, code, _strain_code, _name in correction.SPECS:
            Box.objects.create(pk=pk, organization=self.org, global_code=code, box_number=code[-2:],
                               strain_id=correction.SOURCE_FOR_BOX[pk],
                               status=Box.Status.INACTIVE if pk in (1865,) else Box.Status.ACTIVE)
            BiologicalMeasurement.objects.create(box_id=pk, measured_on=date(2026, 9, 7), polyp_count=0,
                                                 ephyrae_count=0, strobila_count=None, user=self.admin)
        with connection.cursor() as cursor:
            for sql in connection.ops.sequence_reset_sql(no_style(), [Organization, Species, Strain, Box]):
                cursor.execute(sql)
        self.unrelated = Box.objects.create(organization=self.org, global_code="CCP-ACH-2.003", box_number="03",
                                            strain_id=201)
        self.stray = Species.objects.create(scientific_name="Chrysaora sp. synthetic")
        self.impostor = Strain.objects.create(organization=self.org, species=self.white, code="CCP-ACH-2")
        self.sources = sorted(correction.SOURCE_STRAINS)

    # Helpers -----------------------------------------------------------------

    def prepare(self):
        plan = correction.build_reference_plan(self.org.pk, source_strain_ids=self.sources)
        result = correction.apply_reference_plan(self.org.pk, source_strain_ids=self.sources, actor=self.admin,
                                                 expected_plan_hash=correction.fingerprint(plan))
        return sorted(result["prepared_target_strain_ids"].values())

    def box_hash(self, targets):
        return correction.fingerprint(correction.build_box_plan(
            self.org.pk, source_strain_ids=self.sources, target_strain_ids=targets))

    def apply_boxes(self, targets, reviewed):
        return lambda: correction.apply_box_plan(self.org.pk, source_strain_ids=self.sources,
                                                 target_strain_ids=targets, actor=self.admin,
                                                 expected_plan_hash=reviewed)

    def state(self):
        return {
            "boxes": list(Box.objects.order_by("pk").values()),
            "strains": list(Strain.objects.order_by("pk").values()),
            "species": list(Species.objects.order_by("pk").values()),
            "measurements": list(BiologicalMeasurement.objects.order_by("pk").values()),
            "audits": list(AuditLog.objects.order_by("pk").values_list("pk", "object_type", "object_id")),
        }

    @contextmanager
    def workers(self):
        """Always release holders and join before TransactionTestCase flushes."""
        workers, releases = [], []

        def start(action, name):
            state = {"ready": Event(), "done": Event()}

            def run():
                close_old_connections()
                try:
                    with connection.cursor() as cursor:
                        cursor.execute("SET lock_timeout = '8s'")
                        cursor.execute("SELECT pg_backend_pid()")
                        state["pid"] = cursor.fetchone()[0]
                    state["ready"].set()
                    state["result"] = action()
                except BaseException as error:  # noqa: BLE001 - reported to the test thread
                    state["error"] = error
                finally:
                    connections.close_all()
                    state["done"].set()

            thread = Thread(target=run, name=name, daemon=True)
            workers.append((thread, state))
            thread.start()
            self.assertTrue(state["ready"].wait(WAIT_SECONDS), state.get("error"))
            return state

        try:
            yield start, releases
        finally:
            for event in releases:
                event.set()
            for thread, state in workers:
                thread.join(WAIT_SECONDS)
                self.assertFalse(thread.is_alive(), f"Worker did not terminate: {thread.name}")
            close_old_connections()

    def holder(self, write, locked, release):
        def run():
            with transaction.atomic():
                write()
                locked.set()
                if not release.wait(WAIT_SECONDS):
                    raise RuntimeError("Holder was never released")
        return run

    def assert_blocked_by(self, waiter, holder):
        deadline = monotonic() + 10
        while monotonic() < deadline:
            with connection.cursor() as cursor:
                cursor.execute("SELECT %s = ANY(pg_blocking_pids(%s))", [holder["pid"], waiter["pid"]])
                if cursor.fetchone()[0]:
                    self.assertFalse(waiter["done"].is_set())
                    return
            if waiter["done"].wait(0.02):
                break
        self.fail(f"No PostgreSQL lock wait observed; worker error: {waiter.get('error')!r}")

    def run_against_holder(self, write, action):
        locked, release = Event(), Event()
        with self.workers() as (start, releases):
            releases.append(release)
            holder = start(self.holder(write, locked, release), "holder")
            self.assertTrue(locked.wait(WAIT_SECONDS), holder.get("error"))
            waiter = start(action, "applier")
            self.assert_blocked_by(waiter, holder)
            release.set()
            self.assertTrue(waiter["done"].wait(WAIT_SECONDS))
        self.assertNotIn("error", holder)
        return waiter

    # Tests -------------------------------------------------------------------

    def test_application_and_idempotence_preserve_shared_references(self):
        targets = self.prepare()
        before = self.state()
        correction.apply_box_plan(self.org.pk, source_strain_ids=self.sources, target_strain_ids=targets,
                                  actor=self.admin, expected_plan_hash=self.box_hash(targets))
        after = self.state()
        for pk, name in EXPECTED.items():
            box = Box.objects.select_related("strain__species").get(pk=pk)
            self.assertEqual(box.strain.species.scientific_name, name)
        changed = {row["id"] for row in after["boxes"]} & set(EXPECTED)
        self.assertEqual([r for r in after["boxes"] if r["id"] not in changed],
                         [r for r in before["boxes"] if r["id"] not in changed])
        for key in ("strains", "species", "measurements"):
            self.assertEqual(after[key], before[key])
        self.assertEqual(Box.objects.get(pk=1865).status, Box.Status.INACTIVE)
        self.assertEqual(BiologicalMeasurement.objects.filter(polyp_count=0, strobila_count__isnull=True).count(), 6)
        self.assertEqual(AuditLog.objects.filter(metadata__operation=correction.BOX_OPERATION).count(), 6)
        idle = self.state()
        correction.apply_box_plan(self.org.pk, source_strain_ids=self.sources, target_strain_ids=targets,
                                  actor=self.admin, expected_plan_hash=self.box_hash(targets))
        self.assertEqual(self.state(), idle)

    def test_concurrent_box_swap_to_same_code_strain_is_revalidated_after_lock(self):
        targets = self.prepare()
        reviewed = self.box_hash(targets)
        before = self.state()
        waiter = self.run_against_holder(
            lambda: Box.objects.select_for_update().filter(pk=1855).update(strain=self.impostor),
            self.apply_boxes(targets, reviewed))
        self.assertIsInstance(waiter.get("error"), ValueError)
        self.assertIn("Database state changed", str(waiter["error"]))
        after = self.state()
        self.assertEqual(Box.objects.get(pk=1855).strain_id, self.impostor.pk)  # holder's own commit only
        self.assertEqual([r for r in after["boxes"] if r["id"] != 1855],
                         [r for r in before["boxes"] if r["id"] != 1855])
        self.assertEqual(after["audits"], before["audits"])
        plan = correction.build_box_plan(self.org.pk, source_strain_ids=self.sources, target_strain_ids=targets)
        self.assertIn("CCP-ACH-2.002: Current Strain differs from the reviewed source contract.", plan["conflicts"])

    def test_concurrent_source_species_change_is_revalidated_after_lock(self):
        targets = self.prepare()
        reviewed = self.box_hash(targets)
        before = self.state()
        waiter = self.run_against_holder(
            lambda: Strain.objects.select_for_update().filter(pk=201).update(species=self.stray),
            self.apply_boxes(targets, reviewed))
        self.assertIsInstance(waiter.get("error"), ValueError)
        self.assertIn("Database state changed", str(waiter["error"]))
        after = self.state()
        self.assertEqual(after["boxes"], before["boxes"])
        self.assertEqual(after["audits"], before["audits"])
        plan = correction.build_box_plan(self.org.pk, source_strain_ids=self.sources, target_strain_ids=targets)
        self.assertIn("Source Strain 201 differs from the reviewed source contract.", plan["conflicts"])

    def test_concurrent_box_change_is_revalidated_in_reference_step(self):
        reviewed = correction.fingerprint(correction.build_reference_plan(
            self.org.pk, source_strain_ids=self.sources))
        before = self.state()
        waiter = self.run_against_holder(
            lambda: Box.objects.select_for_update().filter(pk=2228).update(strain=self.impostor),
            lambda: correction.apply_reference_plan(self.org.pk, source_strain_ids=self.sources,
                                                    actor=self.admin, expected_plan_hash=reviewed))
        self.assertIsInstance(waiter.get("error"), ValueError)
        self.assertIn("Database state changed", str(waiter["error"]))
        after = self.state()
        self.assertEqual((after["strains"], after["species"], after["audits"]),
                         (before["strains"], before["species"], before["audits"]))

    def test_two_concurrent_appliers_write_one_audit_batch(self):
        targets = self.prepare()
        reviewed = self.box_hash(targets)
        locked, release = Event(), Event()
        with self.workers() as (start, releases):
            releases.append(release)
            holder = start(self.holder(lambda: list(Box.objects.select_for_update().filter(pk=1854)),
                                       locked, release), "holder")
            self.assertTrue(locked.wait(WAIT_SECONDS))
            first = start(self.apply_boxes(targets, reviewed), "first")
            second = start(self.apply_boxes(targets, reviewed), "second")
            self.assert_blocked_by(first, holder)
            # The first applier holds the organization lock while waiting on the Box.
            # The second therefore queues behind the first, not behind the Box holder.
            self.assert_blocked_by(second, first)
            release.set()
            self.assertTrue(first["done"].wait(WAIT_SECONDS) and second["done"].wait(WAIT_SECONDS))
        errors = [w.get("error") for w in (first, second)]
        self.assertEqual(sum(e is None for e in errors), 1, errors)
        failure = next(e for e in errors if e is not None)
        self.assertIsInstance(failure, ValueError)
        self.assertIn("Database state changed", str(failure))
        self.assertEqual(AuditLog.objects.filter(metadata__operation=correction.BOX_OPERATION).count(), 6)

    def test_reference_receipt_failure_rolls_back_new_references_and_audits(self):
        reviewed = correction.fingerprint(correction.build_reference_plan(
            self.org.pk, source_strain_ids=self.sources))
        before = self.state()
        create, calls = AuditLog.objects.create, []

        def fail_receipt(**kwargs):
            calls.append(kwargs)
            if kwargs["object_type"] == "variant_species_reference_preparation":
                raise RuntimeError("Receipt unavailable")
            return create(**kwargs)

        with patch.object(correction.AuditLog.objects, "create", side_effect=fail_receipt):
            with self.assertRaisesMessage(RuntimeError, "Receipt unavailable"):
                correction.apply_reference_plan(self.org.pk, source_strain_ids=self.sources,
                                                actor=self.admin, expected_plan_hash=reviewed)
        self.assertEqual(len(calls), 6)
        self.assertEqual(self.state(), before)

    def test_uniqueness_constraints_allow_same_code_only_under_different_species(self):
        self.prepare()
        before = self.state()
        duplicates = (
            ("taxonomy_species_scientific_name_key",
             lambda: Species.objects.create(scientific_name=correction.PINK)),
            ("unique_strain_per_species",
             lambda: Strain.objects.create(organization=self.org, species_id=128, code="CCP-ACH-2")),
        )
        for constraint, create in duplicates:
            with self.subTest(constraint=constraint):
                with self.assertRaises(IntegrityError) as caught:
                    with transaction.atomic():
                        create()
                self.assertEqual(caught.exception.__cause__.diag.constraint_name, constraint)
                self.assertEqual(self.state(), before)
        self.assertEqual(Strain.objects.filter(code="CCP-ACH-2").count(), 3)

    def test_explicit_fixture_pks_advance_sequences_without_collisions(self):
        organization = Organization.objects.create(name="Sequence QA", slug="sequence-qa")
        species = Species.objects.create(scientific_name="Sequence synthetic species")
        strain = Strain.objects.create(organization=organization, species=species, code="SEQ-QAA-1")
        box = Box.objects.create(organization=organization, strain=strain,
                                 global_code="SEQ-QAA-1.001", box_number="01")
        self.assertGreater(organization.pk, correction.ORGANIZATION_PK)
        self.assertGreater(species.pk, max(correction.SOURCE_SPECIES))
        self.assertGreater(strain.pk, max(correction.SOURCE_STRAINS))
        self.assertGreater(box.pk, max(correction.BOX_PKS))
        self.assertEqual(Box.objects.filter(pk__in=correction.BOX_PKS).count(), 6)

    def test_last_audit_failure_rolls_back_relations_and_audits(self):
        targets = self.prepare()
        reviewed = self.box_hash(targets)
        before = self.state()
        create, calls = AuditLog.objects.create, []

        def fail_last(**kwargs):
            calls.append(kwargs)
            if len(calls) == 6:
                raise RuntimeError("Audit unavailable")
            return create(**kwargs)

        with patch.object(correction.AuditLog.objects, "create", side_effect=fail_last):
            with self.assertRaisesMessage(RuntimeError, "Audit unavailable"):
                self.apply_boxes(targets, reviewed)()
        self.assertEqual(len(calls), 6)
        self.assertEqual(self.state(), before)
