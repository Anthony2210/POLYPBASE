"""Isolated SQLite acceptance/rollback tests, including the integrated schema.

PostgreSQL locking and triggers are not claimed by these tests.
"""

from copy import deepcopy
from datetime import date
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.test import SimpleTestCase, TestCase
from unittest import skipUnless

from apps.audit.models import AuditLog
from apps.cultures.models import Box
from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain

from .historical_temperature_apply import _accept_locked, _current_snapshot, _validate_target, apply_approved_partial
from .historical_temperature_partial import PartialImportError, select_partial
from .historical_temperature_plan import plan
from .models import BiologicalMeasurement, HistoricalTemperatureEvidence, HistoricalTemperatureImportReceipt, HistoricalTemperatureReading
from .test_historical_temperature_plan import candidate, evidence


class DisabledWriterTests(SimpleTestCase):
    def test_separate_action_required_without_queries_or_source_reads(self):
        with self.assertRaisesMessage(PartialImportError, "separate_apply_confirmation_required"):
            apply_approved_partial(raw_directory=None, snapshot_bytes=None, expected_plan_fingerprint=None,
                                   organization_id=1, actor=None, confirmation=None)

    def test_default_or_unexpected_database_refused_without_connecting(self):
        with self.assertRaisesMessage(PartialImportError, "unsafe_or_disabled_target"):
            _validate_target()

    def test_foreign_institution_rejected_before_archive_or_database_access(self):
        from .historical_temperature_apply import APPLY_CONFIRMATION
        with patch("apps.measurements.historical_temperature_apply.prepare_approved_partial", return_value=({}, {
            "organization_id": 1, "plan_fingerprint": "expected",
        })), patch("apps.measurements.historical_temperature_apply._archive_sources") as archive:
            with self.assertRaisesMessage(PartialImportError, "institution_mismatch"):
                apply_approved_partial(raw_directory=None, snapshot_bytes=None, expected_plan_fingerprint="expected",
                                       organization_id=2, actor=None, confirmation=APPLY_CONFIRMATION)
        archive.assert_not_called()

    def test_archive_preserves_original_bytes_and_refuses_changed_fingerprint(self):
        import hashlib
        import io
        import tempfile
        import zipfile
        from pathlib import Path
        from .historical_temperature_apply import _archive_sources
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "synthetic.xlsx"
            source.write_bytes(b"original-source-bytes")
            sources = {source.name: (2026, hashlib.sha256(source.read_bytes()).hexdigest())}
            with patch("apps.measurements.historical_temperature_apply.SOURCES", sources):
                archive = _archive_sources(directory)
                self.assertEqual(archive, _archive_sources(directory))
                with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
                    self.assertEqual(zipped.read(source.name), source.read_bytes())
                source.write_bytes(b"changed-source-bytes")
                with self.assertRaisesMessage(PartialImportError, "source_changed_before_acceptance"):
                    _archive_sources(directory)


