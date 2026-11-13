"""Pure before/after history previews over reviewed engines, without a DB."""

from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from itertools import permutations
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.test import SimpleTestCase

from .effective_history import (
    AdjustEventTemporal, AdjustEventZones, Conflict, ConflictCertainty, ConflictCode, EventIdentity, EventKind,
    EventOrigin, EventTarget, HistoryEvent, IdentityStability, InsertEvent, ModifyEvent, OccupancyState,
    ReconstructionAnchor, SourceHistory, SourceRef, TemporalPosition, ThermalSourceKind, project_history,
)
from .effective_temperature import ThermalEvidence, ThermalSourceHistory, project_temperature, utc
from .history_preview import (
    ConflictEvolution, HistoryPreviewSession, _conflict_changes, _thermal_signature,
)
from .history_preview_validation import OperationJustification, PreviewInputError

PARIS = ZoneInfo("Europe/Paris")
MODULE = "apps.cultures.history_preview"
A, B, C = 1, 2, 3


def at(day, hour=0):
    return datetime(2026, 3, day, hour, tzinfo=PARIS)


def event(key, kind, when, origin=None, destination=None, **kwargs):
    return HistoryEvent(EventIdentity(key, IdentityStability.PERSISTED), kind, EventOrigin.BOX_MOVEMENT,
                        when if isinstance(when, TemporalPosition) else TemporalPosition.exact(when),
                        origin, destination, **kwargs)


def raw(key, zone, when, value="0", probe=11):
    return ThermalEvidence(key, 1, zone, ThermalSourceKind.RAW_PROBE_MEASUREMENT, TemporalPosition.exact(when),
                           temperature_c=Decimal(value), probe_id=probe,
                           sources=(SourceRef("temperature_measurement", key, "measurement"),))


def sources(events, evidence=(), start=None, end=None):
    return (SourceHistory(1, 10, frozenset((A, B, C)), tuple(events)),
            ThermalSourceHistory(1, 10, start or at(1), end or at(25), frozenset((A, B, C)),
                                 frozenset((11, 12, 13)), tuple(evidence)))


