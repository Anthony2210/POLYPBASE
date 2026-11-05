"""Single-Box PostgreSQL proofs; use only the owned isolated native-cluster launcher."""

from copy import deepcopy
from threading import Event, current_thread
from unittest import skipUnless
from unittest.mock import patch

from django.db import DatabaseError, connection, transaction
from django.test import TransactionTestCase

from apps.audit.models import AuditLog
from apps.measurements.models import BiologicalMeasurement
from apps.organizations.models import Organization

from .historical_2026 import cla_box_1902 as correction
from .historical_2026 import identity_execution as execution
from .models import Box, BoxCodeNamespace
from .qr import box_scan_url
from .test_cla_box_1902 import ClaBox1902DependencyRegressions, ClaBox1902Fixture, NEW_CODE, OPERATION, OLD_CODE
from .test_identity_reconciliation_postgres import PostgreSQLFixture, WAIT_SECONDS, sqlstate


@skipUnless(connection.vendor == "postgresql", "Requires the disposable native PostgreSQL cluster.")
class ClaBox1902PostgreSQLTests(ClaBox1902DependencyRegressions, ClaBox1902Fixture, PostgreSQLFixture, TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        # Reuse the worker/teardown protocol, not PostgreSQLFixture's batch data.
        TransactionTestCase.setUp(self)
        self.assertFalse(connection.in_atomic_block)
        self.assertEqual(execution.require_database(), "disposable")
        with execution.session(operation=OPERATION) as binding:
            self.assertEqual(binding["mode"], "disposable")
        self.create_fixture()

    def test_exact_box_delta_preserves_references_zero_null_and_qr(self):
        reviewed = self.plan()
        self.assert_classification(reviewed, "READY")
        before = self.snapshot()
        expected_boxes = {row["id"]: row for row in Box.objects.order_by("pk").values()}
        expected_boxes = deepcopy(expected_boxes)
        expected_boxes[1902].update(global_code=NEW_CODE, strain_id=self.target.pk)
        namespaces = dict(BoxCodeNamespace.objects.values_list("namespace", "high_water"))
        audits = list(AuditLog.objects.order_by("pk").values())
        url = box_scan_url(self.box)
        self.assertEqual(self.apply(plan=reviewed), reviewed)
        self.assertEqual({row["id"]: row for row in Box.objects.order_by("pk").values()}, expected_boxes)
        after = self.snapshot()
        mutable = {Box._meta.db_table, AuditLog._meta.db_table, BoxCodeNamespace._meta.db_table}
        for table in before.keys() - mutable:
            self.assertEqual(after[table], before[table], table)
        namespaces_after = dict(BoxCodeNamespace.objects.values_list("namespace", "high_water"))
        self.assertEqual(
            {key: value for key, value in namespaces_after.items() if key != "CLC-JKA-1"},
            {key: value for key, value in namespaces.items() if key != "CLC-JKA-1"},
        )
        self.assertGreaterEqual(namespaces_after["CLC-JKA-1"], max(9, namespaces.get("CLC-JKA-1", 0)))
        self.assertEqual(list(AuditLog.objects.filter(pk__in=[row["id"] for row in audits])
                              .order_by("pk").values()), audits)
        self.box.refresh_from_db()
        self.assertEqual(self.box.local_code, "CLA-JKA-1.09")
        self.assertEqual(self.box.previous_global_code, "")
        self.assertIsNone(self.box.merged_into_id)
        self.assertEqual(self.box.status, Box.Status.INACTIVE)
        self.assertEqual(box_scan_url(self.box), url)
        self.assertEqual(self.box.tags.get(tag_type="qr").url, url)
        measurements = self.box.biological_measurements.order_by("measured_on")
        self.assertEqual(list(measurements.values_list("polyp_count", "ephyrae_count", "strobila_count")),
                         [(0, 0, 0), (0, 0, None)])
        self.assertEqual(list(measurements.values_list("salinity_psu", flat=True)), [0, None])
        receipt = self.receipt()
        self.assertEqual(receipt.user_id, self.admin.pk)
        self.assertEqual(receipt.metadata["plan_hash"], correction.fingerprint(reviewed))
        self.assertEqual(receipt.metadata["before"]["global_code"], OLD_CODE)
        self.assertEqual(receipt.metadata["after"]["id"], 1902)
        self.assertEqual(receipt.metadata["after"]["global_code"], NEW_CODE)
        self.assertEqual(receipt.metadata["after"]["strain_id"], self.target.pk)
        self.assertEqual(AuditLog.objects.filter(metadata__operation=OPERATION).count(), 2)
        self.assertFalse(AuditLog.objects.filter(object_type="identity_reconciliation").exists())

    def test_concurrent_apply_commits_once_then_requires_a_fresh_review(self):
        reviewed = self.plan()
        held, release = Event(), Event()
        original = AuditLog.objects.create

        def hold_first_audit(**kwargs):
            result = original(**kwargs)
            if current_thread().name == "cla-1902-first" and not held.is_set():
                held.set()
                if not release.wait(WAIT_SECONDS):
                    raise TimeoutError("First single-Box apply was not released")
            return result

        with patch.object(AuditLog.objects, "create", side_effect=hold_first_audit):
            with self.workers() as (start, releases):
                releases.append(release)
                one = start(lambda: self.apply(plan=reviewed), "cla-1902-first")
                self.wait_for(held, "First apply did not reach its transactional audit")
                two = start(lambda: self.apply(plan=reviewed), "cla-1902-second")
                self.wait_for(two["done"], "Second apply did not reject held maintenance locks")
                self.assert_busy_or_stale(two.get("error"))
                self.assertFalse(one["done"].is_set())
                release.set()
                self.wait_for(one["done"], "First apply did not commit")
        self.assertNotIn("error", one, one.get("error"))
        self.assertEqual(one["result"], reviewed)
        self.assertEqual(AuditLog.objects.filter(metadata__operation=OPERATION).count(), 2)
        self.receipt()
        fresh = self.plan()
        self.assert_classification(fresh, "ALREADY_CORRECT")
        before = self.snapshot()
        with self.assertRaises(ValueError):
            self.apply(plan=reviewed)
        self.assertEqual(self.apply(plan=fresh), fresh)
        self.assertEqual(self.snapshot(), before)

    def test_active_writer_aborts_apply_nowait_then_invalidates_the_old_plan(self):
        reviewed = self.plan()
        held, release = Event(), Event()

        def writer():
            with transaction.atomic():
                box = Box.objects.select_for_update().get(pk=1902)
                box.notes = "Synthetic concurrent ordinary writer"
                box.save(update_fields=["notes"])
                held.set()
                if not release.wait(WAIT_SECONDS):
                    raise TimeoutError("Ordinary single-Box writer was not released")

        before = self.snapshot()
        with self.workers() as (start, releases):
            releases.append(release)
            one = start(writer, "cla-1902-writer")
            self.wait_for(held, "Ordinary writer did not hold the Box lock")
            statements = []

            def record_sql(execute, sql, params, many, context):
                statements.append(sql)
                return execute(sql, params, many, context)

            def apply_while_busy():
                with connection.execute_wrapper(record_sql):
                    return self.apply(plan=reviewed)

            two = start(apply_while_busy, "cla-1902-maintenance")
            self.wait_for(two["done"], "Apply waited instead of failing closed with NOWAIT")
            locks = [sql for sql in statements if sql.lstrip().upper().startswith("LOCK TABLE")]
            self.assertTrue(locks, statements)
            self.assertTrue(all("EXCLUSIVE MODE NOWAIT" in sql.upper() for sql in locks))
            self.assertFalse(any("FOR UPDATE" in sql.upper() or "FOR NO KEY UPDATE" in sql.upper()
                                 for sql in statements), "Busy tables must be rejected before row locks")
            self.assert_busy_or_stale(two.get("error"))
            self.assertFalse(one["done"].is_set())
            self.assertEqual(self.snapshot(), before)
            release.set()
            self.wait_for(one["done"], "Ordinary writer did not commit")
        self.assertNotIn("error", one, one.get("error"))
        fresh = self.plan()
        self.assertNotEqual(correction.fingerprint(fresh), correction.fingerprint(reviewed))
        after_writer = self.snapshot()
        with self.assertRaises(ValueError):
            self.apply(plan=reviewed)
        self.assertEqual(self.snapshot(), after_writer)
        self.assertEqual(self.apply(plan=fresh), fresh)

    def test_measurement_change_invalidates_review_and_preserves_zero(self):
        reviewed = self.plan()
        measurement = self.box.biological_measurements.order_by("measured_on").first()
        self.assertEqual(measurement.strobila_count, 0)
        BiologicalMeasurement.objects.filter(pk=measurement.pk).update(strobila_count=None)
        self.assertNotEqual(correction.fingerprint(self.plan()), correction.fingerprint(reviewed))
        before = self.snapshot()
        with self.assertRaises(ValueError):
            self.apply(plan=reviewed)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(list(self.box.biological_measurements.order_by("measured_on")
                              .values_list("polyp_count", "ephyrae_count", "strobila_count")),
                         [(0, 0, None), (0, 0, None)])

    def test_receipt_failure_rolls_back_box_namespaces_and_first_audit(self):
        reviewed = self.plan()
        before = self.snapshot()
        original = AuditLog.objects.create
        calls = []

        def fail_receipt(**kwargs):
            calls.append(kwargs["object_type"])
            if kwargs["object_type"] == "cla_box_1902_correction":
                self.assertEqual(Box.objects.get(pk=1902).global_code, NEW_CODE)
                self.assertEqual(AuditLog.objects.filter(
                    object_type="box", metadata__operation=OPERATION,
                ).count(), 1)
                raise RuntimeError("Synthetic single-Box receipt failure")
            return original(**kwargs)

        with patch.object(AuditLog.objects, "create", side_effect=fail_receipt):
            with self.assertRaisesMessage(RuntimeError, "Synthetic single-Box receipt failure"):
                self.apply(plan=reviewed)
        self.assertEqual(calls, ["box", "cla_box_1902_correction"])
        self.assertFalse(connection.in_atomic_block)
        self.assertFalse(connection.needs_rollback)
        self.assertEqual(self.snapshot(), before)
        self.assert_classification(self.plan(), "READY")

    def test_planning_uses_read_only_transaction_without_write_or_row_locks(self):
        original = correction._build_plan
        observed = []

        def assert_read_only():
            self.assertTrue(connection.in_atomic_block)
            with connection.cursor() as cursor:
                cursor.execute("SHOW transaction_read_only")
                self.assertEqual(cursor.fetchone(), ("on",))
                cursor.execute(
                    "SELECT locktype, mode FROM pg_locks WHERE pid=pg_backend_pid() "
                    "AND (locktype='tuple' OR (locktype='relation' AND mode IN "
                    "('RowShareLock', 'RowExclusiveLock', 'ShareRowExclusiveLock', "
                    "'ExclusiveLock', 'AccessExclusiveLock')))"
                )
                self.assertEqual(cursor.fetchall(), [])
            observed.append(True)

        def inspect(*args, **kwargs):
            assert_read_only()
            result = original(*args, **kwargs)
            assert_read_only()
            return result

        before = self.snapshot()
        with patch.object(correction, "_build_plan", side_effect=inspect):
            reviewed = self.plan()
            self.assert_classification(reviewed, "READY")
            self.assertEqual(self.plan(), reviewed)
        self.assertEqual(len(observed), 4)
        self.assertFalse(connection.in_atomic_block)
        with connection.cursor() as cursor:
            cursor.execute("SHOW transaction_read_only")
            self.assertEqual(cursor.fetchone(), ("off",))
        self.assertEqual(self.snapshot(), before)

    def test_postgresql_rejects_injected_planner_write_and_restores_session(self):
        original = correction._build_plan
        before = self.snapshot()

        def attempt_write(*args, **kwargs):
            original(*args, **kwargs)
            Organization.objects.filter(pk=1).update(name="Forbidden synthetic planner write")
            self.fail("PostgreSQL accepted a write inside read-only planning")

        with patch.object(correction, "_build_plan", side_effect=attempt_write):
            with self.assertRaises(DatabaseError) as caught:
                self.plan()
        self.assertEqual(sqlstate(caught.exception), "25006")
        self.assertFalse(connection.in_atomic_block)
        self.assertFalse(connection.needs_rollback)
        self.assertEqual(self.snapshot(), before)
        self.assert_classification(self.plan(), "READY")
