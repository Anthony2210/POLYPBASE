"""PostgreSQL-only serialization with measurements, subcultures and locations."""

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from unittest import skipUnless
from unittest.mock import patch

from django.db import close_old_connections, connection, connections
from django.test import TransactionTestCase

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.measurements import services as measurement_services
from apps.measurements.models import BiologicalMeasurement

from . import movement_corrections
from . import services as culture_services
from .historical_2026 import cca_eob_correction as correction
from .historical_2026.cca_eob_correction import fingerprint
from .models import Box, BoxLocation, BoxMovement, SubcultureAllocation, SubcultureEvent, ThermalZone
from .polyp_state import resolve_current_polyp_state
from .test_cca_eob_correction import CcaEobFixtureMixin


@skipUnless(connection.vendor == "postgresql", "CCA correction concurrency requires PostgreSQL row locks.")
class CcaEobCorrectionConcurrencyTests(CcaEobFixtureMixin, TransactionTestCase):
    reset_sequences = True

    def run_worker(self, callback, *, lock_attempted=None, lock_table=None, finished=None):
        close_old_connections()

        def observe_lock(execute, sql, params, many, context):
            if lock_attempted is not None and "FOR UPDATE" in sql and lock_table in sql:
                lock_attempted.set()
            return execute(sql, params, many, context)

        try:
            with connection.execute_wrapper(observe_lock):
                return callback()
        finally:
            connections.close_all()
            if finished is not None:
                finished.set()

    def test_ordinary_creation_waits_for_correction_box_lock_then_keeps_both_writes(self):
        plan = self.plan()
        entered = threading.Event()
        attempted = threading.Event()
        release = threading.Event()
        finished = threading.Event()
        real_create = AuditLog.objects.create
        target = self.boxes[4]
        before_revision = Box.objects.get(pk=target.pk).polyp_state_revision

        def blocking_audit(**kwargs):
            result = real_create(**kwargs)
            metadata = kwargs.get("metadata", {})
            if (metadata.get("operation") == correction.OPERATION
                    and metadata.get("measurement_id") == plan["move_order"][0]):
                entered.set()
                if not release.wait(timeout=10):
                    raise TimeoutError("Correction audit was not released.")
            return result

        def create_ordinary():
            return measurement_services.create_biological_measurement(
                box_id=target.pk, organization_ids=[self.org.pk], user=self.writer,
                data={"measured_on": date.fromisocalendar(2026, 42, 3),
                      "polyp_count": 0, "ephyrae_count": 0},
            ).pk

        with patch.object(AuditLog.objects, "create", side_effect=blocking_audit), ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.run_worker, lambda: self.apply(plan=plan))
            try:
                if not entered.wait(timeout=10):
                    first.result(timeout=1)
                    self.fail("Correction did not reach its locked audit.")
                second = pool.submit(
                    self.run_worker, create_ordinary, lock_attempted=attempted,
                    lock_table="cultures_box", finished=finished,
                )
                self.assertTrue(attempted.wait(timeout=10), "Ordinary write did not attempt the Box lock.")
                self.assertFalse(finished.wait(timeout=0.2), "Ordinary write bypassed the correction locks.")
                self.assertFalse(BiologicalMeasurement.objects.filter(
                    box=target, week_start=date.fromisocalendar(2026, 42, 1),
                ).exists())
            finally:
                release.set()
            self.assertEqual(first.result(timeout=15), plan)
            ordinary_pk = second.result(timeout=15)

        ordinary = BiologicalMeasurement.objects.get(pk=ordinary_pk)
        self.assertEqual((ordinary.box_id, ordinary.polyp_count, ordinary.ephyrae_count, ordinary.user_id),
                         (target.pk, 0, 0, self.writer.pk))
        self.assertEqual(ordinary.temporal_precision, BiologicalMeasurement.TemporalPrecision.EXACT_DATE)
        self.assertEqual(BiologicalMeasurement.objects.count(), 5)
        self.assertEqual(Box.objects.get(pk=target.pk).polyp_state_revision, before_revision + 3)
        self.assertEqual(BiologicalMeasurement.objects.get(pk=self.measurements[0].pk).box_id, target.pk)
        self.assertEqual(BiologicalMeasurement.objects.get(pk=self.measurements[2].pk).box_id, target.pk)
        audits = list(AuditLog.objects.order_by("pk"))
        self.assertEqual(len(audits), 5)
        self.assertEqual(audits[-1].metadata["measurement_id"], ordinary_pk)
        self.assertEqual(audits[-1].user_id, self.writer.pk)
        self.assertEqual(audits[-1].action, AuditLog.Action.ENTRY)
        self.assertEqual(audits[-2].object_type, "historical_correction")
        refreshed = self.plan()
        self.assertTrue(refreshed["ready"], refreshed["blockers"])
        self.assertEqual(refreshed["counts"], {correction.MATCH: 4, correction.OUTSIDE: 1})

    def test_competing_ordinary_correction_commits_then_reviewed_plan_is_stale(self):
        # Historical observations are outside the technician's 24-hour window.
        OrganizationMembership.objects.filter(organization=self.org, user=self.writer).update(role="admin")
        plan = self.plan()
        entered = threading.Event()
        attempted = threading.Event()
        release = threading.Event()
        finished = threading.Event()
        measurement = self.measurements[0]
        before_revision = Box.objects.get(pk=measurement.box_id).polyp_state_revision
        real_audit = measurement_services._record_measurement_audit

        def blocking_audit(**kwargs):
            result = real_audit(**kwargs)
            entered.set()
            if not release.wait(timeout=10):
                raise TimeoutError("Ordinary correction audit was not released.")
            return result

        def correct_ordinary():
            return measurement_services.correct_biological_measurement(
                box_id=measurement.box_id, measurement_id=measurement.pk,
                organization_ids=[self.org.pk], user=self.writer,
                data={"notes": "Competing ordinary metadata correction"},
            ).pk

        with patch.object(measurement_services, "_record_measurement_audit", side_effect=blocking_audit), ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.run_worker, correct_ordinary)
            try:
                if not entered.wait(timeout=10):
                    first.result(timeout=1)
                    self.fail("Ordinary correction did not reach its transaction.")

                second = pool.submit(
                    self.run_worker, lambda: self.apply(plan=plan), lock_attempted=attempted,
                    lock_table="cultures_box", finished=finished,
                )
                self.assertTrue(attempted.wait(timeout=10), "Historical correction did not attempt its lock.")
                self.assertFalse(finished.wait(timeout=0.2), "Historical correction bypassed the competing lock.")
            finally:
                release.set()
            self.assertEqual(first.result(timeout=15), measurement.pk)
            with self.assertRaisesMessage(ValueError, "Stale plan"):
                second.result(timeout=15)

        measurement.refresh_from_db()
        self.assertEqual(measurement.notes, "Competing ordinary metadata correction")
        self.assertEqual(measurement.box_id, self.boxes[5].pk)
        self.assertEqual(measurement.polyp_state_sequence, 41)
        self.assertEqual(Box.objects.get(pk=measurement.box_id).polyp_state_revision, before_revision + 1)
        self.assertEqual(BiologicalMeasurement.objects.count(), 4)
        self.assertEqual(AuditLog.objects.count(), 1)
        audit = AuditLog.objects.get()
        self.assertEqual((audit.user_id, audit.action), (self.writer.pk, AuditLog.Action.UPDATE))
        self.assertEqual(audit.metadata["after"]["note"], measurement.notes)
        self.assertFalse(AuditLog.objects.filter(metadata__operation=correction.OPERATION).exists())
        refreshed = self.plan()
        self.assertTrue(refreshed["ready"], refreshed["blockers"])
        self.assertNotEqual(fingerprint(refreshed), fingerprint(plan))
        self.assertEqual(refreshed["entries"][0]["before"]["notes"], measurement.notes)
        self.apply(plan=refreshed)
        measurement.refresh_from_db()
        self.assertEqual(measurement.box_id, self.boxes[4].pk)
        self.assertEqual(measurement.notes, "Competing ordinary metadata correction")

    def run_movement(self, box, destination):
        return culture_services.move_box_to_thermal_zone(
            box=box, thermal_zone=destination,
            moved_at=datetime(2026, 9, 8, 12, tzinfo=timezone.utc),
            user=self.admin, notes="Concurrent location change",
            expected_thermal_zone_id=self.zone.pk,
        ).pk

    def ordered_location_race(self, first_action, second_action, *, fail_first=False):
        entered, attempted, release, finished = (threading.Event() for _ in range(4))
        local = threading.local()
        real_create = AuditLog.objects.create

        def blocking_audit(**kwargs):
            result = real_create(**kwargs)
            metadata = kwargs.get("metadata", {})
            # For the corrector, pause after revisions and the summary audit so
            # rollback exercises the whole write set, not only the first move.
            is_correction = metadata.get("operation") == correction.OPERATION
            if (getattr(local, "is_first", False)
                    and (not is_correction or kwargs.get("object_type") == "historical_correction")):
                entered.set()
                if not release.wait(timeout=10):
                    raise TimeoutError("First location/correction operation was not released.")
                if fail_first:
                    raise RuntimeError("Synthetic location race audit rollback")
            return result

        def action(callback, is_first):
            local.is_first = is_first
            try:
                return callback()
            except (ValueError, RuntimeError) as error:
                return error

        with patch.object(AuditLog.objects, "create", side_effect=blocking_audit), ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.run_worker, lambda: action(first_action, True))
            try:
                if not entered.wait(timeout=10):
                    self.fail(f"First operation did not reach its audit: {first.result(timeout=1)!r}")
                second = pool.submit(
                    self.run_worker, lambda: action(second_action, False),
                    lock_attempted=attempted, lock_table="cultures_box", finished=finished,
                )
                self.assertTrue(attempted.wait(timeout=10), "Second operation did not attempt its Box lock.")
                self.assertFalse(finished.wait(timeout=0.2), "Second operation bypassed the held Box lock.")
            finally:
                release.set()
            return first.result(timeout=15), second.result(timeout=15)

    def test_movement_commits_first_then_correction_waits_and_is_stale_without_mutation(self):
        box = self.boxes[5]
        destination = ThermalZone.objects.create(organization=self.org, name="Movement destination")
        plan = self.plan()
        self.assertTrue(plan["ready"], plan["blockers"])
        measurements = list(BiologicalMeasurement.objects.order_by("pk").values())
        revisions = dict(Box.objects.values_list("pk", "polyp_state_revision"))

        def move_then_snapshot():
            pk = self.run_movement(box, destination)
            return {"movement_pk": pk, "database": self.database_snapshot()}

        first, second = self.ordered_location_race(move_then_snapshot, lambda: self.apply(plan=plan))
        self.assertIsInstance(first, dict)
        self.assertIsInstance(second, ValueError)
        self.assertIn("Stale plan", str(second))
        self.assertEqual(self.database_snapshot(), first["database"])
        self.assertEqual(list(BiologicalMeasurement.objects.order_by("pk").values()), measurements)
        self.assertEqual(dict(Box.objects.values_list("pk", "polyp_state_revision")), revisions)
        self.assert_committed_movement(first["movement_pk"], box, destination)
        self.assertEqual(AuditLog.objects.count(), 1)
        self.assertFalse(AuditLog.objects.filter(metadata__operation=correction.OPERATION).exists())
        refreshed = self.plan()
        self.assertNotEqual(fingerprint(refreshed), fingerprint(plan))
        self.assertTrue(refreshed["ready"], refreshed["blockers"])
        self.assertTrue(refreshed["baseline"]["location_context"]["movements"])
        self.assertTrue(refreshed["baseline"]["location_context"]["locations"])

    def assert_committed_movement(self, movement_pk, box, destination):
        movement = BoxMovement.objects.get(pk=movement_pk)
        self.assertEqual(
            (movement.box_id, movement.from_thermal_zone_id, movement.to_thermal_zone_id,
             movement.user_id, movement.kind, movement.notes),
            (box.pk, self.zone.pk, destination.pk, self.admin.pk,
             BoxMovement.Kind.ORDINARY, "Concurrent location change"),
        )
        box.refresh_from_db()
        self.assertEqual(box.thermal_zone_id, destination.pk)
        location = BoxLocation.objects.get(pk=movement.destination_location_id)
        self.assertEqual((location.box_id, location.thermal_zone_id, location.starts_at, location.ends_at),
                         (box.pk, destination.pk, movement.moved_at, None))
        self.assertEqual(location.notes, movement.notes)
        self.assertEqual(BoxMovement.objects.count(), 1)
        self.assertEqual(BoxLocation.objects.count(), 1)
        audit = AuditLog.objects.get(metadata__movement_id=movement.pk)
        self.assertEqual((audit.user_id, audit.action), (self.admin.pk, AuditLog.Action.UPDATE))
        self.assertEqual(audit.metadata["destination_location_id"], location.pk)

    def test_correction_commits_first_then_waiting_movement_succeeds(self):
        box = self.boxes[5]
        destination = ThermalZone.objects.create(organization=self.org, name="Movement destination")
        plan = self.plan()
        first, second = self.ordered_location_race(
            lambda: self.apply(plan=plan), lambda: self.run_movement(box, destination),
        )
        self.assertEqual(first, plan)
        self.assertIsInstance(second, int)
        self.assert_committed_movement(second, box, destination)
        targets = {entry["measurement_pk"]: entry["target_box_pk"]
                   for entry in plan["entries"] if entry["classification"] == correction.MOVE}
        for before in plan["baseline"]["measurements"]:
            measurement = BiologicalMeasurement.objects.get(pk=before["id"])
            self.assertEqual(measurement.box_id, targets.get(measurement.pk, before["box_id"]))
            self.assertEqual(measurement.polyp_state_sequence, before["polyp_state_sequence"])
        for revision in plan["revision_changes"]:
            self.assertEqual(Box.objects.get(pk=revision["box_id"]).polyp_state_revision, revision["after"])
        self.assertEqual(AuditLog.objects.count(), 5)
        self.assertEqual(AuditLog.objects.filter(metadata__operation=correction.OPERATION).count(), 4)

    def test_correction_audit_rolls_back_then_waiting_movement_succeeds_on_original_state(self):
        box = self.boxes[5]
        destination = ThermalZone.objects.create(organization=self.org, name="Movement destination")
        plan = self.plan()
        measurements = list(BiologicalMeasurement.objects.order_by("pk").values())
        boxes = list(Box.objects.order_by("pk").values())
        first, second = self.ordered_location_race(
            lambda: self.apply(plan=plan), lambda: self.run_movement(box, destination), fail_first=True,
        )
        self.assertIsInstance(first, RuntimeError)
        self.assertIn("Synthetic location race audit rollback", str(first))
        self.assertIsInstance(second, int)
        self.assert_committed_movement(second, box, destination)
        self.assertEqual(list(BiologicalMeasurement.objects.order_by("pk").values()), measurements)
        expected_boxes = [{**row, "thermal_zone_id": destination.pk} if row["id"] == box.pk else row
                          for row in boxes]
        self.assertEqual(list(Box.objects.order_by("pk").values()), expected_boxes)
        self.assertEqual(AuditLog.objects.count(), 1)
        self.assertFalse(AuditLog.objects.filter(metadata__operation=correction.OPERATION).exists())

    def movement_metadata_commit_stales_ready_plan(self, *, change_date):
        box = self.boxes[5]
        origin = BoxLocation.objects.create(
            box=box, thermal_zone=self.zone,
            starts_at=datetime(2026, 9, 1, 12, tzinfo=timezone.utc),
        )
        destination = ThermalZone.objects.create(organization=self.org, name="Movement destination")
        movement_pk = self.run_movement(box, destination)
        movement = BoxMovement.objects.get(pk=movement_pk)
        self.assertEqual(movement.origin_location_id, origin.pk)
        plan = self.plan()
        # History is authorized, but a concurrent correction must still stale
        # the reviewed snapshot before any biological reassignment is written.
        self.assertTrue(plan["ready"], plan["blockers"])
        measurements = list(BiologicalMeasurement.objects.order_by("pk").values())
        boxes = list(Box.objects.order_by("pk").values())
        corrected_date = datetime(2026, 9, 9, 12, tzinfo=timezone.utc)
        data = {"expected_revision": movement.revision}
        data.update({"moved_at": corrected_date} if change_date else {"notes": "Reviewed movement note"})

        def correct_then_snapshot():
            movement_corrections.correct_box_movement(
                box_id=box.pk, movement_id=movement.pk, organization_ids=[self.org.pk],
                user=self.admin, data=data,
            )
            return {"database": self.database_snapshot()}

        first, second = self.ordered_location_race(correct_then_snapshot, lambda: self.apply(plan=plan))
        self.assertIsInstance(first, dict)
        self.assertIsInstance(second, ValueError)
        self.assertIn("Stale plan", str(second))
        self.assertNotIn("Unresolved conflicts", str(second))
        self.assertEqual(self.database_snapshot(), first["database"])
        self.assertEqual(list(BiologicalMeasurement.objects.order_by("pk").values()), measurements)
        self.assertEqual(list(Box.objects.order_by("pk").values()), boxes)
        old_revision, old_date, old_notes = movement.revision, movement.moved_at, movement.notes
        movement.refresh_from_db()
        origin.refresh_from_db()
        location = BoxLocation.objects.get(pk=movement.destination_location_id)
        self.assertEqual(movement.revision, old_revision + 1)
        self.assertEqual(movement.moved_at, corrected_date if change_date else old_date)
        self.assertEqual(movement.notes, old_notes if change_date else "Reviewed movement note")
        self.assertEqual(origin.ends_at, movement.moved_at)
        self.assertEqual(location.starts_at, movement.moved_at)
        self.assertEqual(location.notes, movement.notes)
        self.assertEqual(BoxMovement.objects.count(), 1)
        self.assertEqual(BoxLocation.objects.count(), 2)
        self.assertEqual(AuditLog.objects.count(), 2)
        self.assertEqual(AuditLog.objects.filter(metadata__event="box_movement_correction").count(), 1)
        self.assertFalse(AuditLog.objects.filter(metadata__operation=correction.OPERATION).exists())
        refreshed = self.plan()
        self.assertTrue(refreshed["ready"], refreshed["blockers"])
        self.assertNotEqual(fingerprint(refreshed), fingerprint(plan))
        self.assertNotEqual(refreshed["baseline"]["location_context"], plan["baseline"]["location_context"])

    def test_movement_notes_commit_first_then_waiting_ready_correction_is_stale(self):
        self.movement_metadata_commit_stales_ready_plan(change_date=False)

    def test_movement_date_commit_first_then_waiting_ready_correction_is_stale(self):
        self.movement_metadata_commit_stales_ready_plan(change_date=True)

    def run_subculture(self, parent, revision, *, zone=None):
        event, children = culture_services.create_subculture(
            parent_box=parent, user=self.admin, organization=self.org,
            expected_current_state_revision=revision,
            children=[{"thermal_zone": self.zone if zone is None else zone, "allocated_polyps": 0}],
        )
        return {"event_pk": event.pk, "child_pks": [child.pk for child in children]}

    def ordered_subculture_race(self, first_action, second_action, *, first_kind,
                               fail_first=False, expect_blocked=True):
        entered, attempted, release, finished = (threading.Event() for _ in range(4))
        local = threading.local()
        real_create = AuditLog.objects.create
        real_resolve = culture_services.resolve_current_polyp_state

        def pause_first():
            if not getattr(local, "is_first", False) or getattr(local, "paused", False):
                return
            local.paused = True
            entered.set()
            if not release.wait(timeout=10):
                raise TimeoutError("First correction/subculture operation was not released.")
            if fail_first:
                raise RuntimeError("Synthetic concurrent correction rollback")

        def blocking_audit(**kwargs):
            result = real_create(**kwargs)
            if first_kind == "correction" and kwargs.get("metadata", {}).get("operation") == correction.OPERATION:
                pause_first()
            return result

        def blocking_state(box):
            result = real_resolve(box)
            if first_kind == "subculture":
                # Pause before FK inserts, not after their locks have been taken.
                pause_first()
            return result

        def action(callback, is_first):
            local.is_first = is_first
            try:
                return callback()
            except (ValueError, RuntimeError, culture_services.SubcultureStateChanged) as error:
                return error

        with patch.object(AuditLog.objects, "create", side_effect=blocking_audit), patch.object(
            culture_services, "resolve_current_polyp_state", side_effect=blocking_state,
        ), ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.run_worker, lambda: action(first_action, True))
            try:
                if not entered.wait(timeout=10):
                    self.fail(f"First operation did not reach its pause: {first.result(timeout=1)!r}")
                second = pool.submit(
                    self.run_worker, lambda: action(second_action, False), lock_attempted=attempted,
                    lock_table="cultures_box", finished=finished,
                )
                self.assertTrue(attempted.wait(timeout=10), "Second operation did not attempt its Box lock.")
                if expect_blocked:
                    self.assertFalse(finished.wait(timeout=0.2), "Second operation bypassed the held Box lock.")
                else:
                    # A different parent must be able to commit its org/user FKs.
                    self.assertIsInstance(second.result(timeout=5), dict)
                    self.assertFalse(first.done())
            finally:
                release.set()
            return first.result(timeout=15), second.result(timeout=15)

    def test_subculture_commits_first_then_correction_is_stale_without_deadlock(self):
        parent = Box.objects.get(pk=self.boxes[5].pk)
        state = resolve_current_polyp_state(parent)
        plan = self.plan()
        measurements = list(BiologicalMeasurement.objects.order_by("pk").values())

        def subculture_then_snapshot():
            result = self.run_subculture(parent, state["revision"])
            result["database"] = self.database_snapshot()
            return result

        first, second = self.ordered_subculture_race(
            subculture_then_snapshot, lambda: self.apply(plan=plan), first_kind="subculture",
        )
        self.assertIsInstance(first, dict)
        self.assertIsInstance(second, ValueError)
        self.assertIn("Stale plan", str(second))
        self.assertEqual(self.database_snapshot(), first["database"])
        self.assertEqual(list(BiologicalMeasurement.objects.order_by("pk").values()), measurements)
        event = SubcultureEvent.objects.get(pk=first["event_pk"])
        self.assertEqual(event.parent_state_snapshot, state)
        self.assertEqual((event.allocated_polyp_count, event.parent_polyp_count_after), (0, state["polyp_count"]))
        self.assertEqual(event.user_id, self.admin.pk)
        self.assertEqual(SubcultureAllocation.objects.get(event=event).allocated_polyps, 0)
        self.assertEqual(AuditLog.objects.count(), 1)
        self.assertEqual(AuditLog.objects.get().action, AuditLog.Action.SUBCULTURE)
        refreshed = self.plan()
        self.assertNotEqual(fingerprint(refreshed), fingerprint(plan))
        self.assert_blocked(refreshed)

    def test_correction_commits_first_then_old_subculture_intent_is_rejected(self):
        parent = Box.objects.get(pk=self.boxes[5].pk)
        old_state = resolve_current_polyp_state(parent)
        plan = self.plan()
        first, second = self.ordered_subculture_race(
            lambda: self.apply(plan=plan), lambda: self.run_subculture(parent, old_state["revision"]),
            first_kind="correction",
        )
        self.assertEqual(first, plan)
        self.assertIsInstance(second, culture_services.SubcultureStateChanged)
        self.assertFalse(SubcultureEvent.objects.exists())
        self.assertFalse(SubcultureAllocation.objects.exists())
        self.assertEqual(AuditLog.objects.count(), 4)
        parent.refresh_from_db()
        new_state = resolve_current_polyp_state(parent)
        self.assertNotEqual(new_state["revision"], old_state["revision"])
        self.assertEqual(second.current_state, new_state)
        result = self.run_subculture(parent, new_state["revision"])
        event = SubcultureEvent.objects.get(pk=result["event_pk"])
        self.assertEqual(event.parent_state_snapshot, new_state)
        self.assertEqual(event.parent_polyp_count_after, new_state["polyp_count"])
        self.assertEqual(SubcultureAllocation.objects.get(event=event).allocated_polyps, 0)

    def test_correction_rolls_back_then_waiting_subculture_commits_original_state(self):
        parent = Box.objects.get(pk=self.boxes[5].pk)
        state = resolve_current_polyp_state(parent)
        plan = self.plan()
        measurements = list(BiologicalMeasurement.objects.order_by("pk").values())
        revisions = dict(Box.objects.values_list("pk", "polyp_state_revision"))
        first, second = self.ordered_subculture_race(
            lambda: self.apply(plan=plan), lambda: self.run_subculture(parent, state["revision"]),
            first_kind="correction", fail_first=True,
        )
        self.assertIsInstance(first, RuntimeError)
        self.assertIn("Synthetic concurrent correction rollback", str(first))
        self.assertIsInstance(second, dict)
        self.assertEqual(list(BiologicalMeasurement.objects.order_by("pk").values()), measurements)
        for pk, revision in revisions.items():
            self.assertEqual(Box.objects.get(pk=pk).polyp_state_revision, revision + (pk == parent.pk))
        event = SubcultureEvent.objects.get(pk=second["event_pk"])
        self.assertEqual(event.parent_state_snapshot, state)
        self.assertEqual(event.parent_polyp_count_after, state["polyp_count"])
        self.assertEqual(AuditLog.objects.count(), 1)
        self.assertEqual(AuditLog.objects.get().action, AuditLog.Action.SUBCULTURE)
        self.assertFalse(AuditLog.objects.filter(metadata__operation=correction.OPERATION).exists())

    def test_unrelated_subculture_same_actor_commits_while_correction_holds_locks(self):
        # The corrector now protects zone tenancy as well. Use an independent
        # zone to isolate the original organization/actor FK deadlock regression.
        zone = ThermalZone.objects.create(organization=self.org, name="Unrelated subculture zone")
        parent = Box.objects.create(
            organization=self.org, strain=self.strain, thermal_zone=self.zone,
            global_code="CCA-EOB-2.099", box_number="099",
        )
        BiologicalMeasurement.objects.create(
            box=parent, measured_on=date(2026, 9, 1), polyp_count=9, ephyrae_count=0,
        )
        parent.refresh_from_db()
        state = resolve_current_polyp_state(parent)
        plan = self.plan()
        first, second = self.ordered_subculture_race(
            lambda: self.apply(plan=plan), lambda: self.run_subculture(parent, state["revision"], zone=zone),
            first_kind="correction", expect_blocked=False,
        )
        self.assertEqual(first, plan)
        self.assertIsInstance(second, dict)
        event = SubcultureEvent.objects.get(pk=second["event_pk"])
        self.assertEqual(event.user_id, self.admin.pk)
        self.assertEqual(event.parent_state_snapshot, state)
        self.assertEqual(event.parent_polyp_count_after, 9)
        self.assertEqual(SubcultureAllocation.objects.get(event=event).allocated_polyps, 0)
        self.assertEqual(AuditLog.objects.filter(metadata__operation=correction.OPERATION).count(), 4)
        self.assertEqual(AuditLog.objects.filter(action=AuditLog.Action.SUBCULTURE).count(), 1)
