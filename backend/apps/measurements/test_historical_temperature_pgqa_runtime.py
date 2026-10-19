"""Opt-in proofs on the disposable PG18 database, using the real runtime login.

This module does not provision roles or relax the public importer. Owner-only
fixtures and cleanup belong to config.historical_temperature_pgqa. Importing the
module opens no connection; the harness is imported only by enabled test cases.
"""

import os
from contextlib import contextmanager
from functools import wraps
from importlib import import_module
from queue import Queue
from threading import Barrier, Event, Thread
from time import monotonic, sleep
from unittest import skipUnless
from unittest.mock import patch

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.cultures.models import Box
from django.conf import settings
from django.db import DatabaseError, connection, connections, transaction
from django.test import TransactionTestCase

from . import test_historical_temperature_apply as apply_tests
from . import test_historical_temperature_corrections as correction_tests
from . import test_historical_temperature_postgres as postgres_tests
from .historical_temperature_apply import _accept_locked, _validate_target
from .historical_temperature_partial import PartialImportError
from .models import (
    BiologicalMeasurement,
    HistoricalTemperatureEvidence,
    HistoricalTemperatureImportReceipt,
    HistoricalTemperatureReading,
)


_ENABLED = (
    getattr(settings, "HISTORICAL_TEMPERATURE_PGQA", False)
    and os.getenv("POLYPBASE_TEST_POSTGRES") == "1"
    and os.getenv("POLYPBASE_HISTORICAL_POSTGRES_QA") == "1"
)
_REASON = "Requires the explicitly authorized disposable PG18 runtime-role harness"
_OWNER = os.getenv("POLYPBASE_QA_OWNER")
_RUNTIME = os.getenv("POLYPBASE_QA_RUNTIME")
_LOCK_SQL = postgres_tests._LOCK_SQL
_TRIGGER_TABLES = {
    "historical_box_guard": "cultures_box",
    "historical_biology_guard": "measurements_biologicalmeasurement",
    "historical_reading_valid": "measurements_historicaltemperaturereading",
    "historical_reading_immutable": "measurements_historicaltemperaturereading",
    "historical_reading_nonempty": "measurements_historicaltemperaturereading",
    "historical_reading_no_truncate": "measurements_historicaltemperaturereading",
    "historical_evidence_valid": "measurements_historicaltemperatureevidence",
    "historical_evidence_immutable": "measurements_historicaltemperatureevidence",
    "historical_evidence_no_truncate": "measurements_historicaltemperatureevidence",
    "historical_receipt_immutable": "measurements_historicaltemperatureimportreceipt",
    "historical_receipt_no_truncate": "measurements_historicaltemperatureimportreceipt",
}
_FUNCTIONS = {
    "historical_temperature_immutable", "historical_temperature_reading_immutable",
    "historical_temperature_decimal", "historical_temperature_reading_valid",
    "historical_temperature_evidence_valid", "historical_temperature_nonempty",
    "historical_temperature_biology_guard", "historical_temperature_box_guard",
}


def _harness():
    return import_module("config.historical_temperature_pgqa")


def _sqlstate(error):
    cause = error.__cause__ or error
    return getattr(cause, "sqlstate", None) or getattr(cause, "pgcode", None)


def _runtime_test(method):
    @wraps(method)
    def run(self):
        with self.runtime_session():
            method(self)
    return run


