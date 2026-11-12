"""Database-free regression tests for proposed historical thermal attribution."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.test import SimpleTestCase

from .effective_history import (
    AdjustEventTemporal, AdjustEventZones, ConflictCode, EventIdentity, EventKind, EventOrigin,
    EventTarget, HistoryEvent, IdentityStability, InsertEvent, ModifyEvent, OccupancyState,
    SourceHistory, SourceRef, TemporalPosition, ThermalSourceKind, project_history,
)
from .effective_temperature import (
    AttributionExtent, HistoricalTemperaturePreview, ThermalEvidence, ThermalSourceHistory,
    project_temperature, utc,
)

PARIS = ZoneInfo("Europe/Paris")
A, B, C = 1, 2, 3
RAW = ThermalSourceKind.RAW_PROBE_MEASUREMENT
BUCKET = ThermalSourceKind.CONDENSED_BUCKET
DAILY = ThermalSourceKind.MIXED_DAILY_AGGREGATE
HISTORICAL = ThermalSourceKind.HISTORICAL_SETPOINT


def at(day, hour=0, minute=0):
    return datetime(2026, 3, day, hour, minute, tzinfo=PARIS)


def event(key, kind, moment, origin=None, destination=None):
    return HistoryEvent(EventIdentity(key, IdentityStability.PERSISTED), kind, EventOrigin.BOX_MOVEMENT,
                        moment if isinstance(moment, TemporalPosition) else TemporalPosition.exact(moment),
                        origin, destination)


def history(*events, operations=()):
    return project_history(SourceHistory(1, 10, frozenset((A, B, C)), events), operations=operations)


def reading(key, zone, moment, value="0", probe=11):
    return ThermalEvidence(key, 1, zone, RAW, TemporalPosition.exact(moment),
                           temperature_c=Decimal(value) if value is not None else None, probe_id=probe,
                           association_basis="probe_recorded_zone", sources=(SourceRef("temperature_measurement", key, "measurement"),))


def statistic(key, zone, start, end, *, kind=BUCKET, probe=11, value="10.01", **kwargs):
    return ThermalEvidence(key, 1, zone, kind, TemporalPosition.observation_interval(start, end),
                           temperature_c=Decimal(value), minimum_c=Decimal("-1.23"), maximum_c=Decimal("15.12"),
                           temperature_sum_c=Decimal("30.02") if kind == BUCKET else None,
                           measurement_count=3, probe_id=probe if kind == BUCKET else None, **kwargs)


def source(*evidence, start=None, end=None):
    return ThermalSourceHistory(1, 10, start or at(1), end or at(20), frozenset((A, B, C)),
                                frozenset((11, 12, 13)), evidence)


def attributed(preview):
    return {segment.evidence.key: segment for segment in preview.segments}


class ThermalProjectionTests(SimpleTestCase):
    def opening(self):
        return event("open", EventKind.LOCATION_ASSIGNMENT, at(1), destination=A)

    def moving(self):
        return [self.opening(), event("move", EventKind.MOVEMENT, at(5, 12), A, B)]

    def test_historical_setpoint_keeps_its_documented_value_week_and_refs(self):
        position = TemporalPosition.week(2026, 10, PARIS)
        item = ThermalEvidence("document", 1, A, HISTORICAL, position, temperature_c=Decimal("15"), box_id=10,
                               sources=(SourceRef("historical_temperature_reading", 1, "observation"),))
        preview = project_temperature(history(self.opening()), source(item))
        (segment,) = preview.segments
        self.assertIs(segment.evidence, item)
        self.assertIsNone(segment.evidence.temporal.instant)
        self.assertEqual(segment.evidence.temperature_c, Decimal("15"))
        self.assertTrue(segment.attributions[0].established)
        self.assertEqual(preview.continuity, "not_inferred")

    def test_no_evidence_does_not_synthesize_a_setpoint_or_probe_reading(self):
        preview = project_temperature(history(self.opening()), source())
        self.assertEqual(preview.segments, ())
        self.assertIn("documented_historical_setpoint_unavailable", preview.stays[0].missing_reasons)
        self.assertIn("probe_data_unavailable", preview.stays[0].missing_reasons)
        self.assertEqual(preview.gaps[0].reason, "temperature_data_unavailable")

    def test_exact_movement_uses_correct_zone_at_actual_half_open_instants(self):
        movement = at(5, 12)
        data = source(reading("a_before", A, movement - timedelta(microseconds=1), "15"),
                      reading("a_after", A, movement, "15"), reading("b_before", B, movement - timedelta(microseconds=1), "10", 12),
                      reading("b_after", B, movement, "10", 12))
        preview = project_temperature(history(*self.moving()), data)
        self.assertEqual(set(attributed(preview)), {"a_before", "b_after"})
        self.assertEqual({segment.evidence.key for segment in preview.unassigned}, {"a_after", "b_before"})
        self.assertTrue(all(segment.attributions[0].established for segment in preview.segments))
        self.assertEqual(attributed(preview)["b_after"].evidence.temporal.instant, movement)

    def test_destination_draft_recomputes_sources_without_rewriting_them(self):
        data = source(reading("a", A, at(3)), reading("b", B, at(8), probe=12), reading("c", C, at(8), probe=13))
        baseline = project_temperature(history(*self.moving()), data)
        proposed = history(*self.moving(), operations=[AdjustEventZones(EventTarget("move"), destination_zone_id=C)])
        draft = project_temperature(proposed, data)
        self.assertEqual(set(attributed(baseline)), {"a", "b"})
        self.assertEqual(set(attributed(draft)), {"a", "c"})
        self.assertFalse(attributed(draft)["c"].attributions[0].established)
        self.assertIn("draft_assertion", attributed(draft)["c"].attributions[0].provisional_reasons)
        self.assertEqual(data.evidence[1].zone_id, B)

    def test_date_draft_recomputes_both_sides(self):
        data = source(reading("a", A, at(7)), reading("b", B, at(7), probe=12))
        self.assertEqual(set(attributed(project_temperature(history(*self.moving()), data))), {"b"})
        proposed = history(*self.moving(), operations=[AdjustEventTemporal(EventTarget("move"), TemporalPosition.exact(at(9)))])
        self.assertEqual(set(attributed(project_temperature(proposed, data))), {"a"})

    def test_earlier_event_changes_later_segments_and_preserves_chain_conflicts(self):
        events = [*self.moving(), event("later", EventKind.MOVEMENT, at(10), B, C)]
        data = source(reading("a", A, at(8)), reading("b", B, at(8), probe=12), reading("c", C, at(12), probe=13))
        proposed = history(*events, operations=[AdjustEventZones(EventTarget("move"), destination_zone_id=A)])
        # The same-zone draft is invalid and must not silently change history.
        self.assertTrue(proposed.rejected_operations)
        proposed = history(*events, operations=[AdjustEventZones(EventTarget("move"), destination_zone_id=C)])
        preview = project_temperature(proposed, data)
        self.assertNotIn("b", attributed(preview))
        self.assertIn("c", attributed(preview))
        self.assertIn(ConflictCode.ORIGIN_DESTINATION_DISCONTINUITY, [item.code for item in preview.history.conflicts])
        self.assertTrue(attributed(preview)["c"].attributions[0].provisional_reasons)

    def test_origin_draft_preserves_invalid_premise_in_thermal_results(self):
        proposed = history(*self.moving(), operations=[AdjustEventZones(EventTarget("move"), origin_zone_id=C)])
        preview = project_temperature(proposed, source(reading("b", B, at(8), probe=12)))
        attribution = preview.segments[0].attributions[0]
        self.assertFalse(attribution.established)
        self.assertIn("conflict:origin_destination_discontinuity", attribution.provisional_reasons)

    def test_kind_draft_can_remove_an_assignment_and_later_reactivate_it(self):
        events = [*self.moving(), event("again", EventKind.REACTIVATION, at(10), destination=C)]
        proposed = history(*events, operations=[ModifyEvent(EventTarget("move"), kind=EventKind.DEACTIVATION,
                                                           destination_zone_id=None)])
        preview = project_temperature(proposed, source(reading("b", B, at(8), probe=12), reading("c", C, at(12), probe=13)))
        self.assertEqual(set(attributed(preview)), {"c"})
        self.assertTrue(any(gap.reason == "inactive" for gap in preview.gaps))
        self.assertFalse(preview.segments[0].attributions[0].established)

    def test_deactivation_reactivation_keeps_a_gap_not_a_direct_transfer(self):
        events = [self.opening(), event("stop", EventKind.DEACTIVATION, at(5), A),
                  event("restart", EventKind.REACTIVATION, at(10), destination=B)]
        preview = project_temperature(history(*events), source(reading("a", A, at(3)), reading("during", A, at(8)),
                                                               reading("b", B, at(12), probe=12)))
        self.assertEqual(set(attributed(preview)), {"a", "b"})
        (gap,) = [gap for gap in preview.gaps if gap.reason == "inactive"]
        self.assertEqual((gap.start, gap.end), (utc(at(5)), utc(at(10))))
        self.assertEqual(preview.stays[1].occupancy.state, OccupancyState.INACTIVE)
        self.assertEqual(preview.segments[1].attributions[0].occupancy_index, 2)

    def test_exit_without_reentry_does_not_continue_assigning_temperature(self):
        events = [self.opening(), event("exit", EventKind.PERIOD_BOUNDARY, at(5), A)]
        preview = project_temperature(history(*events), source(reading("later", A, at(8))))
        self.assertEqual(preview.segments, ())
        self.assertTrue(any(gap.reason == "no_assigned_location" for gap in preview.gaps))

    def test_empty_history_is_unknown_even_when_temperature_data_exists(self):
        preview = project_temperature(history(), source(reading("a", A, at(3))))
        self.assertEqual(preview.segments, ())
        self.assertEqual(preview.gaps[0].reason, "occupancy_history_unavailable")
        self.assertEqual((preview.gaps[0].start, preview.gaps[0].end), (utc(at(1)), utc(at(20))))

    def test_history_start_does_not_backfill_an_earlier_location(self):
        preview = project_temperature(history(event("open", EventKind.LOCATION_ASSIGNMENT, at(3), destination=A)),
                                      source(reading("before", A, at(2))))
        self.assertEqual(preview.gaps[0].reason, "occupancy_history_unavailable")
        self.assertEqual(preview.gaps[0].end, utc(at(3)))
        self.assertEqual(preview.segments, ())

    def test_multiple_probes_remain_separate_without_weighting_or_combining(self):
        data = source(reading("one", A, at(3), "0", 11), reading("two", A, at(3), "-2", 12))
        preview = project_temperature(history(self.opening()), data)
        self.assertEqual(len(preview.segments), 2)
        self.assertEqual([segment.evidence.probe_id for segment in preview.segments], [11, 12])
        self.assertEqual([segment.evidence.temperature_c for segment in preview.segments], [Decimal("0"), Decimal("-2")])
        self.assertTrue(all(not segment.overlapping_source_keys for segment in preview.segments))

    def test_daily_aggregate_crossing_intraday_move_is_not_exact_exposure(self):
        data = source(statistic("a_day", A, at(5), at(6), kind=DAILY, daily_computed_from_probes=False),
                      statistic("b_day", B, at(5), at(6), kind=DAILY, daily_computed_from_probes=True))
        preview = project_temperature(history(*self.moving()), data)
        self.assertEqual(len(preview.segments), 2)
        for segment in preview.segments:
            attribution = segment.attributions[0]
            self.assertEqual(attribution.extent, AttributionExtent.PARTIAL)
            self.assertFalse(attribution.established)
            self.assertIsNone(segment.evidence.temperature_sum_c)
            self.assertIsNone(segment.evidence.probe_id)
            self.assertEqual(segment.evidence.measurement_count, 3)
        self.assertIs(preview.segments[0].evidence.daily_computed_from_probes, False)

    def test_whole_day_statistic_keeps_its_source_semantics_without_becoming_raw(self):
        item = statistic("manual_or_mixed", A, at(3), at(4), kind=DAILY, daily_computed_from_probes=False)
        preview = project_temperature(history(self.opening()), source(item))
        self.assertTrue(preview.segments[0].attributions[0].established)
        self.assertEqual(preview.segments[0].evidence.source_kind, DAILY)
        self.assertIn("probe_data_unavailable", preview.stays[0].missing_reasons)

    def test_condensed_bucket_entirely_within_stay_keeps_all_original_statistics(self):
        item = statistic("whole", A, at(3), at(3, 0, 15), last_measured_at=at(3, 0, 13))
        preview = project_temperature(history(*self.moving()), source(item))
        (segment,) = preview.segments
        self.assertIs(segment.evidence, item)
        self.assertEqual(segment.attributions[0].extent, AttributionExtent.COMPLETE)
        self.assertTrue(segment.attributions[0].established)
        self.assertIsNone(segment.evidence.last_temperature_c)
        self.assertEqual(segment.evidence.temperature_sum_c, Decimal("30.02"))
        self.assertNotEqual(segment.evidence.temperature_sum_c, segment.evidence.temperature_c * 3)

    def test_bucket_crossing_exact_movement_is_whole_and_partial_not_prorated(self):
        item = statistic("crossing", A, at(5, 11, 55), at(5, 12, 10))
        preview = project_temperature(history(*self.moving()), source(item))
        (segment,) = preview.segments
        attribution = segment.attributions[0]
        self.assertEqual(attribution.extent, AttributionExtent.PARTIAL)
        self.assertEqual(attribution.overlap_end, utc(at(5, 12)))
        self.assertFalse(attribution.established)
        self.assertEqual(segment.evidence.temperature_sum_c, Decimal("30.02"))
        self.assertEqual(segment.evidence.measurement_count, 3)
        self.assertEqual(segment.evidence.temporal.before, at(5, 12, 10))

    def test_same_zone_revisit_has_one_bucket_with_two_candidate_masks_not_two_statistics(self):
        events = [*self.moving(), event("return", EventKind.MOVEMENT, at(5, 13), B, A)]
        item = statistic("revisit", A, at(5, 11), at(5, 14))
        preview = project_temperature(history(*events), source(item))
        self.assertEqual(len(preview.segments), 1)
        self.assertEqual([item.occupancy_index for item in preview.segments[0].attributions], [0, 2])
        self.assertTrue(all(not item.established for item in preview.segments[0].attributions))
        self.assertEqual(preview.segments[0].evidence.measurement_count, 3)

    def test_bucket_partly_outside_request_keeps_original_support(self):
        item = statistic("request", A, at(3), at(3, 0, 15))
        preview = project_temperature(history(self.opening()), source(item, start=at(3, 0, 5), end=at(3, 0, 10)))
        attribution = preview.segments[0].attributions[0]
        self.assertIn("partial_request_support", attribution.uncertainty_reasons)
        self.assertFalse(attribution.established)
        self.assertEqual(preview.segments[0].evidence.temporal.not_before, at(3))
        self.assertEqual(preview.segments[0].evidence.measurement_count, 3)

    def test_uncertain_movement_never_uses_ordering_anchor_as_actual_time(self):
        uncertain = TemporalPosition.observation_interval(at(5), at(9), ordering_anchor=at(8))
        events = [self.opening(), event("uncertain", EventKind.RECONSTRUCTED_MOVEMENT, uncertain, A, B)]
        data = source(reading("a", A, at(7)), reading("b", B, at(7), probe=12), reading("after", B, at(10), probe=12))
        preview = project_temperature(history(*events), data)
        self.assertEqual(set(attributed(preview)), {"a", "b", "after"})
        for key in ("a", "b"):
            attribution = attributed(preview)[key].attributions[0]
            self.assertEqual(attribution.extent, AttributionExtent.POSSIBLE)
            self.assertFalse(attribution.established)
        self.assertTrue(attributed(preview)["after"].attributions[0].established)
        self.assertIs(preview.stays[0].occupancy.end, uncertain)

    def test_iso_week_movement_has_no_invented_physical_timestamp(self):
        week = TemporalPosition.week(2026, 10, PARIS)
        preview = project_temperature(history(self.opening(), event("week", EventKind.RECONSTRUCTED_MOVEMENT, week, A, B)),
                                      source(reading("a", A, at(4)), reading("b", B, at(4), probe=12)))
        self.assertEqual(len(preview.segments), 2)
        self.assertTrue(all(not segment.attributions[0].established for segment in preview.segments))
        self.assertIsNone(preview.stays[1].occupancy.start.instant)

    def test_recorded_boundary_is_not_certified_as_an_exact_physical_boundary(self):
        boundary = TemporalPosition.recorded_boundary(at(5))
        proposed = history(self.opening(), event("boundary", EventKind.PERIOD_BOUNDARY, boundary, A, B))
        preview = project_temperature(proposed, source(reading("b", B, at(8), probe=12)))
        attribution = preview.segments[0].attributions[0]
        self.assertFalse(attribution.established)
        self.assertIn("recorded_boundary_not_physical_instant", attribution.uncertainty_reasons)
        empty = project_temperature(proposed, source())
        self.assertTrue(all(not gap.established for gap in empty.gaps))
        self.assertTrue(all("recorded_boundary_not_physical_instant" in gap.uncertainty_reasons for gap in empty.gaps))

    def test_unknown_end_keeps_later_attribution_possible_only(self):
        unknown = TemporalPosition.unknown(not_before=at(5))
        preview = project_temperature(history(self.opening(), event("stop", EventKind.DEACTIVATION, unknown, A)),
                                      source(reading("a", A, at(12))))
        self.assertEqual(preview.segments[0].attributions[0].extent, AttributionExtent.POSSIBLE)
        self.assertFalse(preview.segments[0].attributions[0].established)

    def test_impossible_draft_stays_informative_without_verified_thermal_segments(self):
        events = [self.opening(), event("stop", EventKind.DEACTIVATION, at(3), A),
                  event("impossible", EventKind.MOVEMENT, at(5), A, B),
                  event("later", EventKind.MOVEMENT, at(10), B, C)]
        preview = project_temperature(history(*events), source(reading("b", B, at(8), probe=12), reading("c", C, at(12), probe=13)))
        self.assertIn(ConflictCode.MOVEMENT_DURING_INACTIVE_PERIOD, [item.code for item in preview.history.conflicts])
        self.assertTrue(preview.segments)
        self.assertTrue(all(not segment.attributions[0].established for segment in preview.segments))
        self.assertTrue(all("conflict:movement_during_inactive_period" in segment.attributions[0].provisional_reasons
                            for segment in preview.segments))

    def test_unplaced_events_cannot_silently_validate_thermal_attribution(self):
        preview = project_temperature(history(self.opening(), event("unplaced", EventKind.REACTIVATION,
                                                                    TemporalPosition.unknown(), destination=B)),
                                      source(reading("a", A, at(3))))
        self.assertFalse(preview.segments[0].attributions[0].established)
        self.assertIn("unplaced_events", preview.segments[0].attributions[0].provisional_reasons)

    def test_zero_negative_and_unknown_temperature_stay_distinct(self):
        preview = project_temperature(history(self.opening()), source(
            reading("zero", A, at(3), "0"), reading("negative", A, at(4), "-3.25"), reading("unknown", A, at(5), None)))
        data = attributed(preview)
        self.assertEqual(data["zero"].evidence.temperature_c, Decimal("0"))
        self.assertEqual(data["negative"].evidence.temperature_c, Decimal("-3.25"))
        self.assertIsNone(data["unknown"].evidence.temperature_c)
        self.assertFalse(data["unknown"].attributions[0].established)
        self.assertIn("temperature_value_unknown", data["unknown"].attributions[0].uncertainty_reasons)

    def test_raw_and_bucket_overlaps_never_claim_independent_additive_samples(self):
        bucket = statistic("bucket", A, at(3), at(3, 0, 15))
        raw = reading("raw", A, at(3, 0, 5))
        adjacent = reading("adjacent", A, at(3, 0, 15))
        preview = project_temperature(history(self.opening()), source(bucket, raw, adjacent))
        data = attributed(preview)
        self.assertEqual(len(data), 3)
        self.assertEqual(data["bucket"].overlapping_source_keys, ("raw",))
        self.assertEqual(data["raw"].overlapping_source_keys, ("bucket",))
        self.assertFalse(data["raw"].attributions[0].established)
        self.assertEqual(data["raw"].evidence.temporal.instant, at(3, 0, 5))
        self.assertTrue(data["adjacent"].attributions[0].established)
        self.assertEqual(data["bucket"].evidence.measurement_count, 3)

    def test_raw_at_bucket_start_is_flagged_but_exclusive_end_is_independent(self):
        bucket = statistic("bucket", A, at(3), at(3, 0, 15))
        preview = project_temperature(history(self.opening()), source(
            bucket, reading("at_start", A, at(3)), reading("at_end", A, at(3, 0, 15))))
        data = attributed(preview)
        self.assertEqual(data["bucket"].overlapping_source_keys, ("at_start",))
        self.assertEqual(data["at_start"].overlapping_source_keys, ("bucket",))
        self.assertFalse(data["at_start"].attributions[0].established)
        self.assertEqual(data["at_end"].overlapping_source_keys, ())
        self.assertTrue(data["at_end"].attributions[0].established)

    def test_empty_definite_intersections_are_absent_not_reversed_or_outside_request(self):
        uncertain = TemporalPosition.observation_interval(at(5), at(9))
        preview = project_temperature(history(self.opening(), event("move", EventKind.RECONSTRUCTED_MOVEMENT, uncertain, A, B)),
                                      source(start=at(6), end=at(7)))
        self.assertEqual(len(preview.stays), 2)
        self.assertTrue(all(stay.definite_start is None and stay.definite_end is None for stay in preview.stays))
        wider = project_temperature(preview.history, source())
        for stay in wider.stays:
            if stay.definite_start is not None:
                self.assertLess(stay.definite_start, stay.definite_end)
                self.assertGreaterEqual(stay.definite_start, wider.start)
                self.assertLessEqual(stay.definite_end, wider.end)

    def test_uncertain_inactive_gaps_never_claim_established_absence_of_assignment(self):
        uncertain = TemporalPosition.observation_interval(at(5), at(9))
        stopping = history(self.opening(), event("stop", EventKind.DEACTIVATION, uncertain, A))
        preview = project_temperature(stopping, source(reading("a", A, at(6, 12)), start=at(6), end=at(7)))
        (gap,) = [gap for gap in preview.gaps if gap.reason == "inactive"]
        self.assertFalse(gap.established)
        self.assertEqual(gap.provisional_reasons, ())
        self.assertEqual(gap.uncertainty_reasons, ("uncertain_occupancy_boundary",))
        self.assertFalse(preview.segments[0].attributions[0].established)
        starting = history(self.opening(), event("stop", EventKind.DEACTIVATION, at(3), A),
                           event("restart", EventKind.REACTIVATION, uncertain, destination=B))
        preview = project_temperature(starting, source(reading("b", B, at(6, 12), probe=12), start=at(6), end=at(7)))
        (gap,) = [gap for gap in preview.gaps if gap.reason == "inactive"]
        self.assertFalse(gap.established)
        self.assertIn("uncertain_occupancy_boundary", gap.uncertainty_reasons)
        certain = project_temperature(starting, source(start=at(3), end=at(4)))
        self.assertTrue(next(gap for gap in certain.gaps if gap.reason == "inactive").established)

    def test_overlapping_buckets_stay_unresolved_but_other_probes_are_independent(self):
        data = source(statistic("first", A, at(3), at(3, 0, 15)), statistic("second", A, at(3, 0, 10), at(3, 0, 25)),
                      statistic("other_probe", A, at(3), at(3, 0, 15), probe=12))
        result = attributed(project_temperature(history(self.opening()), data))
        self.assertEqual(result["first"].overlapping_source_keys, ("second",))
        self.assertEqual(result["other_probe"].overlapping_source_keys, ())

    def test_exact_boundaries_and_ordering_across_repeated_dst_hour_use_utc(self):
        first = datetime(2026, 10, 25, 2, 30, tzinfo=PARIS, fold=0)
        second = datetime(2026, 10, 25, 2, 30, tzinfo=PARIS, fold=1)
        self.assertEqual(first, second)  # Python wall-time equality must not drive attribution.
        beginning = datetime(2026, 10, 25, 0, tzinfo=PARIS)
        end = datetime(2026, 10, 26, 0, tzinfo=PARIS)
        events = [event("open", EventKind.LOCATION_ASSIGNMENT, beginning, destination=A),
                  event("move", EventKind.MOVEMENT, second, A, B)]
        data = source(reading("first", A, first), reading("second", B, second, probe=12), start=beginning, end=end)
        preview = project_temperature(history(*events), data)
        self.assertEqual(set(attributed(preview)), {"first", "second"})
        self.assertTrue(all(segment.attributions[0].established for segment in preview.segments))
        self.assertEqual(preview.end - preview.start, timedelta(hours=25))

    def test_spring_dst_request_is_23_hours_without_timestamp_shifting(self):
        start = datetime(2026, 3, 29, 0, tzinfo=PARIS)
        end = datetime(2026, 3, 30, 0, tzinfo=PARIS)
        moment = datetime(2026, 3, 29, 3, 0, tzinfo=PARIS)
        preview = project_temperature(history(event("open", EventKind.LOCATION_ASSIGNMENT, start, destination=A)),
                                      source(reading("raw", A, moment), start=start, end=end))
        self.assertEqual(preview.end - preview.start, timedelta(hours=23))
        self.assertEqual(preview.segments[0].evidence.temporal.instant, moment)

    def test_source_and_history_scope_mismatches_fail_without_exposing_evidence(self):
        data = source(reading("a", A, at(3)))
        for bad in (replace(data, organization_id=2), replace(data, box_id=20),
                    replace(data, evidence=(replace(data.evidence[0], organization_id=2),)),
                    replace(data, evidence=(replace(data.evidence[0], zone_id=99),)),
                    replace(data, evidence=(replace(data.evidence[0], probe_id=99),)),
                    replace(data, evidence=(replace(data.evidence[0], box_id=20),))):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                project_temperature(history(self.opening()), bad)

    def test_historical_evidence_must_identify_exact_box_and_unresolved_zone_stays_unassigned(self):
        item = ThermalEvidence("document", 1, None, HISTORICAL, TemporalPosition.week(2026, 10, PARIS),
                               temperature_c=Decimal("15"), box_id=10, established=False,
                               uncertainty_reasons=("location_mapping_unavailable",))
        preview = project_temperature(history(self.opening()), source(item))
        self.assertEqual(preview.segments, ())
        self.assertEqual(preview.unassigned[0].uncertainty_reasons, ("location_mapping_unavailable", "zone_unresolved"))
        with self.assertRaises(ValueError):
            project_temperature(history(self.opening()), source(replace(item, box_id=None)))

    def test_stage_is_deterministic_reusable_and_does_not_mutate_sources(self):
        data = source(reading("b", B, at(8), probe=12), reading("a", A, at(3)))
        proposed = history(*self.moving())
        stage = HistoricalTemperaturePreview(data)
        preview = stage.preview(proposed)
        self.assertEqual(preview, project_temperature(proposed, replace(data, evidence=tuple(reversed(data.evidence)))))
        self.assertEqual(preview, stage.preview(proposed))
        self.assertIs(preview.history, proposed)
        self.assertEqual(data.evidence[0].key, "b")
        self.assertTrue(all(segment.evidence in data.evidence for segment in preview.segments))

    def test_duplicate_evidence_and_invalid_timestamps_or_values_are_refused(self):
        item = reading("a", A, at(3))
        with self.assertRaises(ValueError):
            project_temperature(history(self.opening()), source(item, item))
        for kwargs in ({"temperature_c": Decimal("NaN")}, {"temperature_c": 0.0}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                replace(item, **kwargs)
        with self.assertRaises(ValueError):
            source(start=datetime(2026, 3, 1))
        with self.assertRaises(ValueError):
            source(start=at(5), end=at(3))
