"""Pure chronology and dated-source compatibility; no unsupported persisted rows."""

from dataclasses import FrozenInstanceError
from datetime import date, datetime, timedelta

from django.test import SimpleTestCase

from .models import BiologicalMeasurement
from .scientific_time import ObservationPrecision, ObservationTime, TemporalRelation


class ScientificTimeTests(SimpleTestCase):
    def test_week_is_definitely_before_or_after_outside_inclusive_interval(self):
        observation = ObservationTime.for_iso_week(2026, 40)
        self.assertEqual(observation.interval, (date(2026, 9, 28), date(2026, 10, 4)))
        self.assertEqual(observation.compare_operation_day(date(2026, 9, 27)), TemporalRelation.AFTER)
        self.assertEqual(observation.compare_operation_day(date(2026, 10, 5)), TemporalRelation.BEFORE)

    def test_every_day_in_week_including_endpoints_is_unresolved(self):
        observation = ObservationTime.for_iso_week(2026, 40)
        for offset in range(7):
            with self.subTest(offset=offset):
                self.assertEqual(observation.compare_operation_day(
                    observation.week_start + timedelta(days=offset)), TemporalRelation.UNRESOLVED)

    def test_iso_year_transition_and_week_53(self):
        for year in (2020, 2026):
            with self.subTest(year=year):
                observation = ObservationTime.for_iso_week(year, 53)
                first, last = observation.interval
                self.assertEqual(last.year, year + 1)
                self.assertEqual(last.weekday(), 6)
                self.assertEqual((observation.iso_year, observation.iso_week), (year, 53))
                self.assertEqual(observation.compare_operation_day(first - timedelta(days=1)), TemporalRelation.AFTER)
                self.assertEqual(observation.compare_operation_day(last + timedelta(days=1)), TemporalRelation.BEFORE)
        observation = ObservationTime.for_iso_week(2020, 1)
        self.assertEqual(observation.week_start, date(2019, 12, 30))
        self.assertEqual(observation.iso_year, 2020)

    def test_exact_and_legacy_dates_preserve_same_day_semantics(self):
        for precision in (ObservationPrecision.EXACT_DATE, ObservationPrecision.LEGACY_UNVERIFIED):
            with self.subTest(precision=precision):
                observation = ObservationTime(precision, date(2026, 10, 1), date(2026, 9, 28))
                self.assertEqual(observation.interval, (date(2026, 10, 1), date(2026, 10, 1)))
                self.assertEqual(observation.compare_operation_day(date(2026, 9, 30)), TemporalRelation.AFTER)
                self.assertEqual(observation.compare_operation_day(date(2026, 10, 1)), TemporalRelation.SAME_DAY)
                self.assertEqual(observation.compare_operation_day(date(2026, 10, 2)), TemporalRelation.BEFORE)
                self.assertEqual(observation.precision, precision)

    def test_unconfirmed_occurrence_is_unresolved_for_every_precision(self):
        observations = [ObservationTime.for_iso_week(2026, 40)] + [
            ObservationTime(precision, date(2026, 10, 1), date(2026, 9, 28))
            for precision in (ObservationPrecision.EXACT_DATE, ObservationPrecision.LEGACY_UNVERIFIED)
        ]
        for observation in observations:
            self.assertEqual(observation.compare_operation_day(None), TemporalRelation.UNRESOLVED)

    def test_nonpersisted_source_retains_week_precision_without_effective_date(self):
        measurement = BiologicalMeasurement(
            temporal_precision="iso_week", measured_on=None, week_start=date(2026, 9, 28),
            polyp_count=0, strobila_count=None, polyp_state_sequence=999,
        )
        before = measurement.__dict__.copy()
        observation = ObservationTime.from_measurement(measurement)
        self.assertIsNone(observation.measured_on)
        self.assertEqual(observation.precision, ObservationPrecision.ISO_WEEK)
        self.assertEqual(observation.compare_operation_day(date(2026, 10, 1)), TemporalRelation.UNRESOLVED)
        self.assertEqual(measurement.__dict__, before)
        self.assertEqual(measurement.polyp_count, 0)
        self.assertIsNone(measurement.strobila_count)
        self.assertTrue(measurement._state.adding)

    def test_sequence_and_ingestion_timestamp_cannot_resolve_overlap(self):
        for sequence in (None, 0, 1, 999):
            for created_at in (datetime(2020, 1, 1), datetime(2030, 1, 1)):
                measurement = BiologicalMeasurement(
                    temporal_precision="iso_week", measured_on=None, week_start=date(2026, 9, 28),
                    polyp_state_sequence=sequence, created_at=created_at,
                )
                self.assertEqual(ObservationTime.from_measurement(measurement).compare_operation_day(
                    date(2026, 10, 1)), TemporalRelation.UNRESOLVED)

    def test_chronology_does_not_depend_on_scientific_count(self):
        for value in (0, 100):
            measurement = BiologicalMeasurement(
                temporal_precision="iso_week", measured_on=None, week_start=date(2026, 9, 28),
                polyp_count=value,
            )
            self.assertEqual(ObservationTime.from_measurement(measurement).compare_operation_day(
                date(2026, 10, 1)), TemporalRelation.UNRESOLVED)
        # Missing sources are not ObservationTime instances or numeric fallbacks.
        with self.assertRaises(AttributeError):
            ObservationTime.from_measurement(None)

    def test_invalid_precision_combinations_are_not_inferred(self):
        for precision, day, week in (
            ("iso_week", date(2026, 9, 28), date(2026, 9, 28)),
            ("exact_date", None, date(2026, 9, 28)),
            ("legacy_unverified", None, date(2026, 9, 28)),
            ("exact_date", date(2026, 10, 5), date(2026, 9, 28)),
            ("iso_week", None, date(2026, 9, 29)),
            ("unknown", None, date(2026, 9, 28)),
        ):
            with self.subTest(precision=precision, day=day, week=week), self.assertRaises(ValueError):
                ObservationTime(precision, day, week)
        with self.assertRaises(ValueError):
            ObservationTime.for_iso_week(2021, 53)

    def test_timestamps_are_not_silently_converted_to_days(self):
        observation = ObservationTime.for_iso_week(2026, 40)
        with self.assertRaises(ValueError):
            observation.compare_operation_day(datetime(2026, 10, 1, 12))
        with self.assertRaises(ValueError):
            ObservationTime("exact_date", datetime(2026, 10, 1), date(2026, 9, 28))

    def test_representation_is_immutable(self):
        observation = ObservationTime.for_iso_week(2026, 40)
        with self.assertRaises(FrozenInstanceError):
            observation.measured_on = date(2026, 9, 28)
