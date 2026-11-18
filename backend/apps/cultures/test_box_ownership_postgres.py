"""PostgreSQL evidence for exclusive Box ownership under concurrency.

Skipped on SQLite. Run only against a freshly created, empty, disposable local
cluster (for example `config.sharing_pgqa_settings`, model-synced schema), never
a shared, copied or production database. The 0019 reverse guard (RunPython) is
not exercised by a model-synced schema.
"""

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
import threading
from unittest import skipUnless
from uuid import uuid4

from django.core.exceptions import ValidationError as ModelValidationError
from django.db import IntegrityError, connection, connections, transaction
from django.test import TransactionTestCase
from django.utils import timezone

from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain

from .box_ownership import enroll_origin_box
from .models import Box, BoxOwnershipEpisode, GlobalBoxIdentity

WAIT = 20


def _worker(function):
    def run():
        try:
            return ("ok", function())
        except (ModelValidationError, IntegrityError) as error:
            return ("refused", type(error).__name__)
        finally:
            connections.close_all()
    return run


@skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL row locks and partial unique indexes.")
class BoxOwnershipPostgresTests(TransactionTestCase):
    def setUp(self):
        self.org_a = Organization.objects.create(name="PG owner A", slug="pg-own-a")
        self.org_b = Organization.objects.create(name="PG owner B", slug="pg-own-b")
        self.org_c = Organization.objects.create(name="PG owner C", slug="pg-own-c")
        species = Species.objects.create(scientific_name="PG ownership species")
        self.strain = Strain.objects.create(species=species, organization=self.org_a, code="PG-OWN")
        self.a1 = self.box(self.org_a, "PG-OWN.001")
        self.b1 = self.box(self.org_b, "PG-OWN.002")
        self.c1 = self.box(self.org_c, "PG-OWN.003")
        self.pool = ThreadPoolExecutor(max_workers=2)
        self.addCleanup(self.pool.shutdown, wait=True)

    def box(self, organization, code):
        return Box.objects.create(
            organization=organization, strain=self.strain, global_code=code, box_number=code[-3:],
        )

    def test_concurrent_enrollment_of_one_box_yields_one_identity(self):
        barrier = threading.Barrier(2)

        def enroll():
            barrier.wait(WAIT)
            return enroll_origin_box(organization=self.org_a, box=self.a1).pk

        results = [f.result(WAIT) for f in [self.pool.submit(_worker(enroll)) for _ in range(2)]]
        self.assertEqual([r[0] for r in results], ["ok", "ok"])
        self.assertEqual(results[0][1], results[1][1])
        self.assertEqual(GlobalBoxIdentity.objects.count(), 1)
        self.assertEqual(BoxOwnershipEpisode.objects.count(), 1)

    def test_concurrent_successors_are_serialized_and_only_one_wins(self):
        origin = enroll_origin_box(organization=self.org_a, box=self.a1)
        at = timezone.now()
        origin.ended_at = at
        origin.save(update_fields=["ended_at"])
        holding, release = threading.Event(), threading.Event()

        def successor(organization, box, hold):
            with transaction.atomic():
                episode = BoxOwnershipEpisode.objects.create(
                    identity_id=origin.identity_id, sequence=2, organization=organization,
                    local_box=box, started_at=at, arrival_transfer_id=uuid4(), arrival_item_id=uuid4(),
                )
                if hold:
                    holding.set()
                    assert release.wait(WAIT)
                return episode.pk

        first = self.pool.submit(_worker(lambda: successor(self.org_b, self.b1, True)))
        self.assertTrue(holding.wait(WAIT))
        second = self.pool.submit(_worker(lambda: successor(self.org_c, self.c1, False)))
        # The second writer waits on the identity row lock held by the first.
        with self.assertRaises(FutureTimeout):
            second.result(timeout=1)
        release.set()
        outcomes = sorted([first.result(WAIT)[0], second.result(WAIT)[0]])
        self.assertEqual(outcomes, ["ok", "refused"])
        self.assertEqual(
            list(BoxOwnershipEpisode.objects.filter(identity_id=origin.identity_id, ended_at__isnull=True)
                 .values_list("local_box_id", flat=True)),
            [self.b1.pk],
        )

    def test_database_arbitrates_two_current_owners_without_model_validation(self):
        identity = GlobalBoxIdentity.objects.create()
        barrier = threading.Barrier(2)

        def insert(organization, box):
            barrier.wait(WAIT)
            with transaction.atomic():
                BoxOwnershipEpisode.objects.bulk_create([BoxOwnershipEpisode(
                    identity=identity, sequence=1, organization=organization, local_box=box,
                )])

        futures = [self.pool.submit(_worker(lambda o=o, b=b: insert(o, b)))
                   for o, b in ((self.org_a, self.a1), (self.org_b, self.b1))]
        outcomes = sorted(f.result(WAIT)[0] for f in futures)
        self.assertEqual(outcomes, ["ok", "refused"])
        self.assertEqual(BoxOwnershipEpisode.objects.filter(identity=identity, ended_at__isnull=True).count(), 1)
