"""Pure result boundaries, not mixed-source authority or persisted week support."""

from dataclasses import FrozenInstanceError
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from apps.measurements.models import BiologicalMeasurement
from apps.measurements.scientific_time import ObservationTime, TemporalRelation

from .polyp_state import (
    KnownPolypState, NoEligiblePolypSource, UnresolvedPolypState,
    _legacy_polyp_state, resolve_current_polyp_state, unresolved_observation,
)


class ScientificStateContractTests(SimpleTestCase):
    def test_known_requires_integer_count_and_selected_source(self):
        for count in (None, True, -1, 1.5, "1"):
            with self.subTest(count=count), self.assertRaises(ValueError):
                KnownPolypState(count, {"kind": "measurement"})
        for source in (None, {}, "measurement"):
            with self.subTest(source=source), self.assertRaises(ValueError):
                KnownPolypState(0, source)
        known = KnownPolypState(0, {"kind": "measurement", "id": 1})
        self.assertEqual(known.status, "known")
        self.assertEqual(known.polyp_count, 0)
        self.assertEqual(NoEligiblePolypSource().status, "no_eligible_source")
        with self.assertRaises(FrozenInstanceError):
            known.polyp_count = None

    def test_absent_and_unresolved_variants_cannot_accept_count_or_selected_source(self):
        observation = ObservationTime.for_iso_week(2026, 40)
        for field, value in (("polyp_count", 0), ("source", {"kind": "measurement"}), ("status", "known")):
            with self.subTest(field=field):
                with self.assertRaises(TypeError):
                    NoEligiblePolypSource(**{field: value})
                with self.assertRaises(TypeError):
                    UnresolvedPolypState(observation, date(2026, 10, 1), **{field: value})
        for day in (date(2026, 9, 27), date(2026, 10, 5)):
            with self.assertRaises(ValueError):
                UnresolvedPolypState(observation, day)
        with self.assertRaises(ValueError):
            UnresolvedPolypState(None, None)

    def test_synthetic_week_before_after_and_overlap_do_not_select_authority(self):
        measurement = BiologicalMeasurement(
            temporal_precision="iso_week", measured_on=None, week_start=date(2026, 9, 28),
            polyp_count=100, polyp_state_sequence=999,
        )
        before = measurement.__dict__.copy()
        observation = ObservationTime.from_measurement(measurement)
        for day, relation in ((date(2026, 9, 27), TemporalRelation.AFTER),
                              (date(2026, 10, 5), TemporalRelation.BEFORE)):
            self.assertEqual(observation.compare_operation_day(day), relation)
            # Resolved chronology alone is not a known or eligible state.
            self.assertIsNone(unresolved_observation(observation, day))
        for day in (date(2026, 9, 28), date(2026, 10, 1), date(2026, 10, 4)):
            result = unresolved_observation(observation, day)
            self.assertEqual(result, UnresolvedPolypState(observation, day))
            self.assertEqual(result.status, "unresolved")
            self.assertFalse(hasattr(result, "polyp_count"))
            self.assertFalse(hasattr(result, "source"))
        self.assertEqual(measurement.__dict__, before)
        self.assertTrue(measurement._state.adding)

    def test_unknown_occurrence_keeps_none_without_fabricating_legacy_day(self):
        observations = [ObservationTime.for_iso_week(2026, 40)] + [
            ObservationTime(precision, date(2026, 10, 1), date(2026, 9, 28))
            for precision in ("exact_date", "legacy_unverified")
        ]
        for observation in observations:
            result = unresolved_observation(observation, None)
            self.assertIsNone(result.operation_day)
            self.assertEqual(result.observation, observation)
            with self.assertRaises(RuntimeError):
                _legacy_polyp_state(result, "box:1:2")

    def test_unresolved_cannot_escape_facade_or_fall_back_to_older_authority(self):
        older = KnownPolypState(50, {"kind": "measurement", "id": 1})
        result = unresolved_observation(ObservationTime.for_iso_week(2026, 40), date(2026, 10, 1))
        # Inject only at the boundary: no mixed-source precedence policy is implied.
        box = SimpleNamespace(pk=1, polyp_state_revision=2)
        with patch("apps.cultures.polyp_state.resolve_scientific_polyp_state", return_value=result) as resolver:
            with self.assertRaisesRegex(RuntimeError, "Scientific state contract violation"):
                resolve_current_polyp_state(box)
            resolver.assert_called_once_with(box)
        self.assertEqual(older.polyp_count, 50)
        self.assertFalse(hasattr(result, "polyp_count"))
        with self.assertRaises(TypeError):
            unresolved_observation(result.observation, result.operation_day, older)

    def test_unresolved_cannot_be_resolved_by_zero_count_or_newer_sequence(self):
        for count in (0, 100):
            for sequence in (None, 0, 999):
                measurement = BiologicalMeasurement(
                    temporal_precision="iso_week", measured_on=None, week_start=date(2026, 9, 28),
                    polyp_count=count, polyp_state_sequence=sequence,
                )
                result = unresolved_observation(ObservationTime.from_measurement(measurement), date(2026, 10, 1))
                with self.assertRaises(RuntimeError):
                    _legacy_polyp_state(result, "box:1:2")
                with self.assertRaises(FrozenInstanceError):
                    result.operation_day = date(2026, 10, 5)

    def test_public_dictionary_contains_only_original_fields(self):
        source = {"kind": "measurement", "id": 1, "timestamp": "2026-10-01T12:00:00+00:00",
                  "measured_on": "2026-10-01"}
        for count in (0, 10):
            self.assertEqual(_legacy_polyp_state(KnownPolypState(count, source), "box:1:2"), {
                "polyp_count": count, "source": source, "revision": "box:1:2",
            })
        self.assertEqual(_legacy_polyp_state(NoEligiblePolypSource(), "box:1:2"), {
            "polyp_count": None, "source": None, "revision": "box:1:2",
        })