class _RuntimePGTestCase(TransactionTestCase):
    """No inherited test methods: only explicitly selected proofs are collected."""

    _sql = postgres_tests.PostgreSQLHistoricalTemperatureTests._sql
    _insert_evidence = postgres_tests.PostgreSQLHistoricalTemperatureTests._insert_evidence
    _insert_reading = postgres_tests.PostgreSQLHistoricalTemperatureTests._insert_reading
    _set_up_fixture = apply_tests.AcceptanceTests.setUp

    def setUp(self):
        _harness().assert_disposable(connection)
        with connection.cursor() as cursor:
            cursor.execute("SELECT session_user, current_user")
            self.assertEqual(cursor.fetchone(), (_OWNER, _OWNER))
        self._set_up_fixture()

    def _fixture_teardown(self):
        # TransactionTestCase's ordinary TRUNCATE is intentionally forbidden.
        _harness().cleanup_test_data()

    def assert_runtime_login(self):
        _harness().assert_disposable(connection)
        with connection.cursor() as cursor:
            cursor.execute("SELECT session_user, current_user")
            self.assertEqual(cursor.fetchone(), (_RUNTIME, _RUNTIME))

    @contextmanager
    def runtime_session(self):
        if connection.settings_dict.get("USER") == _RUNTIME:
            self.assert_runtime_login()
            yield
            return
        with _harness().runtime_role():
            self._sql("SET lock_timeout = '5s'")
            self._sql("SET statement_timeout = '15s'")
            self.assert_runtime_login()
            yield

    def bounded_transaction(self, *, lock_timeout="5s"):
        # Values are test constants, passed as parameters rather than SQL text.
        with connection.cursor() as cursor:
            cursor.execute("SELECT set_config('lock_timeout', %s, true)", [lock_timeout])
            cursor.execute("SET LOCAL statement_timeout = '15s'")

    def _accept_in_transaction(self, *, organization=None):
        self._sql(_LOCK_SQL)
        return _accept_locked(
            self.snapshot, self.partial, b"synthetic-original-workbooks",
            self.organization if organization is None else organization, self.actor,
        )

    def accept(self, partial=None, organization=None):
        with transaction.atomic():
            self.bounded_transaction()
            self._sql(_LOCK_SQL)
            result = _accept_locked(
                self.snapshot, self.partial if partial is None else partial,
                b"synthetic-original-workbooks",
                self.organization if organization is None else organization, self.actor,
            )
            self._sql("SET CONSTRAINTS ALL IMMEDIATE")
            return result

    def assert_aborted(self):
        # ORM failures can set needs_rollback. Probe the actual driver as well,
        # proving PostgreSQL's 25P02 rather than only Django's local protection.
        with self.assertRaises(connection.Database.Error) as caught, connection.connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        self.assertEqual(_sqlstate(caught.exception), "25P02")
        transaction.set_rollback(True)

    def run_workers(self, *actions):
        """Copy the active runtime configuration, never open an owner worker.

        Django connection registries are thread-local. Each wrapper is constructed
        inside its own thread; the harness changes/restores the main login once,
        outside the workers, so role restoration cannot race another worker.
        """
        output = Queue()
        with self.runtime_session():
            template = connections["default"]

            def worker(index, action):
                db = None
                try:
                    db = template.copy(alias="default")
                    connections["default"] = db
                    self._sql("SET lock_timeout = '5s'")
                    self._sql("SET statement_timeout = '15s'")
                    self.assert_runtime_login()
                    output.put((index, action()))
                except Exception as error:
                    output.put((index, error))
                finally:
                    if db is not None:
                        db.close()

            threads = [
                Thread(target=worker, args=(index, action), daemon=True)
                for index, action in enumerate(actions)
            ]
            try:
                for thread in threads:
                    thread.start()
            finally:
                _harness().join_workers(threads, timeout=25)
        results = dict(output.get(timeout=1) for _ in actions)
        for result in results.values():
            if isinstance(result, Exception):
                raise result
        return [results[index] for index in range(len(actions))]


