"""PostgreSQL evidence for the transactional claims of Box reader sharing.

Skipped on SQLite, which proves nothing about PostgreSQL snapshots or row
locks. Run only against a disposable local cluster (see
`config.test_settings` with POLYPBASE_TEST_POSTGRES=1), never a shared one.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
import json
import threading
from unittest import skipUnless
from unittest.mock import patch

from django.core import mail
from django.db import connection, connections
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.cultures import scientific_projection
from apps.measurements.models import BiologicalMeasurement

from . import services
from .models import BoxReaderShare, BoxReaderShareCapture
from .services import SharingError
from .sharing_testing import OBSERVATION_SELECTORS, SHARING_SETTINGS, build_world, last_token

WAIT = 20


def _in_thread(function):
    """Run in a worker with its own connection; return a value or a SharingError code."""
    def run():
        try:
            return ("ok", function())
        except SharingError as error:
            return ("refused", error.code)
        finally:
            connections.close_all()
    return run


@skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL snapshots and row locks.")
@override_settings(**SHARING_SETTINGS)
class PostgresSharingConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.w = build_world()
        self.pool = ThreadPoolExecutor(max_workers=4)
        self.addCleanup(self.pool.shutdown, wait=True)

    def create(self, *, actor=None, mode="live", **overrides):
        now = timezone.now()
        arguments = {
            "actor": actor or self.w.admin, "organization": self.w.source, "box_id": self.w.box.pk,
            "recipient_email": self.w.recipient.email, "mode": mode, "selectors": OBSERVATION_SELECTORS,
            "invitation_expires_at": now + timedelta(days=2), "access_expires_at": now + timedelta(days=30),
        }
        arguments.update(overrides)
        return services.create_share(**arguments)

    def observations(self, capture):
        return json.loads(capture.content)["observations"]

    # FROZEN snapshot consistency ---------------------------------------------

    def _capture_while_source_changes(self):
        """Commit a correction and a new measurement between two capture reads."""
        reached, release, seen = threading.Event(), threading.Event(), {}
        original = scientific_projection._observation_rows

        def paused_rows(source, box, disclosure):
            # The institution and Box were already read by this transaction.
            with connection.cursor() as cursor:
                cursor.execute("SHOW transaction_isolation")
                seen["isolation"] = cursor.fetchone()[0]
            reached.set()
            assert release.wait(WAIT)
            return original(source, box, disclosure)

        with patch.object(scientific_projection, "_observation_rows", side_effect=paused_rows):
            future = self.pool.submit(_in_thread(lambda: self.create(mode="frozen").pk))
            self.assertTrue(reached.wait(WAIT))
            # Committed writes that leave the Box row itself untouched, so the
            # capture is not refused and its reads can be compared.
            BiologicalMeasurement.objects.filter(pk=self.w.zero.pk).update(polyp_count=41)
            BiologicalMeasurement.objects.bulk_create([BiologicalMeasurement(
                box=self.w.box, measured_on=date(2026, 5, 6), week_start=date(2026, 5, 4),
                polyp_count=8, ephyrae_count=2,
            )])
            release.set()
            outcome = future.result(WAIT)
        self.assertEqual(outcome[0], "ok", outcome)
        return BoxReaderShareCapture.objects.get(share_id=outcome[1]), seen["isolation"]

    def test_frozen_capture_reads_one_snapshot_despite_concurrent_commits(self):
        capture, isolation = self._capture_while_source_changes()
        self.assertEqual(isolation, "repeatable read")
        observations = self.observations(capture)
        # The state at the start of the capture: one observation, uncorrected.
        self.assertEqual(len(observations), 1)
        self.assertEqual(observations[0]["values"]["polyp_count"], {"state": "value", "value": 0})
        # The source really changed meanwhile.
        self.assertEqual(BiologicalMeasurement.objects.filter(box=self.w.box).count(), 2)

    def test_measurement_entry_during_a_capture_refuses_the_capture(self):
        """The ordinary write path bumps the Box row: the capture fails closed."""
        reached, release = threading.Event(), threading.Event()
        original = scientific_projection._observation_rows

        def paused_rows(source, box, disclosure):
            reached.set()
            assert release.wait(WAIT)
            return original(source, box, disclosure)

        with patch.object(scientific_projection, "_observation_rows", side_effect=paused_rows):
            future = self.pool.submit(_in_thread(lambda: self.create(mode="frozen").pk))
            self.assertTrue(reached.wait(WAIT))
            BiologicalMeasurement.objects.create(
                box=self.w.box, measured_on=date(2026, 5, 6), polyp_count=8, ephyrae_count=2,
            )
            release.set()
            outcome = future.result(WAIT)
        self.assertEqual(outcome, ("refused", "concurrent_source_change"))
        self.assertEqual((BoxReaderShare.objects.count(), BoxReaderShareCapture.objects.count()), (0, 0))
        self.assertFalse(AuditLog.objects.filter(object_type="box_reader_share").exists())
        self.assertEqual(len(mail.outbox), 0)

    def test_control_without_the_snapshot_guarantee_the_same_race_mixes_states(self):
        from contextlib import contextmanager
        from django.db import transaction

        @contextmanager
        def read_committed(*, consistent_capture):
            with transaction.atomic():
                yield

        with patch.object(services, "_mutation", read_committed):
            capture, isolation = self._capture_while_source_changes()
        self.assertEqual(isolation, "read committed")
        # Proves the scenario above is discriminating, not vacuous.
        self.assertEqual(len(self.observations(capture)), 2)

    def test_frozen_capture_refuses_to_run_inside_a_foreign_transaction(self):
        from django.db import transaction

        with transaction.atomic():
            with self.assertRaises(SharingError) as raised:
                self.create(mode="frozen")
        self.assertEqual(raised.exception.code, "frozen_capture_unavailable")
        self.assertEqual(BoxReaderShare.objects.count(), 0)

    def test_recapture_fails_cleanly_when_the_share_changed_after_its_snapshot(self):
        share = self.create(mode="frozen")
        reached, release = threading.Event(), threading.Event()
        original = services._locked_source

        def paused_source(user, organization_id):
            source = original(user, organization_id)  # snapshot established here
            reached.set()
            assert release.wait(WAIT)
            return source

        def recapture():
            return services.update_share(
                actor=self.w.admin, organization=self.w.source, share_id=share.pk, expected_policy_revision=1,
                selectors=["source.institution_name"], confirm_recapture=True,
            ).policy_revision

        with patch.object(services, "_locked_source", side_effect=paused_source):
            future = self.pool.submit(_in_thread(recapture))
            self.assertTrue(reached.wait(WAIT))
            BoxReaderShare.objects.filter(pk=share.pk).update(access_expires_at=timezone.now() + timedelta(days=10))
            release.set()
            outcome = future.result(WAIT)
        self.assertEqual(outcome, ("refused", "concurrent_source_change"))
        stored = BoxReaderShare.objects.get(pk=share.pk)
        self.assertEqual((stored.policy_revision, stored.selectors), (1, sorted(OBSERVATION_SELECTORS)))
        self.assertEqual(BoxReaderShareCapture.objects.count(), 1)
        self.assertFalse(AuditLog.objects.filter(description="Box reader share policy updated").exists())

    # Invitation and grant races ----------------------------------------------

    def _race(self, *functions):
        barrier = threading.Barrier(len(functions))

        def synchronized(function):
            def run():
                barrier.wait(WAIT)
                return function()
            return _in_thread(run)

        futures = [self.pool.submit(synchronized(function)) for function in functions]
        return [future.result(WAIT) for future in futures]

    def test_simultaneous_acceptance_binds_once(self):
        for _round in range(5):
            share = self.create()
            token = last_token()
            accept = lambda: services.accept_invitation(user=self.w.recipient, token=token)["id"]  # noqa: E731
            outcomes = sorted(self._race(accept, accept, accept))
            self.assertEqual(outcomes, [("ok", str(share.public_id)), ("refused", "invitation_unavailable"),
                                        ("refused", "invitation_unavailable")])
            self.assertEqual(
                AuditLog.objects.filter(description="Box reader share invitation accepted", object_id=str(share.public_id)).count(), 1,
            )
            services.revoke_share(actor=self.w.admin, organization=self.w.source, share_id=share.pk)

    def test_simultaneous_creation_leaves_one_open_share(self):
        for mode in ("live", "frozen"):
            with self.subTest(mode=mode):
                outcomes = sorted(self._race(
                    lambda: self.create(mode=mode).pk,
                    lambda: self.create(mode=mode, actor=self.w.second_admin).pk,
                ), key=lambda item: item[0])
                self.assertEqual([item[0] for item in outcomes], ["ok", "refused"], outcomes)
                self.assertEqual(outcomes[1][1], "share_already_exists")
                open_shares = BoxReaderShare.objects.filter(revoked_at__isnull=True)
                self.assertEqual(open_shares.count(), 1)
                self.assertEqual(BoxReaderShareCapture.objects.filter(share__revoked_at__isnull=True).count(), int(mode == "frozen"))
                services.revoke_share(actor=self.w.admin, organization=self.w.source, share_id=open_shares.get().pk)

    def test_simultaneous_policy_changes_have_one_winner(self):
        for mode in ("live", "frozen"):
            with self.subTest(mode=mode):
                share = self.create(mode=mode)

                def change(selectors, actor):
                    return lambda: services.update_share(
                        actor=actor, organization=self.w.source, share_id=share.pk, expected_policy_revision=1,
                        selectors=selectors, confirm_recapture=True,
                    ).selectors

                first = ["source.institution_name"]
                second = ["source.institution_name", "observation.time"]
                outcomes = self._race(change(first, self.w.admin), change(second, self.w.second_admin))
                winners = [item for item in outcomes if item[0] == "ok"]
                losers = [item for item in outcomes if item[0] == "refused"]
                self.assertEqual((len(winners), len(losers)), (1, 1), outcomes)
                self.assertIn(losers[0][1], ("policy_revision_conflict", "concurrent_source_change"))
                stored = BoxReaderShare.objects.get(pk=share.pk)
                self.assertEqual((stored.policy_revision, stored.selectors), (2, winners[0][1]))
                self.assertEqual(
                    BoxReaderShareCapture.objects.filter(share=share).count(), 2 if mode == "frozen" else 0,
                )
                services.revoke_share(actor=self.w.admin, organization=self.w.source, share_id=share.pk)

    def _paused_audit(self, description):
        """Pause one transaction after its row lock, just before it commits."""
        reached, release = threading.Event(), threading.Event()
        original = services._audit

        def paused(action, share, user, text, **metadata):
            if text == description:
                reached.set()
                assert release.wait(WAIT)
            return original(action, share, user, text, **metadata)

        return patch.object(services, "_audit", side_effect=paused), reached, release

    def assert_blocked(self, future):
        with self.assertRaises(Exception):
            future.result(0.7)
        self.assertFalse(future.done())

    def test_revocation_committing_first_defeats_a_waiting_acceptance(self):
        share = self.create()
        token = last_token()
        patcher, reached, release = self._paused_audit("Box reader share revoked")
        with patcher:
            revoke = self.pool.submit(_in_thread(lambda: services.revoke_share(
                actor=self.w.admin, organization=self.w.source, share_id=share.pk,
            ).pk))
            self.assertTrue(reached.wait(WAIT))
            accept = self.pool.submit(_in_thread(
                lambda: services.accept_invitation(user=self.w.recipient, token=token)["id"],
            ))
            self.assert_blocked(accept)  # waits on the share row lock
            release.set()
            self.assertEqual(revoke.result(WAIT)[0], "ok")
            self.assertEqual(accept.result(WAIT), ("refused", "invitation_unavailable"))
        stored = BoxReaderShare.objects.get(pk=share.pk)
        self.assertIsNotNone(stored.revoked_at)
        self.assertIsNone(stored.recipient_user_id)
        self.assertIsNone(stored.accepted_at)

    def test_acceptance_committing_first_is_then_revoked(self):
        share = self.create()
        token = last_token()
        patcher, reached, release = self._paused_audit("Box reader share invitation accepted")
        with patcher:
            accept = self.pool.submit(_in_thread(
                lambda: services.accept_invitation(user=self.w.recipient, token=token)["id"],
            ))
            self.assertTrue(reached.wait(WAIT))
            revoke = self.pool.submit(_in_thread(lambda: services.revoke_share(
                actor=self.w.admin, organization=self.w.source, share_id=share.pk,
            ).pk))
            self.assert_blocked(revoke)
            release.set()
            self.assertEqual(accept.result(WAIT), ("ok", str(share.public_id)))
            self.assertEqual(revoke.result(WAIT)[0], "ok")
        stored = BoxReaderShare.objects.get(pk=share.pk)
        self.assertEqual(stored.recipient_user_id, self.w.recipient.pk)
        self.assertIsNotNone(stored.revoked_at)
        # A read started after the committed revocation is refused.
        with self.assertRaises(SharingError) as raised:
            services.read_shared_box(user=self.w.recipient, public_id=share.public_id)
        self.assertEqual(raised.exception.code, "share_revoked")
        self.assertFalse(AuditLog.objects.filter(action="view", object_type="box_reader_share").exists())

    def test_acceptance_racing_revocation_never_leaves_a_readable_grant(self):
        for _round in range(8):
            share = self.create()
            token = last_token()
            outcomes = self._race(
                lambda: services.accept_invitation(user=self.w.recipient, token=token)["id"],
                lambda: services.revoke_share(actor=self.w.admin, organization=self.w.source, share_id=share.pk).pk,
            )
            self.assertEqual(outcomes[1][0], "ok")
            self.assertIn(outcomes[0], [("ok", str(share.public_id)), ("refused", "invitation_unavailable")])
            stored = BoxReaderShare.objects.get(pk=share.pk)
            self.assertIsNotNone(stored.revoked_at)
            self.assertIsNone(stored.invitation_token_hash)
            with self.assertRaises(SharingError) as raised:
                services.read_shared_box(user=self.w.recipient, public_id=share.public_id)
            self.assertIn(raised.exception.code, ("share_revoked", "share_not_found"))

    def test_membership_change_waits_for_a_share_mutation_holding_the_institution(self):
        """The Admin re-check and a demotion serialize on the institution row."""
        from apps.organizations.models import Organization
        from django.db import transaction

        share = self.create()
        patcher, reached, release = self._paused_audit("Box reader share revoked")

        def lock_institution_like_a_membership_change():
            with transaction.atomic():
                Organization.objects.select_for_update().get(pk=self.w.source.pk)
            return True

        with patcher:
            revoke = self.pool.submit(_in_thread(lambda: services.revoke_share(
                actor=self.w.admin, organization=self.w.source, share_id=share.pk,
            ).pk))
            self.assertTrue(reached.wait(WAIT))
            membership_change = self.pool.submit(_in_thread(lock_institution_like_a_membership_change))
            self.assert_blocked(membership_change)
            release.set()
            self.assertEqual(revoke.result(WAIT)[0], "ok")
            self.assertEqual(membership_change.result(WAIT), ("ok", True))

    # Email delivery: after commit, outside every lock ------------------------

    def _slow_smtp(self, action):
        """Run `action` in a worker whose SMTP call blocks; return what was observed."""
        from django.db import transaction
        from apps.organizations.models import Organization

        reached, release, seen = threading.Event(), threading.Event(), {}

        def slow_send(*args, **kwargs):
            seen["in_atomic_block"] = connection.in_atomic_block
            reached.set()
            assert release.wait(WAIT)
            return 1

        with patch.object(services, "send_mail", side_effect=slow_send):
            future = self.pool.submit(_in_thread(action))
            self.assertTrue(reached.wait(WAIT))
            # Committed and visible from another connection while SMTP is stuck.
            share = BoxReaderShare.objects.get()
            seen["visible_generation"] = share.invitation_generation
            # Neither the institution nor the share row is locked.
            with transaction.atomic():
                Organization.objects.select_for_update(nowait=True).get(pk=self.w.source.pk)
                BoxReaderShare.objects.select_for_update(nowait=True).get(pk=share.pk)
            # A management mutation completes without waiting for SMTP.
            services.revoke_share(actor=self.w.admin, organization=self.w.source, share_id=share.pk)
            release.set()
            seen["outcome"] = future.result(WAIT)
        return seen, share

    def test_no_transaction_or_lock_is_held_while_smtp_runs_on_creation(self):
        seen, share = self._slow_smtp(lambda: self.create().pk)
        self.assertFalse(seen["in_atomic_block"])
        self.assertEqual(seen["outcome"][0], "ok")
        stored = BoxReaderShare.objects.get(pk=share.pk)
        # Revoked during SMTP: the outcome is not recorded over the revocation.
        self.assertIsNotNone(stored.revoked_at)
        self.assertEqual(stored.delivery_status, "pending")
        self.assertIsNone(stored.invitation_token_hash)

    def test_no_transaction_or_lock_is_held_while_smtp_runs_on_frozen_creation(self):
        seen, share = self._slow_smtp(lambda: self.create(mode="frozen").pk)
        self.assertFalse(seen["in_atomic_block"])
        self.assertEqual(BoxReaderShareCapture.objects.filter(share_id=share.pk).count(), 1)
        self.assertEqual(BoxReaderShare.objects.get(pk=share.pk).delivery_status, "pending")

    def test_no_transaction_or_lock_is_held_while_smtp_runs_on_reissue(self):
        share = self.create()
        self.assertEqual(BoxReaderShare.objects.get(pk=share.pk).delivery_status, "sent")

        def reissue():
            return services.reissue_invitation(
                actor=self.w.admin, organization=self.w.source, share_id=share.pk,
                invitation_expires_at=timezone.now() + timedelta(days=3),
            ).pk

        seen, _share = self._slow_smtp(reissue)
        self.assertFalse(seen["in_atomic_block"])
        self.assertEqual(seen["visible_generation"], 2)
        stored = BoxReaderShare.objects.get(pk=share.pk)
        self.assertEqual((stored.invitation_generation, stored.delivery_status), (2, "pending"))
        self.assertIsNotNone(stored.revoked_at)

    def test_email_is_sent_only_after_the_creation_commit(self):
        seen = {}

        def inspecting_send(*args, **kwargs):
            seen["in_atomic_block"] = connection.in_atomic_block
            seen["rows"] = BoxReaderShare.objects.count()
            seen["audits"] = AuditLog.objects.filter(description="Box reader share created").count()
            return 1

        with patch.object(services, "send_mail", side_effect=inspecting_send):
            self.create(mode="frozen")
        self.assertEqual(seen, {"in_atomic_block": False, "rows": 1, "audits": 1})

    def test_a_failure_before_commit_sends_nothing(self):
        original = services._audit

        def failing_audit(action, share, user, description, **metadata):
            original(action, share, user, description, **metadata)
            if description == "Box reader share created":
                raise RuntimeError("fail before commit")

        with patch.object(services, "_audit", side_effect=failing_audit), \
                patch.object(services, "send_mail") as send:
            with self.assertRaises(RuntimeError):
                self.create()
        send.assert_not_called()
        self.assertEqual(BoxReaderShare.objects.count(), 0)
