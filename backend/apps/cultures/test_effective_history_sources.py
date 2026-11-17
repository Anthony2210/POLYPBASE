"""Source adaptation of one Box history, on the isolated test database only."""

from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.measurements.historical_temperature_source import SOURCES
from apps.measurements.models import BiologicalMeasurement, HistoricalTemperatureEvidence, HistoricalTemperatureReading
from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain

from .effective_history import (
    AdjustEventZones, ConflictCertainty, ConflictCode, EventKind, EventOrigin, EventTarget, IdentityStability,
    InsertEvent, OccupancyState, RejectionCode, SideType, TemporalPosition, TemporalPrecision, project_history,
    to_payload,
)
from .effective_history_sources import STARTING_LOCATION_KEY, HistorySourceNotFound, load_box_source_history
from .historical_movements import PROVENANCE as RECONSTRUCTION_PROVENANCE, accept_historical_movements, plan_historical_movements
from .models import MOVEMENT_WEEK_TIME_ZONE, Box, BoxLocation, BoxMovement, HistoricalBoxMovement, ThermalZone
from .services import deactivate_box, move_box_to_thermal_zone, qualify_pending_box, reactivate_box

SOURCE_FILE = "Suivi_2026_actualisé_v2.xlsx"
SOURCE_SHA = SOURCES[SOURCE_FILE][1]


def paris(year, month, day, hour=0):
    return datetime(year, month, day, hour, tzinfo=MOVEMENT_WEEK_TIME_ZONE)


def by_key(source):
    return {event.identity.key: event for event in source.events}


def refs(event):
    return sorted((ref.source, ref.role, ref.basis) for ref in event.sources)


def states(projection):
    return [(interval.state, interval.zone_id) for interval in projection.intervals]