@skipUnless(_ENABLED, _REASON)
class HistoricalTemperatureRuntimePrivilegesTests(_RuntimePGTestCase):
    @_runtime_test
    def test_runtime_login_attributes_schema_and_owner_membership(self):
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole,
                       rolreplication, rolbypassrls
                FROM pg_roles WHERE rolname = current_user
            """)
            self.assertEqual(cursor.fetchone(), (True, False, False, False, False, False))
            cursor.execute("""
                SELECT current_database(), current_schema(),
                       current_setting('server_version_num')::integer,
                       current_setting('session_replication_role'),
                       current_setting('transaction_isolation')
            """)
            database, schema, version, replication, isolation = cursor.fetchone()
            self.assertEqual(database, os.environ["POLYPBASE_QA_DATABASE"])
            self.assertEqual(schema, "public")
            self.assertEqual(version // 10000, 18)
            self.assertEqual((replication, isolation), ("origin", "read committed"))
            cursor.execute("""
                SELECT pg_has_role(current_user, %s, 'MEMBER'),
                       pg_has_role(current_user, %s, 'USAGE'),
                       pg_has_role(current_user, %s, 'SET'),
                       has_schema_privilege(current_user, 'public', 'CREATE'),
                       has_schema_privilege(current_user, 'public', 'USAGE'),
                       has_database_privilege(current_user, current_database(), 'CREATE')
            """, [_OWNER, _OWNER, _OWNER])
            self.assertEqual(cursor.fetchone(), (False, False, False, False, True, False))
            cursor.execute("""
                SELECT pg_get_userbyid(datdba) FROM pg_database
                WHERE datname = current_database()
            """)
            self.assertEqual(cursor.fetchone()[0], _OWNER)
            cursor.execute("""
                SELECT pg_get_userbyid(nspowner), pg_has_role(current_user, nspowner, 'MEMBER')
                FROM pg_namespace WHERE nspname = 'public'
            """)
            schema_owner, member = cursor.fetchone()
            self.assertIn(schema_owner, {_OWNER, "pg_database_owner"})
            self.assertFalse(member)
            cursor.execute("""
                SELECT rolname FROM pg_roles
                WHERE (rolsuper OR rolcreaterole OR rolreplication OR rolbypassrls)
                  AND pg_has_role(current_user, oid, 'MEMBER')
            """)
            self.assertEqual(cursor.fetchall(), [])
            cursor.execute("""
                SELECT c.relname, pg_get_userbyid(c.relowner),
                       pg_has_role(current_user, c.relowner, 'MEMBER')
                FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public' AND c.relname = ANY(%s)
            """, [sorted(set(_TRIGGER_TABLES.values()))])
            rows = cursor.fetchall()
            self.assertEqual({row[0] for row in rows}, set(_TRIGGER_TABLES.values()))
            for table, owner, member in rows:
                self.assertEqual((owner, member), (_OWNER, False), table)

    def assert_permission_denied(self, sql):
        with self.assertRaises(DatabaseError) as caught, transaction.atomic():
            self.bounded_transaction()
            self._sql(sql)
            self.fail("Restricted runtime unexpectedly executed schema/session mutation")
        self.assertEqual(_sqlstate(caught.exception), "42501", sql)

    @_runtime_test
    def test_runtime_cannot_disable_drop_or_replace_scientific_guards(self):
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT t.tgname, c.relname, t.tgenabled
                FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE NOT t.tgisinternal AND n.nspname = 'public'
                  AND t.tgname = ANY(%s)
            """, [sorted(_TRIGGER_TABLES)])
            self.assertEqual({name: (table, enabled) for name, table, enabled in cursor.fetchall()},
                             {name: (table, "O") for name, table in _TRIGGER_TABLES.items()})
            cursor.execute("""
                SELECT p.proname, pg_get_function_identity_arguments(p.oid),
                       pg_get_functiondef(p.oid), pg_get_userbyid(p.proowner),
                       pg_has_role(current_user, p.proowner, 'MEMBER')
                FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
                WHERE n.nspname = 'public' AND p.proname = ANY(%s)
            """, [sorted(_FUNCTIONS)])
            functions = cursor.fetchall()
        self.assertEqual({row[0] for row in functions}, _FUNCTIONS)
        self.assertEqual(len(functions), len(_FUNCTIONS))
        quote = connection.ops.quote_name
        for trigger, table in _TRIGGER_TABLES.items():
            for sql in (
                f"ALTER TABLE public.{quote(table)} DISABLE TRIGGER {quote(trigger)}",
                f"DROP TRIGGER {quote(trigger)} ON public.{quote(table)}",
            ):
                with self.subTest(sql=sql):
                    self.assert_permission_denied(sql)
        for name, arguments, definition, owner, member in functions:
            self.assertEqual((owner, member), (_OWNER, False), name)
            for sql in (definition, f"DROP FUNCTION public.{quote(name)}({arguments}) CASCADE"):
                with self.subTest(function=name, operation=sql.split()[0]):
                    self.assert_permission_denied(sql)
        for sql in (
            "SET LOCAL session_replication_role = 'replica'",
            f"SET LOCAL ROLE {quote(_OWNER)}",
            f"SET LOCAL SESSION AUTHORIZATION {quote(_OWNER)}",
        ):
            with self.subTest(sql=sql):
                self.assert_permission_denied(sql)
        self.assert_runtime_login()
        self._sql("SELECT 1")


