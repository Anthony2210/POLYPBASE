"""Isolated contract tests for week-only subculture chronology diagnostics."""

import json
from dataclasses import FrozenInstanceError, asdict, replace
from datetime import date, datetime, timedelta, timezone

from django.apps import apps
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.exceptions import PermissionDenied

from apps.cultures.models import Box, BoxLineage, SubcultureAllocation, SubcultureEvent

from .models import BiologicalMeasurement
from .test_import_planner import MeasurementImportPlannerTests as PlannerHelpers


CHRONOLOGY = "week_subculture_chronology_conflict"
WEEK_FIELDS = ("box_reference", "iso_year", "iso_week", "polyp_count", "ephyrae_count")


class PlannerHelperMixin:
    # Reuse fixtures and guards without discovering the existing planner tests again.
    setUpTestData = classmethod(PlannerHelpers.setUpTestData.__func__)
    prepare = PlannerHelpers.prepare
    source = PlannerHelpers.source
    measurement = PlannerHelpers.measurement
    reject_non_select = PlannerHelpers.reject_non_select
    plan = PlannerHelpers.plan
    codes = PlannerHelpers.codes


# unittest discovers imported TestCase aliases too; retain only the helper methods.
del PlannerHelpers


class MeasurementImportChronologyTests(PlannerHelperMixin, TestCase):
    def box_fixture(self, number, *, foreign=False):
        return Box.objects.create(
            organization=self.foreign_org if foreign else self.org,
            strain=self.foreign_box.strain if foreign else self.strain,
            global_code=f"APL-{'OTHER' if foreign else 'QA'}-1.{number:03d}",
            box_number=f"{number:03d}", status=Box.Status.ACTIVE,
        )

    def event(self, *, parent=None, day=None, legacy=False, partial=False,
              allocated=2, after=8, occurred_at=None):
        day = self.day if day is None else day
        values = dict(parent_box=self.box if parent is None else parent, event_date=day)
        if not legacy:
            # Direct fixtures obey the absolute-balance CHECK, including partial results.
            values.update(
                occurred_at=occurred_at or datetime.combine(day, datetime.min.time(), timezone.utc),
                parent_state_sequence=1,
                parent_polyp_count_before=allocated + after,
                allocated_polyp_count=None if partial else allocated,
                parent_polyp_count_after=None if partial else after,
                parent_state_snapshot={"synthetic": True},
            )
        return SubcultureEvent.objects.create(**values)

    def allocation(self, event, child, count=2, *, position=1):
        return SubcultureAllocation.objects.create(
            event=event, child_box=child, position=position,
            allocated_polyps=count, child_global_code=child.global_code,
        )

    def week_source(self, *, box=None, day=None, **changes):
        iso = (self.day if day is None else day).isocalendar()
        values = dict(
            box_reference=(self.box if box is None else box).global_code,
            iso_year=str(iso.year), iso_week=str(iso.week),
        )
        values.update(changes)
        return self.source(**values)

    def week_preparation(self, rows=None, *, observer=False):
        return self.prepare(
            [self.week_source()] if rows is None else rows,
            fields=(*WEEK_FIELDS, "observer_reference") if observer else WEEK_FIELDS,
        )

    def operation(self, event, role="parent", allocation=None):
        return dict(
            event_id=event.pk, event_date=event.event_date.isoformat(), role=role,
            allocation_id=None if allocation is None else allocation.pk,
        )

    def assert_evidence(self, plan, row_index, operations):
        row = plan.rows[row_index]
        self.assertEqual(row.outcome, "CONFLICT")
        self.assertIn(CHRONOLOGY, self.codes(row))
        self.assertEqual(sum(issue.code == CHRONOLOGY for issue in row.issues), 1)
        self.assertIsNotNone(row.chronology_conflict_id)
        evidence = next(item for item in plan.chronology_conflicts if item.id == row.chronology_conflict_id)
        self.assertEqual(evidence.box_id, row.box.id)
        self.assertEqual(evidence.week_start, row.week_start)
        monday = date.fromisoformat(row.week_start)
        self.assertEqual((evidence.iso_year, evidence.iso_week), monday.isocalendar()[:2])
        self.assertEqual(evidence.week_end, (monday + timedelta(days=6)).isoformat())
        self.assertIsInstance(evidence.operations, tuple)
        self.assertEqual([asdict(item) for item in evidence.operations], operations)
        self.assertFalse(plan.ready)
        self.assertFalse(plan.summary.ready)
        return evidence

    def assert_no_evidence(self, plan):
        self.assertEqual(plan.chronology_conflicts, ())
        for row in plan.rows:
            self.assertIsNone(row.chronology_conflict_id)
            self.assertNotIn(CHRONOLOGY, self.codes(row))

    def assert_no_subculture_queries(self, queries):
        for query in queries:
            for model in (SubcultureEvent, SubcultureAllocation, BoxLineage):
                self.assertNotIn(model._meta.db_table, query["sql"])

    def test_inclusive_monday_sunday_boundaries_and_iso_year_53(self):
        monday = date(2020, 12, 28)
        before = self.event(day=monday - timedelta(days=1))
        sunday = self.event(day=monday + timedelta(days=6))
        first = self.event(day=monday)
        after = self.event(day=monday + timedelta(days=7))
        plan = self.plan(self.week_preparation([self.week_source(day=date(2021, 1, 1))]))
        evidence = self.assert_evidence(plan, 0, [self.operation(first), self.operation(sunday)])
        self.assertEqual((evidence.iso_year, evidence.iso_week), (2020, 53))
        self.assertEqual((evidence.week_start, evidence.week_end), ("2020-12-28", "2021-01-03"))
        self.assertNotIn(before.pk, [item.event_id for item in evidence.operations])
        self.assertNotIn(after.pk, [item.event_id for item in evidence.operations])
        self.assertIsNone(plan.rows[0].source.row.time.measured_on)
        self.assertEqual(plan.rows[0].source.row.time.precision, "iso_week")

    def test_adjacent_weeks_are_independent(self):
        event = self.event()
        rows = [self.week_source(day=self.day + timedelta(days=offset)) for offset in (-7, 0, 7)]
        plan = self.plan(self.week_preparation(rows))
        self.assertEqual([row.outcome for row in plan.rows], ["CREATE", "CONFLICT", "CREATE"])
        self.assert_evidence(plan, 1, [self.operation(event)])
        self.assertEqual(len(plan.chronology_conflicts), 1)
        for index in (0, 2):
            self.assertIsNone(plan.rows[index].chronology_conflict_id)
            self.assertEqual(self.codes(plan.rows[index]), {"week_precision_persistence_required"})

    def test_parent_child_initialization_and_child_outgoing_operation(self):
        child = self.box_fixture(2)
        incoming = self.event(day=self.day + timedelta(days=1))
        allocation = self.allocation(incoming, child)
        outgoing = self.event(parent=child, day=self.day + timedelta(days=3))
        plan = self.plan(self.week_preparation([self.week_source(), self.week_source(box=child)]))
        self.assert_evidence(plan, 0, [self.operation(incoming)])
        self.assert_evidence(plan, 1, [self.operation(incoming, "child", allocation), self.operation(outgoing)])
        self.assertEqual(len(plan.chronology_conflicts), 2)

    def test_multiple_events_are_sorted_by_effective_day_then_id(self):
        late = self.event(day=self.day + timedelta(days=5))
        early = self.event(day=self.day + timedelta(days=1))
        tied = self.event(day=self.day + timedelta(days=1))
        plan = self.plan(self.week_preparation())
        self.assert_evidence(plan, 0, [self.operation(early), self.operation(tied), self.operation(late)])


    def test_unrelated_and_foreign_events_and_cross_org_allocations_are_excluded(self):
        unrelated = self.box_fixture(2)
        local_child = self.box_fixture(3)
        foreign_child = self.box_fixture(2, foreign=True)
        self.event(parent=unrelated)
        self.event(parent=unrelated, partial=True)
        self.event(parent=self.foreign_box, partial=True)
        foreign_event = self.event(parent=self.foreign_box)
        self.allocation(foreign_event, self.box, 0)
        local_event = self.event(parent=local_child)
        self.allocation(local_event, foreign_child, 0)
        rows = [self.week_source(), self.week_source(box=self.foreign_box), self.week_source(box=foreign_child)]
        plan = self.plan(self.week_preparation(rows))
        self.assertEqual([row.outcome for row in plan.rows], ["CREATE", "UNKNOWN_BOX", "UNKNOWN_BOX"])
        self.assert_no_evidence(plan)

        for row in plan.rows[1:]:
            self.assertIsNone(row.box)
            self.assertIsNone(row.existing)

    def test_partial_parent_conflicts_but_legacy_unknown_children_and_lineage_do_not(self):
        legacy_child = self.box_fixture(2)
        unknown_child = self.box_fixture(3)
        lineage_child = self.box_fixture(4)
        legacy = self.event(legacy=True)
        self.allocation(legacy, legacy_child, 0)
        partial = self.event(partial=True)
        self.allocation(partial, unknown_child, None)
        BoxLineage.objects.create(parent_box=self.box, child_box=lineage_child, subculture_event=partial)
        rows = [self.week_source(box=box) for box in (self.box, legacy_child, unknown_child, lineage_child)]
        plan = self.plan(self.week_preparation(rows))
        self.assertEqual([row.outcome for row in plan.rows], ["CONFLICT", "CREATE", "CREATE", "CREATE"])
        self.assert_evidence(plan, 0, [self.operation(partial)])
        self.assertEqual(len(plan.chronology_conflicts), 1)
        for row in plan.rows[1:]:
            self.assertIsNone(row.chronology_conflict_id)
            self.assertNotIn(CHRONOLOGY, self.codes(row))

    def test_partial_occurrence_with_null_parent_results_blocks_the_whole_plan(self):
        day = self.day + timedelta(days=2)
        partial = self.event(partial=True, day=day)
        self.assertIsNotNone(partial.occurred_at)
        self.assertIsNone(partial.allocated_polyp_count)
        self.assertIsNone(partial.parent_polyp_count_after)
        fields = ("box_reference", "measured_on", "iso_year", "iso_week", "polyp_count", "ephyrae_count")
        preparation = self.prepare([
            self.week_source(measured_on=""),
            self.source(measured_on=(self.day + timedelta(days=7)).isoformat()),
        ], fields=fields)
        plan = self.plan(preparation)
        self.assert_evidence(plan, 0, [self.operation(partial)])
        self.assertIsNone(plan.rows[0].source.row.time.measured_on)
        self.assertEqual(self.codes(plan.rows[0]), {CHRONOLOGY, "week_precision_persistence_required"})
        self.assertEqual(plan.rows[1].outcome, "CREATE")
        self.assertFalse(plan.rows[1].blocked)
        self.assertEqual(plan.summary.blocked_rows, 1)

    def test_partial_event_conflicts_for_parent_and_known_zero_child(self):
        child = self.box_fixture(2)
        partial = self.event(partial=True)
        allocation = self.allocation(partial, child, 0)
        plan = self.plan(self.week_preparation([self.week_source(), self.week_source(box=child)]))
        self.assert_evidence(plan, 0, [self.operation(partial)])
        self.assert_evidence(plan, 1, [self.operation(partial, "child", allocation)])

    def test_zero_parent_allocation_and_zero_after_are_quantitative_not_null(self):
        event = self.event(allocated=0, after=0)
        plan = self.plan(self.week_preparation())
        self.assert_evidence(plan, 0, [self.operation(event)])
        self.assertEqual(self.codes(plan.rows[0]), {CHRONOLOGY, "week_precision_persistence_required"})

    def test_exact_date_replanning_has_no_chronology_queries_or_blocker(self):
        self.event()
        self.event(partial=True)
        week = self.plan(self.week_preparation())
        self.assertEqual(week.rows[0].outcome, "CONFLICT")
        exact_preparation = self.prepare([self.source(measured_on=(self.day + timedelta(days=2)).isoformat())])
        with CaptureQueriesContext(connection) as queries:
            exact = self.plan(exact_preparation)
        self.assert_no_subculture_queries(queries)
        self.assert_no_evidence(exact)
        self.assertEqual(exact.rows[0].outcome, "CREATE")
        self.assertTrue(exact.ready)
        self.measurement(measured_on=self.day + timedelta(days=4))
        with CaptureQueriesContext(connection) as queries:
            mismatch = self.plan(exact_preparation)
        self.assert_no_subculture_queries(queries)
        self.assert_no_evidence(mismatch)
        self.assertEqual(mismatch.rows[0].outcome, "CONFLICT")
        self.assertEqual(self.codes(mismatch.rows[0]), {"exact_date_conflict"})

    def test_matching_occupied_week_has_no_evidence_and_is_ready(self):
        self.event()
        self.event(partial=True)
        measurement = self.measurement(measured_on=self.day + timedelta(days=4))
        plan = self.plan(self.week_preparation())
        self.assert_no_evidence(plan)
        self.assertEqual(plan.rows[0].outcome, "ALREADY_MATCHES")
        self.assertEqual(plan.rows[0].existing.id, measurement.pk)
        self.assertEqual(plan.rows[0].issues, ())
        self.assertTrue(plan.ready)
        self.assertTrue(plan.summary.ready)

    def test_nonmatching_occupied_week_retains_value_conflicts_and_evidence(self):
        event = self.event(partial=True)
        self.measurement(measured_on=self.day + timedelta(days=2), polyp_count=9, ephyrae_count=3)
        plan = self.plan(self.week_preparation())
        self.assert_evidence(plan, 0, [self.operation(event)])
        self.assertEqual(self.codes(plan.rows[0]), {CHRONOLOGY, "supplied_value_conflict"})
        self.assertEqual(
            {issue.field for issue in plan.rows[0].issues if issue.code == "supplied_value_conflict"},
            {"polyp_count", "ephyrae_count"},
        )
        self.assertNotIn("week_precision_persistence_required", self.codes(plan.rows[0]))
        self.assertEqual(plan.rows[0].existing.measured_on, (self.day + timedelta(days=2)).isoformat())

    def test_free_week_preserves_inactive_observer_and_persistence_blockers(self):
        Box.objects.filter(pk=self.box.pk).update(status=Box.Status.INACTIVE)
        event = self.event()
        plan = self.plan(self.week_preparation([self.week_source(observer_reference="Historical observer")], observer=True))
        self.assert_evidence(plan, 0, [self.operation(event)])
        self.assertEqual(self.codes(plan.rows[0]), {
            CHRONOLOGY, "week_precision_persistence_required", "inactive_box", "observer_resolution_unsupported",
        })
        self.assertEqual((plan.summary.conflicts, plan.summary.create_candidates, plan.summary.blocked_rows), (1, 0, 1))

    def test_identical_and_contradictory_duplicates_share_one_evidence_group(self):
        event = self.event(partial=True)
        for values, code in ((["0", "0"], "duplicate_source_slot"), (["0", "1"], "contradictory_source_slot")):
            with self.subTest(code=code):
                plan = self.plan(self.week_preparation([self.week_source(polyp_count=value) for value in values]))
                self.assertEqual(len(plan.chronology_conflicts), 1)
                self.assertEqual(len(plan.duplicate_groups), 1)
                for index in range(2):
                    self.assert_evidence(plan, index, [self.operation(event)])
                    self.assertIn(code, self.codes(plan.rows[index]))
                self.assertEqual(plan.rows[0].chronology_conflict_id, plan.rows[1].chronology_conflict_id)
                self.assertEqual(plan.rows[0].duplicate_group_id, plan.rows[1].duplicate_group_id)

    def test_occupied_matching_duplicates_still_receive_chronology_evidence(self):
        event = self.event()
        self.measurement()
        plan = self.plan(self.week_preparation([self.week_source()] * 3))
        self.assertEqual(len(plan.chronology_conflicts), 1)
        for index in range(3):
            self.assert_evidence(plan, index, [self.operation(event)])
            self.assertIn("duplicate_source_slot", self.codes(plan.rows[index]))

    def test_mixed_exact_and_week_duplicate_keeps_date_and_value_conflicts(self):
        event = self.event()
        self.measurement(measured_on=self.day + timedelta(days=3), polyp_count=5)
        fields = ("box_reference", "measured_on", "iso_year", "iso_week", "polyp_count", "ephyrae_count")
        week_row = self.week_source(measured_on="")
        exact_row = self.source(measured_on=(self.day + timedelta(days=1)).isoformat(), polyp_count="1")
        plan = self.plan(self.prepare([week_row, exact_row], fields=fields))
        self.assert_evidence(plan, 0, [self.operation(event)])
        self.assertTrue({"contradictory_source_slot", "supplied_value_conflict"} <= self.codes(plan.rows[0]))
        self.assertEqual(plan.rows[1].outcome, "CONFLICT")
        self.assertIsNone(plan.rows[1].chronology_conflict_id)
        self.assertNotIn(CHRONOLOGY, self.codes(plan.rows[1]))
        self.assertTrue({"supplied_value_conflict", "exact_date_conflict"} <= self.codes(plan.rows[1]))

    def test_only_valid_supported_week_rows_query_chronology(self):
        self.event()
        invalid = self.week_preparation([
            self.week_source(iso_week="54"), self.week_source(polyp_count=""),
            self.week_source(polyp_count="", ephyrae_count=""),
        ])
        with CaptureQueriesContext(connection) as queries:
            plan = self.plan(invalid)
        self.assert_no_subculture_queries(queries)
        self.assert_no_evidence(plan)
        self.assertFalse(plan.ready)
        unsupported = replace(self.week_preparation(), schema_version="999")
        with CaptureQueriesContext(connection) as queries:
            plan = self.plan(unsupported)
        self.assert_no_subculture_queries(queries)
        self.assert_no_evidence(plan)
        self.assertEqual(plan.rows[0].outcome, "BLOCKED")

    def test_exact_date_with_mismatched_supplied_week_is_invalid_without_queries(self):
        self.event()
        fields = ("box_reference", "measured_on", "iso_year", "iso_week", "polyp_count", "ephyrae_count")
        preparation = self.prepare([
            self.week_source(day=self.day + timedelta(days=7), measured_on=self.day.isoformat()),
        ], fields=fields)
        with CaptureQueriesContext(connection) as queries:
            plan = self.plan(preparation)
        self.assert_no_subculture_queries(queries)
        self.assert_no_evidence(plan)
        self.assertEqual(plan.rows[0].outcome, "INVALID")
        self.assertIn("date_week_mismatch", self.codes(plan.rows[0]))
        self.assertFalse(plan.ready)

    def test_evidence_slots_are_sorted_independently_of_source_order(self):
        child = self.box_fixture(2)
        later = self.day + timedelta(days=7)
        parent_later = self.event(day=later)
        child_event = self.event(parent=child)
        parent_first = self.event()
        sources = [self.week_source(box=child), self.week_source(day=later), self.week_source()]
        first = self.plan(self.week_preparation(sources))
        second = self.plan(self.week_preparation(list(reversed(sources))))
        keys = [(item.box_id, item.iso_year, item.iso_week) for item in first.chronology_conflicts]
        self.assertEqual(keys, sorted(keys))
        self.assertEqual(first.chronology_conflicts, second.chronology_conflicts)
        self.assertEqual(len({item.id for item in first.chronology_conflicts}), 3)
        for plan, events in ((first, [child_event, parent_later, parent_first]), (second, [parent_first, parent_later, child_event])):
            for index, event in enumerate(events):
                self.assert_evidence(plan, index, [self.operation(event)])

    def test_schema_serialization_fingerprint_and_frozen_evidence_dates(self):
        preparation = self.week_preparation()
        before = self.plan(preparation)
        # occurred_at deliberately falls outside the week: event_date is the effective day.
        event = self.event(partial=True, day=self.day + timedelta(days=2), occurred_at=datetime(2027, 1, 1, tzinfo=timezone.utc))
        first = self.plan(preparation)
        second = self.plan(preparation)
        evidence = self.assert_evidence(first, 0, [self.operation(event)])
        self.assertEqual(first.schema_version, "1.2")
        self.assertIsInstance(first.chronology_conflicts, tuple)
        self.assertEqual(first, second)
        self.assertEqual(first.canonical_json(), second.canonical_json())
        self.assertEqual(first.fingerprint, second.fingerprint)
        self.assertNotEqual(first.fingerprint, before.fingerprint)
        decoded = json.loads(first.canonical_json())
        self.assertEqual(decoded["chronology_conflicts"][0]["operations"][0], self.operation(event))
        self.assertEqual(decoded["rows"][0]["chronology_conflict_id"], evidence.id)
        for changed in (
            replace(evidence, week_end="2099-01-01"),
            replace(evidence, operations=(replace(evidence.operations[0], event_date="2099-01-01"),)),
            replace(evidence, operations=(replace(evidence.operations[0], event_id=event.pk + 1000),)),
            replace(evidence, operations=(replace(evidence.operations[0], role="child", allocation_id=123),)),
        ):
            self.assertNotEqual(first.fingerprint, replace(first, chronology_conflicts=(changed,)).fingerprint)
        self.assertNotEqual(first.fingerprint, replace(first, rows=(replace(first.rows[0], chronology_conflict_id=None),)).fingerprint)
        with self.assertRaises(FrozenInstanceError):
            evidence.week_start = "2099-01-01"
        with self.assertRaises(FrozenInstanceError):
            evidence.operations[0].event_date = "2099-01-01"
        self.event(day=self.day + timedelta(days=4))
        self.assertNotEqual(first.fingerprint, self.plan(preparation).fingerprint)
        self.assertEqual(first.canonical_json(), second.canonical_json())

    def test_all_application_tables_are_unchanged_with_select_only_planning(self):
        child = self.box_fixture(2)
        event = self.event(partial=True)
        self.allocation(event, child, 0)
        self.measurement(measured_on=self.day + timedelta(days=7))
        models = [model for model in apps.get_models() if model.__module__.startswith("apps.")]

        def snapshot():
            return {model._meta.label: list(model.objects.order_by("pk").values()) for model in models}

        before = snapshot()
        plan = self.plan(self.week_preparation([
            self.week_source(), self.week_source(box=child),
            self.week_source(day=self.day + timedelta(days=7)),
            self.week_source(box=self.foreign_box), self.week_source(polyp_count="bad"),
        ]))
        self.assertEqual([row.outcome for row in plan.rows], ["CONFLICT", "CONFLICT", "ALREADY_MATCHES", "UNKNOWN_BOX", "INVALID"])
        self.plan(self.week_preparation([self.week_source()] * 2))
        self.plan(self.prepare())
        with self.assertRaises(PermissionDenied):
            self.plan(self.week_preparation(), actor=self.viewer)
        self.assertEqual(snapshot(), before)

    def assert_query_bound(self, queries, *, boxes, slots, week_slots):
        def batches(count):
            return (count + 199) // 200

        self.assertLessEqual(len(queries), 3 + batches(boxes) + batches(slots) + 2 * batches(week_slots))
        self.assertEqual(sum(f'FROM "{Box._meta.db_table}"' in item["sql"] for item in queries), batches(boxes))
        self.assertEqual(sum(f'FROM "{BiologicalMeasurement._meta.db_table}"' in item["sql"] for item in queries), batches(slots))
        self.assertLessEqual(sum(f'FROM "{SubcultureEvent._meta.db_table}"' in item["sql"] for item in queries), batches(week_slots))
        self.assertLessEqual(sum(f'FROM "{SubcultureAllocation._meta.db_table}"' in item["sql"] for item in queries), batches(week_slots))

    def test_201_boxes_batch_parent_and_child_evidence_without_n_plus_one(self):
        parents = [Box(
            organization=self.org, strain=self.strain, global_code=f"APL-QA-1.{index:03d}",
            box_number=f"{index:03d}", status=Box.Status.ACTIVE,
        ) for index in range(2, 203)]
        children = [Box(
            organization=self.org, strain=self.strain, global_code=f"APL-QA-1.{index:03d}",
            box_number=f"{index:03d}", status=Box.Status.ACTIVE,
        ) for index in range(203, 404)]
        Box.objects.bulk_create([*parents, *children])
        events = [self.event(parent=parent, partial=index % 2 == 0) for index, parent in enumerate(parents)]
        allocations = [self.allocation(event, child, 0) for event, child in zip(events, children)]
        for count in (199, 200, 201):
            selected = [*parents[:count], *children[:count]]
            preparation = self.week_preparation([self.week_source(box=box) for box in selected])
            with self.subTest(count=count), CaptureQueriesContext(connection) as queries:
                plan = self.plan(preparation)
            self.assert_query_bound(queries, boxes=2 * count, slots=2 * count, week_slots=2 * count)
            self.assertEqual(len(plan.chronology_conflicts), 2 * count)
            for index in range(count):
                self.assert_evidence(plan, index, [self.operation(events[index])])
                self.assert_evidence(plan, count + index, [self.operation(events[index], "child", allocations[index])])

    def test_201_weeks_for_one_box_batch_slots_and_share_duplicate_evidence(self):
        days = [self.day + timedelta(days=7 * index) for index in range(201)]
        events = [self.event(day=day) for day in reversed(days)]
        by_day = {event.event_date: event for event in events}
        sources = [self.week_source(day=day) for day in days]
        preparation = self.week_preparation([*sources, sources[0], sources[-1]])
        with CaptureQueriesContext(connection) as queries:
            plan = self.plan(preparation)
        self.assert_query_bound(queries, boxes=1, slots=201, week_slots=201)
        self.assertEqual(len(plan.chronology_conflicts), 201)
        self.assertEqual(len(plan.rows), 203)
        for index, day in enumerate(days):
            self.assert_evidence(plan, index, [self.operation(by_day[day])])
        self.assertEqual(plan.rows[0].chronology_conflict_id, plan.rows[201].chronology_conflict_id)
        self.assertEqual(plan.rows[200].chronology_conflict_id, plan.rows[202].chronology_conflict_id)

    def test_mixed_201_exact_slots_only_batch_the_single_week_only_slot(self):
        days = [self.day + timedelta(days=7 * index) for index in range(202)]
        exact_event = self.event(day=days[0])
        week_event = self.event(day=days[-1])
        sources = [self.source(measured_on=day.isoformat()) for day in days[:-1]]
        sources.append(self.week_source(day=days[-1], measured_on=""))
        fields = ("box_reference", "measured_on", "iso_year", "iso_week", "polyp_count", "ephyrae_count")
        preparation = self.prepare(sources, fields=fields)
        with CaptureQueriesContext(connection) as queries:
            plan = self.plan(preparation)
        self.assert_query_bound(queries, boxes=1, slots=202, week_slots=1)
        self.assertEqual([row.outcome for row in plan.rows[:-1]], ["CREATE"] * 201)
        for row in plan.rows[:-1]:
            self.assertIsNone(row.chronology_conflict_id)
            self.assertNotIn(CHRONOLOGY, self.codes(row))
        self.assert_evidence(plan, 201, [self.operation(week_event)])
        self.assertEqual(len(plan.chronology_conflicts), 1)
        self.assertNotIn(exact_event.pk, [item.event_id for item in plan.chronology_conflicts[0].operations])

    def test_sparse_box_week_pairs_do_not_fetch_cross_product_events(self):
        other = self.box_fixture(2)
        next_week = self.day + timedelta(days=7)
        relevant_parent = self.event()
        relevant_child_event = self.event(parent=other, day=next_week)
        # These are in the requested boxes and date envelope, but not requested slots.
        decoy_parent = self.event(day=next_week)
        decoy_child_event = self.event(parent=other)
        outside_parent = self.box_fixture(3)
        decoy_initialization_first = self.event(parent=outside_parent, day=next_week)
        decoy_initialization_other = self.event(parent=outside_parent)
        first_allocation = self.allocation(decoy_initialization_first, self.box, 0)
        other_allocation = self.allocation(decoy_initialization_other, other, 0)
        preparation = self.week_preparation([self.week_source(), self.week_source(box=other, day=next_week)])
        with CaptureQueriesContext(connection) as queries:
            plan = self.plan(preparation)
        self.assert_query_bound(queries, boxes=2, slots=2, week_slots=2)
        self.assert_evidence(plan, 0, [self.operation(relevant_parent)])
        self.assert_evidence(plan, 1, [self.operation(relevant_child_event)])
        returned_ids = {operation.event_id for item in plan.chronology_conflicts for operation in item.operations}
        decoy_event_ids = {
            decoy_parent.pk, decoy_child_event.pk,
            decoy_initialization_first.pk, decoy_initialization_other.pk,
        }
        self.assertFalse(returned_ids & decoy_event_ids)
        # Replay only the captured SELECTs to inspect database results, not just
        # evidence filtered in Python after fetching a box/week cross product.
        with connection.execute_wrapper(self.reject_non_select), connection.cursor() as cursor:
            for query in queries:
                if f'FROM "{SubcultureEvent._meta.db_table}"' in query["sql"]:
                    cursor.execute(query["sql"])
                    self.assertFalse({row[0] for row in cursor.fetchall()} & decoy_event_ids)
                elif f'FROM "{SubcultureAllocation._meta.db_table}"' in query["sql"]:
                    cursor.execute(query["sql"])
                    self.assertFalse({row[0] for row in cursor.fetchall()} & {first_allocation.pk, other_allocation.pk})