class RecordedHistorySourceTests(TestCase):
    def setUp(self):
        # Primary key 1 belongs to the institution-specific rule; stay outside it.
        Organization.objects.update_or_create(pk=1, defaults={"name": "Rule institution"})
        self.org = Organization.objects.create(name="History laboratory")
        self.other = Organization.objects.create(name="Other laboratory")
        self.admin = get_user_model().objects.create_user(username="history_admin", email="history-admin@example.org")
        OrganizationMembership.objects.create(user=self.admin, organization=self.org, role="admin")
        species = Species.objects.create(scientific_name="History synthetic", genus_species_code="HSY")
        self.strain = Strain.objects.create(species=species, code="HSY-LAB-1", organization=self.org, number=1, origin_code="LAB")
        self.a = ThermalZone.objects.create(organization=self.org, name="A", target_temperature_c=Decimal("15.0"))
        self.b = ThermalZone.objects.create(organization=self.org, name="B", target_temperature_c=Decimal("10.0"))
        self.foreign = ThermalZone.objects.create(organization=self.other, name="Foreign")
        self.start = timezone.now() - timedelta(days=60)
        self.box = Box.objects.create(organization=self.org, global_code="HSY-LAB-1.001", box_number="001",
                                      strain=self.strain, status=Box.Status.ACTIVE, thermal_zone=self.a)
        self.first = BoxLocation.objects.create(box=self.box, thermal_zone=self.a, starts_at=self.start)

    def load(self, box=None, organization=None):
        return load_box_source_history(organization_id=(organization or self.org).pk, box_id=(box or self.box).pk)

    def lifecycle(self):
        """Creation period, ordinary move, deactivation, reactivation in the same zone."""
        self.moved_at = self.start + timedelta(days=20)
        self.movement = move_box_to_thermal_zone(box=self.box, thermal_zone=self.b, moved_at=self.moved_at,
                                                 user=self.admin, notes="")
        deactivate_box(box=self.box, user=self.admin, reason="Synthetic stop")
        self.box.refresh_from_db()
        reactivate_box(box=self.box, thermal_zone=self.b, user=self.admin)
        self.box.refresh_from_db()
        self.reactivation = BoxMovement.objects.get(box=self.box, kind=BoxMovement.Kind.REACTIVATION)
        self.stop_audit = AuditLog.objects.get(metadata__transition="active->inactive")

    def snapshot(self):
        models = (Box, BoxLocation, BoxMovement, HistoricalBoxMovement, AuditLog, BiologicalMeasurement, ThermalZone)
        return [list(model.objects.order_by("pk").values()) for model in models]

    def test_service_history_is_one_event_per_business_fact(self):
        self.lifecycle()
        source = self.load()
        events = by_key(source)
        self.assertEqual(sorted(events), sorted([
            f"location:{self.first.pk}:start", f"movement:{self.movement.pk}",
            f"lifecycle:{self.stop_audit.pk}", f"movement:{self.reactivation.pk}",
        ]))
        move = events[f"movement:{self.movement.pk}"]
        self.assertEqual((move.kind, move.origin_zone_id, move.destination_zone_id), (EventKind.MOVEMENT, self.a.pk, self.b.pk))
        self.assertEqual(move.temporal, TemporalPosition.exact(self.moved_at))
        self.assertEqual(refs(move), [
            ("audit_log", "receipt", "recorded"), ("box_location", "period_end", "recorded_link"),
            ("box_location", "period_start", "recorded_link"), ("box_movement", "event", "recorded"),
        ])
        self.assertTrue(move.details["recorded_via_polypbase"])
        stop = events[f"lifecycle:{self.stop_audit.pk}"]
        self.assertEqual((stop.kind, stop.origin, stop.origin_zone_id, stop.destination_zone_id),
                         (EventKind.DEACTIVATION, EventOrigin.LIFECYCLE_AUDIT, self.b.pk, None))
        self.assertEqual(stop.temporal.precision, TemporalPrecision.EXACT)
        restart = events[f"movement:{self.reactivation.pk}"]
        self.assertEqual((restart.kind, restart.origin_zone_id, restart.destination_zone_id),
                         (EventKind.REACTIVATION, None, self.b.pk))
        self.assertIn(("audit_log", "lifecycle", "recorded"), refs(restart))
        creation = events[f"location:{self.first.pk}:start"]
        self.assertEqual((creation.kind, creation.established, creation.temporal.precision),
                         (EventKind.PERIOD_BOUNDARY, False, TemporalPrecision.RECORDED_BOUNDARY))
        self.assertTrue(all(event.identity.stability == IdentityStability.PERSISTED for event in source.events))
        self.assertEqual(source.findings, ())

        projection = project_history(source)
        self.assertEqual(states(projection), [
            (OccupancyState.LOCATED, self.a.pk), (OccupancyState.LOCATED, self.b.pk),
            (OccupancyState.INACTIVE, None), (OccupancyState.LOCATED, self.b.pk),
        ])
        self.assertEqual(projection.conflicts, ())
        self.assertEqual(
            [(side.zone_id, side.side, side.related_zone_id) for side in projection.sides],
            [(self.a.pk, SideType.ENTRY, None), (self.a.pk, SideType.EXIT, self.b.pk),
             (self.b.pk, SideType.ENTRY, self.a.pk), (self.b.pk, SideType.EXIT, None),
             (self.b.pk, SideType.ENTRY, None)])

    def test_reading_and_simulating_never_write(self):
        self.lifecycle()
        BiologicalMeasurement.objects.create(box=self.box, measured_on=date(2026, 3, 2), polyp_count=0, ephyrae_count=0)
        before = self.snapshot()
        with CaptureQueriesContext(connection) as queries:
            source = self.load()
            projection = project_history(source, operations=[
                AdjustEventZones(EventTarget(f"movement:{self.movement.pk}"), destination_zone_id=self.a.pk),
                InsertEvent("d1", EventKind.DEACTIVATION, TemporalPosition.week(2026, 2, MOVEMENT_WEEK_TIME_ZONE)),
            ])
        self.assertEqual(before, self.snapshot())
        self.assertTrue(queries.captured_queries)
        self.assertTrue(all(query["sql"].lstrip().upper().startswith("SELECT") for query in queries.captured_queries))
        self.assertTrue(projection.conflicts)
        self.assertEqual(to_payload(self.load()), to_payload(source))
        self.assertEqual(project_history(self.load()), project_history(source))

    def test_institution_and_identity_boundaries(self):
        self.lifecycle()
        with self.assertRaises(HistorySourceNotFound):
            self.load(organization=self.other)
        with self.assertRaises(HistorySourceNotFound):
            load_box_source_history(organization_id=self.org.pk, box_id=self.box.pk + 1000)
        retired = Box.objects.create(organization=self.org, global_code="HSY-LAB-1.002", box_number="002",
                                     strain=self.strain, status=Box.Status.INACTIVE, merged_into=self.box)
        with self.assertRaises(HistorySourceNotFound):
            self.load(box=retired)
        other_box = Box.objects.create(organization=self.org, global_code="HSY-LAB-1.003", box_number="003",
                                       strain=self.strain, status=Box.Status.ACTIVE, thermal_zone=self.a)
        BoxLocation.objects.create(box=other_box, thermal_zone=self.a, starts_at=self.start)
        BoxMovement.objects.create(box=other_box, from_thermal_zone=self.a, to_thermal_zone=self.b,
                                   moved_at=self.start + timedelta(days=3), kind=BoxMovement.Kind.UNCLASSIFIED)
        source = self.load()
        self.assertEqual(source.zone_ids, frozenset({self.a.pk, self.b.pk}))
        self.assertEqual(len(source.events), 4)
        rejected = project_history(source, operations=[
            AdjustEventZones(EventTarget(f"movement:{self.movement.pk}"), destination_zone_id=self.foreign.pk),
        ]).rejected_operations
        self.assertEqual([item.code for item in rejected], [RejectionCode.ZONE_OUTSIDE_INSTITUTION])

    def test_foreign_zone_references_are_never_exposed(self):
        stray = BoxMovement.objects.create(box=self.box, from_thermal_zone=self.a, to_thermal_zone=self.foreign,
                                           moved_at=self.start + timedelta(days=5), kind=BoxMovement.Kind.UNCLASSIFIED)
        BoxLocation.objects.create(box=self.box, thermal_zone=self.foreign, starts_at=self.start + timedelta(days=5))
        source = self.load()
        event = by_key(source)[f"movement:{stray.pk}"]
        self.assertEqual((event.kind, event.established, event.destination_zone_id),
                         (EventKind.UNCLASSIFIED_MOVEMENT, False, None))
        self.assertEqual(sorted(finding.code for finding in source.findings),
                         ["period_zone_outside_institution", "zone_outside_institution"])
        self.assertNotIn(self.foreign.pk, to_payload(source)["zone_ids"])
        self.assertFalse(any(self.foreign.pk in (item.origin_zone_id, item.destination_zone_id) for item in source.events))

    def test_foreign_zones_downgrade_every_movement_kind_on_either_side(self):
        for kind in (BoxMovement.Kind.ORDINARY, BoxMovement.Kind.INITIAL_LOCATION,
                     BoxMovement.Kind.QUALIFICATION, BoxMovement.Kind.REACTIVATION):
            for side in ("origin", "destination"):
                with self.subTest(kind=kind, side=side):
                    movement = BoxMovement.objects.create(
                        box=self.box, kind=kind, moved_at=self.start + timedelta(days=5),
                        from_thermal_zone=self.foreign if side == "origin" else self.a,
                        to_thermal_zone=self.foreign if side == "destination" else self.b)
                    source = self.load()
                    event = by_key(source)[f"movement:{movement.pk}"]
                    self.assertFalse(event.established)
                    self.assertEqual((event.origin_zone_id, event.destination_zone_id),
                                     (None, self.b.pk) if side == "origin" else (self.a.pk, None))
                    self.assertEqual(refs(event), [("box_movement", "event", "recorded")])
                    self.assertEqual([(item.code, dict(item.details)) for item in source.findings], [
                        ("zone_outside_institution", {"movement_id": movement.pk, "side": side})])
                    movement.delete()

    def test_ordinary_movement_with_missing_origin_is_not_established(self):
        movement = BoxMovement.objects.create(box=self.box, kind=BoxMovement.Kind.ORDINARY,
                                               to_thermal_zone=self.b, moved_at=self.start + timedelta(days=5))
        source = self.load()
        event = by_key(source)[f"movement:{movement.pk}"]
        self.assertFalse(event.established)
        self.assertEqual((event.origin_zone_id, event.destination_zone_id), (None, self.b.pk))
        self.assertEqual([(item.code, dict(item.details)) for item in source.findings], [
            ("zone_missing", {"movement_id": movement.pk, "side": "origin"})])

    def test_foreign_current_zone_is_sanitized_without_guessing_from_periods(self):
        Box.objects.filter(pk=self.box.pk).update(thermal_zone=self.foreign)
        source = self.load()
        self.assertEqual((source.current.status, source.current.thermal_zone_id), (Box.Status.ACTIVE, None))
        self.assertEqual([(item.code, dict(item.details)) for item in source.findings], [
            ("zone_outside_institution", {"box_id": self.box.pk, "side": "current"})])
        self.assertIsNone(to_payload(source)["current"]["thermal_zone_id"])

    def test_foreign_receipt_zones_are_sanitized_even_with_accepted_plan(self):
        boundary = self.start + timedelta(days=10)
        second = BoxLocation.objects.create(box=self.box, thermal_zone=self.b, starts_at=boundary)
        for side in ("origin", "destination"):
            with self.subTest(side=side):
                receipt = HistoricalBoxMovement.objects.bulk_create([HistoricalBoxMovement(
                    box=self.box, iso_year=2026, iso_week=7 if side == "origin" else 8,
                    from_thermal_zone=self.foreign if side == "origin" else self.a,
                    to_thermal_zone=self.foreign if side == "destination" else self.b,
                    origin_location=self.first, destination_location=second,
                    candidate_key=side, plan_fingerprint="b" * 64)])[0]
                candidate = {"id": side, "reconciliation_status": "accepted", "blockers": [],
                             "evidence": {"basis": RECONSTRUCTION_PROVENANCE}}
                with patch("apps.cultures.effective_history_sources._plan", return_value={"candidates": [candidate]}):
                    source = self.load()
                event = by_key(source)[f"historical:{receipt.pk}"]
                self.assertFalse(event.established)
                self.assertEqual(event.temporal, TemporalPosition.unknown())
                self.assertEqual((event.origin_zone_id, event.destination_zone_id),
                                 (None, self.b.pk) if side == "origin" else (self.a.pk, None))
                self.assertIn(("historical_box_movement", "event", "recorded"), refs(event))
                self.assertEqual(len([ref for ref in event.sources if ref.source == "box_location"]), 2)
                self.assertIn(("zone_outside_institution", {"receipt_id": receipt.pk, "side": side}),
                              [(item.code, dict(item.details)) for item in source.findings])

    def test_qualification_to_active_with_and_without_new_location(self):
        Box.objects.filter(pk=self.box.pk).update(status=Box.Status.PENDING_REVIEW)
        self.box.refresh_from_db()
        qualify_pending_box(box=self.box, target_status=Box.Status.ACTIVE, user=self.admin)
        source = self.load()
        self.assertEqual(list(by_key(source)), [f"location:{self.first.pk}:start"])
        self.assertEqual(source.findings, ())
        self.assertEqual(source.current.thermal_zone_id, self.a.pk)

        BoxLocation.objects.filter(pk=self.first.pk).delete()
        Box.objects.filter(pk=self.box.pk).update(status=Box.Status.PENDING_REVIEW, thermal_zone=None)
        self.box.refresh_from_db()
        qualify_pending_box(box=self.box, target_status=Box.Status.ACTIVE, thermal_zone=self.b, user=self.admin)
        movement = BoxMovement.objects.get(box=self.box, kind=BoxMovement.Kind.QUALIFICATION)
        source = self.load()
        event = by_key(source)[f"movement:{movement.pk}"]
        self.assertEqual((event.kind, event.established, event.destination_zone_id),
                         (EventKind.LOCATION_ASSIGNMENT, True, self.b.pk))
        self.assertIn(("audit_log", "lifecycle", "recorded"), refs(event))
        self.assertEqual(source.findings, ())
        self.assertEqual(len(source.events), 1)

    def test_qualification_without_any_location_does_not_invent_an_assignment(self):
        BoxLocation.objects.filter(pk=self.first.pk).delete()
        Box.objects.filter(pk=self.box.pk).update(status=Box.Status.PENDING_REVIEW, thermal_zone=None)
        self.box.refresh_from_db()
        qualify_pending_box(box=self.box, target_status=Box.Status.ACTIVE, user=self.admin)
        source = self.load()
        self.assertEqual(source.events, ())
        self.assertEqual(source.findings, ())
        self.assertEqual((source.current.status, source.current.thermal_zone_id), (Box.Status.ACTIVE, None))

    def test_unpaired_reactivation_keeps_both_representations_without_guessing(self):
        movement = BoxMovement.objects.create(box=self.box, kind=BoxMovement.Kind.REACTIVATION,
                                               to_thermal_zone=self.b, moved_at=self.start + timedelta(days=5))
        audit = AuditLog.objects.create(
            organization=self.org, user=self.admin, action=AuditLog.Action.UPDATE,
            object_type="box", object_id=self.box.global_code,
            metadata={"box_id": self.box.pk, "transition": "inactive->active", "movement_id": movement.pk})
        source = self.load()
        event = by_key(source)[f"lifecycle:{audit.pk}"]
        self.assertFalse(event.established)
        self.assertEqual((event.kind, event.temporal, event.origin_zone_id, event.destination_zone_id),
                         (EventKind.REACTIVATION, TemporalPosition.unknown(), None, None))
        self.assertIn(("box_movement", "event", "recorded"), refs(event))
        self.assertIn(f"movement:{movement.pk}", by_key(source))
        self.assertIn(ConflictCode.COMPETING_EVENT_REPRESENTATIONS,
                      [conflict.code for conflict in project_history(source).conflicts])
        self.assertEqual([(item.code, dict(item.details)) for item in source.findings], [
            ("lifecycle_movement_unverified", {"audit_id": audit.pk})])

        audit.metadata.pop("movement_id")
        audit.save(update_fields=["metadata"])
        event = by_key(self.load())[f"lifecycle:{audit.pk}"]
        self.assertEqual(refs(event), [("audit_log", "lifecycle", "recorded")])
        self.assertFalse(event.established)

    def test_deactivation_without_changed_at_does_not_use_recorded_at(self):
        deactivate_box(box=self.box, user=self.admin, reason="Synthetic stop")
        audit = AuditLog.objects.get(metadata__transition="active->inactive")
        audit.metadata.pop("changed_at")
        audit.save(update_fields=["metadata"])
        source = self.load()
        event = by_key(source)[f"lifecycle:{audit.pk}"]
        self.assertEqual(event.temporal, TemporalPosition.unknown())
        self.assertIsNone(event.origin_zone_id)
        self.assertFalse(event.established)
        self.assertEqual(refs(event), [("audit_log", "lifecycle", "recorded")])
        self.assertEqual([(item.code, dict(item.details)) for item in source.findings], [
            ("lifecycle_locations_unverified", {"audit_id": audit.pk})])
        self.assertIn(f"location:{self.first.pk}:end", by_key(source))

    def test_deactivation_with_foreign_or_missing_period_is_not_established(self):
        changed_at = self.start + timedelta(days=10)
        foreign_period = BoxLocation.objects.create(box=self.box, thermal_zone=self.foreign,
                                                    starts_at=self.start, ends_at=changed_at)
        audit = AuditLog.objects.create(
            organization=self.org, user=self.admin, action=AuditLog.Action.UPDATE,
            object_type="box", object_id=self.box.global_code,
            metadata={"box_id": self.box.pk, "transition": "active->inactive",
                      "changed_at": changed_at.isoformat(), "closed_location_ids": [foreign_period.pk]})
        for location_id in (foreign_period.pk, foreign_period.pk + 1000):
            with self.subTest(location_id=location_id):
                audit.metadata["closed_location_ids"] = [location_id]
                audit.save(update_fields=["metadata"])
                source = self.load()
                event = by_key(source)[f"lifecycle:{audit.pk}"]
                self.assertFalse(event.established)
                self.assertIsNone(event.origin_zone_id)
                self.assertEqual(event.temporal, TemporalPosition.exact(changed_at))
                self.assertEqual(refs(event), [("audit_log", "lifecycle", "recorded")])
                self.assertIn(("lifecycle_locations_unverified", {"audit_id": audit.pk}),
                              [(item.code, dict(item.details)) for item in source.findings])
                self.assertNotIn(self.foreign.pk, source.zone_ids)

    def test_legacy_movement_at_a_stored_boundary_is_not_duplicated(self):
        boundary = self.start + timedelta(days=10)
        BoxLocation.objects.filter(pk=self.first.pk).update(ends_at=boundary)
        second = BoxLocation.objects.create(box=self.box, thermal_zone=self.b, starts_at=boundary)
        legacy = BoxMovement.objects.create(box=self.box, from_thermal_zone=self.a, to_thermal_zone=self.b,
                                            moved_at=boundary, kind=BoxMovement.Kind.UNCLASSIFIED)
        Box.objects.filter(pk=self.box.pk).update(thermal_zone=self.b)
        source = self.load()
        self.assertEqual(sorted(by_key(source)), sorted([f"location:{self.first.pk}:start", f"movement:{legacy.pk}"]))
        event = by_key(source)[f"movement:{legacy.pk}"]
        self.assertEqual(event.kind, EventKind.UNCLASSIFIED_MOVEMENT)
        self.assertEqual(refs(event), [
            ("box_location", "period_end", "exact_boundary_match"),
            ("box_location", "period_start", "exact_boundary_match"), ("box_movement", "event", "recorded"),
        ])
        # No link was written to make the match.
        legacy.refresh_from_db()
        self.assertEqual((legacy.origin_location_id, legacy.destination_location_id, legacy.kind), (None, None, "unclassified"))
        projection = project_history(source)
        self.assertEqual(states(projection), [(OccupancyState.LOCATED, self.a.pk), (OccupancyState.LOCATED, self.b.pk)])
        self.assertEqual(projection.conflicts, ())
        self.assertEqual(second.pk, BoxLocation.objects.get(box=self.box, ends_at__isnull=True).pk)

    def test_bare_period_boundaries_remain_evidence_of_unknown_nature(self):
        boundary = self.start + timedelta(days=10)
        BoxLocation.objects.filter(pk=self.first.pk).update(ends_at=boundary)
        second = BoxLocation.objects.create(box=self.box, thermal_zone=self.b, starts_at=boundary)
        Box.objects.filter(pk=self.box.pk).update(thermal_zone=self.b)
        source = self.load()
        pair = by_key(source)[f"location:{self.first.pk}:end+location:{second.pk}:start"]
        self.assertEqual((pair.kind, pair.established, pair.origin_zone_id, pair.destination_zone_id),
                         (EventKind.PERIOD_BOUNDARY, False, self.a.pk, self.b.pk))
        self.assertEqual(len(source.events), 2)
        self.assertEqual(project_history(source).conflicts, ())

    def test_overlapping_periods_are_reported(self):
        BoxLocation.objects.create(box=self.box, thermal_zone=self.b, starts_at=self.start + timedelta(days=10))
        projection = project_history(self.load())
        self.assertEqual([(conflict.code, conflict.certainty) for conflict in projection.conflicts], [
            (ConflictCode.OVERLAPPING_LOCATION_ASSIGNMENT, ConflictCertainty.UNCERTAIN),
            (ConflictCode.CURRENT_OCCUPANCY_UNRECONCILED, ConflictCertainty.UNCERTAIN),
        ])

    def test_historical_qualification_keeps_the_unknown_end_unknown(self):
        Box.objects.filter(pk=self.box.pk).update(status=Box.Status.PENDING_REVIEW)
        self.box.refresh_from_db()
        qualify_pending_box(box=self.box, target_status=Box.Status.INACTIVE, user=self.admin,
                            reason_missing_from_history=True)
        source = self.load()
        audit = AuditLog.objects.get(metadata__transition="pending_review->inactive")
        stop = by_key(source)[f"lifecycle:{audit.pk}"]
        self.assertEqual((stop.kind, stop.origin_zone_id), (EventKind.DEACTIVATION, self.a.pk))
        self.assertEqual(stop.temporal, TemporalPosition.unknown(not_before=self.start))
        self.assertEqual(len(source.events), 2)
        projection = project_history(source)
        self.assertEqual(states(projection), [(OccupancyState.LOCATED, self.a.pk), (OccupancyState.INACTIVE, None)])
        closed = projection.intervals[0]
        self.assertIsNone(closed.end.instant)
        self.assertIsNone(closed.end.before)
        self.assertTrue(closed.boundary_uncertain)
        self.assertNotIn(ConflictCode.CURRENT_OCCUPANCY_UNRECONCILED, [conflict.code for conflict in projection.conflicts])

    def test_accepted_week_reconstruction_is_one_week_precision_event(self):
        box = Box.objects.create(organization=self.org, global_code="HSY-LAB-1.004", box_number="004",
                                 strain=self.strain, thermal_zone=self.b)
        origin = BoxLocation.objects.create(box=box, thermal_zone=self.a, starts_at=paris(2025, 12, 15), ends_at=paris(2025, 12, 29))
        destination = BoxLocation.objects.create(box=box, thermal_zone=self.b, starts_at=paris(2025, 12, 29))
        for day in (date(2025, 12, 22), date(2025, 12, 29)):
            BiologicalMeasurement.objects.create(box=box, measured_on=day, polyp_count=0, ephyrae_count=0)
        plan = plan_historical_movements(organization_id=self.org.pk, box_ids=[box.pk])
        accept_historical_movements(
            organization_id=self.org.pk, box_ids=[box.pk], candidate_ids=[plan["candidates"][0]["id"]],
            expected_fingerprint=plan["fingerprint"], actor=self.admin, notes="Synthetic explicit review")
        receipt = HistoricalBoxMovement.objects.get(box=box)
        source = self.load(box=box)
        self.assertEqual(sorted(by_key(source)), sorted([f"location:{origin.pk}:start", f"historical:{receipt.pk}"]))
        event = by_key(source)[f"historical:{receipt.pk}"]
        self.assertEqual((event.kind, event.established), (EventKind.RECONSTRUCTED_MOVEMENT, True))
        self.assertEqual((event.temporal.precision, event.temporal.iso_year, event.temporal.iso_week, event.temporal.instant),
                         (TemporalPrecision.ISO_WEEK, 2025, 52, None))
        self.assertEqual(refs(event), [
            ("box_location", "period_end", "reconstruction_receipt"),
            ("box_location", "period_start", "reconstruction_receipt"), ("historical_box_movement", "event", "recorded"),
        ])
        projection = project_history(source)
        self.assertEqual(states(projection), [(OccupancyState.LOCATED, self.a.pk), (OccupancyState.LOCATED, self.b.pk)])
        self.assertEqual(projection.conflicts, ())
        self.assertEqual(destination.thermal_zone_id, projection.intervals[-1].zone_id)

    def test_unverified_reconstruction_is_kept_unplaced(self):
        boundary = self.start + timedelta(days=10)
        BoxLocation.objects.filter(pk=self.first.pk).update(ends_at=boundary)
        second = BoxLocation.objects.create(box=self.box, thermal_zone=self.b, starts_at=boundary)
        receipt = HistoricalBoxMovement.objects.bulk_create([HistoricalBoxMovement(
            box=self.box, iso_year=2026, iso_week=7, from_thermal_zone=self.a, to_thermal_zone=self.b,
            origin_location=self.first, destination_location=second, candidate_key="a" * 64, plan_fingerprint="b" * 64)])[0]
        source = self.load()
        event = by_key(source)[f"historical:{receipt.pk}"]
        self.assertEqual((event.established, event.temporal), (False, TemporalPosition.unknown()))
        self.assertEqual(event.details["stored_iso_week"], 7)
        projection = project_history(source)
        self.assertEqual([item.key for item in projection.unplaced_events], [f"historical:{receipt.pk}"])
        self.assertIn(ConflictCode.INSUFFICIENT_TEMPORAL_PRECISION, [conflict.code for conflict in projection.conflicts])

    def test_institution_specific_setpoint_rule_is_not_extended(self):
        self.assertNotEqual(self.org.pk, 1)
        for week, temperature in ((7, "15"), (8, "10")):
            monday = date.fromisocalendar(2026, week, 1)
            biology = BiologicalMeasurement.objects.create(box=self.box, measured_on=monday, polyp_count=0, ephyrae_count=0)
            HistoricalTemperatureReading.objects.create(biological_measurement=biology, iso_year=2026, iso_week=week,
                                                        temperature_c=temperature)
        source = self.load()
        self.assertFalse(any(event.kind == EventKind.RECONSTRUCTED_MOVEMENT for event in source.events))
        self.assertEqual(source.occupancy_evidence, ())
        self.assertNotIn(STARTING_LOCATION_KEY, by_key(source))


