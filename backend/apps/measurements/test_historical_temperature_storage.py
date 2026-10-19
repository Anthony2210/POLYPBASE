"""Database-free storage contracts. No migration or SQL is executed here."""

from datetime import date
from decimal import Decimal, localcontext
from importlib import import_module
from unittest.mock import patch

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import models
from django.db.migrations import CreateModel
from django.db.migrations.autodetector import MigrationAutodetector
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.state import ProjectState
from django.test import SimpleTestCase, override_settings

from apps.cultures.models import Box

from .models import (
    BiologicalMeasurement, HistoricalTemperatureEvidence, HistoricalTemperatureReading,
)
from .temperature_series import (
    agreed_temperature, daily_temperature_point, historical_temperature_point,
    iso_week_bounds, probe_temperature_point, temperature_decimal,
)


def reading(**overrides):
    measurement = BiologicalMeasurement(
        pk=31, box=Box(pk=12, global_code="ABC-DEF-1.001"),
        measured_on=date(2026, 1, 1), week_start=date(2025, 12, 29),
        polyp_count=0, ephyrae_count=0, strobila_count=None,
    )
    return HistoricalTemperatureReading(**{
        "pk": 41, "biological_measurement": measurement,
        "iso_year": 2026, "iso_week": 1, "temperature_c": "15.000",
        **overrides,
    })