@skipUnless(_ENABLED, _REASON)
class HistoricalTemperatureRuntimeAcceptanceTests(_RuntimePGTestCase):
    test_sql_update_delete_and_truncate_cannot_bypass_orm = _runtime_test(
        postgres_tests.PostgreSQLHistoricalTemperatureTests.test_sql_update_delete_and_truncate_cannot_bypass_orm)
    test_raw_evidence_inserts_preserve_scientific_consistency = _runtime_test(
        postgres_tests.PostgreSQLHistoricalTemperatureTests.test_raw_evidence_inserts_preserve_scientific_consistency)
    test_orphan_reading_is_rejected_at_commit_and_rolled_back = _runtime_test(
        postgres_tests.PostgreSQLHistoricalTemperatureTests.test_orphan_reading_is_rejected_at_commit_and_rolled_back)

    @_runtime_test
    def test_real_locked_acceptance_preserves_zero_evidence_receipt_and_replay(self):
        before = BiologicalMeasurement.objects.values().get(pk=self.biology.pk)
        receipt, replay = self.accept()
        self.assertFalse(replay)
        self.assertEqual(HistoricalTemperatureReading.objects.get().temperature_c, "0.000")
        self.assertCountEqual(list(HistoricalTemperatureEvidence.objects.values_list("source_value", flat=True)),
                              ["0", "0.000"])
        self.assertEqual(HistoricalTemperatureImportReceipt.objects.count(), 1)
        self.assertEqual(receipt.organization_id, self.organization.pk)
        self.assertEqual(receipt.actor_id, self.actor.pk)
        self.assertEqual(receipt.accepted_plan, self.partial)
        self.assertEqual(bytes(receipt.source_archive), b"synthetic-original-workbooks")
        self.assertEqual(receipt.results, [{
            "biological_measurement_id": self.biology.pk,
            "reading_id": HistoricalTemperatureReading.objects.get().pk,
        }])
        again, replay = self.accept()
        self.assertTrue(replay)
        self.assertEqual(again.pk, receipt.pk)
        self.assertEqual(HistoricalTemperatureReading.objects.count(), 1)
        self.assertEqual(HistoricalTemperatureEvidence.objects.count(), 2)
        self.assertEqual(AuditLog.objects.filter(object_type="historical_temperature_import").count(), 1)
        self.assertEqual(BiologicalMeasurement.objects.values().get(pk=self.biology.pk), before)
        self.assertFalse(getattr(settings, "HISTORICAL_TEMPERATURE_LOCAL_UI_APPLY_ENABLED", False))
        with self.assertRaisesMessage(PartialImportError, "unsafe_or_disabled_target"):
            _validate_target()

    @_runtime_test
    def test_foreign_organization_cannot_resolve_acceptance_ids(self):
        before = list(BiologicalMeasurement.objects.values())
        with self.assertRaises(BiologicalMeasurement.DoesNotExist):
            self.accept(organization=self.other)
        self.assertEqual(list(BiologicalMeasurement.objects.values()), before)
        self.assertFalse(HistoricalTemperatureReading.objects.exists())
        self.assertFalse(HistoricalTemperatureEvidence.objects.exists())
        self.assertFalse(HistoricalTemperatureImportReceipt.objects.exists())
        self.assertFalse(AuditLog.objects.exists())

    @_runtime_test
    def test_import_audit_failure_rolls_back_all_staged_history_and_clean_retry(self):
        create = AuditLog.objects.create

        def fail_after_audit(**kwargs):
            create(**kwargs)
            self.assertEqual(HistoricalTemperatureReading.objects.count(), 1)
            self.assertEqual(HistoricalTemperatureEvidence.objects.count(), 2)
            self.assertEqual(HistoricalTemperatureImportReceipt.objects.count(), 1)
            raise RuntimeError("Synthetic runtime import audit failure")

        with (
            patch.object(AuditLog.objects, "create", side_effect=fail_after_audit),
            self.assertRaisesMessage(RuntimeError, "Synthetic runtime import audit failure"),
        ):
            self.accept()
        for model in (HistoricalTemperatureReading, HistoricalTemperatureEvidence,
                      HistoricalTemperatureImportReceipt, AuditLog):
            self.assertFalse(model.objects.exists())
        self.assertFalse(self.accept()[1])
        self.assertTrue(self.accept()[1])


