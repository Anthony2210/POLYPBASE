"""Pure tests of the effective history projection: synthetic events, no database."""

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.test import SimpleTestCase

from .effective_history import (
    AdjustEventKind, AdjustEventTemporal, AdjustEventZones, AnchorStatus, ConflictCertainty, ConflictCode,
    CurrentOccupancy, EffectiveStatus, EventIdentity, EventKind, EventOrigin, EventTarget, HistoryEvent,
    IdentityStability, InsertEvent, ModifyEvent, OccupancyEvidence, OccupancyState, ReconstructionAnchor,
    RejectionCode, SideType, SourceHistory, SourceRef, TemporalPosition, TemporalPrecision, definitely_before,
    project_history, to_payload,
)

PARIS = ZoneInfo("Europe/Paris")
A, B, C = 1, 2, 3


def at(day, hour=12, month=3):
    return datetime(2026, month, day, hour, tzinfo=PARIS)


def exact(day, hour=12, month=3):
    return TemporalPosition.exact(at(day, hour, month))


def event(key, kind, temporal, origin=None, destination=None, *, sources=(), established=True,
          stability=IdentityStability.PERSISTED, fingerprint=None, locator=(), source=EventOrigin.BOX_MOVEMENT):
    return HistoryEvent(
        identity=EventIdentity(key, stability, evidence_fingerprint=fingerprint, locator=locator),
        kind=kind, origin=source, temporal=temporal, origin_zone_id=origin, destination_zone_id=destination,
        sources=sources, established=established, provenance="synthetic",
    )


def history(*events, current=None, evidence=(), zones=(A, B, C)):
    return SourceHistory(organization_id=1, box_id=10, zone_ids=frozenset(zones), events=events,
                         current=current, occupancy_evidence=evidence)


def codes(projection):
    return [(conflict.code, conflict.certainty) for conflict in projection.conflicts]


def states(projection):
    return [(interval.state, interval.zone_id) for interval in projection.intervals]


class TemporalContractTests(SimpleTestCase):
    def test_precisions_stay_distinct_and_never_gain_an_instant(self):
        week = TemporalPosition.week(2026, 10, PARIS)
        interval = TemporalPosition.observation_interval(at(2), at(16))
        unknown = TemporalPosition.unknown(not_before=at(2))
        self.assertEqual(week.precision, TemporalPrecision.ISO_WEEK)
        self.assertEqual((week.not_before, week.before), (at(2, 0), at(9, 0)))
        for position in (week, interval, unknown, TemporalPosition.unknown()):
            self.assertIsNone(position.instant)
        self.assertIsNone(unknown.before)
        self.assertIsNone(TemporalPosition.unknown().position)

    def test_week_bounds_follow_the_local_calendar_across_dst(self):
        week = TemporalPosition.week(2026, 13, PARIS)
        self.assertEqual((week.not_before, week.before), (at(23, 0), at(30, 0)))
        utc = ZoneInfo("UTC")
        self.assertEqual(week.before.astimezone(utc) - week.not_before.astimezone(utc),
                         timedelta(days=7) - timedelta(hours=1))

    def test_invalid_positions_are_refused(self):
        with self.assertRaises(ValueError):
            TemporalPosition.exact(datetime(2026, 3, 1))
        with self.assertRaises(ValueError):
            TemporalPosition.week(2026, 60, PARIS)
        with self.assertRaises(ValueError):
            TemporalPosition.observation_interval(at(5), at(2))
        with self.assertRaises(ValueError):
            TemporalPosition(TemporalPrecision.EXACT)

    def test_order_is_proven_only_without_overlap(self):
        self.assertTrue(definitely_before(exact(1), exact(2)))
        self.assertFalse(definitely_before(exact(2), exact(2)))
        week = TemporalPosition.week(2026, 10, PARIS)
        self.assertTrue(definitely_before(week, exact(9, 0)))
        self.assertFalse(definitely_before(week, exact(8)))
        self.assertFalse(definitely_before(TemporalPosition.unknown(not_before=at(1)), exact(20)))