def evidence(**overrides):
    if ("polyp_cell" in overrides and "temperature_cell" not in overrides
            and overrides["polyp_cell"].lstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ").isdecimal()):
        row = int(overrides["polyp_cell"].lstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ"))
        overrides = {"temperature_cell": f"C{row}", "temperature_merged_range": f"C{row}:C{row+1}", **overrides}
    return HistoricalTemperatureEvidence(**{
        "reading": reading(), "source_file": "synthetic.xlsx", "source_sha256": "a" * 64,
        "sheet": "Synthetic", "temperature_cell": "C3", "temperature_merged_range": "C3:C4",
        "source_value": "15.000", "source_box": "ABC-DEF-1.01",
        "polyp_cell": "F3", "polyp_value": "0", "ephyrae_cell": "F4", "ephyrae_value": "0",
        "week_cell": "F1", "week_value": "S1", "source_iso_year": 2026, "source_iso_week": 1,
        **overrides,
    })


class HistoricalTemperatureDecimalTests(SimpleTestCase):
    def test_zero_is_present_and_no_chart_range_is_a_scientific_validator(self):
        for text in ("0", "0.000", "-2.3456", "45.12345", "1000.12345678901234567890123456789"):
            with self.subTest(text=text):
                self.assertEqual(temperature_decimal(text), Decimal(text))
                reading(temperature_c=text).clean()
                evidence(reading=reading(temperature_c=text), source_value=text).clean()

    def test_exact_source_precision_is_not_rounded_by_decimal_context(self):
        text = "15.1234567890123456789012345678901234567890"
        with localcontext() as context:
            context.prec = 3
            self.assertEqual(str(temperature_decimal(text)), text)
            self.assertEqual(agreed_temperature([text, text]), text)
            evidence(reading=reading(temperature_c=text), source_value=text).clean()

    def test_missing_nonfinite_binary_float_and_invalid_values_are_rejected(self):
        for value in (None, "", " ", "NaN", "Infinity", "-Infinity", "15 C", "15,2", "1_000", 0, 15.1, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                temperature_decimal(value)

    def test_multiple_agreeing_sources_are_order_independent_and_keep_finest_scale(self):
        values = ["15", "15.0000", "1.5e1", "15.00"]
        self.assertEqual(agreed_temperature(values), "15.0000")
        self.assertEqual(agreed_temperature(reversed(values)), "15.0000")
        self.assertEqual(agreed_temperature(["0", "0.000"]), "0.000")

    def test_conflicts_are_not_averaged_or_resolved_by_first_source(self):
        for values in ([], ["15", "20"], ["0", "0.000000000000000000001"], ["15", ""]):
            with self.subTest(values=values), self.assertRaises(ValueError):
                agreed_temperature(values)


class HistoricalTemperatureValidationTests(SimpleTestCase):
    def test_source_box_matches_only_the_approved_original_identity_contract(self):
        for label in ("ABC-DEF-1.1", "ABC-DEF-1.01", "ABC-DEF-1.001"):
            evidence(source_box=label).clean()
        for label in ("ABC-DEF-1.002", "ABC-XYZ-1.001", " ABC-DEF-1.001",
                      "ABC-DEF-1.001 ", "abc-def-1.001", "ABC-DEF-1.0001", "ORIGINAL-BOX-LABEL"):
            with self.subTest(label=label), self.assertRaises(ValidationError) as error:
                evidence(source_box=label).clean()
            self.assertIn("source_box", error.exception.message_dict)

    def test_biological_cells_must_be_paired_within_source_temperature_block(self):
        for overrides in ({"polyp_cell": "F7", "ephyrae_cell": "F8", "temperature_cell": "C3"},
                          {"ephyrae_cell": "G4"}, {"ephyrae_cell": "F5"}, {"week_cell": "G2"}):
            with self.subTest(overrides=overrides), self.assertRaises(ValidationError):
                evidence(**overrides).clean()
        evidence(polyp_cell="F7", ephyrae_cell="F8", temperature_cell="C3",
                 temperature_merged_range="C3:C8").clean()

    def test_reading_does_not_change_biological_values_or_precision(self):
        item = reading()
        measurement = item.biological_measurement
        before = measurement.__dict__.copy()
        item.clean()
        self.assertEqual(measurement.__dict__, before)
        self.assertEqual(measurement.temporal_precision, "legacy_unverified")
        self.assertIsNone(measurement.strobila_count)
        self.assertEqual(measurement.polyp_count, 0)

    def test_reading_must_match_existing_biological_week_not_calendar_year(self):
        reading().clean()  # 2026-W01 starts in calendar year 2025.
        with self.assertRaisesMessage(ValidationError, "source ISO week"):
            reading(iso_week=2).clean()

    def test_invalid_iso_week_is_rejected_including_impossible_week_53(self):
        for year, week in ((2021, 53), (2026, 0), (2026, 54), (0, 1), (10000, 1), (True, 1)):
            with self.subTest(year=year, week=week), self.assertRaises(ValidationError):
                reading(iso_year=year, iso_week=week).clean()
        self.assertEqual(iso_week_bounds(2026, 53), (date(2026, 12, 28), date(2027, 1, 3)))

    def test_agreeing_evidences_preserve_individual_original_values(self):
        item = reading()
        first = evidence(reading=item, source_value="15", polyp_cell="F3", ephyrae_cell="F4")
        second = evidence(reading=item, source_value="15.00000", polyp_cell="F7", ephyrae_cell="F8")
        first.clean()
        second.clean()
        self.assertEqual((first.source_value, second.source_value), ("15", "15.00000"))
        self.assertEqual(first.reading_id, second.reading_id)

    def test_different_source_temperature_or_week_is_rejected(self):
        for changes, field in (
            ({"source_value": "20"}, "source_value"),
            ({"source_iso_week": 2}, "source_iso_week"),
            ({"source_iso_year": 2021, "source_iso_week": 53}, "source_iso_week"),
        ):
            with self.subTest(changes=changes), self.assertRaises(ValidationError) as error:
                evidence(**changes).clean()
            self.assertIn(field, error.exception.message_dict)

    def test_provenance_fields_are_required_but_literal_biological_zero_is_valid(self):
        item = evidence()
        item.full_clean(exclude=["reading"], validate_unique=False, validate_constraints=False)
        for field in (
            "source_file", "source_sha256", "sheet", "temperature_cell", "source_value", "source_box",
            "polyp_cell", "polyp_value", "ephyrae_cell", "ephyrae_value", "week_cell", "week_value",
        ):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                evidence(**{field: ""}).full_clean(
                    exclude=["reading"], validate_unique=False, validate_constraints=False,
                )

    def test_bad_digest_coordinates_and_merges_are_rejected(self):
        for changes in (
            {"source_sha256": "not-a-digest"}, {"source_sha256": "A" * 64},
            {"temperature_cell": "D3"}, {"temperature_cell": "C0"},
            {"polyp_cell": "Synthetic!F3"}, {"ephyrae_cell": "F0"}, {"week_cell": ""},
            {"temperature_merged_range": "C4:C5"}, {"temperature_merged_range": "C3:C2"},
            {"temperature_merged_range": "D3:D4"}, {"temperature_merged_range": "C3:C3"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                evidence(**changes).clean()
        evidence(temperature_merged_range="").clean()

    def test_model_updates_deletes_and_bulk_paths_are_blocked_without_queries(self):
        for model, item in ((HistoricalTemperatureReading, reading()), (HistoricalTemperatureEvidence, evidence())):
            item._state.adding = False
            with self.subTest(model=model.__name__):
                with self.assertRaises(ValidationError):
                    item.save()
                with self.assertRaises(ValidationError):
                    item.delete()
                queryset = model.objects.all()
                for operation in (
                    lambda: queryset.update(pk=99), lambda: queryset.delete(),
                    lambda: queryset.bulk_update([item], ["pk"]),
                    lambda: queryset.bulk_create([item]),
                ):
                    with self.assertRaises(ValidationError):
                        operation()

    def test_insert_calls_validation_and_never_falls_back_to_update(self):
        for item, related_model, related in (
            (reading(), BiologicalMeasurement, reading().biological_measurement),
            (evidence(), HistoricalTemperatureReading, reading()),
        ):
            with self.subTest(model=type(item).__name__), patch.object(item, "full_clean") as validate:
                with patch.object(related_model.objects, "db_manager") as manager:
                    manager.return_value.get.return_value = related
                    with patch.object(models.Model, "save") as insert:
                        item.save()
                validate.assert_called_once_with()
                self.assertTrue(insert.call_args.kwargs["force_insert"])

    def test_stale_or_modified_relation_cache_cannot_validate_an_insert(self):
        for item, related_model, stored_related in (
            (evidence(reading=reading(temperature_c="99"), source_value="99"), HistoricalTemperatureReading, reading()),
            (reading(), BiologicalMeasurement, BiologicalMeasurement(
                            pk=31, box=Box(pk=12, global_code="ABC-DEF-1.001"), week_start=date(2026, 1, 5),
                        )),
        ):
            with self.subTest(model=type(item).__name__):
                with patch.object(related_model.objects, "db_manager") as manager:
                    manager.return_value.get.return_value = stored_related
                    with patch.object(item, "full_clean", side_effect=item.clean):
                        with patch.object(models.Model, "save") as insert:
                            with self.assertRaises(ValidationError):
                                item.save()
                    insert.assert_not_called()

    def test_conflicting_evidence_is_rejected_before_any_insert(self):
        with patch.object(models.Model, "save") as insert:
            with self.assertRaises(ValidationError):
                evidence(source_value="99").save()
        insert.assert_not_called()

    def test_no_float_is_coerced_to_text_by_save(self):
        for item in (reading(temperature_c=15.1), evidence(source_value=15.1)):
            with self.subTest(model=type(item).__name__), self.assertRaises(ValidationError):
                item.save()


@override_settings(MIGRATION_MODULES={})
class HistoricalTemperatureSchemaTests(SimpleTestCase):
    def test_organization_and_box_boundary_has_only_one_relational_path(self):
        relation = HistoricalTemperatureReading._meta.get_field("biological_measurement")
        self.assertTrue(relation.one_to_one)
        self.assertIs(relation.remote_field.model, BiologicalMeasurement)
        self.assertIs(relation.remote_field.on_delete, models.PROTECT)
        self.assertIs(HistoricalTemperatureEvidence._meta.get_field("reading").remote_field.on_delete, models.PROTECT)
        for model in (HistoricalTemperatureReading, HistoricalTemperatureEvidence):
            names = {field.name for field in model._meta.fields}
            self.assertTrue(names.isdisjoint({"organization", "box", "thermal_zone", "probe", "measured_on", "measured_at"}))

    def test_duplicate_source_association_has_database_unique_constraint(self):
        constraint = next(
            item for item in HistoricalTemperatureEvidence._meta.constraints
            if item.name == "unique_historical_temp_evidence"
        )
        self.assertIsInstance(constraint, models.UniqueConstraint)
        self.assertEqual(constraint.fields, ("reading", "source_sha256", "sheet", "polyp_cell", "ephyrae_cell"))

    def test_migration_is_schema_only_and_does_not_change_existing_models(self):
        module = import_module("apps.measurements.migrations.0011_historical_temperature_storage")
        self.assertEqual(module.Migration.dependencies, [("measurements", "0010_measurement_import_receipt")])
        self.assertTrue(all(isinstance(operation, CreateModel) for operation in module.Migration.operations))
        self.assertEqual(len(module.Migration.operations), 2)
        loader = MigrationLoader(None)
        before = loader.project_state([("measurements", "0010_measurement_import_receipt")])
        after = loader.project_state([("measurements", "0011_historical_temperature_storage")])
        for key, state in before.models.items():
            self.assertEqual(after.models[key], state, key)
        self.assertEqual(set(after.models) - set(before.models), {
            ("measurements", "historicaltemperaturereading"), ("measurements", "historicaltemperatureevidence"),
        })

    def test_model_and_migration_states_match_without_opening_database(self):
        loader = MigrationLoader(None)
        changes = MigrationAutodetector(loader.project_state(), ProjectState.from_apps(apps)).changes(graph=loader.graph)
        self.assertNotIn("measurements", changes)


class HistoricalTemperatureProjectionTests(SimpleTestCase):
    def point(self, **overrides):
        return historical_temperature_point(**{
            "reading_id": 41, "biological_measurement_id": 31, "box_id": 12,
            "iso_year": 2026, "iso_week": 1, "temperature_c": "0.0000", "evidence_count": 2,
                        "association_status": "confirmed",
            **overrides,
        })

    def test_weekly_contract_has_interval_not_fabricated_date_zone_or_probe(self):
        self.assertEqual(self.point(), {
            "id": 41, "key": "historical_weekly:41", "source_kind": "historical_weekly", "temporal_precision": "iso_week",
            "statistic": "source_reading", "temperature_c": "0.0000", "unit": "degC",
            "iso_year": 2026, "iso_week": 1, "interval_start": "2025-12-29", "interval_end": "2026-01-04",
            "measured_on": None, "measured_at": None, "biological_measurement_id": 31, "box_id": 12,
            "thermal_zone_id": None, "probe_id": None, "evidence_count": 2,
        })

    def test_daily_adapter_retains_aggregate_zero_and_unknown_acquisition_origin(self):
        point = daily_temperature_point(reading_id=41, box_id=12, thermal_zone_id=7,
                                        day="2026-01-01", average_temperature_c="0.000",
                                        min_temperature_c="0", max_temperature_c=None, measurement_count=1)
        self.assertEqual(point["temperature_c"], "0.000")
        self.assertEqual(point["min_temperature_c"], "0")
        self.assertIsNone(point["max_temperature_c"])
        self.assertIsNone(point["probe_id"])
        self.assertIsNone(point["measured_at"])
        self.assertEqual(point["statistic"], "daily_mean")
        self.assertEqual(point["temporal_precision"], "calendar_day")
        self.assertEqual(point["acquisition_origin"], "not_recorded")
        self.assertNotEqual(point["key"], self.point()["key"])

    def test_probe_adapter_retains_real_timestamp_probe_and_source(self):
        point = probe_temperature_point(reading_id=41, box_id=12, thermal_zone_id=7,
                                        probe_id=9, measured_at="2026-01-01T08:13:41+02:00",
                                        temperature_c="15.123", source="synthetic_probe_feed")
        self.assertEqual(point["temperature_c"], "15.123")
        self.assertEqual(point["measured_at"], "2026-01-01T08:13:41+02:00")
        self.assertEqual(point["probe_id"], 9)
        self.assertEqual(point["source"], "synthetic_probe_feed")
        self.assertIsNone(point["biological_measurement_id"])
        self.assertNotEqual(point["key"], self.point()["key"])

    def test_probe_adapter_does_not_infer_timezone_or_accept_missing_values(self):
        for timestamp, value in (("2026-01-01T08:13:41", "15"), ("2026-01-01", "15"),
                                 ("2026-01-01T08:13:41Z", None)):
            with self.subTest(timestamp=timestamp, value=value), self.assertRaises(ValueError):
                probe_temperature_point(reading_id=41, box_id=12, thermal_zone_id=7,
                                        probe_id=9, measured_at=timestamp, temperature_c=value, source="")

    def test_daily_adapter_does_not_accept_bad_day_count_or_value(self):
        values = {"reading_id": 41, "box_id": 12, "thermal_zone_id": 7, "day": "2026-01-01",
                  "average_temperature_c": "0", "min_temperature_c": None,
                  "max_temperature_c": None, "measurement_count": 1}
        for changes in ({"day": "20260101"}, {"measurement_count": True},
                        {"measurement_count": -1}, {"average_temperature_c": None}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                daily_temperature_point(**{**values, **changes})

    def test_unsupported_and_unsubstantiated_points_are_rejected(self):
        for changes in ({"evidence_count": 0}, {"evidence_count": True}, {"temperature_c": None}, {"iso_week": 54}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.point(**changes)

