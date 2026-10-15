"""One synthetic proposal plus persisted sources, never synthetic batches.

ISO-week proposals stay unsaved: this API neither resolves batches of proposed
observations nor authorizes imports, persistence, or concurrent writes.
"""

from datetime import date, datetime, time, timedelta, timezone
from itertools import product
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from apps.measurements.models import BiologicalMeasurement
from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain

from .models import Box, SubcultureAllocation, SubcultureEvent
from .polyp_state import (
    current_state_prefetches,
    resolve_current_polyp_state,
    resolve_scientific_polyp_state,
)
from .scientific_resolution import resolve_week_observation_state


class ScientificResolutionTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Resolution laboratory")
        self.foreign_org = Organization.objects.create(name="Other resolution laboratory")
        self.user = get_user_model().objects.create_user(username="resolution", email="resolution@example.org")
        species = Species.objects.create(scientific_name="Aurelia resolution", genus_species_code="ARS")
        self.strain = Strain.objects.create(species=species, organization=self.org, code="ARS-R-1")
        self.number = 0
        self.box = self.new_box()
        self.monday = date.fromisocalendar(2026, 40, 1)
        self.sunday = self.monday + timedelta(days=6)

    def new_box(self, organization=None):
        self.number += 1
        return Box.objects.create(
            organization=organization or self.org, strain=self.strain,
            global_code=f"ARS-R-1.{self.number:04d}", box_number=f"{self.number:04d}",
        )

    def operation(self, day, *, partial=False, legacy=False, box=None):
        box = box or self.box
        values = {"parent_box": box, "event_date": day}
        if not legacy:
            box.refresh_from_db()
            box.polyp_state_revision += 1
            box.save(update_fields=["polyp_state_revision"])
            values.update(
                occurred_at=datetime.combine(day, time(12), tzinfo=timezone.utc),
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

    def measurement(self, day, *, box=None, count=100, precision="exact_date"):
        return BiologicalMeasurement.objects.create(
            box=box or self.box, measured_on=day, polyp_count=count, ephyrae_count=0,
            user=self.user, temporal_precision=precision,
        )

    def proposal(self, *, box=None, count=23, sequence=None):
        return BiologicalMeasurement(
            box=box or self.box, temporal_precision="iso_week", measured_on=None,
            week_start=self.monday, polyp_count=count, ephyrae_count=0,
            polyp_state_sequence=sequence,
        )

    def resolve(self, *, box=None, count=23, sequence=None):
        box = box or self.box
        return resolve_week_observation_state(
            box=box, organization_id=self.org.pk,
            measurement=self.proposal(box=box, count=count, sequence=sequence),
        )

    def assert_week(self, state, count=23):
        self.assertEqual(state.status, "known")
        self.assertEqual(state.polyp_count, count)
        self.assertEqual(state.source, {
            "kind": "week_observation", "week_start": self.monday.isoformat(),
            "temporal_precision": "iso_week",
        })

    def assert_source(self, state, kind, source_id, count):
        self.assertEqual(state.status, "known")
        self.assertEqual(state.polyp_count, count)
        self.assertEqual(state.source["kind"], kind)
        self.assertEqual(state.source["id"], source_id)

    def assert_unresolved(self, state, reason="chronology"):
        self.assertEqual(state.status, "unresolved")
        self.assertEqual(state.reason, reason)
        self.assertEqual(state.observation.measured_on, None)
        self.assertEqual(state.observation.week_start, self.monday)
        self.assertFalse(hasattr(state, "polyp_count"))
        self.assertFalse(hasattr(state, "source"))

    def test_empty_history_zero_and_read_only_single_proposal(self):
        proposal = self.proposal(count=0)
        before = dict(proposal.__dict__)
        revision = self.box.polyp_state_revision
        with patch.object(BiologicalMeasurement, "save", side_effect=AssertionError("must not save")):
            state = resolve_week_observation_state(
                box=self.box, organization_id=self.org.pk, measurement=proposal,
            )
        self.assert_week(state, 0)
        self.assertEqual(proposal.__dict__, before)
        self.assertTrue(proposal._state.adding)
        self.assertIsNone(proposal.pk)
        self.assertIsNone(proposal.measured_on)
        self.assertFalse(self.box.biological_measurements.exists())
        self.box.refresh_from_db()
        self.assertEqual(self.box.polyp_state_revision, revision)
        self.assertEqual(resolve_current_polyp_state(self.box)["polyp_count"], None)

    def test_complete_before_after_and_every_overlap_day(self):
        for offset in range(-1, 8):
            box = self.new_box()
            event = self.operation(self.monday + timedelta(days=offset), box=box)
            for sequence in (None, 0, event.parent_state_sequence, event.parent_state_sequence + 1):
                with self.subTest(offset=offset, sequence=sequence):
                    state = self.resolve(box=box, sequence=sequence)
                    if (sequence is not None and sequence <= event.parent_state_sequence) or offset == 7:
                        self.assert_source(state, "subculture", event.pk, 90)
                    elif 0 <= offset <= 6:
                        self.assert_unresolved(state)
                        self.assertEqual(state.operation_day, event.event_date)
                    elif sequence is None:
                        self.assert_unresolved(state, "unknown_source_sequence")
                    else:
                        self.assert_week(state)

    def test_multiple_complete_operations_consumed_and_later_barrier_clears_ambiguity(self):
        first = self.operation(self.monday)
        second = self.operation(self.sunday + timedelta(days=1))
        for sequence in (None, 0, first.parent_state_sequence, second.parent_state_sequence, 999):
            with self.subTest(sequence=sequence):
                self.assert_source(self.resolve(sequence=sequence), "subculture", second.pk, 90)

    def test_later_complete_consumes_old_partial_and_legacy_ambiguity(self):
        self.operation(self.monday, partial=True)
        self.operation(self.monday - timedelta(days=100), legacy=True)
        self.assert_unresolved(self.resolve(sequence=0))
        later = self.operation(self.sunday + timedelta(days=1))
        self.assert_source(self.resolve(sequence=0), "subculture", later.pk, 90)
        self.assert_source(self.resolve(sequence=999), "subculture", later.pk, 90)
        self.assert_source(self.resolve(sequence=None), "subculture", later.pk, 90)

    def test_older_ambiguous_proposal_cannot_block_later_eligible_measurement(self):
        self.operation(self.monday)
        later = self.measurement(self.sunday + timedelta(days=1), count=17)
        for sequence in (None, 0, 999):
            self.assert_source(self.resolve(sequence=sequence), "measurement", later.pk, 17)

    def test_older_persisted_count_cannot_win_relevant_ambiguity(self):
        self.measurement(self.monday - timedelta(days=7), count=800)
        self.operation(self.monday, partial=True)
        self.assert_unresolved(self.resolve(sequence=999))
        self.operation(self.sunday)
        self.assert_unresolved(self.resolve(sequence=999))
        self.assert_unresolved(self.resolve(sequence=None))

    def test_partial_only_uncertain_on_overlap_and_legacy_always_unknown(self):
        for offset in range(-1, 8):
            box = self.new_box()
            self.measurement(self.monday - timedelta(days=7), box=box)
            self.operation(self.monday + timedelta(days=offset), partial=True, box=box)
            for sequence in (None, 0, 999):
                with self.subTest(offset=offset, sequence=sequence):
                    state = self.resolve(box=box, sequence=sequence)
                    if 0 <= offset <= 6:
                        self.assert_unresolved(state)
                    else:
                        self.assert_week(state)
        for offset in (-100, 100):
            box = self.new_box()
            self.operation(self.monday + timedelta(days=offset), legacy=True, box=box)
            self.assert_unresolved(self.resolve(box=box, sequence=999))
            self.assertIsNone(self.resolve(box=box).operation_day)

    def test_child_zero_and_null_allocation_including_partial_parent(self):
        for partial, count, offset in product((False, True), (0, None), (-1, 0, 6, 7)):
            parent = self.new_box()
            event = self.operation(self.monday + timedelta(days=offset), partial=partial, box=parent)
            child, allocation = self.allocation(event, count)
            for sequence in (None, 0, allocation.child_state_sequence + 1):
                with self.subTest(partial=partial, count=count, offset=offset, sequence=sequence):
                    state = self.resolve(box=child, sequence=sequence)
                    if count is not None and (sequence == 0 or offset == 7):
                        self.assert_source(state, "subculture_initialization", allocation.pk, 0)
                    elif offset in (0, 6):
                        self.assert_unresolved(state)
                    elif count is not None and sequence is None:
                        self.assert_unresolved(state, "unknown_source_sequence")
                    else:
                        self.assert_week(state)

    def test_legacy_child_occurrence_does_not_use_date_or_known_zero(self):
        for count, offset in product((None, 0), (-100, 100)):
            event = self.operation(self.monday + timedelta(days=offset), legacy=True)
            child, _ = self.allocation(event, count)
            self.assert_unresolved(self.resolve(box=child, sequence=999))

    def test_duplicate_week_including_zero_zero_is_rejected(self):
        for offset, count in product((0, 6), (0, 23)):
            box = self.new_box()
            self.measurement(self.monday + timedelta(days=offset), box=box, count=count)
            for proposed_count in (0, 23):
                with self.subTest(offset=offset, count=count, proposed_count=proposed_count):
                    with self.assertRaisesMessage(ValueError, "already has an observation"):
                        self.resolve(box=box, count=proposed_count)

    def test_input_contract_rejects_nonproposal_and_unknown_or_invalid_count(self):
        persisted = self.measurement(self.monday - timedelta(days=7))
        cases = [persisted, object(), [self.proposal()]]
        for values in (
            {"pk": 123}, {"box_id": self.new_box().pk}, {"polyp_count": None},
            {"polyp_count": -1}, {"polyp_count": True}, {"polyp_count": 1.5},
            {"polyp_count": "0"}, {"measured_on": self.monday},
            {"temporal_precision": "exact_date", "measured_on": self.monday},
            {"temporal_precision": "legacy_unverified", "measured_on": self.monday},
            {"week_start": self.monday + timedelta(days=1)},
            {"polyp_state_sequence": -1}, {"polyp_state_sequence": True},
        ):
            proposal = self.proposal()
            for key, value in values.items():
                setattr(proposal, key, value)
            cases.append(proposal)
        nonadding = self.proposal()
        nonadding._state.adding = False
        cases.append(nonadding)
        for proposal in cases:
            with self.subTest(proposal=repr(proposal)):
                with self.assertRaises(ValueError):
                    resolve_week_observation_state(
                        box=self.box, organization_id=self.org.pk, measurement=proposal,
                    )

    def test_scope_rechecks_database_and_ignores_foreign_child_parent(self):
        foreign = self.new_box(self.foreign_org)
        self.operation(self.monday, box=foreign)
        with self.assertRaises(PermissionDenied):
            self.resolve(box=foreign)
        foreign.organization_id = self.org.pk
        with self.assertRaises(PermissionDenied):
            self.resolve(box=foreign)
        with self.assertRaises(PermissionDenied):
            resolve_week_observation_state(
                box=self.box, organization_id=self.foreign_org.pk, measurement=self.proposal(),
            )
        foreign.refresh_from_db()
        event = self.operation(self.monday, box=foreign)
        self.allocation(event, 0, box=self.box)
        # Neither discovery nor the dated baseline may select this foreign parent.
        self.assert_week(self.resolve(sequence=0))
        self.assertEqual(resolve_scientific_polyp_state(self.box).status, "no_eligible_source")
        prefetched = Box.objects.prefetch_related(*current_state_prefetches()).get(pk=self.box.pk)
        self.assert_week(self.resolve(box=prefetched, sequence=0))

    def test_exact_and_legacy_public_resolver_compatibility_and_no_opt_in_calls(self):
        for precision in ("exact_date", "legacy_unverified"):
            box = self.new_box()
            old = self.measurement(self.monday - timedelta(days=7), box=box, precision=precision)
            event = self.operation(self.monday, box=box)
            with patch("apps.cultures.scientific_resolution.resolve_week_observation_state",
                       side_effect=AssertionError("public resolver must stay dated")), patch(
                "apps.cultures.scientific_evidence.discover_observation_evidence",
                side_effect=AssertionError("public resolver must not discover proposals"),
            ):
                self.assert_source(resolve_scientific_polyp_state(box), "subculture", event.pk, 90)
                later = self.measurement(self.monday, box=box, count=0, precision=precision)
                state = resolve_scientific_polyp_state(box)
                self.assert_source(state, "measurement", later.pk, 0)
                public = resolve_current_polyp_state(box)
                self.assertEqual(public, {
                    "polyp_count": 0, "source": state.source,
                    "revision": f"box:{box.pk}:{box.polyp_state_revision}",
                })
                self.assertNotEqual(state.source["id"], old.pk)
                self.assertEqual(state.source["measured_on"], self.monday.isoformat())

    def test_direct_and_prefetched_equivalence_for_all_source_shapes(self):
        boxes = [self.box]
        for partial in (False, True):
            parent = self.new_box()
            event = self.operation(self.monday, partial=partial, box=parent)
            boxes.append(parent)
            for count in (0, None):
                child, _ = self.allocation(event, count)
                boxes.append(child)
        dated = self.new_box()
        self.measurement(self.monday - timedelta(days=7), box=dated)
        boxes.append(dated)
        later = self.new_box()
        self.operation(self.monday, box=later)
        self.measurement(self.monday + timedelta(days=7), box=later)
        boxes.append(later)
        for box, sequence in product(boxes, (None, 0, 999)):
            with self.subTest(box=box.pk, sequence=sequence):
                prefetched = Box.objects.prefetch_related(*current_state_prefetches()).get(pk=box.pk)
                self.assertEqual(self.resolve(box=box, sequence=sequence),
                                 self.resolve(box=prefetched, sequence=sequence))

    def test_queries_bounded_at_seven_and_constant_with_fifty_operations(self):
        for partial in (False, True):
            box = self.new_box()
            counts = []
            for size in (1, 50):
                for index in range(1 if size == 1 else 49):
                    self.operation(self.monday + timedelta(days=index % 7), box=box, partial=partial)
                self.assertEqual(box.source_subculture_events.count(), size)
                with CaptureQueriesContext(connection) as queries:
                    state = self.resolve(box=box, sequence=999)
                self.assert_unresolved(state)
                counts.append(len(queries))
                self.assertLessEqual(len(queries), 7)
            self.assertEqual(counts, [7, 7] if partial else [6, 6])
        # Empty baseline traverses all three persisted source kinds.
        with CaptureQueriesContext(connection) as queries:
            self.assert_week(self.resolve())
        self.assertEqual(len(queries), 7)

    def oracle(self, events, measurement, sequence):
        """Exhaustive tiny-history reference, with no production temporal helpers.

        Enumerating the seven possible dates distinguishes certain direction
        from overlap; equality is deliberately ambiguous for a week proposal.
        Source selection uses raw fixture facts, not the production baseline.
        """
        dates = [self.monday + timedelta(days=index) for index in range(7)]

        def relation(day):
            if day is None:
                return "unresolved"
            directions = {"before" if observed < day else "after" if observed > day else "same"
                          for observed in dates}
            return next(iter(directions)) if len(directions) == 1 else "unresolved"

        complete = [event for event in events if event.parent_polyp_count_after is not None]
        latest = max(complete, key=lambda event: event.parent_state_sequence, default=None)
        eligible = measurement is not None and (
            latest is None or (measurement.polyp_state_sequence > latest.parent_state_sequence
                               and measurement.measured_on >= latest.event_date)
        )
        baseline = (("known", "measurement", measurement.pk, measurement.polyp_count) if eligible else
                    ("known", "subculture", latest.pk, latest.parent_polyp_count_after) if latest else
                    ("no_eligible_source",))
        if sequence is not None and any(sequence <= event.parent_state_sequence for event in complete):
            return baseline
        if eligible and all(measurement.measured_on > observed for observed in dates):
            return baseline
        if latest:
            direction = relation(latest.event_date)
            if direction == "before":
                return baseline
            if direction == "unresolved":
                return ("unresolved", "chronology")
            if sequence is None:
                return ("unresolved", "unknown_source_sequence")
        if any(relation(event.event_date) == "unresolved" for event in events if event not in complete):
            return ("unresolved", "chronology")
        return ("known", "week_observation", None, 23)

    def test_exhaustive_small_history_oracle(self):
        days = (self.monday - timedelta(days=1), self.monday, self.sunday,
                self.sunday + timedelta(days=1))
        choices = tuple(product(days, (False, True)))
        history_count = case_count = 0
        # Ordered products cover date order independent of commit sequence,
        # repeated operation days, complete/partial mixtures, and 0..2 barriers.
        for length in range(3):
            for history in product(choices, repeat=length):
                history_count += 1
                for persisted_day in (None, self.monday - timedelta(days=7),
                                      self.sunday + timedelta(days=1)):
                    box = self.new_box()
                    old = (self.measurement(persisted_day, box=box, count=31)
                           if persisted_day is not None and persisted_day < self.monday else None)
                    events = [self.operation(day, partial=partial, box=box) for day, partial in history]
                    measurement = (self.measurement(persisted_day, box=box, count=0)
                                   if persisted_day is not None and persisted_day > self.sunday else old)
                    for sequence in (None, 0, box.polyp_state_revision + 1):
                        case_count += 1
                        with self.subTest(history=history, persisted_day=persisted_day, sequence=sequence):
                            expected = self.oracle(events, measurement, sequence)
                            actual = self.resolve(box=box, sequence=sequence)
                            self.assertEqual(actual.status, expected[0])
                            if actual.status == "known":
                                self.assertEqual((actual.source["kind"], actual.source.get("id"), actual.polyp_count),
                                                 expected[1:])
                                if actual.source["kind"] == "week_observation":
                                    self.assert_week(actual)
                            elif actual.status == "unresolved":
                                self.assert_unresolved(actual, expected[1])
        self.assertEqual(history_count, 73)
        self.assertEqual(case_count, 657)
