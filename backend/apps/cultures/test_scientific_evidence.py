"""Isolated operation discovery; week-only observations are never persisted."""

from collections import defaultdict
from dataclasses import FrozenInstanceError
from datetime import datetime, time, timedelta, timezone as datetime_timezone
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from apps.accounts.models import OrganizationMembership
from apps.measurements.models import BiologicalMeasurement
from apps.measurements.scientific_time import ObservationTime, TemporalRelation
from apps.measurements.services import correct_biological_measurement
from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain

from .api_views import box_list_queryset_for_user
from .models import Box, SubcultureAllocation, SubcultureEvent, ThermalZone
from .polyp_state import current_state_prefetches, resolve_current_polyp_state
from .scientific_evidence import EvidenceCategory as Category, discover_observation_evidence
from .serializers import BoxListSerializer
from .services import create_subculture


class ScientificEvidenceTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Evidence laboratory")
        self.foreign_org = Organization.objects.create(name="Other evidence laboratory")
        self.user = get_user_model().objects.create_user(username="evidence", email="evidence@example.org")
        OrganizationMembership.objects.create(user=self.user, organization=self.org, role="lab_technician")
        self.zone = ThermalZone.objects.create(organization=self.org, name="Evidence zone")
        species = Species.objects.create(scientific_name="Aurelia evidence", genus_species_code="AEV")
        self.strain = Strain.objects.create(species=species, organization=self.org, code="AEV-E-1")
        self.number = 0
        self.box = self.new_box()
        self.week = ObservationTime.for_iso_week(2026, 40)
        self.monday, self.sunday = self.week.interval

    def new_box(self, organization=None):
        self.number += 1
        return Box.objects.create(
            organization=organization or self.org, strain=self.strain,
            global_code=f"AEV-E-1.{self.number:03d}", box_number=f"{self.number:03d}",
        )

    def operation(self, day, *, partial=False, legacy=False, box=None):
        box = box or self.box
        values = {"parent_box": box, "event_date": day}
        if not legacy:
            box.refresh_from_db()
            box.polyp_state_revision += 1
            box.save(update_fields=["polyp_state_revision"])
            values.update(
                occurred_at=datetime.combine(day, time(12), tzinfo=datetime_timezone.utc),
                parent_state_sequence=box.polyp_state_revision,
                parent_polyp_count_before=100,
                allocated_polyp_count=None if partial else 10,
                parent_polyp_count_after=None if partial else 90,
                parent_state_snapshot={"polyp_count": 100, "source": {"kind": "test"}},
            )
        return SubcultureEvent.objects.create(**values)

    def allocation(self, event, count, *, box=None):
        box = box or self.new_box()
        allocation = SubcultureAllocation.objects.create(
            event=event, child_box=box, position=event.allocations.count() + 1,
            allocated_polyps=count, child_global_code=box.global_code,
        )
        return box, allocation

    def measurement(self, day, *, count=100):
        return BiologicalMeasurement.objects.create(
            box=self.box, measured_on=day, polyp_count=count, ephyrae_count=0, user=self.user,
            temporal_precision="exact_date",
        )

    def discover(self, *, box=None, observation=None, sequence=None):
        return discover_observation_evidence(
            box=box or self.box, organization_id=self.org.pk,
            observation=observation or self.week, source_sequence=sequence,
        )

    def assert_exhaustive_parent_reference(self, result):
        # Exhaustive materialization is intentionally limited to small fixtures.
        expected = defaultdict(list)
        for event in SubcultureEvent.objects.filter(parent_box_id=result.box_id):
            if event.occurred_at is None:
                category, day = Category.LEGACY_PARENT_OPERATION, None
            else:
                category = (Category.COMPLETE_PARENT_OPERATION if event.parent_polyp_count_after is not None
                            else Category.PARTIAL_PARENT_OPERATION)
                day = event.event_date
            expected[category, result.observation.compare_operation_day(day)].append((event, day))
        parent_summaries = [row for row in result.summaries if row.allocation_id is None]
        self.assertEqual(len(parent_summaries), len(expected))
        for summary in parent_summaries:
            rows = expected[summary.category, summary.relation]
            self.assertEqual(summary.event_count, len(rows))
            days = [day for _, day in rows if day is not None]
            self.assertEqual(summary.first_operation_day, min(days) if days else None)
            self.assertEqual(summary.last_operation_day, max(days) if days else None)
            if summary.category == Category.COMPLETE_PARENT_OPERATION:
                self.assertEqual(summary.consumption.barrier_count, len(rows))
                self.assertEqual(summary.consumption.latest_barrier_sequence,
                                 max(event.parent_state_sequence for event, _ in rows))
                consumed = (None if result.source_sequence is None else sum(
                    event.parent_state_sequence >= result.source_sequence for event, _ in rows))
                self.assertEqual(summary.consumption.consumed_count, consumed)
            else:
                self.assertIsNone(summary.consumption)

    def test_iso_week_before_after_and_every_overlap_day_including_endpoints(self):
        days = [self.monday - timedelta(days=1)] + [self.monday + timedelta(days=i) for i in range(7)]
        days += [self.sunday + timedelta(days=1)]
        for day in days:
            self.operation(day)
        result = self.discover()
        counts = {row.relation: row.event_count for row in result.summaries}
        self.assertEqual(counts, {TemporalRelation.AFTER: 1, TemporalRelation.UNRESOLVED: 7,
                                  TemporalRelation.BEFORE: 1})
        self.assert_exhaustive_parent_reference(result)

    def test_exact_and_legacy_observations_keep_same_day_direction(self):
        self.operation(self.monday)
        self.operation(self.monday + timedelta(days=1))
        self.operation(self.monday + timedelta(days=2))
        for precision in ("exact_date", "legacy_unverified"):
            observation = ObservationTime(precision, self.monday + timedelta(days=1), self.monday)
            result = self.discover(observation=observation)
            self.assertEqual({row.relation for row in result.summaries},
                             {TemporalRelation.BEFORE, TemporalRelation.AFTER, TemporalRelation.SAME_DAY})
            self.assert_exhaustive_parent_reference(result)

    def test_complete_and_partial_operations_in_same_week_are_not_interchangeable(self):
        for day in (self.monday, self.sunday):
            self.operation(day)
            self.operation(day, partial=True)
        result = self.discover(sequence=1)
        self.assertEqual({row.category: row.event_count for row in result.summaries}, {
            Category.COMPLETE_PARENT_OPERATION: 2, Category.PARTIAL_PARENT_OPERATION: 2,
        })
        self.assert_exhaustive_parent_reference(result)

    def test_zero_child_initialization_and_null_allocation_even_from_partial_parent(self):
        event = self.operation(self.monday, partial=True)
        for count, category in ((0, Category.KNOWN_CHILD_INITIALIZATION),
                                (None, Category.UNKNOWN_CHILD_ALLOCATION)):
            child, allocation = self.allocation(event, count)
            result = self.discover(box=child, sequence=2)
            row, = result.summaries
            self.assertEqual(row.category, category)
            self.assertEqual(row.allocated_polyps, count)
            self.assertEqual(row.allocation_id, allocation.pk)
            self.assertEqual(row.relation, TemporalRelation.UNRESOLVED)
            if count is None:
                self.assertIsNone(row.consumption)
            else:
                self.assertEqual(row.consumption.latest_barrier_sequence, 1)
                self.assertEqual(row.consumption.consumed_count, 0)

    def test_child_barrier_consumption_known_and_unknown_sequence(self):
        child, _ = self.allocation(self.operation(self.monday), 0)
        for sequence, consumed in ((None, None), (0, 1), (1, 1), (2, 0)):
            row, = self.discover(box=child, sequence=sequence).summaries
            self.assertEqual(row.consumption.consumed_count, consumed)
            self.assertEqual(row.relation, TemporalRelation.UNRESOLVED)

    def test_legacy_occurrence_never_uses_event_date_or_allocation_sequence(self):
        for day in (self.monday - timedelta(days=100), self.sunday + timedelta(days=100)):
            event = self.operation(day, legacy=True)
            child, _ = self.allocation(event, 0)
            row, = self.discover(box=child, sequence=0).summaries
            self.assertEqual(row.category, Category.LEGACY_CHILD_OPERATION)
            self.assertEqual(row.relation, TemporalRelation.UNRESOLVED)
            self.assertIsNone(row.first_operation_day)
            self.assertIsNone(row.consumption)
        result = self.discover(sequence=0)
        row, = result.summaries
        self.assertEqual(row.category, Category.LEGACY_PARENT_OPERATION)
        self.assertEqual(row.event_count, 2)
        self.assertIsNone(row.first_operation_day)
        self.assertIsNone(row.last_operation_day)
        self.assert_exhaustive_parent_reference(result)

    def test_known_consumed_sequence_and_unknown_import_sequence_are_separate_from_time(self):
        first = self.operation(self.monday)
        second = self.operation(self.sunday)
        for sequence, consumed in ((None, None), (0, 2), (first.parent_state_sequence, 2),
                                   (second.parent_state_sequence, 1), (999, 0)):
            result = self.discover(sequence=sequence)
            row, = result.summaries
            self.assertEqual(row.relation, TemporalRelation.UNRESOLVED)
            self.assertEqual(row.consumption.consumed_count, consumed)
            self.assert_exhaustive_parent_reference(result)
        # Omission is not replaced by the current Box revision or a future order.
        result = discover_observation_evidence(box=self.box, organization_id=self.org.pk, observation=self.week)
        self.assertIsNone(result.source_sequence)
        self.assertIsNone(result.summaries[0].consumption.consumed_count)

    def test_old_consumed_ambiguous_observation_is_retained_without_poisoning_later_authority(self):
        old = self.measurement(self.monday - timedelta(days=7))
        event = self.operation(self.monday)
        later = self.measurement(self.monday + timedelta(days=7), count=17)
        before = resolve_current_polyp_state(self.box)
        result = self.discover(sequence=old.polyp_state_sequence)
        row, = result.summaries
        self.assertEqual(row.relation, TemporalRelation.UNRESOLVED)
        self.assertEqual(row.consumption.consumed_count, 1)
        self.assertEqual(row.consumption.latest_barrier_sequence, event.parent_state_sequence)
        self.assertEqual(resolve_current_polyp_state(self.box), before)
        self.assertEqual(before["source"]["id"], later.pk)
        self.assertEqual(before["polyp_count"], 17)

    def test_unconsumed_ambiguity_stays_relevant_despite_older_known_measurement(self):
        self.measurement(self.monday - timedelta(days=7))
        event = self.operation(self.monday, partial=True)
        self.operation(self.sunday)
        result = self.discover(sequence=event.parent_state_sequence + 2)
        self.assertEqual(len(result.summaries), 2)
        self.assertTrue(all(row.relation == TemporalRelation.UNRESOLVED for row in result.summaries))
        complete = next(row for row in result.summaries if row.consumption is not None)
        self.assertEqual(complete.consumption.consumed_count, 0)
        self.assert_exhaustive_parent_reference(result)

    def test_consumed_correction_cannot_resurrect_stock_and_does_not_change_discovery(self):
        measurement = self.measurement(self.monday - timedelta(days=7))
        event = self.operation(self.monday)
        before = self.discover(sequence=measurement.polyp_state_sequence)
        corrected = correct_biological_measurement(
            box_id=self.box.pk, measurement_id=measurement.pk, organization_ids=[self.org.pk], user=self.user,
            data={"measured_on": self.monday + timedelta(days=14), "polyp_count": 999},
        )
        self.assertEqual(corrected.polyp_state_sequence, measurement.polyp_state_sequence)
        self.assertEqual(self.discover(sequence=corrected.polyp_state_sequence), before)
        state = resolve_current_polyp_state(self.box)
        self.assertEqual(state["polyp_count"], 90)
        self.assertEqual(state["source"]["id"], event.pk)

    def test_different_week_measurements_and_zero_zero_do_not_affect_operation_facts(self):
        zero = self.measurement(self.monday, count=0)
        later = self.measurement(self.monday + timedelta(days=7), count=0)
        self.assertNotEqual(zero.week_start, later.week_start)
        result = self.discover(sequence=zero.polyp_state_sequence)
        self.assertEqual(result.summaries, ())
        state = resolve_current_polyp_state(self.box)
        self.assertEqual(state["polyp_count"], 0)
        self.assertEqual(state["source"]["id"], later.pk)
        self.assertEqual(list(self.box.biological_measurements.values_list("polyp_count", "ephyrae_count")),
                         [(0, 0), (0, 0)])
        empty = self.new_box()
        self.assertEqual(self.discover(box=empty).summaries, ())
        self.assertIsNone(resolve_current_polyp_state(empty)["polyp_count"])

    def test_institution_scope_rechecks_persisted_box_and_filters_foreign_parent(self):
        foreign = self.new_box(self.foreign_org)
        event = self.operation(self.monday, box=foreign)
        self.allocation(event, 0, box=self.box)
        self.assertEqual(self.discover().summaries, ())
        with self.assertRaises(PermissionDenied):
            self.discover(box=foreign)
        foreign.organization_id = self.org.pk
        with self.assertRaises(PermissionDenied):
            self.discover(box=foreign)
        self.operation(self.monday)
        result = self.discover()
        self.assertEqual(sum(row.event_count for row in result.summaries), 1)
        self.assertTrue(all(row.allocation_id is None for row in result.summaries))

    def test_discovery_is_bounded_without_truncating_a_busy_week(self):
        for size in (1, 50):
            for index in range(size):
                self.operation(self.monday + timedelta(days=index % 7), partial=index % 2 == 1)
            with CaptureQueriesContext(connection) as queries:
                result = self.discover(sequence=1)
            self.assertEqual(len(queries), 3)
            self.assertLessEqual(len(result.summaries), 2)
            self.assertEqual(sum(row.event_count for row in result.summaries), 1 if size == 1 else 51)
            parent_sql = queries[1]["sql"].upper()
            self.assertIn("GROUP BY", parent_sql)
            self.assertIn("COUNT(", parent_sql)
            self.assertNotIn("LIMIT", parent_sql)
            self.assertTrue(all("measurements_biologicalmeasurement" not in query["sql"] for query in queries))

    def test_all_parent_partitions_match_small_exhaustive_reference(self):
        for day in (self.monday - timedelta(days=7), self.monday, self.sunday,
                    self.sunday + timedelta(days=7)):
            for partial in (False, True):
                self.operation(day, partial=partial)
        self.operation(self.monday, legacy=True)
        for sequence in (None, 0, 5, 999):
            result = self.discover(sequence=sequence)
            self.assertEqual(len(result.summaries), 7)
            self.assert_exhaustive_parent_reference(result)

    def test_synthetic_week_is_immutable_nonpersisted_and_counts_cannot_resolve_it(self):
        self.operation(self.monday)
        for count in (0, 100):
            synthetic = BiologicalMeasurement(
                box=self.box, temporal_precision="iso_week", measured_on=None, week_start=self.monday,
                polyp_count=count, ephyrae_count=0,
            )
            before = synthetic.__dict__.copy()
            result = self.discover(observation=ObservationTime.from_measurement(synthetic))
            self.assertEqual(result.summaries[0].relation, TemporalRelation.UNRESOLVED)
            self.assertIsNone(result.source_sequence)
            self.assertEqual(synthetic.__dict__, before)
            self.assertTrue(synthetic._state.adding)
            with self.assertRaises(FrozenInstanceError):
                result.source_sequence = 99
        self.assertEqual(BiologicalMeasurement.objects.count(), 0)

    def test_real_partial_service_history_and_resolvers_and_box_list_never_call_discovery(self):
        self.measurement(self.monday - timedelta(days=7))
        with patch("django.utils.timezone.now", return_value=datetime.combine(
                self.monday, time(12), tzinfo=datetime_timezone.utc)):
            self.box.refresh_from_db()
            event, children = create_subculture(
                parent_box=self.box, user=self.user, organization=self.org,
                expected_current_state_revision=resolve_current_polyp_state(self.box)["revision"],
                children=[{"thermal_zone": self.zone, "allocated_polyps": value} for value in (0, None)],
            )
        self.box.refresh_from_db()
        before = resolve_current_polyp_state(self.box)
        snapshots = list(SubcultureEvent.objects.values())
        result = self.discover()
        self.assertEqual(result.summaries[0].category, Category.PARTIAL_PARENT_OPERATION)
        self.assertIsNone(result.summaries[0].consumption)
        self.assertEqual(self.discover(box=children[0]).summaries[0].allocated_polyps, 0)
        self.assertEqual(self.discover(box=children[1]).summaries[0].category, Category.UNKNOWN_CHILD_ALLOCATION)
        self.assertEqual(list(SubcultureEvent.objects.values()), snapshots)
        with patch("apps.cultures.scientific_evidence.discover_observation_evidence",
                   side_effect=AssertionError("Production must not discover evidence")) as discovery:
            self.assertEqual(resolve_current_polyp_state(self.box), before)
            prefetched = Box.objects.prefetch_related(*current_state_prefetches()).get(pk=self.box.pk)
            with self.assertNumQueries(0):
                self.assertEqual(resolve_current_polyp_state(prefetched), before)
            request = Request(APIRequestFactory().get("/api/boxes/"))
            request.user = self.user
            context = {"request": request, "measurement_role_by_organization": {self.org.pk: "lab_technician"}}
            with CaptureQueriesContext(connection) as queries:
                boxes = list(box_list_queryset_for_user(self.user, organization_ids=[self.org.pk]))
                payload = BoxListSerializer(boxes, many=True, context=context).data
            self.assertLessEqual(len(queries), 6)
            with self.assertNumQueries(0):
                self.assertEqual(BoxListSerializer(boxes, many=True, context=context).data, payload)
            discovery.assert_not_called()
        self.assertIsNone(event.parent_polyp_count_after)

    def test_invalid_contexts_are_rejected_before_history_queries(self):
        for sequence in (-1, True, "1", 1.5):
            with self.subTest(sequence=sequence), self.assertNumQueries(0), self.assertRaises(ValueError):
                self.discover(sequence=sequence)
        with self.assertNumQueries(0), self.assertRaises(ValueError):
            self.discover(box=Box(organization=self.org, strain=self.strain))
        with self.assertNumQueries(0), self.assertRaises(ValueError):
            discover_observation_evidence(box=self.box, organization_id=self.org.pk, observation=None)
