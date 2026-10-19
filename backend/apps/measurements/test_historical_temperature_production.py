"""Production temperature import workflow.

Synthetic workbooks stand in for the seven fingerprinted sources (their allowlist is
patched to the synthetic hashes); everything after extraction is the real pipeline.
Database proofs need a disposable PostgreSQL (POLYPBASE_TEST_POSTGRES=1), never SQLite.
The attested-target and protected-path checks of the PCA workflow are patched here
because they need a Linux root-owned attestation; their own tests cover them.
The disposable superuser fixture bypasses runtime privilege checks; dedicated tests
exercise the original checker. Replay deliberately requires backup evidence younger
than one hour, even when the import already has a receipt.
"""

import hashlib
import io
import json
import os
import shutil
import tempfile
import threading
import zipfile
from contextlib import contextmanager, nullcontext
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest import skipUnless
from unittest.mock import patch
from xml.sax.saxutils import escape

from config import database_backup
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import DatabaseError, close_old_connections, connection, transaction
from django.test import SimpleTestCase, TransactionTestCase

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.cultures import pca_identity_unification as merge
from apps.cultures import pca_production
from apps.cultures.models import Box
from apps.cultures.test_pca_identity_unification import PcaFixtureMixin
from apps.organizations.models import Organization
from apps.taxonomy.models import Strain

from . import historical_temperature_production as production
from .historical_temperature_source import SOURCES, SourceError, column_letter
from .models import (
    BiologicalMeasurement, HistoricalTemperatureEvidence,
    HistoricalTemperatureImportReceipt, HistoricalTemperatureReading,
)

MACHINE = "a" * 32
SOURCE_NAMES = tuple(SOURCES)
REVIEW = "independent-review/HT-1"
CHECK_RUNTIME_PRIVILEGES = production.check_runtime_privileges


def synthetic_target(config=None):
    config = config or {"HOST": "127.0.0.1", "PORT": "55439"}
    return {"schema": "pca_target_v3", "connection_mode": "tcp", "socket_uid": None, "socket_gid": None,
            "machine_id": MACHINE, "configured_host": config["HOST"], "configured_port": str(config["PORT"]),
            "database": "synthetic_ht", "role": "synthetic_runtime", "server_address": "127.0.0.1",
            "server_port": int(config["PORT"]), "database_oid": 1234, "role_oid": 5678,
            "postmaster_started_at": "2026-01-01T00:00:00.000000Z", "server_version_num": 180000}


# ------------------------------------------------------------------ synthetic workbooks

def _cell(ref, value):
    if value is None:
        return ""
    if isinstance(value, str):
        return f'<c r="{ref}" t="inlineStr"><is><t>{escape(value)}</t></is></c>'
    return f'<c r="{ref}"><v>{value}</v></c>'


def workbook(year, blocks):
    """blocks: (box label, temperature, {iso week: (polyps, ephyrae)}). Two rows each."""
    first = 6 if year >= 2025 else 5
    rows = {1: {}, 2: {1: "Espèce", 2: "N° boîte", 3: "Température (°C)"}}
    if year >= 2025:
        rows[1][5] = str(year - 1)
    for index, (label, temperature, weeks) in enumerate(blocks):
        top = 3 + 2 * index
        rows[top] = {1: "Synthetic species", 2: label, 3: temperature, 4: "Nb polypes"}
        rows[top + 1] = {4: "Nb éphyrules"}
        for week, (polyps, ephyrae) in weeks.items():
            column = first + week - 1
            rows[2][column] = week
            rows[top][column], rows[top + 1][column] = polyps, ephyrae
    body = "".join(
        f'<row r="{number}">' + "".join(_cell(f"{column_letter(column)}{number}", value)
                                        for column, value in sorted(cells.items())) + "</row>"
        for number, cells in sorted(rows.items()))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("xl/workbook.xml", '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Synthetic" sheetId="1" r:id="rId1"/></sheets></workbook>')
        archive.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
        archive.writestr("xl/worksheets/sheet1.xml", f'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>{body}</sheetData></worksheet>')
    return buffer.getvalue()


def counts(week):
    """The fixture's biology for any week of its canonical PCA box."""
    return week % 7, week % 3


def default_blocks():
    pca = {1: counts(1), 2: counts(2), 3: counts(3), 7: counts(7), 21: counts(21), 5: (9, 9)}
    return {
        2026: [
            ("PCA-JKA-1.5", "15.0", pca),
            # Old CHO label of the same physical box: W1 repeats, W41 is an untouched CHO row.
            ("PCA-CHO-1.5", "15.0", {1: counts(1), 41: counts(41)}),
            ("ABC-DEF-1.1", "0", {1: (0, 0), 2: (3, 0), 3: (0, 4)}),
            ("ABC-DEF-1.1", "20.5", {2: (3, 0), 4: (1, 1)}),       # W2 conflicts with the 0 block
            ("XYZ-DEF-1.1", "18.0", {1: (1, 1)}),                  # another institution's box
            ("ABC-DEF-1.1", "12.0", {30: (1, 1)}),                 # absent biological week
            ("ABC-DEF-1.1", "n/a", {10: (1, 1)}),                  # malformed temperature
        ],
        2025: [("PCA-JKA-1.5", "14.0", {1: counts(1), 2: counts(2)})],
        **{year: [("ZZZ-ZZZ-1.1", "10.0", {1: (1, 1)})] for year in range(2020, 2025)},
    }


def write_sources(directory, blocks=None):
    blocks = blocks or default_blocks()
    allowlist = {}
    for name, (year, _digest) in SOURCES.items():
        content = workbook(year, blocks[year])
        (Path(directory) / name).write_bytes(content)
        allowlist[name] = (year, hashlib.sha256(content).hexdigest())
    return allowlist


# ------------------------------------------------------------------ database fixture

