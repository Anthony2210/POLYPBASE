"""Synthetic integration proofs; SQLite does not prove PostgreSQL trigger execution."""

from datetime import date
from importlib import import_module
from unittest.mock import MagicMock

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection
from django.db.migrations.loader import MigrationLoader
from django.test import SimpleTestCase, TestCase, override_settings
from apps.accounts.models import OrganizationMembership

from apps.cultures.models import Box
from apps.cultures.temperature_history import box_historical_temperature_queryset
from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain

from .historical_temperature_test_fixtures import evidence
from .models import BiologicalMeasurement, HistoricalTemperatureEvidence, HistoricalTemperatureReading


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

    def test_model_targets_canonical_biology_and_preserves_source(self):
        reading = HistoricalTemperatureReading.objects.create(
            biological_measurement=self.biology, iso_year=2026, iso_week=1, temperature_c="0.000",
        )
        source = evidence(source_box="PCA-CHO-1.05", source_value="0.000")
        saved = HistoricalTemperatureEvidence.objects.create(reading=reading, **source)
        self.assertEqual(saved.source_box, "PCA-CHO-1.05")
        self.assertEqual(list(box_historical_temperature_queryset(self.box, date(2026, 1, 1), date(2026, 1, 2))), [reading])
        self.biology.refresh_from_db()
        self.assertIsNone(self.biology.strobila_count)
        self.assertEqual(self.biology.polyp_count, 0)

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

    def test_duplicate_reading_is_refused(self):
        with self.assertRaises(ValidationError):
            HistoricalTemperatureReading.objects.create(
                biological_measurement=self.duplicate, iso_year=2026, iso_week=1, temperature_c="0",
            )


class IntegratedMigrationGraphTests(SimpleTestCase):
    def test_single_measurement_leaf_and_preserved_cultures_dependency(self):
        graph = MigrationLoader(None).graph
        leaves = graph.leaf_nodes("measurements")
        self.assertEqual(len(leaves), 1, leaves)
        path = graph.forwards_plan(leaves[0])
        self.assertLess(
            path.index(("measurements", "0014_measurement_canonical_evidence")),
            path.index(("measurements", "0015_iminilide_probe_collection")),
        )
        # Every later measurements migration extends one linear chain from
        # 0015: no branch or merge can reorder the historical prefix.
        later = [node for node in path[path.index(("measurements", "0015_iminilide_probe_collection")):]
                 if node[0] == "measurements"]
        self.assertEqual(later[-1], leaves[0])
        for previous, node in zip(later, later[1:]):
            parents = {p.key for p in graph.node_map[node].parents if p.key[0] == "measurements"}
            self.assertEqual(parents, {previous}, node)
        self.assertEqual(
            {parent.key for parent in graph.node_map[("measurements", "0019_box_creation_initial_origin")].parents
             if parent.key[0] == "measurements"},
            {("measurements", "0018_box_1902_preservation")},
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
