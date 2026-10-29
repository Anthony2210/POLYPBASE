"""PostgreSQL-only durable intent races with observed, ordered lock waits."""

import threading
import uuid
from unittest import skipUnless

from django.db import connection, transaction
from django.test import TransactionTestCase
from rest_framework.exceptions import PermissionDenied

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.organizations.models import Organization
from apps.taxonomy.models import Strain

from . import automatic_box_creation as service
from . import test_automatic_box_concurrency as legacy_concurrency
from .models import AutomaticBoxCreationRequest, Box, BoxCodeNamespace, BoxLocation
from .test_automatic_box_intents import AutomaticBoxIntentFixture


@skipUnless(connection.vendor == "postgresql", "Requires disposable PostgreSQL and real row locks.")
class AutomaticBoxIntentConcurrencyTests(AutomaticBoxIntentFixture, TransactionTestCase):
    ordered = legacy_concurrency.AutomaticBoxConcurrencyTests.ordered

    def setUp(self):
        super().setUp()
        self.local = threading.local()

    def held(self, operation, barrier, *, fail=False):
        with transaction.atomic():
            receipt = operation()
            barrier()
            if fail:
                raise RuntimeError("Injected intent transaction rollback")
            return receipt

    def inspect_pending(self):
        return service.inspect_automatic_box_request(
            organization=self.org, actor=self.user, data={"request_id": self.payload["request_id"]},
        )

    def assert_single_creation(self, receipt):
        self.assertIsInstance(receipt, AutomaticBoxCreationRequest)
        receipt.refresh_from_db()
        self.assertIsNotNone(receipt.box_id)
        self.assertFalse(receipt.cancelled)
        self.assertIsNone(receipt.failure)
        self.assertEqual((AutomaticBoxCreationRequest.objects.count(), Box.objects.count(),
                          BoxLocation.objects.count(), AuditLog.objects.count()), (1, 1, 1, 1))
        self.assertEqual(receipt.box.global_code, "AUT-LAB-1.001")
        location = BoxLocation.objects.get(box_id=receipt.box_id)
        self.assertEqual(location.thermal_zone_id, self.zone.pk)
        self.assertIsNone(location.ends_at)
        audit = AuditLog.objects.get()
        self.assertEqual((audit.organization_id, audit.user_id, audit.metadata["box_id"]),
                         (self.org.pk, self.user.pk, receipt.box_id))
        self.assertEqual(BoxCodeNamespace.objects.get().high_water, 1)

    def test_concurrent_duplicate_registration_returns_one_unchanged_pending_receipt(self):
        first, second = self.ordered(
            lambda barrier: self.held(self.register, barrier), self.register, audit_barrier=False,
        )
        self.assert_pending(first)
        self.assert_pending(second)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(first.created_at, second.created_at)
        self.assertEqual(first.intent, second.intent)
        self.assertEqual(AutomaticBoxCreationRequest.objects.count(), 1)
        self.assert_no_creation()

    def test_execution_waits_for_held_registration_then_creates_its_box(self):
        pending, committed = self.ordered(
            lambda barrier: self.held(self.register, barrier), self.execute, audit_barrier=False,
        )
        self.assertEqual(pending.pk, committed.pk)
        self.assertEqual(pending.intent, committed.intent)
        self.assertEqual(pending.resume_token_hash, committed.resume_token_hash)
        self.assert_single_creation(committed)

    def test_same_key_different_payload_or_token_conflicts_after_registration_commits(self):
        for change in ({"notes": "different intent"}, {"resume_token": str(uuid.uuid4())}):
            with self.subTest(change=change):
                self.payload.update(request_id=str(uuid.uuid4()), resume_token=str(uuid.uuid4()))
                before = self.snapshot()
                pending, conflict = self.ordered(
                    lambda barrier: self.held(self.register, barrier),
                    lambda: self.register({**self.payload, **change}), audit_barrier=False,
                )
                self.assertIsInstance(conflict, service.AutomaticBoxCreationConflict)
                self.assert_pending(pending)
                self.assertEqual(AutomaticBoxCreationRequest.objects.count(),
                                 len(before["AutomaticBoxCreationRequest"]) + 1)
                self.assertEqual(pending.intent["notes"], "")
                after = self.snapshot()
                self.assertEqual(after["AutomaticBoxCreationRequest"][:-1], before["AutomaticBoxCreationRequest"])
                self.assert_no_creation()

    def test_concurrent_duplicate_execution_creates_and_allocates_only_once(self):
        pending = self.register()
        first, second = self.ordered(self.execute, self.execute)
        self.assertEqual((first.pk, second.pk), (pending.pk, pending.pk))
        self.assertEqual(first.box_id, second.box_id)
        self.assert_single_creation(second)

    def test_execution_commits_before_waiting_cancellation_returns_committed_receipt(self):
        pending = self.register()
        committed, cancelled = self.ordered(self.execute, self.cancel)
        self.assertEqual((committed.pk, cancelled.pk), (pending.pk, pending.pk))
        self.assertEqual(committed.box_id, cancelled.box_id)
        self.assertEqual(cancelled.intent, pending.intent)
        self.assert_single_creation(cancelled)
        before = self.snapshot()
        self.assertEqual(self.cancel().box_id, committed.box_id)
        self.assertEqual(self.snapshot(), before)

    def test_cancellation_commits_before_delayed_execution_without_creating_box(self):
        pending = self.register()
        cancelled, delayed = self.ordered(
            lambda barrier: self.held(self.cancel, barrier), self.execute, audit_barrier=False,
        )
        for receipt in (cancelled, delayed):
            self.assertIsInstance(receipt, AutomaticBoxCreationRequest)
            receipt.refresh_from_db()
            self.assertEqual(receipt.pk, pending.pk)
            self.assertTrue(receipt.cancelled)
            self.assertIsNone(receipt.box_id)
            self.assertIsNone(receipt.failure)
            self.assertEqual(receipt.intent, pending.intent)
            self.assertEqual(receipt.resume_token_hash, pending.resume_token_hash)
        self.assertEqual(AutomaticBoxCreationRequest.objects.count(), 1)
        self.assert_no_creation()

    def test_registration_rollback_allows_waiting_retry_with_original_key_and_token(self):
        failed, retry = self.ordered(
            lambda barrier: self.held(self.register, barrier, fail=True), self.register, audit_barrier=False,
        )
        self.assertIsInstance(failed, RuntimeError)
        self.assert_pending(retry)
        self.assertEqual(AutomaticBoxCreationRequest.objects.count(), 1)
        self.assert_no_creation()
        self.assert_single_creation(self.execute())

    def test_execution_rollback_leaves_committed_registration_pending_and_retry_succeeds(self):
        pending = self.register()
        before = self.snapshot()
        failed, observed = self.ordered(self.execute, self.inspect_pending, fail_first=True)
        self.assertIsInstance(failed, RuntimeError)
        self.assertEqual(observed, ("pending", None))
        self.assert_pending(pending)
        self.assertEqual(self.snapshot(), before)
        self.assert_no_creation()
        self.assert_single_creation(self.execute())

    def test_cancellation_rollback_allows_waiting_execution_of_original_pending_intent(self):
        pending = self.register()
        failed, committed = self.ordered(
            lambda barrier: self.held(self.cancel, barrier, fail=True), self.execute, audit_barrier=False,
        )
        self.assertIsInstance(failed, RuntimeError)
        self.assertEqual(committed.pk, pending.pk)
        self.assertEqual(committed.intent, pending.intent)
        self.assert_single_creation(committed)

    def test_independent_requests_same_actor_and_organization_keep_distinct_strain_intents(self):
        strain = Strain.objects.create(organization=self.org, species=self.species, code="AUT-LAB-2")
        other = {**self.payload, "request_id": str(uuid.uuid4()), "resume_token": str(uuid.uuid4()),
                 "strain": strain.pk, "notes": "Independent intent"}
        first, second = self.ordered(
            lambda barrier: self.held(self.register, barrier),
            lambda: self.register(other), audit_barrier=False,
        )
        self.assertNotEqual(first.pk, second.pk)
        original = {receipt.pk: (receipt.intent, receipt.resume_token_hash, receipt.request_id,
                                receipt.actor_id, receipt.organization_id) for receipt in (first, second)}
        self.assert_no_creation()
        first_result, second_result = self.ordered(self.execute, lambda: self.execute(self.key(other)))
        self.assertEqual((first_result.pk, second_result.pk), (first.pk, second.pk))
        for receipt, expected_strain in ((first_result, self.strain), (second_result, strain)):
            receipt.refresh_from_db()
            self.assertEqual((receipt.intent, receipt.resume_token_hash, receipt.request_id,
                              receipt.actor_id, receipt.organization_id), original[receipt.pk])
            self.assertFalse(receipt.cancelled)
            self.assertIsNone(receipt.failure)
            self.assertEqual(receipt.box.strain_id, expected_strain.pk)
            self.assertEqual(BoxLocation.objects.get(box_id=receipt.box_id).thermal_zone_id, self.zone.pk)
        self.assertEqual(set(Box.objects.values_list("global_code", flat=True)), {"AUT-LAB-1.001", "AUT-LAB-2.001"})
        self.assertEqual((Box.objects.count(), BoxLocation.objects.count(), AuditLog.objects.count(),
                          AutomaticBoxCreationRequest.objects.count()), (2, 2, 2, 2))
        self.assertEqual(list(BoxCodeNamespace.objects.order_by("pk").values_list("high_water", flat=True)), [1, 1])
        before = self.snapshot()
        self.assertEqual(self.execute().box_id, first_result.box_id)
        self.assertEqual(self.execute(self.key(other)).box_id, second_result.box_id)
        self.assertEqual(self.snapshot(), before)

    def test_recovery_of_a_cannot_tombstone_delayed_independent_b(self):
        strain = Strain.objects.create(organization=self.org, species=self.species, code="AUT-LAB-2")
        other = {**self.payload, "request_id": str(uuid.uuid4()), "resume_token": str(uuid.uuid4()), "strain": strain.pk}

        def recover_a(barrier):
            with transaction.atomic():
                a = self.register()
                self.assertEqual(service.inspect_automatic_box_request(
                    organization=self.org, actor=self.user, data={"request_id": other["request_id"]},
                ), ("unknown", None))
                with self.assertRaises(service.AutomaticBoxCreationConflict):
                    self.cancel({"request_id": other["request_id"], "resume_token": self.payload["resume_token"]})
                barrier()
                return a

        a, b = self.ordered(recover_a, lambda: self.register(other), audit_barrier=False)
        self.assert_pending(a)
        self.assertFalse(b.cancelled)
        self.assertEqual(self.execute(self.key(other)).box.strain_id, strain.pk)
        self.assertFalse(AutomaticBoxCreationRequest.objects.filter(cancelled=True).exists())
        self.assertEqual(Box.objects.count(), 1)

    def test_independent_requests_same_strain_allocate_distinct_consecutive_numbers(self):
        self.register()
        other = {**self.payload, "request_id": str(uuid.uuid4()), "resume_token": str(uuid.uuid4())}
        self.register(other)
        first, second = self.ordered(self.execute, lambda: self.execute(self.key(other)))
        self.assertNotEqual(first.box_id, second.box_id)
        self.assertEqual((first.box.global_code, second.box.global_code), ("AUT-LAB-1.001", "AUT-LAB-1.002"))
        self.assertEqual((first.box.box_number, second.box.box_number), ("001", "002"))
        self.assertFalse(AutomaticBoxCreationRequest.objects.filter(cancelled=True).exists())
        self.assertEqual((Box.objects.count(), BoxLocation.objects.count(), AuditLog.objects.count()), (2, 2, 2))
        self.assertEqual(BoxCodeNamespace.objects.get().high_water, 2)

    def test_permission_revocation_serializes_before_waiting_register_execute_and_cancel(self):
        pending = self.register()

        def revoke(barrier):
            with transaction.atomic():
                Organization.objects.select_for_update().get(pk=self.org.pk)
                membership = OrganizationMembership.objects.select_for_update().get(pk=self.membership.pk)
                membership.role = "viewer"
                membership.save(update_fields=["role"])
                barrier()

        for operation in (self.register, self.execute, self.cancel):
            with self.subTest(operation=operation.__name__):
                OrganizationMembership.objects.filter(pk=self.membership.pk).update(role="lab_technician")
                before = self.snapshot()
                _, denied = self.ordered(revoke, operation, audit_barrier=False)
                self.assertIsInstance(denied, PermissionDenied)
                self.assertEqual(self.snapshot(), before)
                self.assert_pending(pending)
                self.assert_no_creation()
