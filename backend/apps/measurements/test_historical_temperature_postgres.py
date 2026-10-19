"""Opt-in PostgreSQL proofs for the dedicated isolated temperature QA launcher.

Both documented PostgreSQL opt-ins and the fail-closed dedicated settings are
required. The runner never creates/drops/clones databases. Destructive synthetic
fixture cleanup is owner-only and confined to the authorized disposable target.
"""

import os
from importlib import import_module
from queue import Queue
from threading import Barrier, Event, Thread
from time import monotonic, sleep
from unittest import skipUnless

from django.conf import settings
from django.db import IntegrityError, close_old_connections, connection, transaction
from django.test import TransactionTestCase

from apps.cultures.models import Box
from apps.taxonomy.models import Strain

from .historical_temperature_apply import _accept_locked, _current_snapshot
from .historical_temperature_partial import PartialImportError
from .models import (
    BiologicalMeasurement, HistoricalTemperatureEvidence,
    HistoricalTemperatureImportReceipt, HistoricalTemperatureReading,
)
from . import test_historical_temperature_apply as apply_tests


_ENABLED = (os.getenv("POLYPBASE_HISTORICAL_POSTGRES_QA") == "1"
            and os.getenv("POLYPBASE_TEST_POSTGRES") == "1"
            and getattr(settings, "HISTORICAL_TEMPERATURE_PGQA", False))
_GUARDS = import_module("apps.measurements.migrations.0012_historical_temperature_guards")
_RECEIPT = import_module("apps.measurements.migrations.0013_historical_temperature_receipt")
_LOCK_SQL = "LOCK TABLE cultures_box, measurements_biologicalmeasurement, measurements_historicaltemperaturereading, measurements_historicaltemperatureevidence, measurements_historicaltemperatureimportreceipt IN SHARE ROW EXCLUSIVE MODE"


GUARD_MESSAGE = "Historical relationship writes require READ COMMITTED"


