"""Reconfirmation contracts; PostgreSQL proofs use only the fresh-cluster harness.

Run from the repository root:
    uv run python scripts/test_historical_temperature_production.py --run \
        --pg-bin "C:/Program Files/PostgreSQL/18/bin" \
        apps.measurements.test_historical_temperature_reconfirm

SQLite proves refusal only, never trigger, locking or transactional guarantees.
"""

import json
import os
import threading
from copy import deepcopy
from datetime import date
from importlib import import_module
from io import StringIO
from pathlib import Path
from time import monotonic, sleep
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import DatabaseError, close_old_connections, connection, transaction
from django.test import SimpleTestCase, TestCase, TransactionTestCase

from apps.audit.models import AuditLog
from apps.cultures.models import Box
from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain

from .models import (
    BiologicalMeasurement, HistoricalTemperatureEvidence,
    HistoricalTemperatureImportReceipt, HistoricalTemperatureReading,
)
from .test_historical_temperature_plan import evidence


# The public contract does not prescribe a dedicated exception class. Do not
# accept TypeError/AttributeError/ImportError: those indicate API integration bugs.
REFUSALS = (ValueError, ValidationError, PermissionDenied, RuntimeError)
TABLES = (
    "measurements_historicaltemperaturereading",
    "measurements_historicaltemperatureevidence",
    "measurements_historicaltemperatureimportreceipt",
)
SNAPSHOT_TABLES = (*TABLES, "measurements_biologicalmeasurement", "cultures_box")
DISPOSABLE = os.environ.get("POLYPBASE_HISTORICAL_TEMPERATURE_DISPOSABLE_CLUSTER")


def service():
    return import_module("apps.measurements.historical_temperature_reconfirm")


def sql(statement, parameters=None):
    with connection.cursor() as cursor:
        cursor.execute(statement, parameters)


def state():
    return {
        model._meta.label: list(model.objects.order_by("pk").values())
        for model in (
            HistoricalTemperatureReading, HistoricalTemperatureEvidence,
            HistoricalTemperatureImportReceipt, BiologicalMeasurement, Box, AuditLog,
        )
    }


class ScopeWithoutConnectionTests(SimpleTestCase):
    def test_foreign_organization_is_rejected_before_opening_a_cursor(self):
        module = service()
        for organization_id in (0, 2, None):
            for apply in (False, True):
                with self.subTest(organization_id=organization_id, apply=apply):
                    with self.assertRaises(ValueError):
                        module.run(organization_id=organization_id, apply=apply)


class ReconfirmationFixture:
    def make_fixture(self):
        self.org = Organization.objects.create(pk=1, name="Synthetic reconfirmation laboratory")
        self.other = Organization.objects.create(pk=2, name="Foreign synthetic laboratory")
        self.actor = get_user_model().objects.create_superuser(
            username="reconfirm-superuser", email="reconfirm@example.invalid", password=None,
        )
        self.ordinary = get_user_model().objects.create_user(
            username="reconfirm-ordinary", email="ordinary@example.invalid", is_staff=True,
        )
        species = Species.objects.create(scientific_name="Synthetic reconfirmation species", genus_species_code="ABC")
        strain = Strain.objects.create(organization=self.org, species=species, code="ABC-DEF-1")
        self.box = Box.objects.create(
            organization=self.org, strain=strain, global_code="ABC-DEF-1.001", box_number="001",
        )
        foreign_strain = Strain.objects.create(organization=self.other, species=species, code="XYZ-DEF-1")
        foreign_box = Box.objects.create(
            organization=self.other, strain=foreign_strain, global_code="XYZ-DEF-1.001", box_number="001",
        )
        self.reading = self.make_reading(self.box, pending=True, multiple=True)
        self.confirmed = self.make_reading(self.box, week=2)
        self.foreign = self.make_reading(foreign_box, pending=True)
        self.biology = self.reading.biological_measurement
        self.receipt = HistoricalTemperatureImportReceipt.objects.create(
            organization=self.org, actor_id=self.actor.pk,
            plan_fingerprint="a" * 64, snapshot_sha256="b" * 64,
            accepted_plan={"synthetic": True}, source_archive=b"synthetic original workbook bytes",
            results={"reading_ids": [self.reading.pk, self.confirmed.pk]},
        )
        self.old_audit = AuditLog.objects.create(
            organization=self.org, user=self.ordinary, action=AuditLog.Action.IMPORT,
            object_type="historical_temperature_import", object_id=str(self.receipt.pk),
            metadata={"synthetic": True, "receipt_id": self.receipt.pk},
        )
        self.module = service()

    def make_reading(self, box, *, week=1, pending=False, multiple=False, source_box=None):
        biology = BiologicalMeasurement.objects.create(
            box=box, measured_on=date.fromisocalendar(2026, week, 4),
            polyp_count=0, ephyrae_count=0, user=self.ordinary,
        )
        with transaction.atomic():
            reading = HistoricalTemperatureReading.objects.create(
                biological_measurement=biology, iso_year=2026, iso_week=week, temperature_c="0.000",
            )
            for index in range(2 if multiple else 1):
                HistoricalTemperatureEvidence.objects.create(reading=reading, **evidence(
                    source_box=source_box or box.global_code, source_value="0" if index == 0 else "0.000",
                    polyp_cell=f"F{3 + 2 * index}", ephyrae_cell=f"F{4 + 2 * index}",
                    source_iso_week=week, week_value=str(week),
                ))
        if pending:
            # Exercise the permanent invalidation trigger, not a disabled guard.
            sql("UPDATE measurements_historicaltemperaturereading SET association_status='pending_review' WHERE id=%s", [reading.pk])
            reading.refresh_from_db()
        return reading

    def approved(self, report=None, **overrides):
        report = self.module.run() if report is None else report
        arguments = {
            "organization_id": 1, "actor_id": self.actor.pk,
            "reason": "Synthetic independent source review",
            "attestation": self.module.ATTESTATION, "apply": True,
            "expected_count": report["pending_count"],
            "expected_fingerprint": report["fingerprint"],
        }
        arguments.update(overrides)
        return self.module.run(**arguments)