class ProjectionTests(SimpleTestCase):
    def base(self):
        return [
            event("movement:1", EventKind.LOCATION_ASSIGNMENT, exact(1), None, A),
            event("movement:2", EventKind.MOVEMENT, exact(5), A, B),
            event("movement:3", EventKind.MOVEMENT, exact(12), B, C),
        ]

    def test_multiple_movements_build_one_sequence_intervals_and_sides(self):
        projection = project_history(history(*self.base(), current=CurrentOccupancy("active", C)))
        self.assertEqual([item.key for item in projection.events], ["movement:1", "movement:2", "movement:3"])
        self.assertEqual(states(projection), [(OccupancyState.LOCATED, A), (OccupancyState.LOCATED, B),
                                              (OccupancyState.LOCATED, C)])
        self.assertEqual(projection.intervals[0].ended_by, "movement:2")
        self.assertIsNone(projection.intervals[-1].end)
        self.assertEqual(
            [(side.zone_id, side.side, side.event_key, side.related_zone_id) for side in projection.sides],
            [(A, SideType.ENTRY, "movement:1", None),
             (A, SideType.EXIT, "movement:2", B), (B, SideType.ENTRY, "movement:2", A),
             (B, SideType.EXIT, "movement:3", C), (C, SideType.ENTRY, "movement:3", B)])
        self.assertEqual(projection.conflicts, ())

    def test_projection_is_deterministic_and_independent_of_input_order(self):
        events = self.base()
        first = project_history(history(*events))
        second = project_history(history(*reversed(events)))
        self.assertEqual(first, second)
        self.assertEqual(json.dumps(to_payload(first), sort_keys=True), json.dumps(to_payload(second), sort_keys=True))

    def test_several_proofs_of_one_movement_are_one_event(self):
        proofs = (SourceRef("box_movement", 2, "event"), SourceRef("box_location", 7, "period_end"),
                  SourceRef("box_location", 8, "period_start"), SourceRef("audit_log", 40, "receipt", exclusive=False))
        events = self.base()
        events[1] = event("movement:2", EventKind.MOVEMENT, exact(5), A, B, sources=proofs)
        projection = project_history(history(*events))
        self.assertEqual(len(projection.events), 3)
        self.assertEqual(len([side for side in projection.sides if side.event_key == "movement:2"]), 2)
        self.assertEqual(projection.events[1].sources, proofs)
        self.assertEqual(projection.conflicts, ())

    def test_duplicate_representations_of_one_fact_are_reported_not_merged(self):
        shared = SourceRef("box_location", 8, "period_start")
        projection = project_history(history(
            event("movement:1", EventKind.LOCATION_ASSIGNMENT, exact(1), None, A),
            event("movement:2", EventKind.MOVEMENT, exact(5), A, B, sources=(shared,)),
            event("historical:9", EventKind.RECONSTRUCTED_MOVEMENT, TemporalPosition.week(2026, 9, PARIS), A, B,
                  sources=(shared,), source=EventOrigin.HISTORICAL_BOX_MOVEMENT),
        ))
        self.assertEqual(len(projection.events), 3)
        competing = [conflict for conflict in projection.conflicts
                     if conflict.code == ConflictCode.COMPETING_EVENT_REPRESENTATIONS]
        self.assertEqual(len(competing), 1)
        self.assertEqual(competing[0].event_keys, ("historical:9", "movement:2"))
        self.assertEqual(competing[0].certainty, ConflictCertainty.UNCERTAIN)

    def test_mixed_recorded_and_reconstructed_events(self):
        interval = TemporalPosition.observation_interval(at(9, 0), at(23, 0), ordering_anchor=at(16, 0))
        projection = project_history(history(
            event("movement:1", EventKind.LOCATION_ASSIGNMENT, exact(1), None, A),
            event("historical-transition:abc", EventKind.RECONSTRUCTED_MOVEMENT, interval, A, B,
                  stability=IdentityStability.DERIVED, fingerprint="f1", source=EventOrigin.HISTORICAL_TRANSITION),
            event("movement:4", EventKind.MOVEMENT, exact(25), B, C),
        ))
        self.assertEqual(states(projection), [(OccupancyState.LOCATED, A), (OccupancyState.LOCATED, B),
                                              (OccupancyState.LOCATED, C)])
        middle = projection.intervals[1]
        self.assertEqual(middle.start.precision, TemporalPrecision.OBSERVATION_INTERVAL)
        self.assertIsNone(middle.start.instant)
        self.assertTrue(middle.boundary_uncertain)
        self.assertEqual(projection.conflicts, ())

    def test_deactivation_is_an_exit_and_reactivation_an_entry(self):
        projection = project_history(history(
            event("movement:1", EventKind.LOCATION_ASSIGNMENT, exact(1), None, A),
            event("lifecycle:5", EventKind.DEACTIVATION, exact(5), A, None, source=EventOrigin.LIFECYCLE_AUDIT),
            event("movement:6", EventKind.REACTIVATION, exact(12), None, B),
        ))
        self.assertEqual(states(projection), [(OccupancyState.LOCATED, A), (OccupancyState.INACTIVE, None),
                                              (OccupancyState.LOCATED, B)])
        self.assertEqual(
            [(side.zone_id, side.side, side.related_zone_id) for side in projection.sides],
            [(A, SideType.ENTRY, None), (A, SideType.EXIT, None), (B, SideType.ENTRY, None)])
        # No direct A -> B movement exists anywhere in the projection.
        self.assertFalse(any(item.origin_zone_id == A and item.destination_zone_id == B for item in projection.events))
        self.assertEqual(projection.conflicts, ())

    def test_reactivation_in_the_same_zone_keeps_the_inactive_period(self):
        projection = project_history(history(
            event("movement:1", EventKind.LOCATION_ASSIGNMENT, exact(1), None, A),
            event("lifecycle:5", EventKind.DEACTIVATION, exact(5), None, None, source=EventOrigin.LIFECYCLE_AUDIT),
            event("movement:6", EventKind.REACTIVATION, exact(12), None, A),
        ))
        self.assertEqual(states(projection), [(OccupancyState.LOCATED, A), (OccupancyState.INACTIVE, None),
                                              (OccupancyState.LOCATED, A)])
        exit_side = projection.sides[1]
        self.assertEqual((exit_side.zone_id, exit_side.side, exit_side.zone_basis), (A, SideType.EXIT, "projected_state"))
        self.assertEqual(projection.conflicts, ())

    def test_inactive_period_conflicts_are_impossible_when_everything_is_exact(self):
        projection = project_history(history(
            event("movement:1", EventKind.LOCATION_ASSIGNMENT, exact(1), None, A),
            event("lifecycle:5", EventKind.DEACTIVATION, exact(5), A, None),
            event("movement:7", EventKind.MOVEMENT, exact(8), A, B),
            event("lifecycle:8", EventKind.DEACTIVATION, exact(10), B, None),
            event("lifecycle:9", EventKind.DEACTIVATION, exact(11), None, None),
            event("movement:10", EventKind.REACTIVATION, exact(12), None, A),
            event("movement:11", EventKind.REACTIVATION, exact(13), None, B),
        ))
        self.assertEqual(codes(projection), [
            (ConflictCode.MOVEMENT_DURING_INACTIVE_PERIOD, ConflictCertainty.IMPOSSIBLE),
            (ConflictCode.INCOMPATIBLE_LIFECYCLE_TRANSITION, ConflictCertainty.IMPOSSIBLE),
            (ConflictCode.INCOMPATIBLE_LIFECYCLE_TRANSITION, ConflictCertainty.IMPOSSIBLE),
        ])
        self.assertEqual(projection.conflicts[0].event_keys, ("lifecycle:5", "movement:7"))

    def test_impossible_premises_taint_intervals_and_both_sides_through_the_suffix(self):
        projection = project_history(history(
            event("start", EventKind.LOCATION_ASSIGNMENT, exact(1), None, A),
            event("stop", EventKind.DEACTIVATION, exact(3), A),
            event("bad", EventKind.MOVEMENT, exact(5), A, B),
            event("cascade", EventKind.MOVEMENT, exact(7), A, C),
            event("later", EventKind.MOVEMENT, exact(9), C, B),
        ))
        self.assertEqual(codes(projection), [
            (ConflictCode.MOVEMENT_DURING_INACTIVE_PERIOD, ConflictCertainty.IMPOSSIBLE),
            (ConflictCode.ORIGIN_DESTINATION_DISCONTINUITY, ConflictCertainty.IMPOSSIBLE),
        ])
        self.assertFalse(projection.intervals[0].provisional)
        self.assertTrue(all(item.provisional for item in projection.intervals[1:]))
        for item in projection.intervals[1:]:
            self.assertIn("conflict:movement_during_inactive_period", item.provisional_reasons)
        self.assertIn("conflict:origin_destination_discontinuity", projection.intervals[-1].provisional_reasons)
        for side in projection.sides:
            self.assertEqual(side.provisional, side.event_key in ("bad", "cascade", "later"))
        payload = to_payload(projection)
        self.assertTrue(payload["intervals"][-1]["provisional_reasons"])

    def test_unresolved_premises_remain_provisional_after_exact_events(self):
        projection = project_history(history(
            event("uncertain", EventKind.PERIOD_BOUNDARY, exact(1), None, A, established=False),
            event("later", EventKind.MOVEMENT, exact(5), A, B),
        ))
        self.assertEqual(projection.conflicts, ())
        self.assertTrue(all(item.provisional for item in (*projection.intervals, *projection.sides)))
        self.assertTrue(all("unestablished_provenance" in item.provisional_reasons for item in projection.intervals))

    def test_discontinuity_and_overlap_are_reported_without_repair(self):
        projection = project_history(history(
            event("movement:1", EventKind.LOCATION_ASSIGNMENT, exact(1), None, A),
            event("movement:2", EventKind.MOVEMENT, exact(5), C, B),
            event("movement:3", EventKind.LOCATION_ASSIGNMENT, exact(8), None, C),
        ))
        self.assertEqual(codes(projection), [
            (ConflictCode.ORIGIN_DESTINATION_DISCONTINUITY, ConflictCertainty.IMPOSSIBLE),
            (ConflictCode.OVERLAPPING_LOCATION_ASSIGNMENT, ConflictCertainty.IMPOSSIBLE),
        ])
        # The claimed origin is kept as stated: nothing is rewritten to fit.
        self.assertEqual(projection.events[1].origin_zone_id, C)
        self.assertEqual(projection.conflicts[0].details["zone_before"], A)

    def test_unestablished_sources_make_a_conflict_uncertain(self):
        projection = project_history(history(
            event("location:1:start", EventKind.PERIOD_BOUNDARY, TemporalPosition.recorded_boundary(at(1)), None, A,
                  established=False, source=EventOrigin.BOX_LOCATION),
            event("movement:2", EventKind.MOVEMENT, exact(5), B, C),
        ))
        conflict = projection.conflicts[0]
        self.assertEqual((conflict.code, conflict.certainty),
                         (ConflictCode.ORIGIN_DESTINATION_DISCONTINUITY, ConflictCertainty.UNCERTAIN))
        self.assertEqual(conflict.uncertainty_reasons, ("unestablished_provenance",))

    def test_identical_timestamps_are_ordered_deterministically_and_flagged(self):
        events = [
            event("movement:1", EventKind.LOCATION_ASSIGNMENT, exact(1), None, A),
            event("movement:3", EventKind.MOVEMENT, exact(5), B, C),
            event("movement:2", EventKind.MOVEMENT, exact(5), A, B),
        ]
        projection = project_history(history(*events))
        self.assertEqual(projection, project_history(history(*reversed(events))))
        self.assertEqual([item.key for item in projection.events], ["movement:1", "movement:2", "movement:3"])
        self.assertEqual(projection.events[1].ordering_ambiguous_with, ("movement:3",))
        self.assertEqual(codes(projection), [(ConflictCode.INSUFFICIENT_TEMPORAL_PRECISION, ConflictCertainty.UNCERTAIN)])
        self.assertEqual(projection.conflicts[0].event_keys, ("movement:2", "movement:3"))

    def test_ambiguous_order_downgrades_a_chain_conflict(self):
        projection = project_history(history(
            event("movement:1", EventKind.LOCATION_ASSIGNMENT, exact(1), None, A),
            event("historical:2", EventKind.RECONSTRUCTED_MOVEMENT, TemporalPosition.week(2026, 10, PARIS), A, B),
            event("movement:3", EventKind.MOVEMENT, exact(4), A, C),
        ))
        chain = [conflict for conflict in projection.conflicts
                 if conflict.code == ConflictCode.ORIGIN_DESTINATION_DISCONTINUITY]
        self.assertEqual(len(chain), 1)
        self.assertEqual(chain[0].certainty, ConflictCertainty.UNCERTAIN)
        self.assertIn("ambiguous_ordering", chain[0].uncertainty_reasons)

    def test_uncertain_interval_never_becomes_a_timestamp(self):
        unknown_end = TemporalPosition.unknown(not_before=at(1))
        projection = project_history(history(
            event("location:1:start", EventKind.PERIOD_BOUNDARY, TemporalPosition.recorded_boundary(at(1)), None, A,
                  established=False),
            event("lifecycle:2", EventKind.DEACTIVATION, unknown_end, A, None),
            event("lifecycle:3", EventKind.REACTIVATION, TemporalPosition.unknown(), None, B),
        ))
        self.assertEqual([item.key for item in projection.unplaced_events], ["lifecycle:3"])
        closed = projection.intervals[0]
        self.assertEqual(closed.end, unknown_end)
        self.assertIsNone(closed.end.instant)
        self.assertIsNone(closed.end.before)
        self.assertTrue(closed.boundary_uncertain)
        self.assertIn((ConflictCode.INSUFFICIENT_TEMPORAL_PRECISION, ConflictCertainty.UNCERTAIN), codes(projection))
        payload = to_payload(projection)
        self.assertIsNone(payload["intervals"][0]["end"]["instant"])

    def test_initial_historical_location_anchors_confirms_or_contradicts(self):
        week = TemporalPosition.week(2026, 9, PARIS)
        anchor = event("starting-location", EventKind.INITIAL_HISTORICAL_LOCATION, week, None, A,
                       stability=IdentityStability.DERIVED, fingerprint="s1", source=EventOrigin.STARTING_LOCATION)
        opening = project_history(history(anchor, event("movement:2", EventKind.MOVEMENT, exact(5), A, B)))
        self.assertEqual(states(opening), [(OccupancyState.LOCATED, A), (OccupancyState.LOCATED, B)])
        self.assertEqual([side.side for side in opening.sides if side.zone_id == A], [SideType.EXIT])
        confirmed = project_history(history(
            event("movement:1", EventKind.LOCATION_ASSIGNMENT, exact(1, month=2), None, A), anchor))
        self.assertEqual((states(confirmed), confirmed.conflicts), ([(OccupancyState.LOCATED, A)], ()))
        contradicted = project_history(history(
            event("movement:1", EventKind.LOCATION_ASSIGNMENT, exact(1, month=2), None, B), anchor))
        self.assertEqual(codes(contradicted), [(ConflictCode.CONTRADICTS_ESTABLISHED_EVIDENCE, ConflictCertainty.IMPOSSIBLE)])
        self.assertEqual(states(contradicted), [(OccupancyState.LOCATED, B)])
        self.assertTrue(contradicted.intervals[0].provisional)
        self.assertIn("conflict:contradicts_established_evidence", contradicted.intervals[0].provisional_reasons)

    def test_initial_anchor_is_not_an_ambiguous_physical_event(self):
        week = TemporalPosition.week(2026, 10, PARIS)
        transition_time = TemporalPosition.observation_interval(at(2, 0), at(16, 0), ordering_anchor=at(9, 0))
        anchor = event("anchor", EventKind.INITIAL_HISTORICAL_LOCATION, week, None, A)
        transition = event("transition", EventKind.RECONSTRUCTED_MOVEMENT, transition_time, A, B)
        projection = project_history(history(anchor, transition))
        self.assertEqual(projection.conflicts, ())
        self.assertTrue(all(not item.ordering_ambiguous_with for item in projection.events))
        self.assertEqual(projection.intervals[0].start, week)
        self.assertEqual(projection.intervals[0].end, transition_time)
        self.assertIsNone(projection.intervals[0].end.instant)
        self.assertTrue(all(not item.provisional for item in projection.intervals))
        contradicted = project_history(history(anchor, event(
            "transition", EventKind.RECONSTRUCTED_MOVEMENT, transition_time, C, B)))
        self.assertEqual(codes(contradicted), [
            (ConflictCode.ORIGIN_DESTINATION_DISCONTINUITY, ConflictCertainty.UNCERTAIN)])
        self.assertIn("overlapping_state_evidence", contradicted.conflicts[0].uncertainty_reasons)
        self.assertTrue(all(item.provisional for item in contradicted.intervals))
        separated = project_history(history(anchor, event("later", EventKind.MOVEMENT, exact(20), C, B)))
        self.assertEqual(codes(separated), [
            (ConflictCode.ORIGIN_DESTINATION_DISCONTINUITY, ConflictCertainty.IMPOSSIBLE)])

    def test_evidence_conflict_with_unplaced_event_remains_explicit_and_provisional(self):
        projection = project_history(history(
            *self.base()[:2],
            event("unplaced", EventKind.REACTIVATION, TemporalPosition.unknown(), None, C),
            evidence=(OccupancyEvidence(zone_id=C, within=exact(7)),),
        ))
        conflicts = [item for item in projection.conflicts
                     if item.code == ConflictCode.CONTRADICTS_ESTABLISHED_EVIDENCE]
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0].certainty, ConflictCertainty.UNCERTAIN)
        self.assertEqual([item.key for item in projection.unplaced_events], ["unplaced"])
        self.assertTrue(all(item.provisional for item in (*projection.intervals, *projection.sides)))
        self.assertTrue(all("unplaced_events" in item.provisional_reasons for item in projection.intervals))

    def test_established_occupancy_evidence_is_compared_not_enforced(self):
        observed = OccupancyEvidence(zone_id=A, within=TemporalPosition.week(2026, 11, PARIS), provenance="observation")
        consistent = project_history(history(*self.base()[:2], evidence=(
            OccupancyEvidence(zone_id=B, within=TemporalPosition.week(2026, 11, PARIS)),)))
        self.assertEqual(consistent.conflicts, ())
        contradicted = project_history(history(*self.base()[:2], evidence=(observed,)))
        self.assertEqual(codes(contradicted), [(ConflictCode.CONTRADICTS_ESTABLISHED_EVIDENCE, ConflictCertainty.IMPOSSIBLE)])
        before_history = OccupancyEvidence(zone_id=C, within=TemporalPosition.week(2026, 2, PARIS))
        self.assertEqual(project_history(history(*self.base()[:2], evidence=(before_history,))).conflicts, ())
        straddling = OccupancyEvidence(zone_id=C, within=TemporalPosition.week(2026, 10, PARIS))
        uncertain = project_history(history(*self.base()[:2], evidence=(straddling,)))
        self.assertEqual(codes(uncertain), [(ConflictCode.CONTRADICTS_ESTABLISHED_EVIDENCE, ConflictCertainty.UNCERTAIN)])

    def test_evidence_conflicts_respect_provisional_occupancy_premises(self):
        observed = OccupancyEvidence(zone_id=A, within=exact(3))
        original = event("assignment", EventKind.LOCATION_ASSIGNMENT, exact(1), None, A)
        draft = project_history(history(original, evidence=(observed,)), operations=[
            AdjustEventZones(EventTarget("assignment"), destination_zone_id=B)])
        unestablished = project_history(history(
            event("assignment", EventKind.LOCATION_ASSIGNMENT, exact(1), None, B, established=False),
            evidence=(observed,)))
        for projection in (draft, unestablished):
            with self.subTest(projection=projection):
                self.assertEqual(codes(projection), [
                    (ConflictCode.CONTRADICTS_ESTABLISHED_EVIDENCE, ConflictCertainty.UNCERTAIN)])
                self.assertIn("unestablished_provenance", projection.conflicts[0].uncertainty_reasons)
                self.assertTrue(projection.intervals[0].provisional)
                self.assertTrue(projection.sides[0].provisional)
        self.assertIn("draft_assertion", draft.conflicts[0].uncertainty_reasons)

    def test_current_occupancy_reconciliation(self):
        events = self.base()
        self.assertEqual(project_history(history(*events, current=CurrentOccupancy("active", C))).conflicts, ())
        wrong = project_history(history(*events, current=CurrentOccupancy("active", A)))
        self.assertEqual(codes(wrong), [(ConflictCode.CURRENT_OCCUPANCY_UNRECONCILED, ConflictCertainty.IMPOSSIBLE)])
        self.assertEqual(wrong.conflicts[0].details["current_zone"], A)
        pending = project_history(history(*events, current=CurrentOccupancy("pending_review", A)))
        self.assertEqual(codes(pending), [(ConflictCode.CURRENT_OCCUPANCY_UNRECONCILED, ConflictCertainty.UNCERTAIN)])
        empty = project_history(history(current=CurrentOccupancy("active", A)))
        self.assertEqual(empty.conflicts[0].uncertainty_reasons, ("no_history",))
        self.assertEqual(project_history(history(current=CurrentOccupancy("inactive", None))).conflicts, ())


