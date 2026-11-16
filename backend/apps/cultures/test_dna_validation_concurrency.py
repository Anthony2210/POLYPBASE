"""Run against a disposable PostgreSQL database, never a shared environment."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from queue import Queue
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, connections, transaction
from django.test import TransactionTestCase
from rest_framework.exceptions import APIException, PermissionDenied

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain

from .dna_validation import mutate_dna_validation
from .models import Box, BoxDnaValidation


@skipUnless(connection.vendor == "postgresql", "DNA concurrency requires PostgreSQL row locks.")
class DnaValidationConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name="DNA concurrency QA")
        self.user = get_user_model().objects.create_user(username="dna-concurrency", email="dna@example.org")
        OrganizationMembership.objects.create(
            user=self.user, organization=self.organization, role="lab_technician",
        )
        species = Species.objects.create(scientific_name="Aurelia concurrent DNA", genus_species_code="DNC")
        strain = Strain.objects.create(species=species, organization=self.organization, code="DNC-A-1")
        self.box = Box.objects.create(
            organization=self.organization, strain=strain, global_code="DNC-A-1.001", box_number="001",
        )

    def mutate(self, operation, data):
        return mutate_dna_validation(
            user=self.user, organization=self.organization, box_id=self.box.pk,
            operation=operation, data=data,
        )

    def race(self, intents):
        barrier = threading.Barrier(2, timeout=10)

        def worker(operation, data):
            close_old_connections()
            try:
                barrier.wait()
                try:
                    validation = self.mutate(operation, data)
                    return ("ok", validation.revision)
                except APIException as exc:
                    return (exc.status_code, str(exc.detail["code"]))
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(worker, operation, data) for operation, data in intents]
            return [future.result(timeout=30) for future in futures]

    def _dna_worker(self, pids):
        close_old_connections()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                pids.put(cursor.fetchone()[0])
            return self.mutate("add", {"evidence_url": "https://example.org/concurrent"})
        finally:
            connections.close_all()

    def _wait_for_blocked_query(self, pid, table):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_stat_clear_snapshot()")
                cursor.execute(
                    "SELECT pg_backend_pid() = ANY(pg_blocking_pids(pid)), query "
                    "FROM pg_stat_activity WHERE pid = %s",
                    [pid],
                )
                row = cursor.fetchone()
            if row and row[0] and table in row[1]:
                return
            time.sleep(0.01)
        self.fail(f"DNA worker {pid} did not block on {table} held by this transaction")

    def test_box_locked_audit_commits_while_dna_waits_for_box(self):
        # Reproduce the Box -> audit FK path without unrelated movement writes.
        # Observe the actual Box wait, not merely that the worker has started.
        pids = Queue()
        with ThreadPoolExecutor(max_workers=1) as executor:
            with transaction.atomic():
                Box.objects.select_for_update().get(pk=self.box.pk)
                future = executor.submit(self._dna_worker, pids)
                self._wait_for_blocked_query(pids.get(timeout=10), Box._meta.db_table)
                with connection.cursor() as cursor:
                    cursor.execute("SET LOCAL lock_timeout = '3s'")
                audit = AuditLog.objects.create(
                    organization=self.organization, user=self.user,
                    action=AuditLog.Action.UPDATE, object_type="box",
                    object_id=self.box.global_code, description="Concurrent Box-locked audit",
                )
                # Django PostgreSQL FKs are deferred: force their KEY SHARE
                # checks before releasing Box, otherwise the cycle can be missed.
                with connection.cursor() as cursor:
                    cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
            validation = future.result(timeout=15)
        self.assertEqual(validation.revision, 1)
        self.assertTrue(AuditLog.objects.filter(pk=audit.pk).exists())
        self.assertEqual(BoxDnaValidation.objects.count(), 1)
        self.assertEqual(AuditLog.objects.filter(metadata__event="box_dna_validation_added").count(), 1)
        self.assertEqual(AuditLog.objects.count(), 2)

    def test_membership_change_still_serializes_and_denies_waiting_dna(self):
        pids = Queue()
        with ThreadPoolExecutor(max_workers=1) as executor:
            with transaction.atomic():
                Organization.objects.select_for_update().get(pk=self.organization.pk)
                future = executor.submit(self._dna_worker, pids)
                self._wait_for_blocked_query(pids.get(timeout=10), Organization._meta.db_table)
                OrganizationMembership.objects.filter(
                    user=self.user, organization=self.organization,
                ).update(role=OrganizationMembership.Role.VIEWER)
            with self.assertRaises(PermissionDenied):
                future.result(timeout=15)
        self.assertFalse(BoxDnaValidation.objects.exists())
        self.assertFalse(AuditLog.objects.exists())

    def assert_race(self, results, code):
        self.assertEqual(sum(result[0] == "ok" for result in results), 1, results)
        self.assertIn((409, code), results)

    def test_concurrent_adds_commit_one_validation_and_one_audit(self):
        results = self.race([
            ("add", {"evidence_url": "https://example.org/first"}),
            ("add", {"evidence_url": "https://example.org/second"}),
        ])
        self.assert_race(results, "dna_validation_exists")
        self.assertEqual(BoxDnaValidation.objects.count(), 1)
        self.assertEqual(BoxDnaValidation.objects.get().revision, 1)
        self.assertEqual(AuditLog.objects.filter(metadata__event="box_dna_validation_added").count(), 1)

    def test_concurrent_updates_reject_stale_intent(self):
        self.mutate("add", {"evidence_url": "https://example.org/original"})
        results = self.race([
            ("update", {"evidence_url": "https://example.org/first", "expected_revision": 1}),
            ("update", {"evidence_url": "https://example.org/second", "expected_revision": 1}),
        ])
        self.assert_race(results, "dna_validation_changed")
        self.assertEqual(BoxDnaValidation.objects.get().revision, 2)
        self.assertEqual(AuditLog.objects.count(), 2)

    def test_concurrent_update_and_revoke_commit_only_one_intent(self):
        self.mutate("add", {"evidence_url": "https://example.org/original"})
        results = self.race([
            ("update", {"evidence_url": "https://example.org/updated", "expected_revision": 1}),
            ("revoke", {"expected_revision": 1}),
        ])
        self.assert_race(results, "dna_validation_changed")
        self.assertEqual(BoxDnaValidation.objects.get().revision, 2)
        self.assertEqual(AuditLog.objects.count(), 2)

    def test_concurrent_revokes_retain_one_row_and_one_revoke_audit(self):
        self.mutate("add", {"evidence_url": "https://example.org/original"})
        results = self.race([("revoke", {"expected_revision": 1}), ("revoke", {"expected_revision": 1})])
        self.assert_race(results, "dna_validation_changed")
        self.assertEqual(BoxDnaValidation.objects.count(), 1)
        self.assertIsNotNone(BoxDnaValidation.objects.get().revoked_at)
        self.assertEqual(AuditLog.objects.filter(metadata__event="box_dna_validation_revoked").count(), 1)

    def test_recreation_cannot_accept_old_update_or_revoke(self):
        self.mutate("add", {"evidence_url": "https://example.org/original"})
        self.mutate("revoke", {"expected_revision": 1})
        self.mutate("add", {"evidence_url": "https://example.org/replacement"})
        results = self.race([
            ("update", {"evidence_url": "https://example.org/stale", "expected_revision": 1}),
            ("revoke", {"expected_revision": 1}),
        ])
        self.assertEqual(results, [(409, "dna_validation_changed"), (409, "dna_validation_changed")])
        self.assertEqual(BoxDnaValidation.objects.filter(revoked_at__isnull=True).get().revision, 3)
        self.assertEqual(BoxDnaValidation.objects.count(), 2)
        self.assertEqual(AuditLog.objects.count(), 3)