class ConflictMatchingTests(SimpleTestCase):
    def replay(self, *, certainty=ConflictCertainty.UNCERTAIN, **details):
        return Conflict(ConflictCode.ORIGIN_DESTINATION_DISCONTINUITY, certainty, ("open", "move"),
                        ("unestablished_provenance",) if certainty == ConflictCertainty.UNCERTAIN else (),
                        {"state_before": "located", "zone_before": A, "claimed_origin": C, **details})

    def claim(self, reference, *, certainty=ConflictCertainty.UNCERTAIN):
        return Conflict(ConflictCode.COMPETING_EVENT_REPRESENTATIONS, certainty, ("open", "move"),
                        ("competing_sources",), {"source": "box_location", "source_id": reference, "role": "period_start"})

    def compare(self, before, after, changed=(), intervals=()):
        return _conflict_changes(before, after, set(changed), set(intervals))

    def assert_disappeared_and_introduced(self, before, after):
        changes = self.compare([before], [after])
        self.assertEqual(len(changes), 2)
        self.assertEqual({item.evolution for item in changes},
                         {ConflictEvolution.NOT_REPORTED_AFTER, ConflictEvolution.INTRODUCED})
        self.assertTrue(any(item.before is before and item.after is None for item in changes))
        self.assertTrue(any(item.before is None and item.after is after for item in changes))

    def test_different_source_evidence_never_becomes_false_aggravation(self):
        self.assert_disappeared_and_introduced(self.claim(7), self.claim(8, certainty=ConflictCertainty.IMPOSSIBLE))
        for field, value in (("source", "audit_log"), ("role", "period_end")):
            with self.subTest(field=field):
                before = self.claim(7)
                after = replace(before, certainty=ConflictCertainty.IMPOSSIBLE,
                                details={**before.details, field: value})
                self.assert_disappeared_and_introduced(before, after)

    def test_different_observation_evidence_and_provenance_are_not_paired(self):
        before = Conflict(ConflictCode.CONTRADICTS_ESTABLISHED_EVIDENCE, ConflictCertainty.UNCERTAIN,
                          ("open",), details={"evidence_zone": B, "provenance": "historical_observation_1",
                                              "projected": [["located", A]]})
        for changes in ({"evidence_zone": C}, {"provenance": "historical_observation_2"}):
            with self.subTest(changes=changes):
                after = replace(before, certainty=ConflictCertainty.IMPOSSIBLE, details={**before.details, **changes})
                self.assert_disappeared_and_introduced(before, after)

    def test_observation_without_source_identity_does_not_match_on_certainty_change_alone(self):
        before = Conflict(ConflictCode.CONTRADICTS_ESTABLISHED_EVIDENCE, ConflictCertainty.UNCERTAIN,
                          ("open",), details={"evidence_zone": B, "provenance": "observation",
                                              "projected": [["located", A]]})
        self.assert_disappeared_and_introduced(before, replace(before, certainty=ConflictCertainty.IMPOSSIBLE))
        # Full equality is still preserved, including diagnostics lacking an id.
        (change,) = self.compare([before], [before])
        self.assertEqual(change.evolution, ConflictEvolution.PREEXISTING_INDEPENDENT)

    def test_different_lifecycle_causes_do_not_match_despite_same_code_and_events(self):
        before = Conflict(ConflictCode.INCOMPATIBLE_LIFECYCLE_TRANSITION, ConflictCertainty.UNCERTAIN,
                          ("open", "move"), details={"state_before": "inactive", "zone_before": None,
                                                     "reason": "already_inactive"})
        after = replace(before, certainty=ConflictCertainty.IMPOSSIBLE,
                        details={"state_before": "located", "zone_before": A, "reason": "not_inactive"})
        self.assert_disappeared_and_introduced(before, after)

    def test_unknown_detail_schema_does_not_fall_back_to_code_and_events(self):
        before = replace(self.replay(), details={"evidence_id": 10, "reason": "external_cause"})
        self.assert_disappeared_and_introduced(before, replace(before, details={"evidence_id": 11, "reason": "external_cause"}))
        self.assert_disappeared_and_introduced(before, replace(before, certainty=ConflictCertainty.IMPOSSIBLE))

    def test_reversed_replay_event_roles_are_not_the_same_cause(self):
        before = self.replay()
        after = replace(before, event_keys=tuple(reversed(before.event_keys)), certainty=ConflictCertainty.IMPOSSIBLE)
        self.assert_disappeared_and_introduced(before, after)

    def test_identifiable_cause_can_change_diagnostic_facts(self):
        before = self.replay()
        after = self.replay(zone_before=B, claimed_origin=A)
        (change,) = self.compare([before], [after], changed=("move",))
        self.assertEqual(change.evolution, ConflictEvolution.CHANGED)
        self.assertIs(change.before, before)
        self.assertIs(change.after, after)

    def test_genuine_aggravation_keeps_the_same_identifiable_cause(self):
        before = self.replay()
        after = self.replay(certainty=ConflictCertainty.IMPOSSIBLE)
        (change,) = self.compare([before], [after])
        self.assertEqual(change.evolution, ConflictEvolution.AGGRAVATED)
        self.assertIs(change.before, before)
        self.assertIs(change.after, after)

    def test_current_occupancy_keeps_evidence_identity_separate_from_projected_facts(self):
        before = Conflict(ConflictCode.CURRENT_OCCUPANCY_UNRECONCILED, ConflictCertainty.UNCERTAIN, ("move",),
                          details={"projected_state": "located", "projected_zone": B,
                                   "current_status": "active", "current_zone": A})
        changed_projection = replace(before, details={**before.details, "projected_zone": C})
        (change,) = self.compare([before], [changed_projection])
        self.assertEqual(change.evolution, ConflictEvolution.CHANGED)
        self.assert_disappeared_and_introduced(before, replace(before, details={**before.details, "current_zone": C}))
        self.assert_disappeared_and_introduced(before, replace(before, details={**before.details, "current_status": "inactive"}))

    def test_state_anchor_conflicts_are_identifiable_by_ordered_event_references(self):
        before = Conflict(ConflictCode.CONTRADICTS_ESTABLISHED_EVIDENCE, ConflictCertainty.UNCERTAIN,
                          ("move", "anchor:proof"), details={"state_before": "located", "zone_before": A, "observed_zone": B})
        after = replace(before, certainty=ConflictCertainty.IMPOSSIBLE, details={**before.details, "zone_before": C})
        (change,) = self.compare([before], [after])
        self.assertEqual(change.evolution, ConflictEvolution.AGGRAVATED)
        self.assert_disappeared_and_introduced(before, replace(after, event_keys=("move", "anchor:different_proof")))

    def test_temporal_cluster_identity_ignores_only_member_order_not_cause(self):
        before = Conflict(ConflictCode.INSUFFICIENT_TEMPORAL_PRECISION, ConflictCertainty.UNCERTAIN,
                          ("open", "move"), details={"reason": "events_cannot_be_ordered"})
        (change,) = self.compare([before], [replace(before, event_keys=("move", "open"))])
        self.assertEqual(change.evolution, ConflictEvolution.CHANGED)
        self.assert_disappeared_and_introduced(before, replace(before, details={"reason": "unidentified_cause"}))

    def test_multiple_residuals_match_semantics_not_sorted_order(self):
        before = [self.claim(11), self.claim(22)]
        after = [self.claim(22, certainty=ConflictCertainty.IMPOSSIBLE), self.claim(11, certainty=ConflictCertainty.IMPOSSIBLE)]
        expected = self.compare(before, after)
        for old in permutations(before):
            for new in permutations(after):
                changes = self.compare(old, new)
                self.assertEqual(changes, expected)
                self.assertEqual(len(changes), 2)
                self.assertTrue(all(item.evolution == ConflictEvolution.AGGRAVATED for item in changes))
                self.assertTrue(all(item.before.details["source_id"] == item.after.details["source_id"] for item in changes))

    def test_resolved_and_introduced_conflicts_do_not_hide_behind_an_exact_match(self):
        stable, gone, added = self.claim(1), self.claim(2), self.claim(3, certainty=ConflictCertainty.IMPOSSIBLE)
        changes = self.compare([gone, stable], [added, stable])
        self.assertEqual(len(changes), 3)
        self.assertEqual({item.evolution for item in changes}, {
            ConflictEvolution.PREEXISTING_INDEPENDENT, ConflictEvolution.NOT_REPORTED_AFTER, ConflictEvolution.INTRODUCED})

    def test_exact_duplicates_and_unequal_multiplicities_are_preserved(self):
        item = self.claim(1)
        for old_count, new_count in ((3, 2), (2, 3), (2, 0), (0, 2)):
            with self.subTest(old=old_count, new=new_count):
                changes = self.compare([item] * old_count, [item] * new_count)
                self.assertEqual(sum(change.evolution == ConflictEvolution.PREEXISTING_INDEPENDENT for change in changes),
                                 min(old_count, new_count))
                self.assertEqual(sum(change.evolution == ConflictEvolution.NOT_REPORTED_AFTER for change in changes),
                                 max(0, old_count - new_count))
                self.assertEqual(sum(change.evolution == ConflictEvolution.INTRODUCED for change in changes),
                                 max(0, new_count - old_count))

    def test_identical_residual_duplicates_allow_only_proven_pair_counts(self):
        before, after = self.replay(), self.replay(certainty=ConflictCertainty.IMPOSSIBLE)
        changes = self.compare([before] * 3, [after] * 2)
        self.assertEqual(sum(item.evolution == ConflictEvolution.AGGRAVATED for item in changes), 2)
        self.assertEqual(sum(item.evolution == ConflictEvolution.NOT_REPORTED_AFTER for item in changes), 1)

    def test_ambiguous_distinct_residual_versions_are_not_paired_by_order(self):
        before = [self.replay(zone_before=A), self.replay(zone_before=B)]
        after = [self.replay(certainty=ConflictCertainty.IMPOSSIBLE, zone_before=C),
                 self.replay(certainty=ConflictCertainty.IMPOSSIBLE, zone_before=None)]
        expected = self.compare(before, after)
        self.assertEqual(len(expected), 4)
        self.assertEqual(sum(item.evolution == ConflictEvolution.NOT_REPORTED_AFTER for item in expected), 2)
        self.assertEqual(sum(item.evolution == ConflictEvolution.INTRODUCED for item in expected), 2)
        for old in permutations(before):
            for new in permutations(after):
                self.assertEqual(self.compare(old, new), expected)

    def test_exact_matches_are_consumed_before_unambiguous_semantic_residuals(self):
        exact, previous, proposed = self.replay(), self.replay(zone_before=B), self.replay(zone_before=C)
        changes = self.compare([previous, exact], [exact, proposed])
        self.assertEqual(len(changes), 2)
        self.assertTrue(any(item.evolution == ConflictEvolution.PREEXISTING_INDEPENDENT for item in changes))
        self.assertTrue(any(item.before is previous and item.after is proposed and item.evolution == ConflictEvolution.CHANGED
                            for item in changes))

    def test_independent_and_draft_dependent_exact_conflicts_keep_their_classification(self):
        item = self.replay()
        (independent,) = self.compare([item], [item], changed=("later",), intervals=("move",))
        (affected,) = self.compare([item], [item], changed=("move",))
        self.assertEqual(independent.evolution, ConflictEvolution.PREEXISTING_INDEPENDENT)
        self.assertEqual(affected.evolution, ConflictEvolution.PREEXISTING_AFFECTED)

    def test_output_is_stable_across_mixed_exact_residual_and_duplicate_permutations(self):
        first, second, third = self.claim(1), self.claim(2), self.claim(3)
        before = [first, second, second]
        after = [first, third, replace(second, certainty=ConflictCertainty.IMPOSSIBLE)]
        expected = self.compare(before, after)
        for old in permutations(before):
            for new in permutations(after):
                self.assertEqual(self.compare(old, new), expected)


