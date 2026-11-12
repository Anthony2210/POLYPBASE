"""The 13 legacy PostgreSQL scenarios, inherited by the genuine-role v2 suite.

Names preserve the assertion mapping to v1. Only the authorized temperature
transition changes: v2 preserves the exact 41 confirmations, and rejects a
mixed/pending manifest. Ordinary runtime changes still invalidate associations.
Fault injection uses actual SQL triggers, never mocked permission decisions.
"""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import date
from threading import Event

from django.db import DatabaseError, connection

from apps.audit.models import AuditLog
from apps.measurements.models import BiologicalMeasurement, HistoricalTemperatureReading, Observation


from .box_1902_test_support import runtime_test
from .historical_2026 import cla_box_1902 as correction
from .models import Box, HistoricalBoxMovement
from .qr import box_scan_url
from .test_cla_box_1902 import NEW_CODE, OPERATION, OLD_CODE


class ClaBox1902PostgreSQLTests:
    """All old PostgreSQL acceptance methods run in the concrete v2 class."""

    def create_historical_fixture(self, *, pending_weeks=()):
        movement = self.box.movements.get()
        HistoricalBoxMovement.objects.create(
            box=self.box, iso_year=2026, iso_week=39,
            from_thermal_zone=movement.from_thermal_zone, to_thermal_zone=movement.to_thermal_zone,
            origin_location=movement.origin_location, destination_location=movement.destination_location,
            candidate_key="b" * 64, plan_fingerprint="c" * 64,
            evidence={"synthetic": True}, accepted_by=self.admin, notes="Preserved historical movement",
        )
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT tgname FROM pg_trigger WHERE NOT tgisinternal AND tgenabled = 'O' "
                "AND tgname IN ('historical_box_guard','historical_biology_guard',"
                "'historical_reading_valid','historical_reading_immutable','historical_reading_nonempty',"
                "'historical_evidence_valid','historical_evidence_immutable')"
            )
            self.assertEqual({r[0] for r in cursor.fetchall()}, {
                "historical_box_guard", "historical_biology_guard", "historical_reading_valid",
                "historical_reading_immutable", "historical_reading_nonempty",
                "historical_evidence_valid", "historical_evidence_immutable",
            })
            if pending_weeks:
                cursor.execute(
                    "UPDATE measurements_historicaltemperaturereading SET association_status='pending_review' "
                    "WHERE id = ANY(%s)", [[12161 + week for week in pending_weeks]],
                )
        return self.reading_ids, self.witness_ids

    def assert_historical_correction(self, *, pending_weeks=()):
        direct, witnesses = self.create_historical_fixture(pending_weeks=pending_weeks)
        reviewed = self.plan()
        self.assert_classification(reviewed, "READY")
        self.assertEqual(len(direct), 41)
        # Supersedes v1's accepted invalidations: v2 has no scientific delta.
        self.assertEqual(reviewed["temperature_association_invalidations"], [])
        self.assertEqual(reviewed["temperature_association_preservations"], [
            pk for week, pk in zip(range(12, 53), direct) if week not in pending_weeks
        ])
        readings = {r["id"]: r for r in HistoricalTemperatureReading.objects.order_by("pk").values()}
        self.assertEqual(sum(readings[pk]["association_status"] == "confirmed" for pk in direct),
                         len(direct) - len(pending_weeks))
        dependencies = reviewed["dependencies"]["measurements.historicaltemperaturereading"]
        self.assertIn(witnesses[0], [r["id"] for r in dependencies])
        self.assertNotIn(witnesses[1], [r["id"] for r in dependencies])
        self.approve(plan=reviewed)
        before = self.snapshot()
        if pending_weeks:
            # The documented v2 writer requires all 41 exact confirmed rows.
            with self.assertRaisesMessage(DatabaseError, "Exact 41 confirmed readings") as raised:
                self.apply(plan=reviewed)
            self.assertEqual(raised.exception.__cause__.sqlstate, "23514")
            self.assertEqual(self.snapshot(), before)
            self.assertEqual({r["id"]: r for r in HistoricalTemperatureReading.objects.order_by("pk").values()},
                             readings)
            return
        expected_boxes = {r["id"]: r for r in Box.objects.order_by("pk").values()}
        expected_boxes[1902].update(global_code=NEW_CODE, strain_id=285)
        expected_dependencies = deepcopy(reviewed["dependencies"])
        for row in expected_dependencies.get("cultures.box", []):
            if row["id"] == 1902:
                row.update(global_code=NEW_CODE, strain_id=285)
        old_audits = list(AuditLog.objects.order_by("pk").values())
        url = box_scan_url(self.box)
        self.assertEqual(self.apply(plan=reviewed), reviewed)
        self.assert_preserved(before)
        self.assertEqual({r["id"]: r for r in Box.objects.order_by("pk").values()}, expected_boxes)
        self.assertEqual({r["id"]: r for r in HistoricalTemperatureReading.objects.order_by("pk").values()}, readings)
        self.box.refresh_from_db()
        self.assertEqual(box_scan_url(self.box), url)
        self.assertEqual(self.box.tags.get(tag_type="qr").url, url)
        fresh = self.plan()
        self.assert_classification(fresh, "ALREADY_CORRECT")
        self.assertEqual(fresh["temperature_association_invalidations"], [])
        self.assertEqual(fresh["dependencies"], expected_dependencies)
        self.assertEqual(list(AuditLog.objects.filter(pk__in=[r["id"] for r in old_audits])
                              .order_by("pk").values()), old_audits)
        new_audits = list(AuditLog.objects.exclude(pk__in=[r["id"] for r in old_audits]).order_by("pk"))
        self.assertEqual(len(new_audits), 2)
        # v2 adds independently approved provenance to the old exact audit fields.
        for audit, object_type, object_id in (
            (new_audits[0], "box", NEW_CODE), (new_audits[1], "cla_box_1902_correction", "1902"),
        ):
            self.assertEqual((audit.organization_id, audit.user_id, audit.action, audit.object_type,
                              audit.object_id, audit.description),
                             (1, self.admin.pk, AuditLog.Action.UPDATE, object_type, object_id, correction.REASON))
            self.assertEqual(audit.metadata, {
                "operation": OPERATION, "plan_hash": correction.fingerprint(reviewed),
                "source_species_id": 131, "before": reviewed["manifest"]["box"],
                "after": fresh["manifest"]["box"], "reviewed_plan": reviewed,
                "reason": correction.REASON, "scientific_basis": correction.REASON,
                "actor_id": self.admin.pk, "approval_id": 1,
            })
            # SQL jsonb uses native numerics, whereas Django snapshots stringify
            # Decimal. Compare every field to the independently approved SQL image.
            self.assertEqual(audit.original_values, reviewed["manifest"]["box"])
        self.assertEqual(self.receipt().pk, new_audits[1].pk)
        after = self.snapshot()
        with self.assertRaisesMessage(ValueError, "changed since review"):
            self.apply(plan=reviewed)
        self.assertEqual(self.snapshot(), after)
        self.assertEqual(self.apply(plan=fresh), fresh)
        self.assertEqual(self.snapshot(), after)

    @runtime_test
    def test_41_confirmed_historical_readings_allow_only_direct_status_transitions(self):
        """V1 direct invalidation is replaced by exact v2 preservation."""
        self.assert_historical_correction()

    @runtime_test
    def test_mixed_historical_statuses_preserve_pending_and_all_scientific_history(self):
        """V2 rejects mixed status input; every original scientific row survives."""
        self.assert_historical_correction(pending_weeks=(12, 17, 38, 52))

    @runtime_test
    def test_already_pending_historical_readings_need_no_status_changes(self):
        """V2 rejects all-pending input without changing science or approval."""
        self.assert_historical_correction(pending_weeks=tuple(range(12, 53)))

    @runtime_test
    def test_reviewed_historical_scientific_state_change_rejects_without_writes(self):
        self.create_historical_fixture()
        reviewed = self.plan()
        biology = self.box.biological_measurements.order_by("measured_on").first()
        BiologicalMeasurement.objects.filter(pk=biology.pk).update(polyp_count=1)
        self.assertEqual(HistoricalTemperatureReading.objects.get(
            biological_measurement=biology).association_status, "pending_review")
        self.assertNotEqual(correction.fingerprint(self.plan()), correction.fingerprint(reviewed))
        before = self.snapshot()
        with self.assertRaisesMessage(ValueError, "changed since review"):
            self.apply(plan=reviewed)
        self.assertEqual(self.snapshot(), before)

    @runtime_test
    def test_extra_historical_association_invalidates_review_without_writes(self):
        self.create_historical_fixture()
        biology = BiologicalMeasurement.objects.create(
            box=self.box, measured_on=date(2024, 12, 23),
            polyp_count=0, ephyrae_count=0, strobila_count=None,
        )
        reviewed = self.plan()
        # A real extra immutable reading/evidence pair, not a mocked dependency.
        with self.role("polypbase"):
            self.create_extra_reading(biology)
        self.assertNotEqual(correction.fingerprint(self.plan()), correction.fingerprint(reviewed))
        before = self.snapshot()
        with self.assertRaisesMessage(ValueError, "changed since review"):
            self.apply(plan=reviewed)
        self.assertEqual(self.snapshot(), before)

    @runtime_test
    def test_unexpected_post_update_dependency_mutation_rolls_back_historical_correction(self):
        direct, witnesses = self.create_historical_fixture()
        self.install_dependency_mutation()
        reviewed = self.plan()
        self.approve(plan=reviewed)
        before = self.snapshot()
        observed = []

        def inspect(execute, statement, params, many, context):
            result = execute(statement, params, many, context)
            if statement.startswith("SELECT public.box_1902_apply_v2"):
                observed.append(True)
                self.assertEqual(Box.objects.get(pk=1902).global_code, NEW_CODE)
                self.assertEqual(HistoricalTemperatureReading.objects.filter(
                    pk__in=direct, association_status="confirmed").count(), 41)
                self.assertEqual(HistoricalTemperatureReading.objects.filter(
                    pk__in=witnesses, association_status="confirmed").count(), 2)
                self.assertTrue(Observation.objects.filter(
                    box_id=1902, notes="Unexpected synthetic dependency mutation").exists())
            return result

        with connection.execute_wrapper(inspect):
            with self.assertRaisesMessage(ValueError, "Scientific or historical state changed"):
                self.apply(plan=reviewed)
        self.assertEqual(observed, [True])
        self.assertFalse(connection.in_atomic_block)
        self.assertFalse(connection.needs_rollback)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.plan(), reviewed)

    @runtime_test
    def test_exact_box_delta_preserves_references_zero_null_and_qr(self):
        # The original single-Box test now also asserts v2 private provenance.
        self.test_apply_changes_exactly_two_fields_on_exactly_one_box()
        receipt = self.receipt()
        self.assertEqual(receipt.user_id, self.admin.pk)
        self.assertEqual(receipt.metadata["before"]["global_code"], OLD_CODE)
        self.assertEqual(receipt.metadata["after"]["id"], 1902)
        self.assertEqual(receipt.metadata["after"]["global_code"], NEW_CODE)
        self.assertEqual(receipt.metadata["after"]["strain_id"], 285)
        self.assertEqual(AuditLog.objects.filter(metadata__operation=OPERATION).count(), 2)
        self.assertFalse(AuditLog.objects.filter(object_type="identity_reconciliation").exists())

    @runtime_test
    def test_concurrent_apply_commits_once_then_requires_a_fresh_review(self):
        """Hold a real SQL audit INSERT, not the removed ORM audit writer."""
        self.install_audit_barrier()
        reviewed = self.plan()
        self.approve(plan=reviewed)
        self.concurrent_python_correction(reviewed)
        self.assertEqual(AuditLog.objects.filter(metadata__operation=OPERATION).count(), 2)
        self.receipt()
        fresh = self.plan()
        self.assert_classification(fresh, "ALREADY_CORRECT")
        before = self.snapshot()
        with self.assertRaises(ValueError):
            self.apply(plan=reviewed)
        self.assertEqual(self.apply(plan=fresh), fresh)
        self.assertEqual(self.snapshot(), before)

    @runtime_test
    def test_active_writer_aborts_apply_nowait_then_invalidates_the_old_plan(self):
        reviewed = self.plan()
        held, release = Event(), Event()

        def writer():
            with self.connect("polypbase") as conn:
                with conn.transaction():
                    conn.execute("SELECT id FROM cultures_box WHERE id=1902 FOR UPDATE")
                    conn.execute("UPDATE cultures_box SET notes=%s WHERE id=1902",
                                 ["Synthetic concurrent ordinary writer"])
                    held.set()
                    if not release.wait(20):
                        raise TimeoutError("Ordinary writer was not released")

        before = self.snapshot()
        statements = []

        def record(execute, statement, params, many, context):
            statements.append(statement)
            return execute(statement, params, many, context)

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(writer)
            try:
                self.assertTrue(held.wait(20))
                with connection.execute_wrapper(record), self.assertRaises(DatabaseError) as raised:
                    self.apply(plan=reviewed)
                self.assertEqual(raised.exception.__cause__.sqlstate, "55P03")
                self.assertTrue(any(s.startswith("SELECT public.box_1902_lock_v2") for s in statements))
                self.assertFalse(any(s.startswith("SELECT public.box_1902_apply_v2") for s in statements))
                body = self.execute("SELECT prosrc FROM pg_proc WHERE oid='public.box_1902_lock_v2(bigint)'::regprocedure")[0][0]
                self.assertIn("EXCLUSIVE MODE NOWAIT", body)
                self.assertFalse(any("FOR UPDATE" in s.upper() or "FOR NO KEY UPDATE" in s.upper() for s in statements))
                self.assertFalse(future.done())
                self.assertEqual(self.snapshot(), before)
            finally:
                release.set()
            future.result(timeout=20)
        fresh = self.plan()
        self.assertNotEqual(correction.fingerprint(fresh), correction.fingerprint(reviewed))
        after_writer = self.snapshot()
        with self.assertRaises(ValueError):
            self.apply(plan=reviewed)
        self.assertEqual(self.snapshot(), after_writer)
        self.approve(plan=fresh)
        self.assertEqual(self.apply(plan=fresh), fresh)

    @runtime_test
    def test_measurement_change_invalidates_review_and_preserves_zero(self):
        reviewed = self.plan()
        measurement = self.box.biological_measurements.filter(measured_on__year=2026).order_by("measured_on").first()
        self.assertEqual(measurement.strobila_count, 0)
        BiologicalMeasurement.objects.filter(pk=measurement.pk).update(strobila_count=None)
        self.assertNotEqual(correction.fingerprint(self.plan()), correction.fingerprint(reviewed))
        before = self.snapshot()
        with self.assertRaises(ValueError):
            self.apply(plan=reviewed)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(list(self.box.biological_measurements.filter(measured_on__year=2026).order_by("measured_on")
                              .values_list("polyp_count", "ephyrae_count", "strobila_count")),
                         [(0, 0, None), (0, 0, None)])

    @runtime_test
    def test_receipt_failure_rolls_back_box_namespaces_and_first_audit(self):
        self.create_historical_fixture()
        self.test_receipt_audit_failure_rolls_back_box_namespace_and_other_audits()
        self.assertFalse(connection.in_atomic_block)
        self.assertFalse(connection.needs_rollback)
        self.assert_classification(self.plan(), "READY")

    @runtime_test
    def test_planning_uses_read_only_transaction_without_write_or_row_locks(self):
        observed = []

        def inspect(execute, statement, params, many, context):
            if statement == "SELECT public.box_1902_manifest_v2()":
                self.assert_planner_read_only(observed)
            result = execute(statement, params, many, context)
            if statement == "SELECT public.box_1902_manifest_v2()":
                self.assert_planner_read_only(observed)
            return result

        before = self.snapshot()
        with connection.execute_wrapper(inspect):
            reviewed = self.plan()
            self.assert_classification(reviewed, "READY")
            self.assertEqual(self.plan(), reviewed)
        self.assertEqual(len(observed), 4)
        self.assertFalse(connection.in_atomic_block)
        with connection.cursor() as cursor:
            cursor.execute("SHOW transaction_read_only")
            self.assertEqual(cursor.fetchone(), ("off",))
        self.assertEqual(self.snapshot(), before)

    @runtime_test
    def test_postgresql_rejects_injected_planner_write_and_restores_session(self):
        """Real read-only transaction rejects DDL before any schema permission check."""
        before = self.snapshot()

        def attempt(execute, statement, params, many, context):
            result = execute(statement, params, many, context)
            if statement == "SELECT public.box_1902_manifest_v2()":
                with connection.cursor() as cursor:
                    cursor.execute("CREATE TABLE public.box1902_forbidden_planner_write (id integer)")
                self.fail("PostgreSQL accepted a write inside read-only planning")
            return result

        with connection.execute_wrapper(attempt), self.assertRaises(DatabaseError) as raised:
            self.plan()
        self.assertEqual(raised.exception.__cause__.sqlstate, "25006")
        self.assertFalse(connection.in_atomic_block)
        self.assertFalse(connection.needs_rollback)
        self.assertEqual(self.snapshot(), before)
        self.assert_classification(self.plan(), "READY")
