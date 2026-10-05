"""Frozen pre-optimization oracle for the complete box-list payload.

Keep the legacy query helpers independent of the production prefetch helpers.
Measurement date ties cannot occur with valid per-box ISO-week uniqueness; the
ordering contract and same-clock scientific transitions are tested instead.
"""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, transaction
from django.db.models import F, OuterRef, Prefetch, Q, Subquery, Value
from django.db.models.functions import Coalesce
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from apps.accounts.models import OrganizationMembership
from apps.accounts.permissions import get_authorized_organization_ids
from apps.measurements.models import BiologicalMeasurement
from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain

from .api_views import box_list_queryset_for_user
from .models import Box, BoxLocation, SubcultureAllocation, SubcultureEvent, ThermalZone
from .polyp_state import MEASUREMENT_STATE_ORDER, _measurement_candidates, resolve_current_polyp_state
from .serializers import BoxListSerializer
from .services import create_subculture


LEGACY_MEASUREMENT_STATE_ORDER = ("-measured_on", "-state_order", "-created_at", "-pk")


def legacy_measurement_candidates():
    events = SubcultureEvent.objects.filter(
        parent_box_id=OuterRef("box_id"), occurred_at__isnull=False,
        allocated_polyp_count__isnull=False, parent_polyp_count_after__isnull=False,
    ).order_by("-parent_state_sequence", "-pk")
    initialization = SubcultureAllocation.objects.filter(
        child_box_id=OuterRef("box_id"), event__occurred_at__isnull=False,
        allocated_polyps__isnull=False,
        event__parent_box__organization_id=F("child_box__organization_id"),
    )
    return BiologicalMeasurement.objects.annotate(
        state_order=Coalesce("polyp_state_sequence", Value(0)),
        operation_sequence=Coalesce(
            Subquery(events.values("parent_state_sequence")[:1]),
            Subquery(initialization.values("child_state_sequence")[:1]), Value(0),
        ),
        operation_day=Coalesce(
            Subquery(events.values("event_date")[:1]),
            Subquery(initialization.values("event__event_date")[:1]),
        ),
    ).filter(
        Q(operation_sequence=0)
        | Q(polyp_state_sequence__gt=F("operation_sequence"), measured_on__gte=F("operation_day"))
    )


def legacy_current_state_prefetches():
    measurement = legacy_measurement_candidates()
    latest_id = measurement.filter(box_id=OuterRef("box_id")).order_by(
        *LEGACY_MEASUREMENT_STATE_ORDER,
    ).values("pk")[:1]
    events = SubcultureEvent.objects.filter(
        occurred_at__isnull=False, allocated_polyp_count__isnull=False,
        parent_polyp_count_after__isnull=False,
    )
    event_id = events.filter(parent_box_id=OuterRef("parent_box_id")).order_by(
        "-parent_state_sequence", "-pk",
    ).values("pk")[:1]
    initialization = SubcultureAllocation.objects.select_related("event", "event__parent_box").filter(
        event__occurred_at__isnull=False, allocated_polyps__isnull=False,
        event__parent_box__organization_id=F("child_box__organization_id"),
    )
    return [
        Prefetch("biological_measurements", queryset=measurement.filter(pk=Subquery(latest_id)), to_attr="polyp_state_measurements"),
        Prefetch("source_subculture_events", queryset=events.filter(pk=Subquery(event_id)), to_attr="polyp_state_events"),
        Prefetch("subculture_initialization", queryset=initialization, to_attr="polyp_state_initialization"),
    ]


