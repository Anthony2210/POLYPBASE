"""Synthetic integration proofs; SQLite does not prove PostgreSQL trigger execution."""

from copy import deepcopy
from datetime import date
from importlib import import_module
from unittest.mock import MagicMock

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection
from django.db.migrations.loader import MigrationLoader
from django.test import SimpleTestCase, TestCase, override_settings
from apps.accounts.models import OrganizationMembership

from apps.cultures import pca_identity_unification as merge
from apps.cultures.models import Box
from apps.cultures.temperature_history import box_historical_temperature_queryset
from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain

from .historical_temperature_apply import _accept_locked, _current_snapshot
from .historical_temperature_partial import select_partial, snapshot_baseline
from .historical_temperature_plan import plan
from .models import BiologicalMeasurement, HistoricalTemperatureEvidence, HistoricalTemperatureReading
from .test_historical_temperature_plan import baseline, candidate, evidence


class PcaTemperaturePlannerTests(SimpleTestCase):
    def snapshot(self):
        snapshot = baseline()
        snapshot["boxes"][0].update(
            global_code="PCA-JKA-1.005", merged_into_id=None, previous_global_code="PCA-CHO-1.005",
        )
        snapshot["boxes"].append({
            "id": 12, "organization_id": 7, "global_code": "MERGED:12:PCA-JKA-1.005",
            "merged_into_id": 11, "previous_global_code": "PCA-JKA-1.005",
        })
        snapshot["biological_measurements"][0]["duplicate_of_id"] = None
        snapshot["biological_measurements"].append({
            **snapshot["biological_measurements"][0], "id": 22, "duplicate_of_id": 21,
            "polyp_count": 900, "ephyrae_count": 99,
        })
        return snapshot

    def source(self, code="PCA-CHO-1.05", **changes):
        return candidate(source=evidence(source_box=code, **changes), canonical_box=code.rsplit(".", 1)[0] + ".005")

    def test_cho_and_jka_are_one_culture_preserving_both_labels_and_zero(self):
        rows = [self.source(), self.source("PCA-JKA-1.005", polyp_cell="F7", ephyrae_cell="F8")]
        original = deepcopy(rows)
        result = plan(rows, organization_id=7, baseline=self.snapshot())
        self.assertEqual(rows, original)
        self.assertEqual(result["baseline_errors"], [])
        self.assertEqual(len(result["associations"]), 1)
        association = result["associations"][0]
        self.assertEqual((association["canonical_box"], association["biological_measurement_id"]), ("PCA-JKA-1.005", 21))
        self.assertEqual(association["baseline_counts"], {"polyps": 0, "ephyrae": 0})
        self.assertEqual(association["status"], "CREATE_READING")
        self.assertEqual({e["source_box"] for e in association["evidences"]}, {"PCA-CHO-1.05", "PCA-JKA-1.005"})
        payload = result["proposed_changes"][0]["payload"]
        self.assertEqual(payload["biological_measurement_id"], 21)
        self.assertEqual(len(payload["evidences"]), 2)
        replay = self.snapshot()
        replay["historical_temperatures"] = [{"id": 31, **payload}]
        again = plan(rows, organization_id=7, baseline=replay)
        self.assertEqual(again["associations"][0]["status"], "ALREADY_PRESENT")
        self.assertEqual(again["proposed_changes"], [])

    def test_conflicting_pca_sources_are_not_arbitrated_or_averaged(self):
        for changes in ({"source_value": "20"}, {"polyp_value": "1"}):
            row = self.source("PCA-JKA-1.005", polyp_cell="F7", ephyrae_cell="F8", **changes)
            if "polyp_value" in changes:
                row["polyps"] = 1
            result = plan([self.source(), row], organization_id=7, baseline=self.snapshot())
            self.assertEqual(result["associations"][0]["status"], "CONFLICT")
            self.assertEqual(result["proposed_changes"], [])

    def test_no_week_inference_no_other_suffix_alias_and_no_fallback_to_old_identity(self):
        snapshot = self.snapshot()
        snapshot["biological_measurements"] = []
        result = plan([self.source()], organization_id=7, baseline=snapshot)
        self.assertEqual(result["associations"][0]["status"], "MISSING_WEEK")
        snapshot = baseline()
        snapshot["boxes"][0]["global_code"] = "PCA-CHO-1.005"
        result = plan([self.source()], organization_id=7, baseline=snapshot)
        self.assertEqual(result["associations"][0]["status"], "MISSING_BOX")
        row = candidate(source=evidence(source_box="PCA-CHO-1.006"), canonical_box="PCA-CHO-1.006")
        result = plan([row], organization_id=7, baseline=self.snapshot())
        self.assertEqual(result["associations"][0]["status"], "MISSING_BOX")

    def test_foreign_retirement_or_duplicate_graph_blocks_all_proposals(self):
        for field, value in (("organization_id", 8), ("merged_into_id", 12), ("previous_global_code", "ABC-DEF-1.001")):
            snapshot = self.snapshot()
            snapshot["boxes"][1][field] = value
            result = plan([self.source()], organization_id=7, baseline=snapshot)
            self.assertEqual(result["baseline_status"], "BASELINE_INVALID")
            self.assertEqual(result["proposed_changes"], [])
        snapshot = self.snapshot()
        snapshot["biological_measurements"][1]["duplicate_of_id"] = 999
        result = plan([self.source()], organization_id=7, baseline=snapshot)
        self.assertEqual(result["baseline_status"], "BASELINE_INVALID")


class PcaTemperatureStorageTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Synthetic PCA integration")
        species = Species.objects.create(scientific_name="Synthetic PCA", genus_species_code="PCA")
        strain = Strain.objects.create(species=species, code="PCA-JKA-1", organization=self.org)
        self.box = Box.objects.create(organization=self.org, strain=strain, global_code="PCA-JKA-1.005", box_number="005")
        self.biology = BiologicalMeasurement.objects.create(
            box=self.box, measured_on=date(2026, 1, 1), polyp_count=0, ephyrae_count=0, strobila_count=None,
        )
        self.duplicate = BiologicalMeasurement.objects.create(
            box=self.box, measured_on=date(2026, 1, 2), polyp_count=0, ephyrae_count=0,
            duplicate_of=self.biology,
        )

    def test_model_and_snapshot_target_canonical_biology_and_preserve_source(self):
        reading = HistoricalTemperatureReading.objects.create(
            biological_measurement=self.biology, iso_year=2026, iso_week=1, temperature_c="0.000",
        )
        source = evidence(source_box="PCA-CHO-1.05", source_value="0.000")
        saved = HistoricalTemperatureEvidence.objects.create(reading=reading, **source)
        self.assertEqual(saved.source_box, "PCA-CHO-1.05")
        current = _current_snapshot(self.org.pk)
        projected = snapshot_baseline({"identity": {"organization_id": self.org.pk}, **current, "historical_temperatures": []})
        row = candidate(source=source, canonical_box="PCA-CHO-1.005")
        result = plan([row], organization_id=self.org.pk, baseline=projected)
        self.assertEqual(result["associations"][0]["biological_measurement_id"], self.biology.pk)
        self.assertIn("duplicate_of_id", current["biological_measurements"][0])
        self.assertEqual(list(box_historical_temperature_queryset(self.box, date(2026, 1, 1), date(2026, 1, 2))), [reading])
        self.biology.refresh_from_db()
        self.assertIsNone(self.biology.strobila_count)
        self.assertEqual(self.biology.polyp_count, 0)

    def test_locked_import_preserves_cho_evidence_and_receipt_on_canonical_replay(self):
        current = _current_snapshot(self.org.pk)
        snapshot = {"identity": {"organization_id": self.org.pk}, **current, "historical_temperatures": []}
        source = evidence(source_box="PCA-CHO-1.05", source_value="0.000")
        association_plan = plan(
            [candidate(source=source, canonical_box="PCA-CHO-1.005")],
            organization_id=self.org.pk, baseline=snapshot_baseline(snapshot),
        )
        partial = select_partial({
            "association_plan": association_plan, "organization_id": self.org.pk,
            "summary": {"ready": True}, "report_fingerprint": "a" * 64,
            "baseline_fingerprint": "b" * 64,
            "source_extraction": {"exclusions": [], "header_issues": []},
        })
        partial["snapshot_sha256"] = "c" * 64
        actor = get_user_model().objects.create_user(
                    username="synthetic-temperature-admin", email="synthetic-temperature-admin@example.test",
                )
        OrganizationMembership.objects.create(user=actor, organization=self.org, role="admin")
        receipt, replay = _accept_locked(snapshot, partial, b"synthetic-cho-source", self.org, actor)
        self.assertFalse(replay)
        reading = HistoricalTemperatureReading.objects.get()
        self.assertEqual(reading.biological_measurement_id, self.biology.pk)
        self.assertEqual(reading.temperature_c, "0.000")
        self.assertEqual(reading.evidences.get().source_box, "PCA-CHO-1.05")
        self.assertEqual(receipt.accepted_plan["payloads"][0]["evidences"], [source])
        again, replay = _accept_locked(snapshot, partial, b"synthetic-cho-source", self.org, actor)
        self.assertTrue(replay)
        self.assertEqual(again.pk, receipt.pk)
        self.assertEqual(HistoricalTemperatureReading.objects.count(), 1)
        self.duplicate.refresh_from_db()
        self.biology.refresh_from_db()
        self.assertEqual(self.duplicate.duplicate_of_id, self.biology.pk)
        self.assertIsNone(self.biology.strobila_count)

    @override_settings(SECURE_SSL_REDIRECT=False)
    def test_retired_historical_url_resolves_only_within_the_active_institution(self):
        reading = HistoricalTemperatureReading.objects.create(
            biological_measurement=self.biology, iso_year=2026, iso_week=1, temperature_c="0",
        )
        HistoricalTemperatureEvidence.objects.create(
            reading=reading, **evidence(source_box="PCA-CHO-1.05", source_value="0"),
        )
        donor = Box.objects.create(
            organization=self.org, strain=self.box.strain, global_code="PCA-CHO-1.005", box_number="005",
            status=Box.Status.INACTIVE,
        )
        Box.objects.filter(pk=donor.pk).update(
            global_code=f"MERGED:{donor.pk}:PCA-CHO-1.005", previous_global_code="PCA-CHO-1.005",
            merged_into=self.box,
        )
        user = get_user_model().objects.create_user(username="pca-temperature-viewer", email="pca-temperature@example.test")
        OrganizationMembership.objects.create(user=user, organization=self.org, role="viewer")
        self.client.force_login(user)
        responses = [self.client.get(
            f"/api/boxes/{pk}/historical-temperatures/?start=2026-01-01&end=2026-01-02",
            HTTP_X_ORGANIZATION_ID=str(self.org.pk),
        ) for pk in (self.box.pk, donor.pk)]
        self.assertEqual([r.status_code for r in responses], [200, 200])
        self.assertEqual(responses[0].json(), responses[1].json())
        other = Organization.objects.create(name="Foreign synthetic PCA")
        OrganizationMembership.objects.create(user=user, organization=other, role="viewer")
        response = self.client.get(
            f"/api/boxes/{donor.pk}/historical-temperatures/?start=2026-01-01&end=2026-01-02",
            HTTP_X_ORGANIZATION_ID=str(other.pk),
        )
        self.assertEqual(response.status_code, 404)

    def test_duplicate_reading_is_refused_and_pca_merge_detects_real_temperature_dependency(self):
        with self.assertRaises(ValidationError):
            HistoricalTemperatureReading.objects.create(
                biological_measurement=self.duplicate, iso_year=2026, iso_week=1, temperature_c="0",
            )
        reading = HistoricalTemperatureReading.objects.create(
            biological_measurement=self.biology, iso_year=2026, iso_week=1, temperature_c="0",
        )
        HistoricalTemperatureEvidence.objects.create(
            reading=reading, **evidence(source_box="PCA-CHO-1.05", source_value="0"),
        )
        entries = merge._dependency_inventory([self.biology.pk])
        entry = next(e for e in entries if e["model"] == "measurements.historicaltemperaturereading")
        self.assertFalse(entry["allowed"])
        self.assertEqual(entry["count"], 1)
        self.assertEqual(entry["rows"][0]["pk"], reading.pk)