@skipUnless(_ENABLED, "PostgreSQL proof requires explicit disposable-database opt-in")
class PostgreSQLHistoricalTemperatureTests(TransactionTestCase):
    accept = apply_tests.AcceptanceTests.accept

    def _assert_disposable(self):
        from config.historical_temperature_pgqa import assert_disposable

        assert_disposable(connection)

    def setUp(self):
        self._assert_disposable()
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM pg_trigger WHERE tgname = 'historical_reading_valid'")
            missing = cursor.fetchone() is None
        if missing:
            with connection.schema_editor() as editor:
                _GUARDS.forwards(None, editor)
                _RECEIPT.guards(None, editor)
        apply_tests.AcceptanceTests.setUp(self)

    def _fixture_teardown(self):
        from config.historical_temperature_pgqa import cleanup_test_data

        cleanup_test_data()

    def _sql(self, sql, params=None):
        with connection.cursor() as cursor:
            cursor.execute(sql, params)

    def _insert_evidence(self, **changes):
        # Deliberately bypass model validation and avoid existing source keys.
        item = HistoricalTemperatureEvidence.objects.order_by("pk").first()
        fields = [field.column for field in item._meta.fields if field.name not in {"id", "created_at"}]
        values = {name: getattr(item, name) for name in fields}
        values.update(polyp_cell="F11", ephyrae_cell="F12", temperature_cell="C11", temperature_merged_range="C11:C12")
        values.update(changes)
        placeholders = ",".join(["%s"] * len(fields))
        self._sql(
            f"INSERT INTO measurements_historicaltemperatureevidence ({','.join(fields)},created_at) "
            f"VALUES ({placeholders},CURRENT_TIMESTAMP)", [values[name] for name in fields],
        )

    def _insert_reading(self, **changes):
        values = {
            "biological_measurement_id": self.biology.pk, "iso_year": 2026, "iso_week": 1,
            "temperature_c": "0", "association_status": "confirmed",
        }
        values.update(changes)
        self._sql(
            f"INSERT INTO measurements_historicaltemperaturereading ({','.join(values)},created_at) "
            f"VALUES ({','.join(['%s'] * len(values))},CURRENT_TIMESTAMP)", list(values.values()),
        )

    def test_raw_cho_evidence_accepts_only_the_explicit_canonical_pca_identity(self):
        self._sql("UPDATE cultures_box SET global_code='PCA-JKA-1.005' WHERE id=%s", [self.box.pk])
        from .test_historical_temperature_plan import evidence

        with transaction.atomic():
            reading = HistoricalTemperatureReading.objects.create(
                biological_measurement=self.biology, iso_year=2026, iso_week=1, temperature_c="0",
            )
            HistoricalTemperatureEvidence.objects.create(reading=reading, **evidence(
                source_box="PCA-CHO-1.05", source_value="0",
            ))
        # Bypass the model to exercise the PostgreSQL evidence trigger itself.
        self._insert_evidence(source_box="PCA-CHO-1.005")
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._insert_evidence(source_box="PCA-CHO-1.006", polyp_cell="F15", ephyrae_cell="F16",
                                  temperature_cell="C15", temperature_merged_range="C15:C16")
        self.assertEqual(HistoricalTemperatureEvidence.objects.count(), 2)

    def test_sql_duplicate_cannot_receive_reading_or_retire_an_accepted_measurement(self):
        self.accept()
        duplicate = BiologicalMeasurement.objects.create(
            box=self.box, measured_on=self.biology.measured_on, polyp_count=0, ephyrae_count=0,
            duplicate_of=self.biology,
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._insert_reading(biological_measurement_id=duplicate.pk)
        target = BiologicalMeasurement.objects.create(
            box=self.box, measured_on="2026-01-08", polyp_count=0, ephyrae_count=0,
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._sql("UPDATE measurements_biologicalmeasurement SET duplicate_of_id=%s WHERE id=%s",
                      [target.pk, self.biology.pk])
        self.assertIsNone(BiologicalMeasurement.objects.get(pk=self.biology.pk).duplicate_of_id)
        self.assertEqual(HistoricalTemperatureReading.objects.get().association_status, "confirmed")

    def test_sql_update_delete_and_truncate_cannot_bypass_orm(self):
        self.accept()
        for table in ("measurements_historicaltemperaturereading", "measurements_historicaltemperatureevidence", "measurements_historicaltemperatureimportreceipt"):
            for sql in (f"UPDATE {table} SET id=id", f"DELETE FROM {table}", f"TRUNCATE {table} CASCADE"):
                with self.subTest(sql=sql), self.assertRaises(IntegrityError):
                    with transaction.atomic(), connection.cursor() as cursor:
                        cursor.execute(sql)

    def test_raw_evidence_inserts_preserve_scientific_consistency(self):
        self.accept()
        for column, bad in (("polyp_value", "1"), ("ephyrae_value", "1"), ("source_value", "99"),
                            ("source_iso_week", 2), ("source_iso_year", 2025), ("week_value", "2"),
                            ("source_box", "ABC-DEF-1.002")):
            with self.subTest(column=column), self.assertRaises(IntegrityError):
                with transaction.atomic():
                    self._insert_evidence(**{column: bad})
        self._insert_evidence()
        self.assertEqual(HistoricalTemperatureEvidence.objects.count(), 3)

    def test_sql_biology_corrections_invalidate_without_changing_provenance(self):
        self.accept()
        original = HistoricalTemperatureReading.objects.values().get()
        evidence = list(HistoricalTemperatureEvidence.objects.order_by("pk").values())
        receipt = HistoricalTemperatureImportReceipt.objects.values().get()
        for change in ("polyp_count=1", "ephyrae_count=1", "week_start=week_start+7",
                       "measured_on=measured_on+7", "measured_on=measured_on+7,week_start=week_start+7",
                       "measured_on=measured_on-7,week_start=week_start-7"):
            with self.subTest(change=change), transaction.atomic():
                self._sql(f"UPDATE measurements_biologicalmeasurement SET {change} WHERE id=%s", [self.biology.pk])
                self.assertEqual(HistoricalTemperatureReading.objects.values().get(), {
                    **original, "association_status": "pending_review",
                })
                self.assertEqual(list(HistoricalTemperatureEvidence.objects.order_by("pk").values()), evidence)
                self.assertEqual(HistoricalTemperatureImportReceipt.objects.values().get(), receipt)
                transaction.set_rollback(True)
        # A correction and its invalidation must roll back together.
        self.assertEqual(HistoricalTemperatureReading.objects.values().get(), original)
        self.biology.refresh_from_db()
        self.assertEqual((self.biology.polyp_count, self.biology.ephyrae_count), (0, 0))

    def test_same_week_dates_and_metadata_do_not_invalidate(self):
        self.accept()
        self._sql("UPDATE measurements_biologicalmeasurement SET measured_on=measured_on+1, "
                  "notes='Corrected note', strobila_count=1, salinity_psu=30 WHERE id=%s", [self.biology.pk])
        self._sql("UPDATE measurements_biologicalmeasurement SET measured_on=DATE '2025-12-31', "
                  "polyp_count=polyp_count, ephyrae_count=ephyrae_count, week_start=week_start WHERE id=%s", [self.biology.pk])
        self._sql("UPDATE cultures_box SET local_code='Local metadata', notes='Corrected note' WHERE id=%s", [self.box.pk])
        self.assertEqual(HistoricalTemperatureReading.objects.get().association_status, "confirmed")
        self._insert_evidence()
        self.assertEqual(HistoricalTemperatureEvidence.objects.count(), 3)

    def test_box_identity_corrections_invalidate_all_linked_readings(self):
        self.accept()
        strain = Strain.objects.create(species=self.box.strain.species, code="ABC-XYZ-1")
        second = BiologicalMeasurement.objects.create(box=self.box, measured_on="2026-01-08", polyp_count=0, ephyrae_count=0)
        with transaction.atomic():
            self._insert_reading(biological_measurement_id=second.pk, iso_week=2)
            second_reading = HistoricalTemperatureReading.objects.get(biological_measurement=second)
            self._insert_evidence(reading_id=second_reading.pk, source_iso_week=2, week_value="2")
        for column, value in (("global_code", "ABC-DEF-1.002"), ("strain_id", strain.pk), ("box_number", "002")):
            with self.subTest(column=column), transaction.atomic():
                self._sql(f"UPDATE cultures_box SET {column}=%s WHERE id=%s", [value, self.box.pk])
                self.assertEqual(list(HistoricalTemperatureReading.objects.values_list("association_status", flat=True)),
                                 ["pending_review", "pending_review"])
                with self.assertRaises(IntegrityError), transaction.atomic():
                    self._insert_evidence()
                transaction.set_rollback(True)
        self._sql("UPDATE cultures_box SET global_code='ABC-DEF-1.002' WHERE id=%s", [self.box.pk])
        self._sql("UPDATE cultures_box SET global_code='ABC-DEF-1.001' WHERE id=%s", [self.box.pk])
        self.assertFalse(HistoricalTemperatureReading.objects.exclude(association_status="pending_review").exists())

    def test_institution_and_biological_box_reassignment_remain_blocked_when_pending(self):
        self.accept()
        same = Box.objects.create(organization=self.organization, strain=self.box.strain,
                                  global_code="ABC-DEF-1.002", box_number="002")
        foreign = Box.objects.create(organization=self.other, strain=self.box.strain,
                                     global_code="ABC-DEF-1.003", box_number="003")
        for status in ("confirmed", "pending_review"):
            if status == "pending_review":
                self._sql("UPDATE measurements_biologicalmeasurement SET polyp_count=1 WHERE id=%s", [self.biology.pk])
            for sql, params in (
                ("UPDATE cultures_box SET organization_id=%s WHERE id=%s", [self.other.pk, self.box.pk]),
                ("UPDATE measurements_biologicalmeasurement SET box_id=%s WHERE id=%s", [same.pk, self.biology.pk]),
                ("UPDATE measurements_biologicalmeasurement SET box_id=%s WHERE id=%s", [foreign.pk, self.biology.pk]),
            ):
                with self.subTest(status=status, sql=sql, params=params), self.assertRaises(IntegrityError):
                    with transaction.atomic():
                        self._sql(sql, params)
            self.assertEqual(HistoricalTemperatureReading.objects.get().association_status, status)

    def test_only_status_invalidation_is_allowed_never_payload_changes_or_reapproval(self):
        self.accept()
        original = HistoricalTemperatureReading.objects.values().get()
        alternative = BiologicalMeasurement.objects.create(
            box=self.box, measured_on="2026-01-08", polyp_count=0, ephyrae_count=0,
        )
        for change in ("id=id+1000", f"biological_measurement_id={alternative.pk}", "iso_year=2025",
                       "iso_week=2", "temperature_c='0'", "created_at=created_at+interval '1 second'"):
            with self.subTest(change=change), self.assertRaises(IntegrityError):
                with transaction.atomic():
                    self._sql(f"UPDATE measurements_historicaltemperaturereading "
                              f"SET association_status='pending_review',{change}")
        self._sql("UPDATE measurements_historicaltemperaturereading SET association_status='pending_review'")
        self.assertEqual(HistoricalTemperatureReading.objects.values().get(), {**original, "association_status": "pending_review"})
        for sql in (
            "UPDATE measurements_historicaltemperaturereading SET association_status='confirmed'",
            "UPDATE measurements_historicaltemperaturereading SET association_status='pending_review'",
            "UPDATE measurements_historicaltemperaturereading SET temperature_c='1'",
            "DELETE FROM measurements_historicaltemperaturereading",
        ):
            with self.subTest(sql=sql), self.assertRaises(IntegrityError):
                with transaction.atomic():
                    self._sql(sql)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._insert_evidence()
        # Further biological corrections, including restoring the original values,
        # must succeed without either a no-op historical UPDATE or reapproval.
        self._sql("UPDATE measurements_biologicalmeasurement SET polyp_count=2 WHERE id=%s", [self.biology.pk])
        self._sql("UPDATE measurements_biologicalmeasurement SET polyp_count=0 WHERE id=%s", [self.biology.pk])
        self.assertEqual(HistoricalTemperatureReading.objects.get().association_status, "pending_review")
        self.assertEqual(HistoricalTemperatureEvidence.objects.count(), 2)

    def test_sql_readings_must_start_confirmed_and_match_both_week_representations(self):
        for status in ("pending_review", "unknown"):
            with self.subTest(status=status), self.assertRaises(IntegrityError), transaction.atomic():
                self._insert_reading(association_status=status)
        # Force the insert trigger now, not merely the deferred evidence guard.
        for change in ("measured_on=measured_on+7", "week_start=week_start+7", "week_start=week_start+1"):
            with self.subTest(change=change), transaction.atomic():
                self._sql(f"UPDATE measurements_biologicalmeasurement SET {change} WHERE id=%s", [self.biology.pk])
                with self.assertRaises(IntegrityError), transaction.atomic():
                    self._insert_reading()
                transaction.set_rollback(True)

    def test_evidence_checks_both_week_representations_under_lock(self):
        self.accept()
        for change in ("measured_on=measured_on+7", "week_start=week_start+7", "week_start=week_start+1"):
            with self.subTest(change=change), transaction.atomic():
                # Test-only corruption isolates evidence validation from the
                # biology invalidation trigger; never a supported write workflow.
                self._sql("ALTER TABLE measurements_biologicalmeasurement DISABLE TRIGGER historical_biology_guard")
                self._sql(f"UPDATE measurements_biologicalmeasurement SET {change} WHERE id=%s", [self.biology.pk])
                self._sql("ALTER TABLE measurements_biologicalmeasurement ENABLE TRIGGER historical_biology_guard")
                self.assertEqual(HistoricalTemperatureReading.objects.get().association_status, "confirmed")
                with self.assertRaises(IntegrityError), transaction.atomic():
                    self._insert_evidence()
                transaction.set_rollback(True)

    def test_orphan_reading_is_rejected_at_commit_and_rolled_back(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                HistoricalTemperatureReading.objects.create(biological_measurement=self.biology, iso_year=2026, iso_week=1, temperature_c="0")
        self.assertFalse(HistoricalTemperatureReading.objects.exists())

    def test_competing_imports_commit_once_and_replay_once(self):
        gate, output = Barrier(2), Queue()
        snapshot, partial = self.snapshot, self.partial
        organization, actor = self.organization, self.actor

        def worker():
            close_old_connections()
            try:
                gate.wait(timeout=10)
                with transaction.atomic():
                    with connection.cursor() as cursor:
                        cursor.execute("SET LOCAL lock_timeout = '10s'")
                        cursor.execute(_LOCK_SQL)
                    receipt, replay = _accept_locked(snapshot, partial, b"synthetic-original-workbooks", organization, actor)
                output.put((receipt.pk, replay))
            except Exception as exc:
                output.put(exc)
            finally:
                connection.close()

        threads = [Thread(target=worker, daemon=True) for _ in range(2)]
        for thread in threads:
            thread.start()
        from config.historical_temperature_pgqa import join_workers

        join_workers(threads, timeout=20)
        results = [output.get_nowait() for _ in threads]
        for result in results:
            if isinstance(result, Exception):
                raise result
        self.assertEqual(results[0][0], results[1][0])
        self.assertEqual(sorted(row[1] for row in results), [False, True])
        self.assertEqual(HistoricalTemperatureReading.objects.count(), 1)
        self.assertEqual(HistoricalTemperatureEvidence.objects.count(), 2)

    def test_old_snapshot_isolation_cannot_hide_new_relationships(self):
        strain = Strain.objects.create(species=self.box.strain.species, code="ABC-XYZ-1")
        alternative = Box.objects.create(organization=self.organization, strain=self.box.strain,
                                         global_code="ABC-DEF-1.002", box_number="002")
        # The extra Box is fixture state, not drift: re-baseline before the strict stale check.
        self.snapshot = {**_current_snapshot(self.organization.pk), "historical_temperatures": []}
        for isolation in ("REPEATABLE READ", "SERIALIZABLE"):
            for sql in (
                f"UPDATE measurements_biologicalmeasurement SET polyp_count=1 WHERE id={self.biology.pk}",
                f"UPDATE measurements_biologicalmeasurement SET ephyrae_count=1 WHERE id={self.biology.pk}",
                f"UPDATE measurements_biologicalmeasurement SET week_start=week_start+7 WHERE id={self.biology.pk}",
                f"UPDATE measurements_biologicalmeasurement SET measured_on=measured_on+7 WHERE id={self.biology.pk}",
                f"UPDATE measurements_biologicalmeasurement SET box_id={alternative.pk} WHERE id={self.biology.pk}",
                f"UPDATE cultures_box SET global_code='ABC-DEF-1.003' WHERE id={self.box.pk}",
                f"UPDATE cultures_box SET strain_id={strain.pk} WHERE id={self.box.pk}",
                f"UPDATE cultures_box SET box_number='002' WHERE id={self.box.pk}",
                f"UPDATE cultures_box SET organization_id={self.other.pk} WHERE id={self.box.pk}",
            ):
                with self.subTest(isolation=isolation, sql=sql), self.assertRaisesMessage(IntegrityError, GUARD_MESSAGE):
                    with transaction.atomic():
                        self._sql(f"SET TRANSACTION ISOLATION LEVEL {isolation}")
                        self._sql(sql)
            with self.subTest(isolation=isolation), self.assertRaisesMessage(IntegrityError, GUARD_MESSAGE), transaction.atomic():
                self._sql(f"SET TRANSACTION ISOLATION LEVEL {isolation}")
                self._insert_reading()
        self.accept()
        for isolation in ("REPEATABLE READ", "SERIALIZABLE"):
            with self.subTest(evidence_isolation=isolation), self.assertRaisesMessage(IntegrityError, GUARD_MESSAGE), transaction.atomic():
                self._sql(f"SET TRANSACTION ISOLATION LEVEL {isolation}")
                self._insert_evidence()

    def _ordered_transactions(self, first_action, second_action):
        """Hold the first transaction until pg_blocking_pids proves the other waits.

        No timing-based ordering assumptions: events establish the first writer,
        and PostgreSQL confirms actual contention before that writer can commit.
        """
        ready, release = Event(), Event()
        first_pid, second_pid, output = Queue(), Queue(), Queue()

        def worker(name, action, pid_queue, hold):
            close_old_connections()
            try:
                with transaction.atomic():
                    with connection.cursor() as cursor:
                        cursor.execute("SET LOCAL lock_timeout = '10s'")
                        cursor.execute("SET LOCAL statement_timeout = '15s'")
                        cursor.execute("SELECT pg_backend_pid()")
                        pid_queue.put(cursor.fetchone()[0])
                    result = action()
                    if hold:
                        ready.set()
                        if not release.wait(timeout=15):
                            raise TimeoutError("First transaction was not released")
                output.put((name, result))
            except Exception as exc:
                output.put((name, exc))
            finally:
                if hold:
                    ready.set()
                connection.close()

        first = Thread(target=worker, args=("first", first_action, first_pid, True), daemon=True)
        second = Thread(target=worker, args=("second", second_action, second_pid, False), daemon=True)
        first.start()
        try:
            self.assertTrue(ready.wait(timeout=10), "First transaction never became ready")
            blocker = first_pid.get(timeout=5)
            second.start()
            waiting = second_pid.get(timeout=5)
            deadline = monotonic() + 5
            while monotonic() < deadline:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT %s = ANY(pg_blocking_pids(%s))", [blocker, waiting])
                    if cursor.fetchone()[0]:
                        break
                sleep(0.01)
            else:
                self.fail("Second transaction never blocked on the first transaction")
        finally:
            release.set()
            from config.historical_temperature_pgqa import join_workers

            join_workers([first, second], timeout=20)
        return dict(output.get_nowait() for _ in range(2))

    def _import_with_table_locks(self):
        self._sql(_LOCK_SQL)
        receipt, replay = _accept_locked(self.snapshot, self.partial, b"synthetic-original-workbooks", self.organization, self.actor)
        return receipt.pk, replay

    def _assert_correction_import_order(self, sql, *, import_first):
        def correction():
            self._sql(sql)
            return "corrected"

        if import_first:
            results = self._ordered_transactions(self._import_with_table_locks, correction)
            for result in results.values():
                if isinstance(result, Exception):
                    raise result
            self.assertFalse(results["first"][1])
            self.assertEqual(results["second"], "corrected")
            self.assertEqual(HistoricalTemperatureReading.objects.get().association_status, "pending_review")
            self.assertEqual(HistoricalTemperatureEvidence.objects.count(), 2)
        else:
            results = self._ordered_transactions(correction, self._import_with_table_locks)
            if isinstance(results["first"], Exception):
                raise results["first"]
            self.assertIsInstance(results["second"], PartialImportError)
            self.assertIn("stale_baseline", str(results["second"]))
            self.assertFalse(HistoricalTemperatureReading.objects.exists())
            self.assertFalse(HistoricalTemperatureEvidence.objects.exists())

    def test_table_locked_import_then_count_correction_commits_pending(self):
        self._assert_correction_import_order(
            f"UPDATE measurements_biologicalmeasurement SET polyp_count=1 WHERE id={self.biology.pk}", import_first=True,
        )

    def test_count_correction_then_table_locked_import_rejects_stale_baseline(self):
        self._assert_correction_import_order(
            f"UPDATE measurements_biologicalmeasurement SET polyp_count=1 WHERE id={self.biology.pk}", import_first=False,
        )

    def test_table_locked_import_then_week_correction_commits_pending(self):
        self._assert_correction_import_order(
            "UPDATE measurements_biologicalmeasurement SET measured_on=measured_on+7,week_start=week_start+7 "
            f"WHERE id={self.biology.pk}", import_first=True,
        )

    def test_week_correction_then_table_locked_import_rejects_stale_baseline(self):
        self._assert_correction_import_order(
            "UPDATE measurements_biologicalmeasurement SET measured_on=measured_on+7,week_start=week_start+7 "
            f"WHERE id={self.biology.pk}", import_first=False,
        )

    def test_table_locked_import_then_identity_correction_commits_pending(self):
        self._assert_correction_import_order(
            f"UPDATE cultures_box SET global_code='ABC-DEF-1.002' WHERE id={self.box.pk}", import_first=True,
        )

    def test_identity_correction_then_table_locked_import_rejects_stale_baseline(self):
        self._assert_correction_import_order(
            f"UPDATE cultures_box SET global_code='ABC-DEF-1.002' WHERE id={self.box.pk}", import_first=False,
        )

    def test_table_locked_import_then_institution_reassignment_is_rejected(self):
        results = self._ordered_transactions(self._import_with_table_locks, lambda: self._sql(
            f"UPDATE cultures_box SET organization_id={self.other.pk} WHERE id={self.box.pk}",
        ))
        if isinstance(results["first"], Exception):
            raise results["first"]
        self.assertIsInstance(results["second"], IntegrityError)
        self.box.refresh_from_db()
        self.assertEqual(self.box.organization_id, self.organization.pk)
        self.assertEqual(HistoricalTemperatureReading.objects.get().association_status, "confirmed")

    def test_institution_reassignment_then_table_locked_import_rejects_stale_baseline(self):
        results = self._ordered_transactions(lambda: self._sql(
            f"UPDATE cultures_box SET organization_id={self.other.pk} WHERE id={self.box.pk}",
        ), self._import_with_table_locks)
        if isinstance(results["first"], Exception):
            raise results["first"]
        self.assertIsInstance(results["second"], PartialImportError)
        self.assertIn("stale_baseline", str(results["second"]))
        self.assertFalse(HistoricalTemperatureReading.objects.exists())

    def test_direct_evidence_insert_then_count_correction_commits_pending(self):
        self.accept()
        results = self._ordered_transactions(self._insert_evidence, lambda: self._sql(
            f"UPDATE measurements_biologicalmeasurement SET polyp_count=1 WHERE id={self.biology.pk}",
        ))
        for result in results.values():
            if isinstance(result, Exception):
                raise result
        self.assertEqual(HistoricalTemperatureEvidence.objects.count(), 3)
        self.assertEqual(HistoricalTemperatureReading.objects.get().association_status, "pending_review")

    def test_direct_count_correction_then_evidence_insert_refuses_pending(self):
        self.accept()
        results = self._ordered_transactions(lambda: self._sql(
            f"UPDATE measurements_biologicalmeasurement SET polyp_count=1 WHERE id={self.biology.pk}",
        ), self._insert_evidence)
        if isinstance(results["first"], Exception):
            raise results["first"]
        self.assertIsInstance(results["second"], IntegrityError)
        self.assertEqual(HistoricalTemperatureEvidence.objects.count(), 2)
        self.assertEqual(HistoricalTemperatureReading.objects.get().association_status, "pending_review")

    def test_direct_evidence_insert_then_identity_correction_commits_pending(self):
        self.accept()
        results = self._ordered_transactions(self._insert_evidence, lambda: self._sql(
            f"UPDATE cultures_box SET global_code='ABC-DEF-1.002' WHERE id={self.box.pk}",
        ))
        for result in results.values():
            if isinstance(result, Exception):
                raise result
        self.assertEqual(HistoricalTemperatureEvidence.objects.count(), 3)
        self.assertEqual(HistoricalTemperatureReading.objects.get().association_status, "pending_review")

    def test_direct_identity_correction_then_evidence_insert_refuses_pending(self):
        self.accept()
        results = self._ordered_transactions(lambda: self._sql(
            f"UPDATE cultures_box SET global_code='ABC-DEF-1.002' WHERE id={self.box.pk}",
        ), self._insert_evidence)
        if isinstance(results["first"], Exception):
            raise results["first"]
        self.assertIsInstance(results["second"], IntegrityError)
        self.assertEqual(HistoricalTemperatureEvidence.objects.count(), 2)
        self.assertEqual(HistoricalTemperatureReading.objects.get().association_status, "pending_review")

    def test_failure_rolls_back_real_postgres_transaction(self):
        apply_tests.AcceptanceTests.test_audit_failure_rolls_back_all_readings_evidences_and_receipt(self)

    def test_empty_guard_migration_round_trip_and_nonempty_rollback_rejection(self):
        # This does not apply/reverse 0011; it exercises only guard DDL on empty storage.
        with connection.schema_editor() as editor:
            _RECEIPT.remove_guards(None, editor)
            _GUARDS.backwards(None, editor)
            _GUARDS.forwards(None, editor)
            _RECEIPT.guards(None, editor)
        self.accept()
        with self.assertRaises(RuntimeError), connection.schema_editor() as editor:
            _GUARDS.backwards(None, editor)
        self._sql("UPDATE measurements_historicaltemperaturereading SET association_status='pending_review'")
        with self.assertRaises(RuntimeError), connection.schema_editor() as editor:
            _GUARDS.backwards(None, editor)