def legacy_box_list_queryset_for_user(user, organization_ids=None):
    organization_ids = organization_ids or get_authorized_organization_ids(user)
    latest_measurement_id = Subquery(
        BiologicalMeasurement.objects.filter(box_id=OuterRef("box_id"))
        .order_by("-measured_on", "-created_at")
        .values("id")[:1]
    )
    latest_measurements = (
        BiologicalMeasurement.objects.filter(id__in=latest_measurement_id)
        .select_related("user")
        .order_by("-measured_on", "-created_at")
    )
    latest_salinity = Subquery(
        BiologicalMeasurement.objects.filter(box_id=OuterRef("pk"), salinity_psu__isnull=False)
        .order_by("-measured_on", "-created_at")
        .values("salinity_psu")[:1]
    )
    current_location_started_at = Subquery(
        BoxLocation.objects.filter(
            box_id=OuterRef("pk"),
            thermal_zone_id=OuterRef("thermal_zone_id"),
            thermal_zone__organization_id__in=organization_ids,
            ends_at__isnull=True,
            end_date_unknown=False,
        )
        .order_by("-starts_at", "-id")
        .values("starts_at")[:1]
    )
    return (
        Box.objects.select_related(
            "organization", "strain", "strain__species", "strain__origin", "origin", "thermal_zone",
        )
        .annotate(
            latest_salinity_annotation=latest_salinity,
            current_location_started_at_annotation=current_location_started_at,
        )
        .prefetch_related(
            *legacy_current_state_prefetches(),
            Prefetch("biological_measurements", queryset=latest_measurements),
        )
        .filter(organization_id__in=organization_ids)
    )


class BoxListQueryTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.clock = patch("django.utils.timezone.now", return_value=self.now)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.today = timezone.localdate(self.now)
        self.org = Organization.objects.create(name="Box-list laboratory")
        self.foreign_org = Organization.objects.create(name="Foreign box-list laboratory")
        self.user = get_user_model().objects.create_user(username="box-list", email="box-list@example.org")
        self.membership = OrganizationMembership.objects.create(
            user=self.user, organization=self.org, role=OrganizationMembership.Role.LAB_TECHNICIAN,
        )
        self.zone = ThermalZone.objects.create(organization=self.org, name="Measured zone", salinity_psu="33.50")
        species = Species.objects.create(scientific_name="Aurelia query", genus_species_code="AQU")
        self.strain = Strain.objects.create(species=species, organization=self.org, code="AQU-LIST-1")
        self.foreign_strain = Strain.objects.create(species=species, organization=self.foreign_org, code="AQU-OTHER-1")
        self.next_number = 0

    def box(self, *, located=True, foreign=False, **kwargs):
        self.next_number += 1
        strain = self.foreign_strain if foreign else self.strain
        while Box.objects.filter(global_code=f"{strain.code}.{self.next_number:03d}").exists():
            self.next_number += 1
        return Box.objects.create(
            organization=self.foreign_org if foreign else self.org,
            strain=strain, thermal_zone=self.zone if located and not foreign else None,
            global_code=f"{strain.code}.{self.next_number:03d}",
            box_number=f"{self.next_number:03d}", local_code=f"Local {self.next_number}",
            entered_on=self.today - timedelta(days=60), volume_liters=Decimal("0.30"), **kwargs,
        )

    def measurement(self, box, *, days=0, **kwargs):
        values = dict(polyp_count=100, ephyrae_count=8, strobila_count=3,
                      salinity_psu="34.25", user=self.user, notes="Scientific observation")
        values.update(kwargs)
        return BiologicalMeasurement.objects.create(box=box, measured_on=self.today + timedelta(days=days), **values)

    def operation(self, box, values=(30, 0), **kwargs):
        box.refresh_from_db()
        return create_subculture(
            parent_box=box, organization=self.org, user=self.user,
            expected_current_state_revision=resolve_current_polyp_state(box)["revision"],
            children=[{"thermal_zone": self.zone, "allocated_polyps": value} for value in values],
            **kwargs,
        )

    def context(self, *, cached_role=False, url="/api/boxes/"):
        request = Request(APIRequestFactory().get(url))
        request.user = self.user
        context = {"request": request}
        if cached_role:
            context["measurement_role_by_organization"] = {self.org.pk: self.membership.role}
        return context

    def queryset(self, helper=box_list_queryset_for_user):
        return helper(self.user, organization_ids=[self.org.pk]).order_by("global_code")

    def assert_equivalent(self):
        # Fresh querysets and contexts prevent either path from reusing the
        # other's prefetch or editability cache. Compare every nested field.
        old = BoxListSerializer(self.queryset(legacy_box_list_queryset_for_user), many=True, context=self.context()).data
        new = BoxListSerializer(self.queryset(), many=True, context=self.context()).data
        self.assertEqual(new, old)
        self.assertTrue(new)
        self.assertEqual(set(new[0]), set(BoxListSerializer.Meta.fields))
        return {row["id"]: row for row in new}

    def assert_candidate_and_state_equivalent(self, box, *, candidate_ids, polyp_count, source_kind, source):
        rows = self.assert_equivalent()
        old_ids = list(legacy_measurement_candidates().filter(box_id=box.pk)
                       .order_by(*LEGACY_MEASUREMENT_STATE_ORDER).values_list("pk", flat=True))
        new_ids = list(_measurement_candidates().filter(box_id=box.pk)
                       .order_by(*MEASUREMENT_STATE_ORDER).values_list("pk", flat=True))
        self.assertEqual(new_ids, old_ids)
        self.assertEqual(new_ids, candidate_ids)

        fresh = Box.objects.get(pk=box.pk)
        prefetched = self.queryset().get(pk=box.pk)
        timestamp = (source.created_at if source_kind == "measurement" else
                     source.event.occurred_at if source_kind == "subculture_initialization" else source.occurred_at)
        expected_source = {"kind": source_kind, "id": source.pk, "timestamp": timestamp.isoformat()}
        if source_kind == "measurement":
            expected_source["measured_on"] = source.measured_on.isoformat()
        expected = {
            "polyp_count": polyp_count,
            "source": expected_source,
            "revision": f"box:{fresh.pk}:{fresh.polyp_state_revision}",
        }
        self.assertEqual(rows[box.pk]["current_polyp_state"], expected)
        self.assertEqual(resolve_current_polyp_state(fresh), expected)
        with self.assertNumQueries(0):
            self.assertEqual(resolve_current_polyp_state(prefetched), expected)

    def test_ordinary_zero_null_absent_inactive_and_locations_for_each_role(self):
        zero = self.box()
        self.measurement(zero, days=-14, salinity_psu="0.00", strobila_count=0)
        latest = self.measurement(zero, polyp_count=0, ephyrae_count=0,
                                  strobila_count=None, salinity_psu=None, needs_attention=True,
                                  culture_status=BiologicalMeasurement.CultureStatus.MEDIUM)
        started = self.now - timedelta(days=7)
        BoxLocation.objects.create(box=zero, thermal_zone=self.zone, starts_at=started)
        measured_zero = self.box()
        self.measurement(measured_zero, strobila_count=0, salinity_psu="0.00", user=None)
        absent = self.box(located=False)
        historical = self.box(located=False, status=Box.Status.INACTIVE,
                              stop_reason="Historical stop", deactivated_on=self.today - timedelta(days=2))
        historical_measurement = self.measurement(historical, days=-28, strobila_count=None)
        BiologicalMeasurement.objects.filter(pk=historical_measurement.pk).update(created_at=self.now - timedelta(days=28))
        BoxLocation.objects.create(box=historical, thermal_zone=self.zone,
                                   starts_at=self.now - timedelta(days=40), ends_at=self.now - timedelta(days=2))
        foreign = self.box(foreign=True)
        self.measurement(foreign)
        for role in OrganizationMembership.Role.values:
            with self.subTest(role=role):
                self.membership.role = role
                self.membership.save(update_fields=["role"])
                rows = self.assert_equivalent()
                self.assertNotIn(foreign.pk, rows)
                row = rows[zero.pk]
                self.assertEqual(row["latest_measurement"]["id"], latest.pk)
                self.assertEqual(row["latest_measurement"]["polyp_count"], 0)
                self.assertIsNone(row["latest_measurement"]["strobila_count"])
                self.assertIsNone(row["latest_measurement"]["salinity_psu"])
                self.assertEqual(Decimal(str(row["latest_salinity_psu"])), Decimal("0"))
                self.assertEqual(row["current_polyp_state"]["polyp_count"], 0)
                self.assertEqual(row["current_location_started_at"], started)
                self.assertEqual(rows[measured_zero.pk]["latest_measurement"]["strobila_count"], 0)
                self.assertIsNone(rows[measured_zero.pk]["latest_measurement"]["user"])
                self.assertIsNone(rows[absent.pk]["latest_measurement"])
                self.assertIsNone(rows[absent.pk]["current_polyp_state"]["source"])
                self.assertIsNone(rows[absent.pk]["current_polyp_state"]["polyp_count"])
                self.assertIsNone(rows[absent.pk]["current_location_started_at"])
                self.assertIsNone(rows[historical.pk]["thermal_zone"])
                self.assertIsNone(rows[historical.pk]["current_location_started_at"])
                self.assertEqual(rows[historical.pk]["latest_measurement"]["id"], historical_measurement.pk)
                self.assertEqual(row["latest_measurement"]["can_edit"], role in {"admin", "lab_technician"})
                self.assertEqual(rows[historical.pk]["latest_measurement"]["can_edit"], role == "admin")

    def test_complete_partial_and_unknown_child_initializations(self):
        complete = self.box()
        self.measurement(complete)
        event, complete_children = self.operation(complete, (100, 0))
        partial = self.box()
        source = self.measurement(partial)
        partial_event, partial_children = self.operation(partial, (20, 0, None))
        rows = self.assert_equivalent()
        self.assertEqual(rows[complete.pk]["current_polyp_state"]["polyp_count"], 0)
        self.assertEqual(rows[complete.pk]["current_polyp_state"]["source"]["id"], event.pk)
        self.assertEqual(rows[partial.pk]["current_polyp_state"]["source"]["id"], source.pk)
        self.assertIsNone(partial_event.parent_polyp_count_after)
        for child, value in zip(complete_children + partial_children, (100, 0, 20, 0, None), strict=True):
            with self.subTest(child=child.pk):
                row = rows[child.pk]
                self.assertIsNone(row["latest_measurement"])
                self.assertEqual(row["current_polyp_state"]["polyp_count"], value)
                if value is None:
                    self.assertIsNone(row["current_polyp_state"]["source"])
                else:
                    self.assertEqual(row["current_polyp_state"]["source"]["kind"], "subculture_initialization")
                self.assertIsNotNone(row["current_location_started_at"])

    def test_same_clock_complete_then_partial_preserves_latest_complete_event(self):
        parent = self.box()
        self.measurement(parent)
        first, children = self.operation(parent, (10,))
        second, _ = self.operation(parent, (20,))
        partial, _ = self.operation(parent, (None,))
        self.assertEqual(first.occurred_at, second.occurred_at)
        self.assertEqual(second.occurred_at, partial.occurred_at)
        self.assertGreater(second.parent_state_sequence, first.parent_state_sequence)
        child_measurement = self.measurement(children[0], polyp_count=7)
        rows = self.assert_equivalent()
        self.assertEqual(rows[parent.pk]["current_polyp_state"]["polyp_count"], 70)
        self.assertEqual(rows[parent.pk]["current_polyp_state"]["source"]["id"], second.pk)
        self.assertEqual(rows[children[0].pk]["current_polyp_state"]["source"]["id"], child_measurement.pk)
        # Legacy events have no absolute state and must not displace a source.
        SubcultureEvent.objects.create(parent_box=parent, event_date=self.today + timedelta(days=7))
        self.assert_equivalent()

    def test_consumed_future_source_and_new_lower_date_eligible_measurement(self):
        parent = self.box()
        future = self.measurement(parent, days=28)
        event, _ = self.operation(parent, (30,))
        rows = self.assert_equivalent()
        self.assertEqual(rows[parent.pk]["latest_measurement"]["id"], future.pk)
        self.assertEqual(rows[parent.pk]["current_polyp_state"]["source"]["id"], event.pk)
        fresh = self.measurement(parent, polyp_count=12)
        rows = self.assert_equivalent()
        self.assertEqual(rows[parent.pk]["latest_measurement"]["id"], future.pk)
        self.assertEqual(rows[parent.pk]["current_polyp_state"]["source"]["id"], fresh.pk)
        self.assertEqual(rows[parent.pk]["current_polyp_state"]["polyp_count"], 12)

    def test_corrections_retain_sequence_and_biological_date_priority(self):
        parent = self.box()
        consumed = self.measurement(parent, days=28)
        event, _ = self.operation(parent, (30,))
        consumed_sequence = consumed.polyp_state_sequence
        consumed.measured_on = self.today + timedelta(days=42)
        consumed.polyp_count = 1
        consumed.save()
        self.assertEqual(consumed.polyp_state_sequence, consumed_sequence)
        self.assertEqual(self.assert_equivalent()[parent.pk]["current_polyp_state"]["source"]["id"], event.pk)
        eligible = self.measurement(parent, days=7, polyp_count=12)
        backdated = self.measurement(parent, days=-14, polyp_count=999)
        backdated.polyp_count = 2
        backdated.save()
        self.assertGreater(backdated.polyp_state_sequence, eligible.polyp_state_sequence)
        rows = self.assert_equivalent()
        self.assertEqual(rows[parent.pk]["current_polyp_state"]["source"]["id"], eligible.pk)
        later = self.measurement(parent, days=14, polyp_count=20)
        sequence = eligible.polyp_state_sequence
        eligible.polyp_count = 15
        eligible.save()
        self.assertEqual(eligible.polyp_state_sequence, sequence)
        self.assertEqual(self.assert_equivalent()[parent.pk]["current_polyp_state"]["source"]["id"], later.pk)

    def test_foreign_initialization_anomaly_is_ignored(self):
        foreign_parent = self.box(foreign=True)
        foreign_source = self.measurement(foreign_parent)
        # This event has a valid absolute balance. Only the cross-organization
        # allocation is anomalous; normal services never create that relation.
        event = SubcultureEvent.objects.create(
            parent_box=foreign_parent, occurred_at=self.now, event_date=self.today,
            parent_state_sequence=2, parent_polyp_count_before=100,
            allocated_polyp_count=10, parent_polyp_count_after=90,
            parent_state_snapshot={"polyp_count": 100, "source": {"kind": "measurement", "id": foreign_source.pk}},
        )
        unknown = self.box(located=False)
        measured = self.box()
        source = self.measurement(measured, days=-14, polyp_count=8)
        for position, child in enumerate((unknown, measured), 1):
            SubcultureAllocation.objects.create(event=event, child_box=child, position=position,
                allocated_polyps=5, child_state_sequence=2, child_global_code=child.global_code)
        rows = self.assert_equivalent()
        self.assertNotIn(foreign_parent.pk, rows)
        self.assertIsNone(rows[unknown.pk]["current_polyp_state"]["source"])
        self.assertIsNone(rows[unknown.pk]["current_polyp_state"]["polyp_count"])
        self.assertEqual(rows[measured.pk]["current_polyp_state"]["source"]["id"], source.pk)

    def test_initialized_child_becomes_parent_with_complete_and_partial_operations(self):
        parent = self.box()
        with patch("django.utils.timezone.now", return_value=self.now - timedelta(days=14)):
            self.measurement(parent, days=-14)
            _, children = self.operation(parent, (40,))
        child = children[0]
        initialization = child.subculture_initialization
        self.assert_candidate_and_state_equivalent(
            child, candidate_ids=[], polyp_count=40,
            source_kind="subculture_initialization", source=initialization,
        )
        consumed = self.measurement(child, days=28, polyp_count=40)
        self.assert_candidate_and_state_equivalent(
            child, candidate_ids=[consumed.pk], polyp_count=40,
            source_kind="measurement", source=consumed,
        )
        complete, _ = self.operation(child, (10,))
        self.assertGreater(complete.event_date, initialization.event.event_date)
        self.assert_candidate_and_state_equivalent(
            child, candidate_ids=[], polyp_count=30, source_kind="subculture", source=complete,
        )
        # This observation passes the initialization day but not the child's own operation day.
        backdated = self.measurement(child, days=-7, polyp_count=999)
        self.assertGreater(backdated.polyp_state_sequence, complete.parent_state_sequence)
        self.assert_candidate_and_state_equivalent(
            child, candidate_ids=[], polyp_count=30, source_kind="subculture", source=complete,
        )
        partial, _ = self.operation(child, (5, None))
        self.assertIsNone(partial.parent_polyp_count_after)
        self.assert_candidate_and_state_equivalent(
            child, candidate_ids=[], polyp_count=30, source_kind="subculture", source=complete,
        )
        eligible = self.measurement(child, polyp_count=12)
        self.assert_candidate_and_state_equivalent(
            child, candidate_ids=[eligible.pk], polyp_count=12,
            source_kind="measurement", source=eligible,
        )
        self.operation(child, (0, None))
        self.assert_candidate_and_state_equivalent(
            child, candidate_ids=[eligible.pk], polyp_count=12,
            source_kind="measurement", source=eligible,
        )

    def test_legacy_null_and_zero_measurement_sequences_before_and_after_operation(self):
        for sequence in (None, 0):
            with self.subTest(sequence=sequence):
                parent = self.box()
                older = self.measurement(parent, days=-14)
                legacy = self.measurement(parent, days=14, polyp_count=0)
                # save() assigns a new sequence; update only the synthetic legacy fixture.
                BiologicalMeasurement.objects.filter(pk=legacy.pk).update(polyp_state_sequence=sequence)
                self.assert_candidate_and_state_equivalent(
                    parent, candidate_ids=[legacy.pk, older.pk], polyp_count=0,
                    source_kind="measurement", source=legacy,
                )
                complete, _ = self.operation(parent, (0,))
                self.assertGreater(complete.parent_state_sequence, 0)
                self.assert_candidate_and_state_equivalent(
                    parent, candidate_ids=[], polyp_count=0, source_kind="subculture", source=complete,
                )
                new_legacy = self.measurement(parent, days=7, polyp_count=999)
                BiologicalMeasurement.objects.filter(pk=new_legacy.pk).update(polyp_state_sequence=sequence)
                self.assert_candidate_and_state_equivalent(
                    parent, candidate_ids=[], polyp_count=0, source_kind="subculture", source=complete,
                )
                eligible = self.measurement(parent, polyp_count=7)
                self.assert_candidate_and_state_equivalent(
                    parent, candidate_ids=[eligible.pk], polyp_count=7,
                    source_kind="measurement", source=eligible,
                )

    def test_complete_event_sequence_ties_use_pk_for_source_and_eligibility_day(self):
        parent = self.box()
        self.measurement(parent, days=-14)
        first = SubcultureEvent.objects.create(
            parent_box=parent, occurred_at=self.now + timedelta(days=14),
            event_date=self.today + timedelta(days=14), parent_state_sequence=10,
            parent_polyp_count_before=100, allocated_polyp_count=30, parent_polyp_count_after=70,
            parent_state_snapshot={"polyp_count": 100},
        )
        second = SubcultureEvent.objects.create(
            parent_box=parent, occurred_at=self.now, event_date=self.today, parent_state_sequence=10,
            parent_polyp_count_before=70, allocated_polyp_count=20, parent_polyp_count_after=50,
            parent_state_snapshot={"polyp_count": 70},
        )
        Box.objects.filter(pk=parent.pk).update(polyp_state_revision=10)
        self.assertEqual(first.parent_state_sequence, second.parent_state_sequence)
        self.assertGreater(second.pk, first.pk)
        self.assertLess(second.event_date, first.event_date)
        self.assert_candidate_and_state_equivalent(
            parent, candidate_ids=[], polyp_count=50, source_kind="subculture", source=second,
        )
        backdated = self.measurement(parent, days=-7, polyp_count=999)
        self.assertGreater(backdated.polyp_state_sequence, second.parent_state_sequence)
        self.assert_candidate_and_state_equivalent(
            parent, candidate_ids=[], polyp_count=50, source_kind="subculture", source=second,
        )
        # Eligible against the higher-PK event, but not against the lower-PK event's later day.
        eligible = self.measurement(parent, days=7, polyp_count=12)
        self.assert_candidate_and_state_equivalent(
            parent, candidate_ids=[eligible.pk], polyp_count=12,
            source_kind="measurement", source=eligible,
        )

    def test_ordering_contract_without_impossible_same_week_measurement_ties(self):
        self.assertEqual(MEASUREMENT_STATE_ORDER, LEGACY_MEASUREMENT_STATE_ORDER)
        parent = self.box()
        self.measurement(parent)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.measurement(parent)
        self.assert_equivalent()

    def serialized_query_count(self):
        with CaptureQueriesContext(connection) as queries:
            boxes = list(self.queryset())
            payload = BoxListSerializer(boxes, many=True, context=self.context()).data
        for box in boxes:
            self.assertLessEqual(len(box.polyp_state_measurements), 1)
            self.assertLessEqual(len(box.polyp_state_events), 1)
            self.assertLessEqual(len(box.list_latest_measurements), 1)
        # Once prefetching and the one role lookup are complete, serialization
        # must not fetch authors, organizations, histories or state per box.
        with self.assertNumQueries(0):
            cached = BoxListSerializer(boxes, many=True, context=self.context(cached_role=True)).data
        self.assertEqual(cached, payload)
        return len(queries), payload

    def test_measured_history_query_count_and_prefetch_size_are_bounded(self):
        first = self.box()
        self.measurement(first)
        small_count, _ = self.serialized_query_count()
        for index in range(12):
            box = first if index == 0 else self.box()
            for week in range(1, 9):
                self.measurement(box, days=-7 * week, polyp_count=week)
            if index:
                self.measurement(box, polyp_count=index)
        large_count, payload = self.serialized_query_count()
        self.assertEqual(len(payload), 12)
        self.assertEqual(large_count, small_count)
        self.assertLessEqual(large_count, 6)
        self.assert_equivalent()

    def test_paginated_payload_matches_oracle_with_page_local_prefetches(self):
        for index in range(7):
            parent = self.box()
            self.measurement(parent, days=-14, salinity_psu="0.00")
            self.measurement(parent, polyp_count=index + 10, salinity_psu=None)
            if index % 2 == 0:
                self.operation(parent, (0,))
        expected_ids = list(self.queryset().values_list("pk", flat=True))
        seen = []
        for offset in range(0, len(expected_ids), 3):
            with self.subTest(offset=offset):
                envelopes = []
                for helper in (legacy_box_list_queryset_for_user, box_list_queryset_for_user):
                    context = self.context(url=f"/api/boxes/?limit=3&offset={offset}")
                    paginator = LimitOffsetPagination()
                    with CaptureQueriesContext(connection) as queries:
                        page = paginator.paginate_queryset(self.queryset(helper), context["request"])
                        payload = BoxListSerializer(page, many=True, context=context).data
                        envelope = paginator.get_paginated_response(payload).data
                    self.assertLessEqual(len(queries), 7)
                    self.assertEqual(len(page), min(3, len(expected_ids) - offset))
                    for box in page:
                        self.assertLessEqual(len(box.polyp_state_measurements), 1)
                        self.assertLessEqual(len(box.polyp_state_events), 1)
                        latest = (box._prefetched_objects_cache["biological_measurements"]
                                  if helper is legacy_box_list_queryset_for_user
                                  else box.list_latest_measurements)
                        self.assertLessEqual(len(latest), 1)
                    envelopes.append(envelope)
                self.assertEqual(envelopes[1], envelopes[0])
                self.assertEqual(envelopes[1]["count"], len(expected_ids))
                seen.extend(row["id"] for row in envelopes[1]["results"])
        self.assertEqual(seen, expected_ids)
