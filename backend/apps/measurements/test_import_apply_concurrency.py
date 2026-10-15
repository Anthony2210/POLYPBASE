"""Opt-in races on the disposable PostgreSQL QA endpoint, never SQLite evidence.

Run with config.test_settings and POLYPBASE_TEST_POSTGRES=1 only after the
repository's disposable database at 127.0.0.1:55439 has been made available.
TransactionTestCase flushes that test database between cases. No container or
external database is created or started by this module.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import date
import os
from queue import Queue
import threading
import time
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, connections
from django.test import TransactionTestCase

from apps.accounts.models import OrganizationMembership
from apps.audit.models import Alert, AuditLog
from apps.cultures.models import Box
from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain

from . import import_apply
from .csv_import import read_csv
from .import_apply import MeasurementImportApplyError, apply_measurement_import
from .import_planner import build_measurement_import_plan
from .models import BiologicalMeasurement, MeasurementImportReceipt
from .services import MeasurementWeekConflict, create_biological_measurement


DISPOSABLE_POSTGRES = (
    os.getenv("POLYPBASE_TEST_POSTGRES") == "1"
    and connection.vendor == "postgresql"
    and connection.settings_dict.get("HOST") == "127.0.0.1"
    and str(connection.settings_dict.get("PORT")) == "55439"
    and connection.settings_dict.get("NAME") in {"subculture_qa", "test_subculture_qa"}
)


@skipUnless(DISPOSABLE_POSTGRES, "Requires opt-in disposable PostgreSQL QA at 127.0.0.1:55439.")
class MeasurementImportApplyConcurrencyTests(TransactionTestCase):
    measured_on = date(2026, 9, 16)

    def setUp(self):
        self.assertEqual(connection.settings_dict["NAME"], "test_subculture_qa")
        self.org = Organization.objects.create(name="Disposable CSV apply concurrency QA")
        self.users = [
            get_user_model().objects.create_user(
                username=f"csv-apply-racer-{index}", email=f"csv-apply-racer-{index}@example.org",
            )
            for index in range(2)
        ]
        for user in self.users:
            OrganizationMembership.objects.create(user=user, organization=self.org, role="admin")
        species = Species.objects.create(scientific_name="Aurelia apply concurrency", genus_species_code="AIC")
        strain = Strain.objects.create(organization=self.org, species=species, code="AIC-QA-1")
        self.boxes = [
            Box.objects.create(
                organization=self.org, strain=strain, global_code=f"AIC-QA-1.00{index}",
                box_number=f"00{index}",
            )
            for index in (1, 2)
        ]
        self.local = threading.local()

    def _arguments(self, *, actor=None, rows=None, suffix=b""):
        source = b"box_reference,measured_on,polyp_count,ephyrae_count\r\n"
        rows = rows if rows is not None else [(self.boxes[0], 0)]
        for box, count in rows:
            source += f"{box.global_code},{self.measured_on.isoformat()},{count},0\r\n".encode("utf-8")
        source += suffix
        mapping = {field: field for field in ("box_reference", "measured_on", "polyp_count", "ephyrae_count")}
        actor = actor or self.users[0]
        preparation = read_csv(source, source_id="diagnostic-upload", mapping=mapping, confirmed=True, delimiter=",")
        plan = build_measurement_import_plan(preparation, organization=self.org, actor=actor)
        self.assertTrue(plan.ready)
        return dict(
            source=source, mapping=mapping, delimiter=",", actor=actor, organization=self.org,
            expected_preparation_fingerprint=plan.preparation_fingerprint,
            expected_plan_fingerprint=plan.fingerprint,
        )

    def _import(self, arguments):
        receipt, replayed = apply_measurement_import(**arguments)
        return {"kind": "import", "receipt_id": receipt.pk, "replayed": replayed}

    def _manual(self):
        measurement = create_biological_measurement(
            box_id=self.boxes[0].pk, organization_ids=[self.org.pk], user=self.users[1],
            data={"measured_on": self.measured_on, "polyp_count": 9, "ephyrae_count": 0},
        )
        return {"kind": "manual", "measurement_id": measurement.pk}

    def _worker(self, action, pids, *, first=False, attempted=None, before_sql=None):
        close_old_connections()
        self.local.first = first
        constraint_names = []
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET lock_timeout = '8s'")
                cursor.execute("SET statement_timeout = '15s'")
                cursor.execute("SELECT pg_backend_pid()")
                pid = cursor.fetchone()[0]
            pids.put(pid)

            def observe(execute, sql, params, many, context):
                normalized = " ".join(sql.upper().replace('"', '').split())
                if attempted is not None and "FOR UPDATE" in normalized and "CULTURES_BOX" in normalized:
                    attempted.set()
                if before_sql is not None:
                    before_sql(normalized)
                try:
                    return execute(sql, params, many, context)
                except Exception as error:
                    diagnostic = getattr(error, "diag", None) or getattr(error.__cause__, "diag", None)
                    constraint = getattr(diagnostic, "constraint_name", None)
                    if constraint:
                        constraint_names.append(constraint)
                    raise

            with connection.execute_wrapper(observe):
                try:
                    result = action()
                except (MeasurementImportApplyError, MeasurementWeekConflict) as error:
                    result = {"kind": "rejected", "error": error}
            self.assertFalse(connection.in_atomic_block)
            self.assertFalse(connection.needs_rollback)
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1, pg_backend_pid()")
                self.assertEqual(cursor.fetchone(), (1, pid))
            return {**result, "pid": pid, "constraint_names": constraint_names}
        finally:
            connections.close_all()

    def _assert_blocked_by(self, waiter, blocker):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with connection.cursor() as cursor:
                cursor.execute("SELECT %s = ANY(pg_blocking_pids(%s))", [blocker, waiter])
                if cursor.fetchone()[0]:
                    return
            time.sleep(0.02)
        self.fail("PostgreSQL did not report the second connection blocked by the first.")

    def _ordered(self, first_action, second_action):
        entered = threading.Event()
        attempted = threading.Event()
        release = threading.Event()
        first_pids, second_pids = Queue(), Queue()
        original_audit = AuditLog.objects.create

        def blocking_audit(**kwargs):
            audit = original_audit(**kwargs)
            if getattr(self.local, "first", False):
                entered.set()
                if not release.wait(15):
                    raise TimeoutError("First writer was not released from its locked transaction.")
            return audit

        with patch.object(AuditLog.objects, "create", blocking_audit), ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self._worker, first_action, first_pids, first=True)
            try:
                first_pid = first_pids.get(timeout=8)
                self.assertTrue(entered.wait(8), "First writer did not reach its transactional audit.")
                second = pool.submit(self._worker, second_action, second_pids, attempted=attempted)
                second_pid = second_pids.get(timeout=8)
                self.assertNotEqual(first_pid, second_pid)
                self.assertTrue(attempted.wait(8), "Second writer did not attempt the shared Box lock.")
                self._assert_blocked_by(second_pid, first_pid)
                self.assertFalse(second.done())
            finally:
                release.set()
            return first.result(timeout=20), second.result(timeout=20)

    def _assert_one_import(self, *, count=0, actor=None, source=None):
        self.assertEqual(BiologicalMeasurement.objects.count(), 1)
        self.assertEqual(MeasurementImportReceipt.objects.count(), 1)
        self.assertEqual(AuditLog.objects.count(), 2)
        self.assertFalse(Alert.objects.exists())
        measurement = BiologicalMeasurement.objects.get()
        self.assertEqual((measurement.polyp_count, measurement.ephyrae_count), (count, 0))
        self.assertEqual(measurement.imported_by_id, (actor or self.users[0]).pk)
        self.assertIsNone(measurement.original_observer_id)
        self.assertEqual(measurement.origin, "file_import")
        receipt = MeasurementImportReceipt.objects.get()
        self.assertEqual(receipt.result["rows"][0]["measurement_id"], measurement.pk)
        self.assertEqual(set(receipt.result["audit_ids"]), set(AuditLog.objects.values_list("pk", flat=True)))
        if source is not None:
            self.assertEqual(bytes(receipt.source_bytes), source)
        self.boxes[0].refresh_from_db()
        self.assertEqual(self.boxes[0].polyp_state_revision, 1)

    def test_same_source_same_actor_waits_then_replays_original_receipt(self):
        arguments = self._arguments()
        first, second = self._ordered(lambda: self._import(arguments), lambda: self._import(arguments))
        self.assertEqual((first["kind"], second["kind"]), ("import", "import"))
        self.assertFalse(first["replayed"])
        self.assertTrue(second["replayed"])
        self.assertEqual(first["receipt_id"], second["receipt_id"])
        self.assertEqual(first["constraint_names"], [])
        self.assertEqual(second["constraint_names"], [])
        self._assert_one_import(source=arguments["source"])

    def test_same_source_different_actors_replays_previously_reviewed_interpretation(self):
        # Both actors review before acceptance. The replay identity belongs to the
        # institution/source/interpretation, not to the actor who wins the race.
        first_arguments = self._arguments(actor=self.users[0])
        second_arguments = self._arguments(actor=self.users[1])
        first, second = self._ordered(lambda: self._import(first_arguments), lambda: self._import(second_arguments))
        self.assertEqual(second["kind"], "import", second)
        self.assertTrue(second["replayed"])
        self.assertEqual(first["receipt_id"], second["receipt_id"])
        self.assertEqual(MeasurementImportReceipt.objects.get().actor_id, self.users[0].pk)
        self._assert_one_import()

    def test_different_source_same_values_invalidates_the_losing_reviewed_plan(self):
        first_arguments = self._arguments()
        second_arguments = self._arguments(suffix=b"\r\n")
        first, second = self._ordered(lambda: self._import(first_arguments), lambda: self._import(second_arguments))
        self.assertEqual(first["kind"], "import")
        self.assertEqual(second["kind"], "rejected")
        self.assertEqual(second["error"].code, "stale_plan")
        self._assert_one_import(source=first_arguments["source"])

    def test_conflicting_source_rolls_back_its_entire_multi_box_batch(self):
        first_arguments = self._arguments()
        second_arguments = self._arguments(rows=[(self.boxes[1], 13), (self.boxes[0], 17)])
        first, second = self._ordered(lambda: self._import(first_arguments), lambda: self._import(second_arguments))
        self.assertEqual(first["kind"], "import")
        self.assertEqual(second["kind"], "rejected")
        self.assertEqual(second["error"].code, "stale_plan")
        self._assert_one_import(source=first_arguments["source"])
        self.assertFalse(BiologicalMeasurement.objects.filter(box=self.boxes[1]).exists())
        self.boxes[1].refresh_from_db()
        self.assertEqual(self.boxes[1].polyp_state_revision, 0)

    def test_reverse_source_order_shares_stable_multi_box_lock_order(self):
        first_arguments = self._arguments(rows=[(self.boxes[0], 0), (self.boxes[1], 0)])
        second_arguments = self._arguments(rows=[(self.boxes[1], 0), (self.boxes[0], 0)])
        first, second = self._ordered(lambda: self._import(first_arguments), lambda: self._import(second_arguments))
        self.assertEqual(first["kind"], "import")
        self.assertEqual(second["kind"], "rejected")
        self.assertEqual(second["error"].code, "stale_plan")
        self.assertEqual(BiologicalMeasurement.objects.count(), 2)
        self.assertEqual(MeasurementImportReceipt.objects.count(), 1)
        self.assertEqual(AuditLog.objects.count(), 3)
        self.assertFalse(Alert.objects.exists())
        for box in self.boxes:
            box.refresh_from_db()
            self.assertEqual(box.polyp_state_revision, 1)

    def test_different_sources_on_disjoint_boxes_can_both_commit(self):
        arguments = [self._arguments(rows=[(box, 0)]) for box in self.boxes]
        barrier = threading.Barrier(2, timeout=8)
        original_plan = import_apply.build_measurement_import_plan
        pids = Queue()

        def overlap_locked_plans(*args, **kwargs):
            plan = original_plan(*args, **kwargs)
            barrier.wait()
            return plan

        with patch.object(import_apply, "build_measurement_import_plan", overlap_locked_plans), ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self._worker, lambda args=args: self._import(args), pids) for args in arguments]
            results = [future.result(timeout=25) for future in futures]
        self.assertEqual(len({result["pid"] for result in results}), 2)
        self.assertTrue(all(result["kind"] == "import" and not result["replayed"] for result in results), results)
        self.assertEqual(MeasurementImportReceipt.objects.count(), 2)
        self.assertEqual(BiologicalMeasurement.objects.count(), 2)
        self.assertEqual(AuditLog.objects.count(), 4)
        self.assertFalse(Alert.objects.exists())

    def test_manual_create_wins_then_import_revalidates_without_receipt(self):
        arguments = self._arguments()
        first, second = self._ordered(self._manual, lambda: self._import(arguments))
        self.assertEqual(first["kind"], "manual")
        self.assertEqual(second["kind"], "rejected")
        self.assertEqual(second["error"].code, "stale_plan")
        self.assertEqual(BiologicalMeasurement.objects.count(), 1)
        measurement = BiologicalMeasurement.objects.get()
        self.assertEqual(measurement.pk, first["measurement_id"])
        self.assertEqual(measurement.polyp_count, 9)
        self.assertEqual(measurement.origin, "direct_entry")
        self.assertEqual(measurement.original_observer_id, self.users[1].pk)
        self.assertFalse(MeasurementImportReceipt.objects.exists())
        self.assertEqual(AuditLog.objects.count(), 1)
        self.assertFalse(Alert.objects.exists())
        self.boxes[0].refresh_from_db()
        self.assertEqual(self.boxes[0].polyp_state_revision, 1)

    def test_import_wins_then_manual_create_reports_week_conflict_without_overwrite(self):
        arguments = self._arguments()
        first, second = self._ordered(lambda: self._import(arguments), self._manual)
        self.assertEqual(first["kind"], "import")
        self.assertEqual(second["kind"], "rejected")
        self.assertIsInstance(second["error"], MeasurementWeekConflict)
        self.assertEqual(second["error"].measurement_id, BiologicalMeasurement.objects.get().pk)
        self._assert_one_import()

    def test_same_blank_source_races_on_receipt_constraint_and_rolls_back_losing_audit(self):
        # No Box exists in this plan to serialize acceptance: the receipt's unique
        # constraint must select a winner and roll back the loser's IMPORT audit.
        arguments = self._arguments(rows=[])
        barrier = threading.Barrier(2, timeout=8)
        pids = Queue()

        def meet_at_receipt_insert(sql):
            if sql.startswith("INSERT INTO MEASUREMENTS_MEASUREMENTIMPORTRECEIPT "):
                barrier.wait()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(self._worker, lambda: self._import(arguments), pids, before_sql=meet_at_receipt_insert)
                for _ in range(2)
            ]
            results = [future.result(timeout=25) for future in futures]
        self.assertEqual(len({result["pid"] for result in results}), 2)
        self.assertTrue(all(result["kind"] == "import" for result in results), results)
        self.assertEqual(sorted(result["replayed"] for result in results), [False, True])
        self.assertEqual(results[0]["receipt_id"], results[1]["receipt_id"])
        self.assertEqual(
            [name for result in results for name in result["constraint_names"]],
            ["unique_measurement_import_acceptance"],
        )
        self.assertFalse(BiologicalMeasurement.objects.exists())
        self.assertEqual(MeasurementImportReceipt.objects.count(), 1)
        self.assertEqual(AuditLog.objects.count(), 1)
        receipt = MeasurementImportReceipt.objects.get()
        self.assertEqual(bytes(receipt.source_bytes), arguments["source"])
        self.assertEqual(receipt.result["rows"], [])
        self.assertEqual(receipt.result["audit_ids"], [AuditLog.objects.get().pk])
        self.assertFalse(Alert.objects.exists())