class HistoryPreviewTests(SimpleTestCase):
    def events(self):
        return [event("open", EventKind.LOCATION_ASSIGNMENT, at(1), destination=A),
                event("move", EventKind.MOVEMENT, at(5), A, B),
                event("later", EventKind.MOVEMENT, at(15), B, C)]

    def session(self, events=None, evidence=()):
        return HistoryPreviewSession(*sources(self.events() if events is None else events, evidence))

    def test_empty_preview_matches_reviewed_effective_and_thermal_projection(self):
        source, data = sources(self.events(), [raw("b", B, at(8), "0", 12)])
        session = HistoryPreviewSession(source, data)
        result = session.preview()
        expected = project_history(source)
        self.assertEqual(result.before.history, expected)
        self.assertEqual(result.before.thermal, project_temperature(expected, data))
        self.assertIs(result.before, result.after)
        for name in ("event_changes", "interval_changes", "side_changes", "thermal_changes", "stay_changes", "gap_changes"):
            self.assertEqual(getattr(result, name), ())
        self.assertEqual(result.mode, "simulation")
        self.assertFalse(result.after.safety.publication_evaluated)

    def test_destination_changes_stays_sides_and_attributions_with_original_evidence(self):
        session = self.session(evidence=[raw("b", B, at(8), "0", 12), raw("c", C, at(8), "-2", 13)])
        result = session.preview(operations=[AdjustEventZones(EventTarget("move"), destination_zone_id=C)])
        (change,) = [item for item in result.event_changes if item.event_key == "move"]
        self.assertEqual((change.before.destination_zone_id, change.after.destination_zone_id), (B, C))
        self.assertIn("destination_zone_id", change.changed_fields)
        self.assertIs(change.before.original, session.source.events[1])
        self.assertIs(change.after.original, session.source.events[1])
        thermal = {item.source_key: item for item in result.thermal_changes}
        self.assertTrue(thermal["b"].before_assigned)
        self.assertFalse(thermal["b"].after_assigned)
        self.assertFalse(thermal["c"].before_assigned)
        self.assertTrue(thermal["c"].after_assigned)
        self.assertIs(thermal["c"].before.evidence, thermal["c"].after.evidence)
        self.assertTrue(result.side_changes)
        self.assertTrue(result.interval_changes)
        self.assertTrue(result.after.safety.has_provisional_occupancy)
        self.assertFalse(result.after.thermal.segments[0].attributions[0].established)

    def test_exact_time_edit_recomputes_probe_boundary(self):
        result = self.session(evidence=[raw("a", A, at(7)), raw("b", B, at(7), probe=12)]).preview(
            operations=[AdjustEventTemporal(EventTarget("move"), TemporalPosition.exact(at(9)))])
        self.assertEqual({segment.evidence.key for segment in result.before.thermal.segments}, {"b"})
        self.assertEqual({segment.evidence.key for segment in result.after.thermal.segments}, {"a"})
        change = next(item for item in result.event_changes if item.event_key == "move")
        self.assertEqual(change.before.temporal.instant, at(5))
        self.assertEqual(change.after.temporal.instant, at(9))
        self.assertIn("temporal", change.changed_fields)

    def test_early_edit_recomputes_downstream_intervals_without_inventing_movements(self):
        result = self.session(evidence=[raw("c", C, at(18), probe=13)]).preview(
            operations=[AdjustEventZones(EventTarget("move"), destination_zone_id=C)])
        self.assertEqual([item.key for item in result.after.history.events], ["open", "move", "later"])
        self.assertEqual(result.after.history.events[-1].origin_zone_id, B)
        self.assertIn("later", result.affected_event_keys)
        self.assertIn("later", [item.started_by for item in result.interval_changes])
        introduced = [item for item in result.conflict_changes if item.evolution == ConflictEvolution.INTRODUCED]
        self.assertTrue(any(item.after.code == ConflictCode.ORIGIN_DESTINATION_DISCONTINUITY for item in introduced))
        self.assertIn("conflict:origin_destination_discontinuity",
                      result.after.thermal.segments[0].attributions[0].provisional_reasons)

    def test_edit_before_thermal_window_recomputes_the_queried_stay(self):
        source, data = sources(self.events(), [raw("b", B, at(12), probe=12), raw("c", C, at(12), probe=13)],
                               start=at(10), end=at(20))
        result = HistoryPreviewSession(source, data).preview(operations=[
            AdjustEventZones(EventTarget("move"), destination_zone_id=C)])
        self.assertLess(utc(result.event_changes[0].before.temporal.instant), result.before.thermal.start)
        self.assertEqual({item.evidence.key for item in result.before.thermal.segments}, {"b"})
        self.assertEqual({item.evidence.key for item in result.after.thermal.segments}, {"c"})
        self.assertEqual([item.key for item in result.after.history.events], ["open", "move", "later"])

    def test_deactivation_and_reactivation_have_separate_exit_entry_and_gap(self):
        session = self.session(events=self.events()[:2], evidence=[raw("a", A, at(3)), raw("b", B, at(8), probe=12),
                                                                 raw("c", C, at(18), probe=13)])
        result = session.preview(operations=[ModifyEvent(EventTarget("move"), kind=EventKind.DEACTIVATION,
                                                         destination_zone_id=None),
                                             InsertEvent("restart", EventKind.REACTIVATION, TemporalPosition.exact(at(15)),
                                                         destination_zone_id=C)],
                                 justifications=[OperationJustification(1, "Historical reactivation evidence to review")])
        self.assertEqual([item.evidence.key for item in result.after.thermal.segments], ["a", "c"])
        stop = next(item for item in result.event_changes if item.event_key == "move")
        self.assertEqual((stop.after.kind, stop.after.destination_zone_id), (EventKind.DEACTIVATION, None))
        self.assertEqual([(side.side.value, side.zone_id) for side in result.after.history.sides if side.event_key == "move"],
                         [("exit", A)])
        self.assertEqual([(side.side.value, side.zone_id) for side in result.after.history.sides if side.event_key == "draft:restart"],
                         [("entry", C)])
        gap = next(gap for gap in result.after.thermal.gaps if gap.reason == "inactive")
        self.assertEqual((gap.start, gap.end), (utc(at(5)), utc(at(15))))
        self.assertEqual(result.justifications[0].operation_index, 1)
        self.assertFalse(result.after.safety.publication_evaluated)

    def test_related_operations_are_simulated_as_one_final_batch(self):
        session = self.session()
        result = session.preview(operations=[AdjustEventZones(EventTarget("move"), destination_zone_id=C),
                                             ModifyEvent(EventTarget("later"), origin_zone_id=C, destination_zone_id=A)])
        self.assertEqual(result.after.history.conflicts, ())
        self.assertEqual([interval.zone_id for interval in result.after.history.intervals], [A, C, A])
        self.assertEqual(len(result.event_changes), 2)
        self.assertTrue(all(not event.established for event in result.after.history.events[1:]))
        self.assertEqual(session.preview().after.history.intervals[-1].zone_id, C)

    def test_one_rejected_operation_rejects_the_whole_batch(self):
        session = self.session()
        with self.assertRaises(PreviewInputError) as error:
            session.preview(operations=[AdjustEventZones(EventTarget("move"), destination_zone_id=C),
                                         AdjustEventZones(EventTarget("foreign:999"), destination_zone_id=A)])
        self.assertEqual(error.exception.code, "draft_operations_rejected")
        self.assertEqual(error.exception.rejections[0].index, 1)
        self.assertIs(session.preview().before, session.before)
        self.assertEqual(session.before.history.events[1].destination_zone_id, B)

    def test_preexisting_conflict_before_a_later_edit_is_independent(self):
        events = self.events()
        events[1] = event("move", EventKind.MOVEMENT, at(5), C, B)
        result = self.session(events=events).preview(operations=[
            AdjustEventTemporal(EventTarget("later"), TemporalPosition.exact(at(18)))])
        changes = [item for item in result.conflict_changes
                   if item.after is not None and item.after.code == ConflictCode.ORIGIN_DESTINATION_DISCONTINUITY]
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0].before, changes[0].after)
        self.assertEqual(changes[0].evolution, ConflictEvolution.PREEXISTING_INDEPENDENT)
        self.assertIn("move", result.affected_event_keys)  # Its interval's end changed, not the earlier contradiction.

    def test_source_representation_conflicts_remain_independent_of_date_assertions(self):
        events = self.events()
        claim = SourceRef("box_location", 99, "period_start")
        events[1] = replace(events[1], sources=(claim,))
        events[2] = replace(events[2], sources=(claim,))
        result = self.session(events=events).preview(operations=[
            AdjustEventTemporal(EventTarget("move"), TemporalPosition.exact(at(6)))])
        change = next(item for item in result.conflict_changes
                      if item.after is not None and item.after.code == ConflictCode.COMPETING_EVENT_REPRESENTATIONS)
        self.assertEqual(change.evolution, ConflictEvolution.PREEXISTING_INDEPENDENT)

    def test_same_diagnostic_against_an_edited_event_is_preexisting_affected(self):
        events = self.events()
        events[1] = replace(events[1], origin_zone_id=C, established=False)
        result = self.session(events=events).preview(operations=[
            AdjustEventTemporal(EventTarget("move"), TemporalPosition.exact(at(6)))])
        change = next(item for item in result.conflict_changes
                      if item.after is not None and item.after.code == ConflictCode.ORIGIN_DESTINATION_DISCONTINUITY)
        self.assertEqual(change.before, change.after)
        self.assertEqual(change.evolution, ConflictEvolution.PREEXISTING_AFFECTED)

    def test_conflict_aggravation_uses_existing_certainty_not_a_new_severity_policy(self):
        events = [event("open", EventKind.LOCATION_ASSIGNMENT, at(1), destination=A),
                  event("bad", EventKind.MOVEMENT, at(5), C, B),
                  event("unplaced", EventKind.REACTIVATION, TemporalPosition.unknown(), destination=C)]
        result = self.session(events=events).preview(operations=[
            AdjustEventTemporal(EventTarget("unplaced"), TemporalPosition.exact(at(20)))])
        change = next(item for item in result.conflict_changes
                      if item.after is not None and item.after.code == ConflictCode.ORIGIN_DESTINATION_DISCONTINUITY)
        self.assertEqual(change.evolution, ConflictEvolution.AGGRAVATED)
        self.assertEqual(change.before.certainty, ConflictCertainty.UNCERTAIN)
        self.assertEqual(change.after.certainty, ConflictCertainty.IMPOSSIBLE)
        self.assertFalse(result.after.safety.publication_evaluated)

    def test_a_conflict_no_longer_reported_is_not_publication_approval(self):
        events = self.events()
        events[1] = replace(events[1], origin_zone_id=C)
        result = self.session(events=events).preview(operations=[AdjustEventZones(EventTarget("move"), origin_zone_id=A)])
        self.assertEqual(result.after.history.conflicts, ())
        self.assertTrue(any(item.evolution == ConflictEvolution.NOT_REPORTED_AFTER for item in result.conflict_changes))
        self.assertTrue(result.after.safety.has_provisional_occupancy)
        self.assertFalse(result.after.safety.publication_evaluated)

    def test_zero_negative_and_absence_are_not_conflated(self):
        session = self.session(evidence=[raw("zero", A, at(3), "0"), raw("negative", B, at(8), "-3.25", 12)])
        result = session.preview(operations=[AdjustEventZones(EventTarget("move"), destination_zone_id=C)])
        self.assertEqual(result.before.thermal.segments[0].evidence.temperature_c, Decimal("0"))
        self.assertEqual(result.before.thermal.segments[1].evidence.temperature_c, Decimal("-3.25"))
        stay = next(stay for stay in result.after.thermal.stays if stay.occupancy.started_by == "move")
        self.assertEqual(stay.source_keys, ())
        self.assertIn("temperature_data_unavailable", stay.missing_reasons)
        self.assertTrue(result.gap_changes)

    def test_unknown_date_and_provisional_sources_keep_their_metadata(self):
        events = [self.events()[0], event("stop", EventKind.DEACTIVATION, TemporalPosition.unknown(not_before=at(5)), A,
                                         established=False)]
        result = self.session(events=events, evidence=[raw("late", A, at(10))]).preview()
        self.assertIsNone(result.after.history.events[-1].temporal.instant)
        self.assertTrue(result.after.safety.has_temporal_uncertainty)
        self.assertTrue(result.after.safety.has_provisional_occupancy)
        self.assertFalse(result.after.thermal.segments[0].attributions[0].established)
        self.assertTrue(result.after.thermal.segments[0].attributions[0].provisional_reasons)

    def test_multiple_probes_and_crossing_bucket_are_never_combined_or_split(self):
        bucket = ThermalEvidence("bucket", 1, A, ThermalSourceKind.CONDENSED_BUCKET,
                                 TemporalPosition.observation_interval(at(4), at(6)), temperature_c=Decimal("10.01"),
                                 temperature_sum_c=Decimal("30.02"), measurement_count=3, probe_id=11)
        result = self.session(evidence=[bucket, raw("other", A, at(4), "0", 12)]).preview()
        self.assertEqual(len(result.after.thermal.segments), 2)
        segment = next(item for item in result.after.thermal.segments if item.evidence.key == "bucket")
        self.assertIs(segment.evidence, bucket)
        self.assertEqual(bucket.measurement_count, 3)
        self.assertEqual(bucket.temperature_sum_c, Decimal("30.02"))
        self.assertFalse(segment.attributions[0].established)
        self.assertEqual(result.after.thermal.continuity, "not_inferred")

    def test_reconstructed_anchor_keeps_identity_and_original_evidence_in_both_sides(self):
        original = replace(self.events()[1], identity=EventIdentity("derived", IdentityStability.DERIVED,
                                                                   evidence_fingerprint="a" * 64, locator=(("reading", 7),)))
        source, data = sources([self.events()[0], original])
        session = HistoryPreviewSession(source, data, anchors=[ReconstructionAnchor("anchor1", 1, 10, "a" * 64, (("reading", 7),))])
        result = session.preview(operations=[AdjustEventTemporal(EventTarget("anchor:anchor1"), TemporalPosition.exact(at(8)))])
        self.assertEqual(result.event_changes[0].event_key, "anchor:anchor1")
        for side in (result.event_changes[0].before, result.event_changes[0].after):
            self.assertEqual(side.identity.derived_key, "derived")
            self.assertEqual(side.identity.evidence_fingerprint, "a" * 64)
            self.assertEqual(side.original.temporal, original.temporal)
            self.assertEqual(side.sources, original.sources)

    def test_dst_fold_correction_is_one_hour_not_a_noop_and_keeps_original_representation(self):
        first = datetime(2026, 10, 25, 2, 30, tzinfo=PARIS, fold=0)
        second = datetime(2026, 10, 25, 2, 30, tzinfo=PARIS, fold=1)
        start = datetime(2026, 10, 24, tzinfo=PARIS)
        end = datetime(2026, 10, 26, tzinfo=PARIS)
        events = [event("open", EventKind.LOCATION_ASSIGNMENT, start, destination=A),
                  event("move", EventKind.MOVEMENT, first, A, B)]
        between = utc(first) + timedelta(minutes=30)
        source, data = sources(events, [raw("a", A, between), raw("b", B, between, probe=12)], start, end)
        result = HistoryPreviewSession(source, data).preview(operations=[
            AdjustEventTemporal(EventTarget("move"), TemporalPosition.exact(second))])
        self.assertEqual({item.evidence.key for item in result.before.thermal.segments}, {"b"})
        self.assertEqual({item.evidence.key for item in result.after.thermal.segments}, {"a"})
        change = result.event_changes[0]
        self.assertEqual(change.before.temporal.instant.fold, 0)
        self.assertEqual(change.after.temporal.instant.fold, 1)
        self.assertEqual(utc(change.after.temporal.instant) - utc(change.before.temporal.instant), timedelta(hours=1))
        self.assertIs(change.before.original, events[1])
        self.assertIs(change.after.original, events[1])
        # Equivalent representations of the same instant remain a real no-op.
        with self.assertRaises(PreviewInputError) as error:
            HistoryPreviewSession(source, data).preview(operations=[
                AdjustEventTemporal(EventTarget("move"), TemporalPosition.exact(utc(first)))])
        self.assertEqual(error.exception.rejections[0].code.value, "no_change")

    def test_thermal_difference_uses_stable_occupancy_identity_not_shifted_indices(self):
        preview = self.session(events=self.events()[:1], evidence=[raw("a", A, at(3))]).preview()
        segment = preview.before.thermal.segments[0]
        history = preview.before.history
        shifted_history = replace(history, intervals=(replace(history.intervals[0], started_by="earlier"), *history.intervals))
        shifted_segment = replace(segment, attributions=(replace(segment.attributions[0], occupancy_index=1),))
        self.assertEqual(_thermal_signature(segment, history), _thermal_signature(shifted_segment, shifted_history))

    def test_deterministic_repeated_drafts_and_input_order(self):
        evidence = [raw("a", A, at(3)), raw("b", B, at(8), probe=12)]
        operations = [AdjustEventTemporal(EventTarget("move"), TemporalPosition.exact(at(9)))]
        first = self.session(evidence=evidence)
        second = self.session(events=list(reversed(self.events())), evidence=list(reversed(evidence)))
        self.assertEqual(first.preview(operations=operations), second.preview(operations=operations))
        self.assertEqual(first.preview(operations=operations), first.preview(operations=operations))
        self.assertEqual(first.source.events[1].temporal.instant, at(5))

    def test_representative_repeated_previews_reuse_one_baseline_and_all_evidence(self):
        events = [self.events()[0]]
        itinerary = (A, B, C, A, B, C, A, B)
        for index in range(1, len(itinerary)):
            events.append(event(f"move:{index}", EventKind.MOVEMENT, at(1) + timedelta(hours=6 * index),
                                itinerary[index - 1], itinerary[index]))
        evidence = [raw(f"sample:{zone}:{probe}:{index}", zone, at(1) + timedelta(minutes=15 * index),
                        value=str(zone - 2), probe=probe)
                    for zone in (A, B, C) for probe in range(8 + 3 * zone, 11 + 3 * zone) for index in range(192)]
        self.assertEqual(len(evidence), 1728)
        with patch(f"{MODULE}.project_history", wraps=project_history) as history_engine, \
                patch(f"{MODULE}.project_temperature", wraps=project_temperature) as thermal_engine:
            source, data = sources(events, evidence)
            session = HistoryPreviewSession(source, replace(data, probe_ids=frozenset(range(11, 20))))
            for index in range(5):
                result = session.preview(operations=[AdjustEventTemporal(
                    EventTarget("move:1"), TemporalPosition.exact(at(1, 6) + timedelta(minutes=index + 1)))])
                self.assertIs(result.before, session.before)
                self.assertEqual(len(result.after.thermal.segments) + len(result.after.thermal.unassigned), 1728)
                self.assertIs(thermal_engine.call_args.args[1], session.thermal_source)
            self.assertEqual(history_engine.call_count, 6)
            self.assertEqual(thermal_engine.call_count, 6)

    def test_empty_preview_does_not_reserialize_or_compare_thermal_evidence(self):
        session = self.session(evidence=[raw("a", A, at(3))])
        with patch(f"{MODULE}._compare", side_effect=AssertionError("unnecessary diff")), \
                patch(f"{MODULE}.project_history", side_effect=AssertionError("unnecessary replay")), \
                patch(f"{MODULE}.project_temperature", side_effect=AssertionError("unnecessary thermal replay")):
            self.assertIs(session.preview().before, session.before)

    def test_storage_overlap_expansion_is_bounded_before_thermal_allocation(self):
        evidence = [ThermalEvidence(f"bucket:{index}", 1, A, ThermalSourceKind.CONDENSED_BUCKET,
                                    TemporalPosition.observation_interval(at(2) + timedelta(minutes=index), at(10)),
                                    temperature_c=Decimal("0"), probe_id=11)
                    for index in range(330)]
        with patch(f"{MODULE}.project_temperature", side_effect=AssertionError("must reject before allocation")):
            with self.assertRaises(PreviewInputError) as error:
                self.session(events=self.events()[:1], evidence=evidence)
        self.assertEqual(error.exception.code, "thermal_overlap_limit_exceeded")

    def test_attribution_work_is_bounded_before_thermal_projection(self):
        with patch(f"{MODULE}.MAX_ATTRIBUTION_WORK", 1), \
                patch(f"{MODULE}.project_temperature", side_effect=AssertionError("must reject before allocation")):
            with self.assertRaises(PreviewInputError) as error:
                self.session(evidence=[raw("a", A, at(3))])
        self.assertEqual(error.exception.code, "thermal_attribution_work_limit_exceeded")

    def test_insertions_count_toward_event_work_limit_before_draft_replay(self):
        with patch(f"{MODULE}.MAX_EVENT_COMPARISONS", 1):
            session = self.session(events=self.events()[:2])
            with patch(f"{MODULE}.project_history", side_effect=AssertionError("must reject before replay")):
                with self.assertRaises(PreviewInputError) as error:
                    session.preview(operations=[InsertEvent("new", EventKind.MOVEMENT, TemporalPosition.exact(at(10)), B, C)],
                                    justifications=[OperationJustification(0, "Proposed historical evidence")])
        self.assertEqual(error.exception.code, "history_work_limit_exceeded")

    def test_history_work_is_bounded_before_engine_projection(self):
        with patch(f"{MODULE}.MAX_EVENT_COMPARISONS", 1), \
                patch(f"{MODULE}.project_history", side_effect=AssertionError("must reject before replay")):
            with self.assertRaises(PreviewInputError) as error:
                self.session()
        self.assertEqual(error.exception.code, "history_work_limit_exceeded")