class _CorrectionFixture:
    _set_up_fixture = correction_tests.HistoricalTemperatureCorrectionTests.setUp
    _set_up_acceptance = apply_tests.AcceptanceTests.setUp
    history = correction_tests.HistoricalTemperatureCorrectionTests.history
    state = correction_tests.HistoricalTemperatureCorrectionTests.state
    correct = correction_tests.HistoricalTemperatureCorrectionTests.correct
    assert_history_preserved = correction_tests.HistoricalTemperatureCorrectionTests.assert_history_preserved
    assert_correction_audit = correction_tests.HistoricalTemperatureCorrectionTests.assert_correction_audit
    projection_values = correction_tests.HistoricalTemperatureCorrectionTests.projection_values
    def foreign_biology(self):
        # Unlike TestCase, TransactionTestCase has no enclosing transaction.
        with transaction.atomic():
            return correction_tests.HistoricalTemperatureCorrectionTests.foreign_biology(self)


@skipUnless(_ENABLED, _REASON)
class HistoricalTemperatureRuntimeCorrectionTests(_CorrectionFixture, _RuntimePGTestCase):
    # Bind methods, not the SQLite TestCase: no decorator bypass or duplicate suite.
    test_count_changes_mark_pending_and_preserve_complete_source_payloads = _runtime_test(
        correction_tests.HistoricalTemperatureCorrectionTests.test_count_changes_mark_pending_and_preserve_complete_source_payloads)
    test_nonzero_source_counts_corrected_to_real_zero_mark_pending = _runtime_test(
        correction_tests.HistoricalTemperatureCorrectionTests.test_nonzero_source_counts_corrected_to_real_zero_mark_pending)
    test_noop_and_explicit_unchanged_zero_counts_stay_confirmed = _runtime_test(
        correction_tests.HistoricalTemperatureCorrectionTests.test_noop_and_explicit_unchanged_zero_counts_stay_confirmed)
    test_irrelevant_changes_and_dates_in_same_iso_week_stay_confirmed = _runtime_test(
        correction_tests.HistoricalTemperatureCorrectionTests.test_irrelevant_changes_and_dates_in_same_iso_week_stay_confirmed)
    test_cross_week_correction_keeps_source_week_and_never_relinks = _runtime_test(
        correction_tests.HistoricalTemperatureCorrectionTests.test_cross_week_correction_keeps_source_week_and_never_relinks)
    test_audit_failure_rolls_back_saved_correction_pending_status_and_audit = _runtime_test(
        correction_tests.HistoricalTemperatureCorrectionTests.test_audit_failure_rolls_back_saved_correction_pending_status_and_audit)
    test_foreign_scope_and_box_identifiers_cannot_broaden_correction = _runtime_test(
        correction_tests.HistoricalTemperatureCorrectionTests.test_foreign_scope_and_box_identifiers_cannot_broaden_correction)
    test_denied_roles_inactive_membership_and_foreign_admin_cannot_correct = _runtime_test(
        correction_tests.HistoricalTemperatureCorrectionTests.test_denied_roles_inactive_membership_and_foreign_admin_cannot_correct)
    test_pending_projection_stays_excluded_after_counts_reset_and_later_notes = _runtime_test(
        correction_tests.HistoricalTemperatureCorrectionTests.test_pending_projection_stays_excluded_after_counts_reset_and_later_notes)
    test_return_to_original_date_does_not_reconfirm_association = _runtime_test(
        correction_tests.HistoricalTemperatureCorrectionTests.test_return_to_original_date_does_not_reconfirm_association)
    test_pending_importer_replay_is_stale_then_requires_review_after_numbers_reset = _runtime_test(
        correction_tests.HistoricalTemperatureCorrectionTests.test_pending_importer_replay_is_stale_then_requires_review_after_numbers_reset)
    test_pending_reading_evidence_and_receipt_remain_orm_immutable = _runtime_test(
        correction_tests.HistoricalTemperatureCorrectionTests.test_pending_reading_evidence_and_receipt_remain_orm_immutable)

    @_runtime_test
    def test_sql_cannot_reapprove_pending_after_counts_are_restored(self):
        self.correct({"polyp_count": 7})
        self.correct({"polyp_count": 0})
        before = self.state()
        with self.assertRaises(DatabaseError) as caught, transaction.atomic():
            self.bounded_transaction()
            self._sql("UPDATE measurements_historicaltemperaturereading "
                      "SET association_status = 'confirmed' WHERE id = %s", [self.reading.pk])
        self.assertEqual(_sqlstate(caught.exception), "23514")
        self.assertEqual(self.state(), before)
        self.assert_history_preserved("pending_review")


