"""PostgreSQL evidence for donation proposal locking, idempotency and races.

Skipped on SQLite. Run only against a freshly created, empty, disposable local
cluster (for example `config.sharing_pgqa_settings`, model-synced schema), never
a shared, copied or production database.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import date
import threading
import time
from unittest import skipUnless
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.db import connection, connections, transaction
from django.test import TransactionTestCase

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.measurements.models import BiologicalMeasurement
from apps.organizations.models import Organization
from apps.taxonomy.models import GlobalStrainIdentity, Species, Strain

from . import box_donation
from .box_donation import DonationError
from .box_ownership import enroll_origin_box_as_admin
from .models import Box, BoxDonationProposal, BoxOwnershipEpisode, GlobalBoxIdentity

WAIT = 20


def _worker(function):
    def run():
        try:
            return ("ok", function())
        except DonationError as error:
            return ("refused", error.code)
        finally:
            connections.close_all()
    return run


@skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL row locks and partial unique indexes.")
class BoxDonationPostgresTests(TransactionTestCase):
    def setUp(self):
        User = get_user_model()
        self.org_a = Organization.objects.create(name="PG donor A", slug="pg-don-a")
        self.org_b = Organization.objects.create(name="PG receiver B", slug="pg-don-b")
        self.org_c = Organization.objects.create(name="PG receiver C", slug="pg-don-c")
        self.admin = User.objects.create_user(username="pg-don-admin", password="pw")
        self.admin2 = User.objects.create_user(username="pg-don-admin2", password="pw")
        for user in (self.admin, self.admin2):
            OrganizationMembership.objects.create(
                user=user, organization=self.org_a, role=OrganizationMembership.Role.ADMIN,
            )
        species = Species.objects.create(scientific_name="PG donation species")
        self.strain = Strain.objects.create(
            species=species, organization=self.org_a, code="PGD-AAA-1",
            global_identity=GlobalStrainIdentity.objects.create(),
        )
        self.box = Box.objects.create(
            organization=self.org_a, strain=self.strain, global_code="PGD-AAA-1.001", box_number="001",
        )
        BiologicalMeasurement.objects.create(box=self.box, measured_on=date(2026, 3, 2), polyp_count=0)
        self.pool = ThreadPoolExecutor(max_workers=2)
        self.addCleanup(self.pool.shutdown, wait=True)

    def wait_for_lock_waiter(self):
        """Return once another backend is blocked on a row lock (no timing guess)."""
        deadline = time.monotonic() + WAIT
        with connections["default"].cursor() as cursor:
            while time.monotonic() < deadline:
                cursor.execute(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname = current_database() AND wait_event_type = 'Lock'"
                )
                if cursor.fetchone()[0]:
                    return
                time.sleep(0.05)
        self.fail("No backend ever waited on the Box lock.")

    def enroll(self, *, hold=None, release=None, barrier=None):
        def run():
            if barrier is not None:
                barrier.wait(WAIT)
            with transaction.atomic():
                episode, created = enroll_origin_box_as_admin(
                    actor=self.admin, organization=self.org_a, box=self.box,
                )
                if hold is not None:
                    hold.set()
                    release.wait(WAIT)
            return (episode.pk, created)
        return run

    def test_enrollment_waiting_on_the_box_lock_does_not_audit_again(self):
        holding, release = threading.Event(), threading.Event()
        first = self.pool.submit(_worker(self.enroll(hold=holding, release=release)))
        self.assertTrue(holding.wait(WAIT))
        # The second caller starts while the first enrollment is uncommitted,
        # so a read before the lock would still see "not enrolled".
        second = self.pool.submit(_worker(self.enroll()))
        self.wait_for_lock_waiter()
        release.set()
        results = [first.result(WAIT), second.result(WAIT)]
        self.assertEqual([r[0] for r in results], ["ok", "ok"])
        self.assertEqual(results[0][1][0], results[1][1][0])
        self.assertEqual([r[1][1] for r in results], [True, False])
        self.assertEqual(GlobalBoxIdentity.objects.count(), 1)
        self.assertEqual(BoxOwnershipEpisode.objects.count(), 1)
        self.assertEqual(AuditLog.objects.filter(object_type="global_box_identity").count(), 1)

    def test_simultaneous_enrollments_audit_exactly_once(self):
        barrier = threading.Barrier(2)
        futures = [self.pool.submit(_worker(self.enroll(barrier=barrier))) for _ in range(2)]
        results = [f.result(WAIT) for f in futures]
        self.assertEqual([r[0] for r in results], ["ok", "ok"])
        self.assertEqual(results[0][1][0], results[1][1][0])
        self.assertEqual(sorted(r[1][1] for r in results), [False, True])
        self.assertEqual(GlobalBoxIdentity.objects.count(), 1)
        self.assertEqual(BoxOwnershipEpisode.objects.count(), 1)
        self.assertEqual(AuditLog.objects.filter(object_type="global_box_identity").count(), 1)

    def test_concurrent_proposals_audit_one_enrollment(self):
        barrier = threading.Barrier(2)
        futures = [
            self.pool.submit(self.create(self.admin, self.payload(self.org_b), barrier)),
            self.pool.submit(self.create(self.admin2, self.payload(self.org_c), barrier)),
        ]
        self.assertEqual(sorted(f.result(WAIT)[0] for f in futures), ["ok", "refused"])
        self.assertEqual(AuditLog.objects.filter(object_type="global_box_identity").count(), 1)
        self.assertEqual(AuditLog.objects.filter(object_type="box_donation_proposal").count(), 1)

    def payload(self, destination, request_id=None):
        return {
            "request_id": str(request_id or uuid4()),
            "source_box_id": self.box.pk,
            "destination_institution_id": str(destination.portable_id),
            "history_verified": True,
            "history_sha256": box_donation.source_history_reference(self.org_a, self.box)["sha256"],
        }

    def create(self, actor, payload, barrier=None):
        def run():
            if barrier is not None:
                barrier.wait(WAIT)
            proposal, created = box_donation.create_proposal(
                actor=actor, organization=self.org_a, data=payload,
            )
            return (str(proposal.proposal_id), created)
        return _worker(run)

    def test_concurrent_proposals_for_one_box_yield_one_pending_and_one_identity(self):
        barrier = threading.Barrier(2)
        futures = [
            self.pool.submit(self.create(self.admin, self.payload(self.org_b), barrier)),
            self.pool.submit(self.create(self.admin2, self.payload(self.org_c), barrier)),
        ]
        results = sorted(f.result(WAIT)[0] for f in futures)
        self.assertEqual(results, ["ok", "refused"])
        refused = [f.result()[1] for f in futures if f.result()[0] == "refused"]
        self.assertEqual(refused, ["donation_already_pending"])
        self.assertEqual(BoxDonationProposal.objects.filter(state="pending").count(), 1)
        self.assertEqual(GlobalBoxIdentity.objects.count(), 1)
        self.assertEqual(BoxOwnershipEpisode.objects.count(), 1)

    def test_concurrent_identical_requests_create_once(self):
        barrier = threading.Barrier(2)
        payload = self.payload(self.org_b)
        futures = [self.pool.submit(self.create(self.admin, payload, barrier)) for _ in range(2)]
        results = [f.result(WAIT) for f in futures]
        self.assertEqual([r[0] for r in results], ["ok", "ok"])
        self.assertEqual(results[0][1][0], results[1][1][0])
        self.assertEqual(sorted(r[1][1] for r in results), [False, True])
        self.assertEqual(BoxDonationProposal.objects.count(), 1)

    def test_concurrent_cancellations_have_one_winner(self):
        proposal, _created = box_donation.create_proposal(
            actor=self.admin, organization=self.org_a, data=self.payload(self.org_b),
        )
        barrier = threading.Barrier(2)

        def cancel(actor):
            def run():
                barrier.wait(WAIT)
                return box_donation.cancel_proposal(
                    actor=actor, organization=self.org_a, proposal_id=proposal.proposal_id,
                    data={"expected_revision": 1},
                ).revision
            return _worker(run)

        results = sorted(f.result(WAIT) for f in [
            self.pool.submit(cancel(self.admin)), self.pool.submit(cancel(self.admin2)),
        ])
        self.assertEqual(results[0], ("ok", 2))
        self.assertEqual(results[1][0], "refused")
        self.assertIn(results[1][1], {"proposal_not_pending", "stale_proposal_revision"})
        self.assertEqual(BoxDonationProposal.objects.get().revision, 2)

    def test_demotion_committed_while_waiting_is_observed(self):
        holding, release = threading.Event(), threading.Event()
        payload = self.payload(self.org_b)

        def demote():
            with transaction.atomic():
                # Membership changes lock the institution first.
                Organization.objects.select_for_update().get(pk=self.org_a.pk)
                OrganizationMembership.objects.filter(user=self.admin, organization=self.org_a).update(
                    role=OrganizationMembership.Role.VIEWER,
                )
                holding.set()
                release.wait(WAIT)
            return "demoted"

        demotion = self.pool.submit(_worker(demote))
        self.assertTrue(holding.wait(WAIT))
        creation = self.pool.submit(self.create(self.admin, payload))
        release.set()
        self.assertEqual(demotion.result(WAIT), ("ok", "demoted"))
        self.assertEqual(creation.result(WAIT), ("refused", "donation_admin_required"))
        self.assertEqual(BoxDonationProposal.objects.count(), 0)
        self.assertEqual(GlobalBoxIdentity.objects.count(), 0)

    def test_measurement_committed_while_waiting_makes_the_reference_stale(self):
        holding, release = threading.Event(), threading.Event()
        payload = self.payload(self.org_b)

        def measure():
            with transaction.atomic():
                BiologicalMeasurement.objects.create(box=self.box, measured_on=date(2026, 3, 9), polyp_count=5)
                holding.set()
                release.wait(WAIT)
            return "measured"

        measurement = self.pool.submit(_worker(measure))
        self.assertTrue(holding.wait(WAIT))
        creation = self.pool.submit(self.create(self.admin, payload))
        release.set()
        self.assertEqual(measurement.result(WAIT), ("ok", "measured"))
        self.assertEqual(creation.result(WAIT), ("refused", "source_history_changed"))
        self.assertEqual(GlobalBoxIdentity.objects.count(), 0)