class DraftOperationTests(SimpleTestCase):
    def source(self):
        return history(
            event("movement:1", EventKind.LOCATION_ASSIGNMENT, exact(1), None, A),
            event("movement:2", EventKind.MOVEMENT, exact(5), A, B),
            event("movement:3", EventKind.MOVEMENT, exact(12), B, C),
            current=CurrentOccupancy("active", C),
        )

    def test_modifying_an_early_event_preserves_the_original(self):
        source = self.source()
        untouched = to_payload(source)
        projection = project_history(source, operations=[
            AdjustEventTemporal(EventTarget("movement:1"), TemporalPosition.week(2026, 8, PARIS)),
        ])
        first = projection.events[0]
        self.assertEqual((first.key, first.status), ("movement:1", EffectiveStatus.MODIFIED))
        self.assertEqual(first.temporal.precision, TemporalPrecision.ISO_WEEK)
        self.assertEqual(first.original.temporal, exact(1))
        self.assertEqual(first.applied_operations, (0,))
        self.assertEqual(to_payload(source), untouched)
        self.assertEqual(projection.conflicts, ())
        self.assertEqual(projection.intervals[0].start.iso_week, 8)

    def test_modifying_an_early_destination_surfaces_the_downstream_break(self):
        projection = project_history(self.source(), operations=[
            AdjustEventZones(EventTarget("movement:1"), destination_zone_id=C),
        ])
        self.assertEqual(codes(projection), [(ConflictCode.ORIGIN_DESTINATION_DISCONTINUITY, ConflictCertainty.UNCERTAIN)])
        self.assertEqual(projection.conflicts[0].uncertainty_reasons, ("unestablished_provenance",))
        self.assertEqual(projection.conflicts[0].event_keys, ("movement:1", "movement:2"))
        self.assertEqual(projection.events[0].original.destination_zone_id, A)

    def test_inserted_intermediary_event(self):
        projection = project_history(self.source(), operations=[
            AdjustEventZones(EventTarget("movement:3"), origin_zone_id=A),
            InsertEvent("d1", EventKind.MOVEMENT, exact(8), B, A),
        ])
        self.assertEqual([item.key for item in projection.events], ["movement:1", "movement:2", "draft:d1", "movement:3"])
        inserted = projection.events[2]
        self.assertEqual((inserted.status, inserted.origin, inserted.original), (EffectiveStatus.INSERTED, EventOrigin.DRAFT, None))
        self.assertEqual(inserted.identity.stability, IdentityStability.DRAFT)
        self.assertEqual(states(projection), [(OccupancyState.LOCATED, A), (OccupancyState.LOCATED, B),
                                              (OccupancyState.LOCATED, A), (OccupancyState.LOCATED, C)])
        self.assertEqual((projection.conflicts, projection.rejected_operations), ((), ()))

    def test_unresolved_conflicts_of_a_draft_are_detected(self):
        projection = project_history(self.source(), operations=[InsertEvent("d1", EventKind.MOVEMENT, exact(8), B, A)])
        self.assertEqual(codes(projection), [(ConflictCode.ORIGIN_DESTINATION_DISCONTINUITY, ConflictCertainty.UNCERTAIN)])
        self.assertEqual(projection.conflicts[0].event_keys, ("draft:d1", "movement:3"))
        lifecycle = project_history(self.source(), operations=[
            InsertEvent("stop", EventKind.DEACTIVATION, exact(3), A, None),
        ])
        self.assertEqual(codes(lifecycle)[0], (ConflictCode.MOVEMENT_DURING_INACTIVE_PERIOD, ConflictCertainty.UNCERTAIN))

    def test_kind_and_combined_changes(self):
        projection = project_history(self.source(), operations=[
            InsertEvent("stop", EventKind.DEACTIVATION, exact(3), A, None),
            ModifyEvent(EventTarget("movement:2"), kind=EventKind.REACTIVATION, origin_zone_id=None),
        ])
        self.assertEqual(projection.rejected_operations, ())
        self.assertEqual(states(projection)[:3], [(OccupancyState.LOCATED, A), (OccupancyState.INACTIVE, None),
                                                  (OccupancyState.LOCATED, B)])
        self.assertEqual(projection.conflicts, ())
        second = next(item for item in projection.events if item.key == "movement:2")
        self.assertEqual((second.kind, second.original.kind), (EventKind.REACTIVATION, EventKind.MOVEMENT))

    def test_draft_assertions_do_not_establish_source_evidence(self):
        for established in (False, True):
            with self.subTest(established=established):
                source = history(event("source", EventKind.UNCLASSIFIED_MOVEMENT, exact(1), A, B,
                                       established=established))
                projection = project_history(source, operations=[
                    AdjustEventKind(EventTarget("source"), EventKind.MOVEMENT),
                    InsertEvent("new", EventKind.MOVEMENT, exact(5), B, C),
                ])
                self.assertEqual(projection.rejected_operations, ())
                self.assertTrue(all(not item.established for item in projection.events))
                self.assertEqual(projection.events[0].original.established, established)
                self.assertEqual(source.events[0].kind, EventKind.UNCLASSIFIED_MOVEMENT)
                self.assertTrue(all(item.provisional for item in (*projection.intervals, *projection.sides)))
                self.assertTrue(all("draft_assertion" in item.provisional_reasons for item in projection.intervals))

    def test_no_weekly_limit_is_imposed_on_the_historical_draft(self):
        projection = project_history(self.source(), operations=[
            AdjustEventZones(EventTarget("movement:3"), origin_zone_id=A),
            InsertEvent("d1", EventKind.MOVEMENT, exact(6), B, A),
        ])
        self.assertEqual((projection.conflicts, projection.rejected_operations), ((), ()))

    def test_invalid_operations_are_rejected_and_change_nothing(self):
        source = self.source()
        baseline = project_history(source)
        operations = [
            AdjustEventZones(EventTarget("movement:404"), destination_zone_id=A),
            AdjustEventZones(EventTarget("movement:2"), destination_zone_id=99),
            AdjustEventKind(EventTarget("movement:2"), EventKind.REACTIVATION),
            AdjustEventKind(EventTarget("movement:2"), EventKind.RECONSTRUCTED_MOVEMENT),
            AdjustEventTemporal(EventTarget("movement:2"), exact(5)),
            InsertEvent("d1", EventKind.MOVEMENT, exact(8), A, A),
            InsertEvent("d2", EventKind.PERIOD_BOUNDARY, exact(8), A, B),
            InsertEvent("d3", EventKind.DEACTIVATION, "2026-03-08", A, None),
            "not an operation",
        ]
        projection = project_history(source, operations=operations)
        self.assertEqual([item.code for item in projection.rejected_operations], [
            RejectionCode.TARGET_NOT_FOUND, RejectionCode.ZONE_OUTSIDE_INSTITUTION, RejectionCode.INVALID_SHAPE,
            RejectionCode.KIND_NOT_ALLOWED, RejectionCode.NO_CHANGE, RejectionCode.INVALID_SHAPE,
            RejectionCode.KIND_NOT_ALLOWED, RejectionCode.INVALID_TEMPORAL, RejectionCode.UNKNOWN_OPERATION,
        ])
        self.assertEqual([item.index for item in projection.rejected_operations], list(range(9)))
        self.assertEqual((projection.events, projection.intervals, projection.sides, projection.conflicts),
                         (baseline.events, baseline.intervals, baseline.sides, baseline.conflicts))

    def test_duplicate_draft_identifier_is_rejected(self):
        projection = project_history(self.source(), operations=[
            InsertEvent("d1", EventKind.DEACTIVATION, exact(20), C, None),
            InsertEvent("d1", EventKind.DEACTIVATION, exact(21), C, None),
        ])
        self.assertEqual([item.code for item in projection.rejected_operations], [RejectionCode.DUPLICATE_DRAFT_ID])