@skipUnless(connection.vendor == "sqlite", "Requires isolated SQLite settings")
class AcceptanceTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name="Synthetic temperature laboratory")
        self.other = Organization.objects.create(name="Foreign synthetic laboratory")
        self.actor = get_user_model().objects.create_user(username="temperature-admin", email="temperature-admin@example.org")
        species = Species.objects.create(scientific_name="Synthetic species", genus_species_code="ABC")
        strain = Strain.objects.create(species=species, code="ABC-DEF-1")
        self.box = Box.objects.create(organization=self.organization, strain=strain, global_code="ABC-DEF-1.001", box_number="001")
        self.biology = BiologicalMeasurement.objects.create(box=self.box, measured_on=date(2026, 1, 1), polyp_count=0, ephyrae_count=0)
        current = _current_snapshot(self.organization.pk)
        self.snapshot = {**current, "historical_temperatures": []}
        baseline = {
            "organization_id": self.organization.pk,
            "boxes": [{k: row[k] for k in ("id", "organization_id", "global_code")} for row in current["boxes"]],
            "biological_measurements": [{k: row[k] for k in ("id", "box_id", "week_start", "polyp_count", "ephyrae_count")} for row in current["biological_measurements"]],
            "historical_temperatures": [],
        }
        rows = [candidate(source=evidence(source_value="0")), candidate(source=evidence(source_value="0.000", polyp_cell="F7", ephyrae_cell="F8"))]
        association_plan = plan(rows, organization_id=self.organization.pk, baseline=baseline)
        self.partial = select_partial({
            "association_plan": association_plan, "organization_id": self.organization.pk,
            "summary": {"ready": False}, "report_fingerprint": "a" * 64,
            "baseline_fingerprint": "b" * 64, "source_extraction": {"exclusions": [], "header_issues": []},
        })
        self.partial["snapshot_sha256"] = "c" * 64

    def accept(self, partial=None, organization=None):
        with transaction.atomic():
            return _accept_locked(self.snapshot, partial or self.partial, b"synthetic-original-workbooks", organization or self.organization, self.actor)

    def test_exact_acceptance_and_idempotent_replay_preserve_biology(self):
        before = BiologicalMeasurement.objects.values().get(pk=self.biology.pk)
        receipt, replay = self.accept()
        self.assertFalse(replay)
        self.assertEqual(HistoricalTemperatureReading.objects.count(), 1)
        self.assertEqual(HistoricalTemperatureEvidence.objects.count(), 2)
        self.assertEqual(HistoricalTemperatureReading.objects.get().temperature_c, "0.000")
        again, replay = self.accept()
        self.assertTrue(replay)
        self.assertEqual(again.pk, receipt.pk)
        self.assertEqual(AuditLog.objects.filter(object_type="historical_temperature_import").count(), 1)
        self.assertEqual(BiologicalMeasurement.objects.values().get(pk=self.biology.pk), before)

    def test_stale_counts_date_precision_and_box_identity_rejected(self):
        for field, value in (("polyp_count", 1), ("measured_on", date(2026, 1, 2)), ("temporal_precision", "exact_date")):
            with self.subTest(field=field), transaction.atomic():
                BiologicalMeasurement.objects.filter(pk=self.biology.pk).update(**{field: value})
                with self.assertRaisesMessage(PartialImportError, "stale_baseline"):
                    self.accept()
                transaction.set_rollback(True)
        Box.objects.filter(pk=self.box.pk).update(global_code="ABC-DEF-1.002")
        with self.assertRaisesMessage(PartialImportError, "stale_baseline"):
            self.accept()
        self.assertEqual(HistoricalTemperatureReading.objects.count(), 0)

    def test_audit_failure_rolls_back_all_readings_evidences_and_receipt(self):
        with patch.object(AuditLog.objects, "create", side_effect=RuntimeError("synthetic audit failure")):
            with self.assertRaises(RuntimeError):
                self.accept()
        self.assertEqual(HistoricalTemperatureReading.objects.count(), 0)
        self.assertEqual(HistoricalTemperatureEvidence.objects.count(), 0)
        self.assertEqual(HistoricalTemperatureImportReceipt.objects.count(), 0)

    def test_evidence_failure_rolls_back_reading(self):
        with patch.object(HistoricalTemperatureEvidence.objects, "create", side_effect=RuntimeError("synthetic evidence failure")):
            with self.assertRaises(RuntimeError):
                self.accept()
        self.assertEqual(HistoricalTemperatureReading.objects.count(), 0)

    def test_foreign_institution_id_is_never_resolved(self):
        with self.assertRaises(BiologicalMeasurement.DoesNotExist):
            self.accept(organization=self.other)
        self.assertEqual(HistoricalTemperatureReading.objects.count(), 0)

    def test_cross_institution_box_reassignment_invalidates_snapshot(self):
        Box.objects.filter(pk=self.box.pk).update(organization=self.other)
        with self.assertRaisesMessage(PartialImportError, "stale_baseline"):
            self.accept()

    def test_existing_reading_is_not_overwritten_or_silently_adopted(self):
        HistoricalTemperatureReading.objects.create(biological_measurement=self.biology, iso_year=2026, iso_week=1, temperature_c="99")
        with self.assertRaisesMessage(PartialImportError, "unexpected_existing_temperature"):
            self.accept()
        self.assertEqual(HistoricalTemperatureReading.objects.get().temperature_c, "99")

    def test_replay_requires_identical_plan_sources_and_evidence(self):
        self.accept()
        changed = deepcopy(self.partial)
        changed["excluded"].append({"status": "MISSING_BOX"})
        with self.assertRaisesMessage(PartialImportError, "receipt_mismatch"):
            self.accept(partial=changed)
        with transaction.atomic(), self.assertRaisesMessage(PartialImportError, "replay_sources_changed"):
            _accept_locked(self.snapshot, self.partial, b"changed", self.organization, self.actor)

    def test_direct_evidence_write_checks_stored_counts_even_with_stale_cache(self):
        reading = HistoricalTemperatureReading.objects.create(biological_measurement=self.biology, iso_year=2026, iso_week=1, temperature_c="0")
        values = evidence(source_value="0", polyp_value="1")
        reading.biological_measurement.polyp_count = 1
        with self.assertRaises(ValidationError):
            HistoricalTemperatureEvidence.objects.create(reading=reading, **values)
        self.assertEqual(HistoricalTemperatureEvidence.objects.count(), 0)

    def test_direct_orm_evidence_rejects_mismatched_original_source_box(self):
        reading = HistoricalTemperatureReading.objects.create(
            biological_measurement=self.biology, iso_year=2026, iso_week=1, temperature_c="0",
        )
        for label in ("ABC-DEF-1.002", "ABC-XYZ-1.001", "ABC-DEF-1.0001", "abc-def-1.001"):
            with self.subTest(label=label), self.assertRaises(ValidationError) as error:
                HistoricalTemperatureEvidence.objects.create(reading=reading, **evidence(
                    source_value="0", source_box=label,
                ))
            self.assertIn("source_box", error.exception.message_dict)
        self.assertEqual(HistoricalTemperatureEvidence.objects.count(), 0)
        # Even a fabricated cached Box identity must not validate an insertion.
        reading.biological_measurement.box.global_code = "ABC-DEF-1.002"
        with self.assertRaises(ValidationError):
            HistoricalTemperatureEvidence.objects.create(reading=reading, **evidence(
                source_value="0", source_box="ABC-DEF-1.002",
            ))
        self.assertEqual(HistoricalTemperatureEvidence.objects.count(), 0)
        for index, label in enumerate(("ABC-DEF-1.1", "ABC-DEF-1.01", "ABC-DEF-1.001")):
            HistoricalTemperatureEvidence.objects.create(reading=HistoricalTemperatureReading.objects.get(pk=reading.pk),
                **evidence(source_value="0", source_box=label, polyp_cell=f"F{11 + 2 * index}",
                           ephyrae_cell=f"F{12 + 2 * index}"))
        self.assertEqual(HistoricalTemperatureEvidence.objects.count(), 3)

    def test_receipt_preserves_actor_identity_without_blocking_account_removal(self):
        receipt, _ = self.accept()
        actor_id = self.actor.pk
        self.actor.delete()
        receipt.refresh_from_db()
        self.assertEqual(receipt.actor_id, actor_id)

    def test_evidence_and_receipt_orm_immutability(self):
        receipt, _ = self.accept()
        for item in (receipt, HistoricalTemperatureEvidence.objects.first(), HistoricalTemperatureReading.objects.first()):
            with self.assertRaises(ValidationError):
                item.save()
            with self.assertRaises(ValidationError):
                item.delete()
