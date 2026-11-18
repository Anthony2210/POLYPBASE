"""PCA identity merge must never turn a Box with an ownership history into an alias.

The PCA suite itself only runs through the interactive PostgreSQL QA harness.
These tests reuse its exact synthetic fixture and call the shared merge core
(`_make_plan`, `_apply_reviewed_plan`) that both public entry points use:
`pca_identity_unification.apply_plan` (command `unify_pca_identity`) and
`pca_production.apply_plan` (command `pca_production`, with `verify_execution`).
Only the fixture's target sentinel is bypassed; no public entry point is called
on a non-harness database. The merge core uses PostgreSQL-specific SQL, so
every class here is skipped on SQLite; run on a freshly created, empty,
disposable cluster (for example `config.sharing_pgqa_settings`).
"""

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
import threading
from unittest import skipUnless
from unittest.mock import patch

from django.db import connection, connections, transaction
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.audit.models import AuditLog
from apps.measurements.models import BiologicalMeasurement

from . import pca_identity_unification as merge
from .box_ownership import enroll_origin_box
from .models import Box, BoxOwnershipEpisode, GlobalBoxIdentity
from .test_pca_identity_unification import PcaFixtureMixin

WAIT = 20
BLOCKER = f"Box {merge.DONOR_ID} has a global ownership history and cannot become a merged alias."


class OwnershipPcaMixin(PcaFixtureMixin):
    def setUp(self):
        # The fixture asserts the interactive QA harness; the merge core below
        # performs no target check of its own and is called directly.
        with patch.object(merge, "require_local_database"):
            super().setUp()

    def core_plan(self):
        return merge._make_plan(self.org)

    def core_apply(self, plan=None, verify_execution=None):
        plan = self.core_plan() if plan is None else plan
        return merge._apply_reviewed_plan(
            1, actor_id=self.admin.pk, expected_plan_hash=merge.fingerprint(plan),
            backup_ref="synthetic-qa-backup", review_ref="synthetic-independent-review",
            verify_execution=verify_execution,
        )

    def state(self):
        return {
            "boxes": list(Box.objects.order_by("pk").values()),
            "measurements": list(BiologicalMeasurement.objects.order_by("pk").values()),
            "episodes": list(BoxOwnershipEpisode.objects.order_by("pk").values()),
            "identities": list(GlobalBoxIdentity.objects.order_by("pk").values()),
            "audits": list(AuditLog.objects.order_by("pk").values()),
        }


@skipUnless(connection.vendor == "postgresql", "The PCA merge core requires PostgreSQL.")
class PcaMergeOwnershipGuardTests(OwnershipPcaMixin, TestCase):
    def assert_refused(self, message, plan=None, verify_execution=None):
        before = self.state()
        with self.assertRaisesMessage(ValueError, message):
            self.core_apply(plan, verify_execution=verify_execution)
        self.assertEqual(self.state(), before)

    def test_legacy_boxes_without_episode_still_merge(self):
        plan = self.core_plan()
        self.assertTrue(plan["ready"], plan["blockers"])
        self.core_apply(plan)
        donor = Box.objects.get(pk=merge.DONOR_ID)
        self.assertEqual((donor.merged_into_id, donor.global_code), (merge.CANONICAL_ID, merge.RETIRED_CODE))
        self.assertFalse(BoxOwnershipEpisode.objects.exists())

    def test_donor_with_current_episode_is_refused(self):
        episode = enroll_origin_box(organization=self.org, box=self.donor)
        plan = self.core_plan()
        self.assertFalse(plan["ready"])
        self.assertIn(BLOCKER, plan["blockers"])
        self.assert_refused("Unresolved conflicts block the entire identity merge.", plan)
        self.assert_refused("Unresolved conflicts block the entire identity merge.", plan,
                            verify_execution=lambda: None)  # pca_production path
        self.assertIsNone(Box.objects.get(pk=merge.DONOR_ID).merged_into_id)
        self.assertEqual(BoxOwnershipEpisode.objects.get().pk, episode.pk)

    def test_donor_with_historical_episode_is_refused(self):
        origin = enroll_origin_box(organization=self.org, box=self.donor)
        at = timezone.now()
        origin.ended_at = at
        origin.save(update_fields=["ended_at"])
        successor = BoxOwnershipEpisode.objects.create(
            identity=origin.identity, sequence=2, organization=self.other_org, local_box=self.other_box,
            started_at=at, arrival_transfer_id="00000000-0000-4000-8000-000000000001",
            arrival_item_id="00000000-0000-4000-8000-000000000002",
        )
        plan = self.core_plan()
        self.assertIn(BLOCKER, plan["blockers"])
        before_episodes = list(BoxOwnershipEpisode.objects.order_by("pk").values())
        self.assert_refused("Unresolved conflicts block the entire identity merge.", plan)
        self.assertEqual(list(BoxOwnershipEpisode.objects.order_by("pk").values()), before_episodes)
        self.assertEqual(BoxOwnershipEpisode.objects.get(ended_at__isnull=True).pk, successor.pk)

    def test_enrollment_after_review_invalidates_the_reviewed_plan(self):
        reviewed = self.core_plan()
        self.assertTrue(reviewed["ready"], reviewed["blockers"])
        enroll_origin_box(organization=self.org, box=self.donor)
        self.assert_refused("Stale plan", reviewed)
        self.assertIsNone(Box.objects.get(pk=merge.DONOR_ID).merged_into_id)

    def test_enrolled_canonical_is_blocked_by_dependency_inventory(self):
        enroll_origin_box(organization=self.org, box=self.canonical)
        plan = self.core_plan()
        self.assertFalse(plan["ready"])
        self.assertIn(
            "Unexpected Box or measurement dependency: cultures.boxownershipepisode.local_box_id.",
            plan["blockers"],
        )
        self.assertNotIn(BLOCKER, plan["blockers"])
        self.assert_refused("Unresolved conflicts block the entire identity merge.", plan)

    def test_merged_alias_cannot_be_enrolled_afterwards(self):
        self.core_apply()
        with self.assertRaises(ValidationError):
            enroll_origin_box(organization=self.org, box=Box.objects.get(pk=merge.DONOR_ID))
        self.assertFalse(GlobalBoxIdentity.objects.exists())