@skipUnless(connection.vendor == "sqlite", "SQLite refusal proof only")
class SQLiteRefusalTests(ReconfirmationFixture, TestCase):
    def setUp(self):
        self.make_fixture()

    def test_dry_run_and_apply_refuse_sqlite_without_mutating_anything(self):
        before = state()
        for apply in (False, True):
            with self.subTest(apply=apply), self.assertRaises(REFUSALS):
                self.module.run(
                    organization_id=1, actor_id=self.actor.pk, apply=apply,
                    reason="Synthetic review", attestation=self.module.ATTESTATION,
                    expected_count=1, expected_fingerprint="c" * 64,
                )
            self.assertEqual(state(), before)


@skipUnless(connection.vendor == "postgresql" and DISPOSABLE,
            "Requires scripts/test_historical_temperature_production.py and its fresh disposable cluster")
class PostgreSQLReconfirmationTests(ReconfirmationFixture, TransactionTestCase):
    reset_sequences = True

    def assert_disposable(self):
        self.assertEqual(connection.settings_dict["HOST"], "127.0.0.1")
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database(), current_setting('data_directory')")
            database, directory = cursor.fetchone()
        self.assertEqual(database, "test_subculture_qa")
        self.assertEqual(Path(directory).resolve(), Path(DISPOSABLE).resolve())
        self.assertTrue(Path(directory).parent.name.startswith("polypbase-ht-production-synthetic-"))

    def setUp(self):
        self.assert_disposable()
        self.make_fixture()

    def _fixture_teardown(self):
        # Adapt ProductionFixture's append-only cleanup, with an additional live
        # cluster identity check immediately before any trigger is disabled.
        self.assert_disposable()
        for table in TABLES:
            sql(f"ALTER TABLE {table} DISABLE TRIGGER USER")
        try:
            super()._fixture_teardown()
        finally:
            for table in TABLES:
                sql(f"ALTER TABLE {table} ENABLE TRIGGER USER")

    def trigger_definition(self):
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_get_functiondef('historical_temperature_reading_immutable()'::regprocedure)")
            return cursor.fetchone()[0]

    def assert_raw_reconfirmation_rejected(self, reading_id):
        with self.assertRaises(DatabaseError), transaction.atomic():
            sql("UPDATE measurements_historicaltemperaturereading SET association_status='confirmed' WHERE id=%s", [reading_id])

    def assert_refused_unchanged(self, **overrides):
        before = state()
        definition = self.trigger_definition()
        with self.assertRaises(REFUSALS):
            self.approved(**overrides)
        self.assertEqual(state(), before)
        self.assertEqual(self.trigger_definition(), definition)

    def test_dry_run_is_json_deterministic_read_only_and_scoped(self):
        before, definition = state(), self.trigger_definition()
        report = self.module.run()
        self.assertIsInstance(report, dict)
        self.assertEqual(json.loads(json.dumps(report)), report)
        self.assertEqual(report["pending_count"], 1)
        self.assertEqual(report["eligible_count"], 1)
        self.assertEqual(report["blockers"], [])
        self.assertRegex(report["fingerprint"], r"^[0-9a-f]{64}$")
        self.assertEqual(self.module.run(), report)
        self.assertEqual(state(), before)
        self.assertEqual(self.trigger_definition(), definition)
        self.assert_raw_reconfirmation_rejected(self.reading.pk)

    def test_command_dry_run_then_authorized_apply_and_stale_replay(self):
        before = state()
        output = StringIO()
        call_command("reconfirm_historical_temperatures", "--organization-id", "1", stdout=output)
        report = json.loads(output.getvalue())
        self.assertEqual(report, self.module.run())
        self.assertEqual(state(), before)
        arguments = (
            "--organization-id", "1", "--apply", "--actor-id", str(self.actor.pk),
            "--reason", "Synthetic command review", "--owner-attestation", self.module.ATTESTATION,
            "--expected-count", str(report["pending_count"]),
            "--expected-fingerprint", report["fingerprint"],
        )
        output = StringIO()
        call_command("reconfirm_historical_temperatures", *arguments, stdout=output)
        result = json.loads(output.getvalue())
        self.assertEqual(result["applied_count"], 1)
        event = AuditLog.objects.get(pk=result["audit_id"])
        self.assertEqual(event.object_type, "historical_temperature_reconfirmation")
        self.assertEqual(event.metadata["report"], report)
        self.assertEqual(event.metadata["reason"], "Synthetic command review")
        self.assertEqual(event.metadata["operator_id"], self.actor.pk)
        self.assertEqual(event.metadata["owner_attestation"], self.module.ATTESTATION)
        applied = state()
        with self.assertRaises(CommandError):
            call_command("reconfirm_historical_temperatures", *arguments, stdout=StringIO())
        self.assertEqual(state(), applied)

    def test_command_apply_without_approval_is_rejected_without_writes(self):
        before = state()
        with self.assertRaises(CommandError):
            call_command(
                "reconfirm_historical_temperatures", "--organization-id", "1", "--apply",
                stdout=StringIO(),
            )
        self.assertEqual(state(), before)

    def test_command_blocked_dry_run_prints_report_and_exits_unsuccessfully(self):
        BiologicalMeasurement.objects.filter(pk=self.biology.pk).update(polyp_count=1)
        before = state()
        output = StringIO()
        with self.assertRaises(CommandError):
            call_command("reconfirm_historical_temperatures", "--organization-id", "1", stdout=output)
        report = json.loads(output.getvalue())
        self.assertEqual(report["eligible_count"], 0)
        self.assertEqual([item["reading_id"] for item in report["blockers"]], [self.reading.pk])
        self.assertEqual(state(), before)

    def test_authorized_apply_changes_only_status_and_appends_one_audit(self):
        before, definition = state(), self.trigger_definition()
        report = self.module.run()
        result = self.approved(report)
        self.assertIsInstance(result, dict)
        json.dumps(result)
        expected = deepcopy(before)
        for row in expected[HistoricalTemperatureReading._meta.label]:
            if row["id"] == self.reading.pk:
                row["association_status"] = "confirmed"
        after = state()
        audit_key = AuditLog._meta.label
        self.assertEqual({k: v for k, v in after.items() if k != audit_key},
                         {k: v for k, v in expected.items() if k != audit_key})
        old_ids = {row["id"] for row in before[audit_key]}
        self.assertEqual([row for row in after[audit_key] if row["id"] in old_ids], before[audit_key])
        event = AuditLog.objects.exclude(pk__in=old_ids).get()
        self.assertEqual(event.object_type, "historical_temperature_reconfirmation")
        self.assertEqual(event.organization_id, 1)
        self.assertEqual(event.user_id, self.actor.pk)
        # Metadata key names beyond the report are not part of the public API.
        metadata = json.dumps(event.metadata, sort_keys=True)
        self.assertIn("Synthetic independent source review", metadata)
        self.assertIn(self.module.ATTESTATION, metadata)
        self.assertIn(report["fingerprint"], metadata)

        def dictionaries(value):
            if isinstance(value, dict):
                yield value
                for nested in value.values():
                    yield from dictionaries(nested)
            elif isinstance(value, list):
                for nested in value:
                    yield from dictionaries(nested)

        payloads = list(dictionaries(event.metadata))
        self.assertTrue(any(all(payload.get(key) == report[key] for key in (
            "pending_count", "eligible_count", "blockers", "fingerprint",
        )) for payload in payloads), event.metadata)
        self.assertTrue(any(value == [self.reading.pk] for payload in payloads for value in payload.values()), event.metadata)
        self.assertEqual(self.trigger_definition(), definition)
        self.assert_raw_reconfirmation_rejected(self.foreign.pk)
        sql("UPDATE measurements_historicaltemperaturereading SET association_status='pending_review' WHERE id=%s", [self.reading.pk])
        self.assert_raw_reconfirmation_rejected(self.reading.pk)

    def test_receipts_remain_sql_immutable_before_during_and_after_apply(self):
        before = list(HistoricalTemperatureImportReceipt.objects.order_by("pk").values())
        definition = self.trigger_definition()
        report = self.module.run()
        phases = []

        def assert_receipt_guard(phase):
            for assignment, parameters in (
                ("organization_id=%s", [self.other.pk]),
                ("actor_id=actor_id+1", []),
                ("plan_fingerprint=%s", ["c" * 64]),
                ("snapshot_sha256=%s", ["d" * 64]),
                ("accepted_plan=%s", [json.dumps({"changed": True})]),
                ("results=%s", [json.dumps([])]),
                ("source_archive=%s", [b"changed archive"]),
                ("created_at=created_at+INTERVAL '1 second'", []),
                ("id=id+1000", []),
            ):
                with self.subTest(phase=phase, assignment=assignment):
                    with self.assertRaisesMessage(DatabaseError, "Historical temperature provenance is append-only"), transaction.atomic():
                        sql("UPDATE measurements_historicaltemperatureimportreceipt SET "
                            + assignment + " WHERE id=%s", [*parameters, self.receipt.pk])
            for statement in (
                "DELETE FROM measurements_historicaltemperatureimportreceipt",
                "TRUNCATE measurements_historicaltemperatureimportreceipt CASCADE",
            ):
                with self.subTest(phase=phase, statement=statement):
                    with self.assertRaisesMessage(DatabaseError, "Historical temperature provenance is append-only"), transaction.atomic():
                        sql(statement)
            self.assertEqual(list(HistoricalTemperatureImportReceipt.objects.order_by("pk").values()), before)
            phases.append(phase)

        def execute(execute, statement, parameters, many, context):
            result = execute(statement, parameters, many, context)
            if "CREATE OR REPLACE FUNCTION" in statement.upper() and "$reconfirm$" in statement:
                self.assertNotEqual(self.trigger_definition(), definition)
                assert_receipt_guard("temporary exception installed")
            return result

        assert_receipt_guard("before apply")
        self.assertEqual(self.module.run(), report)
        with connection.execute_wrapper(execute):
            self.approved(report)
        assert_receipt_guard("after commit")
        self.assertEqual(phases, ["before apply", "temporary exception installed", "after commit"])
        self.assertEqual(self.trigger_definition(), definition)

    def test_appended_receipts_do_not_change_the_reading_approval(self):
        report = self.module.run()
        for organization, digest in ((self.org, "c"), (self.other, "d")):
            HistoricalTemperatureImportReceipt.objects.create(
                organization=organization, actor_id=self.actor.pk,
                plan_fingerprint=digest * 64, snapshot_sha256="e" * 64,
                accepted_plan={"synthetic": True}, source_archive=b"another original archive",
                # JSON claims do not create or change relational reading links.
                results=[{"reading_id": self.reading.pk, "biological_measurement_id": self.biology.pk}],
            )
            before_dry_run = state()
            self.assertEqual(self.module.run(), report)
            self.assertEqual(state(), before_dry_run)
        receipts = list(HistoricalTemperatureImportReceipt.objects.order_by("pk").values())
        result = self.approved(report)
        self.assertEqual(result["applied_count"], 1)
        self.assertEqual(list(HistoricalTemperatureImportReceipt.objects.order_by("pk").values()), receipts)
        self.foreign.refresh_from_db()
        self.assertEqual(self.foreign.association_status, "pending_review")

    def test_fingerprint_covers_exact_pending_reading_snapshot_not_receipts(self):
        with patch.object(self.module, "_digest", wraps=self.module._digest) as digest:
            report = self.module.run()
        digest.assert_called_once()
        payload = digest.call_args.args[0]
        self.assertEqual(set(payload), {"version", "organization_id", "function", "snapshot"})
        self.assertEqual(payload["organization_id"], 1)
        self.assertEqual(payload["function"], self.trigger_definition())
        self.assertEqual(len(payload["snapshot"]), 1)
        row = payload["snapshot"][0]
        self.assertEqual(set(row), {"reading", "biology", "box", "evidence"})
        for key, item in (("reading", self.reading), ("biology", self.biology), ("box", self.box)):
            # Biological fixture inserts advance the persisted Box revision.
            item.refresh_from_db()
            self.assertEqual(row[key], {f.attname: getattr(item, f.attname) for f in item._meta.fields})
        self.assertEqual(row["evidence"], list(self.reading.evidences.order_by("pk").values()))
        self.assertEqual(report["fingerprint"], self.module._digest(payload))

    def test_box_metadata_change_invalidates_approval_without_changing_eligibility(self):
        report = self.module.run()
        Box.objects.filter(pk=self.box.pk).update(notes="Updated metadata after approval")
        current = self.module.run()
        self.assertEqual(current["pending_count"], report["pending_count"])
        self.assertEqual(current["eligible_count"], report["eligible_count"])
        self.assertEqual(current["blockers"], [])
        self.assertNotEqual(current["fingerprint"], report["fingerprint"])
        self.assert_refused_unchanged(report=report)

    def test_replay_is_rejected_not_reported_as_a_second_success(self):
        report = self.module.run()
        self.approved(report)
        self.assert_refused_unchanged(report=report)
        empty = self.module.run()
        self.assertEqual((empty["pending_count"], empty["eligible_count"]), (0, 0))
        self.assert_refused_unchanged(report=empty)

    def test_apply_requires_active_superuser_reason_exact_attestation_and_approval(self):
        inactive = get_user_model().objects.create_superuser(
            username="inactive-reconfirmer", email="inactive@example.invalid", password=None, is_active=False,
        )
        report = self.module.run()
        for changes in (
            {"actor_id": None}, {"actor_id": self.ordinary.pk}, {"actor_id": inactive.pk},
            {"actor_id": 999999}, {"reason": ""}, {"reason": "   "},
            {"attestation": ""}, {"attestation": self.module.ATTESTATION + " "},
            {"expected_count": None}, {"expected_count": 0}, {"expected_count": 2},
            {"expected_fingerprint": None}, {"expected_fingerprint": "f" * 64},
        ):
            with self.subTest(changes=changes):
                self.assert_refused_unchanged(report=report, **changes)

    def test_count_mismatch_blocks_all_pending_readings_and_never_treats_zero_as_absent(self):
        original = self.module.run()
        BiologicalMeasurement.objects.filter(pk=self.biology.pk).update(polyp_count=1)
        report = self.module.run()
        self.assertEqual((report["pending_count"], report["eligible_count"]), (1, 0))
        self.assertEqual([item["reading_id"] for item in report["blockers"]], [self.reading.pk])
        self.assertTrue(report["blockers"][0]["errors"])
        self.assertNotEqual(report["fingerprint"], original["fingerprint"])
        self.assert_refused_unchanged(report=report)
        BiologicalMeasurement.objects.filter(pk=self.biology.pk).update(polyp_count=0)
        self.assertEqual(self.module.run()["eligible_count"], 1)

    def test_mixed_eligible_and_blocked_batch_is_not_partially_applied(self):
        sql("UPDATE measurements_historicaltemperaturereading SET association_status='pending_review' WHERE id=%s", [self.confirmed.pk])
        BiologicalMeasurement.objects.filter(pk=self.biology.pk).update(ephyrae_count=1)
        report = self.module.run()
        self.assertEqual((report["pending_count"], report["eligible_count"]), (2, 1))
        self.assert_refused_unchanged(report=report)
        self.assert_refused_unchanged(report=report, expected_count=1)

    def test_confirmed_copy_rejects_cross_week_and_changed_box_identity(self):
        for changes, model, pk in (
            ({"measured_on": date(2026, 1, 8)}, BiologicalMeasurement, self.biology.pk),
            ({"week_start": date(2026, 1, 12)}, BiologicalMeasurement, self.biology.pk),
            ({"global_code": "ABC-DEF-1.002"}, Box, self.box.pk),
        ):
            with self.subTest(changes=changes):
                original = model.objects.filter(pk=pk).values(*changes).get()
                model.objects.filter(pk=pk).update(**changes)
                try:
                    report = self.module.run()
                    expected_ids = [self.reading.pk]
                    if model is Box:
                        # Changing identity also invalidates the confirmed week-2 row.
                        expected_ids.append(self.confirmed.pk)
                    self.assertEqual(report["pending_count"], len(expected_ids))
                    self.assertEqual(report["eligible_count"], 0)
                    self.assertEqual([item["reading_id"] for item in report["blockers"]], expected_ids)
                    self.assert_refused_unchanged(report=report)
                finally:
                    model.objects.filter(pk=pk).update(**original)
                restored = self.module.run()
                self.assertEqual(restored["blockers"], [])
                self.assertEqual(restored["eligible_count"], len(expected_ids))

    def corrupt_evidence_fixture(self, statement, parameters):
        # Deliberately model pre-existing corruption, never a supported write.
        # Only the verified fresh-cluster owner may bypass provenance guards.
        self.assert_disposable()
        with transaction.atomic():
            sql("ALTER TABLE measurements_historicaltemperatureevidence DISABLE TRIGGER USER")
            try:
                sql(statement, parameters)
            finally:
                sql("ALTER TABLE measurements_historicaltemperatureevidence ENABLE TRIGGER USER")

    def test_missing_retained_evidence_blocks_dry_run_and_apply(self):
        self.corrupt_evidence_fixture(
            "DELETE FROM measurements_historicaltemperatureevidence WHERE reading_id=%s", [self.reading.pk],
        )
        before = state()
        report = self.module.run()
        self.assertEqual((report["pending_count"], report["eligible_count"]), (1, 0))
        self.assertEqual([item["reading_id"] for item in report["blockers"]], [self.reading.pk])
        self.assertIn("Missing retained source evidence", json.dumps(report["blockers"]))
        self.assertEqual(state(), before)
        self.assert_refused_unchanged(report=report)

    def test_mismatched_literal_week_in_one_evidence_blocks_the_reading(self):
        evidence_id = self.reading.evidences.order_by("pk").last().pk
        self.corrupt_evidence_fixture(
            "UPDATE measurements_historicaltemperatureevidence SET week_value='2' WHERE id=%s", [evidence_id],
        )
        before = state()
        report = self.module.run()
        self.assertEqual((report["pending_count"], report["eligible_count"]), (1, 0))
        self.assertEqual([item["reading_id"] for item in report["blockers"]], [self.reading.pk])
        self.assertIn(f"Evidence {evidence_id}:", json.dumps(report["blockers"]))
        self.assertIn("Source week value mismatch", json.dumps(report["blockers"]))
        self.assertEqual(state(), before)
        self.assert_refused_unchanged(report=report)

    def test_altered_immutable_function_or_disabled_trigger_is_refused(self):
        definition = self.trigger_definition()
        report = self.module.run()
        for alter, restore in (
            (definition.replace("BEGIN", "BEGIN /* Synthetic guard-body drift */", 1), definition),
            ("ALTER TABLE measurements_historicaltemperaturereading DISABLE TRIGGER historical_reading_immutable",
             "ALTER TABLE measurements_historicaltemperaturereading ENABLE TRIGGER historical_reading_immutable"),
        ):
            with self.subTest(alter=alter):
                self.assert_disposable()
                sql(alter)
                try:
                    before = state()
                    altered_definition = self.trigger_definition()
                    with self.assertRaises(ValueError):
                        self.module.run()
                    self.assert_refused_unchanged(report=report)
                    self.assertEqual(state(), before)
                    self.assertEqual(self.trigger_definition(), altered_definition)
                finally:
                    sql(restore)
                self.assertEqual(self.trigger_definition(), definition)
                self.assertEqual(self.module.run(), report)
                self.assert_raw_reconfirmation_rejected(self.reading.pk)

    def test_conditional_or_column_limited_immutable_trigger_is_refused(self):
        self.assert_disposable()
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_get_triggerdef(oid) FROM pg_trigger "
                "WHERE tgrelid='public.measurements_historicaltemperaturereading'::regclass "
                "AND tgname='historical_reading_immutable'"
            )
            original = cursor.fetchone()[0]
        report = self.module.run()
        for events, condition in (
            ("UPDATE OR DELETE", "WHEN (false)"),
            ("UPDATE OF temperature_c OR DELETE", ""),
        ):
            with self.subTest(events=events, condition=condition):
                try:
                    with transaction.atomic():
                        sql("DROP TRIGGER historical_reading_immutable ON measurements_historicaltemperaturereading")
                        sql(
                            f"CREATE TRIGGER historical_reading_immutable BEFORE {events} "
                            "ON measurements_historicaltemperaturereading FOR EACH ROW "
                            f"{condition} EXECUTE FUNCTION historical_temperature_reading_immutable()"
                        )
                    before = state()
                    with self.assertRaises(ValueError):
                        self.module.run()
                    self.assert_refused_unchanged(report=report)
                    self.assertEqual(state(), before)
                finally:
                    with transaction.atomic():
                        sql("DROP TRIGGER IF EXISTS historical_reading_immutable ON measurements_historicaltemperaturereading")
                        sql(original)
                self.assertEqual(self.module.run(), report)
                self.assert_raw_reconfirmation_rejected(self.reading.pk)

    def test_replica_session_is_refused_before_dry_run_or_apply(self):
        self.assert_disposable()
        report = self.module.run()
        before = state()
        with connection.cursor() as cursor:
            cursor.execute("SHOW session_replication_role")
            self.assertEqual(cursor.fetchone()[0], "origin")
        try:
            sql("SET session_replication_role = replica")
            with self.assertRaises(ValueError):
                self.module.run()
            self.assert_refused_unchanged(report=report)
            self.assertEqual(state(), before)
        finally:
            sql("SET session_replication_role = origin")
        self.assertEqual(self.module.run(), report)
        self.assert_raw_reconfirmation_rejected(self.reading.pk)

    def test_every_evidence_is_checked_on_an_in_memory_confirmed_copy(self):
        seen_clean, seen_full, seen_reading = [], [], []
        original_clean = HistoricalTemperatureEvidence.clean
        original_full = HistoricalTemperatureEvidence.full_clean
        original_reading = HistoricalTemperatureReading.clean

        def clean(item):
            seen_clean.append((item.pk, item.reading.association_status))
            return original_clean(item)

        def full(item, *args, **kwargs):
            seen_full.append((item.pk, item.reading.association_status))
            return original_full(item, *args, **kwargs)

        def reading_clean(item):
            seen_reading.append((item.pk, item.association_status))
            return original_reading(item)

        with patch.object(HistoricalTemperatureEvidence, "clean", clean), \
                patch.object(HistoricalTemperatureEvidence, "full_clean", full), \
                patch.object(HistoricalTemperatureReading, "clean", reading_clean):
            report = self.module.run()
        self.assertEqual(report["eligible_count"], 1)
        ids = set(self.reading.evidences.values_list("pk", flat=True))
        self.assertEqual({pk for pk, status in seen_clean if status == "confirmed"}, ids)
        self.assertEqual({pk for pk, status in seen_full if status == "confirmed"}, ids)
        self.assertIn((self.reading.pk, "confirmed"), seen_reading)
        self.reading.refresh_from_db()
        self.assertEqual(self.reading.association_status, "pending_review")

    def test_one_invalid_evidence_blocks_even_when_another_evidence_matches(self):
        rejected_id = self.reading.evidences.order_by("pk").last().pk
        original = HistoricalTemperatureEvidence.clean

        def clean(item):
            original(item)
            if item.pk == rejected_id:
                raise ValidationError({"source_box": "Synthetic invalid second evidence"})

        with patch.object(HistoricalTemperatureEvidence, "clean", clean):
            report = self.module.run()
            self.assertEqual(report["eligible_count"], 0)
            self.assertIn("Synthetic invalid second evidence", json.dumps(report["blockers"]))
            self.assert_refused_unchanged(report=report)

    def test_canonical_pca_alias_is_eligible_without_rewriting_original_evidence(self):
        strain = Strain.objects.create(organization=self.org, species=self.box.strain.species, code="PCA-JKA-1")
        box = Box.objects.create(
            organization=self.org, strain=strain, global_code="PCA-JKA-1.005", box_number="005",
        )
        alias = self.make_reading(box, pending=True, source_box="PCA-CHO-1.05")
        before = list(alias.evidences.values())
        report = self.module.run()
        self.assertEqual((report["pending_count"], report["eligible_count"]), (2, 2))
        self.approved(report)
        alias.refresh_from_db()
        self.assertEqual(alias.association_status, "confirmed")
        self.assertEqual(list(alias.evidences.values()), before)

    def test_same_count_but_changed_snapshot_invalidates_fingerprint(self):
        report = self.module.run()
        BiologicalMeasurement.objects.filter(pk=self.biology.pk).update(measured_on=date(2026, 1, 2))
        current = self.module.run()
        self.assertEqual(current["pending_count"], report["pending_count"])
        self.assertEqual(current["eligible_count"], report["eligible_count"])
        self.assertNotEqual(current["fingerprint"], report["fingerprint"])
        self.assert_refused_unchanged(report=report)

    def test_audit_failure_rolls_back_status_and_restores_original_trigger(self):
        before, definition = state(), self.trigger_definition()
        report = self.module.run()
        reached = []

        def fail(**kwargs):
            reached.append(HistoricalTemperatureReading.objects.get(pk=self.reading.pk).association_status)
            raise RuntimeError("Synthetic late reconfirmation audit failure")

        with patch.object(AuditLog.objects, "create", side_effect=fail):
            with self.assertRaisesMessage(RuntimeError, "Synthetic late reconfirmation audit failure"):
                self.approved(report)
        self.assertEqual(reached, ["confirmed"])
        self.assertEqual(state(), before)
        self.assertEqual(self.trigger_definition(), definition)
        self.assert_raw_reconfirmation_rejected(self.reading.pk)
        self.approved(report)

    def test_trigger_restoration_failure_rolls_back_the_entire_apply(self):
        before, definition = state(), self.trigger_definition()
        report = self.module.run()
        attempted = []

        def execute(execute, statement, parameters, many, context):
            if statement.strip().rstrip(";") == definition.strip().rstrip(";"):
                attempted.append(statement)
                raise RuntimeError("Synthetic original trigger restoration failure")
            return execute(statement, parameters, many, context)

        with connection.execute_wrapper(execute):
            with self.assertRaisesMessage(RuntimeError, "Synthetic original trigger restoration failure"):
                self.approved(report)
        self.assertEqual(len(attempted), 1)
        self.assertEqual(state(), before)
        self.assertEqual(self.trigger_definition(), definition)
        self.assert_raw_reconfirmation_rejected(self.reading.pk)

    def test_temporary_guard_never_allows_unapproved_ids_or_payload_changes(self):
        before, definition = state(), self.trigger_definition()
        report = self.module.run()
        checked, installed = [], []

        def execute(execute, statement, parameters, many, context):
            result = execute(statement, parameters, many, context)
            if "CREATE OR REPLACE FUNCTION" not in statement.upper() or "$reconfirm$" not in statement:
                return result
            installed.append(statement)
            self.assertNotEqual(self.trigger_definition(), definition)
            self.assertEqual(
                HistoricalTemperatureReading.objects.get(pk=self.reading.pk).association_status,
                "pending_review",
            )
            for probe, values in (
                ("UPDATE measurements_historicaltemperaturereading SET temperature_c='99' WHERE id=%s", [self.reading.pk]),
                ("UPDATE measurements_historicaltemperaturereading SET association_status='confirmed', temperature_c='99' WHERE id=%s", [self.reading.pk]),
                ("UPDATE measurements_historicaltemperaturereading SET association_status='confirmed' WHERE id=%s", [self.foreign.pk]),
                ("DELETE FROM measurements_historicaltemperaturereading WHERE id=%s", [self.reading.pk]),
                ("TRUNCATE measurements_historicaltemperaturereading CASCADE", []),
            ):
                with self.assertRaises(DatabaseError), transaction.atomic():
                    sql(probe, values)
                checked.append(probe)
            # This org-1 reading was confirmed at snapshot time, so its ID must
            # not be in the temporary allowlist. Roll back its invalidation too.
            with transaction.atomic():
                sql("UPDATE measurements_historicaltemperaturereading SET association_status='pending_review' WHERE id=%s", [self.confirmed.pk])
                self.assert_raw_reconfirmation_rejected(self.confirmed.pk)
                checked.append("unapproved same-organization pending reading")
                transaction.set_rollback(True)
            self.assertEqual(state(), before)
            return result

        with connection.execute_wrapper(execute):
            result = self.approved(report)
        self.assertEqual(len(installed), 1)
        self.assertEqual(len(checked), 6)
        self.assertEqual(result["applied_count"], 1)
        self.reading.refresh_from_db()
        self.confirmed.refresh_from_db()
        self.foreign.refresh_from_db()
        self.assertEqual(self.reading.association_status, "confirmed")
        self.assertEqual(self.reading.temperature_c, "0.000")
        self.assertEqual(self.confirmed.association_status, "confirmed")
        self.assertEqual(self.foreign.association_status, "pending_review")
        self.assertEqual(self.trigger_definition(), definition)

    def test_snapshot_tables_are_locked_before_validation_without_disabling_triggers(self):
        original = HistoricalTemperatureReading.clean
        observed = []
        statements = []

        def clean(item):
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT c.relname FROM pg_locks l JOIN pg_class c ON c.oid=l.relation "
                    "WHERE l.pid=pg_backend_pid() AND l.granted AND l.mode IN "
                    "('ShareLock','ShareRowExclusiveLock','ExclusiveLock','AccessExclusiveLock')"
                )
                locked = {row[0] for row in cursor.fetchall()}
            self.assertTrue(set(SNAPSHOT_TABLES).issubset(locked), locked)
            observed.append(item.pk)
            return original(item)

        def execute(execute, statement, parameters, many, context):
            statements.append(statement.upper())
            return execute(statement, parameters, many, context)

        with connection.execute_wrapper(execute), patch.object(HistoricalTemperatureReading, "clean", clean):
            report = self.module.run()
            self.approved(report)
        self.assertTrue(observed)
        self.assertFalse(any("DISABLE TRIGGER" in text or ("SESSION_REPLICATION_ROLE" in text and not text.startswith("SHOW ")) for text in statements))

    def run_in_thread(self, function):
        outcome = {}

        def work():
            try:
                outcome["result"] = function()
            except BaseException as error:
                outcome["error"] = error
            finally:
                close_old_connections()
                connection.close()

        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        return thread, outcome

    def test_biological_writer_waits_until_the_validated_snapshot_is_committed(self):
        report = self.module.run()
        validating, release, writer_started = threading.Event(), threading.Event(), threading.Event()
        writer_pid = []
        original = HistoricalTemperatureReading.clean
        definition = self.trigger_definition()

        def clean(item):
            result = original(item)
            if item.pk == self.reading.pk and not validating.is_set():
                validating.set()
                if not release.wait(15):
                    raise RuntimeError("Synthetic snapshot barrier timed out")
            return result

        def write():
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                writer_pid.append(cursor.fetchone()[0])
            writer_started.set()
            BiologicalMeasurement.objects.filter(pk=self.biology.pk).update(ephyrae_count=1)

        writer = None
        with patch.object(HistoricalTemperatureReading, "clean", clean):
            applying, apply_outcome = self.run_in_thread(lambda: self.approved(report))
            try:
                self.assertTrue(validating.wait(10), apply_outcome)
                writer, write_outcome = self.run_in_thread(write)
                self.assertTrue(writer_started.wait(5))
                deadline = monotonic() + 5
                blocked = False
                while monotonic() < deadline:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT cardinality(pg_blocking_pids(%s)) > 0", [writer_pid[0]])
                        blocked = cursor.fetchone()[0]
                    if blocked:
                        break
                    sleep(0.02)
                self.assertTrue(blocked, "Biological writer bypassed the snapshot table lock")
            finally:
                release.set()
                applying.join(25)
                if writer is not None:
                    writer.join(25)
        self.assertFalse(applying.is_alive())
        self.assertFalse(writer.is_alive())
        self.assertNotIn("error", apply_outcome)
        self.assertNotIn("error", write_outcome)
        self.assertEqual(AuditLog.objects.filter(object_type="historical_temperature_reconfirmation").count(), 1)
        self.reading.refresh_from_db()
        self.assertEqual(self.reading.association_status, "pending_review")
        self.assertEqual(self.trigger_definition(), definition)
        self.assert_raw_reconfirmation_rejected(self.reading.pk)

    def test_two_concurrent_applies_have_exactly_one_success_and_one_audit(self):
        report = self.module.run()
        barrier = threading.Barrier(2, timeout=15)

        def apply():
            barrier.wait()
            return self.approved(report)

        first, one = self.run_in_thread(apply)
        second, two = self.run_in_thread(apply)
        first.join(30)
        second.join(30)
        self.assertFalse(first.is_alive() or second.is_alive(), "Concurrent apply did not finish")
        self.assertEqual(sum("result" in item for item in (one, two)), 1, (one, two))
        errors = [item["error"] for item in (one, two) if "error" in item]
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], REFUSALS + (DatabaseError,))
        self.assertEqual(AuditLog.objects.filter(object_type="historical_temperature_reconfirmation").count(), 1)
        self.reading.refresh_from_db()
        self.foreign.refresh_from_db()
        self.assertEqual(self.reading.association_status, "confirmed")
        self.assertEqual(self.foreign.association_status, "pending_review")