class ObservedEnvironmentSourceTests(TestCase):
    """The confirmed rule names its institution by primary key."""

    def setUp(self):
        self.org, _created = Organization.objects.update_or_create(pk=1, defaults={"name": "Aquarium de Paris"})
        species = Species.objects.create(scientific_name="Observed synthetic", genus_species_code="OBS")
        self.strain = Strain.objects.create(species=species, code="OBS-LAB-1", organization=self.org, number=1, origin_code="LAB")
        self.zone15 = ThermalZone.objects.create(organization=self.org, name="Zone 15", target_temperature_c=Decimal("15.0"))
        self.zone10 = ThermalZone.objects.create(organization=self.org, name="Zone 10", target_temperature_c=Decimal("10.0"))
        self.zone0 = ThermalZone.objects.create(organization=self.org, name="Zone 0", target_temperature_c=Decimal("0"))
        self.box = Box.objects.create(organization=self.org, global_code="OBS-LAB-1.001", box_number="001",
                                      strain=self.strain, status=Box.Status.INACTIVE)

    def observe(self, week, temperature):
        monday = date.fromisocalendar(2026, week, 1)
        biology = BiologicalMeasurement.objects.create(box=self.box, measured_on=monday, polyp_count=0, ephyrae_count=0)
        reading = HistoricalTemperatureReading.objects.create(
            biological_measurement=biology, iso_year=2026, iso_week=week, temperature_c=temperature)
        HistoricalTemperatureEvidence(
            reading=reading, source_file=SOURCE_FILE, source_sha256=SOURCE_SHA, sheet="Aurelia", temperature_cell=f"C{week + 2}",
            source_value=temperature, source_box=self.box.global_code, polyp_cell=f"F{week + 2}", polyp_value="0",
            ephyrae_cell=f"F{week + 3}", ephyrae_value="0", week_cell="F2", week_value=str(week),
            source_iso_year=2026, source_iso_week=week).save()
        return reading

    def load(self):
        return load_box_source_history(organization_id=self.org.pk, box_id=self.box.pk)

    def transitions(self, source):
        return [event for event in source.events if event.origin == EventOrigin.HISTORICAL_TRANSITION]

    def test_observed_change_is_an_interval_with_a_derived_identity(self):
        origin, destination = self.observe(7, "15"), self.observe(8, "10")
        source = self.load()
        (event,) = self.transitions(source)
        self.assertEqual((event.kind, event.origin_zone_id, event.destination_zone_id),
                         (EventKind.RECONSTRUCTED_MOVEMENT, self.zone15.pk, self.zone10.pk))
        self.assertEqual(event.temporal.precision, TemporalPrecision.OBSERVATION_INTERVAL)
        self.assertEqual((event.temporal.instant, event.temporal.not_before, event.temporal.before),
                         (None, paris(2026, 2, 9), paris(2026, 2, 23)))
        self.assertEqual(event.identity.stability, IdentityStability.DERIVED)
        self.assertEqual(dict(event.identity.locator)["origin_reading_id"], origin.pk)
        self.assertEqual(dict(event.identity.locator)["destination_reading_id"], destination.pk)
        self.assertEqual(event.identity, self.transitions(self.load())[0].identity)
        self.assertEqual([(item.zone_id, item.within.iso_week) for item in source.occupancy_evidence],
                         sorted([(self.zone15.pk, 7), (self.zone10.pk, 8)]))
        start = by_key(source)[STARTING_LOCATION_KEY]
        self.assertEqual((start.kind, start.destination_zone_id, start.temporal.precision),
                         (EventKind.INITIAL_HISTORICAL_LOCATION, self.zone15.pk, TemporalPrecision.ISO_WEEK))

        projection = project_history(source)
        self.assertEqual(states(projection), [(OccupancyState.LOCATED, self.zone15.pk), (OccupancyState.LOCATED, self.zone10.pk)])
        self.assertTrue(all(interval.boundary_uncertain for interval in projection.intervals))
        self.assertNotIn(ConflictCode.INSUFFICIENT_TEMPORAL_PRECISION,
                         [conflict.code for conflict in projection.conflicts])
        self.assertEqual([(conflict.code, conflict.certainty) for conflict in projection.conflicts], [
            (ConflictCode.CURRENT_OCCUPANCY_UNRECONCILED, ConflictCertainty.IMPOSSIBLE)])
        self.assertEqual({side.event_key for side in projection.sides}, {event.identity.key})

    def test_changed_evidence_changes_the_fingerprint_not_the_contract(self):
        self.observe(7, "15")
        self.observe(8, "10")
        (before,) = self.transitions(self.load())
        self.zone10.target_temperature_c = Decimal("12.0")
        self.zone10.save()
        replacement = ThermalZone.objects.create(organization=self.org, name="Zone 10 bis", target_temperature_c=Decimal("10.0"))
        (after,) = self.transitions(self.load())
        self.assertEqual(after.destination_zone_id, replacement.pk)
        self.assertEqual(after.identity.key, before.identity.key)
        self.assertEqual(after.identity.locator, before.identity.locator)
        self.assertNotEqual(after.identity.evidence_fingerprint, before.identity.evidence_fingerprint)
        rejected = project_history(self.load(), operations=[AdjustEventZones(
            EventTarget(before.identity.key, before.identity.evidence_fingerprint), destination_zone_id=self.zone0.pk,
        )]).rejected_operations
        self.assertEqual([item.code for item in rejected], [RejectionCode.TARGET_EVIDENCE_CHANGED])

    def test_zero_setpoint_is_a_real_environment_and_absence_stays_absent(self):
        self.assertEqual(self.transitions(self.load()), [])
        self.assertNotIn(STARTING_LOCATION_KEY, by_key(self.load()))
        self.observe(7, "0")
        self.observe(8, "10")
        source = self.load()
        (event,) = self.transitions(source)
        self.assertEqual((event.origin_zone_id, event.destination_zone_id), (self.zone0.pk, self.zone10.pk))
        self.assertEqual(event.details["from_environment_c"], "0")
        self.assertEqual(by_key(source)[STARTING_LOCATION_KEY].destination_zone_id, self.zone0.pk)
        self.assertEqual(source.occupancy_evidence[0].zone_id, min(self.zone0.pk, self.zone10.pk))
        payload = to_payload(project_history(source))
        details = next(item for item in payload["events"] if item["origin"] == "historical_transition")["original"]["details"]
        self.assertEqual(details["from_environment_c"], "0")

    def test_recorded_movement_absorbs_the_observed_change(self):
        self.observe(7, "15")
        self.observe(8, "10")
        movement = BoxMovement.objects.create(box=self.box, from_thermal_zone=self.zone15, to_thermal_zone=self.zone10,
                                              moved_at=paris(2026, 2, 17, 9), kind=BoxMovement.Kind.ORDINARY)
        source = self.load()
        self.assertEqual(self.transitions(source), [])
        event = by_key(source)[f"movement:{movement.pk}"]
        self.assertIn(("historical_transition", "corroboration", "recorded"), refs(event))
        self.assertEqual(event.temporal, TemporalPosition.exact(paris(2026, 2, 17, 9)))

    def test_stored_periods_and_observed_change_are_one_event(self):
        self.observe(7, "15")
        self.observe(8, "10")
        origin = BoxLocation.objects.create(box=self.box, thermal_zone=self.zone15, starts_at=paris(2026, 2, 2), ends_at=paris(2026, 2, 16))
        BoxLocation.objects.create(box=self.box, thermal_zone=self.zone10, starts_at=paris(2026, 2, 16))
        source = self.load()
        (event,) = self.transitions(source)
        self.assertEqual([ref for ref in refs(event) if ref[0] == "box_location"], [
            ("box_location", "period_end", "location_periods"), ("box_location", "period_start", "location_periods")])
        boundaries = [item.identity.key for item in source.events if item.kind == EventKind.PERIOD_BOUNDARY]
        self.assertEqual(boundaries, [f"location:{origin.pk}:start"])
        self.assertNotIn(ConflictCode.COMPETING_EVENT_REPRESENTATIONS,
                         [conflict.code for conflict in project_history(source).conflicts])

    def test_unresolved_observed_change_is_a_finding_not_an_event(self):
        self.observe(7, "15")
        self.observe(8, "12")
        source = self.load()
        self.assertEqual(self.transitions(source), [])
        self.assertEqual([finding.code for finding in source.findings], ["historical_transition_unresolved"])
        self.assertEqual(source.findings[0].details["status"], "environment_only")