def _worker(function):
    def run():
        try:
            return ("ok", function())
        except (ValueError, ValidationError) as error:
            return ("refused", str(error))
        finally:
            connections.close_all()
    return run


@skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL row locks; run on a disposable cluster.")
class PcaMergeOwnershipConcurrencyTests(OwnershipPcaMixin, TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        super().setUp()
        self.pool = ThreadPoolExecutor(max_workers=2)
        self.addCleanup(self.pool.shutdown, wait=True)

    def test_enrollment_holding_the_box_lock_makes_the_waiting_merge_refuse(self):
        plan = self.core_plan()
        self.assertTrue(plan["ready"], plan["blockers"])
        holding, release = threading.Event(), threading.Event()

        def enroll():
            with transaction.atomic():
                episode = enroll_origin_box(organization=self.org, box=self.donor)
                holding.set()
                assert release.wait(WAIT)
                return episode.pk

        first = self.pool.submit(_worker(enroll))
        self.assertTrue(holding.wait(WAIT))
        second = self.pool.submit(_worker(lambda: self.core_apply(plan)))
        with self.assertRaises(FutureTimeout):
            second.result(timeout=1)  # waits on the donor Box row lock
        release.set()
        self.assertEqual(first.result(WAIT)[0], "ok")
        outcome, message = second.result(WAIT)
        self.assertEqual(outcome, "refused")
        self.assertIn("Stale plan", message)
        self.assertIsNone(Box.objects.get(pk=merge.DONOR_ID).merged_into_id)
        self.assertEqual(BoxOwnershipEpisode.objects.get().local_box_id, merge.DONOR_ID)

    def test_merge_holding_the_box_lock_makes_the_waiting_enrollment_refuse(self):
        plan = self.core_plan()
        holding, release, calls = threading.Event(), threading.Event(), []

        def verify_execution():
            calls.append(1)
            if len(calls) == 2:  # second call: Box rows locked, plan rechecked
                holding.set()
                assert release.wait(WAIT)

        first = self.pool.submit(_worker(lambda: self.core_apply(plan, verify_execution=verify_execution)))
        self.assertTrue(holding.wait(WAIT))
        second = self.pool.submit(_worker(lambda: enroll_origin_box(organization=self.org, box=self.donor)))
        with self.assertRaises(FutureTimeout):
            second.result(timeout=1)
        release.set()
        self.assertEqual(first.result(WAIT)[0], "ok")
        self.assertEqual(second.result(WAIT)[0], "refused")
        self.assertEqual(Box.objects.get(pk=merge.DONOR_ID).merged_into_id, merge.CANONICAL_ID)
        self.assertFalse(BoxOwnershipEpisode.objects.exists())
        self.assertFalse(GlobalBoxIdentity.objects.exists())