class IntegratedMigrationGraphTests(SimpleTestCase):
    def test_single_measurement_leaf_and_preserved_cultures_dependency(self):
        graph = MigrationLoader(None).graph
        self.assertEqual(graph.leaf_nodes("measurements"), [("measurements", "0015_iminilide_probe_collection")])
        path = graph.forwards_plan(("measurements", "0015_iminilide_probe_collection"))
        self.assertLess(
            path.index(("measurements", "0014_measurement_canonical_evidence")),
            path.index(("measurements", "0015_iminilide_probe_collection")),
        )
        for migration in (
            ("measurements", "0011_historical_temperature_storage"),
            ("measurements", "0012_historical_temperature_guards"),
            ("measurements", "0013_historical_temperature_receipt"),
            ("cultures", "0015_box_merged_into_box_previous_global_code_and_more"),
        ):
            self.assertLess(path.index(migration), path.index(("measurements", "0014_measurement_canonical_evidence")))

    def test_postgres_guard_extension_is_schema_only_and_sqlite_noop(self):
        guards = import_module("apps.measurements.migrations.0014_measurement_canonical_evidence")
        self.assertEqual(guards.SQL.count("CREATE OR REPLACE FUNCTION"), 4)
        self.assertIn("b.duplicate_of_id IS NOT NULL", guards.SQL)
        self.assertIn("NEW.duplicate_of_id IS NOT NULL", guards.SQL)
        self.assertIn("AND code = 'PCA-JKA-1.005'", guards.SQL)
        self.assertIn("NEW.merged_into_id", guards.SQL)
        self.assertNotIn("INSERT INTO", guards.SQL)
        self.assertNotIn("DISABLE TRIGGER", guards.SQL)
        editor = MagicMock()
        editor.connection.vendor = "sqlite"
        guards.forwards(None, editor)
        editor.execute.assert_not_called()
        editor.connection.vendor = "postgresql"
        guards.forwards(None, editor)
        editor.execute.assert_called_once_with(guards.SQL)