@skipUnless(_ENABLED, _REASON)
class HistoricalTemperatureRuntimeFailureTests(_RuntimePGTestCase):
    def test_audit_lock_timeout_aborts_entire_import_then_clean_retry(self):
        ready, release = Event(), Event()
        original = BiologicalMeasurement.objects.values().get(pk=self.biology.pk)

        def holder():
            try:
                with transaction.atomic():
                    self.bounded_transaction()
                    table = connection.ops.quote_name(AuditLog._meta.db_table)
                    self._sql(f"LOCK TABLE {table} IN ACCESS EXCLUSIVE MODE")
                    ready.set()
                    if not release.wait(timeout=10):
                        raise TimeoutError("Audit lock holder was not released")
            finally:
                ready.set()
            return "released"

        def importer():
            try:
                self.assertTrue(ready.wait(timeout=5), "Audit lock holder never became ready")
                with transaction.atomic():
                    self.bounded_transaction(lock_timeout="300ms")
                    create = AuditLog.objects.create

                    def blocked_audit(**kwargs):
                        self.assertEqual(HistoricalTemperatureReading.objects.count(), 1)
                        self.assertEqual(HistoricalTemperatureEvidence.objects.count(), 2)
                        self.assertEqual(HistoricalTemperatureImportReceipt.objects.count(), 1)
                        return create(**kwargs)

                    with (
                        patch.object(AuditLog.objects, "create", side_effect=blocked_audit),
                        self.assertRaises(DatabaseError) as caught,
                    ):
                        self._accept_in_transaction()
                    self.assertEqual(_sqlstate(caught.exception), "55P03")
                    self.assert_aborted()
                return "55P03"
            finally:
                release.set()

        self.assertEqual(self.run_workers(holder, importer), ["released", "55P03"])
        for model in (HistoricalTemperatureReading, HistoricalTemperatureEvidence,
                      HistoricalTemperatureImportReceipt, AuditLog):
            self.assertFalse(model.objects.exists())
        self.assertEqual(BiologicalMeasurement.objects.values().get(pk=self.biology.pk), original)
        with self.runtime_session():
            self.assertFalse(self.accept()[1])
            self.assertTrue(self.accept()[1])

    def test_duplicate_evidence_race_has_one_unique_violation_and_clean_retry(self):
        self.accept()
        ready, release = Event(), Event()
        first_pid, second_pid = Queue(), Queue()

        def first_insert():
            try:
                with transaction.atomic():
                    self.bounded_transaction(lock_timeout="10s")
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT pg_backend_pid()")
                        first_pid.put(cursor.fetchone()[0])
                    self._insert_evidence()
                    ready.set()
                    if not release.wait(timeout=10):
                        raise TimeoutError("Evidence insert was not released")
            finally:
                ready.set()
            return "inserted"

        def duplicate_insert():
            self.assertTrue(ready.wait(timeout=5), "First evidence was not staged")
            with transaction.atomic():
                self.bounded_transaction(lock_timeout="10s")
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    second_pid.put(cursor.fetchone()[0])
                try:
                    self._insert_evidence()
                except DatabaseError as error:
                    self.assertEqual(_sqlstate(error), "23505")
                    self.assert_aborted()
                    return "23505"
                self.fail("Duplicate evidence unexpectedly committed")

        def observe_contention():
            try:
                blocker = first_pid.get(timeout=5)
                waiting = second_pid.get(timeout=5)
                deadline = monotonic() + 5
                while monotonic() < deadline:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT %s = ANY(pg_blocking_pids(%s))", [blocker, waiting])
                        if cursor.fetchone()[0]:
                            return "blocked"
                    sleep(0.01)
                self.fail("Duplicate insert never waited on the uncommitted unique key")
            finally:
                release.set()

        self.assertEqual(self.run_workers(first_insert, duplicate_insert, observe_contention),
                         ["inserted", "23505", "blocked"])
        self.assertEqual(HistoricalTemperatureEvidence.objects.count(), 3)
        self.assertEqual(HistoricalTemperatureReading.objects.count(), 1)
        self.assertEqual(HistoricalTemperatureImportReceipt.objects.count(), 1)
        with self.runtime_session(), transaction.atomic():
            self.bounded_transaction()
            self._insert_evidence(polyp_cell="F13", ephyrae_cell="F14",
                                  temperature_cell="C13", temperature_merged_range="C13:C14")
        self.assertEqual(HistoricalTemperatureEvidence.objects.count(), 4)