class ReconstructedIdentityTests(SimpleTestCase):
    LOCATOR = (("policy", "p"), ("origin_reading_id", 4), ("destination_reading_id", 5))

    def source(self, *, key="historical-transition:abc", fingerprint="f1"):
        return history(
            event("movement:1", EventKind.LOCATION_ASSIGNMENT, exact(1), None, A),
            event(key, EventKind.RECONSTRUCTED_MOVEMENT, TemporalPosition.week(2026, 11, PARIS), A, B,
                  stability=IdentityStability.DERIVED, fingerprint=fingerprint, locator=self.LOCATOR,
                  source=EventOrigin.HISTORICAL_TRANSITION),
        )

    def anchor(self, **overrides):
        values = {"anchor_id": "k1", "organization_id": 1, "box_id": 10, "evidence_fingerprint": "f1",
                  "locator": self.LOCATOR}
        return ReconstructionAnchor(**{**values, **overrides})

    def test_derived_key_alone_cannot_designate_an_event(self):
        change = TemporalPosition.week(2026, 12, PARIS)
        bare = project_history(self.source(), operations=[
            AdjustEventTemporal(EventTarget("historical-transition:abc"), change)])
        self.assertEqual([item.code for item in bare.rejected_operations], [RejectionCode.DERIVED_TARGET_REQUIRES_EVIDENCE])
        stale = project_history(self.source(fingerprint="f2"), operations=[
            AdjustEventTemporal(EventTarget("historical-transition:abc", "f1"), change)])
        self.assertEqual([item.code for item in stale.rejected_operations], [RejectionCode.TARGET_EVIDENCE_CHANGED])
        accepted = project_history(self.source(), operations=[
            AdjustEventTemporal(EventTarget("historical-transition:abc", "f1"), change)])
        self.assertEqual(accepted.rejected_operations, ())
        self.assertEqual(accepted.events[1].temporal.iso_week, 12)

    def test_anchor_gives_a_durable_identity_while_evidence_is_unchanged(self):
        projection = project_history(self.source(), anchors=[self.anchor()], operations=[
            AdjustEventTemporal(EventTarget("anchor:k1"), TemporalPosition.week(2026, 12, PARIS))])
        anchored = projection.events[1]
        self.assertEqual((anchored.key, anchored.identity.stability, anchored.identity.derived_key),
                         ("anchor:k1", IdentityStability.ANCHORED, "historical-transition:abc"))
        self.assertEqual(anchored.identity.evidence_fingerprint, "f1")
        self.assertEqual(projection.rejected_operations, ())
        self.assertEqual([(item.status, item.event_key) for item in projection.anchor_resolutions],
                         [(AnchorStatus.MATCHED, "anchor:k1")])
        # A recomputed hash does not break the anchor as long as the evidence is the same.
        rekeyed = project_history(self.source(key="historical-transition:other"), anchors=[self.anchor()])
        self.assertEqual(rekeyed.events[1].key, "anchor:k1")

    def test_changed_orphaned_foreign_and_contested_anchors_are_reported(self):
        changed = project_history(self.source(fingerprint="f2"), anchors=[self.anchor()])
        self.assertEqual(changed.anchor_resolutions[0].status, AnchorStatus.EVIDENCE_CHANGED)
        self.assertEqual(changed.events[1].identity.stability, IdentityStability.DERIVED)
        orphaned = project_history(self.source(fingerprint="f2"), anchors=[self.anchor(locator=(("policy", "x"),))])
        self.assertEqual(orphaned.anchor_resolutions[0].status, AnchorStatus.ORPHANED)
        for override in ({"box_id": 11}, {"organization_id": 2}):
            foreign = project_history(self.source(), anchors=[self.anchor(**override)])
            self.assertEqual(foreign.anchor_resolutions[0].status, AnchorStatus.OUT_OF_SCOPE)
            self.assertEqual(foreign.events[1].identity.stability, IdentityStability.DERIVED)
        contested = project_history(self.source(), anchors=[self.anchor(), self.anchor(anchor_id="k2")])
        self.assertEqual({item.status for item in contested.anchor_resolutions}, {AnchorStatus.AMBIGUOUS})
        self.assertEqual(contested.events[1].key, "historical-transition:abc")

    def test_anchor_requires_consistent_locator_and_fingerprint(self):
        wrong_locator = (("policy", "other"),)
        cases = [
            (self.anchor(), AnchorStatus.MATCHED),
            (self.anchor(evidence_fingerprint="stale"), AnchorStatus.EVIDENCE_CHANGED),
            (self.anchor(locator=wrong_locator, evidence_fingerprint="stale"), AnchorStatus.ORPHANED),
            (self.anchor(locator=wrong_locator), AnchorStatus.LOCATOR_MISMATCH),
            (self.anchor(locator=()), AnchorStatus.MATCHED),
        ]
        for anchor, status in cases:
            with self.subTest(anchor=anchor):
                projection = project_history(self.source(), anchors=[anchor])
                self.assertEqual(projection.anchor_resolutions[0].status, status)
                self.assertEqual(projection.events[1].identity.stability,
                                 IdentityStability.ANCHORED if status == AnchorStatus.MATCHED else IdentityStability.DERIVED)
        # A matching fingerprint elsewhere cannot override an explicit locator.
        source = self.source()
        other = event("other", EventKind.RECONSTRUCTED_MOVEMENT, exact(20), B, C,
                      stability=IdentityStability.DERIVED, fingerprint="f2", locator=wrong_locator)
        projection = project_history(history(*source.events, other), anchors=[self.anchor(locator=wrong_locator)])
        self.assertEqual(projection.anchor_resolutions[0].status, AnchorStatus.EVIDENCE_CHANGED)
        self.assertEqual(projection.anchor_resolutions[0].event_key, "other")
        self.assertTrue(all(item.identity.stability != IdentityStability.ANCHORED for item in projection.events))

    def test_duplicate_source_keys_are_a_programming_error(self):
        with self.assertRaises(ValueError):
            project_history(history(event("movement:1", EventKind.MOVEMENT, exact(1), A, B),
                                    event("movement:1", EventKind.MOVEMENT, exact(2), B, C)))


class PayloadTests(SimpleTestCase):
    def test_zero_stays_zero_and_missing_stays_missing(self):
        source = history(HistoryEvent(
            identity=EventIdentity("historical-transition:z", IdentityStability.DERIVED, evidence_fingerprint="f"),
            kind=EventKind.RECONSTRUCTED_MOVEMENT, origin=EventOrigin.HISTORICAL_TRANSITION,
            temporal=TemporalPosition.week(2026, 9, PARIS), origin_zone_id=A, destination_zone_id=B,
            details={"from_environment_c": "0", "numeric_zero": 0, "missing": None},
        ))
        details = to_payload(project_history(source))["events"][0]["original"]["details"]
        self.assertEqual(details, {"from_environment_c": "0", "missing": None, "numeric_zero": 0})
        self.assertIsNot(details["numeric_zero"], None)
        self.assertIsNone(details["missing"])
        with self.assertRaises(TypeError):
            source.events[0].details["numeric_zero"] = 1