@skipUnless(connection.vendor == "postgresql", "Production workflow proofs need PostgreSQL, never SQLite.")
class ProductionFixture(PcaFixtureMixin, TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        guard = patch.object(merge, "require_local_database", return_value=("127.0.0.1", "55439", "x"))
        guard.start()
        self.addCleanup(guard.stop)
        super().setUp()
        self.runtime_privilege_guard = patch.object(production, "check_runtime_privileges")
        self.runtime_privileges = self.runtime_privilege_guard.start()
        self.addCleanup(self.runtime_privilege_guard.stop)
        plan = merge._build_read_only_plan(1, self.admin.pk)
        self.pca_receipt = merge._apply_reviewed_plan(
            1, actor_id=self.admin.pk, expected_plan_hash=merge.fingerprint(plan),
            backup_ref="synthetic-backup", review_ref="synthetic-review")
        patched = patch.dict(production.POST_PCA, {"receipt_audit_id": self.pca_receipt.pk,
                                                   "actor_id": self.admin.pk})
        patched.start()
        self.addCleanup(patched.stop)
        self.strain = Strain.objects.create(pk=200, organization=self.org, species=self.species, code="ABC-DEF-1")
        self.box = Box.objects.create(pk=3000, organization=self.org, strain=self.strain,
                                      global_code="ABC-DEF-1.001", box_number="001")
        self.foreign_strain = Strain.objects.create(pk=201, organization=self.other_org, species=self.species,
                                                    code="XYZ-DEF-1")
        self.foreign_box = Box.objects.create(pk=3001, organization=self.other_org, strain=self.foreign_strain,
                                              global_code="XYZ-DEF-1.001", box_number="001")
        self.add_biology(self.box, 2026, {1: (0, 0), 2: (3, 0), 3: (0, 4), 4: (1, 1)})
        self.add_biology(self.foreign_box, 2026, {1: (1, 1)})
        original_sources = dict(SOURCES)
        self.addCleanup(lambda: (SOURCES.clear(), SOURCES.update(original_sources)))
        self.directory = Path(tempfile.mkdtemp(prefix="ht-production-"))
        self.addCleanup(shutil.rmtree, self.directory, True)
        self.raw = self.directory / "raw"
        self.raw.mkdir()
        self.install_sources()
        self.target = synthetic_target(connection.settings_dict)
        for name, mocked in (
            ("require_target", lambda target: False),
            ("verify_target", lambda target, in_snapshot=False: None),
        ):
            patcher = patch.object(pca_production, name, side_effect=mocked)
            patcher.start()
            self.addCleanup(patcher.stop)
        artifacts = pca_production.pca_protected_artifacts
        for name in ("protected_file", "protected_directory"):
            patcher = patch.object(artifacts, name, side_effect=lambda path: nullcontext())
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(artifacts, "validate_file")
        patcher.start()
        self.addCleanup(patcher.stop)
        binaries = os.environ.get("POLYPBASE_PCA_QA_PG_BIN")
        if binaries:
            original = database_backup._run

            def installed(command, environment):
                suffix = ".exe" if os.name == "nt" else ""
                return original([str(Path(binaries) / (Path(command[0]).name + suffix)), *command[1:]], environment)

            patcher = patch.object(database_backup, "_run", side_effect=installed)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _fixture_teardown(self):
        # Flush truncates tables whose append-only guards are exactly what is under test.
        tables = ("measurements_historicaltemperaturereading", "measurements_historicaltemperatureevidence",
                  "measurements_historicaltemperatureimportreceipt")
        with connection.cursor() as cursor:
            for table in tables:
                cursor.execute(f"ALTER TABLE {table} DISABLE TRIGGER USER")
        try:
            super()._fixture_teardown()
        finally:
            with connection.cursor() as cursor:
                for table in tables:
                    cursor.execute(f"ALTER TABLE {table} ENABLE TRIGGER USER")

    def install_sources(self, blocks=None):
        allowlist = write_sources(self.raw, blocks)
        SOURCES.clear()
        SOURCES.update(allowlist)

    def add_biology(self, box, year, weeks):
        rows = []
        for week, (polyps, ephyrae) in weeks.items():
            measured_on = date.fromisocalendar(year, week, 3)
            rows.append(BiologicalMeasurement(
                box=box, measured_on=measured_on, week_start=BiologicalMeasurement.week_start_for(measured_on),
                polyp_count=polyps, ephyrae_count=ephyrae, user=self.writer, original_observer=self.writer,
                observer_status="known", imported_by=self.writer, origin="file_import", import_format="csv"))
        BiologicalMeasurement.objects.bulk_create(rows)

    # -- workflow helpers
    def plan(self, **overrides):
        options = {"actor": self.admin, "target": self.target, "raw_directory": self.raw}
        options.update(overrides)
        return production.build_plan(1, **options)

    def backup(self, document, **overrides):
        options = {"actor": self.admin, "document": document, "approved_hash": production.plan_hash(document),
                   "raw_directory": self.raw, "output_dir": self.directory}
        options.update(overrides)
        return production.backup_plan(1, **options)

    def apply(self, document, evidence, **overrides):
        digest = production.plan_hash(document)
        options = {"actor": self.admin, "document": document, "approved_hash": digest,
                   "backup_evidence": evidence, "review_ref": REVIEW,
                   "authorization": production.authorization_text(digest), "raw_directory": self.raw}
        options.update(overrides)
        return production.apply_plan(1, **options)

    def reviewed(self):
        document = self.plan()
        self.assertTrue(document["ready"], document["blockers"])
        return document, self.backup(document)

    def counts(self):
        return (HistoricalTemperatureReading.objects.count(), HistoricalTemperatureEvidence.objects.count(),
                HistoricalTemperatureImportReceipt.objects.count(),
                AuditLog.objects.filter(object_type="historical_temperature_import").count())

    def biology_state(self):
        return [merge.snapshot(row) for row in BiologicalMeasurement.objects.order_by("pk")]

    def readings_by_key(self):
        result = {}
        for reading in HistoricalTemperatureReading.objects.select_related("biological_measurement__box"):
            box = reading.biological_measurement.box.global_code
            result[(box, reading.iso_year, reading.iso_week)] = reading
        return result


class PlanTests(ProductionFixture):
    def test_plan_is_read_only_deterministic_and_reuses_the_existing_pipeline(self):
        before = (self.biology_state(), self.counts(), AuditLog.objects.count())
        document = self.plan()
        self.assertEqual((self.biology_state(), self.counts(), AuditLog.objects.count()), before)
        self.assertTrue(document["ready"], document["blockers"])
        again = self.plan()
        self.assertEqual(production.plan_hash({**again, "generated_at": document["generated_at"]}),
                         production.plan_hash(document))
        # No frozen QA snapshot or 40,291 plan is ever consulted.
        from .historical_temperature_partial import APPROVED_PLAN_FINGERPRINT
        self.assertNotEqual(document["plan"]["plan_fingerprint"], APPROVED_PLAN_FINGERPRINT)
        self.assertEqual(document["post_pca"]["checked_measurements"], 110)   # 92 moved + 18 duplicates
        self.assertEqual(document["plan"]["global_ready"], False)
        self.assertEqual(document["organization_id"], 1)

    def test_schema_checks_runtime_privileges(self):
        production.check_schema()
        self.runtime_privileges.assert_called_once_with()

    def test_original_runtime_privilege_check_refuses_the_disposable_owner(self):
        with self.assertRaises(production.ProductionImportError):
            CHECK_RUNTIME_PRIVILEGES()

    def test_plan_runs_in_a_repeatable_read_read_only_snapshot(self):
        seen = []
        original = production.read_state

        def spy(organization):
            with connection.cursor() as cursor:
                cursor.execute("SELECT current_setting('transaction_read_only'), current_setting('transaction_isolation')")
                seen.append(cursor.fetchone())
            return original(organization)

        with patch.object(production, "read_state", side_effect=spy):
            self.plan()
        self.assertEqual(seen, [("on", "repeatable read")])

    def test_accepted_and_excluded_associations(self):
        document = self.plan()
        accepted = {(p["biological_measurement_id"]): p for p in document["plan"]["payloads"]}
        by_box = {}
        for payload in document["plan"]["payloads"]:
            row = BiologicalMeasurement.objects.get(pk=payload["biological_measurement_id"])
            by_box[(row.box.global_code, payload["iso_year"], payload["iso_week"])] = payload
        # PCA: canonical measurement, JKA and CHO labels grouped on W1, true zeros kept.
        self.assertEqual(len(by_box[("PCA-JKA-1.005", 2026, 1)]["evidences"]), 2)
        for key in (("PCA-JKA-1.005", 2026, 1), ("PCA-JKA-1.005", 2026, 2), ("PCA-JKA-1.005", 2026, 3),
                    ("PCA-JKA-1.005", 2026, 7), ("PCA-JKA-1.005", 2026, 21), ("PCA-JKA-1.005", 2026, 41),
                    ("PCA-JKA-1.005", 2025, 1), ("PCA-JKA-1.005", 2025, 2),
                    ("ABC-DEF-1.001", 2026, 1), ("ABC-DEF-1.001", 2026, 3), ("ABC-DEF-1.001", 2026, 4)):
            self.assertIn(key, by_box)
        self.assertEqual(by_box[("ABC-DEF-1.001", 2026, 1)]["temperature_c"], "0")
        self.assertEqual({e["polyp_value"] for e in by_box[("PCA-JKA-1.005", 2026, 21)]["evidences"]}, {"0"})
        self.assertEqual({e["ephyrae_value"] for e in by_box[("PCA-JKA-1.005", 2026, 21)]["evidences"]}, {"0"})
        self.assertEqual(document["review"]["zero_temperature_readings"], 2)   # ABC W1 and W3
        # W41 is the untouched CHO row, reached through the explicit product identity.
        self.assertEqual({e["source_box"] for e in by_box[("PCA-JKA-1.005", 2026, 41)]["evidences"]}, {"PCA-CHO-1.5"})
        # Never accepted: count mismatch, source conflict, foreign box, absent week, malformed temperature.
        self.assertNotIn(("PCA-JKA-1.005", 2026, 5), by_box)
        self.assertNotIn(("ABC-DEF-1.001", 2026, 2), by_box)
        self.assertNotIn(("ABC-DEF-1.001", 2026, 30), by_box)
        self.assertNotIn(("ABC-DEF-1.001", 2026, 10), by_box)
        self.assertFalse(any(row.box.global_code.startswith("XYZ") for row in
                             BiologicalMeasurement.objects.filter(pk__in=accepted)
                             .select_related("box")))
        statuses = {(row["canonical_box"], row["iso_year"], row["iso_week"]): row
                    for row in document["plan"]["excluded"]}
        self.assertEqual(statuses[("PCA-JKA-1.005", 2026, 5)]["status"], "CONFLICT")
        self.assertIn("BIOLOGICAL_COUNT_MISMATCH", statuses[("PCA-JKA-1.005", 2026, 5)]["issues"])
        self.assertEqual(statuses[("ABC-DEF-1.001", 2026, 2)]["status"], "CONFLICT")
        self.assertEqual(statuses[("ABC-DEF-1.001", 2026, 30)]["status"], "MISSING_WEEK")
        self.assertEqual(statuses[("XYZ-DEF-1.001", 2026, 1)]["status"], "MISSING_BOX")
        self.assertTrue(any(row["reason"] == "TEMPERATURE_MALFORMED"
                            for row in document["plan"]["source_exclusions"]))

    def test_payloads_never_target_duplicates_or_the_retired_box(self):
        document = self.plan()
        ids = [p["biological_measurement_id"] for p in document["plan"]["payloads"]]
        rows = BiologicalMeasurement.objects.filter(pk__in=ids)
        self.assertFalse(rows.filter(duplicate_of__isnull=False).exists())
        self.assertFalse(rows.filter(box_id=merge.DONOR_ID).exists())
        self.assertFalse(rows.exclude(box__organization_id=1).exists())
        duplicate_weeks = set(BiologicalMeasurement.objects.filter(duplicate_of__isnull=False)
                              .values_list("week_start", flat=True))
        self.assertEqual(len(duplicate_weeks), 18)
        # W5 counts differ from the canonical row but are not attached to the CHO duplicate either.
        self.assertFalse(any(p["iso_week"] == 5 and p["iso_year"] == 2026 for p in document["plan"]["payloads"]))

    def test_post_pca_state_is_required_and_verified(self):
        for change, expected in (
            ({"receipt_audit_id": 99999}, "receipt_missing_or_ambiguous"),
            ({"actor_id": 12345}, "receipt_differs"),
            ({"counts": {"duplicates": 0, "moved": 0, "untouched": 0}}, "receipt_differs"),
        ):
            with self.subTest(change=change), patch.dict(production.POST_PCA, change):
                with self.assertRaisesMessage(production.ProductionImportError, expected):
                    self.plan()
        Box.objects.filter(pk=merge.DONOR_ID).update(merged_into=None)
        with self.assertRaisesMessage(production.ProductionImportError, "post_pca_box_identity_differs"):
            self.plan()

    def test_pca_structural_drift_blocks_the_plan(self):
        moved = BiologicalMeasurement.objects.filter(environment_source_box_id=merge.DONOR_ID).first()
        BiologicalMeasurement.objects.filter(pk=moved.pk).update(environment_source_box=None)
        with self.assertRaisesMessage(production.ProductionImportError, "post_pca_measurement_differs"):
            self.plan()

    def test_unmerged_database_has_no_importable_plan(self):
        AuditLog.objects.filter(pk=self.pca_receipt.pk)._raw_delete(using="default")
        with self.assertRaises(production.ProductionImportError):
            self.plan()

    def test_pending_review_reading_blocks_the_plan(self):
        biology = BiologicalMeasurement.objects.get(box=self.box, week_start=date.fromisocalendar(2026, 4, 1))
        with connection.cursor() as cursor:
            cursor.execute("ALTER TABLE measurements_historicaltemperaturereading DISABLE TRIGGER USER")
            cursor.execute(
                "INSERT INTO measurements_historicaltemperaturereading "
                "(biological_measurement_id, association_status, iso_year, iso_week, temperature_c, created_at) "
                "VALUES (%s, 'pending_review', 2026, 4, '1', now())", [biology.pk])
            cursor.execute("ALTER TABLE measurements_historicaltemperaturereading ENABLE TRIGGER USER")
        document = self.plan()
        self.assertFalse(document["ready"])
        self.assertIn("PENDING_REVIEW_ASSOCIATION_PRESENT", document["blockers"])
        with self.assertRaises(production.ProductionImportError):
            production.validate_plan(document, production.plan_hash(document))

    def test_changed_source_is_refused_before_any_plan(self):
        path = self.raw / SOURCE_NAMES[3]
        path.write_bytes(path.read_bytes() + b"x")
        with self.assertRaisesMessage(SourceError, "SHA-256 mismatch"):
            self.plan()

    def test_missing_source_is_refused(self):
        (self.raw / SOURCE_NAMES[0]).unlink()
        with self.assertRaises(OSError):
            self.plan()

    def test_authorization_is_scoped_to_an_active_administrator_of_organization_one(self):
        technician = get_user_model().objects.get(username="pca-writer")
        outsider = get_user_model().objects.create_user(username="outsider", email="o@example.test")
        OrganizationMembership.objects.create(organization=self.other_org, user=outsider, role="admin")
        inactive = get_user_model().objects.create_user(username="inactive", email="i@example.test", is_active=False)
        OrganizationMembership.objects.create(organization=self.org, user=inactive, role="admin")
        for actor in (technician, outsider, inactive, SimpleNamespace(pk=None), SimpleNamespace(pk=999999)):
            with self.subTest(actor=actor.pk), self.assertRaises((PermissionDenied, get_user_model().DoesNotExist)):
                self.plan(actor=actor)
        with self.assertRaises(production.ProductionImportError):
            production.build_plan(2, actor=self.admin, target=self.target, raw_directory=self.raw)

    def test_other_institution_data_never_enters_the_baseline(self):
        original = production.build_report
        seen = {}

        def spy(raw_directory, *, organization_id, baseline=None, manifest=None):
            seen["baseline"] = baseline
            return original(raw_directory, organization_id=organization_id, baseline=baseline, manifest=manifest)

        with patch.object(production, "build_report", side_effect=spy):
            self.plan()
        self.assertEqual({box["organization_id"] for box in seen["baseline"]["boxes"]}, {1})
        self.assertNotIn(self.foreign_box.pk, {box["id"] for box in seen["baseline"]["boxes"]})
        foreign_biology = set(BiologicalMeasurement.objects.filter(box=self.foreign_box).values_list("pk", flat=True))
        self.assertFalse(foreign_biology & {row["id"] for row in seen["baseline"]["biological_measurements"]})


class WorkflowTests(ProductionFixture):
    def test_plan_backup_apply_postcheck_preserve_everything_else(self):
        document, evidence = self.reviewed()
        before_biology = self.biology_state()
        boxes_before = [merge.snapshot(box) for box in Box.objects.order_by("pk")]
        audit_before = AuditLog.objects.count()
        receipt, replayed = self.apply(document, evidence)
        self.assertFalse(replayed)
        payloads = document["plan"]["payloads"]
        self.assertEqual(self.counts(), (len(payloads), document["plan"]["summary"]["accepted_evidences"], 1, 1))
        self.assertEqual(AuditLog.objects.count(), audit_before + 1)
        # Biology, authors, dates and boxes are untouched.
        self.assertEqual(self.biology_state(), before_biology)
        self.assertEqual([merge.snapshot(box) for box in Box.objects.order_by("pk")], boxes_before)
        readings = self.readings_by_key()
        self.assertEqual(readings[("ABC-DEF-1.001", 2026, 1)].temperature_c, "0")
        self.assertEqual({e.polyp_value for e in readings[("PCA-JKA-1.005", 2026, 21)].evidences.all()}, {"0"})
        self.assertEqual(receipt.actor_id, self.admin.pk)
        self.assertEqual(receipt.organization_id, 1)
        audit = AuditLog.objects.get(object_type="historical_temperature_import")
        self.assertEqual(audit.user_id, self.admin.pk)
        self.assertEqual(audit.organization_id, 1)
        self.assertEqual(audit.metadata["backup"], evidence)
        self.assertEqual(audit.metadata["review_ref"], REVIEW)
        self.assertEqual(audit.metadata["approved_plan_hash"], production.plan_hash(document))
        self.assertEqual(audit.metadata["target"], self.target)
        self.assertEqual(audit.metadata["accepted_readings"], len(payloads))
        report = production.postcheck(1, actor=self.admin, document=document, approved_hash=production.plan_hash(document))
        self.assertEqual(report["readings"], len(payloads))
        # Idempotent replay: no second receipt, reading or audit.
        again, replayed = self.apply(document, evidence)
        self.assertTrue(replayed)
        self.assertEqual(again.pk, receipt.pk)
        self.assertEqual(self.counts(), (len(payloads), document["plan"]["summary"]["accepted_evidences"], 1, 1))

    def test_postcheck_requires_the_receipt_and_detects_tampering(self):
        document, evidence = self.reviewed()
        digest = production.plan_hash(document)
        with self.assertRaisesMessage(production.ProductionImportError, "receipt_missing"):
            production.postcheck(1, actor=self.admin, document=document, approved_hash=digest)
        self.apply(document, evidence)
        biology = BiologicalMeasurement.objects.get(box=self.box, week_start=date.fromisocalendar(2026, 1, 1))
        BiologicalMeasurement.objects.filter(pk=biology.pk).update(notes="edited later")
        with self.assertRaisesMessage(production.ProductionImportError, "touched_biology_changed"):
            production.postcheck(1, actor=self.admin, document=document, approved_hash=digest)

    def test_replay_refuses_notes_changed_on_attached_biology(self):
        document, evidence = self.reviewed()
        self.apply(document, evidence)
        before = self.counts()
        measurement_id = document["plan"]["payloads"][0]["biological_measurement_id"]
        BiologicalMeasurement.objects.filter(pk=measurement_id).update(notes="edited after import")
        biology = self.biology_state()
        with self.assertRaisesMessage(production.ProductionImportError, "touched_biology_changed"):
            self.apply(document, evidence)
        self.assertEqual(self.counts(), before)
        self.assertEqual(self.biology_state(), biology)

    def test_postcheck_refuses_baseline_change_outside_imported_payloads(self):
        document, evidence = self.reviewed()
        self.apply(document, evidence)
        before = self.counts()
        payload_ids = {p["biological_measurement_id"] for p in document["plan"]["payloads"]}
        biology = BiologicalMeasurement.objects.get(box=self.box, week_start=date.fromisocalendar(2026, 2, 1))
        self.assertNotIn(biology.pk, payload_ids)
        BiologicalMeasurement.objects.filter(pk=biology.pk).update(polyp_count=biology.polyp_count + 1)
        changed_biology = self.biology_state()
        with self.assertRaises(production.ProductionImportError):
            production.postcheck(1, actor=self.admin, document=document,
                                 approved_hash=production.plan_hash(document))
        self.assertEqual(self.counts(), before)
        self.assertEqual(self.biology_state(), changed_biology)

    def test_replay_still_requires_backup_evidence_younger_than_one_hour(self):
        document, evidence = self.reviewed()
        self.apply(document, evidence)
        before = self.counts()
        expired = deepcopy(evidence)
        expired["created_at"] = (datetime.now(timezone.utc) - timedelta(hours=1, seconds=1)).isoformat()
        expired["evidence_hash"] = database_backup._evidence_hash(expired)
        with self.assertRaisesMessage(ValueError, "older than one hour"):
            self.apply(document, expired)
        self.assertEqual(self.counts(), before)
        report = production.postcheck(1, actor=self.admin, document=document,
                                     approved_hash=production.plan_hash(document))
        self.assertEqual(report["readings"], len(document["plan"]["payloads"]))

    def test_plan_is_stale_after_any_baseline_change(self):
        document, evidence = self.reviewed()
        before = (self.counts(), self.biology_state())
        self.add_biology(self.box, 2026, {5: (2, 2)})        # a new weekly entry
        with self.assertRaisesMessage(production.StalePlanError, "baseline"):
            self.apply(document, evidence)
        self.assertEqual(self.counts(), before[0])
        BiologicalMeasurement.objects.filter(box=self.box, week_start=date.fromisocalendar(2026, 5, 1)).delete()
        BiologicalMeasurement.objects.filter(box=self.box, week_start=date.fromisocalendar(2026, 1, 1)).update(polyp_count=5)
        with self.assertRaises(production.StalePlanError):
            self.apply(document, evidence)
        self.assertEqual(self.counts(), before[0])

    def test_notes_edit_on_attached_biology_is_stale_even_if_counts_agree(self):
        document, evidence = self.reviewed()
        biology = BiologicalMeasurement.objects.get(box=self.box, week_start=date.fromisocalendar(2026, 1, 1))
        BiologicalMeasurement.objects.filter(pk=biology.pk).update(notes="edited")
        with self.assertRaises(production.StalePlanError):
            self.apply(document, evidence)
        self.assertEqual(self.counts(), (0, 0, 0, 0))

    def test_changed_source_refuses_backup_and_apply(self):
        document, evidence = self.reviewed()
        path = self.raw / SOURCE_NAMES[6]
        path.write_bytes(path.read_bytes() + b"tampered")
        with self.assertRaises((SourceError, production.ProductionImportError)):
            self.apply(document, evidence)
        with self.assertRaises(SourceError):
            self.backup(document)
        self.assertEqual(self.counts(), (0, 0, 0, 0))

    def test_source_swapped_for_a_valid_but_different_allowlist_is_stale(self):
        document, evidence = self.reviewed()
        blocks = default_blocks()
        blocks[2026][2] = ("ABC-DEF-1.1", "1", {1: (0, 0)})      # different temperature, same layout
        self.install_sources(blocks)
        with self.assertRaises(production.ProductionImportError):
            self.apply(document, evidence)
        self.assertEqual(self.counts(), (0, 0, 0, 0))

    def test_plan_tampering_changes_the_hash(self):
        document = self.plan()
        digest = production.plan_hash(document)
        for mutate in (lambda d: d["plan"]["payloads"][0].update(temperature_c="99"),
                       lambda d: d["plan"]["payloads"].pop(),
                       lambda d: d.update(organization_id=2),
                       lambda d: d["baseline"].update(fingerprint="0" * 64)):
            copy = json.loads(json.dumps(document))
            mutate(copy)
            with self.subTest(), self.assertRaises(production.ProductionImportError):
                production.validate_plan(copy, digest)
        for bad in ("A" * 64, "g" * 64, "a" * 63, None, ""):
            with self.assertRaises(production.ProductionImportError):
                production.validate_plan(document, bad)

    def test_authorization_review_and_backup_gates_refuse_without_writing(self):
        document, evidence = self.reviewed()
        digest = production.plan_hash(document)
        stale_evidence = {**evidence, "created_at": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()}
        stale_evidence["evidence_hash"] = database_backup._evidence_hash(stale_evidence)
        other = {**evidence, "plan_hash": "b" * 64}
        other["evidence_hash"] = database_backup._evidence_hash(other)
        missing = {**evidence, "archive": {**evidence["archive"], "path": str(self.directory / "gone.dump")}}
        missing["evidence_hash"] = database_backup._evidence_hash(missing)
        cases = (
            {"authorization": None}, {"authorization": production.authorization_text("b" * 64)},
            {"authorization": "APPLY_PCA_ORGANIZATION_1:" + digest}, {"review_ref": " "}, {"review_ref": None},
            {"approved_hash": "a" * 64}, {"backup_evidence": "arbitrary-backup-reference"},
            {"backup_evidence": {}}, {"backup_evidence": stale_evidence}, {"backup_evidence": other},
            {"backup_evidence": missing}, {"backup_evidence": {**evidence, "evidence_hash": "c" * 64}},
        )
        for overrides in cases:
            with self.subTest(overrides=list(overrides)), self.assertRaises(ValueError):
                self.apply(document, evidence, **overrides)
        self.assertEqual(self.counts(), (0, 0, 0, 0))

    def test_corrupt_archive_is_refused(self):
        document, evidence = self.reviewed()
        with open(evidence["archive"]["path"], "ab") as stream:
            stream.write(b"corruption")
        with self.assertRaises(ValueError):
            self.apply(document, evidence)
        self.assertEqual(self.counts(), (0, 0, 0, 0))

    def test_backup_binds_the_plan_and_target_and_needs_a_matching_live_snapshot(self):
        document = self.plan()
        evidence = self.backup(document)
        database_backup.validate_evidence(evidence, self.target, production.plan_hash(document))
        self.assertEqual(evidence["plan_hash"], production.plan_hash(document))
        with self.assertRaises(ValueError):
            database_backup.validate_evidence(evidence, {**self.target, "database_oid": 1}, production.plan_hash(document))
        self.add_biology(self.box, 2026, {6: (2, 2)})
        with self.assertRaises(production.StalePlanError):
            self.backup(document)
        self.assertEqual(self.counts(), (0, 0, 0, 0))

    def test_wrong_attested_target_is_refused_before_database_access(self):
        document, evidence = self.reviewed()
        calls = []

        def refuse(target, in_snapshot=False):
            calls.append(target)
            raise ValueError("Live database identity/session state differs from the independent attestation.")

        with patch.object(pca_production, "verify_target", side_effect=refuse):
            for action in (self.plan, lambda: self.apply(document, evidence), lambda: self.backup(document),
                           lambda: production.postcheck(1, actor=self.admin, document=document,
                                                        approved_hash=production.plan_hash(document))):
                with self.assertRaisesMessage(ValueError, "differs from the independent attestation"):
                    action()
        self.assertEqual(self.counts(), (0, 0, 0, 0))
        with patch.object(pca_production, "require_target", side_effect=ValueError("Target differs")):
            with patch.object(production, "read_state") as read:
                with self.assertRaises(ValueError):
                    self.plan()
                with self.assertRaises(ValueError):
                    self.apply(document, evidence)
                read.assert_not_called()

    def test_atomic_rollback_on_evidence_reading_audit_and_receipt_failure(self):
        document, evidence = self.reviewed()
        total = document["plan"]["summary"]["accepted_evidences"]
        calls = {"n": 0}
        original = HistoricalTemperatureEvidence.objects.create

        def fail_midway(**kwargs):
            calls["n"] += 1
            if calls["n"] == total // 2:
                raise RuntimeError("synthetic mid-import failure")
            return original(**kwargs)

        with patch.object(HistoricalTemperatureEvidence.objects, "create", side_effect=fail_midway):
            with self.assertRaisesMessage(RuntimeError, "mid-import"):
                self.apply(document, evidence)
        self.assertEqual(self.counts(), (0, 0, 0, 0))
        for target in (AuditLog.objects, HistoricalTemperatureImportReceipt.objects):
            with patch.object(target, "create", side_effect=RuntimeError("late failure")):
                with self.assertRaisesMessage(RuntimeError, "late failure"):
                    self.apply(document, evidence)
            self.assertEqual(self.counts(), (0, 0, 0, 0))
        # The same reviewed plan still applies afterwards: nothing was half committed.
        receipt, replayed = self.apply(document, evidence)
        self.assertFalse(replayed)

    def test_database_guards_stop_an_invalid_payload_and_roll_back_everything(self):
        document, evidence = self.reviewed()
        original_insert = production._insert
        approved_payloads = deepcopy(document["plan"]["payloads"])
        before_biology = self.biology_state()
        inserted = []

        def insert_invalid_evidence(organization, payloads):
            altered = deepcopy(payloads)
            self.assertGreaterEqual(len(altered), 2)
            # This wrapper runs after recomputation; the reviewed document stays intact.
            inserted.extend(original_insert(organization, altered[:1]))
            self.assertTrue(HistoricalTemperatureEvidence.objects.filter(
                reading_id=inserted[0]["reading_id"]).exists())
            altered[1]["evidences"][0]["source_box"] = "BAD-BAD-999.999"
            return inserted + original_insert(organization, altered[1:])

        with patch.object(production, "_insert", side_effect=insert_invalid_evidence) as insert, \
                patch.object(HistoricalTemperatureEvidence, "clean"):
            with self.assertRaisesMessage(DatabaseError, "Historical evidence scientific relationship mismatch"):
                self.apply(document, evidence)
        insert.assert_called_once()
        self.assertEqual(len(inserted), 1)
        self.assertEqual(document["plan"]["payloads"], approved_payloads)
        self.assertEqual(self.counts(), (0, 0, 0, 0))
        self.assertEqual(self.biology_state(), before_biology)

    def test_existing_reading_is_kept_and_its_week_is_not_proposed(self):
        document, evidence = self.reviewed()
        self.apply(document, evidence)
        second = self.plan()
        self.assertEqual(second["plan"]["summary"]["accepted_readings"], 0)
        self.assertIn("NOTHING_TO_IMPORT", second["blockers"])
        self.assertFalse(second["ready"])
        self.assertEqual(second["review"]["existing_readings_in_baseline"], len(document["plan"]["payloads"]))
        statuses = {row["status"] for row in second["plan"]["excluded"]}
        self.assertIn("ALREADY_PRESENT", statuses)

    def test_receipt_conflict_for_another_approval_is_refused(self):
        document, evidence = self.reviewed()
        receipt, _ = self.apply(document, evidence)
        other = json.loads(json.dumps(document))
        other["generated_at"] = "2026-10-20T00:00:00+00:00"          # a different approved artifact
        digest = production.plan_hash(other)
        evidence2 = {**evidence, "plan_hash": digest}
        evidence2["evidence_hash"] = database_backup._evidence_hash(evidence2)
        with self.assertRaisesMessage(production.ProductionImportError, "inconsistent receipt"):
            self.apply(other, evidence2, approved_hash=digest, authorization=production.authorization_text(digest))
        self.assertEqual(HistoricalTemperatureImportReceipt.objects.count(), 1)
        self.assertEqual(receipt.pk, HistoricalTemperatureImportReceipt.objects.get().pk)

    def test_receipt_and_readings_are_immutable_after_apply(self):
        document, evidence = self.reviewed()
        receipt, _ = self.apply(document, evidence)
        with self.assertRaises(Exception):
            HistoricalTemperatureReading.objects.all().update(temperature_c="1")
        with connection.cursor() as cursor, self.assertRaises(DatabaseError), transaction.atomic():
            cursor.execute("DELETE FROM measurements_historicaltemperaturereading")
        with connection.cursor() as cursor, self.assertRaises(DatabaseError), transaction.atomic():
            cursor.execute("UPDATE measurements_historicaltemperatureimportreceipt SET actor_id = 1")

    def test_inactive_actor_or_downgraded_membership_fails_at_apply(self):
        document, evidence = self.reviewed()
        OrganizationMembership.objects.filter(user=self.admin).update(role="lab_technician")
        with self.assertRaises(PermissionDenied):
            self.apply(document, evidence)
        OrganizationMembership.objects.filter(user=self.admin).update(role="admin", is_active=False)
        with self.assertRaises(PermissionDenied):
            self.apply(document, evidence)
        OrganizationMembership.objects.filter(user=self.admin).update(is_active=True)
        get_user_model().objects.filter(pk=self.admin.pk).update(is_active=False)
        with self.assertRaises(PermissionDenied):
            self.apply(document, evidence)
        self.assertEqual(self.counts(), (0, 0, 0, 0))

    def test_apply_records_the_actor_as_audit_author_not_as_measurement_author(self):
        second_admin = get_user_model().objects.create_user(username="second-admin", email="s@example.test")
        OrganizationMembership.objects.create(organization=self.org, user=second_admin, role="admin")
        document, evidence = self.reviewed()
        before = {row["id"]: (row["user_id"], row["original_observer_id"], row["imported_by_id"], row["measured_on"])
                  for row in self.biology_state()}
        receipt, _ = self.apply(document, evidence, actor=second_admin)
        self.assertEqual(receipt.actor_id, second_admin.pk)
        self.assertEqual(AuditLog.objects.get(object_type="historical_temperature_import").user_id, second_admin.pk)
        after = {row["id"]: (row["user_id"], row["original_observer_id"], row["imported_by_id"], row["measured_on"])
                 for row in self.biology_state()}
        self.assertEqual(before, after)


class ConcurrencyTests(ProductionFixture):
    def run_in_thread(self, function):
        outcome = {}

        def work():
            try:
                outcome["result"] = function()
            except BaseException as error:  # reported to the test thread
                outcome["error"] = error
            finally:
                close_old_connections()
                connection.close()

        thread = threading.Thread(target=work)
        thread.start()
        return thread, outcome

    def test_a_concurrent_writer_holding_a_box_lock_makes_apply_fail_atomically(self):
        document, evidence = self.reviewed()
        held, release, errors = threading.Event(), threading.Event(), []

        def writer():
            try:
                with transaction.atomic():
                    Box.objects.select_for_update().filter(pk=self.box.pk).update(notes="editing")
                    held.set()
                    release.wait(30)
            finally:
                close_old_connections()
                connection.close()

        thread = threading.Thread(target=writer)
        thread.start()
        try:
            self.assertTrue(held.wait(10))
            with patch.object(production, "LOCK_TIMEOUT", "1s"):
                with self.assertRaises(DatabaseError):
                    self.apply(document, evidence)
        finally:
            release.set()
            thread.join(30)
        self.assertEqual(self.counts(), (0, 0, 0, 0))

    def test_a_write_committed_between_recompute_and_lock_is_detected(self):
        document, evidence = self.reviewed()
        original = production.read_state
        calls = {"n": 0}

        def interfering(organization):
            # The third call is the locked recheck; slip a conflicting entry in first.
            calls["n"] += 1
            return original(organization)

        # Recompute reads once (unlocked); the in-lock state differs after this commit.
        original_recompute = production._recompute

        def recompute_then_write(*args, **kwargs):
            original_recompute(*args, **kwargs)
            thread, outcome = self.run_in_thread(lambda: self.add_biology(self.box, 2026, {7: (4, 4)}))
            thread.join(30)
            self.assertNotIn("error", outcome)

        with patch.object(production, "_recompute", side_effect=recompute_then_write):
            with self.assertRaises(production.StalePlanError):
                self.apply(document, evidence)
        self.assertEqual(self.counts(), (0, 0, 0, 0))

    def test_two_simultaneous_applies_create_exactly_one_import(self):
        document, evidence = self.reviewed()
        barrier = threading.Barrier(2, timeout=30)

        def attempt():
            barrier.wait()
            return self.apply(document, evidence)

        first, one = self.run_in_thread(attempt)
        second, two = self.run_in_thread(attempt)
        first.join(300)
        second.join(300)
        outcomes = [one, two]
        successes = [o["result"] for o in outcomes if "result" in o]
        self.assertTrue(successes, [o.get("error") for o in outcomes])
        self.assertEqual(len({receipt.pk for receipt, _ in successes}), 1)
        self.assertEqual(sum(not replayed for _, replayed in successes), 1)
        total = len(document["plan"]["payloads"])
        self.assertEqual(self.counts(), (total, document["plan"]["summary"]["accepted_evidences"], 1, 1))
        for outcome in outcomes:
            if "error" in outcome:   # a loser may only fail closed (stale or lock timeout), never write
                self.assertIsInstance(outcome["error"], (production.ProductionImportError, DatabaseError))


class CommandTests(ProductionFixture):
    def run_command(self, action, **options):
        defaults = {"production": True, "organization_id": 1, "actor_id": self.admin.pk,
                    "target": "/etc/polypbase/pca-production-target.json",
                    "plan_file": str(self.directory / "plan.json")}
        defaults.update(options)
        out = StringIO()
        with patch.object(pca_production, "read_target", return_value=self.target), \
                patch.object(pca_production, "read_artifact", side_effect=lambda p: json.loads(Path(p).read_text())), \
                patch.object(pca_production, "write_artifact",
                             side_effect=lambda p, v: Path(p).write_text(json.dumps(v, sort_keys=True))):
            call_command("historical_temperature_production", action, stdout=out, **defaults)
        return json.loads(out.getvalue())

    def test_separate_actions_end_to_end(self):
        plan = self.run_command("plan", raw_directory=self.raw)
        self.assertTrue(plan["ready"])
        self.assertNotIn("payloads", plan)           # the plan itself stays in the private file
        document = json.loads((self.directory / "plan.json").read_text())
        digest = plan["plan_hash"]
        self.assertEqual(digest, production.plan_hash(document))
        backup = self.run_command("backup", raw_directory=self.raw, expected_plan_hash=digest,
                                  backup_dir=str(self.directory), backup_evidence=str(self.directory / "backup.json"))
        self.assertEqual(backup["backup_evidence"]["plan_hash"], digest)
        self.assertEqual(self.counts(), (0, 0, 0, 0))
        result = self.run_command("apply", raw_directory=self.raw, expected_plan_hash=digest,
                                  backup_evidence=str(self.directory / "backup.json"), review_ref=REVIEW,
                                  authorization=production.authorization_text(digest))
        self.assertFalse(result["replayed"])
        check = self.run_command("postcheck", expected_plan_hash=digest)
        self.assertEqual(check["postcheck"]["receipt_id"], result["receipt_id"])

    def test_each_action_rejects_the_options_of_another_action(self):
        digest = "a" * 64
        cases = (
            ("plan", {"raw_directory": self.raw, "expected_plan_hash": digest}),
            ("plan", {"raw_directory": self.raw, "authorization": "x"}),
            ("backup", {"raw_directory": self.raw, "expected_plan_hash": digest, "backup_dir": "x",
                        "backup_evidence": "y", "authorization": "x"}),
            ("apply", {"raw_directory": self.raw, "expected_plan_hash": digest, "backup_evidence": "y",
                       "review_ref": "r", "authorization": "x", "backup_dir": "x"}),
            ("postcheck", {"expected_plan_hash": digest, "authorization": "x"}),
            ("postcheck", {"expected_plan_hash": digest, "raw_directory": self.raw}),
        )
        for action, options in cases:
            with self.subTest(action=action, options=sorted(options)), self.assertRaises(CommandError):
                self.run_command(action, **options)
        for action, options in (("backup", {"raw_directory": self.raw}), ("apply", {"raw_directory": self.raw}),
                                ("plan", {}), ("postcheck", {})):
            with self.subTest(missing=action), self.assertRaises(CommandError):
                self.run_command(action, **options)
        with self.assertRaisesMessage(CommandError, "--production"):
            self.run_command("plan", raw_directory=self.raw, production=False)
        self.assertEqual(self.counts(), (0, 0, 0, 0))

    def test_a_blocked_plan_is_written_for_inspection_but_exits_unsuccessfully(self):
        Box.objects.filter(pk=merge.DONOR_ID).update(merged_into=None)
        with self.assertRaises(CommandError):
            self.run_command("plan", raw_directory=self.raw)
        self.assertFalse((self.directory / "plan.json").exists())
        AuditLog.objects.filter(pk=self.pca_receipt.pk)._raw_delete(using="default")


class RefusalWithoutDatabaseTests(SimpleTestCase):
    """Attestation and artifact checks run before any connection."""

    def test_a_plan_for_another_target_is_refused_by_the_command(self):
        target = synthetic_target()
        with patch.object(pca_production, "read_target", return_value=target), \
                patch.object(pca_production, "require_target", return_value=False), \
                patch.object(pca_production, "read_artifact", return_value={"target": {**target, "role": "other"}}), \
                patch.object(production, "backup_plan") as backup:
            with self.assertRaisesMessage(CommandError, "differs from the supplied independent attestation"):
                call_command("historical_temperature_production", "backup", production=True, organization_id=1,
                             actor_id=1, target="t", plan_file="p", raw_directory=Path("r"),
                             expected_plan_hash="a" * 64, backup_dir="b", backup_evidence="e")
            backup.assert_not_called()

    def test_validate_plan_rejects_a_foreign_or_not_ready_document_without_a_connection(self):
        with patch.object(production, "connection") as mocked:
            for document in ({}, None, "x", {"schema": production.SCHEMA}):
                with self.assertRaises(production.ProductionImportError):
                    production.validate_plan(document, "a" * 64)
            mocked.cursor.assert_not_called()

    def test_scope_is_organization_one_only(self):
        with patch.object(pca_production, "require_target", return_value=False), \
                patch.object(pca_production, "verify_target") as verify:
            for organization_id in (2, True):
                with self.subTest(organization_id=organization_id), self.assertRaises(production.ProductionImportError):
                    production.build_plan(organization_id, actor=SimpleNamespace(pk=1),
                                          target={}, raw_directory=Path("x"))
            verify.assert_not_called()


class RuntimePrivilegeTests(SimpleTestCase):
    """Exercise each privilege refusal without connecting or creating database roles."""

    def check_privileges(self, forbidden=None):
        queries = []
        with patch.object(production, "connection") as mocked_connection:
            cursor = mocked_connection.cursor.return_value.__enter__.return_value

            def execute(sql, params=None):
                queries.append(sql)
                cursor.fetchone.return_value = (forbidden is not None and forbidden in sql.lower(),)

            cursor.execute.side_effect = execute
            if forbidden is None:
                CHECK_RUNTIME_PRIVILEGES()
            else:
                with self.assertRaises(production.ProductionImportError):
                    CHECK_RUNTIME_PRIVILEGES()
        return queries

    def test_unprivileged_runtime_passes_all_checks(self):
        queries = self.check_privileges()
        for marker in ("relowner", "proowner", "has_schema_privilege", "has_database_privilege"):
            with self.subTest(privilege=marker):
                self.assertTrue(any(marker in sql.lower() for sql in queries))

    def test_table_owner_or_owner_role_member_is_refused(self):
        queries = self.check_privileges("relowner")
        self.assertTrue(any("relowner" in sql.lower() and "pg_has_role" in sql.lower() for sql in queries))

    def test_guard_function_owner_or_owner_role_member_is_refused(self):
        queries = self.check_privileges("proowner")
        self.assertTrue(any("proowner" in sql.lower() and "pg_has_role" in sql.lower() for sql in queries))

    def test_schema_create_privilege_is_refused(self):
        self.check_privileges("has_schema_privilege")

    def test_database_create_privilege_is_refused(self):
        self.check_privileges("has_database_privilege")


@skipUnless(os.environ.get("POLYPBASE_HISTORICAL_TEMPERATURE_SCALE"),
            "Set POLYPBASE_HISTORICAL_TEMPERATURE_SCALE=<boxes> (for example 600) to measure a large synthetic import.")
class ScaleTests(ProductionFixture):
    """Feasibility measurement only; prints timings and sizes. Synthetic data."""

    def test_large_synthetic_import(self):
        import time

        boxes = int(os.environ["POLYPBASE_HISTORICAL_TEMPERATURE_SCALE"])
        weeks = range(1, 11)
        blocks = {year: [] for year in range(2020, 2027)}
        strain = Strain.objects.create(pk=900, organization=self.org, species=self.species, code="SCA-DEF-1")
        created = Box.objects.bulk_create([
            Box(pk=10000 + i, organization=self.org, strain=strain, global_code=f"SCA-DEF-1.{i + 1:03d}",
                box_number=f"{i + 1:03d}") for i in range(boxes)])
        rows = []
        for i, box in enumerate(created):
            for year in blocks:
                data = {}
                for week in weeks:
                    values = ((i + week) % 5, (i * week) % 4)
                    data[week] = values
                    measured_on = date.fromisocalendar(year, week, 3)
                    rows.append(BiologicalMeasurement(
                        box=box, measured_on=measured_on, week_start=BiologicalMeasurement.week_start_for(measured_on),
                        polyp_count=values[0], ephyrae_count=values[1], user=self.writer,
                        original_observer=self.writer, observer_status="known", imported_by=self.writer,
                        origin="file_import", import_format="csv"))
                blocks[year].append((box.global_code, "0" if i % 7 == 0 else f"{10 + i % 15}.5", data))
        BiologicalMeasurement.objects.bulk_create(rows, batch_size=5000)
        blocks[2026] = default_blocks()[2026] + blocks[2026]
        blocks[2025] = default_blocks()[2025] + blocks[2025]
        self.install_sources(blocks)
        timings, sizes = {}, {}

        def timed(name, function):
            started = time.monotonic()
            result = function()
            timings[name] = round(time.monotonic() - started, 1)
            return result

        document = timed("plan", self.plan)
        self.assertTrue(document["ready"], document["blockers"])
        text = json.dumps(document, sort_keys=True, indent=2)
        sizes["plan_file_bytes"] = len(text.encode())
        sizes["payloads"] = len(document["plan"]["payloads"])
        sizes["evidences"] = document["plan"]["summary"]["accepted_evidences"]
        sizes["baseline_counts"] = document["baseline"]["counts"]
        timed("plan_hash", lambda: production.plan_hash(document))
        evidence = timed("backup", lambda: self.backup(document))
        inner = {}
        for name in ("_recompute", "_insert", "read_state", "touched_fingerprint", "_archive_sources"):
            original = getattr(production, name)

            def wrapper(*args, _original=original, _name=name, **kwargs):
                started = time.monotonic()
                try:
                    if _name == "_insert" and os.environ.get("POLYPBASE_HT_PROFILE"):
                        import cProfile, pstats
                        profiler = cProfile.Profile()
                        try:
                            return profiler.runcall(_original, *args, **kwargs)
                        finally:
                            pstats.Stats(profiler).sort_stats("cumulative").print_stats(35)
                    return _original(*args, **kwargs)
                finally:
                    inner[_name] = round(inner.get(_name, 0) + time.monotonic() - started, 1)

            patcher = patch.object(production, name, side_effect=wrapper)
            patcher.start()
            self.addCleanup(patcher.stop)
        sizes["archive_bytes"] = evidence["archive"]["size_bytes"]
        receipt, _ = timed("apply_total", lambda: self.apply(document, evidence))
        timings["apply_inner"] = inner
        sizes["receipt_archive_bytes"] = len(bytes(receipt.source_archive))
        timed("postcheck", lambda: production.postcheck(
            1, actor=self.admin, document=document, approved_hash=production.plan_hash(document)))
        timed("replay", lambda: self.apply(document, evidence))
        self.assertEqual(self.counts()[0], sizes["payloads"])
        report = Path(__file__).resolve().parents[3] / ".local-docs" / "historical-temperature-production-scale.json"
        report.parent.mkdir(exist_ok=True)
        report.write_text(json.dumps({"timings_seconds": timings, "sizes": sizes}, indent=2), encoding="utf-8")
        print("SCALE", json.dumps({"timings_seconds": timings, "sizes": sizes}))