@skipUnless(_ENABLED, _REASON)
class HistoricalTemperatureRuntimeDeadlockTests(_CorrectionFixture, _RuntimePGTestCase):
    def test_deterministic_deadlock_rolls_back_victim_correction_audit_and_retries(self):
        OrganizationMembership.objects.create(
            user=self.actor, organization=self.other, role=OrganizationMembership.Role.ADMIN,
        )
        second_box = Box.objects.create(
            organization=self.other, strain=self.box.strain,
            global_code="ABC-DEF-1.002", box_number="002",
        )
        with transaction.atomic():
            second_biology = BiologicalMeasurement.objects.create(
                box=second_box, measured_on="2026-01-01", polyp_count=0, ephyrae_count=0,
            )
            self._insert_reading(biological_measurement_id=second_biology.pk)
            second_reading = HistoricalTemperatureReading.objects.get(biological_measurement=second_biology)
            self._insert_evidence(reading_id=second_reading.pk, source_box=second_box.global_code)
        originals = {row["id"]: row for row in BiologicalMeasurement.objects.values()}
        history = self.history()
        audit_count = AuditLog.objects.count()
        gate = Barrier(2)
        # select_related('organization').select_for_update() also locks the
        # institution; use separate institutions to reach the intended cycle.
        targets = [
            (self.box.pk, self.biology.pk, self.organization.pk),
            (second_box.pk, second_biology.pk, self.other.pk),
        ]

        def action(index):
            box_id, biology_id, organization_id = targets[index]
            other_box_id = targets[1 - index][0]

            def run():
                with transaction.atomic():
                    self.bounded_transaction(lock_timeout="10s")
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT current_setting('deadlock_timeout')::interval < interval '5 seconds'")
                        self.assertTrue(cursor.fetchone()[0], "Deadlock detection must precede bounded lock_timeout")
                    # The service holds its own Box, biology and reading locks and
                    # has already staged correction, invalidation and audit.
                    self.correct({"polyp_count": 7}, box_id=box_id, measurement_id=biology_id,
                                 organization_ids=[organization_id])
                    gate.wait(timeout=5)
                    try:
                        self._sql("SELECT id FROM cultures_box WHERE id = %s FOR UPDATE", [other_box_id])
                    except DatabaseError as error:
                        self.assertEqual(_sqlstate(error), "40P01")
                        self.assert_aborted()
                        return "40P01"
                return "committed"
            return run

        results = self.run_workers(action(0), action(1))
        self.assertCountEqual(results, ["40P01", "committed"])
        victim = results.index("40P01")
        victim_box, victim_biology, victim_organization = targets[victim]
        survivor_biology = targets[1 - victim][1]
        self.assertEqual(BiologicalMeasurement.objects.values().get(pk=victim_biology), originals[victim_biology])
        self.assertEqual(BiologicalMeasurement.objects.get(pk=survivor_biology).polyp_count, 7)
        victim_reading = HistoricalTemperatureReading.objects.get(biological_measurement_id=victim_biology)
        self.assertEqual(victim_reading.association_status, "confirmed")
        self.assertEqual(HistoricalTemperatureReading.objects.get(
            biological_measurement_id=survivor_biology).association_status, "pending_review")
        self.assertEqual(AuditLog.objects.count(), audit_count + 1)
        self.assertFalse(AuditLog.objects.filter(action=AuditLog.Action.UPDATE,
                                                metadata__measurement_id=victim_biology).exists())
        self.assertEqual(self.history()["evidences"], history["evidences"])
        self.assertEqual(self.history()["receipts"], history["receipts"])
        with self.runtime_session():
            self.correct({"polyp_count": 7}, box_id=victim_box, measurement_id=victim_biology,
                         organization_ids=[victim_organization])
        self.assertEqual(BiologicalMeasurement.objects.get(pk=victim_biology).polyp_count, 7)
        self.assertEqual(HistoricalTemperatureReading.objects.filter(association_status="pending_review").count(), 2)
        self.assertEqual(AuditLog.objects.count(), audit_count + 2)
        self.assertEqual(self.history()["evidences"], history["evidences"])
        self.assertEqual(self.history()["receipts"], history["receipts"])
