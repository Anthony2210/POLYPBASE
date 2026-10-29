"""PostgreSQL-only races using independent connections and observed lock waits."""

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest import skipUnless
from unittest.mock import patch

from django.db import close_old_connections, connection, connections, transaction
from django.test import TransactionTestCase
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.measurements.models import BiologicalMeasurement
from apps.organizations.models import Organization
from apps.taxonomy.models import Strain

from .automatic_box_creation import (
    AutomaticBoxCreationCancelled, AutomaticBoxCreationConflict, inspect_automatic_box_request,
    execute_automatic_box_intent, register_automatic_box_intent, resolve_automatic_box_request,
)
from .models import AutomaticBoxCreationRequest, Box, BoxCodeNamespace, ThermalZone
from .polyp_state import resolve_current_polyp_state
from .services import create_subculture
from .test_automatic_box_creation import AutomaticBoxFixture


@skipUnless(connection.vendor == "postgresql", "Requires disposable PostgreSQL and real row locks.")
class AutomaticBoxConcurrencyTests(AutomaticBoxFixture, TransactionTestCase):
    def setUp(self):
        super().setUp()
        self.local = threading.local()
        self.child_zone = ThermalZone.objects.create(organization=self.org, name="Subculture child zone")
        self.parent = Box.objects.create(
            organization=self.org, strain=self.strain, thermal_zone=self.zone,
            global_code=f"{self.strain.code}.010", box_number="010",
        )
        BiologicalMeasurement.objects.create(
            box=self.parent, measured_on=timezone.localdate(), polyp_count=10, user=self.user,
        )
        self.parent.refresh_from_db()
        self.revision = resolve_current_polyp_state(self.parent)["revision"]

    def subculture(self):
        return create_subculture(
            parent_box=self.parent, organization=self.org, user=self.user,
            expected_current_state_revision=self.revision,
            children=[{"thermal_zone": self.child_zone, "allocated_polyps": 0}],
        )

    def ordered(self, first, second, *, fail_first=False, audit_barrier=True):
        """Hold the first transaction, observe PostgreSQL blocking, then release it."""
        entered, release, attempted = threading.Event(), threading.Event(), threading.Event()
        pids = {}
        original = AuditLog.objects.create

        def barrier():
            entered.set()
            if not release.wait(15):
                raise TimeoutError("First writer was not released")

        def audit(**kwargs):
            result = original(**kwargs)
            if getattr(self.local, "first", False) and audit_barrier:
                barrier()
                if fail_first:
                    raise RuntimeError("Injected transaction rollback")
            return result

        def run(action, is_first):
            close_old_connections()
            self.local.first = is_first
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    pids[is_first] = cursor.fetchone()[0]
                if not is_first:
                    attempted.set()
                return action() if audit_barrier or not is_first else action(barrier)
            except (RuntimeError, PermissionDenied, ValidationError, AutomaticBoxCreationConflict) as error:
                return error
            finally:
                connections.close_all()

        with patch.object(AuditLog.objects, "create", audit), ThreadPoolExecutor(max_workers=2) as pool:
            one = pool.submit(run, first, True)
            try:
                self.assertTrue(entered.wait(15), "First writer did not reach barrier")
                two = pool.submit(run, second, False)
                self.assertTrue(attempted.wait(15))
                deadline = time.monotonic() + 10
                blocked = False
                while time.monotonic() < deadline:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT %s = ANY(pg_blocking_pids(%s))", [pids[True], pids[False]])
                        blocked = cursor.fetchone()[0]
                    if blocked or two.done():
                        break
                    time.sleep(0.02)
                self.assertTrue(blocked, "Second writer did not block on first transaction")
                self.assertFalse(two.done())
            finally:
                release.set()
            return one.result(timeout=20), two.result(timeout=20)

    def test_same_key_concurrent_first_use_creates_once_then_replays(self):
        BoxCodeNamespace.objects.all().delete()
        first, second = self.ordered(self.service, self.service)
        self.assertEqual((first[1], second[1]), (True, False))
        self.assertEqual(first[0].pk, second[0].pk)
        self.assertEqual(AutomaticBoxCreationRequest.objects.count(), 1)
        self.assertEqual(AuditLog.objects.count(), 1)
        self.assertEqual(BoxCodeNamespace.objects.get().high_water, 11)

    def test_same_key_different_intent_conflicts_after_wait(self):
        first, second = self.ordered(self.service, lambda: self.service({**self.payload, "notes": "different"}))
        self.assertTrue(first[1])
        self.assertIsInstance(second, AutomaticBoxCreationConflict)
        self.assertEqual(AutomaticBoxCreationRequest.objects.count(), 1)

    def test_different_keys_create_distinct_consecutive_boxes(self):
        first, second = self.ordered(self.service, lambda: self.service({**self.payload, "request_id": str(uuid.uuid4())}))
        self.assertEqual([first[0].global_code, second[0].global_code], ["AUT-LAB-1.011", "AUT-LAB-1.012"])
        self.assertEqual(AutomaticBoxCreationRequest.objects.count(), 2)

    def test_rollback_releases_same_key_and_first_use_namespace(self):
        BoxCodeNamespace.objects.all().delete()
        first, second = self.ordered(self.service, self.service, fail_first=True)
        self.assertIsInstance(first, RuntimeError)
        self.assertEqual(second[0].global_code, "AUT-LAB-1.011")
        self.assertTrue(second[1])
        self.assertEqual(AuditLog.objects.count(), 1)
        self.assertEqual(AutomaticBoxCreationRequest.objects.count(), 1)

    def registered_execution(self):
        data = {**self.payload, "resume_token": str(uuid.uuid4())}
        register_automatic_box_intent(organization=self.org, actor=self.user, data=data)
        return lambda: execute_automatic_box_intent(
            organization=self.org, actor=self.user,
            data={key: data[key] for key in ("request_id", "resume_token")},
        )

    def test_registered_execution_then_subculture_shares_namespace(self):
        first, second = self.ordered(self.registered_execution(), self.subculture)
        self.assertEqual(first.box.global_code, "AUT-LAB-1.011")
        self.assertEqual(second[1][0].global_code, "AUT-LAB-1.012")
        self.assertEqual(BoxCodeNamespace.objects.get().high_water, 12)

    def test_subculture_then_registered_execution_shares_namespace(self):
        first, second = self.ordered(self.subculture, self.registered_execution())
        self.assertEqual(first[1][0].global_code, "AUT-LAB-1.011")
        self.assertEqual(second.box.global_code, "AUT-LAB-1.012")
        self.assertEqual(BoxCodeNamespace.objects.get().high_water, 12)

    def test_ordinary_then_subculture_shares_namespace_without_deadlock(self):
        first, second = self.ordered(self.service, self.subculture)
        self.assertEqual(first[0].global_code, "AUT-LAB-1.011")
        self.assertEqual(second[1][0].global_code, "AUT-LAB-1.012")
        self.assertEqual(BoxCodeNamespace.objects.get().high_water, 12)

    def test_subculture_then_ordinary_shares_namespace_without_deadlock(self):
        first, second = self.ordered(self.subculture, self.service)
        self.assertEqual(first[1][0].global_code, "AUT-LAB-1.011")
        self.assertEqual(second[0].global_code, "AUT-LAB-1.012")

    def test_subculture_rollback_then_ordinary_reuses_suffix(self):
        first, second = self.ordered(self.subculture, self.service, fail_first=True)
        self.assertIsInstance(first, RuntimeError)
        self.assertEqual(second[0].global_code, "AUT-LAB-1.011")
        self.assertEqual(BoxCodeNamespace.objects.get().high_water, 11)

    def test_ordinary_rollback_then_subculture_reuses_suffix(self):
        first, second = self.ordered(self.service, self.subculture, fail_first=True)
        self.assertIsInstance(first, RuntimeError)
        self.assertEqual(second[1][0].global_code, "AUT-LAB-1.011")
        self.assertFalse(AutomaticBoxCreationRequest.objects.exists())

    def resolve(self):
        return resolve_automatic_box_request(
            organization=self.org, actor=self.user, data={"request_id": self.payload["request_id"]},
        )

    def held_resolution(self, barrier, *, fail=False):
        with transaction.atomic():
            result = self.resolve()
            barrier()
            if fail:
                raise RuntimeError("Injected resolution rollback")
            return result

    def inspect(self, request_id):
        return inspect_automatic_box_request(
            organization=self.org, actor=self.user, data={"request_id": request_id},
        )

    def test_recovery_of_a_cannot_cancel_delayed_independent_b(self):
        other_strain = Strain.objects.create(organization=self.org, species=self.species, code="AUT-LAB-2")
        b = {**self.payload, "request_id": str(uuid.uuid4()), "strain": other_strain.pk}

        def recovering(barrier):
            with transaction.atomic():
                # Corrupted storage supplied A and independent B: neither is trusted.
                self.assertEqual(self.inspect(self.payload["request_id"]), ("unknown", None))
                self.assertEqual(self.inspect(b["request_id"]), ("unknown", None))
                barrier()  # B's legitimate creation waits on this institution lock.

        _, created = self.ordered(recovering, lambda: self.service(b), audit_barrier=False)
        self.assertTrue(created[1])
        self.assertEqual(created[0].strain_id, other_strain.pk)
        receipt_b = AutomaticBoxCreationRequest.objects.get(request_id=b["request_id"])
        self.assertFalse(receipt_b.cancelled)
        self.assertEqual(receipt_b.box_id, created[0].pk)
        self.assertFalse(AutomaticBoxCreationRequest.objects.filter(request_id=self.payload["request_id"]).exists())
        self.assertFalse(self.service(b)[1])
        self.assertEqual(AuditLog.objects.count(), 1)

    def test_inspection_waits_for_independent_inflight_creation_without_cancelling(self):
        a = str(uuid.uuid4())

        def recovery():
            self.assertEqual(self.inspect(a), ("unknown", None))
            return self.inspect(self.payload["request_id"])

        created, observed = self.ordered(self.service, recovery)
        self.assertEqual(observed[0], "created")
        self.assertEqual(observed[1].pk, created[0].pk)
        self.assertFalse(AutomaticBoxCreationRequest.objects.filter(request_id=a).exists())
        self.assertFalse(AutomaticBoxCreationRequest.objects.filter(cancelled=True).exists())

    def test_create_commits_then_resolution_returns_created_box(self):
        first, second = self.ordered(self.service, self.resolve)
        self.assertEqual(first[0].pk, second.pk)
        self.assertEqual(AutomaticBoxCreationRequest.objects.count(), 1)
        self.assertFalse(AutomaticBoxCreationRequest.objects.get().cancelled)
        self.assertEqual(AuditLog.objects.count(), 1)

    def test_create_rolls_back_then_resolution_persists_cancellation(self):
        first, second = self.ordered(self.service, self.resolve, fail_first=True)
        self.assertIsInstance(first, RuntimeError)
        self.assertIsNone(second)
        self.assertTrue(AutomaticBoxCreationRequest.objects.get().cancelled)
        self.assertEqual(Box.objects.count(), 1)  # Only the fixture parent.
        self.assertEqual(BoxCodeNamespace.objects.get().high_water, 10)
        self.assertFalse(AuditLog.objects.exists())
        with self.assertRaises(AutomaticBoxCreationCancelled):
            self.service()

    def test_resolution_wins_and_late_create_cannot_write(self):
        first, second = self.ordered(self.held_resolution, self.service, audit_barrier=False)
        self.assertIsNone(first)
        self.assertIsInstance(second, AutomaticBoxCreationCancelled)
        self.assertTrue(AutomaticBoxCreationRequest.objects.get().cancelled)
        self.assertEqual(Box.objects.count(), 1)
        self.assertEqual(BoxCodeNamespace.objects.get().high_water, 10)

    def test_resolution_wins_against_different_invalid_intent(self):
        first, second = self.ordered(
            self.held_resolution, lambda: self.service({**self.payload, "strain": 999999, "notes": "edited"}),
            audit_barrier=False,
        )
        self.assertIsNone(first)
        self.assertIsInstance(second, AutomaticBoxCreationCancelled)
        self.assertEqual(AutomaticBoxCreationRequest.objects.count(), 1)

    def test_concurrent_resolutions_share_one_durable_tombstone(self):
        first, second = self.ordered(self.held_resolution, self.resolve, audit_barrier=False)
        self.assertIsNone(first)
        self.assertIsNone(second)
        self.assertEqual(AutomaticBoxCreationRequest.objects.count(), 1)

    def test_resolution_rollback_allows_waiting_creation(self):
        first, second = self.ordered(
            lambda barrier: self.held_resolution(barrier, fail=True), self.service, audit_barrier=False,
        )
        self.assertIsInstance(first, RuntimeError)
        self.assertTrue(second[1])
        self.assertEqual(second[0].global_code, "AUT-LAB-1.011")
        self.assertFalse(AutomaticBoxCreationRequest.objects.get().cancelled)

    def test_waiting_resolution_rechecks_membership_before_returning_committed_box(self):
        result = self.state_change(
            OrganizationMembership, self.membership.pk, {"role": "viewer"}, replay=True, operation=self.resolve,
        )
        self.assertIsInstance(result, PermissionDenied)
        self.assertFalse(AutomaticBoxCreationRequest.objects.get().cancelled)

    def test_waiting_resolution_rechecks_institution_before_cancelling_absent_key(self):
        result = self.state_change(Organization, self.org.pk, {"is_active": False}, operation=self.resolve)
        self.assertIsInstance(result, PermissionDenied)
        self.assertFalse(AutomaticBoxCreationRequest.objects.exists())

    def test_waiting_resolution_rechecks_actor_before_cancelling_absent_key(self):
        result = self.state_change(type(self.user), self.user.pk, {"is_active": False}, operation=self.resolve)
        self.assertIsInstance(result, PermissionDenied)
        self.assertFalse(AutomaticBoxCreationRequest.objects.exists())

    def state_change(self, model, pk, changes, *, replay=False, operation=None):
        if replay:
            self.service()

        def mutate(barrier):
            with transaction.atomic():
                # Permission administration locks the institution before membership/user.
                if model in (OrganizationMembership, type(self.user)):
                    Organization.objects.select_for_update().get(pk=self.org.pk)
                row = model.objects.select_for_update().get(pk=pk)
                for key, value in changes.items():
                    setattr(row, key, value)
                row.save(update_fields=list(changes))
                barrier()
        return self.ordered(mutate, operation or self.service, audit_barrier=False)[1]

    def test_waiting_creation_rechecks_committed_membership_role(self):
        result = self.state_change(OrganizationMembership, self.membership.pk, {"role": "viewer"})
        self.assertIsInstance(result, PermissionDenied)
        self.assertFalse(AutomaticBoxCreationRequest.objects.exists())

    def test_waiting_replay_rechecks_committed_membership_activity(self):
        result = self.state_change(OrganizationMembership, self.membership.pk, {"is_active": False}, replay=True)
        self.assertIsInstance(result, PermissionDenied)
        self.assertEqual(AutomaticBoxCreationRequest.objects.count(), 1)

    def test_waiting_replay_rechecks_committed_institution_activity(self):
        result = self.state_change(Organization, self.org.pk, {"is_active": False}, replay=True)
        self.assertIsInstance(result, PermissionDenied)

    def test_waiting_replay_rechecks_committed_actor_activity(self):
        result = self.state_change(type(self.user), self.user.pk, {"is_active": False}, replay=True)
        self.assertIsInstance(result, PermissionDenied)

    def test_waiting_replay_rechecks_zone_activity(self):
        result = self.state_change(ThermalZone, self.zone.pk, {"is_active": False}, replay=True)
        self.assertIsInstance(result, ValidationError)

    def test_waiting_replay_rechecks_strain_ownership(self):
        result = self.state_change(Strain, self.strain.pk, {"organization": self.other_org}, replay=True)
        self.assertIsInstance(result, ValidationError)
